"""
Panda Agent — Self-Evolving Skill System
========================================

Surpasses Hermes Agent's skill system with four innovations:

1. **Semantic Matching** — embedding-based skill discovery, not keyword triggers
2. **Self-Evolution** — skills auto-improve from execution experience
3. **Versioned A/B Testing** — track which version performs better, auto-rollback
4. **Skill Pipelines** — compose skills into workflows with contracts

Architecture::

    ┌─────────────────────────────────────────────────────────────────┐
    │                        SkillEngine                              │
    │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
    │  │  Discoverer   │  │  Evolver      │  │  Pipeline Composer   │  │
    │  │  (semantic)   │  │  (self-learn) │  │  (skill chains)      │  │
    │  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
    │         │                 │                      │              │
    │  ┌──────▼─────────────────▼──────────────────────▼───────────┐  │
    │  │                    Skill Registry                          │  │
    │  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────────────┐  │  │
    │  │  │ v1.0.0 │ │ v1.1.0 │ │ v2.0.0 │ │ metrics (success,   │  │  │
    │  │  │ 82% ✓  │ │ 91% ✓  │ │ 73% ✗  │ │ latency, fallback)  │  │  │
    │  │  └────────┘ └────────┘ └────────┘ └────────────────────┘  │  │
    │  └───────────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("panda.skill")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

MAX_SKILL_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_SKILL_BODY_LEN = 100_000
MIN_SIMILARITY_FOR_MATCH = 0.35
MAX_VERSIONS_PER_SKILL = 10

_VALID_EXECUTION_MODES = frozenset({"sequential", "parallel", "conditional"})


# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────

class SkillStatus(Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    EVOLVING = "evolving"  # being auto-improved


class ExecutionMode(Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"


class SkillOutcome(Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class SkillVersion:
    version: str
    content: str
    created_at: str = ""
    success_count: int = 0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    last_used_at: str = ""
    active: bool = True

    @property
    def total_uses(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        if self.total_uses == 0:
            return 0.0
        return self.success_count / self.total_uses

    def record_outcome(self, success: bool, latency_ms: float = 0.0) -> None:
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        n = self.total_uses
        self.avg_latency_ms = (self.avg_latency_ms * (n - 1) + latency_ms) / n
        self.last_used_at = datetime.now(timezone.utc).isoformat()


@dataclass
class SkillMeta:
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    author: str = "panda-agent"
    related_skills: List[str] = field(default_factory=list)
    execution_mode: str = "sequential"
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.execution_mode not in _VALID_EXECUTION_MODES:
            self.execution_mode = "sequential"


@dataclass
class SkillMatch:
    skill_name: str
    similarity: float
    skill: PandaSkill
    matched_tags: List[str] = field(default_factory=list)


@dataclass
class SkillExecutionReport:
    skill_name: str
    version: str
    outcome: SkillOutcome
    latency_ms: float
    error: str = ""
    suggestions: List[str] = field(default_factory=list)
    context_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvolutionProposal:
    skill_name: str
    current_version: str
    proposed_content: str
    reason: str
    expected_improvement: str


# ────────────────────────────────────────────────────────────────────
# PandaSkill
# ────────────────────────────────────────────────────────────────────


class PandaSkill:
    """A self-aware, versioned skill that tracks its own performance."""

    def __init__(
        self,
        meta: SkillMeta,
        content: str,
        versions: Optional[List[SkillVersion]] = None,
    ) -> None:
        self.meta = meta
        self._content = content
        self._versions: List[SkillVersion] = versions or []
        self._lock = threading.RLock()

        # Ensure current version exists in version history
        if not self._versions:
            self._versions.append(
                SkillVersion(
                    version=meta.version,
                    content=content,
                    created_at=meta.created_at,
                )
            )

    # ── Properties ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def content(self) -> str:
        return self._content

    @property
    def current_version(self) -> SkillVersion:
        with self._lock:
            for v in self._versions:
                if v.active:
                    return v
            return self._versions[-1]

    @property
    def success_rate(self) -> float:
        v = self.current_version
        return v.success_rate

    @property
    def total_uses(self) -> int:
        return sum(v.total_uses for v in self._versions)

    # ── Version Management ────────────────────────────────────────

    def record_execution(
        self,
        success: bool,
        latency_ms: float = 0.0,
    ) -> None:
        """Record execution outcome for the active version."""
        with self._lock:
            self.current_version.record_outcome(success, latency_ms)

    def evolve(
        self,
        new_content: str,
        reason: str = "Auto-improved from execution experience",
    ) -> str:
        """Create a new version with improved content.

        Returns the new version string.
        """
        with self._lock:
            # Bump version
            parts = [int(x) for x in self.meta.version.split(".")]
            parts[-1] += 1
            new_version = ".".join(str(x) for x in parts)

            # Deactivate current
            for v in self._versions:
                v.active = False

            # Create new version
            sv = SkillVersion(
                version=new_version,
                content=new_content,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            self._versions.append(sv)

            # Trim old versions
            if len(self._versions) > MAX_VERSIONS_PER_SKILL:
                self._versions = self._versions[-MAX_VERSIONS_PER_SKILL:]

            # Update meta
            self.meta.version = new_version
            self.meta.updated_at = sv.created_at
            self._content = new_content

            logger.info(
                "Skill '%s' evolved: %s → %s (reason: %s)",
                self.name, parts_to_str(parts, -1), new_version, reason,
            )
            return new_version

    def rollback(self) -> Optional[str]:
        """Rollback to the previous version if the current one is worse."""
        with self._lock:
            if len(self._versions) < 2:
                return None

            current = self._versions[-1]
            previous = self._versions[-2]

            if current.success_rate >= previous.success_rate:
                return None  # no need to rollback

            # Deactivate current, activate previous
            current.active = False
            previous.active = True
            self._content = previous.content
            self.meta.version = previous.version

            logger.warning(
                "Skill '%s' rolled back: %s (%.0f%%) → %s (%.0f%%)",
                self.name,
                current.version, current.success_rate * 100,
                previous.version, previous.success_rate * 100,
            )
            return previous.version

    def get_version_history(self) -> List[Dict[str, Any]]:
        """Return version history with metrics."""
        return [
            {
                "version": v.version,
                "success_rate": round(v.success_rate, 3),
                "total_uses": v.total_uses,
                "avg_latency_ms": round(v.avg_latency_ms, 1),
                "active": v.active,
                "created_at": v.created_at,
                "last_used_at": v.last_used_at,
            }
            for v in self._versions
        ]

    # ── Semantic Embedding ────────────────────────────────────────

    def _build_embedding_text(self) -> str:
        """Build text for embedding: name + description + tags."""
        parts = [self.name, self.meta.description]
        parts.extend(self.meta.tags)
        return " ".join(parts)

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meta": {
                "name": self.meta.name,
                "description": self.meta.description,
                "tags": self.meta.tags,
                "version": self.meta.version,
                "author": self.meta.author,
                "related_skills": self.meta.related_skills,
                "execution_mode": self.meta.execution_mode,
                "input_schema": self.meta.input_schema,
                "output_schema": self.meta.output_schema,
                "status": self.meta.status,
                "created_at": self.meta.created_at,
                "updated_at": self.meta.updated_at,
            },
            "content": self._content,
            "versions": [
                {
                    "version": v.version,
                    "content": v.content,
                    "success_count": v.success_count,
                    "failure_count": v.failure_count,
                    "avg_latency_ms": v.avg_latency_ms,
                    "last_used_at": v.last_used_at,
                    "active": v.active,
                }
                for v in self._versions
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PandaSkill":
        meta = SkillMeta(**data["meta"])
        versions = [SkillVersion(**v) for v in data.get("versions", [])]
        return cls(meta=meta, content=data.get("content", ""), versions=versions)


def parts_to_str(parts: List[int], offset: int) -> str:
    """Convert version parts to string, e.g. [1, 0, 0] → '1.0.1' with offset=-1."""
    parts = list(parts)
    if offset < 0:
        parts[offset] += 1
    return ".".join(str(x) for x in parts)
