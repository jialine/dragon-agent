"""
Dragon Agent — Skill Engine (Semantic Discovery + Self-Evolution + Pipelines)
============================================================================

The SkillEngine is the central orchestrator for Dragon's skill system.
It provides:

1. **Semantic Skill Discovery** — finds the best skill for a task using
   embedding similarity, falling back to keyword matching when embeddings
   are unavailable.

2. **Self-Evolution** — monitors skill execution outcomes and proposes
   improvements. Skills that degrade in performance are automatically
   rolled back to their last-known-good version.

3. **Skill Pipelines** — composes multiple skills into workflows with
   input/output contracts and parallel/conditional execution.

Usage::

    from dragon.skill.engine import SkillEngine

    engine = SkillEngine(skills_dir="~/.dragon/skills/")

    # Discover the best skill for a task
    match = await engine.discover("How do I set up a CI/CD pipeline?")
    if match:
        result = await engine.execute(match.skill_name, context={...})

    # Record outcome — triggers auto-evolution
    engine.record("ci-cd-setup", success=True, latency_ms=1200.0)

    # Compose a pipeline
    pipeline = engine.compose(["validate-config", "deploy", "health-check"])
    results = await pipeline.run(context={...})
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from dragon.skill.skill import (
    DragonSkill, SkillMeta, SkillVersion, SkillMatch,
    SkillExecutionReport, SkillOutcome, SkillStatus,
    EvolutionProposal, ExecutionMode,
)

logger = logging.getLogger("dragon.skill.engine")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

DEFAULT_SKILLS_DIR = "~/.dragon/skills/"
DEFAULT_VECTORDB_DIR = "dragon_data/skill_vectors"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
AUTO_EVOLVE_THRESHOLD = 3  # consecutive failures before proposing evolution
ROLLBACK_THRESHOLD = 0.15   # success rate drop before auto-rollback


# ────────────────────────────────────────────────────────────────────
# SkillPipeline — compose skills into workflows
# ────────────────────────────────────────────────────────────────────


@dataclass
class PipelineStep:
    skill_name: str
    mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    condition: Optional[Callable[[Dict], bool]] = None
    input_map: Optional[Dict[str, str]] = None  # context key → skill input key
    timeout_secs: float = 120.0


@dataclass
class PipelineResult:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    success: bool = True
    total_latency_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": self.steps,
            "success": self.success,
            "total_latency_ms": self.total_latency_ms,
            "error": self.error,
        }


class SkillPipeline:
    """Compose multiple DragonSkills into a workflow.

    Supports:
    - Sequential execution (default)
    - Parallel execution of independent steps
    - Conditional execution with predicate functions
    - Input/output mapping between steps
    """

    def __init__(
        self,
        engine: "SkillEngine",
        steps: List[PipelineStep],
        name: str = "",
    ) -> None:
        self.engine = engine
        self.steps = steps
        self.name = name or f"pipeline-{len(steps)}-steps"

    async def run(self, context: Dict[str, Any]) -> PipelineResult:
        result = PipelineResult()
        start = time.monotonic()

        # Group steps by mode
        sequential_steps = []
        parallel_batch = []
        pending_conditional = []

        for step in self.steps:
            if step.mode == ExecutionMode.CONDITIONAL:
                pending_conditional.append(step)
            elif step.mode == ExecutionMode.PARALLEL:
                parallel_batch.append(step)
            else:
                # Flush parallel batch before sequential
                if parallel_batch:
                    batch_result = await self._run_parallel(parallel_batch, context)
                    result.steps.extend(batch_result)
                    if not all(s.get("success", False) for s in batch_result):
                        result.success = False
                        result.error = "Parallel batch failed"
                        break
                    parallel_batch = []

                sequential_steps.append(step)

        # Process remaining
        for step in sequential_steps:
            step_result = await self._run_step(step, context)
            result.steps.append(step_result)
            if not step_result.get("success", False):
                result.success = False
                result.error = step_result.get("error", "Step failed")
                break

        # Process conditionals last (they depend on full context)
        for step in pending_conditional:
            if step.condition and step.condition(context):
                step_result = await self._run_step(step, context)
                result.steps.append(step_result)

        result.total_latency_ms = (time.monotonic() - start) * 1000
        return result

    async def _run_step(
        self, step: PipelineStep, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a single pipeline step."""
        step_start = time.monotonic()

        # Map context inputs
        step_context = dict(context)
        if step.input_map:
            for ctx_key, skill_key in step.input_map.items():
                if ctx_key in context:
                    step_context[skill_key] = context[ctx_key]

        try:
            skill = self.engine.get(step.skill_name)
            if skill is None:
                return {
                    "skill": step.skill_name,
                    "success": False,
                    "error": f"Skill '{step.skill_name}' not found",
                    "latency_ms": (time.monotonic() - step_start) * 1000,
                }

            # Execute via engine's executor
            if self.engine._executor:
                output = await self.engine._executor(skill, step_context)
                self.engine.record(step.skill_name, success=True, latency_ms=output.get("latency_ms", 0))
                return {
                    "skill": step.skill_name,
                    "success": True,
                    "output": output,
                    "latency_ms": (time.monotonic() - step_start) * 1000,
                }
            else:
                return {
                    "skill": step.skill_name,
                    "success": True,
                    "output": {"content": skill.content, "context": step_context},
                    "latency_ms": (time.monotonic() - step_start) * 1000,
                }
        except Exception as e:
            self.engine.record(step.skill_name, success=False, latency_ms=0)
            return {
                "skill": step.skill_name,
                "success": False,
                "error": str(e),
                "latency_ms": (time.monotonic() - step_start) * 1000,
            }

    async def _run_parallel(
        self, steps: List[PipelineStep], context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Execute multiple steps in parallel."""
        tasks = [self._run_step(s, context) for s in steps]
        return await asyncio.gather(*tasks)


# ────────────────────────────────────────────────────────────────────
# SkillEngine
# ────────────────────────────────────────────────────────────────────


class SkillEngine:
    """Central orchestrator for Dragon's self-evolving skill system.

    Parameters
    ----------
    skills_dir : str
        Directory where skills are stored (JSON files).
    embedding_model : str or None
        HuggingFace model for semantic matching. If None, uses keyword matching.
    auto_evolve : bool
        Whether to auto-evolve skills based on execution outcomes.
    """

    def __init__(
        self,
        skills_dir: str = DEFAULT_SKILLS_DIR,
        embedding_model: Optional[str] = None,
        auto_evolve: bool = True,
    ) -> None:
        self.skills_dir = Path(skills_dir).expanduser()
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.auto_evolve = auto_evolve

        # Skills registry: name → DragonSkill
        self._skills: Dict[str, DragonSkill] = OrderedDict()
        self._lock = threading.Lock()

        # Embedding model (lazy loaded)
        self._embedding_model: Optional[Any] = None
        self._embedding_model_name = embedding_model
        self._skill_embeddings: Dict[str, List[float]] = {}

        # Execution callback — set by external system (e.g., DragonDispatcher)
        self._executor: Optional[Callable] = None

        # Evolution proposals queue
        self._evolution_proposals: List[EvolutionProposal] = []

        # Load existing skills
        self._load_all()

        logger.info(
            "SkillEngine initialized — %d skills in %s",
            len(self._skills), self.skills_dir,
        )

    # ── Executor Registration ──────────────────────────────────────

    def register_executor(self, executor: Callable) -> None:
        """Register a callable that executes skills.

        The executor receives (DragonSkill, context_dict) and returns
        a dict with at least {'success': bool, 'output': ..., 'latency_ms': float}.
        """
        self._executor = executor

    # ── Skill CRUD ─────────────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        content: str,
        tags: Optional[List[str]] = None,
        version: str = "1.0.0",
        execution_mode: str = "sequential",
        related_skills: Optional[List[str]] = None,
    ) -> DragonSkill:
        """Register a new skill or update an existing one."""
        with self._lock:
            meta = SkillMeta(
                name=name,
                description=description,
                tags=tags or [],
                version=version,
                execution_mode=execution_mode,
                related_skills=related_skills or [],
            )
            skill = DragonSkill(meta=meta, content=content)
            self._skills[name] = skill
            self._persist_skill(skill)
            logger.info("Registered skill: %s (v%s)", name, version)
            return skill

    def get(self, name: str) -> Optional[DragonSkill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        """List all registered skills with metadata."""
        return [
            {
                "name": s.name,
                "description": s.meta.description,
                "version": s.meta.version,
                "tags": s.meta.tags,
                "status": s.meta.status,
                "success_rate": round(s.success_rate, 3),
                "total_uses": s.total_uses,
                "versions": len(s._versions),
            }
            for s in self._skills.values()
        ]

    def delete(self, name: str) -> bool:
        """Delete a skill."""
        with self._lock:
            if name in self._skills:
                del self._skills[name]
                # Remove persisted file
                skill_file = self.skills_dir / f"{name}.json"
                if skill_file.exists():
                    skill_file.unlink()
                logger.info("Deleted skill: %s", name)
                return True
            return False

    # ── Semantic Discovery ─────────────────────────────────────────

    async def discover(
        self,
        query: str,
        top_k: int = 3,
        min_similarity: float = 0.35,
    ) -> List[SkillMatch]:
        """Find the best skills for a task using semantic similarity.

        Falls back to keyword matching if embeddings are unavailable.
        """
        if not self._skills:
            return []

        # Try semantic matching first
        if self._embedding_model is not None or self._embedding_model_name:
            return await self._semantic_discover(query, top_k, min_similarity)

        # Fallback: keyword matching
        return self._keyword_discover(query, top_k)

    async def _semantic_discover(
        self, query: str, top_k: int, min_similarity: float
    ) -> List[SkillMatch]:
        """Embedding-based semantic discovery."""
        # Lazy-load embedding model
        if self._embedding_model is None and self._embedding_model_name:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedding_model = SentenceTransformer(
                    self._embedding_model_name, device="cpu"
                )
                # Pre-compute all skill embeddings
                self._precompute_embeddings()
            except ImportError:
                logger.warning("sentence-transformers not available, using keyword matching")
                return self._keyword_discover(query, top_k)
            except Exception as e:
                logger.error("Failed to load embedding model: %s", e)
                return self._keyword_discover(query, top_k)

        if self._embedding_model is None:
            return self._keyword_discover(query, top_k)

        # Generate query embedding
        query_embedding = self._embedding_model.encode(
            [query], convert_to_numpy=True, show_progress_bar=False
        )[0]

        # Compute cosine similarities
        matches: List[SkillMatch] = []
        for name, skill in self._skills.items():
            if name not in self._skill_embeddings:
                continue
            skill_emb = self._skill_embeddings[name]
            similarity = self._cosine_similarity(query_embedding, skill_emb)

            if similarity >= min_similarity:
                matches.append(SkillMatch(
                    skill_name=name,
                    similarity=float(similarity),
                    skill=skill,
                ))

        matches.sort(key=lambda m: m.similarity, reverse=True)
        return matches[:top_k]

    def _keyword_discover(self, query: str, top_k: int) -> List[SkillMatch]:
        """Keyword-based fallback discovery."""
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored: List[Tuple[float, DragonSkill]] = []

        for name, skill in self._skills.items():
            score = 0.0

            # Name match
            if name.lower() in query_lower or query_lower in name.lower():
                score += 0.5

            # Tag match
            matched_tags: List[str] = []
            for tag in skill.meta.tags:
                if tag.lower() in query_lower:
                    score += 0.3
                    matched_tags.append(tag)

            # Description word overlap
            desc_words = set(skill.meta.description.lower().split())
            overlap = len(query_words & desc_words)
            if overlap > 0:
                score += min(0.3, overlap * 0.05)

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            SkillMatch(skill_name=s.name, similarity=sco, skill=s)
            for sco, s in scored[:top_k]
        ]

    def _precompute_embeddings(self) -> None:
        """Pre-compute embeddings for all skills."""
        if self._embedding_model is None:
            return
        for name, skill in self._skills.items():
            text = skill._build_embedding_text()
            emb = self._embedding_model.encode(
                [text], convert_to_numpy=True, show_progress_bar=False
            )[0]
            self._skill_embeddings[name] = emb.tolist()

    @staticmethod
    def _cosine_similarity(a, b) -> float:
        import numpy as np
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    # ── Execution & Evolution ──────────────────────────────────────

    def record(
        self,
        skill_name: str,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """Record a skill execution outcome.

        This triggers auto-evolution checks:
        - If success rate drops below threshold → auto-rollback
        - If consecutive failures exceed threshold → propose evolution
        """
        skill = self.get(skill_name)
        if skill is None:
            return

        skill.record_execution(success, latency_ms)

        # Check rollback
        if self.auto_evolve:
            skill.rollback()

        # Check evolution need
        if self.auto_evolve and not success:
            self._check_evolution(skill)

        # Persist updated metrics
        self._persist_skill(skill)

    def _check_evolution(self, skill: DragonSkill) -> None:
        """Check if a skill needs auto-evolution."""
        v = skill.current_version

        # Count consecutive failures
        consecutive_failures = 0
        for ver in reversed(skill._versions):
            if ver.failure_count > 0 and ver.success_count == 0:
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= AUTO_EVOLVE_THRESHOLD:
            logger.warning(
                "Skill '%s' has %d consecutive failing versions — evolution needed",
                skill.name, consecutive_failures,
            )
            # Don't auto-evolve content without user approval — just flag it
            skill.meta.status = SkillStatus.EVOLVING.value

    def propose_evolution(self, skill_name: str) -> Optional[EvolutionProposal]:
        """Generate an evolution proposal for a skill.

        Currently flags the skill for evolution; the actual content
        improvement is driven by an LLM executor.
        """
        skill = self.get(skill_name)
        if skill is None:
            return None

        v = skill.current_version
        proposal = EvolutionProposal(
            skill_name=skill_name,
            current_version=v.version,
            proposed_content="",  # filled by LLM executor
            reason=f"Success rate {v.success_rate:.1%}, {v.failure_count} failures in v{v.version}",
            expected_improvement="Improved success rate through refined instructions",
        )
        self._evolution_proposals.append(proposal)
        return proposal

    def get_evolution_proposals(self) -> List[EvolutionProposal]:
        """Get all pending evolution proposals."""
        return list(self._evolution_proposals)

    # ── Skill Pipelines ────────────────────────────────────────────

    def compose(
        self,
        skill_names: List[str],
        mode: str = "sequential",
        name: str = "",
    ) -> SkillPipeline:
        """Compose multiple skills into a pipeline.

        Args:
            skill_names: Ordered list of skill names to execute.
            mode: 'sequential' or 'parallel'.
            name: Optional pipeline name.
        """
        exec_mode = ExecutionMode.SEQUENTIAL
        if mode == "parallel":
            exec_mode = ExecutionMode.PARALLEL

        steps = [
            PipelineStep(skill_name=sn, mode=exec_mode)
            for sn in skill_names
        ]
        return SkillPipeline(self, steps, name=name)

    # ── Persistence ────────────────────────────────────────────────

    def _persist_skill(self, skill: DragonSkill) -> None:
        """Save a skill to disk as JSON."""
        skill_file = self.skills_dir / f"{skill.name}.json"
        try:
            # Atomic write
            tmp = skill_file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(skill.to_dict(), f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, skill_file)
        except OSError as e:
            logger.error("Failed to persist skill '%s': %s", skill.name, e)

    def _load_all(self) -> None:
        """Load all skills from the skills directory."""
        if not self.skills_dir.exists():
            return

        for skill_file in self.skills_dir.glob("*.json"):
            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                skill = DragonSkill.from_dict(data)
                self._skills[skill.name] = skill
                logger.debug("Loaded skill: %s (v%s)", skill.name, skill.meta.version)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to load skill %s: %s", skill_file, e)

    # ── Stats ──────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return engine statistics."""
        total_uses = sum(s.total_uses for s in self._skills.values())
        active_skills = sum(
            1 for s in self._skills.values()
            if s.meta.status == SkillStatus.ACTIVE.value
        )
        avg_success = (
            sum(s.success_rate for s in self._skills.values()) / max(1, len(self._skills))
        )
        return {
            "total_skills": len(self._skills),
            "active_skills": active_skills,
            "total_executions": total_uses,
            "avg_success_rate": round(avg_success, 3),
            "evolution_proposals": len(self._evolution_proposals),
            "skills_dir": str(self.skills_dir),
        }
