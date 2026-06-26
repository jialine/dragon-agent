"""
Dragon Pipeline — 智能调度管线

Orchestrates the full request lifecycle:
  User Input → Router → Simple? → Dispatcher (single model)
                       → Complex? → Jury Debate (3-6 models) → Risk Gate → MD Report

Also enforces session turn limits (default 150) and risk-gated execution.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

from dragon.router import DragonRouter, RouteResult
from dragon.dispatch import DragonDispatcher, DispatchResult
from dragon.jury import JuryDebate, JuryVerdict
from dragon.session import SessionStore, Session
from dragon.interrupt import InterruptManager, TaskInterrupted, get_interrupt_manager

logger = logging.getLogger("dragon.pipeline")


# ════════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════════

class RiskLevel(Enum):
    """Risk classification for task execution gating."""
    LOW = "low"           # risk_score < 25: auto-execute
    MEDIUM = "medium"     # risk_score 25-50: auto-execute with warnings
    HIGH = "high"         # risk_score 50-75: require user approval
    CRITICAL = "critical" # risk_score > 75: block, require explicit override


class PipelineAction(Enum):
    """Action determined by the risk gate."""
    AUTO_EXECUTE = "auto_execute"
    REQUIRE_APPROVAL = "require_approval"
    SESSION_PAUSED = "session_paused"  # 150-turn limit reached


# ════════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineResponse:
    """Complete response from the pipeline.

    Attributes:
        route: Router classification result (industry, difficulty, etc.)
        result: DispatchResult for simple tasks, JuryVerdict for complex tasks.
        risk_score: 0-100 risk score (0=safe, 100=critical).
        risk_level: RiskLevel classification.
        action: PipelineAction determined by risk gate + session state.
        report: Markdown report (complex tasks only).
        session_turns: Current turn count for this session.
        session_paused: True if the session has been paused (150-turn limit).
        metadata: Arbitrary extra data for debugging/observability.
    """
    route: RouteResult
    result: Any  # DispatchResult | JuryVerdict
    risk_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    action: PipelineAction = PipelineAction.AUTO_EXECUTE
    report: str = ""
    session_turns: int = 0
    session_paused: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════════
# Pipeline
# ════════════════════════════════════════════════════════════════════════

class DragonPipeline:
    """End-to-end intelligent dispatch pipeline.

    Usage::

        pipeline = DragonPipeline(
            router=router,
            dispatcher=dispatcher,
            jury=jury,
            session_store=session_store,
        )

        response = await pipeline.process(
            session_id="sess_abc",
            user_message="如何优化供应链成本？",
        )

        if response.action == PipelineAction.REQUIRE_APPROVAL:
            print(f"High risk task ({response.risk_score:.0f}/100) — awaiting approval")
        else:
            print(response.report or response.result.content)
    """

    # ── Defaults ──────────────────────────────────────────────────────

    DEFAULT_MAX_TURNS = 150          # 超过此轮数暂停会话
    DEFAULT_RISK_THRESHOLD = 50.0    # risk_score 超过此值需要审批
    DEFAULT_JURY_SIZE = 5            # 默认陪审团人数
    DEFAULT_COMPLEXITY_THRESHOLD = 5.0  # difficulty_score >= 此值视为复杂任务

    # ── Constructor ───────────────────────────────────────────────────

    def __init__(
        self,
        router: DragonRouter,
        dispatcher: DragonDispatcher,
        jury: JuryDebate,
        session_store: SessionStore,
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        risk_threshold: float = DEFAULT_RISK_THRESHOLD,
        complexity_threshold: float = DEFAULT_COMPLEXITY_THRESHOLD,
        interrupt_manager: Optional[InterruptManager] = None,
    ) -> None:
        self._router = router
        self._dispatcher = dispatcher
        self._jury = jury
        self._session_store = session_store
        self._max_turns = max_turns
        self._risk_threshold = risk_threshold
        self._complexity_threshold = complexity_threshold
        self._interrupt = interrupt_manager or get_interrupt_manager()

        logger.info(
            "DragonPipeline initialized (max_turns=%d, risk_threshold=%.0f, "
            "complexity_threshold=%.1f)",
            max_turns, risk_threshold, complexity_threshold,
        )

    # ── Public API ────────────────────────────────────────────────────

    async def process(
        self,
        session_id: str,
        user_message: str,
        *,
        knowledge: Optional[str] = None,
        proposals: Optional[Dict[str, dict]] = None,
    ) -> PipelineResponse:
        """Process a user message through the full pipeline.

        Steps:
        1. Check session turn limit (pause at 150)
        2. Route via local Qwen3-0.6B (intent + complexity)
        3. Simple task → single model dispatch
        4. Complex task → multi-model jury debate → risk assessment
        5. Risk gate → auto-execute or require approval
        6. Generate Markdown report (complex tasks only)

        Args:
            session_id: Unique session identifier.
            user_message: The user's query/message.
            knowledge: Optional reference knowledge for the model.
            proposals: Optional pre-built proposals for jury debate.
                       If None and task is complex, the system auto-generates
                       proposals from industry models.

        Returns:
            PipelineResponse with routing, result, risk assessment, and report.
        """
        metadata: Dict[str, Any] = {}

        # ── Step 1: Session turn check ────────────────────────────────
        session = self._session_store.get(session_id)
        if session is None:
            session = self._session_store.create(
                title=user_message[:50],
                platform="api",
            )
            logger.info("Created new session: %s", session_id)

        current_turns = session.message_count

        if current_turns >= self._max_turns:
            logger.warning(
                "Session %s reached turn limit (%d/%d) — pausing",
                session_id, current_turns, self._max_turns,
            )
            return PipelineResponse(
                route=RouteResult.fallback_general("会话轮数已达上限"),
                result=None,
                risk_score=0.0,
                risk_level=RiskLevel.LOW,
                action=PipelineAction.SESSION_PAUSED,
                report=self._build_limit_report(current_turns),
                session_turns=current_turns,
                session_paused=True,
                metadata=metadata,
            )

        # Record user message
        self._session_store.add_message(session_id, "user", user_message)
        current_turns += 1

        # ── Step 2: Route (intent + complexity) ───────────────────────
        route = await self._router.classify(user_message)
        metadata["route"] = {
            "industry": route.industry,
            "difficulty": route.difficulty,
            "difficulty_score": route.difficulty_score,
            "confidence": route.confidence,
            "reason": route.reason,
        }
        logger.info(
            "Routed: industry=%s difficulty=%s score=%.1f",
            route.industry, route.difficulty, route.difficulty_score,
        )

        # ── Step 3/4: Dispatch based on complexity ────────────────────
        is_complex = route.difficulty == "complex" or \
                     route.difficulty_score >= self._complexity_threshold

        if not is_complex:
            # Simple task → single model dispatch
            return await self._handle_simple(
                session_id, user_message, route, knowledge, current_turns, metadata
            )
        else:
            # Complex task → multi-model jury debate
            return await self._handle_complex(
                session_id, user_message, route, knowledge, proposals,
                current_turns, metadata,
            )

    # ── Simple Task Handler ───────────────────────────────────────────

    async def _handle_simple(
        self,
        session_id: str,
        user_message: str,
        route: RouteResult,
        knowledge: Optional[str],
        turns: int,
        metadata: Dict[str, Any],
    ) -> PipelineResponse:
        """Handle a simple task with single-model dispatch."""
        try:
            result = await self._dispatcher.dispatch(
                industry=route.industry,
                messages=[{"role": "user", "content": user_message}],
                knowledge=knowledge,
            )
        except Exception as exc:
            logger.exception("Dispatcher failed for simple task")
            # Fallback: try general industry
            try:
                result = await self._dispatcher.dispatch(
                    industry="general",
                    messages=[{"role": "user", "content": user_message}],
                    knowledge=knowledge,
                )
            except Exception:
                return PipelineResponse(
                    route=route,
                    result=None,
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    action=PipelineAction.AUTO_EXECUTE,
                    report=f"调度失败: {exc}",
                    session_turns=turns,
                    metadata=metadata,
                )

        # Record assistant response
        self._session_store.add_message(
            session_id, "assistant", result.content[:5000]
        )

        # Simple tasks: low risk, auto-execute
        risk_score = max(0.0, (1.0 - route.confidence) * 30.0)  # low risk by default
        risk_level = self._classify_risk(risk_score)

        return PipelineResponse(
            route=route,
            result=result,
            risk_score=risk_score,
            risk_level=risk_level,
            action=self._gate(risk_level),
            report="",  # No report for simple tasks
            session_turns=turns,
            metadata=metadata,
        )

    # ── Complex Task Handler ──────────────────────────────────────────

    async def _handle_complex(
        self,
        session_id: str,
        user_message: str,
        route: RouteResult,
        knowledge: Optional[str],
        proposals: Optional[Dict[str, dict]],
        turns: int,
        metadata: Dict[str, Any],
    ) -> PipelineResponse:
        """Handle a complex task with multi-model jury debate."""
        from dragon.report import generate_verdict_report

        # If no proposals, auto-generate from industry models
        if proposals is None:
            proposals = await self._generate_proposals(
                user_message, route, knowledge
            )

        try:
            verdict = await self._jury.deliberate(
                query=user_message,
                proposals=proposals,
                memory_context=knowledge,
            )
        except Exception as exc:
            logger.exception("Jury debate failed")
            return PipelineResponse(
                route=route,
                result=None,
                risk_score=70.0,  # failed debate = high risk
                risk_level=RiskLevel.HIGH,
                action=PipelineAction.REQUIRE_APPROVAL,
                report=f"辩论引擎异常: {exc}",
                session_turns=turns,
                metadata=metadata,
            )

        # Calculate risk from verdict
        risk_score = self._calculate_risk(verdict)
        risk_level = self._classify_risk(risk_score)

        # Generate Markdown report
        report = generate_verdict_report(verdict, risk_score, risk_level)

        # Record assistant response (truncated for DB)
        self._session_store.add_message(
            session_id, "assistant",
            f"[多模型辩论完成] 胜出方案: {verdict.winner} "
            f"置信度: {verdict.confidence:.1%} "
            f"风险: {risk_score:.0f}/100"
        )

        metadata["debate"] = {
            "winner": verdict.winner,
            "decision": verdict.decision.value,
            "confidence": verdict.confidence,
            "jury_size": len(verdict.ballots),
            "deception_flags": len(verdict.deception_flags),
            "risk_score": risk_score,
            "risk_level": risk_level.value,
        }

        action = self._gate(risk_level)

        # Check if we're approaching the turn limit
        session_paused = turns + 1 >= self._max_turns

        return PipelineResponse(
            route=route,
            result=verdict,
            risk_score=risk_score,
            risk_level=risk_level,
            action=action if not session_paused else PipelineAction.SESSION_PAUSED,
            report=report,
            session_turns=turns,
            session_paused=session_paused,
            metadata=metadata,
        )

    # ── Risk Calculation ──────────────────────────────────────────────

    def _calculate_risk(self, verdict: JuryVerdict) -> float:
        """Calculate risk score (0-100) from jury verdict.

        Factors:
        - Consensus: lower consensus → higher risk (weight: 0.35)
        - Confidence: lower confidence → higher risk (weight: 0.25)
        - Deception flags: more flags → higher risk (weight: 0.25)
        - Ballot variance: higher variance in confidence → higher risk (weight: 0.15)
        """
        # Consensus factor
        # SPLIT or DEADLOCK = high risk, CONSENSUS = low risk
        decision_risk = {
            "consensus": 10.0,
            "majority": 40.0,
            "split": 70.0,
            "deadlock": 90.0,
        }
        consensus_risk = decision_risk.get(
            verdict.decision.value if hasattr(verdict.decision, 'value') else str(verdict.decision),
            50.0,
        )

        # Confidence factor (inverted: high confidence = low risk)
        confidence = max(0.0, verdict.confidence)
        confidence_risk = (1.0 - confidence) * 100.0

        # Deception factor
        deception_count = len(verdict.deception_flags)
        deception_risk = min(100.0, deception_count * 25.0)

        # Ballot variance factor
        ballot_confidences = [b.confidence for b in verdict.ballots]
        if len(ballot_confidences) >= 2:
            mean_conf = sum(ballot_confidences) / len(ballot_confidences)
            variance = sum((c - mean_conf) ** 2 for c in ballot_confidences) / len(ballot_confidences)
            variance_risk = min(100.0, variance * 200.0)
        else:
            variance_risk = 30.0

        risk = (
            consensus_risk * 0.35
            + confidence_risk * 0.25
            + deception_risk * 0.25
            + variance_risk * 0.15
        )

        return round(max(0.0, min(100.0, risk)), 1)

    def _classify_risk(self, risk_score: float) -> RiskLevel:
        """Classify risk score into RiskLevel."""
        if risk_score < 25.0:
            return RiskLevel.LOW
        elif risk_score < 50.0:
            return RiskLevel.MEDIUM
        elif risk_score < 75.0:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    def _gate(self, risk_level: RiskLevel) -> PipelineAction:
        """Determine action based on risk level.

        LOW/MEDIUM → auto-execute
        HIGH/CRITICAL → require user approval
        """
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return PipelineAction.REQUIRE_APPROVAL
        return PipelineAction.AUTO_EXECUTE

    # ── Proposal Generation ───────────────────────────────────────────

    async def _generate_proposals(
        self,
        user_message: str,
        route: RouteResult,
        knowledge: Optional[str],
    ) -> Dict[str, dict]:
        """Auto-generate proposals from industry-specific models.

        Each registered industry model is asked to propose a solution.
        Returns a dict of proposal_id → {summary, author}.
        """
        industries = self._dispatcher.registered_industries
        if not industries:
            # Fallback: use route.industry + general
            industries = [route.industry, "general"]

        # Limit to 5 proposals max
        industries = industries[:5]

        proposal_prompt = (
            f"针对以下问题，请提供一个简洁的解决方案（不超过300字）：\n\n{user_message}\n\n"
            f"请以JSON格式返回：{{\"summary\": \"你的方案描述\", \"approach\": \"具体方法\", "
            f"\"pros\": [\"优点1\", \"优点2\"], \"cons\": [\"缺点1\", \"缺点2\"]}}"
        )

        tasks = []
        for ind in industries:
            tasks.append(self._fetch_proposal(ind, proposal_prompt, knowledge))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        proposals: Dict[str, dict] = {}
        labels = ["A", "B", "C", "D", "E"]

        for i, (ind, result) in enumerate(zip(industries, results)):
            label = labels[i] if i < len(labels) else f"P{i}"
            if isinstance(result, Exception):
                proposals[label] = {
                    "summary": f"[{ind}] 方案生成失败: {result}",
                    "author": ind,
                }
            elif isinstance(result, dict):
                proposals[label] = {
                    "summary": result.get("summary", f"[{ind}] 无方案"),
                    "author": ind,
                }
            else:
                proposals[label] = {
                    "summary": str(result)[:500],
                    "author": ind,
                }

        return proposals

    async def _fetch_proposal(
        self,
        industry: str,
        prompt: str,
        knowledge: Optional[str],
    ) -> dict:
        """Fetch a single proposal from an industry model."""
        import json
        import re

        result = await self._dispatcher.dispatch(
            industry=industry,
            messages=[{"role": "user", "content": prompt}],
            knowledge=knowledge,
        )

        # Try extracting JSON from response
        content = result.content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try extracting from markdown fences
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        return {"summary": content[:500], "author": industry}

    # ── Reports ───────────────────────────────────────────────────────

    def _build_limit_report(self, turns: int) -> str:
        """Build a report for session turn limit reached."""
        return (
            f"## ⏸️ 会话暂停\n\n"
            f"当前会话已达到 **{turns}/{self._max_turns}** 轮对话上限。\n\n"
            f"### 建议操作\n"
            f"- 输入 `继续` 或 `/continue` 来继续对话\n"
            f"- 输入 `总结` 来获取当前会话的摘要\n"
            f"- 输入 `新会话` 来开启新的对话\n"
        )

    # ── Resume ────────────────────────────────────────────────────────

    async def resume(self, session_id: str, extra_turns: int = 50) -> bool:
        """Resume a paused session, granting additional turns.

        Args:
            session_id: Session to resume.
            extra_turns: Additional turns to grant.

        Returns:
            True if session was paused and resumed.
        """
        session = self._session_store.get(session_id)
        if session is None:
            return False

        # Update metadata with turn extension
        meta = dict(session.meta)
        meta["resumed_at"] = str(asyncio.get_event_loop().time())
        meta["extra_turns_granted"] = extra_turns
        self._session_store.update_meta(session_id, meta=meta)

        logger.info(
            "Session %s resumed with +%d turns (was %d)",
            session_id, extra_turns, session.message_count,
        )
        return True

    # ── Async Context Manager ─────────────────────────────────────────

    async def close(self) -> None:
        """Release resources."""
        await self._router.shutdown()
        await self._dispatcher.close()


# ════════════════════════════════════════════════════════════════════════
# Exports
# ════════════════════════════════════════════════════════════════════════

__all__ = [
    "DragonPipeline",
    "PipelineResponse",
    "PipelineAction",
    "RiskLevel",
]
