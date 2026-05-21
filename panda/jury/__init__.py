"""
Panda Agent — Jury Debate Engine (评审辩论引擎)

Multi-model debate with 3-round deliberation, weighted voting,
deception detection, and structured verdict generation.

Architecture:
    ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
    │  deliberate()│────▶│  Round 1: State   │────▶│  Round 2:     │
    │   (async)    │     │  (indep. review)  │     │  Cross-exam   │
    └──────────────┘     └──────────────────┘     └──────────────┘
           │                                              │
           ▼                                              ▼
    ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
    │  JuryVerdict  │◀────│  _detect_decept  │◀────│  Round 3:     │
    │   (result)    │     │  _tally_ballots  │     │  Final Vote   │
    └──────────────┘     └──────────────────┘     └──────────────┘

Key components:
  - VoteDecision: enum for vote outcome classification
  - Ballot: individual juror vote record
  - DebateRound: structured round transcript
  - JuryVerdict: final deliberation result
  - JuryDebate: orchestrates the full debate process
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
import asyncio
import time
import logging
import json
import re
import hashlib

from panda.dispatch import PandaDispatcher, DispatchResult

# ────────────────────────────────────────────────────────────────────
# Structured logging
# ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("panda.jury")


# ════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════

class VoteDecision(Enum):
    """Jury vote outcome classification."""

    CONSENSUS = "consensus"   # >= 80% agreement
    MAJORITY = "majority"     # >= 60% agreement
    SPLIT = "split"           # < 60% agreement, no clear winner
    DEADLOCK = "deadlock"     # exact tie between top contenders


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════

@dataclass
class Ballot:
    """A single juror's final vote after deliberation.

    Attributes:
        voter: Model name that cast this ballot (e.g., "finance-gpt4").
        voted_for: Proposal ID selected ("A", "B", "C", ...).
        confidence: Self-assessed confidence in this vote (0.0–1.0).
        key_reason: Primary justification for the choice.
        against_reasons: Why each *other* proposal was NOT chosen.
        suspected_deception: Whether this juror suspects deception in others.
        suspected_deception_detail: Explanation if suspected_deception is True.
    """

    voter: str
    voted_for: str
    confidence: float
    key_reason: str = ""
    against_reasons: List[str] = field(default_factory=list)
    suspected_deception: bool = False
    suspected_deception_detail: str = ""

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class DebateRound:
    """Transcript of a single debate round.

    Attributes:
        round_number: Which round (1 = statements, 2 = cross-exam, 3 = vote).
        statements: Map of voter → their statement/position for this round.
        challenges: Map of challenger → list of questions posed.
        responses: Map of challenged juror → their response.
        metadata: Arbitrary round-level metadata (timing, costs, etc.).
    """

    round_number: int
    statements: Dict[str, str] = field(default_factory=dict)
    challenges: Dict[str, List[str]] = field(default_factory=dict)
    responses: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JuryVerdict:
    """Final output of the jury deliberation process.

    Attributes:
        decision: Vote outcome classification (CONSENSUS, MAJORITY, etc.).
        winner: Proposal ID that won ("A", "B", "C", ...). Empty if deadlock.
        ballots: All individual juror ballots.
        debate_transcript: Full round-by-round transcript.
        minority_report: Summary of dissenting opinions.
        deception_flags: Warnings about potential deception or manipulation.
        confidence: Overall verdict confidence (0.0–1.0).
        recommendation: Human-readable recommendation in Chinese.
        metadata: Arbitrary metadata (timing, token costs, etc.).
    """

    decision: VoteDecision
    winner: str
    ballots: List[Ballot] = field(default_factory=list)
    debate_transcript: List[DebateRound] = field(default_factory=list)
    minority_report: str = ""
    deception_flags: List[str] = field(default_factory=list)
    confidence: float = 0.0
    recommendation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════════════
# JSON Extraction Helper
# ════════════════════════════════════════════════════════════════════

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Recover a JSON object from LLM output that may contain extra text.

    Strategies (in order):
      1. Direct ``json.loads`` on the stripped text.
      2. Find the first ``{...}`` pair via balanced-brace scanning.
      3. Regex fallback for loose ``{...}`` blocks.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Find JSON inside markdown fences
    fence_pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    matches = re.findall(fence_pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    # 3. Balanced brace scan
    try:
        start = text.index("{")
        depth = 0
        end = start
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    # 4. Loose regex fallback
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to extract JSON from response: %.200s...", text)
    return None


# ════════════════════════════════════════════════════════════════════
# Debate Prompt Templates (Chinese)
# ════════════════════════════════════════════════════════════════════

DEBATE_SYSTEM_PROMPT = """你是一个公正、严谨的评审员。你将以客观、专业的态度参与多方案评审辩论。
你的职责是：
1. 深入分析每个候选方案的优缺点
2. 识别方案中的事实错误、逻辑漏洞或信息缺失
3. 在辩论中坚持真理，不盲从多数意见
4. 发现其他评审可能忽略的关键问题
5. 当发现其他评审可能存在误导或欺骗时，明确指出

请始终保持公正和专业。你的目标是找出最佳方案，而不是支持特定立场。"""


ROUND1_EVALUATION_PROMPT = """你是一个公正的评审员。以下是一个问题的多个候选方案。请独立评审所有方案。

原始问题: {query}

候选方案:
{proposals_text}

请完成以下任务：
1. 对每个方案进行独立、全面的评价（列出优点和缺点）
2. 如果你发现任何方案存在事实错误、逻辑漏洞或信息遗漏，请具体指出
3. 给出你的初步倾向（最佳方案）
4. 给出0-1的置信度

返回JSON格式: 
{{
  "evaluations": {{
    "A": {{"strengths": ["优点1", "优点2"], "weaknesses": ["缺点1"], "factual_issues": ["事实问题"]}},
    "B": {{"strengths": ["优点1"], "weaknesses": ["缺点1", "缺点2"], "factual_issues": []}}
  }},
  "flaws_found": [
    {{"proposal": "A", "issue": "具体问题描述", "severity": "high/medium/low"}}
  ],
  "preliminary_vote": "A",
  "confidence": 0.85,
  "reasoning": "选择理由的详细说明",
  "key_concerns": ["值得关注的要点1", "要点2"]
}}"""


ROUND2_CROSS_EXAM_PROMPT = """你是一个公正的评审员。以下是第一轮评审中其他评审员的观点，以及需要你回应的质疑。

原始问题: {query}

候选方案:
{proposals_text}

第一轮其他评审的观点:
{other_statements}

针对你的质疑（需要回应）:
{challenges_to_you}

请完成以下任务：
1. 回应针对你的质疑（逐一回答）
2. 对其他评审的观点提出你的质疑（如果他们的分析存在漏洞或错误）
3. 根据辩论内容，更新你对各方案的评价
4. 如果你发现其他评审存在事实错误、逻辑漏洞或误导性陈述，请明确指出

返回JSON格式:
{{
  "responses_to_challenges": [
    {{"challenge": "质疑内容", "response": "你的回应"}}
  ],
  "challenges_to_others": [
    {{"target": "评审名称", "issue": "你发现的漏洞或错误", "question": "你提出的质疑问题"}}
  ],
  "updated_evaluations": {{
    "A": {{"strengths": ["更新后的优点"], "weaknesses": ["更新后的缺点"]}}
  }},
  "suspected_deception": false,
  "suspected_deception_detail": "",
  "remarks": "你的综合评论"
}}"""


ROUND3_FINAL_VOTE_PROMPT = """你是一个公正的评审员。经过多轮辩论，请你投出最终一票。

原始问题: {query}

候选方案:
{proposals_text}

完整的辩论记录:
{full_transcript}

请基于所有辩论内容，完成以下任务：
1. 投票选择最佳方案（A/B/C/...）
2. 给出0-1的置信度
3. 说明选择该方案的关键理由
4. 说明不选其他方案的具体原因（逐一说明）
5. 指出辩论中是否发现任何疑似误导或欺骗行为

返回JSON格式:
{{
  "vote": "A",
  "confidence": 0.85,
  "key_reason": "选择该方案的关键理由（详细说明）",
  "against_reasons": {{
    "B": "不选B的具体原因",
    "C": "不选C的具体原因"
  }},
  "suspected_deception": false,
  "suspected_deception_detail": "",
  "final_remarks": "你的最终评论"
}}"""


# ════════════════════════════════════════════════════════════════════
# Default Jury Panel Configuration
# ════════════════════════════════════════════════════════════════════

# Default jurors: a diverse panel of industry perspectives
# Each is a (model_name, industry_key) tuple
DEFAULT_JURY_PANEL: List[Tuple[str, str]] = [
    ("finance-juror", "finance"),
    ("medical-juror", "medical"),
    ("legal-juror", "legal"),
    ("education-juror", "education"),
    ("general-juror", "general"),
]


# ════════════════════════════════════════════════════════════════════
# Jury Debate Engine
# ════════════════════════════════════════════════════════════════════

class JuryDebate:
    """Multi-model jury deliberation with 3-round debate and deception detection.

    Orchestrates a panel of LLM "jurors" through structured debate rounds to
    evaluate competing proposals. Combines weighted voting, cross-examination,
    and automated deception signal detection to produce a reliable verdict.

    Quickstart::

        from panda.dispatch import PandaDispatcher
        from panda.jury import JuryDebate

        dispatcher = PandaDispatcher()
        # ... register industry providers ...

        jury = JuryDebate(dispatcher, min_consensus=0.7, max_rounds=3)

        proposals = {
            "A": {"summary": "方案A：增加客服人员并优化排班", "author": "运营模型"},
            "B": {"summary": "方案B：部署AI客服系统进行分流", "author": "技术模型"},
        }

        verdict = await jury.deliberate(
            query="如何提高客服响应速度？",
            proposals=proposals,
        )
        print(f"Winner: {verdict.winner}, Confidence: {verdict.confidence:.2f}")
        print(f"Recommendation: {verdict.recommendation}")

    Parameters:
        dispatcher: Configured :class:`PandaDispatcher` for all LLM calls.
        memory_graph: Optional :class:`MemoryGraph` for fact-checking against
            the persistent knowledge graph.
        min_consensus: Agreement threshold for CONSENSUS classification (0.0–1.0).
        max_rounds: Maximum debate rounds (1–5, default 3).
        jury_panel: Custom list of (juror_name, industry_key) tuples. If None,
            uses :data:`DEFAULT_JURY_PANEL`.
    """

    # ── Defaults ──────────────────────────────────────────────────────

    DEFAULT_MIN_CONSENSUS = 0.8
    DEFAULT_MAX_ROUNDS = 3
    MAX_ROUNDS_CAP = 5

    # ── Constructor ───────────────────────────────────────────────────

    def __init__(
        self,
        dispatcher: PandaDispatcher,
        memory_graph: Optional[Any] = None,
        min_consensus: float = 0.8,
        max_rounds: int = 3,
        jury_panel: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        if not 0.0 < min_consensus <= 1.0:
            raise ValueError(f"min_consensus must be in (0, 1], got {min_consensus}")
        if max_rounds < 1 or max_rounds > self.MAX_ROUNDS_CAP:
            raise ValueError(
                f"max_rounds must be 1–{self.MAX_ROUNDS_CAP}, got {max_rounds}"
            )

        self._dispatcher = dispatcher
        self._memory_graph = memory_graph
        self._min_consensus = min_consensus
        self._max_rounds = min(max_rounds, self.MAX_ROUNDS_CAP)
        self._jury_panel = jury_panel or list(DEFAULT_JURY_PANEL)

        logger.info(
            "JuryDebate initialized (jurors=%d, min_consensus=%.0f%%, max_rounds=%d)",
            len(self._jury_panel),
            self._min_consensus * 100,
            self._max_rounds,
        )

    # ── Public API ────────────────────────────────────────────────────

    async def deliberate(
        self,
        query: str,
        proposals: Dict[str, dict],
        memory_context: Optional[str] = None,
    ) -> JuryVerdict:
        """Run the full 3-round jury deliberation process.

        Args:
            query: The original user question/problem to solve.
            proposals: Dict mapping proposal ID (e.g., "A") to dict with keys:
                ``summary`` (str) — proposal description,
                ``author`` (str) — name of the proposing model/entity.
            memory_context: Pre-computed memory context string. If None and
                ``memory_graph`` is available, context is auto-fetched.

        Returns:
            A :class:`JuryVerdict` with the deliberation result.

        Raises:
            ValueError: If proposals dict is empty or query is empty.
            RuntimeError: If no juror responses were obtained.
        """
        if not query or not query.strip():
            raise ValueError("query must be non-empty")
        if not proposals:
            raise ValueError("proposals must be non-empty")
        if len(proposals) < 2:
            raise ValueError(f"Need at least 2 proposals, got {len(proposals)}")

        t_start = time.monotonic()
        transcript: List[DebateRound] = []
        juror_names = [name for name, _ in self._jury_panel]

        # Auto-fetch memory context if applicable
        if memory_context is None and self._memory_graph is not None:
            try:
                ctx = self._memory_graph.get_context(query)
                if ctx and ctx.get("relevant_entities"):
                    mem_parts = ["## 知识图谱相关信息"]
                    for ent in ctx["relevant_entities"]:
                        name = ent.get("name", ent.get("id", "?"))
                        etype = ent.get("type", "")
                        props = ent.get("properties", {})
                        neighbors = ent.get("neighbors", [])
                        desc = f"  - {name}"
                        if etype:
                            desc += f" [{etype}]"
                        if props:
                            desc += f" {props}"
                        if neighbors:
                            n_names = [n.get("name", "?") for n in neighbors[:3]]
                            desc += f" (相关: {', '.join(n_names)})"
                        mem_parts.append(desc)
                    if ctx.get("relations"):
                        mem_parts.append("\n已知关系:")
                        for rel in ctx["relations"][:10]:
                            mem_parts.append(
                                f"  {rel.get('source_id','?')} → "
                                f"{rel.get('type','related_to')} → "
                                f"{rel.get('target_id','?')}"
                            )
                    memory_context = "\n".join(mem_parts)
            except Exception as exc:
                logger.warning("Failed to fetch memory context: %s", exc)

        # Build proposals text (reusable)
        proposals_text = self._format_proposals(proposals)

        # ── ROUND 1: Independent Position Statements ──────────────────
        logger.info("Round 1: Independent position statements (jurors=%d)", len(juror_names))
        round1 = await self._run_round1(query, proposals_text, memory_context, juror_names)
        transcript.append(round1)
        logger.info("Round 1 complete: %d statements collected", len(round1.statements))

        # ── ROUND 2: Cross-Examination ────────────────────────────────
        logger.info("Round 2: Cross-examination among jurors")
        round2 = await self._run_round2(query, proposals_text, round1, juror_names)
        transcript.append(round2)
        logger.info(
            "Round 2 complete: %d challenges, %d responses",
            sum(len(v) for v in round2.challenges.values()),
            len(round2.responses),
        )

        # ── ROUND 3: Final Voting ─────────────────────────────────────
        logger.info("Round 3: Final voting")
        ballots = await self._run_round3(query, proposals_text, transcript, juror_names)
        logger.info("Round 3 complete: %d ballots cast", len(ballots))

        # ── Tally & Verdict ───────────────────────────────────────────
        tally_result = self._tally(ballots, list(proposals.keys()))
        deception_flags = self._detect_deception(ballots, transcript, proposals)
        recommendation = self._generate_recommendation(
            tally_result, deception_flags, len(proposals)
        )
        overall_confidence = self._calculate_overall_confidence(
            tally_result, ballots
        )

        verdict = JuryVerdict(
            decision=tally_result["decision"],
            winner=tally_result["winner"],
            ballots=ballots,
            debate_transcript=transcript,
            minority_report=tally_result["minority_report"],
            deception_flags=deception_flags,
            confidence=overall_confidence,
            recommendation=recommendation,
            metadata={
                "query": query,
                "proposal_count": len(proposals),
                "jury_size": len(juror_names),
                "rounds": len(transcript),
                "total_latency_ms": (time.monotonic() - t_start) * 1000,
                "min_consensus": self._min_consensus,
                "vote_distribution": tally_result["distribution"],
            },
        )

        logger.info(
            "Jury verdict: winner=%s decision=%s confidence=%.2f",
            verdict.winner,
            verdict.decision.value,
            verdict.confidence,
        )
        return verdict

    # ── Round 1: Independent Statements ──────────────────────────────

    async def _run_round1(
        self,
        query: str,
        proposals_text: str,
        memory_context: Optional[str],
        juror_names: List[str],
    ) -> DebateRound:
        """Each juror independently evaluates all proposals (no cross-influence)."""
        prompt = ROUND1_EVALUATION_PROMPT.format(
            query=query,
            proposals_text=proposals_text,
        )

        tasks = []
        for juror_name, industry in self._jury_panel:
            tasks.append(
                self._dispatch_to_juror(
                    juror_name=juror_name,
                    industry=industry,
                    prompt=prompt,
                    memory_context=memory_context,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        statements: Dict[str, str] = {}
        for (juror_name, _), result in zip(self._jury_panel, results):
            if isinstance(result, Exception):
                logger.error("Juror '%s' failed in Round 1: %s", juror_name, result)
                statements[juror_name] = json.dumps(
                    {
                        "error": f"评审员 {juror_name} 响应异常: {result}",
                        "preliminary_vote": "",
                        "confidence": 0.0,
                        "reasoning": "评审员响应失败",
                    },
                    ensure_ascii=False,
                )
            else:
                statements[juror_name] = result

        return DebateRound(round_number=1, statements=statements)

    # ── Round 2: Cross-Examination ───────────────────────────────────

    async def _run_round2(
        self,
        query: str,
        proposals_text: str,
        round1: DebateRound,
        juror_names: List[str],
    ) -> DebateRound:
        """Each juror sees others' statements and challenges them."""
        challenges: Dict[str, List[str]] = {}
        responses: Dict[str, str] = {}
        round2_statements: Dict[str, str] = {}

        tasks = []
        for i, (juror_name, industry) in enumerate(self._jury_panel):
            # Build other jurors' statements for this juror to review
            other_statements = {}
            for other_name in juror_names:
                if other_name != juror_name and other_name in round1.statements:
                    other_statements[other_name] = round1.statements[other_name]

            # Build any challenges from previous iteration (first pass: none)
            challenges_to_me = challenges.get(juror_name, [])

            other_text = self._format_other_statements(other_statements)
            challenges_text = self._format_challenges(challenges_to_me)

            prompt = ROUND2_CROSS_EXAM_PROMPT.format(
                query=query,
                proposals_text=proposals_text,
                other_statements=other_text,
                challenges_to_you=challenges_text,
            )

            tasks.append(
                self._dispatch_to_juror(
                    juror_name=juror_name,
                    industry=industry,
                    prompt=prompt,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results: extract challenges to others, responses, and updated statements
        for (juror_name, _), result in zip(self._jury_panel, results):
            if isinstance(result, Exception):
                logger.error("Juror '%s' failed in Round 2: %s", juror_name, result)
                responses[juror_name] = f"评审员 {juror_name} 响应异常: {result}"
                round2_statements[juror_name] = responses[juror_name]
                continue

            round2_statements[juror_name] = result

            parsed = _extract_json(result)
            if parsed is None:
                logger.warning(
                    "Failed to parse Round 2 response from '%s' as JSON", juror_name
                )
                responses[juror_name] = result
                continue

            # Collect responses to challenges
            resp_list = parsed.get("responses_to_challenges", [])
            if resp_list:
                resp_parts = []
                for r in resp_list:
                    challenge = r.get("challenge", "")
                    response = r.get("response", "")
                    resp_parts.append(f"质疑: {challenge}\n回应: {response}")
                responses[juror_name] = "\n\n".join(resp_parts)
            else:
                responses[juror_name] = parsed.get("remarks", "无具体回应")

            # Collect new challenges to others
            new_challenges = parsed.get("challenges_to_others", [])
            for ch in new_challenges:
                target = ch.get("target", "")
                question = ch.get("question", ch.get("issue", ""))
                if target and question:
                    if target not in challenges:
                        challenges[target] = []
                    challenges[target].append(
                        f"[来自 {juror_name}] {question}"
                    )

        return DebateRound(
            round_number=2,
            statements=round2_statements,
            challenges=challenges,
            responses=responses,
        )

    # ── Round 3: Final Voting ────────────────────────────────────────

    async def _run_round3(
        self,
        query: str,
        proposals_text: str,
        transcript: List[DebateRound],
        juror_names: List[str],
    ) -> List[Ballot]:
        """Each juror casts a final ballot after reviewing the full transcript."""
        full_transcript = self._format_full_transcript(transcript)

        prompt = ROUND3_FINAL_VOTE_PROMPT.format(
            query=query,
            proposals_text=proposals_text,
            full_transcript=full_transcript,
        )

        tasks = []
        for juror_name, industry in self._jury_panel:
            tasks.append(
                self._dispatch_to_juror(
                    juror_name=juror_name,
                    industry=industry,
                    prompt=prompt,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        ballots: List[Ballot] = []
        for (juror_name, _), result in zip(self._jury_panel, results):
            if isinstance(result, Exception):
                logger.error(
                    "Juror '%s' failed in Round 3: %s — recording abstention",
                    juror_name,
                    result,
                )
                ballots.append(
                    Ballot(
                        voter=juror_name,
                        voted_for="",
                        confidence=0.0,
                        key_reason=f"评审员响应异常: {result}",
                        against_reasons=["响应失败，无法评估"],
                        suspected_deception=False,
                    )
                )
                continue

            parsed = _extract_json(result)
            if parsed is None:
                logger.warning(
                    "Failed to parse Round 3 ballot from '%s' as JSON", juror_name
                )
                ballots.append(
                    Ballot(
                        voter=juror_name,
                        voted_for="",
                        confidence=0.0,
                        key_reason=f"响应格式异常，无法解析: {result[:200]}",
                        against_reasons=["响应解析失败"],
                        suspected_deception=False,
                    )
                )
                continue

            against_dict = parsed.get("against_reasons", {})
            against_list: List[str] = []
            if isinstance(against_dict, dict):
                for prop_id, reason in against_dict.items():
                    against_list.append(f"方案{prop_id}: {reason}")
            elif isinstance(against_dict, list):
                against_list = [str(item) for item in against_dict]

            ballots.append(
                Ballot(
                    voter=juror_name,
                    voted_for=str(parsed.get("vote", "")).strip().upper(),
                    confidence=float(parsed.get("confidence", 0.5)),
                    key_reason=str(parsed.get("key_reason", ""))[:500],
                    against_reasons=against_list,
                    suspected_deception=bool(
                        parsed.get("suspected_deception", False)
                    ),
                    suspected_deception_detail=str(
                        parsed.get("suspected_deception_detail", "")
                    )[:300],
                )
            )

        return ballots

    # ── Vote Tally ───────────────────────────────────────────────────

    def _tally(
        self,
        ballots: List[Ballot],
        proposal_ids: List[str],
    ) -> Dict[str, Any]:
        """Count ballots, determine winner, classify decision type.

        Returns a dict with keys:
            - decision: VoteDecision enum value
            - winner: winning proposal ID (empty if no clear winner)
            - distribution: {proposal_id: vote_count}
            - vote_percentages: {proposal_id: percentage}
            - minority_report: str summarizing dissenting opinions
            - top_two: list of top two proposal IDs
        """
        valid_ballots = [b for b in ballots if b.voted_for]
        total_valid = len(valid_ballots)

        if total_valid == 0:
            return {
                "decision": VoteDecision.DEADLOCK,
                "winner": "",
                "distribution": {pid: 0 for pid in proposal_ids},
                "vote_percentages": {pid: 0.0 for pid in proposal_ids},
                "minority_report": "无有效投票，评审陷入僵局。",
                "top_two": ["", ""],
            }

        # Count votes
        distribution: Dict[str, int] = {pid: 0 for pid in proposal_ids}
        voted_for_map: Dict[str, List[Ballot]] = {}
        for b in valid_ballots:
            vid = b.voted_for
            if vid in proposal_ids:
                distribution[vid] += 1
                if vid not in voted_for_map:
                    voted_for_map[vid] = []
                voted_for_map[vid].append(b)
            else:
                # Vote for unknown proposal — treat as abstained
                logger.warning(
                    "Juror '%s' voted for unknown proposal '%s'", b.voter, vid
                )

        # Calculate percentages
        vote_percentages = {
            pid: (count / total_valid * 100) if total_valid > 0 else 0.0
            for pid, count in distribution.items()
        }

        # Sort by vote count descending
        sorted_pairs = sorted(distribution.items(), key=lambda x: x[1], reverse=True)
        top_two_proposals = [
            sorted_pairs[0][0] if len(sorted_pairs) > 0 else "",
            sorted_pairs[1][0] if len(sorted_pairs) > 1 else "",
        ]

        winner_id = sorted_pairs[0][0]
        winner_count = sorted_pairs[0][1]
        winner_pct = (winner_count / total_valid * 100) if total_valid > 0 else 0.0

        # Check for tie at the top
        if len(sorted_pairs) > 1 and sorted_pairs[0][1] == sorted_pairs[1][1]:
            decision = VoteDecision.DEADLOCK
            winner_id = ""  # No winner in a tie
            minority_report = self._build_minority_report(
                ballots, proposal_ids, sorted_pairs, total_valid
            )
        elif winner_pct >= self._min_consensus * 100:
            decision = VoteDecision.CONSENSUS
            minority_report = self._build_minority_report(
                ballots, proposal_ids, sorted_pairs, total_valid
            )
        elif winner_pct >= 60.0:
            decision = VoteDecision.MAJORITY
            minority_report = self._build_minority_report(
                ballots, proposal_ids, sorted_pairs, total_valid
            )
        else:
            decision = VoteDecision.SPLIT
            minority_report = self._build_minority_report(
                ballots, proposal_ids, sorted_pairs, total_valid
            )

        logger.info(
            "Tally: %s — votes=%s winner_pct=%.1f%%",
            decision.value,
            distribution,
            winner_pct,
        )

        return {
            "decision": decision,
            "winner": winner_id,
            "distribution": distribution,
            "vote_percentages": vote_percentages,
            "minority_report": minority_report,
            "top_two": top_two_proposals,
        }

    @staticmethod
    def _build_minority_report(
        ballots: List[Ballot],
        proposal_ids: List[str],
        sorted_pairs: List[Tuple[str, int]],
        total_valid: int,
    ) -> str:
        """Generate a minority report summarizing dissent."""
        if len(sorted_pairs) <= 1:
            return "无少数意见（所有投票一致）。"

        winner_id = sorted_pairs[0][0] if sorted_pairs[0][1] > sorted_pairs[1][1] else ""

        # Collect non-winning ballots
        minority_ballots = [b for b in ballots if b.voted_for != winner_id and b.voted_for]

        if not minority_ballots:
            # All ballots are for winner, but maybe some are empty
            abstentions = [b for b in ballots if not b.voted_for]
            if abstentions:
                parts = ["少数报告:"]
                parts.append(f"  {len(abstentions)} 位评审未投出有效票")
                for b in abstentions:
                    parts.append(f"  - {b.voter}: {b.key_reason[:100]}")
                return "\n".join(parts)
            return "全体一致同意，无少数意见。"

        # Group minority ballots by voted_for
        grouped: Dict[str, List[Ballot]] = {}
        for b in minority_ballots:
            grouped.setdefault(b.voted_for, []).append(b)

        parts = ["少数报告（非获胜方案支持者意见）:"]
        for pid, group in grouped.items():
            parts.append(f"\n  支持方案{pid}的评审意见 ({len(group)} 票):")
            for b in group:
                parts.append(f"  - {b.voter}: {b.key_reason[:150]}")
                if b.suspected_deception:
                    parts.append(f"    ⚠ 疑似欺骗: {b.suspected_deception_detail[:100]}")

        return "\n".join(parts)

    # ── Deception Detection ──────────────────────────────────────────

    def _detect_deception(
        self,
        ballots: List[Ballot],
        transcript: List[DebateRound],
        proposals: Dict[str, dict],
    ) -> List[str]:
        """Analyze ballots and transcript for deception signals.

        Five detection signals:
            a) Over-confidence: confidence > 0.99 but key_reason too short
            b) Accused by peers: other jurors flagged suspected_deception
            c) Contradiction: conflicting analyses between jurors
            d) Factual inconsistency: check against MemoryGraph
            e) Evasion: vague or off-topic response

        Returns a list of Chinese-language flag strings.
        """
        flags: List[str] = []

        # ── Signal (a): Over-confidence ───────────────────────────────
        for b in ballots:
            if b.confidence > 0.99 and len(b.key_reason.strip()) < 20:
                flags.append(
                    f"[过度自信] 评审员 {b.voter} 置信度={b.confidence}% "
                    f"但关键理由过短({len(b.key_reason.strip())}字符)，"
                    f"可能存在虚高自信或缺乏实质分析。"
                )

        # ── Signal (b): Accused by peers ──────────────────────────────
        accused_voters: Dict[str, List[str]] = {}
        for b in ballots:
            if b.suspected_deception and b.suspected_deception_detail:
                accused_voters.setdefault(b.voter, []).append(
                    b.suspected_deception_detail
                )

        # Also check Round 2 for deception flags in parsed responses
        for round_data in transcript:
            if round_data.round_number == 2:
                for juror_name, statement in round_data.statements.items():
                    parsed = _extract_json(statement)
                    if parsed and parsed.get("suspected_deception"):
                        detail = parsed.get("suspected_deception_detail", "")
                        accused_voters.setdefault(juror_name, []).append(detail)

        for accuser, details in accused_voters.items():
            flags.append(
                f"[同侪指控] 评审员 {accuser} 被其他评审指控可能存在欺骗行为。"
                f"详情: {'; '.join(details[:3])}"
            )

        # ── Signal (c): Contradiction ─────────────────────────────────
        # Compare Round 1 evaluations for contradictory assessments
        contradictions = self._detect_contradictions(transcript, ballots)
        flags.extend(contradictions)

        # ── Signal (d): Factual inconsistency via MemoryGraph ─────────
        if self._memory_graph is not None:
            factual_flags = self._check_factual_consistency(ballots, proposals)
            flags.extend(factual_flags)

        # ── Signal (e): Evasion ───────────────────────────────────────
        evasion_flags = self._detect_evasion(ballots, transcript)
        flags.extend(evasion_flags)

        if flags:
            logger.warning("Deception signals detected: %d flags", len(flags))
        else:
            logger.info("No deception signals detected")

        return flags

    def _detect_contradictions(
        self,
        transcript: List[DebateRound],
        ballots: List[Ballot],
    ) -> List[str]:
        """Detect contradictions between jurors' evaluations."""
        flags: List[str] = []

        # Build a map of which proposals each juror supports
        voter_support: Dict[str, str] = {}
        for b in ballots:
            voter_support[b.voter] = b.voted_for

        # Check Round 1 for extreme disagreement patterns
        for round_data in transcript:
            if round_data.round_number != 1:
                continue

            # Extract preliminary votes from Round 1
            r1_votes: Dict[str, str] = {}
            for juror_name, statement in round_data.statements.items():
                parsed = _extract_json(statement)
                if parsed:
                    prelim = parsed.get("preliminary_vote", "")
                    if prelim:
                        r1_votes[juror_name] = str(prelim).strip().upper()

            # Check if a juror flipped dramatically between R1 and final
            for juror_name, final_vote in voter_support.items():
                r1_vote = r1_votes.get(juror_name, "")
                if r1_vote and final_vote and r1_vote != final_vote:
                    # Dramatic flip — check if reasoning is provided
                    ballot = next(
                        (b for b in ballots if b.voter == juror_name), None
                    )
                    if ballot and len(ballot.key_reason) < 30:
                        flags.append(
                            f"[立场翻转] 评审员 {juror_name} 从初步倾向方案{r1_vote} "
                            f"翻转为最终投票方案{final_vote}，但未提供充分理由。"
                        )

        return flags

    def _check_factual_consistency(
        self,
        ballots: List[Ballot],
        proposals: Dict[str, dict],
    ) -> List[str]:
        """Check ballot claims against MemoryGraph for factual consistency."""
        flags: List[str] = []

        try:
            # Combine all key reasons as a query to memory
            claims_text = " ".join(b.key_reason for b in ballots if b.key_reason)
            if not claims_text:
                return flags

            ctx = self._memory_graph.get_context(claims_text, max_entities=5)
            if not ctx or not ctx.get("relevant_entities"):
                return flags

            # For each ballot, check if key reasons contradict known facts
            for b in ballots:
                if not b.key_reason:
                    continue
                # Simple heuristic: look for entity names in key_reason
                # that are flagged as contradictory in memory
                for ent in ctx.get("relevant_entities", []):
                    ent_name = ent.get("name", "")
                    if ent_name and ent_name in b.key_reason:
                        # Check if the entity has contradictory properties
                        props = ent.get("properties", {})
                        if props.get("status") == "deprecated" or props.get(
                            "disputed", False
                        ):
                            flags.append(
                                f"[事实矛盾] 评审员 {b.voter} 的理由引用了存疑实体"
                                f" '{ent_name}' (状态: {props.get('status', '未知')})。"
                            )
        except Exception as exc:
            logger.warning("Factual consistency check failed: %s", exc)

        return flags

    def _detect_evasion(
        self,
        ballots: List[Ballot],
        transcript: List[DebateRound],
    ) -> List[str]:
        """Detect evasive or vague responses that avoid substantive analysis."""
        flags: List[str] = []

        # Evasion signal keywords (Chinese)
        vague_phrases = [
            "无法确定", "不好说", "都有可能", "视情况而定",
            "需要更多信息", "建议进一步研究", "暂时无法判断",
            "缺乏足够数据", "各有优劣", "难以抉择",
        ]

        for b in ballots:
            reason_lower = b.key_reason.lower() if b.key_reason else ""
            # Count vague phrases
            vague_count = sum(1 for phrase in vague_phrases if phrase in reason_lower)

            # If the reason is mostly vague phrases and confidence is low
            if vague_count >= 2 and b.confidence < 0.4:
                flags.append(
                    f"[回避分析] 评审员 {b.voter} 的理由包含多处模糊表述"
                    f"({vague_count}处)，且置信度低({b.confidence:.0%})，"
                    f"可能未进行实质性分析。"
                )

            # Check for extremely short responses that avoid commitment
            if len(b.key_reason.strip()) < 15 and b.confidence < 0.5:
                if any(phrase in reason_lower for phrase in vague_phrases):
                    flags.append(
                        f"[回避投票] 评审员 {b.voter} 理由过于简短"
                        f"({len(b.key_reason.strip())}字符)，疑似回避实质性判断。"
                    )

        # Also check Round 2 responses for evasiveness
        for round_data in transcript:
            if round_data.round_number != 2:
                continue
            for juror_name, response in round_data.responses.items():
                resp_len = len(response.strip())
                if resp_len < 20:
                    flags.append(
                        f"[回避辩论] 评审员 {juror_name} 在交叉质询中回应过短"
                        f"({resp_len}字符)，可能回避了关键质疑。"
                    )

        return flags

    # ── Recommendation Generation ─────────────────────────────────────

    def _generate_recommendation(
        self,
        tally_result: Dict[str, Any],
        deception_flags: List[str],
        num_proposals: int,
    ) -> str:
        """Produce a human-readable recommendation based on the verdict."""
        decision: VoteDecision = tally_result["decision"]
        winner = tally_result["winner"]
        distribution = tally_result.get("distribution", {})
        vote_pcts = tally_result.get("vote_percentages", {})

        # Base recommendation by decision type
        if decision == VoteDecision.CONSENSUS:
            rec = (
                f"✅ 全体评审达成共识（{self._min_consensus*100:.0f}%以上同意），"
                f"方案{winner}获得高度认可。可以高度信任此结论，建议直接采纳方案{winner}。"
            )
        elif decision == VoteDecision.MAJORITY:
            winner_pct = vote_pcts.get(winner, 0)
            rec = (
                f"👍 多数评审支持方案{winner}（得票率{winner_pct:.1f}%），"
                f"建议采纳但需关注少数意见。请审阅少数报告后做出最终决定。"
            )
        elif decision == VoteDecision.SPLIT:
            # Find the top contender
            sorted_items = sorted(
                distribution.items(), key=lambda x: x[1], reverse=True
            )
            if len(sorted_items) >= 2:
                top_a, top_b = sorted_items[0][0], sorted_items[1][0]
                pct_a = vote_pcts.get(top_a, 0)
                pct_b = vote_pcts.get(top_b, 0)
                rec = (
                    f"⚡ 评审存在明显分歧，方案{top_a}（{pct_a:.1f}%）和方案{top_b}"
                    f"（{pct_b:.1f}%）支持度接近。建议由用户介入判断，"
                    f"或补充更多信息后重新评审。"
                )
            else:
                rec = "⚡ 评审存在明显分歧，建议用户介入判断。"
        elif decision == VoteDecision.DEADLOCK:
            sorted_items = sorted(
                distribution.items(), key=lambda x: x[1], reverse=True
            )
            if len(sorted_items) >= 2:
                top_a, top_b = sorted_items[0][0], sorted_items[1][0]
                rec = (
                    f"🚫 评审陷入僵局，方案{top_a}和方案{top_b}得票相同。"
                    f"强烈建议由用户主导决策，或引入额外评审员重新辩论。"
                )
            else:
                rec = "🚫 评审陷入僵局，强烈建议由用户主导决策。"
        else:
            rec = "评审结果异常，建议用户介入。"

        # Append vote breakdown
        vote_breakdown = "，".join(
            f"方案{pid}: {count}票({pct:.1f}%)"
            for pid, count in distribution.items()
            for pct in [vote_pcts.get(pid, 0)]
        )
        rec += f"\n\n📊 投票分布: {vote_breakdown}"

        # Append deception warnings if any
        if deception_flags:
            rec += f"\n\n⚠️ 欺骗检测警告 ({len(deception_flags)} 项):"
            for flag in deception_flags[:5]:  # Max 5 listed in recommendation
                rec += f"\n  • {flag}"
            if len(deception_flags) > 5:
                rec += f"\n  • ... 及其他 {len(deception_flags) - 5} 项"

        # Append minority report tip if available
        minority = tally_result.get("minority_report", "")
        if minority and "全体一致同意" not in minority and "无少数意见" not in minority:
            rec += (
                f"\n\n📝 少数意见摘要:\n"
                f"{minority[:500]}{'...' if len(minority) > 500 else ''}"
            )

        return rec

    # ── Confidence Calculation ────────────────────────────────────────

    @staticmethod
    def _calculate_overall_confidence(
        tally_result: Dict[str, Any],
        ballots: List[Ballot],
    ) -> float:
        """Calculate overall verdict confidence from vote consensus and juror confidence.

        Formula: vote_consensus_ratio * avg_winner_confidence
        Clamped to [0, 1].
        """
        winner = tally_result["winner"]
        vote_pcts = tally_result.get("vote_percentages", {})

        # Vote consensus ratio: how much the winning proposal dominated
        winner_pct = vote_pcts.get(winner, 0) / 100.0

        # Average confidence of jurors who voted for the winner
        winner_ballots = [b for b in ballots if b.voted_for == winner]
        if winner_ballots:
            avg_winner_conf = sum(b.confidence for b in winner_ballots) / len(
                winner_ballots
            )
        else:
            # No winner — use average of all confidences penalized
            all_conf = [b.confidence for b in ballots if b.confidence > 0]
            avg_winner_conf = (
                sum(all_conf) / len(all_conf) * 0.5 if all_conf else 0.3
            )

        overall = winner_pct * avg_winner_conf
        return round(max(0.0, min(1.0, overall)), 4)

    # ── Dispatch Helper ───────────────────────────────────────────────

    async def _dispatch_to_juror(
        self,
        juror_name: str,
        industry: str,
        prompt: str,
        memory_context: Optional[str] = None,
    ) -> str:
        """Dispatch a prompt to a single juror via the PandaDispatcher.

        Args:
            juror_name: Human-readable juror identifier.
            industry: Industry key registered in the dispatcher.
            prompt: The debate round prompt to send.
            memory_context: Optional memory-enriched context to inject.

        Returns:
            The raw text response from the juror LLM.

        Raises:
            Exception: If the dispatch fails (caught by caller).
        """
        messages = [
            {"role": "system", "content": DEBATE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        knowledge = memory_context or None

        try:
            result: DispatchResult = await self._dispatcher.dispatch(
                industry=industry,
                messages=messages,
                knowledge=knowledge,
                stream=False,
            )
            logger.debug(
                "Juror '%s' (%s/%s) responded in %.0f ms",
                juror_name,
                industry,
                result.model,
                result.latency_ms,
            )
            return result.content
        except Exception as exc:
            logger.error(
                "Dispatch to juror '%s' (industry=%s) failed: %s",
                juror_name,
                industry,
                exc,
            )
            raise

    # ── Formatting Helpers ────────────────────────────────────────────

    @staticmethod
    def _format_proposals(proposals: Dict[str, dict]) -> str:
        """Format proposal dict into a prompt-friendly string."""
        parts = []
        for pid, info in proposals.items():
            author = info.get("author", "未知")
            summary = info.get("summary", "")

            part = f"方案{pid} (提案者: {author}):\n  {summary}"

            # Include any extra fields
            for key, value in info.items():
                if key not in ("summary", "author"):
                    part += f"\n  {key}: {value}"

            parts.append(part)
        return "\n\n".join(parts)

    @staticmethod
    def _format_other_statements(statements: Dict[str, str]) -> str:
        """Format other jurors' statements for cross-examination."""
        if not statements:
            return "（本次无其他评审观点）"

        parts = []
        for name, statement in statements.items():
            # Truncate very long statements
            display = statement[:1500] + ("..." if len(statement) > 1500 else "")
            parts.append(f"评审员 {name} 的观点:\n{display}")
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _format_challenges(challenges: List[str]) -> str:
        """Format challenges for a juror to respond to."""
        if not challenges:
            return "（本轮暂无针对你的质疑）"

        parts = ["以下是对你的质疑，请逐一回应:"]
        for i, ch in enumerate(challenges, 1):
            parts.append(f"  {i}. {ch}")
        return "\n".join(parts)

    @staticmethod
    def _format_full_transcript(transcript: List[DebateRound]) -> str:
        """Format all debate rounds into a complete transcript string."""
        parts = []

        for rd in transcript:
            parts.append(f"{'=' * 60}")
            parts.append(f"第 {rd.round_number} 轮辩论")
            parts.append(f"{'=' * 60}")

            if rd.round_number == 1:
                parts.append("【独立评审阶段】")
                for name, statement in rd.statements.items():
                    display = statement[:1200] + (
                        "..." if len(statement) > 1200 else ""
                    )
                    parts.append(f"\n评审员 {name}:\n{display}")

            elif rd.round_number == 2:
                parts.append("【交叉质询阶段】")
                if rd.challenges:
                    parts.append("\n-- 质询记录 --")
                    for challenger, questions in rd.challenges.items():
                        parts.append(f"\n对评审员 {challenger} 的质询:")
                        for q in questions:
                            parts.append(f"  • {q}")
                if rd.responses:
                    parts.append("\n-- 回应记录 --")
                    for name, response in rd.responses.items():
                        display = response[:800] + (
                            "..." if len(response) > 800 else ""
                        )
                        parts.append(f"\n评审员 {name} 回应:\n{display}")

            elif rd.round_number == 3:
                parts.append("【最终投票阶段】")
                for name, statement in rd.statements.items():
                    display = statement[:800] + (
                        "..." if len(statement) > 800 else ""
                    )
                    parts.append(f"\n评审员 {name}:\n{display}")

            parts.append("")

        return "\n".join(parts)

    # ── Convenience Methods ───────────────────────────────────────────

    @property
    def jury_size(self) -> int:
        """Number of jurors on this panel."""
        return len(self._jury_panel)

    @property
    def juror_names(self) -> List[str]:
        """List of juror names."""
        return [name for name, _ in self._jury_panel]

    def get_juror_industry(self, juror_name: str) -> Optional[str]:
        """Get the industry assigned to a specific juror."""
        for name, industry in self._jury_panel:
            if name == juror_name:
                return industry
        return None

    def __repr__(self) -> str:
        return (
            f"JuryDebate(jurors={self.jury_size}, "
            f"min_consensus={self._min_consensus:.0%}, "
            f"max_rounds={self._max_rounds})"
        )


# ════════════════════════════════════════════════════════════════════
# Module exports
# ════════════════════════════════════════════════════════════════════

__all__ = [
    "VoteDecision",
    "Ballot",
    "DebateRound",
    "JuryVerdict",
    "JuryDebate",
    "DEFAULT_JURY_PANEL",
]
