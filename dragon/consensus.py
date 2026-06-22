"""
Dragon Agent — Consensus Builder & Source Attribution

Builds human-readable consensus output from jury verdicts with:
  - Semantic clustering of model positions
  - Agreement level classification
  - Source attribution for every claim
  - Confidence-calibrated output formatting
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.consensus")


# ════════════════════════════════════════════════════════════════════
# Source Attribution
# ════════════════════════════════════════════════════════════════════


class SourceType(Enum):
    KNOWLEDGE_BASE = "knowledge_base"
    WEB = "web"
    MODEL_INFERENCE = "model_inference"
    COMPUTED = "computed"
    LOGICAL = "logical"


@dataclass
class SourceAttribution:
    """A verified source for a claim."""

    claim: str
    source_type: SourceType
    source_detail: str  # URL, doc_id, or "model:name"
    retrieval_score: float = 0.0
    access_time: float = field(default_factory=time.time)

    def format_badge(self) -> str:
        """Format as a human-readable badge."""
        badges = {
            SourceType.KNOWLEDGE_BASE: "📚 知识库",
            SourceType.WEB: "🌐 网页",
            SourceType.MODEL_INFERENCE: "🧠 模型推理",
            SourceType.COMPUTED: "🔢 计算验证",
            SourceType.LOGICAL: "🔍 逻辑检查",
        }
        badge = badges.get(self.source_type, "❓ 未知")
        score_str = f" {self.retrieval_score:.0%}" if self.retrieval_score else ""
        return f"[{badge}{score_str}]"

    def format_citation(self) -> str:
        """Format as a citation line."""
        detail = self.source_detail
        if self.source_type == SourceType.WEB and detail.startswith("http"):
            detail = f"[链接]({detail})"
        elif self.source_type == SourceType.MODEL_INFERENCE:
            detail = f"模型: {detail}"
        return f"> {self.format_badge()} {detail}"


# ════════════════════════════════════════════════════════════════════
# Consensus
# ════════════════════════════════════════════════════════════════════


class AgreementLevel(Enum):
    HIGH = "high"       # >= 80% agreement
    MODERATE = "moderate"  # 60-80%
    LOW = "low"         # 40-60%
    NONE = "none"       # < 40%


@dataclass
class DisputedClaim:
    """A claim where models disagree."""

    claim: str
    positions: Dict[str, str]  # model_name → position
    verification: Optional[Dict] = None  # FactCheck result if available


@dataclass
class ConsensusResult:
    """Final consensus output from the jury pipeline."""

    # Core answer
    answer: str  # Markdown-formatted final answer
    short_answer: str = ""  # One-sentence summary

    # Metrics
    agreement_level: AgreementLevel = AgreementLevel.MODERATE
    confidence: float = 0.0
    model_count: int = 0
    agreeing_models: int = 0

    # Details
    agreed_claims: List[str] = field(default_factory=list)
    disputed_claims: List[DisputedClaim] = field(default_factory=list)
    model_positions: Dict[str, str] = field(default_factory=dict)  # model → summary
    sources: List[SourceAttribution] = field(default_factory=list)
    minority_report: str = ""

    # Metadata
    latency_ms: float = 0.0
    pipeline_steps: List[str] = field(default_factory=list)

    @property
    def agreement_pct(self) -> float:
        if self.model_count == 0:
            return 0.0
        return self.agreeing_models / self.model_count


class ConsensusBuilder:
    """Builds consensus from jury verdict + fact check results."""

    CONFIDENCE_EMOJI = {
        (0.85, 1.01): "🟢",
        (0.60, 0.85): "🟡",
        (0.30, 0.60): "🟠",
        (0.00, 0.30): "🔴",
    }

    def __init__(
        self,
        fact_checker=None,  # FactChecker instance (optional)
        source_tracker: Optional[SourceTracker] = None,
    ) -> None:
        self.fact_checker = fact_checker
        self.source_tracker = source_tracker or SourceTracker()

    def _confidence_emoji(self, confidence: float) -> str:
        for (lo, hi), emoji in self.CONFIDENCE_EMOJI.items():
            if lo <= confidence < hi:
                return emoji
        return "🔴"

    async def build(
        self,
        verdict,  # JuryVerdict
        question: str,
        fact_check_report=None,  # Optional FactCheckReport
    ) -> ConsensusResult:
        """Build consensus from a jury verdict."""
        start = time.monotonic()
        steps: List[str] = []

        # 1. Parse model positions from verdict
        model_positions = self._extract_positions(verdict)
        model_count = len(model_positions)
        steps.append(f"Extracted {model_count} model positions")

        # 2. Determine agreement level
        agreement, agreeing_count = self._calc_agreement(verdict, model_count)
        steps.append(f"Agreement: {agreement.value} ({agreeing_count}/{model_count})")

        # 3. Build answer based on agreement level
        answer, short_answer, confidence = self._build_answer(
            verdict, agreement, agreeing_count, model_count, question
        )

        # 4. Extract claims
        agreed_claims: List[str] = []
        disputed: List[DisputedClaim] = []

        if hasattr(verdict, "ballots"):
            agreed_claims, disputed = self._extract_claim_consensus(verdict)

        # 5. Source attribution
        sources = self.source_tracker.attribute_from_verdict(
            verdict, fact_check_report
        )

        elapsed = (time.monotonic() - start) * 1000

        return ConsensusResult(
            answer=answer,
            short_answer=short_answer,
            agreement_level=agreement,
            confidence=confidence,
            model_count=model_count,
            agreeing_models=agreeing_count,
            agreed_claims=agreed_claims,
            disputed_claims=disputed,
            model_positions=model_positions,
            sources=sources,
            minority_report=getattr(verdict, "minority_report", ""),
            latency_ms=elapsed,
            pipeline_steps=steps,
        )

    @staticmethod
    def _extract_positions(verdict) -> Dict[str, str]:
        """Extract each model's position from the verdict."""
        positions: Dict[str, str] = {}
        if hasattr(verdict, "ballots"):
            for ballot in verdict.ballots:
                pos = getattr(ballot, "key_reason", "")
                if not pos and hasattr(ballot, "voted_for"):
                    pos = f"Voted for: {ballot.voted_for}"
                positions[ballot.voter] = pos
        return positions

    @staticmethod
    def _calc_agreement(verdict, model_count: int) -> Tuple[AgreementLevel, int]:
        """Calculate agreement level from ballots."""
        if not hasattr(verdict, "ballots") or not verdict.ballots:
            return AgreementLevel.NONE, 0

        # Count votes for each proposal
        votes: Dict[str, int] = {}
        for b in verdict.ballots:
            v = getattr(b, "voted_for", "unknown")
            votes[v] = votes.get(v, 0) + 1

        if not votes:
            return AgreementLevel.NONE, 0

        max_votes = max(votes.values())
        pct = max_votes / model_count

        if pct >= 0.8:
            return AgreementLevel.HIGH, max_votes
        elif pct >= 0.6:
            return AgreementLevel.MODERATE, max_votes
        elif pct >= 0.4:
            return AgreementLevel.LOW, max_votes
        else:
            return AgreementLevel.NONE, max_votes

    def _build_answer(
        self,
        verdict,
        agreement: AgreementLevel,
        agreeing: int,
        total: int,
        question: str,
    ) -> Tuple[str, str, float]:
        """Build the final answer text based on agreement level."""
        rec = getattr(verdict, "recommendation", "无法生成回答。")
        conf = getattr(verdict, "confidence", 0.5)

        if agreement == AgreementLevel.HIGH:
            emoji = self._confidence_emoji(conf)
            answer = f"## 回答\n\n{rec}\n\n---\n"
            answer += f"### 置信度\n{emoji} 高 ({conf:.0%}) — {agreeing}/{total} 模型一致"
            short = rec[:100] if rec else "模型高度一致。"
            return answer, short, conf

        elif agreement == AgreementLevel.MODERATE:
            emoji = self._confidence_emoji(conf)
            minority = getattr(verdict, "minority_report", "")
            answer = f"## 回答\n\n{rec}\n\n---\n"
            answer += f"### 置信度\n{emoji} 中等 ({conf:.0%}) — {agreeing}/{total} 模型同意"
            if minority:
                answer += f"\n\n### 少数派报告\n{minority}"
            short = rec[:100] if rec else "多数模型同意，但有分歧。"
            return answer, short, conf * 0.8

        elif agreement == AgreementLevel.LOW:
            # Low agreement → show all positions
            answer = f"## ⚠️ 模型存在较大分歧\n\n"
            answer += f"**{agreeing}/{total}** 模型达成部分一致，但以下点存在分歧：\n\n"
            answer += f"### 各方观点\n\n"
            if hasattr(verdict, "ballots"):
                for b in verdict.ballots:
                    answer += f"- **{b.voter}**: {getattr(b, 'key_reason', '未提供理由')}\n"
            answer += f"\n---\n"
            answer += f"### 建议\n我无法给出确定答案，建议您：\n"
            answer += f"1. 查阅权威来源核实\n"
            answer += f"2. 提供更多上下文信息\n"
            answer += f"3. 咨询相关领域专家\n"
            return answer, "模型存在较大分歧，无法给出确定答案。", 0.35

        else:  # NONE
            answer = f"## 🔴 模型无法达成共识\n\n"
            answer += f"针对问题「{question}」，{total} 个模型未能达成任何一致。\n\n"
            answer += f"### 各方立场\n\n"
            if hasattr(verdict, "ballots"):
                for b in verdict.ballots:
                    answer += f"- **{b.voter}**: {getattr(b, 'key_reason', '未提供理由')}\n"
            answer += f"\n---\n*建议提供更多信息后重新提问。*"
            return answer, "模型无法达成共识。", 0.15

    @staticmethod
    def _extract_claim_consensus(
        verdict,
    ) -> Tuple[List[str], List[DisputedClaim]]:
        """Extract agreed and disputed claims from ballots."""
        agreed: List[str] = []
        disputed: List[DisputedClaim] = []

        if not hasattr(verdict, "ballots"):
            return agreed, disputed

        # Collect key reasons from all ballots
        reasons: Dict[str, List[str]] = {}
        for b in verdict.ballots:
            reason = getattr(b, "key_reason", "")
            if reason:
                reasons.setdefault(b.voter, []).append(reason)

        # Simple heuristic: if a reason appears in >50% of ballots, it's agreed
        all_reasons = []
        for r_list in reasons.values():
            all_reasons.extend(r_list)

        if not all_reasons:
            return agreed, disputed

        from collections import Counter

        reason_counts = Counter(all_reasons)
        threshold = len(verdict.ballots) * 0.5

        for reason, count in reason_counts.items():
            if count >= threshold and len(reason) > 20:
                agreed.append(reason)

        return agreed, disputed


# ════════════════════════════════════════════════════════════════════
# Source Tracker
# ════════════════════════════════════════════════════════════════════


class SourceTracker:
    """Tracks and formats source attributions for claims."""

    def __init__(self) -> None:
        self._attributions: List[SourceAttribution] = []

    def attribute_from_verdict(
        self,
        verdict,
        fact_check_report=None,
    ) -> List[SourceAttribution]:
        """Extract source attributions from verdict and fact check results."""
        attributions: List[SourceAttribution] = []

        # From fact check results
        if fact_check_report and hasattr(fact_check_report, "results"):
            for result in fact_check_report.results:
                for ev in result.evidence_for:
                    source_type = self._map_source_type(ev.source)
                    attributions.append(
                        SourceAttribution(
                            claim=result.claim.text[:200],
                            source_type=source_type,
                            source_detail=ev.source_detail,
                            retrieval_score=ev.relevance,
                        )
                    )

        # From model inference (each model as a source)
        if hasattr(verdict, "ballots"):
            for b in verdict.ballots:
                attributions.append(
                    SourceAttribution(
                        claim=f"模型 {b.voter} 的推理结果",
                        source_type=SourceType.MODEL_INFERENCE,
                        source_detail=b.voter,
                        retrieval_score=getattr(b, "confidence", 0.5),
                    )
                )

        self._attributions = attributions
        return attributions

    @staticmethod
    def _map_source_type(source_str: str) -> SourceType:
        mapping = {
            "knowledge_base": SourceType.KNOWLEDGE_BASE,
            "web": SourceType.WEB,
            "computed": SourceType.COMPUTED,
            "logical": SourceType.LOGICAL,
            "model_inference": SourceType.MODEL_INFERENCE,
        }
        return mapping.get(source_str, SourceType.MODEL_INFERENCE)

    def format_all(self) -> str:
        """Format all attributions as a Markdown section."""
        if not self._attributions:
            return ""

        lines = ["### 📋 来源追溯\n"]
        for attr in self._attributions[:10]:  # Limit to 10
            lines.append(attr.format_citation())
        return "\n".join(lines)
