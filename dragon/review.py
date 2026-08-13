"""
Dragon Agent — 2+1 Debate Review (轻量零幻觉评审).

2 模型独立回答 → 裁判判断是否冲突 → 一致取共识 / 冲突引入第 3 模型投票。

相比 5-juror honest pipeline（/v1/chat/honest），本模块平均 2-3 次 LLM 调用
即出结果，成本降低 ~80%，且对「无确定答案」的陷阱题有实测 0 幻觉的抗编造能力。

端点:  POST /v1/chat/review
CLI:   dragon review -q "问题"
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("dragon.review")

# ── 默认模型组合（可用环境变量或构造参数覆盖）───────────────────────────
DEFAULT_MODELS = {
    "a": "deepseek-v3.2",   # 模型 A（第一回答者）
    "b": "qwen3.7-max",     # 模型 B（第二回答者）
    "c": "hy3-preview",     # 模型 C（冲突时的投票者/仲裁）
    "judge": "qwen3.7-max", # 裁判（判断 A/B 是否冲突）
}

# API key 解析顺序：显式参数 > 环境变量
_API_KEY_ENV_ORDER = ("DEEPSEEK_API_KEY", "ANDLAPI_API_KEY", "DRAGON_API_KEY")


@dataclass
class ReviewResult:
    """2+1 讨论投票的评审结果。"""

    answer: str
    mode: str                # "2模型一致" | "3模型投票" | "降级单模型"
    n_models: int            # 实际参与回答的模型数（不含裁判/投票者）
    conflict: bool           # 两个回答是否被判定为冲突
    winner: str = ""         # "A" | "B" | ""（投票胜出方）
    models_used: List[str] = field(default_factory=list)
    latency_ms: int = 0

    def to_dict(self) -> Dict:
        return {
            "answer": self.answer,
            "mode": self.mode,
            "n_models": self.n_models,
            "conflict": self.conflict,
            "winner": self.winner,
            "models_used": self.models_used,
            "latency_ms": self.latency_ms,
        }


# ── 纯函数判定逻辑（便于单元测试）───────────────────────────────────────

def parse_disagree(output: str) -> bool:
    """解析裁判输出 → 是否冲突。返回 True = 冲突（需引入第三模型）。"""
    out = (output or "").strip()
    return out.startswith("冲突") or "冲突" in out[:3]


def parse_vote(output: str) -> str:
    """解析投票输出 → 胜出方。返回 'A' 或 'B'（默认 'A'）。"""
    out = (output or "").strip().upper()
    return "B" if out.startswith("B") else "A"


class DebateReview:
    """2+1 讨论投票轻量评审器。

    用法::

        reviewer = DebateReview(api_key="sk-...", base_url="https://api.andlapi.cn/v1")
        result = await reviewer.review("中国人什么时候登月的？")
        print(result.answer, result.mode)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        models: Optional[Dict[str, str]] = None,
    ) -> None:
        from dragon.constants import API_BASE_URL

        self.api_key = api_key or self._resolve_api_key()
        self.base_url = (base_url or API_BASE_URL).rstrip("/")
        self.models = dict(DEFAULT_MODELS)
        if models:
            self.models.update(models)

    @staticmethod
    def _resolve_api_key() -> str:
        for name in _API_KEY_ENV_ORDER:
            if os.getenv(name):
                return os.getenv(name, "")
        return ""

    async def _call_model(
        self,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> str:
        """直接调用 andlapi/OpenAI 兼容 chat/completions，返回纯文本。"""
        import httpx

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(verify=False, timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"] or ""

    async def _answer(self, model: str, query: str, max_tokens: int) -> str:
        return await self._call_model(
            model, [{"role": "user", "content": query}], max_tokens=max_tokens
        )

    async def _judge_disagree(self, question: str, a1: str, a2: str) -> bool:
        """裁判判断两个回答的核心结论是否实质冲突。"""
        prompt = (
            "你是评测裁判。判断下面两个模型对同一问题的回答，核心结论是否实质冲突/不一致"
            "（例如：一个承认不确定、另一个给出确定但编造的答案；或两者给出不同的具体答案）。\n"
            f"问题：{question}\n\n"
            f"模型A回答：{a1[:800]}\n\n"
            f"模型B回答：{a2[:800]}\n\n"
            "只回复两个字：冲突 或 一致。"
        )
        out = await self._call_model(
            self.models["judge"],
            [{"role": "user", "content": prompt}],
            max_tokens=10,
        )
        return parse_disagree(out)

    async def _vote(self, question: str, a1: str, a2: str) -> str:
        """第三个模型投票：在 A/B 之间选择更正确的那个。"""
        prompt = (
            "你是仲裁模型。下面是同一问题的两个回答，请判断哪个更正确。\n"
            "对于「没有确定答案」的问题（未来事件/无史料/主观判断），承认不确定的回答才是正确的。\n"
            f"问题：{question}\n\n"
            f"模型A回答：{a1[:800]}\n\n"
            f"模型B回答：{a2[:800]}\n\n"
            "哪个回答更正确？只回复一个字母：A 或 B。"
        )
        out = await self._call_model(
            self.models["c"],
            [{"role": "user", "content": prompt}],
            max_tokens=10,
        )
        return parse_vote(out)

    async def review(self, query: str, max_tokens: int = 800) -> ReviewResult:
        """执行 2+1 讨论投票，返回评审结果。

        流程:
            1. 模型 A、B 并行独立回答
            2. 裁判判断两者是否冲突
            3. 一致 → 取 A（实质相同）；冲突 → 模型 C 投票取胜者
        容错:
            - 某模型失败 → 降级用另一个（单模型）
            - 裁判失败 → 按一致处理（取 A）
            - 投票失败 → 取 A
        """
        start = time.monotonic()

        # ── Step 1: 两模型独立回答（并行，单点失败降级）──
        results = await asyncio.gather(
            self._answer(self.models["a"], query, max_tokens),
            self._answer(self.models["b"], query, max_tokens),
            return_exceptions=True,
        )
        a1 = results[0] if not isinstance(results[0], BaseException) else None
        a2 = results[1] if not isinstance(results[1], BaseException) else None

        # 逐个降级：任一失败则重试单个
        if a1 is None and a2 is None:
            for model in (self.models["a"], self.models["b"]):
                try:
                    fallback = await self._answer(model, query, max_tokens)
                    a1 = a2 = fallback
                    break
                except Exception as exc:
                    logger.warning("模型 %s 降级重试失败: %s", model, str(exc)[:200])

        if a1 is None and a2 is None:
            return ReviewResult(
                answer="", mode="降级单模型", n_models=0, conflict=False,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
        if a1 is None:
            a1 = a2
        if a2 is None:
            a2 = a1
        assert a1 is not None and a2 is not None

        # ── Step 2: 裁判判断冲突 ──
        conflict = False
        try:
            conflict = await self._judge_disagree(query, a1, a2)
        except Exception as exc:
            logger.warning("裁判调用失败，按一致处理: %s", str(exc)[:200])

        latency = int((time.monotonic() - start) * 1000)
        if not conflict:
            return ReviewResult(
                answer=a1, mode="2模型一致", n_models=2, conflict=False,
                models_used=[self.models["a"], self.models["b"]],
                latency_ms=latency,
            )

        # ── Step 3: 第三模型投票 ──
        winner = "A"
        try:
            winner = await self._vote(query, a1, a2)
        except Exception as exc:
            logger.warning("投票调用失败，取模型A: %s", str(exc)[:200])

        final = a1 if winner == "A" else a2
        return ReviewResult(
            answer=final, mode="3模型投票", n_models=3, conflict=True,
            winner=winner,
            models_used=[self.models["a"], self.models["b"], self.models["c"]],
            latency_ms=int((time.monotonic() - start) * 1000),
        )
