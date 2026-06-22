"""
Dragon Agent — Fact Checker (事实核查引擎)

Verifies factual claims from jury model outputs against:
  1. Local knowledge base (ChromaDB)
  2. Web search (DuckDuckGo)
  3. Numerical computation (Python eval)
  4. Logical consistency checks

Integrates with: dragon.memory (ChromaDB) + dragon.web_search
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.factcheck")


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════


class ClaimType(Enum):
    FACTUAL = "factual"       # Verifiable factual claim
    NUMERICAL = "numerical"   # Mathematical / quantitative
    LOGICAL = "logical"       # Logical reasoning chain
    SUBJECTIVE = "subjective" # Opinion / cannot be verified


class VerificationStatus(Enum):
    VERIFIED = "verified"           # Confirmed by evidence
    LIKELY_TRUE = "likely_true"     # Supported but not definitive
    UNCERTAIN = "uncertain"         # Cannot determine (insufficient info)
    LIKELY_FALSE = "likely_false"   # Evidence suggests false
    CONTRADICTED = "contradicted"   # Explicitly contradicted
    UNVERIFIABLE = "unverifiable"   # Subjective / no way to check


@dataclass
class FactClaim:
    """A single factual claim extracted from model output."""

    text: str
    claim_type: ClaimType
    source_model: str = ""
    model_confidence: float = 1.0  # Model's self-assessed confidence

    def __post_init__(self) -> None:
        self.model_confidence = max(0.0, min(1.0, self.model_confidence))


@dataclass
class Evidence:
    """A piece of evidence supporting or contradicting a claim."""

    text: str
    source: str  # "knowledge_base", "web", "computed", "logical"
    source_detail: str = ""  # URL, doc_id, or computation result
    relevance: float = 0.0
    supports: bool = True  # True = supports claim, False = contradicts


@dataclass
class VerificationResult:
    """Result of verifying a single claim."""

    claim: FactClaim
    status: VerificationStatus
    confidence: float  # 0-1, how confident we are in this verification
    evidence_for: List[Evidence] = field(default_factory=list)
    evidence_against: List[Evidence] = field(default_factory=list)
    explanation: str = ""
    latency_ms: float = 0.0


@dataclass
class FactCheckReport:
    """Complete fact-check report for a set of claims."""

    claims: List[FactClaim] = field(default_factory=list)
    results: List[VerificationResult] = field(default_factory=list)
    verified_count: int = 0
    total_claims: int = 0
    overall_confidence: float = 0.0
    latency_ms: float = 0.0

    @property
    def verification_rate(self) -> float:
        """Fraction of claims that were verified or likely true."""
        if not self.total_claims:
            return 1.0
        verified = sum(
            1
            for r in self.results
            if r.status in (VerificationStatus.VERIFIED, VerificationStatus.LIKELY_TRUE)
        )
        return verified / self.total_claims


# ════════════════════════════════════════════════════════════════════
# Claim Extractor
# ════════════════════════════════════════════════════════════════════


class ClaimExtractor:
    """Extracts individual factual claims from model output text."""

    # Patterns for claim detection
    SENTENCE_PATTERN = re.compile(r"[^。！？\n]+[。！？]")

    # Indicators of factual claims (Chinese)
    FACTUAL_INDICATORS = [
        "根据", "数据显示", "研究表明", "历史", "成立于",
        "位于", "由.*组成", "公式", "定理", "定律",
        "在.*年", ".*世纪", "占比", "总计", "共计",
    ]

    # Indicators of subjective claims
    SUBJECTIVE_INDICATORS = [
        "我认为", "建议", "推荐", "最好", "应该",
        "可能", "或许", "大概", "一般说来",
    ]

    # Numerical patterns
    NUMERICAL_PATTERN = re.compile(
        r"(\d+(?:\.\d+)?)\s*(%|％|倍|万亿|亿|万|千|百|十|元|美元|吨|公里|米|秒|小时|天|年|月|日|°C|℃|个|人|次)"
    )

    @classmethod
    def extract(cls, text: str, source_model: str = "") -> List[FactClaim]:
        """Extract claims from text."""
        claims: List[FactClaim] = []

        # Split into sentences
        sentences = cls.SENTENCE_PATTERN.findall(text)
        if not sentences:
            # Fallback: split by newline or comma for unstructured text
            sentences = re.split(r"[。！？\n,，]", text)

        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 10:  # Skip very short fragments
                continue

            claim_type = cls._classify(sent)
            claims.append(
                FactClaim(
                    text=sent,
                    claim_type=claim_type,
                    source_model=source_model,
                )
            )

        return claims

    @classmethod
    def _classify(cls, text: str) -> ClaimType:
        """Classify a sentence into claim type."""
        text_lower = text.lower()

        # Subjective first (opinions)
        for indicator in cls.SUBJECTIVE_INDICATORS:
            if re.search(indicator, text):
                return ClaimType.SUBJECTIVE

        # Numerical
        if cls.NUMERICAL_PATTERN.search(text):
            return ClaimType.NUMERICAL

        # Logical indicators
        if any(
            word in text_lower
            for word in ["因此", "所以", "因为", "证明", "推导", "如果.*那么"]
        ):
            return ClaimType.LOGICAL

        # Default: factual
        return ClaimType.FACTUAL


# ════════════════════════════════════════════════════════════════════
# Fact Checker
# ════════════════════════════════════════════════════════════════════


class FactChecker:
    """Multi-source fact verification engine."""

    def __init__(
        self,
        memory=None,  # DragonMemory instance (optional)
        web_searcher=None,  # WebSearcher instance (optional)
        enable_web_search: bool = True,
        enable_computation: bool = True,
        kb_similarity_threshold: float = 0.65,
        web_similarity_threshold: float = 0.50,
    ) -> None:
        self.memory = memory
        self.web_searcher = web_searcher
        self.enable_web_search = enable_web_search
        self.enable_computation = enable_computation
        self.kb_threshold = kb_similarity_threshold
        self.web_threshold = web_similarity_threshold

    async def verify_claims(
        self, claims: List[FactClaim], question_context: str = ""
    ) -> FactCheckReport:
        """Verify a list of claims and return a comprehensive report."""
        start = time.monotonic()

        results: List[VerificationResult] = []
        tasks = [self._verify_single(claim, question_context) for claim in claims]
        results = await asyncio.gather(*tasks)

        elapsed = (time.monotonic() - start) * 1000

        verified = sum(
            1
            for r in results
            if r.status in (VerificationStatus.VERIFIED, VerificationStatus.LIKELY_TRUE)
        )

        overall_conf = (
            sum(r.confidence for r in results) / len(results) if results else 0.0
        )

        return FactCheckReport(
            claims=claims,
            results=results,
            verified_count=verified,
            total_claims=len(claims),
            overall_confidence=overall_conf,
            latency_ms=elapsed,
        )

    async def _verify_single(
        self, claim: FactClaim, context: str = ""
    ) -> VerificationResult:
        """Verify a single claim against all available sources."""
        start = time.monotonic()
        evidence_for: List[Evidence] = []
        evidence_against: List[Evidence] = []

        # Unverifiable types
        if claim.claim_type == ClaimType.SUBJECTIVE:
            return VerificationResult(
                claim=claim,
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                explanation="This is a subjective opinion — not verifiable as fact.",
                latency_ms=(time.monotonic() - start) * 1000,
            )

        # Step 1: Knowledge base lookup
        if self.memory is not None:
            kb_evidence = await self._check_knowledge_base(claim)
            for ev in kb_evidence:
                if ev.supports:
                    evidence_for.append(ev)
                else:
                    evidence_against.append(ev)

        # Step 2: Web search
        if (
            self.enable_web_search
            and self.web_searcher is not None
            and claim.claim_type == ClaimType.FACTUAL
        ):
            web_evidence = await self._check_web(claim)
            for ev in web_evidence:
                if ev.supports:
                    evidence_for.append(ev)
                else:
                    evidence_against.append(ev)

        # Step 3: Numerical verification
        if self.enable_computation and claim.claim_type == ClaimType.NUMERICAL:
            comp_evidence = self._check_numerical(claim)
            if comp_evidence:
                if comp_evidence.supports:
                    evidence_for.append(comp_evidence)
                else:
                    evidence_against.append(comp_evidence)

        # Step 4: Logical consistency
        if claim.claim_type == ClaimType.LOGICAL:
            logical_evidence = self._check_logical(claim)
            if logical_evidence:
                evidence_for.append(logical_evidence)

        # Determine status
        status, confidence, explanation = self._evaluate(
            claim, evidence_for, evidence_against
        )

        return VerificationResult(
            claim=claim,
            status=status,
            confidence=confidence,
            evidence_for=evidence_for,
            evidence_against=evidence_against,
            explanation=explanation,
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _check_knowledge_base(self, claim: FactClaim) -> List[Evidence]:
        """Search the knowledge base for supporting/contradicting evidence."""
        evidence: List[Evidence] = []
        try:
            results = self.memory.search(
                claim.text, collection="knowledge", top_k=3
            )
            for r in results:
                score = 1.0 - r.get("score", 1.0)  # Convert distance to similarity
                if score >= self.kb_threshold:
                    evidence.append(
                        Evidence(
                            text=r.get("doc", "")[:300],
                            source="knowledge_base",
                            source_detail=r.get("meta", {}).get("doc_id", ""),
                            relevance=score,
                            supports=True,
                        )
                    )
        except Exception as exc:
            logger.debug("Knowledge base lookup failed: %s", exc)

        return evidence

    async def _check_web(self, claim: FactClaim) -> List[Evidence]:
        """Search the web for supporting/contradicting evidence."""
        evidence: List[Evidence] = []
        try:
            # Lazy import to avoid circular dependency
            from dragon.web_search import WebSearcher

            if self.web_searcher is None:
                self.web_searcher = WebSearcher(max_results=3)

            results = await self.web_searcher.verify_claim(claim.text)
            for r in results:
                if r.relevance >= self.web_threshold:
                    evidence.append(
                        Evidence(
                            text=r.snippet[:300],
                            source="web",
                            source_detail=r.url,
                            relevance=r.relevance,
                            supports=r.relevance >= self.web_threshold + 0.2,
                        )
                    )
        except Exception as exc:
            logger.debug("Web search failed: %s", exc)

        return evidence

    @staticmethod
    def _check_numerical(claim: FactClaim) -> Optional[Evidence]:
        """Verify a numerical claim through computation."""
        # Try to extract and evaluate a mathematical expression
        num_pattern = re.compile(
            r"(\d+(?:\.\d+)?)\s*([+\-×÷*/])\s*(\d+(?:\.\d+)?)\s*=\s*(\d+(?:\.\d+)?)"
        )
        match = num_pattern.search(claim.text)
        if not match:
            return None

        a, op, b, expected = match.groups()
        a, b, expected = float(a), float(b), float(expected)

        try:
            if op in ("+", "＋"):
                result = a + b
            elif op in ("-", "－", "−"):
                result = a - b
            elif op in ("*", "×", "×", "·"):
                result = a * b
            elif op in ("/", "÷", "÷"):
                result = a / b if b != 0 else float("inf")
            else:
                return None

            correct = math.isclose(result, expected, rel_tol=1e-6)
            return Evidence(
                text=f"Computed: {a} {op} {b} = {result} (expected {expected})",
                source="computed",
                source_detail=f"{a}{op}{b}={result}",
                relevance=1.0,
                supports=correct,
            )
        except Exception:
            return None

    @staticmethod
    def _check_logical(claim: FactClaim) -> Optional[Evidence]:
        """Check logical consistency (basic heuristics)."""
        text = claim.text.lower()

        # Check for common logical fallacies
        fallacies = {
            "circular": ["因为.*所以.*因为", "由于.*因此.*由于"],
            "contradiction": ["既.*又.*不", "同时.*且.*不"],
            "false_dilemma": ["要么.*要么", "不是.*就是"],
        }

        for fallacy_type, patterns in fallacies.items():
            for pattern in patterns:
                if re.search(pattern, text):
                    return Evidence(
                        text=f"Potential {fallacy_type} logical fallacy detected.",
                        source="logical",
                        source_detail=f"fallacy:{fallacy_type}",
                        relevance=0.5,
                        supports=False,
                    )

        return Evidence(
            text="No obvious logical fallacies detected.",
            source="logical",
            source_detail="heuristic_check",
            relevance=0.3,
            supports=True,
        )

    @staticmethod
    def _evaluate(
        claim: FactClaim,
        evidence_for: List[Evidence],
        evidence_against: List[Evidence],
    ) -> Tuple[VerificationStatus, float, str]:
        """Determine final verification status from evidence."""
        if claim.claim_type == ClaimType.SUBJECTIVE:
            return (
                VerificationStatus.UNVERIFIABLE,
                0.0,
                "Subjective claim — cannot be objectively verified.",
            )

        total_for = sum(e.relevance for e in evidence_for)
        total_against = sum(e.relevance for e in evidence_against)

        if total_for == 0 and total_against == 0:
            return (
                VerificationStatus.UNCERTAIN,
                0.3,
                "No evidence found either way — unable to verify.",
            )

        if total_for > 0 and total_against == 0:
            if total_for >= 1.5:
                return (
                    VerificationStatus.VERIFIED,
                    min(0.95, 0.6 + total_for * 0.15),
                    f"Verified by {len(evidence_for)} source(s).",
                )
            return (
                VerificationStatus.LIKELY_TRUE,
                min(0.85, 0.5 + total_for * 0.2),
                f"Supported by {len(evidence_for)} source(s), but confidence is moderate.",
            )

        if total_against > 0 and total_for == 0:
            if total_against >= 1.5:
                return (
                    VerificationStatus.CONTRADICTED,
                    0.9,
                    f"Contradicted by {len(evidence_against)} source(s).",
                )
            return (
                VerificationStatus.LIKELY_FALSE,
                0.7,
                f"Evidence suggests this may be incorrect ({len(evidence_against)} source(s) disagree).",
            )

        # Mixed evidence
        net = total_for - total_against
        if net > 0.5:
            return (
                VerificationStatus.LIKELY_TRUE,
                0.6,
                f"Mixed evidence, but overall supporting ({len(evidence_for)} for, {len(evidence_against)} against).",
            )
        elif net < -0.5:
            return (
                VerificationStatus.LIKELY_FALSE,
                0.6,
                f"Mixed evidence, but overall contradicting ({len(evidence_for)} for, {len(evidence_against)} against).",
            )
        else:
            return (
                VerificationStatus.UNCERTAIN,
                0.4,
                f"Conflicting evidence — cannot determine ({len(evidence_for)} for, {len(evidence_against)} against).",
            )


# ════════════════════════════════════════════════════════════════════
# Convenience
# ════════════════════════════════════════════════════════════════════


async def quick_fact_check(text: str, question: str = "") -> FactCheckReport:
    """Convenience: extract and verify claims from text in one call."""
    claims = ClaimExtractor.extract(text)
    checker = FactChecker(enable_web_search=False)
    return await checker.verify_claims(claims, question)
