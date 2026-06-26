"""
Dragon Report — Markdown Report Generator

Generates structured Markdown reports from JuryVerdict results.
Used by DragonPipeline for complex task outputs.

Report sections:
1. Executive Summary (风险值 + 决策)
2. Problem & Proposals (问题 + 方案)
3. Debate Process (辩论过程，3轮)
4. Final Verdict (最终裁决 + 投票分布)
5. Risk Assessment (风险评估详解)
6. Minority Report (少数派意见)
7. Recommendation (建议)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from dragon.jury import JuryVerdict, VoteDecision
from dragon.pipeline import RiskLevel

logger = logging.getLogger("dragon.report")


# ════════════════════════════════════════════════════════════════════════
# Main Report Generator
# ════════════════════════════════════════════════════════════════════════

def generate_verdict_report(
    verdict: JuryVerdict,
    risk_score: float = 0.0,
    risk_level: Optional[RiskLevel] = None,
    *,
    title: str = "Dragon Agent 多模型评审报告",
) -> str:
    """Generate a comprehensive Markdown report from a JuryVerdict.

    Args:
        verdict: The jury deliberation result.
        risk_score: Computed risk score (0-100).
        risk_level: Risk classification for display.
        title: Report title override.

    Returns:
        Complete Markdown report string.
    """
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Header ────────────────────────────────────────────────────────
    lines.append(f"# {title}")
    lines.append(f"")
    lines.append(f"> 生成时间: {now}  |  评审模型数: {len(verdict.ballots)}  |  辩论轮次: {len(verdict.debate_transcript)}")
    lines.append(f"")

    # ── Executive Summary ─────────────────────────────────────────────
    lines.extend(_build_executive_summary(verdict, risk_score, risk_level))

    # ── Problem & Proposals ───────────────────────────────────────────
    lines.extend(_build_proposals_section(verdict))

    # ── Debate Process ─────────────────────────────────────────────────
    lines.extend(_build_debate_section(verdict))

    # ── Final Verdict ─────────────────────────────────────────────────
    lines.extend(_build_verdict_section(verdict))

    # ── Risk Assessment ───────────────────────────────────────────────
    lines.extend(_build_risk_section(verdict, risk_score, risk_level))

    # ── Minority Report ───────────────────────────────────────────────
    lines.extend(_build_minority_section(verdict))

    # ── Recommendation ────────────────────────────────────────────────
    lines.extend(_build_recommendation_section(verdict))

    # ── Footer ────────────────────────────────────────────────────────
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"*本报告由 Dragon Agent 多模型评审引擎自动生成。*")
    lines.append(f"*辩论引擎: JuryDebate v1.0  |  风险引擎: DragonPipeline v1.0*")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════
# Section Builders
# ════════════════════════════════════════════════════════════════════════

def _build_executive_summary(
    verdict: JuryVerdict,
    risk_score: float,
    risk_level: Optional[RiskLevel],
) -> list[str]:
    """Build the executive summary section."""
    lines: list[str] = []

    # Risk indicator
    if risk_level:
        risk_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🟠",
            "critical": "🔴",
        }
        risk_labels = {
            "low": "低风险",
            "medium": "中等风险",
            "high": "高风险",
            "critical": "极高风险",
        }
        emoji = risk_emoji.get(risk_level.value if hasattr(risk_level, 'value') else str(risk_level), "⚪")
        label = risk_labels.get(risk_level.value if hasattr(risk_level, 'value') else str(risk_level), "未知")
    else:
        emoji = "⚪"
        label = "未评估"

    # Decision emoji
    decision_emoji = {
        "consensus": "✅",
        "majority": "👍",
        "split": "⚠️",
        "deadlock": "❌",
    }
    dec = verdict.decision.value if hasattr(verdict.decision, 'value') else str(verdict.decision)
    dec_emoji = decision_emoji.get(dec, "❓")

    lines.append(f"## 📊 执行摘要")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| **决策类型** | {dec_emoji} {_label(dec)} |")
    lines.append(f"| **胜出方案** | **{verdict.winner}** |")
    lines.append(f"| **置信度** | {verdict.confidence:.1%} |")
    lines.append(f"| **风险评分** | {emoji} **{risk_score:.1f}/100** ({label}) |")

    risk_action = "⚠️ **需要人工审批后执行**" if risk_score >= 50 else "✅ **可自动执行**"
    lines.append(f"| **执行建议** | {risk_action} |")
    lines.append(f"")
    lines.append(f"**总评:** {verdict.recommendation[:300]}")
    lines.append(f"")

    return lines


def _build_proposals_section(verdict: JuryVerdict) -> list[str]:
    """Build the problem and proposals section from debate metadata."""
    lines: list[str] = []
    metadata = verdict.metadata

    query = metadata.get("query", "未知问题")
    proposal_count = metadata.get("proposal_count", 0)

    lines.append(f"## 📝 问题与方案")
    lines.append(f"")
    lines.append(f"**原始问题:** {query}")
    lines.append(f"")
    lines.append(f"共收到 **{proposal_count}** 个候选方案，经过 **{len(verdict.debate_transcript)}** 轮辩论评审。")
    lines.append(f"")

    # Extract proposal descriptions from first round statements
    if verdict.debate_transcript:
        round1 = verdict.debate_transcript[0]
        lines.append(f"### 候选方案概述")
        lines.append(f"")

        for juror, statement in round1.statements.items():
            # Try to extract key info from first 200 chars
            preview = statement[:200].replace("\n", " ").strip()
            lines.append(f"- **{juror}**: {preview}...")
        lines.append(f"")

    return lines


def _build_debate_section(verdict: JuryVerdict) -> list[str]:
    """Build the debate process section."""
    lines: list[str] = []

    if not verdict.debate_transcript:
        return lines

    lines.append(f"## 🔄 辩论过程")
    lines.append(f"")

    for round_obj in verdict.debate_transcript:
        rn = round_obj.round_number
        round_names = {1: "独立评审", 2: "交叉质询", 3: "最终投票"}

        lines.append(f"### 第 {rn} 轮: {round_names.get(rn, f'第{rn}轮')}")
        lines.append(f"")

        if round_obj.statements:
            lines.append(f"**各评审员陈述:**")
            for juror, statement in round_obj.statements.items():
                # Truncate long statements
                truncated = statement[:300].replace("\n", " ")
                if len(statement) > 300:
                    truncated += "..."
                lines.append(f"")
                lines.append(f"<details>")
                lines.append(f"<summary><b>{juror}</b></summary>")
                lines.append(f"")
                lines.append(f"{statement[:1000]}")
                lines.append(f"")
                lines.append(f"</details>")

        if round_obj.challenges:
            lines.append(f"")
            lines.append(f"**质询记录:** {sum(len(v) for v in round_obj.challenges.values())} 条质询")
            lines.append(f"")

        lines.append(f"")

    return lines


def _build_verdict_section(verdict: JuryVerdict) -> list[str]:
    """Build the final verdict section with vote distribution."""
    lines: list[str] = []

    lines.append(f"## ⚖️ 最终裁决")
    lines.append(f"")

    # Vote distribution
    if "vote_distribution" in verdict.metadata:
        dist = verdict.metadata["vote_distribution"]
        lines.append(f"### 投票分布")
        lines.append(f"")
        lines.append(f"| 方案 | 得票数 | 占比 |")
        lines.append(f"|------|--------|------|")

        total = sum(dist.values()) if dist else 0
        for proposal, count in dist.items():
            pct = (count / total * 100) if total > 0 else 0
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            lines.append(f"| **{proposal}** | {count} | {bar} {pct:.0f}% |")

        lines.append(f"")

    # Individual ballots
    lines.append(f"### 评审员投票详情")
    lines.append(f"")

    for ballot in verdict.ballots:
        conf_bar = "🟩" * int(ballot.confidence * 10) + "⬜" * (10 - int(ballot.confidence * 10))
        lines.append(f"#### {ballot.voter} — 投票给 **{ballot.voted_for}**")
        lines.append(f"")
        lines.append(f"- **置信度:** {conf_bar} {ballot.confidence:.1%}")
        lines.append(f"- **关键理由:** {ballot.key_reason[:200]}")
        if ballot.against_reasons:
            lines.append(f"- **反对其他方案:**")
            for reason in ballot.against_reasons[:3]:
                lines.append(f"  - {reason[:150]}")
        if ballot.suspected_deception:
            lines.append(f"- ⚠️ **疑似欺骗标记:** {ballot.suspected_deception_detail[:200]}")
        lines.append(f"")

    return lines


def _build_risk_section(
    verdict: JuryVerdict,
    risk_score: float,
    risk_level: Optional[RiskLevel],
) -> list[str]:
    """Build the risk assessment section."""
    lines: list[str] = []

    lines.append(f"## 🔍 风险评估")
    lines.append(f"")

    # Risk gauge
    gauge = _build_risk_gauge(risk_score)
    lines.append(f"### 风险仪表盘")
    lines.append(f"")
    lines.append(f"```")
    lines.append(f"{gauge}")
    lines.append(f"```")
    lines.append(f"")

    # Risk breakdown
    lines.append(f"### 风险因子分析")
    lines.append(f"")

    # Decision risk
    dec = verdict.decision.value if hasattr(verdict.decision, 'value') else str(verdict.decision)
    decision_risk_map = {
        "consensus": ("低", "评审员高度一致，方案可靠"),
        "majority": ("中", "多数评审员达成一致，存在少数分歧"),
        "split": ("高", "评审员意见严重分歧，方案可信度下降"),
        "deadlock": ("极高", "评审员完全僵持，无法做出可靠决策"),
    }
    dr_level, dr_note = decision_risk_map.get(dec, ("未知", ""))
    lines.append(f"- **共识风险:** {dr_level} — {dr_note}")

    # Confidence risk
    if verdict.confidence >= 0.8:
        conf_note = "评审员总体信心充足"
    elif verdict.confidence >= 0.6:
        conf_note = "评审员信心中等，建议核实关键事实"
    else:
        conf_note = "评审员信心不足，强烈建议人工复核"
    lines.append(f"- **置信度风险:** 置信度 {verdict.confidence:.1%} — {conf_note}")

    # Deception risk
    deception_count = len(verdict.deception_flags)
    if deception_count == 0:
        dec_note = "未检测到欺骗信号"
    elif deception_count <= 2:
        dec_note = f"检测到 {deception_count} 个疑似欺骗信号，建议关注"
    else:
        dec_note = f"⚠️ 检测到 {deception_count} 个欺骗信号，方案可能存在误导"
    lines.append(f"- **欺骗风险:** {dec_note}")

    # Deception details
    if verdict.deception_flags:
        lines.append(f"")
        lines.append(f"#### 欺骗标记详情")
        for flag in verdict.deception_flags[:5]:
            lines.append(f"- {flag[:300]}")
    lines.append(f"")

    return lines


def _build_minority_section(verdict: JuryVerdict) -> list[str]:
    """Build the minority report section."""
    lines: list[str] = []

    if not verdict.minority_report:
        return lines

    lines.append(f"## 🗣️ 少数派意见")
    lines.append(f"")
    lines.append(f"{verdict.minority_report}")
    lines.append(f"")

    return lines


def _build_recommendation_section(verdict: JuryVerdict) -> list[str]:
    """Build the final recommendation section."""
    lines: list[str] = []

    lines.append(f"## 💡 建议与后续步骤")
    lines.append(f"")

    if verdict.recommendation:
        lines.append(f"{verdict.recommendation}")
        lines.append(f"")

    # Next steps based on decision type
    dec = verdict.decision.value if hasattr(verdict.decision, 'value') else str(verdict.decision)

    lines.append(f"### 建议操作")
    lines.append(f"")

    steps = {
        "consensus": [
            "✅ 直接采纳胜出方案",
            "📋 将方案转化为具体的执行计划",
            "📊 设定关键绩效指标（KPI）追踪效果",
        ],
        "majority": [
            "👍 采纳多数派方案，但需关注少数意见",
            "🔍 核实少数派提出的关键异议点",
            "📋 制定方案 + 备选方案的执行计划",
        ],
        "split": [
            "⚠️ 不建议直接执行，需进一步调研",
            "🔬 组织第二轮专题论证",
            "👥 可引入外部专家意见",
            "📊 考虑混合方案（取各方案优点）",
        ],
        "deadlock": [
            "❌ 当前评审无法做出决策",
            "🔄 重新定义问题范围",
            "👥 扩大评审团规模或引入新视角",
            "🔬 进行实验性验证（A/B测试等）",
        ],
    }

    for step in steps.get(dec, ["请人工判断后续步骤"]):
        lines.append(f"1. {step}")

    lines.append(f"")

    return lines


# ════════════════════════════════════════════════════════════════════════
# Helper Functions
# ════════════════════════════════════════════════════════════════════════

def _label(value: str) -> str:
    """Human-readable label for enum values."""
    labels = {
        "consensus": "共识达成",
        "majority": "多数通过",
        "split": "意见分歧",
        "deadlock": "评审僵局",
    }
    return labels.get(value, value)


def _build_risk_gauge(risk_score: float) -> str:
    """Build an ASCII risk gauge visualization."""
    normalized = max(0.0, min(100.0, risk_score))
    filled = int(normalized / 5)  # 20 segments

    if normalized < 25:
        color = "GREEN"
    elif normalized < 50:
        color = "YELLOW"
    elif normalized < 75:
        color = "ORANGE"
    else:
        color = "RED"

    bar = "█" * filled + "░" * (20 - filled)

    return (
        f"风险评分: {normalized:.1f}/100  [{color}]\n"
        f"0%  [{bar}]  100%\n"
        f"   Low          Medium        High       Critical"
    )


# ════════════════════════════════════════════════════════════════════════
# Quick Text Summary (for non-markdown contexts)
# ════════════════════════════════════════════════════════════════════════

def generate_verdict_summary(
    verdict: JuryVerdict,
    risk_score: float = 0.0,
) -> str:
    """Generate a concise plain-text summary of the verdict.

    Useful for messaging platforms that don't support Markdown.
    """
    dec = verdict.decision.value if hasattr(verdict.decision, 'value') else str(verdict.decision)
    dec_label = _label(dec)

    lines = [
        f"【多模型评审结果】",
        f"",
        f"决策: {dec_label} | 胜出方案: {verdict.winner}",
        f"置信度: {verdict.confidence:.1%} | 风险: {risk_score:.0f}/100",
        f"",
    ]

    if verdict.recommendation:
        lines.append(f"建议: {verdict.recommendation[:300]}")

    if verdict.deception_flags:
        lines.append(f"")
        lines.append(f"⚠️ 检测到 {len(verdict.deception_flags)} 个欺骗信号")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════
# Exports
# ════════════════════════════════════════════════════════════════════════

__all__ = [
    "generate_verdict_report",
    "generate_verdict_summary",
]
