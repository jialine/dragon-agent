"""
Tier Classifier — rule-based intent classification for 3-tier routing.

Tiers:
  1 (simple): greetings, facts, calculations, translations, single short questions
  2 (medium): code writing, explanations, comparisons, how-to, technical questions
  3 (complex): design, architecture, analysis, planning, multi-sentence briefs
"""

import re
from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    SIMPLE = 1
    MEDIUM = 2
    COMPLEX = 3


@dataclass
class Classification:
    tier: Tier
    confidence: float  # 0.0-1.0
    reason: str


# ── Tier 1 patterns (simple) ──────────────────────────────────────

T1_GREETINGS = {
    "你好", "hi", "hello", "hey", "嗨", "在吗", "在不", "早", "晚安",
    "早上好", "下午好", "晚上好", "再见", "拜拜", "谢谢", "thanks",
    "thank you", "好的", "ok", "okay", "嗯", "哦",
}

T1_FACT_PATTERNS = [
    r"^(什么|啥|什么是|什么是)\S{1,10}\?*$",      # "什么是XX"
    r"^(谁|谁是)\S{1,10}\?*$",                      # "谁是XX"
    r"^(几|多少|几点|什么时间|什么时候)",            # "几点/什么时候"
    r"^[0-9\+\-\*\/\(\)\s]+[=＝]?\s*\??$",          # 纯算式 "1+1=?"
    r"^[a-zA-Z\s]{1,30}[?？]$",                      # 短英文问句
    r"^(翻译|translate)\s",                          # 翻译
    r"^(今天|明天|昨天|星期|周)",                    # 日期
    r"^(天气|温度)",                                  # 天气
]

T1_MAX_LENGTH = 30  # chars — very short queries are likely simple

# ── Tier 3 patterns (complex) ─────────────────────────────────────

T3_TRIGGERS = [
    r"设计", r"架构", r"方案", r"规划",
    r"分析一下", r"深入分析", r"全面",
    r"帮我评估", r"帮我设计", r"帮我规划",
    r"制定.*方案", r"制定.*计划",
    r"对比.*优劣", r"优缺点",
    r"多维度", r"系统性",
    r"帮我写一个完整的", r"实现一个",
    r"优化.*架构", r"重构",
    r"review|review.*code|code.*review",
    r"审计", r"排查",
]

T3_MIN_LENGTH = 80  # chars — long queries are likely complex

# ── Tier 2 patterns (medium) ──────────────────────────────────────

T2_TRIGGERS = [
    r"怎么写", r"怎么实现", r"如何",
    r"代码", r"编程", r"函数", r"python", r"javascript",
    r"bug", r"报错", r"错误", r"调试",
    r"区别", r"对比", r"vs", r"比较",
    r"推荐", r"建议",
    r"配置", r"部署", r"安装",
    r"解释", r"说明", r"原理",
    r"shell", r"bash", r"linux", r"git",
    r"api", r"接口", r"http",
]


def classify(text: str) -> Classification:
    """Classify user input into one of three tiers."""
    text_stripped = text.strip()
    text_lower = text_stripped.lower()
    text_len = len(text_stripped)

    # ── Tier 3 check (complex) ──
    t3_score = 0
    for pattern in T3_TRIGGERS:
        if re.search(pattern, text_lower):
            t3_score += 1
    if text_len >= T3_MIN_LENGTH:
        t3_score += 1
    if t3_score >= 2:
        return Classification(Tier.COMPLEX, min(t3_score / 3, 1.0),
                              f"complex triggers: {t3_score}, length: {text_len}")

    # ── Tier 1 check (simple) ──
    if text_stripped in T1_GREETINGS or text_lower in T1_GREETINGS:
        return Classification(Tier.SIMPLE, 1.0, "greeting match")

    if text_len <= T1_MAX_LENGTH:
        for pattern in T1_FACT_PATTERNS:
            if re.match(pattern, text_stripped):
                return Classification(Tier.SIMPLE, 0.9,
                                      f"fact pattern: {pattern[:30]}...")

    # Catch very short queries
    if text_len <= 8 and "?" not in text_stripped and "?" not in text_stripped:
        return Classification(Tier.SIMPLE, 0.7, f"very short: {text_len} chars")

    # ── Tier 3 check (single strong trigger for long text) ──
    if t3_score >= 1 and text_len >= 50:
        return Classification(Tier.COMPLEX, 0.75,
                              f"complex trigger + length: {text_len}")

    # ── Tier 2 check (medium) ──
    for pattern in T2_TRIGGERS:
        if re.search(pattern, text_lower):
            return Classification(Tier.MEDIUM, 0.8,
                                  f"medium trigger: {pattern[:30]}...")

    # ── Default: medium ──
    if text_len > 30:
        return Classification(Tier.MEDIUM, 0.6,
                              f"default medium, length: {text_len}")
    return Classification(Tier.SIMPLE, 0.5, f"default simple, length: {text_len}")
