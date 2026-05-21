"""
Panda Agent — Explorer Ensemble Module
=======================================

Parallel multi-model exploration engine. Dispatches the same problem to
multiple industry-specific LLMs simultaneously, collects findings from
diverse perspectives, and synthesizes the optimal answer.

Architecture::

    ┌──────────────┐     ┌─────────────────────────┐     ┌───────────────┐
    │  User Query  │────▶│  ExplorerEnsemble        │────▶│  Synthesizer  │
    │  + Industry  │     │  ┌──────┐┌──────┐┌──────┐│     │  (merge)      │
    └──────────────┘     │  │ Exp1 ││ Exp2 ││ Exp3 ││     └───────────────┘
                         │  └──┬───┘└──┬───┘└──┬───┘│
                         │     │       │       │    │
                         └─────┼───────┼───────┼────┘
                               ▼       ▼       ▼
                         PandaDispatcher.dispatch()

Key Features
------------
* **Parallel exploration** — asyncio.gather() runs all explorers simultaneously
* **Multi-perspective** — each explorer uses a distinct system prompt and model
* **Smart selection** — difficulty-based: 1 explorer for simple, up to 5 for complex
* **Anti-herd bias** — every explorer uses a different model
* **Loop guard** — AntiLoopGuard integration detects and breaks infinite patterns
* **Cost tracking** — CostOptimizer records per-explorer token usage
* **Synthesis** — premium model merges findings with consensus/dissent annotation
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from panda.dispatch import PandaDispatcher, DispatchResult, ProviderProfile
from panda.guard import AntiLoopGuard, LoopAction, ActionType, LoopDetection
from panda.utils.cost import CostOptimizer, MODEL_TIERS, _find_tier_for_model

# ────────────────────────────────────────────────────────────────────
# Logger
# ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("panda.explorer")

# ════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════


class ExploreStrategy(Enum):
    """Exploration dispatch strategies.

    * **PARALLEL** — all explorers run simultaneously (default, fastest)
    * **SEQUENTIAL** — run explorers one at a time, each building on prior results
    * **DIVERSE** — pick maximally different perspectives to reduce bias
    * **DEPTH_FIRST** — one explorer goes deep, follow-ups fill gaps
    * **BREADTH_FIRST** — all explorers produce summaries, synthesizer picks best
    """

    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"
    DIVERSE = "diverse"
    DEPTH_FIRST = "depth_first"
    BREADTH_FIRST = "breadth_first"


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════


@dataclass
class ExplorerConfig:
    """Configuration for a single explorer.

    Attributes:
        name: Human-readable name (e.g. "金融视角", "技术视角")
        model: Model identifier (e.g. "deepseek-chat")
        system_prompt: System-level prompt defining the perspective
        perspective: Short perspective label for reporting
        provider: API provider namespace ("openai", "anthropic", "deepseek")
        api_key_env: Environment variable name for the API key
        base_url: Optional OpenAI-compatible base URL override
        max_tokens: Maximum completion tokens
        temperature: Sampling temperature (0.0 = deterministic)
    """

    name: str
    model: str
    system_prompt: str
    perspective: str = ""
    provider: str = "openai"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: Optional[str] = None
    max_tokens: int = 1024
    temperature: float = 0.7

    def to_provider_profile(self) -> ProviderProfile:
        """Convert to a :class:`ProviderProfile` for the PandaDispatcher."""
        return ProviderProfile(
            name=f"explorer:{self.name}",
            provider=self.provider,
            model=self.model,
            api_key_env=self.api_key_env,
            base_url=self.base_url,
            system_prompt=self.system_prompt,
            timeout=30.0,  # per-explorer timeout
        )


@dataclass
class ExplorationResult:
    """Result from a single explorer's investigation.

    Attributes:
        explorer_name: Name of the explorer (e.g. "金融视角")
        model_used: Model identifier used for this exploration
        findings: List of key findings from this perspective
        approach: Description of the analytical approach taken
        confidence: Self-assessed confidence score (0.0 – 1.0)
        references: Cited sources or reference materials
        caveats: Known limitations or caveats in the analysis
        cost_usd: Estimated API cost for this exploration
        raw_content: Full raw response from the model
        latency_ms: Round-trip latency in milliseconds
    """

    explorer_name: str
    model_used: str
    findings: List[str] = field(default_factory=list)
    approach: str = ""
    confidence: float = 0.5
    references: List[str] = field(default_factory=list)
    caveats: str = ""
    cost_usd: float = 0.0
    raw_content: str = ""
    latency_ms: float = 0.0


# ════════════════════════════════════════════════════════════════════
# Related-industry adjacency (for smart explorer selection)
# ════════════════════════════════════════════════════════════════════

_RELATED_INDUSTRIES: Dict[str, List[str]] = {
    "finance": ["general", "legal", "education"],
    "medical": ["general", "legal", "education"],
    "legal": ["general", "finance", "education"],
    "education": ["general", "finance", "medical"],
    "general": ["finance", "medical", "legal", "education"],
}

# Industry → Chinese label for prompt building
_INDUSTRY_LABELS: Dict[str, str] = {
    "finance": "金融",
    "medical": "医疗",
    "legal": "法律",
    "education": "教育",
    "general": "通用",
}

# ════════════════════════════════════════════════════════════════════
# Built-in Explorer Presets
# ════════════════════════════════════════════════════════════════════

_BUILTIN_EXPLORERS: Dict[str, ExplorerConfig] = {
    # ── Finance ─────────────────────────────────────────────────────
    "finance": ExplorerConfig(
        name="金融视角",
        model="deepseek-chat",
        provider="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        system_prompt=(
            "你是一位资深金融分析师，拥有20年投资银行和风险管理的经验。\n\n"
            "你的分析框架：\n"
            "1. **财务维度** — 收入、成本、利润、现金流、资产负债表分析\n"
            "2. **市场维度** — 市场规模、竞争格局、市场份额、增长趋势\n"
            "3. **风险维度** — 市场风险、信用风险、操作风险、合规风险\n"
            "4. **估值维度** — DCF、可比公司、先例交易等估值方法\n\n"
            "请从金融角度深入分析问题，给出结构化的专业意见。"
            "在回答末尾，用## 关键发现 和 ## 风险提示 的格式总结你的核心观点。"
        ),
        perspective="金融分析",
    ),
    # ── Medical ─────────────────────────────────────────────────────
    "medical": ExplorerConfig(
        name="医学视角",
        model="deepseek-chat",
        provider="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        system_prompt=(
            "你是一位临床医学专家，同时具有公共卫生和医学研究背景。\n\n"
            "你的分析框架：\n"
            "1. **病因病理** — 疾病的生物学机制和病理生理过程\n"
            "2. **临床诊断** — 症状识别、鉴别诊断、检查方案\n"
            "3. **治疗策略** — 循证医学、药物治疗、手术及介入方案\n"
            "4. **预后管理** — 愈后、康复、长期管理和预防策略\n\n"
            "请从医学专业角度分析问题，基于循证医学给出严谨的建议。"
            "在回答末尾，用## 关键发现 和 ## 注意事项 的格式总结你的核心观点。"
        ),
        perspective="医学分析",
    ),
    # ── Legal ───────────────────────────────────────────────────────
    "legal": ExplorerConfig(
        name="法律视角",
        model="deepseek-chat",
        provider="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        system_prompt=(
            "你是一位资深法律顾问，拥有商事诉讼和合规咨询的丰富经验。\n\n"
            "你的分析框架：\n"
            "1. **法律定性** — 明确涉及的法律关系和可能适用的法域\n"
            "2. **法规依据** — 援引具体的法律条文、司法解释和指导案例\n"
            "3. **风险评估** — 合规风险、诉讼风险、合同风险的系统评估\n"
            "4. **应对策略** — 预防性措施、争议解决方案、合规建议\n\n"
            "请从法律专业角度分析问题，确保引用的法条准确、分析逻辑严密。"
            "在回答末尾，用## 关键发现 和 ## 法律风险 的格式总结你的核心观点。"
        ),
        perspective="法律分析",
    ),
    # ── Education ───────────────────────────────────────────────────
    "education": ExplorerConfig(
        name="教育视角",
        model="deepseek-chat",
        provider="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        system_prompt=(
            "你是一位教育学和认知科学专家，专注于学习设计和能力培养。\n\n"
            "你的分析框架：\n"
            "1. **学习目标** — 知识、技能、素养三维目标的拆解\n"
            "2. **认知规律** — 基于认知负荷理论、建构主义的学习路径\n"
            "3. **教学方法** — 差异化教学、项目式学习、翻转课堂等策略\n"
            "4. **评估反馈** — 形成性评估、总结性评估、元认知培养\n\n"
            "请从教育专业角度分析问题，给出可落地的教学设计和学习建议。"
            "在回答末尾，用## 关键发现 和 ## 教学建议 的格式总结你的核心观点。"
        ),
        perspective="教育分析",
    ),
    # ── General ─────────────────────────────────────────────────────
    "general": ExplorerConfig(
        name="通用视角",
        model="deepseek-chat",
        provider="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        system_prompt=(
            "你是一位综合型战略顾问，擅长从多角度系统性地分析复杂问题。\n\n"
            "你的分析框架：\n"
            "1. **问题拆解** — 将复杂问题分解为可管理的子问题\n"
            "2. **多维度分析** — 从经济、社会、技术、环境等多维度审视\n"
            "3. **利益相关方** — 识别关键利益方，分析各方诉求和影响\n"
            "4. **方案评估** — 对可行方案进行比较分析和优先级排序\n\n"
            "请给出全面、平衡的分析，覆盖问题的多个方面。"
            "在回答末尾，用## 关键发现 和 ## 行动建议 的格式总结你的核心观点。"
        ),
        perspective="综合分析",
    ),
}

# ════════════════════════════════════════════════════════════════════
# Synthesizer prompt template
# ════════════════════════════════════════════════════════════════════

_SYNTHESIZER_SYSTEM_PROMPT = """你是综合分析师（Synthesizer），负责融合多位专家从不同角度的分析结果。

你的任务：
1. **提取共识** — 找出所有专家都同意的核心观点
2. **标注分歧** — 明确指出专家之间存在分歧的地方，并分析分歧的原因
3. **综合方案** — 在共识的基础上，结合各专家的独特视角，给出最佳综合方案
4. **置信度评估** — 对综合方案的每个关键结论给出置信度说明

输出格式：
## 共识点
- ...
## 分歧点
- ...
## 综合方案
- ...
## 置信度说明
- ...
"""

# ════════════════════════════════════════════════════════════════════
# Explorer Ensemble
# ════════════════════════════════════════════════════════════════════


class ExplorerEnsemble:
    """Parallel multi-model exploration engine.

    Dispatches the same problem to multiple industry LLMs simultaneously,
    collects their findings from diverse perspectives, and synthesizes
    the best answer.

    Typical usage::

        dispatcher = PandaDispatcher()
        guard = AntiLoopGuard()
        cost = CostOptimizer(daily_budget=1.0)
        ensemble = ExplorerEnsemble(dispatcher, guard, cost)

        results = await ensemble.explore(
            query="如何评估一家AI创业公司的投资价值？",
            industry="finance",
            difficulty="complex",
            strategy=ExploreStrategy.PARALLEL,
        )

        answer = await ensemble.synthesize(query, results, "finance")
        print(answer)
    """

    # ────────────────────────────────────────────────────────────────
    # Per-explorer timeout (seconds)
    # ────────────────────────────────────────────────────────────────
    EXPLORER_TIMEOUT = 30.0

    # ────────────────────────────────────────────────────────────────
    # Synthesizer uses a premium model for best merge quality
    # ────────────────────────────────────────────────────────────────
    SYNTHESIZER_MODEL = "deepseek-reasoner"
    SYNTHESIZER_PROVIDER = "deepseek"
    SYNTHESIZER_NAME = "synthesizer"

    # ─────────────────────────────────────────────────────────────────
    # Industry key prefix used when registering explorers with dispatcher
    # ─────────────────────────────────────────────────────────────────
    _EXPLORER_KEY_PREFIX = "explorer:"

    def __init__(
        self,
        dispatcher: PandaDispatcher,
        guard: AntiLoopGuard,
        cost: CostOptimizer,
    ) -> None:
        """Initialize the explorer ensemble.

        Args:
            dispatcher: PandaDispatcher for LLM dispatch calls.
            guard: AntiLoopGuard for loop detection and mitigation.
            cost: CostOptimizer for model selection and cost tracking.
        """
        self.dispatcher = dispatcher
        self.guard = guard
        self.cost = cost
        self._session_id: str = ""   # set by caller for interrupt tracking

        # Explorer registry: name → ExplorerConfig
        self._explorers: Dict[str, ExplorerConfig] = {}

        # Track which explorers are already registered with the dispatcher
        self._dispatcher_registered: Set[str] = set()

        # Strategy for the current exploration session
        self._current_strategy: Optional[str] = None

        # Register built-in explorers
        for industry, config in _BUILTIN_EXPLORERS.items():
            self.register_explorer(industry, config)

        # Register synthesizer provider
        self._register_synthesizer()

        logger.info(
            "ExplorerEnsemble initialized — %d explorers, synthesizer=%s",
            len(self._explorers),
            self.SYNTHESIZER_MODEL,
        )

    # ────────────────────────────────────────────────────────────────
    # Explorer Registry
    # ────────────────────────────────────────────────────────────────

    def register_explorer(self, name: str, config: ExplorerConfig) -> None:
        """Register a new explorer (or overwrite an existing one).

        Args:
            name: Unique explorer key (e.g. "finance", "medical", "custom_1").
            config: :class:`ExplorerConfig` with model, system prompt, etc.
        """
        self._explorers[name] = config
        # Ensure dispatcher has this provider registered
        self._ensure_dispatcher_registered(config)
        logger.info(
            "Registered explorer '%s' (model=%s, perspective=%s)",
            config.name,
            config.model,
            config.perspective,
        )

    def unregister_explorer(self, name: str) -> None:
        """Remove an explorer from the registry."""
        if name in self._explorers:
            config = self._explorers.pop(name)
            # Remove from dispatcher
            provider_name = config.to_provider_profile().name
            if provider_name in self._dispatcher_registered:
                self.dispatcher.unregister(self._explorer_key(config))
                self._dispatcher_registered.discard(provider_name)
            logger.info("Unregistered explorer '%s'", name)

    def get_explorer(self, name: str) -> Optional[ExplorerConfig]:
        """Get an explorer's config by name, or None."""
        return self._explorers.get(name)

    @property
    def registered_explorers(self) -> List[str]:
        """List of all registered explorer keys."""
        return list(self._explorers.keys())

    # ────────────────────────────────────────────────────────────────
    # Dispatcher Registration Helpers
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _explorer_key(config: ExplorerConfig) -> str:
        """Build the industry key used to dispatch to an explorer."""
        return f"explorer:{config.name}"

    def _ensure_dispatcher_registered(self, config: ExplorerConfig) -> str:
        """Register the explorer's :class:`ProviderProfile` with the dispatcher.

        Returns the industry key to use for dispatch calls.
        """
        key = self._explorer_key(config)
        profile = config.to_provider_profile()

        if profile.name not in self._dispatcher_registered:
            self.dispatcher.register(key, profile)
            self._dispatcher_registered.add(profile.name)

        return key

    def _register_synthesizer(self) -> str:
        """Register the synthesizer provider with the dispatcher.

        Returns the industry key for synthesis dispatch calls.
        """
        key = f"explorer:{self.SYNTHESIZER_NAME}"
        profile = ProviderProfile(
            name=self.SYNTHESIZER_NAME,
            provider=self.SYNTHESIZER_PROVIDER,
            model=self.SYNTHESIZER_MODEL,
            api_key_env=f"{self.SYNTHESIZER_PROVIDER.upper()}_API_KEY",
            system_prompt=_SYNTHESIZER_SYSTEM_PROMPT,
            timeout=60.0,
        )

        if profile.name not in self._dispatcher_registered:
            self.dispatcher.register(key, profile)
            self._dispatcher_registered.add(profile.name)

        return key

    # ────────────────────────────────────────────────────────────────
    # Smart Explorer Selection
    # ────────────────────────────────────────────────────────────────

    def _select_explorers(
        self,
        industry: str,
        difficulty: str,
        strategy: ExploreStrategy,
        max_explorers: int = 5,
    ) -> List[ExplorerConfig]:
        """Select which explorers to use based on difficulty and industry.

        Selection logic:
            - **simple** → 1 general-purpose explorer
            - **medium** → industry-specific + general (2 explorers)
            - **complex** → industry + 1–2 related industries + general (3–4)

        Each selected explorer uses a **different model** to avoid herd bias.

        Args:
            industry: Target industry key (finance/medical/legal/education/general).
            difficulty: Query difficulty (simple/medium/complex).
            strategy: Exploration strategy (may affect selection).
            max_explorers: Upper bound on the number of explorers.

        Returns:
            Ordered list of :class:`ExplorerConfig` to dispatch.
        """
        difficulty = difficulty.lower()
        industry = industry.lower()
        if industry not in self._explorers:
            industry = "general"

        selected: List[ExplorerConfig] = []

        if difficulty == "simple":
            # 1 general explorer
            selected.append(self._explorers.get("general", _BUILTIN_EXPLORERS["general"]))

        elif difficulty == "medium":
            # Industry-specific + general (2 explorers)
            primary = self._explorers.get(industry, _BUILTIN_EXPLORERS.get(industry))
            if primary:
                selected.append(primary)
            selected.append(self._explorers.get("general", _BUILTIN_EXPLORERS["general"]))
            # Deduplicate (in case industry IS general)
            seen = set()
            deduped = []
            for e in selected:
                if e.name not in seen:
                    seen.add(e.name)
                    deduped.append(e)
            selected = deduped

        elif difficulty == "complex":
            # Industry + 1–2 related + general (3-4 explorers)
            primary = self._explorers.get(industry, _BUILTIN_EXPLORERS.get(industry))
            if primary:
                selected.append(primary)

            related = _RELATED_INDUSTRIES.get(industry, ["general"])
            added = 0
            for rel_industry in related:
                if added >= 2:
                    break
                if rel_industry == industry:
                    continue
                explorer = self._explorers.get(rel_industry)
                if explorer and explorer.name not in {e.name for e in selected}:
                    selected.append(explorer)
                    added += 1

            # Always add general if not already included
            general = self._explorers.get("general", _BUILTIN_EXPLORERS["general"])
            if general.name not in {e.name for e in selected}:
                selected.append(general)

        else:
            # Unknown difficulty → safe default: one general
            selected.append(self._explorers.get("general", _BUILTIN_EXPLORERS["general"]))

        # Clamp to max_explorers
        selected = selected[:max(max_explorers, 1)]

        # ── Ensure different models per explorer (anti-herd) ──
        selected = self._assign_diverse_models(selected)

        # For DIVERSE strategy, we might want to add more explorers
        if strategy == ExploreStrategy.DIVERSE and len(selected) < max_explorers:
            extra = [
                e
                for e in self._explorers.values()
                if e.name not in {s.name for s in selected}
            ][: max_explorers - len(selected)]
            selected.extend(extra)

        logger.info(
            "Selected %d explorers for industry=%s difficulty=%s strategy=%s: %s",
            len(selected),
            industry,
            difficulty,
            strategy.value,
            [e.name for e in selected],
        )

        return selected

    def _assign_diverse_models(
        self, explorers: List[ExplorerConfig]
    ) -> List[ExplorerConfig]:
        """Ensure no two explorers use the same model.

        Assigns different models from the CostOptimizer's tier list.
        Modifies the configs in-place (clones them to avoid mutating originals).
        """
        used_models: Set[str] = set()
        result: List[ExplorerConfig] = []

        # Available models across tiers (cheapest first, skip tier0 local)
        all_models: List[str] = []
        for tier_name in ["tier1_small", "tier2_medium", "tier3_large", "tier4_premium"]:
            tier = MODEL_TIERS.get(tier_name, {})
            all_models.extend(tier.get("models", []))

        model_idx = 0
        for explorer in explorers:
            model = explorer.model
            if model in used_models:
                # Find an unused model
                while model_idx < len(all_models):
                    candidate = all_models[model_idx]
                    model_idx += 1
                    if candidate not in used_models:
                        model = candidate
                        break
                else:
                    # All models exhausted — reuse the first one
                    model = all_models[0] if all_models else model

            used_models.add(model)

            # Create a clone with the assigned model (don't mutate the registry)
            result.append(
                ExplorerConfig(
                    name=explorer.name,
                    model=model,
                    system_prompt=explorer.system_prompt,
                    perspective=explorer.perspective,
                    provider=explorer.provider if model == explorer.model else "openai",
                    api_key_env=explorer.api_key_env,
                    base_url=explorer.base_url,
                    max_tokens=explorer.max_tokens,
                    temperature=explorer.temperature,
                )
            )

        return result

    # ────────────────────────────────────────────────────────────────
    # Core: Explore
    # ────────────────────────────────────────────────────────────────

    async def explore(
        self,
        query: str,
        industry: str,
        difficulty: str,
        strategy: ExploreStrategy = ExploreStrategy.PARALLEL,
        max_explorers: int = 5,
    ) -> List[ExplorationResult]:
        """Dispatch the query to multiple explorers and collect results.

        Args:
            query: The user's question or problem statement.
            industry: Target industry (finance/medical/legal/education/general).
            difficulty: Estimated difficulty (simple/medium/complex).
            strategy: How to dispatch explorers (PARALLEL/SEQUENTIAL/DIVERSE/…).
            max_explorers: Maximum number of explorers to dispatch.

        Returns:
            Ordered list of :class:`ExplorationResult`, one per explorer.
        """
        # ── 0. Trivial query check ──────────────────────────────────
        if self.cost.should_skip_exploration(query):
            logger.info("Skipping exploration — query classified as trivial")
            return []

        # ── 0.5. Interrupt check ────────────────────────────────────
        try:
            from panda.interrupt import get_interrupt_manager
            get_interrupt_manager().check(self._session_id) if self._session_id else None
        except ImportError:
            pass

        # ── 1. Anti-loop guard check ────────────────────────────────
        self._current_strategy = strategy.value
        detection: LoopDetection = self.guard.check(
            current_strategy=self._current_strategy
        )

        if detection.action == LoopAction.STRATEGY_SWITCH:
            new_strategy_str = detection.strategy_suggestion or "PARALLEL"
            try:
                strategy = ExploreStrategy(new_strategy_str.lower())
            except ValueError:
                strategy = ExploreStrategy.PARALLEL
            logger.warning(
                "AntiLoopGuard: switching strategy %s → %s (reason: %s)",
                self._current_strategy,
                strategy.value,
                detection.detail,
            )
            self._current_strategy = strategy.value

        if detection.action == LoopAction.ESCALATE:
            logger.warning(
                "AntiLoopGuard: escalation recommended (reason: %s)", detection.detail
            )
            # Upgrade difficulty to complex for better exploration
            if difficulty in ("simple", "medium"):
                difficulty = "complex"

        # ── 2. Select explorers ─────────────────────────────────────
        explorers = self._select_explorers(industry, difficulty, strategy, max_explorers)

        if not explorers:
            logger.warning("No explorers selected for industry=%s difficulty=%s", industry, difficulty)
            return []

        # ── 3. Ensure all selected explorers are registered ─────────
        for cfg in explorers:
            self._ensure_dispatcher_registered(cfg)

        # ── 4. Dispatch based on strategy ───────────────────────────
        if strategy == ExploreStrategy.SEQUENTIAL:
            results = await self._explore_sequential(query, explorers)
        elif strategy == ExploreStrategy.DEPTH_FIRST:
            results = await self._explore_depth_first(query, explorers)
        else:
            # PARALLEL, DIVERSE, BREADTH_FIRST all use parallel dispatch
            results = await self._explore_parallel(query, explorers)

        # ── 5. Record exploration in guard ──────────────────────────
        total_cost = sum(r.cost_usd for r in results)
        guard_result = self.guard.record_and_check(
            action_type=ActionType.MODEL_RESPONSE,
            action_name=f"ensemble_explore:{industry}",
            arguments={
                "query": query[:200],
                "industry": industry,
                "difficulty": difficulty,
                "strategy": strategy.value,
                "num_explorers": len(results),
                "total_cost_usd": total_cost,
            },
            result={"num_results": len(results), "explorers": [r.explorer_name for r in results]},
            success=len(results) > 0,
            meta={"query_len": len(query), "industry": industry},
            current_strategy=self._current_strategy,
        )

        trace_id, post_detection = guard_result
        if post_detection.action != LoopAction.CONTINUE:
            logger.warning(
                "Post-exploration loop detected: %s (action=%s)",
                post_detection.detail,
                post_detection.action.value,
            )

        # ── 6. Sort by confidence (descending) ──────────────────────
        results.sort(key=lambda r: r.confidence, reverse=True)

        logger.info(
            "Exploration complete: %d results from %d explorers, total_cost=$%.6f",
            len(results),
            len(explorers),
            total_cost,
        )

        return results

    # ────────────────────────────────────────────────────────────────
    # Parallel Exploration (PARALLEL / DIVERSE / BREADTH_FIRST)
    # ────────────────────────────────────────────────────────────────

    async def _explore_parallel(
        self,
        query: str,
        explorers: List[ExplorerConfig],
    ) -> List[ExplorationResult]:
        """Run all explorers concurrently via asyncio.gather()."""
        tasks = []
        for cfg in explorers:
            tasks.append(self._run_single_explorer(query, cfg))

        # Run all with individual timeouts, collect successes
        results: List[ExplorationResult] = []
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        for i, outcome in enumerate(raw):
            if isinstance(outcome, Exception):
                logger.error(
                    "Explorer '%s' failed: %s",
                    explorers[i].name if i < len(explorers) else f"#{i}",
                    outcome,
                )
                # Record failure in guard
                self.guard.record(
                    action_type=ActionType.ERROR,
                    action_name=f"explorer:{explorers[i].name if i < len(explorers) else f'#{i}'}",
                    arguments={"query": query[:200]},
                    success=False,
                    meta={"error": str(outcome)},
                )
            elif isinstance(outcome, ExplorationResult):
                results.append(outcome)

        return results

    # ────────────────────────────────────────────────────────────────
    # Sequential Exploration
    # ────────────────────────────────────────────────────────────────

    async def _explore_sequential(
        self,
        query: str,
        explorers: List[ExplorerConfig],
    ) -> List[ExplorationResult]:
        """Run explorers one at a time; each can see prior findings."""
        results: List[ExplorationResult] = []
        prior_findings: List[str] = []

        for i, cfg in enumerate(explorers):
            # Build a query that includes prior findings as context
            enhanced_query = query
            if prior_findings and i > 0:
                prior_summary = "\n".join(
                    f"- [{r.explorer_name}] {fnd}"
                    for r in results
                    for fnd in r.findings[:3]
                )
                enhanced_query = (
                    f"{query}\n\n"
                    f"## 前序专家的分析摘要\n{prior_summary}\n\n"
                    f"请基于以上前序分析，从{cfg.perspective}角度"
                    f"补充新观点或深入分析。"
                )

            result = await self._run_single_explorer(enhanced_query, cfg)
            if result:
                results.append(result)
                prior_findings.extend(result.findings)

            # Guard check between sequential steps
            detection = self.guard.check(current_strategy=self._current_strategy)
            if detection.action == LoopAction.STRATEGY_SWITCH:
                logger.warning(
                    "Guard triggered during sequential exploration: %s", detection.detail
                )
                break

        return results

    # ────────────────────────────────────────────────────────────────
    # Depth-First Exploration
    # ────────────────────────────────────────────────────────────────

    async def _explore_depth_first(
        self,
        query: str,
        explorers: List[ExplorerConfig],
    ) -> List[ExplorationResult]:
        """Primary explorer goes deep; others fill identified gaps."""
        if not explorers:
            return []

        # Phase 1: primary explorer goes deep
        primary = explorers[0]
        primary_result = await self._run_single_explorer(
            query + "\n\n请进行深度分析，尽可能覆盖问题的所有关键方面。",
            primary,
        )

        results: List[ExplorationResult] = []
        if primary_result:
            results.append(primary_result)

        # Phase 2: remaining explorers fill gaps
        for cfg in explorers[1:]:
            gap_query = (
                f"{query}\n\n"
                f"前序深度分析已由「{primary.name}」完成。"
                f"请从{cfg.perspective}角度，重点补充前序分析中"
                f"可能遗漏的关键点和新视角。"
            )
            result = await self._run_single_explorer(gap_query, cfg)
            if result:
                results.append(result)

        return results

    # ────────────────────────────────────────────────────────────────
    # Single Explorer Dispatch
    # ────────────────────────────────────────────────────────────────

    async def _run_single_explorer(
        self,
        query: str,
        config: ExplorerConfig,
    ) -> Optional[ExplorationResult]:
        """Dispatch a single query to one explorer with timeout protection.

        Args:
            query: The user query (may include prior context).
            config: Explorer configuration.

        Returns:
            :class:`ExplorationResult` on success, ``None`` on failure.
        """
        key = self._explorer_key(config)
        messages = [{"role": "user", "content": query}]

        try:
            dispatch_result: DispatchResult = await asyncio.wait_for(
                self.dispatcher.dispatch(
                    industry=key,
                    messages=messages,
                    knowledge=None,
                    stream=False,
                ),
                timeout=self.EXPLORER_TIMEOUT,
            )

            # ── Parse findings from the content ──
            findings = self._extract_findings(dispatch_result.content)
            confidence = self._estimate_confidence(dispatch_result.content)

            # ── Track cost ──
            cost_usd = self.cost.record_usage(
                model=dispatch_result.model,
                tokens_in=dispatch_result.usage.prompt_tokens,
                tokens_out=dispatch_result.usage.completion_tokens,
            )

            # ── Extract references ──
            references = self._extract_references(dispatch_result.content)

            # ── Record in guard ──
            self.guard.record(
                action_type=ActionType.TOOL_CALL,
                action_name=f"explorer:{config.name}",
                arguments={"query": query[:200], "model": config.model, "key": key},
                result={
                    "content_len": len(dispatch_result.content),
                    "num_findings": len(findings),
                    "confidence": confidence,
                },
                success=True,
                meta={
                    "latency_ms": dispatch_result.latency_ms,
                    "model": dispatch_result.model,
                    "industry_key": key,
                },
            )

            logger.debug(
                "Explorer '%s' completed in %.0fms (model=%s, findings=%d, cost=$%.6f)",
                config.name,
                dispatch_result.latency_ms,
                dispatch_result.model,
                len(findings),
                cost_usd,
            )

            return ExplorationResult(
                explorer_name=config.name,
                model_used=dispatch_result.model,
                findings=findings,
                approach=config.perspective,
                confidence=confidence,
                references=references,
                caveats=self._extract_caveats(dispatch_result.content),
                cost_usd=cost_usd,
                raw_content=dispatch_result.content,
                latency_ms=dispatch_result.latency_ms,
            )

        except asyncio.TimeoutError:
            logger.warning(
                "Explorer '%s' timed out after %.0fs", config.name, self.EXPLORER_TIMEOUT
            )
            self.guard.record(
                action_type=ActionType.ERROR,
                action_name=f"explorer_timeout:{config.name}",
                arguments={"query": query[:200], "timeout_s": self.EXPLORER_TIMEOUT},
                success=False,
            )
            return None

        except Exception as exc:
            logger.error("Explorer '%s' error: %s", config.name, exc)
            self.guard.record(
                action_type=ActionType.ERROR,
                action_name=f"explorer_error:{config.name}",
                arguments={"query": query[:200], "error_type": type(exc).__name__},
                result=str(exc)[:500],
                success=False,
            )
            return None

    # ────────────────────────────────────────────────────────────────
    # Synthesizer
    # ────────────────────────────────────────────────────────────────

    async def synthesize(
        self,
        query: str,
        results: List[ExplorationResult],
        industry: str,
    ) -> str:
        """Merge findings from multiple explorers into a single answer.

        Uses a premium model (deepseek-reasoner) to synthesize diverse
        perspectives, highlight consensus and disagreement, and produce
        a cohesive final answer.

        Args:
            query: The original user question.
            results: List of :class:`ExplorationResult` from explore().
            industry: Industry context for the synthesis.

        Returns:
            Synthesized answer string with consensus/dissent annotations.
        """
        if not results:
            return "未获得任何探索结果，无法进行综合分析。"

        # ── Build synthesis prompt ──────────────────────────────────
        findings_text_parts: List[str] = []
        for i, r in enumerate(results, 1):
            findings_text_parts.append(
                f"### 专家{i}：{r.explorer_name}（模型：{r.model_used}，置信度：{r.confidence:.2f}）\n"
                f"分析角度：{r.approach}\n"
                f"关键发现：\n" + "\n".join(f"- {f}" for f in r.findings) + "\n"
                + (f"注意事项：{r.caveats}\n" if r.caveats else "")
            )

        n = len(results)
        all_findings = "\n\n".join(findings_text_parts)

        synthesis_user_message = (
            f"**原始问题**：{query}\n\n"
            f"以下是{N}位专家从不同角度的分析结果。"
            f"请融合这些观点，给出最佳方案。标注共识点和分歧点。\n\n"
            f"{all_findings}"
        ).replace("{N}", str(n))

        messages = [{"role": "user", "content": synthesis_user_message}]

        # ── Dispatch to synthesizer ─────────────────────────────────
        try:
            synth_key = f"explorer:{self.SYNTHESIZER_NAME}"
            dispatch_result = await asyncio.wait_for(
                self.dispatcher.dispatch(
                    industry=synth_key,
                    messages=messages,
                    knowledge=None,
                    stream=False,
                ),
                timeout=60.0,  # synthesis gets more time
            )

            # Track synthesis cost
            self.cost.record_usage(
                model=dispatch_result.model,
                tokens_in=dispatch_result.usage.prompt_tokens,
                tokens_out=dispatch_result.usage.completion_tokens,
            )

            # Guard record
            self.guard.record(
                action_type=ActionType.MODEL_RESPONSE,
                action_name="synthesizer",
                arguments={
                    "num_inputs": n,
                    "query": query[:200],
                    "industry": industry,
                },
                result={"content_len": len(dispatch_result.content)},
                success=True,
                meta={"model": dispatch_result.model},
            )

            logger.info(
                "Synthesizer completed: %d inputs → %.0f tokens output",
                n,
                dispatch_result.usage.completion_tokens,
            )

            return dispatch_result.content

        except asyncio.TimeoutError:
            logger.error("Synthesizer timed out after 60s")
            return self._fallback_synthesize(results)

        except Exception as exc:
            logger.error("Synthesizer failed: %s", exc)
            return self._fallback_synthesize(results)

    def _fallback_synthesize(self, results: List[ExplorationResult]) -> str:
        """Simple fallback synthesis when the LLM synthesizer fails."""
        parts: List[str] = [
            "## 综合摘要（后备模式）\n",
            f"以下综合了 {len(results)} 位专家的分析结果。\n",
        ]

        # Collect all findings
        all_findings: List[str] = []
        for r in results:
            all_findings.extend(r.findings)

        if all_findings:
            parts.append("### 主要发现\n")
            for f in all_findings[:10]:
                parts.append(f"- {f}\n")

        parts.append(f"\n### 参与专家\n")
        for r in results:
            parts.append(f"- **{r.explorer_name}**（{r.model_used}，置信度 {r.confidence:.2f}）\n")

        if results:
            best = results[0]
            parts.append(f"\n> 最高置信度专家「{best.explorer_name}」的原始分析：\n\n{best.raw_content[:2000]}\n")

        return "".join(parts)

    # ────────────────────────────────────────────────────────────────
    # Content Parsing Utilities
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_findings(content: str) -> List[str]:
        """Extract bullet-point findings from model output.

        Looks for lines starting with '-', '•', '1.', '*', or '## 关键发现' section.
        """
        findings: List[str] = []

        # Try to find a "关键发现" (Key Findings) section
        import re

        # Pattern: ## 关键发现 ... followed by bullet points
        key_finding_match = re.search(
            r"#{1,3}\s*关键发现\s*\n(.*?)(?=\n#{1,3}\s|\Z)",
            content,
            re.DOTALL,
        )
        if key_finding_match:
            section = key_finding_match.group(1)
            for line in section.strip().split("\n"):
                line = line.strip()
                if line and (line.startswith("- ") or line.startswith("• ") or line.startswith("* ")):
                    findings.append(line.lstrip("- •*").strip())
                elif re.match(r"^\d+[\.\)、]\s", line):
                    findings.append(re.sub(r"^\d+[\.\)、]\s*", "", line).strip())
            if findings:
                return findings

        # Fallback: scan entire content for bullet points
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("• ") or line.startswith("* "):
                text = line.lstrip("- •*").strip()
                if len(text) > 5:  # ignore very short bullets
                    findings.append(text)
            elif re.match(r"^\d+[\.\)、]\s", line):
                text = re.sub(r"^\d+[\.\)、]\s*", "", line).strip()
                if len(text) > 5:
                    findings.append(text)

        # If still no findings, use first paragraph as a finding
        if not findings and content.strip():
            first_para = content.strip().split("\n\n")[0]
            if len(first_para) > 10:
                findings.append(first_para[:300])

        return findings[:10]  # cap at 10 findings

    @staticmethod
    def _extract_references(content: str) -> List[str]:
        """Extract references/citations from model output."""
        import re

        refs: List[str] = []
        ref_match = re.search(
            r"#{1,3}\s*(?:参考|引用|来源|References?)\s*\n(.*?)(?=\n#{1,3}\s|\Z)",
            content,
            re.DOTALL | re.IGNORECASE,
        )
        if ref_match:
            for line in ref_match.group(1).strip().split("\n"):
                line = line.strip()
                if line and (line.startswith("- ") or line.startswith("* ") or re.match(r"^\d+", line)):
                    refs.append(line.lstrip("- *0123456789.、) ").strip())

        return refs[:10]

    @staticmethod
    def _extract_caveats(content: str) -> str:
        """Extract caveats/limitations/risks section from model output."""
        import re

        for heading in ["注意事项", "风险提示", "法律风险", "局限性", "Caveats", "Limitations"]:
            match = re.search(
                rf"#{1,3}\s*{heading}\s*\n(.*?)(?=\n#{1,3}\s|\Z)",
                content,
                re.DOTALL,
            )
            if match:
                return match.group(1).strip()[:500]

        return ""

    @staticmethod
    def _estimate_confidence(content: str) -> float:
        """Estimate confidence from the model output.

        Looks for explicit confidence statements or uses heuristics
        based on answer thoroughness.
        """
        import re

        # Look for explicit confidence patterns
        patterns = [
            r"置信度[：:]\s*([\d.]+)",
            r"confidence[：:]\s*([\d.]+)",
            r"确信度[：:]\s*([\d.]+)",
        ]
        for pat in patterns:
            match = re.search(pat, content, re.IGNORECASE)
            if match:
                try:
                    val = float(match.group(1))
                    if val > 1.0:
                        val = val / 100.0  # e.g. "85" → 0.85
                    return max(0.0, min(1.0, val))
                except ValueError:
                    pass

        # Heuristic based on content length and structure
        score = 0.5
        if len(content) > 500:
            score += 0.1
        if len(content) > 1000:
            score += 0.1
        if "## " in content:  # structured output
            score += 0.1
        if "但是" in content or "然而" in content or "局限性" in content:
            score += 0.05  # acknowledges uncertainty
        if re.search(r"(不确定|无法确定|可能有误|未验证)", content):
            score -= 0.15  # explicit uncertainty

        return max(0.1, min(0.95, score))

    # ────────────────────────────────────────────────────────────────
    # Full pipeline: explore + synthesize
    # ────────────────────────────────────────────────────────────────

    async def analyze(
        self,
        query: str,
        industry: str,
        difficulty: str,
        strategy: ExploreStrategy = ExploreStrategy.PARALLEL,
        max_explorers: int = 5,
    ) -> Dict[str, Any]:
        """Run the complete exploration → synthesis pipeline.

        This is the main entry point for most use cases. It runs the full
        ensemble pipeline and returns both the raw exploration results and
        the synthesized answer.

        Args:
            query: The user's question.
            industry: Target industry.
            difficulty: Query difficulty.
            strategy: Exploration strategy.
            max_explorers: Max explorers to dispatch.

        Returns:
            Dict with keys: ``answer``, ``results``, ``metadata``.
        """
        start_time = time.monotonic()

        # Phase 1: Explore
        results = await self.explore(
            query=query,
            industry=industry,
            difficulty=difficulty,
            strategy=strategy,
            max_explorers=max_explorers,
        )

        # Phase 2: Synthesize
        answer = await self.synthesize(query, results, industry)

        total_time_ms = (time.monotonic() - start_time) * 1000
        total_cost = sum(r.cost_usd for r in results)

        metadata = {
            "industry": industry,
            "difficulty": difficulty,
            "strategy": strategy.value,
            "num_explorers": len(results),
            "total_cost_usd": total_cost,
            "total_time_ms": total_time_ms,
            "explorer_names": [r.explorer_name for r in results],
            "models_used": list({r.model_used for r in results}),
            "avg_confidence": (
                sum(r.confidence for r in results) / len(results) if results else 0.0
            ),
        }

        logger.info(
            "analyze() complete: industry=%s, difficulty=%s, "
            "%d explorers, $%.6f, %.0fms",
            industry,
            difficulty,
            len(results),
            total_cost,
            total_time_ms,
        )

        return {
            "answer": answer,
            "results": results,
            "metadata": metadata,
        }

    # ────────────────────────────────────────────────────────────────
    # Reset
    # ────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset all session state (guard traces, strategy)."""
        self.guard.reset()
        self._current_strategy = None
        logger.info("ExplorerEnsemble reset complete")


# ════════════════════════════════════════════════════════════════════
# Exports
# ════════════════════════════════════════════════════════════════════

__all__ = [
    # Core class
    "ExplorerEnsemble",
    # Enums
    "ExploreStrategy",
    # Data classes
    "ExplorerConfig",
    "ExplorationResult",
    # Built-in explorers
    "_BUILTIN_EXPLORERS",
]
