"""
Dragon Agent — Expert Consultation Module (专家会诊模块)
=====================================================

When the Router classifies a query as very difficult (difficulty_score >= 7.0),
this module orchestrates a multi-model "expert consultation" — assembling the
strongest available models (tier3-large + tier4-premium), running parallel
exploration to gather diverse perspectives, and then convening a Jury Debate
to converge on the best answer.

Architecture::

    ┌──────────────┐     ┌──────────────────────┐     ┌────────────────┐
    │  Router       │────▶│  ExpertConsultation   │────▶│  User Approval │
    │  score >= 7   │     │  .assess()            │     │  (allow_consult)│
    └──────────────┘     └──────────┬───────────┘     └───────┬────────┘
                                    │                         │
                                    ▼                         ▼
                         ┌──────────────────────┐     ┌──────────────┐
                         │  ExplorerEnsemble     │     │  YES →       │
                         │  (tier3 + tier4)      │     │  .consult()  │
                         └──────────┬───────────┘     └──────┬───────┘
                                    │                        │
                                    ▼                        ▼
                         ┌──────────────────────┐     ┌──────────────┐
                         │  Jury Debate          │────▶│  Result /    │
                         │  (elite panel vote)    │     │  无法解决     │
                         └──────────────────────┘     └──────────────┘

Key Features
------------
* **Difficulty gating** — only triggers for difficulty_score >= 7.0
* **Success-rate estimation** — realistic success probabilities per difficulty band
* **User approval flow** — transparent cost/success estimates before proceeding
* **Elite panel assembly** — tier3-large + tier4-premium models only
* **Jury debate** — structured multi-round deliberation among elite models
* **Honest fallback** — returns "无法解决" when consensus cannot be reached
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from dragon.dispatch import DragonDispatcher
from dragon.jury import JuryDebate, JuryVerdict, VoteDecision, Ballot, DebateRound
from dragon.explorer import ExplorerEnsemble, ExplorationResult, ExplorerConfig, ExploreStrategy
from dragon.guard import AntiLoopGuard
from dragon.utils.cost import CostOptimizer, MODEL_TIERS

# ────────────────────────────────────────────────────────────────────
# Logger
# ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("dragon.consult")

# ════════════════════════════════════════════════════════════════════
# Difficulty → Success Rate Mapping
# ════════════════════════════════════════════════════════════════════

# Maps difficulty_score bands to (estimated_success_rate, label, recommendation)
DIFFICULTY_SUCCESS_TABLE: Dict[str, Dict[str, Any]] = {
    "0-2": {
        "min_score": 0.0,
        "max_score": 2.0,
        "success_rate": 0.98,
        "label": "极简单，直接回答",
        "needs_consultation": False,
        "recommendation": "标准路由即可处理，无需专家会诊。",
    },
    "3-4": {
        "min_score": 3.0,
        "max_score": 4.0,
        "success_rate": 0.92,
        "label": "简单",
        "needs_consultation": False,
        "recommendation": "单一行业模型即可胜任。",
    },
    "5-6": {
        "min_score": 5.0,
        "max_score": 6.0,
        "success_rate": 0.80,
        "label": "中等难度",
        "needs_consultation": False,
        "recommendation": "多探索者并行分析即可，无需会诊。",
    },
    "7": {
        "min_score": 7.0,
        "max_score": 7.99,
        "success_rate": 0.65,
        "label": "困难 — 建议专家会诊",
        "needs_consultation": True,
        "recommendation": "建议启动专家会诊，调用最强模型联合讨论。",
    },
    "8": {
        "min_score": 8.0,
        "max_score": 8.99,
        "success_rate": 0.45,
        "label": "很困难 — 强烈建议专家会诊",
        "needs_consultation": True,
        "recommendation": "强烈建议启动专家会诊，单一模型难以胜任。",
    },
    "9": {
        "min_score": 9.0,
        "max_score": 9.99,
        "success_rate": 0.25,
        "label": "极困难 — 即使专家会诊成功率也有限",
        "needs_consultation": True,
        "recommendation": "此问题极难，即使专家会诊也存在较高失败风险。",
    },
    "10": {
        "min_score": 10.0,
        "max_score": 10.0,
        "success_rate": 0.10,
        "label": "可能无法解决 — 建议重新表述问题",
        "needs_consultation": True,
        "recommendation": "成功率极低，建议用户重新表述或拆分问题。",
    },
}


def _lookup_success_rate(difficulty_score: float) -> Tuple[float, str, bool, str]:
    """Look up the estimated success rate and metadata for a difficulty score.

    Args:
        difficulty_score: 0.0–10.0 score from the Router.

    Returns:
        Tuple of (success_rate, label, needs_consultation, recommendation).
    """
    score = max(0.0, min(10.0, difficulty_score))

    for band_key in ["0-2", "3-4", "5-6", "7", "8", "9", "10"]:
        band = DIFFICULTY_SUCCESS_TABLE[band_key]
        if band["min_score"] <= score <= band["max_score"]:
            return (
                band["success_rate"],
                band["label"],
                band["needs_consultation"],
                band["recommendation"],
            )

    # Fallback (should never reach here)
    return (0.5, "未知难度", True, "建议专家会诊以获取最佳结果。")


# ════════════════════════════════════════════════════════════════════
# Elite Panel Configuration
# ════════════════════════════════════════════════════════════════════

# The elite consultation panel draws from tier3-large and tier4-premium models.
# Each entry: (explorer_name, model, provider, api_key_env, system_prompt)

ELITE_PANEL: List[Dict[str, str]] = [
    {
        "name": "首席分析官",
        "model": "deepseek-reasoner",
        "provider": "deepseek",
        "api_key_env": "DEEPSEEK_API_KEY",
        "perspective": "深度推理",
        "system_prompt": (
            "你是一位首席分析官（Chief Analysis Officer），拥有跨学科的深度推理能力。\n\n"
            "你的分析框架：\n"
            "1. **问题本质** — 识别问题的核心矛盾与关键约束\n"
            "2. **多维度拆解** — 从多个维度（技术、经济、社会、伦理等）拆解问题\n"
            "3. **因果链分析** — 追溯根本原因，构建因果逻辑链\n"
            "4. **方案推演** — 推演各种解决方案的链式后果\n\n"
            "请进行深度、严谨的分析。在回答末尾用 ## 核心洞察 和 ## 推荐方案 总结。"
        ),
    },
    {
        "name": "战略顾问",
        "model": "gpt-4o",
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "perspective": "战略咨询",
        "system_prompt": (
            "你是一位资深战略顾问（Strategy Consultant），具有麦肯锡级别的分析能力。\n\n"
            "你的分析框架：\n"
            "1. **MECE分解** — 相互独立、完全穷尽地拆解问题\n"
            "2. **假设驱动** — 建立核心假设，用数据和逻辑验证\n"
            "3. **利益相关方** — 识别所有关键方，分析其立场和影响力\n"
            "4. **80/20原则** — 识别最关键的因素，集中分析高影响领域\n\n"
            "请提供结构化、可执行的战略建议。在回答末尾用 ## 核心洞察 和 ## 行动方案 总结。"
        ),
    },
    {
        "name": "科学顾问",
        "model": "claude-sonnet-4",
        "provider": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "perspective": "科学分析",
        "system_prompt": (
            "你是一位科学顾问（Scientific Advisor），秉持科学方法论进行严谨分析。\n\n"
            "你的分析框架：\n"
            "1. **证据评估** — 评估现有证据的强度、来源和局限性\n"
            "2. **假设检验** — 对每个关键主张进行证伪测试\n"
            "3. **不确定性量化** — 明确标注每个结论的不确定性程度\n"
            "4. **替代解释** — 主动考虑替代假设和竞争理论\n\n"
            "请保持科学严谨性，区分事实与推测。在回答末尾用 ## 核心洞察 和 ## 研究建议 总结。"
        ),
    },
]


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════


@dataclass
class ConsultationAssessment:
    """Pre-consultation assessment result.

    Attributes:
        difficulty_score: Original 0–10 difficulty rating from the Router.
        estimated_success: Estimated probability of success (0.0–1.0).
        needs_consultation: True if expert consultation is recommended.
        recommended_panel: List of model names recommended for the panel.
        estimated_cost: Estimated USD cost for the full consultation.
        warning_message: Chinese-language warning/explanation for the user.
        difficulty_label: Human-readable difficulty label.
        recommendation: Detailed recommendation text.
    """

    difficulty_score: float
    estimated_success: float
    needs_consultation: bool
    recommended_panel: List[str] = field(default_factory=list)
    estimated_cost: float = 0.0
    warning_message: str = ""
    difficulty_label: str = ""
    recommendation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "difficulty_score": self.difficulty_score,
            "estimated_success": round(self.estimated_success, 4),
            "needs_consultation": self.needs_consultation,
            "recommended_panel": self.recommended_panel,
            "estimated_cost": round(self.estimated_cost, 6),
            "warning_message": self.warning_message,
            "difficulty_label": self.difficulty_label,
            "recommendation": self.recommendation,
        }


@dataclass
class ConsultationResult:
    """Result of an expert consultation.

    Attributes:
        solved: Whether the consultation reached a confident solution.
        solution: The final synthesized solution text (empty if not solved).
        confidence: Overall confidence in the solution (0.0–1.0).
        panel_used: List of model names that participated.
        debate_rounds: Number of debate rounds conducted.
        cost_usd: Total USD cost of the consultation.
        minority_opinions: Dissenting opinions from the jury debate.
        cannot_solve_reason: Explanation if solved=False.
        verdict_decision: The VoteDecision from the jury (CONSENSUS/MAJORITY/etc.).
        exploration_count: Number of successful explorer results.
        elapsed_ms: Total wall-clock time for the consultation.
    """

    solved: bool
    solution: str
    confidence: float
    panel_used: List[str] = field(default_factory=list)
    debate_rounds: int = 0
    cost_usd: float = 0.0
    minority_opinions: List[str] = field(default_factory=list)
    cannot_solve_reason: str = ""
    verdict_decision: str = ""
    exploration_count: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "solved": self.solved,
            "solution": self.solution,
            "confidence": round(self.confidence, 4),
            "panel_used": self.panel_used,
            "debate_rounds": self.debate_rounds,
            "cost_usd": round(self.cost_usd, 6),
            "minority_opinions": self.minority_opinions,
            "cannot_solve_reason": self.cannot_solve_reason,
            "verdict_decision": self.verdict_decision,
            "exploration_count": self.exploration_count,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ════════════════════════════════════════════════════════════════════
# Expert Consultation Engine
# ════════════════════════════════════════════════════════════════════


class ExpertConsultation:
    """Orchestrates multi-model expert consultations for difficult queries.

    When the Router rates a query at difficulty_score >= 7.0, this engine:
    1. Assesses the problem and estimates success probability
    2. Requests user approval with transparent cost/success estimates
    3. Assembles an elite panel (tier3-large + tier4-premium models)
    4. Runs parallel exploration to gather diverse perspectives
    5. Convenes a Jury Debate to converge on the best answer
    6. Returns the solution or honestly states "无法解决"

    Typical usage::

        from dragon.dispatch import DragonDispatcher
        from dragon.jury import JuryDebate
        from dragon.consult import ExpertConsultation

        dispatcher = DragonDispatcher()
        # ... register providers ...
        jury = JuryDebate(dispatcher, min_consensus=0.7)

        consult = ExpertConsultation(dispatcher, jury)

        assessment = consult.assess(
            query="如何设计一个全球碳交易市场？",
            difficulty_score=8.5,
            industry="finance",
        )

        if assessment.needs_consultation:
            approval = await consult.request_approval(assessment)
            # Show approval["message"] to user...
            # If user approves:
            result = await consult.consult(query, industry)
            print(result.solution)

    Parameters:
        dispatcher: Configured DragonDispatcher for all LLM calls.
        jury: Configured JuryDebate instance for deliberation.
        cost_optimizer: Optional CostOptimizer for cost tracking and budget control.
        guard: Optional AntiLoopGuard (created internally if not provided).
    """

    # ────────────────────────────────────────────────────────────────
    # Consultation constants
    # ────────────────────────────────────────────────────────────────

    # Minimum difficulty score that triggers consultation
    CONSULT_THRESHOLD = 7.0

    # Minimum jury consensus required to declare "solved"
    MIN_SOLVE_CONSENSUS = 0.6

    # Minimum verdict confidence to declare "solved"
    MIN_SOLVE_CONFIDENCE = 0.4

    # Maximum explorer timeout per model (seconds)
    EXPLORER_TIMEOUT = 45.0

    # Estimated tokens per explorer call (for cost estimation)
    EST_TOKENS_IN = 3000
    EST_TOKENS_OUT = 1500

    def __init__(
        self,
        dispatcher: DragonDispatcher,
        jury: JuryDebate,
        cost_optimizer: Optional[CostOptimizer] = None,
        guard: Optional[AntiLoopGuard] = None,
    ) -> None:
        """Initialize the expert consultation engine.

        Args:
            dispatcher: DragonDispatcher for LLM dispatch calls.
            jury: JuryDebate instance for multi-model deliberation.
            cost_optimizer: CostOptimizer for cost tracking. Created internally
                with default budget if not provided.
            guard: AntiLoopGuard for loop detection. Created internally if not
                provided.
        """
        self._dispatcher = dispatcher
        self._jury = jury
        self._cost = cost_optimizer or CostOptimizer(daily_budget=5.0)
        self._guard = guard or AntiLoopGuard()

        # Build elite explorer panel
        self._elite_explorers: Dict[str, ExplorerConfig] = {}
        self._panel_model_names: List[str] = []
        self._build_elite_panel()

        logger.info(
            "ExpertConsultation initialized — panel=%d models, threshold=%.1f",
            len(self._panel_model_names),
            self.CONSULT_THRESHOLD,
        )

    # ────────────────────────────────────────────────────────────────
    # Elite Panel Construction
    # ────────────────────────────────────────────────────────────────

    def _build_elite_panel(self) -> None:
        """Build the elite explorer panel from tier3 + tier4 models.

        Registers each elite model as an ExplorerConfig and stores their
        names for panel reporting.
        """
        for entry in ELITE_PANEL:
            config = ExplorerConfig(
                name=entry["name"],
                model=entry["model"],
                provider=entry["provider"],
                api_key_env=entry["api_key_env"],
                system_prompt=entry["system_prompt"],
                perspective=entry["perspective"],
                max_tokens=2048,
                temperature=0.3,  # Low temperature for precise analysis
            )
            self._elite_explorers[entry["name"]] = config
            self._panel_model_names.append(entry["model"])

        logger.debug(
            "Elite panel built: %s",
            [f"{e.name}({e.model})" for e in self._elite_explorers.values()],
        )

    # ────────────────────────────────────────────────────────────────
    # Assessment
    # ────────────────────────────────────────────────────────────────

    def assess(
        self,
        query: str,
        difficulty_score: float,
        industry: str = "general",
    ) -> ConsultationAssessment:
        """Assess whether expert consultation is needed and estimate success.

        Args:
            query: The original user question.
            difficulty_score: Difficulty rating from the Router (0.0–10.0).
            industry: Target industry key (finance/medical/legal/education/general).

        Returns:
            ConsultationAssessment with success estimate, panel, and cost.
        """
        score = max(0.0, min(10.0, difficulty_score))

        success_rate, label, needs_consult, recommendation = _lookup_success_rate(score)

        # Estimate cost: each elite model generates ~EST_TOKENS_IN input and
        # ~EST_TOKENS_OUT output tokens.  Jury debate adds ~2× overhead.
        estimated_cost = self._estimate_consultation_cost()

        # Build warning message
        if needs_consult:
            warning = (
                f"⚠️ 此问题难度评分为 **{score:.1f}/10**，成功率预估 **{success_rate * 100:.0f}%**。\n\n"
                f"**难度等级**: {label}\n"
                f"**建议**: {recommendation}\n\n"
                f"**专家会诊方案**:\n"
                f"- 会诊成员: {', '.join(self._panel_model_names)}\n"
                f"- 预估成本: ${estimated_cost:.4f} USD\n"
                f"- 流程: 多模型并行分析 → 评审辩论 → 综合裁决\n\n"
                f"是否启动专家会诊？"
            )
        else:
            warning = (
                f"✅ 此问题难度评分为 **{score:.1f}/10**，成功率预估 **{success_rate * 100:.0f}%**。\n"
                f"**难度等级**: {label}\n"
                f"**建议**: {recommendation}\n\n"
                f"当前难度无需启动专家会诊，标准分析流程即可处理。"
            )

        return ConsultationAssessment(
            difficulty_score=score,
            estimated_success=success_rate,
            needs_consultation=needs_consult,
            recommended_panel=list(self._panel_model_names),
            estimated_cost=estimated_cost,
            warning_message=warning,
            difficulty_label=label,
            recommendation=recommendation,
        )

    def _estimate_consultation_cost(self) -> float:
        """Estimate the total USD cost of a consultation.

        Accounts for:
        - Exploration: each elite model called once
        - Jury debate: each model called ~3 times (3 rounds)
        - Synthesizer: one premium synthesis call

        Returns:
            Estimated cost in USD.
        """
        total = 0.0

        # Cost per elite model for exploration
        for entry in ELITE_PANEL:
            model = entry["model"]
            tier = self._find_tier(model)
            if tier:
                tier_info = MODEL_TIERS.get(tier, {})
                cost_one_call = (
                    (self.EST_TOKENS_IN / 1_000_000) * tier_info.get("price_per_1M_in", 0)
                    + (self.EST_TOKENS_OUT / 1_000_000) * tier_info.get("price_per_1M_out", 0)
                )
                total += cost_one_call

        # Jury debate: each juror called 3 times (rounds 1, 2, 3)
        total *= 3

        # Synthesizer: one extra call
        synth_cost = (
            (self.EST_TOKENS_IN / 1_000_000) * 0.55  # tier3_large input
            + (self.EST_TOKENS_OUT / 1_000_000) * 2.19  # tier3_large output
        )
        total += synth_cost

        return round(total, 6)

    @staticmethod
    def _find_tier(model: str) -> Optional[str]:
        """Find which MODEL_TIERS tier a model belongs to."""
        model_lower = model.lower()
        for tier_name, tier_info in MODEL_TIERS.items():
            for m in tier_info.get("models", []):
                if m.lower() == model_lower or m.lower() in model_lower:
                    return tier_name
        return None

    # ────────────────────────────────────────────────────────────────
    # Approval Request
    # ────────────────────────────────────────────────────────────────

    async def request_approval(
        self,
        assessment: ConsultationAssessment,
    ) -> Dict[str, Any]:
        """Generate the approval request message for the user.

        This method formats the assessment into a user-facing message with
        clear cost/benefit information.  It does NOT block waiting for
        approval — that is handled by the API layer.

        Args:
            assessment: The ConsultationAssessment from assess().

        Returns:
            Dict with keys:
                - needs_approval: bool — whether user approval is required
                - message: str — formatted message for the user
                - estimated_success_rate: float
                - model_panel: List[str]
                - estimated_cost: float
                - difficulty_score: float
        """
        return {
            "needs_approval": assessment.needs_consultation,
            "message": assessment.warning_message,
            "estimated_success_rate": round(assessment.estimated_success, 4),
            "model_panel": assessment.recommended_panel,
            "estimated_cost": round(assessment.estimated_cost, 6),
            "difficulty_score": assessment.difficulty_score,
        }

    # ────────────────────────────────────────────────────────────────
    # Core: Consult
    # ────────────────────────────────────────────────────────────────

    async def consult(
        self,
        query: str,
        industry: str = "general",
        memory_context: Optional[Dict[str, Any]] = None,
    ) -> ConsultationResult:
        """Run the full expert consultation: elite exploration + jury debate.

        This is the main entry point after user approval.  It:
        1. Creates a temporary ExplorerEnsemble with elite models only
        2. Runs parallel exploration to gather diverse analyses
        3. Converts exploration results into jury debate proposals
        4. Convenes the Jury Debate for structured deliberation
        5. Returns the final solution or honestly reports failure

        Args:
            query: The original user question/problem to solve.
            industry: Target industry for context.
            memory_context: Optional memory graph context dict.

        Returns:
            ConsultationResult with solution, confidence, panel, and cost.
        """
        t_start = time.monotonic()
        total_cost = 0.0
        panel_used: List[str] = []
        minority_opinions: List[str] = []

        logger.info(
            "Starting expert consultation — query=%.100s, industry=%s",
            query,
            industry,
        )

        # ── 1. Build elite ExplorerEnsemble ──────────────────────────
        ensemble = self._build_elite_ensemble()

        # ── 2. Run parallel exploration with elite panel ─────────────
        try:
            exploration_results = await asyncio.wait_for(
                ensemble.explore(
                    query=query,
                    industry=industry,
                    difficulty="complex",  # Always use complex to get all explorers
                    strategy=ExploreStrategy.PARALLEL,
                    max_explorers=len(self._elite_explorers),
                ),
                timeout=self.EXPLORER_TIMEOUT * len(self._elite_explorers) + 10,
            )
        except asyncio.TimeoutError:
            logger.error("Exploration timed out for query=%.100s", query)
            return ConsultationResult(
                solved=False,
                solution="",
                confidence=0.0,
                panel_used=self._panel_model_names,
                debate_rounds=0,
                cost_usd=total_cost,
                cannot_solve_reason="专家探索阶段超时，无法完成分析。请简化问题或稍后重试。",
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )

        if not exploration_results:
            logger.warning("No exploration results obtained for query=%.100s", query)
            return ConsultationResult(
                solved=False,
                solution="",
                confidence=0.0,
                panel_used=self._panel_model_names,
                debate_rounds=0,
                cost_usd=total_cost,
                cannot_solve_reason="所有专家模型均未能返回有效分析结果。请检查 API 配置或稍后重试。",
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )

        # Track cost and panel
        for r in exploration_results:
            total_cost += r.cost_usd
            if r.model_used not in panel_used:
                panel_used.append(r.model_used)

        logger.info(
            "Exploration complete: %d results, cost=$%.6f",
            len(exploration_results),
            total_cost,
        )

        # ── 3. Convert exploration results to jury proposals ─────────
        proposals = self._explorations_to_proposals(exploration_results)

        if len(proposals) < 2:
            # Not enough distinct proposals for a debate — return best
            best = exploration_results[0] if exploration_results else None
            if best:
                return ConsultationResult(
                    solved=best.confidence >= self.MIN_SOLVE_CONFIDENCE,
                    solution=best.raw_content,
                    confidence=best.confidence,
                    panel_used=panel_used,
                    debate_rounds=0,
                    cost_usd=total_cost,
                    exploration_count=len(exploration_results),
                    elapsed_ms=(time.monotonic() - t_start) * 1000,
                    cannot_solve_reason=(
                        "" if best.confidence >= self.MIN_SOLVE_CONFIDENCE
                        else "探索结果置信度过低，且无法形成多方案辩论。"
                    ),
                )
            return ConsultationResult(
                solved=False,
                solution="",
                confidence=0.0,
                panel_used=panel_used,
                cost_usd=total_cost,
                cannot_solve_reason="无法生成足够的候选方案进行辩论。",
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )

        # ── 4. Run Jury Debate ───────────────────────────────────────
        memory_ctx_str: Optional[str] = None
        if memory_context:
            try:
                memory_ctx_str = json.dumps(memory_context, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                memory_ctx_str = str(memory_context)

        try:
            verdict: JuryVerdict = await asyncio.wait_for(
                self._jury.deliberate(
                    query=query,
                    proposals=proposals,
                    memory_context=memory_ctx_str,
                ),
                timeout=120.0,  # 2-minute timeout for full debate
            )
        except asyncio.TimeoutError:
            logger.error("Jury debate timed out for query=%.100s", query)
            return ConsultationResult(
                solved=False,
                solution="",
                confidence=0.0,
                panel_used=panel_used,
                debate_rounds=0,
                cost_usd=total_cost,
                cannot_solve_reason="评审辩论阶段超时。专家模型响应过慢，请稍后重试。",
                exploration_count=len(exploration_results),
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )
        except Exception as exc:
            logger.exception("Jury debate failed: %s", exc)
            return ConsultationResult(
                solved=False,
                solution="",
                confidence=0.0,
                panel_used=panel_used,
                debate_rounds=0,
                cost_usd=total_cost,
                cannot_solve_reason=f"评审辩论异常: {exc}",
                exploration_count=len(exploration_results),
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )

        # Estimate jury cost (rough)
        jury_rounds = len(verdict.debate_transcript)
        debate_cost_est = jury_rounds * len(panel_used) * 0.005  # ~$0.005 per juror-round
        total_cost += debate_cost_est

        # Collect minority opinions
        if verdict.minority_report:
            minority_opinions.append(verdict.minority_report)
        if verdict.deception_flags:
            minority_opinions.extend(verdict.deception_flags)

        # ── 5. Build result ──────────────────────────────────────────
        solved = (
            verdict.decision in (VoteDecision.CONSENSUS, VoteDecision.MAJORITY)
            and verdict.confidence >= self.MIN_SOLVE_CONFIDENCE
            and bool(verdict.winner)
        )

        if solved:
            # Extract winner's solution from proposals
            winner_proposal = proposals.get(verdict.winner, {})
            solution_text = winner_proposal.get("summary", verdict.recommendation)

            return ConsultationResult(
                solved=True,
                solution=solution_text,
                confidence=verdict.confidence,
                panel_used=panel_used,
                debate_rounds=jury_rounds,
                cost_usd=round(total_cost, 6),
                minority_opinions=minority_opinions,
                verdict_decision=verdict.decision.value,
                exploration_count=len(exploration_results),
                elapsed_ms=(time.monotonic() - t_start) * 1000,
            )

        # Not solved — build honest failure report
        cannot_solve_reason = self._build_failure_reason(verdict, exploration_results)

        return ConsultationResult(
            solved=False,
            solution="",
            confidence=verdict.confidence,
            panel_used=panel_used,
            debate_rounds=jury_rounds,
            cost_usd=round(total_cost, 6),
            minority_opinions=minority_opinions,
            cannot_solve_reason=cannot_solve_reason,
            verdict_decision=verdict.decision.value,
            exploration_count=len(exploration_results),
            elapsed_ms=(time.monotonic() - t_start) * 1000,
        )

    # ────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ────────────────────────────────────────────────────────────────

    def _build_elite_ensemble(self) -> ExplorerEnsemble:
        """Build a temporary ExplorerEnsemble with only the elite panel.

        Registers all elite explorers with the dispatcher and returns a
        fresh ensemble configured for consultation-grade analysis.

        Returns:
            Configured ExplorerEnsemble.
        """
        ensemble = ExplorerEnsemble(
            dispatcher=self._dispatcher,
            guard=self._guard,
            cost=self._cost,
        )

        # Unregister all built-in explorers and replace with elite only
        for key in list(ensemble.registered_explorers):
            ensemble.unregister_explorer(key)

        # Register elite explorers
        for name, config in self._elite_explorers.items():
            ensemble.register_explorer(name, config)

        logger.debug(
            "Elite ensemble built with %d explorers: %s",
            len(ensemble.registered_explorers),
            ensemble.registered_explorers,
        )

        return ensemble

    @staticmethod
    def _explorations_to_proposals(
        explorations: List[ExplorationResult],
    ) -> Dict[str, Dict[str, str]]:
        """Convert exploration results to jury debate proposals.

        Each exploration becomes a proposal identified by a letter (A, B, C, ...).

        Args:
            explorations: List of ExplorationResult from the ensemble.

        Returns:
            Dict mapping proposal ID (e.g., "A") to dict with "summary" and "author".
        """
        proposals: Dict[str, Dict[str, str]] = {}
        labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        for i, exp in enumerate(explorations):
            if i >= len(labels):
                break
            label = labels[i]

            # Build a concise summary from the findings
            summary_parts: List[str] = []
            if exp.findings:
                summary_parts.append("## 关键发现")
                for f in exp.findings[:5]:
                    summary_parts.append(f"- {f}")
            if exp.approach:
                summary_parts.append(f"\n## 分析视角\n{exp.approach}")
            if exp.caveats:
                summary_parts.append(f"\n## 局限性\n{exp.caveats}")
            if exp.raw_content:
                # Include raw content as the full proposal body
                summary_parts.append(f"\n## 完整分析\n{exp.raw_content[:3000]}")

            proposals[label] = {
                "summary": "\n".join(summary_parts) if summary_parts else exp.raw_content[:2000],
                "author": f"{exp.explorer_name} ({exp.model_used})",
            }

        logger.info("Converted %d explorations into %d proposals", len(explorations), len(proposals))
        return proposals

    @staticmethod
    def _build_failure_reason(
        verdict: JuryVerdict,
        explorations: List[ExplorationResult],
    ) -> str:
        """Build an honest, informative failure reason string.

        Args:
            verdict: The JuryVerdict from deliberation.
            explorations: The exploration results.

        Returns:
            Chinese-language explanation of why consultation failed.
        """
        parts: List[str] = ["## 专家会诊结论：无法解决\n"]

        # Decision-based reason
        if verdict.decision == VoteDecision.DEADLOCK:
            parts.append(
                "评审团陷入僵局（DEADLOCK）：各专家模型的投票结果平局，"
                "未能形成明确的优胜方案。"
            )
        elif verdict.decision == VoteDecision.SPLIT:
            parts.append(
                "评审团意见分裂（SPLIT）：各专家模型之间存在较大分歧，"
                "未能达成有效共识。"
            )
        else:
            parts.append(
                f"评审结果为 {verdict.decision.value}，"
                f"但综合置信度 {verdict.confidence:.0%} 低于最低阈值，"
                f"无法给出可靠答案。"
            )

        # Add minority report if available
        if verdict.minority_report:
            parts.append(f"\n### 少数意见\n{verdict.minority_report[:500]}")

        # Add deception flags if any
        if verdict.deception_flags:
            parts.append("\n### 评审异常标记")
            for flag in verdict.deception_flags[:3]:
                parts.append(f"- {flag}")

        # Summarize exploration confidence
        if explorations:
            avg_conf = sum(e.confidence for e in explorations) / len(explorations)
            parts.append(f"\n### 探索阶段摘要\n- 参与专家: {len(explorations)} 位")
            parts.append(f"- 平均置信度: {avg_conf:.1%}")
            parts.append(f"- 最低置信度: {min(e.confidence for e in explorations):.1%}")

        parts.append(
            "\n### 建议\n"
            "- 尝试重新表述问题，提供更多背景信息和约束条件\n"
            "- 将复杂问题拆分为多个子问题逐个解决\n"
            "- 补充关键数据或领域知识后再尝试"
        )

        return "\n".join(parts)


# ════════════════════════════════════════════════════════════════════
# Module-level convenience functions
# ════════════════════════════════════════════════════════════════════


def get_difficulty_band(difficulty_score: float) -> Dict[str, Any]:
    """Get the difficulty band metadata for a given score.

    Convenience function for external consumers (API layer, logging, etc.).

    Args:
        difficulty_score: 0.0–10.0 score from the Router.

    Returns:
        Dict with keys: success_rate, label, needs_consultation, recommendation.
    """
    success_rate, label, needs_consult, recommendation = _lookup_success_rate(difficulty_score)
    return {
        "success_rate": success_rate,
        "label": label,
        "needs_consultation": needs_consult,
        "recommendation": recommendation,
    }


def should_consult(difficulty_score: float) -> bool:
    """Quick check: does this difficulty score warrant expert consultation?

    Args:
        difficulty_score: 0.0–10.0 score from the Router.

    Returns:
        True if consultation is recommended.
    """
    return difficulty_score >= ExpertConsultation.CONSULT_THRESHOLD
