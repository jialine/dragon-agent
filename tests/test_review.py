"""
Unit tests for dragon.review — 2+1 Debate Review (轻量零幻觉评审).

Pure unit tests: no LLM calls, no network. All model calls are mocked.

Covers:
  - ReviewResult dataclass + to_dict
  - parse_disagree / parse_vote pure functions
  - DebateReview constructor (defaults, custom models, api_key resolution)
  - review() agree path / conflict path / degraded path / judge-failure fallback
  - review() single-model fallback (one model fails, the other succeeds)
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from dragon.review import (
    DEFAULT_MODELS,
    DebateReview,
    ReviewResult,
    parse_disagree,
    parse_vote,
)


def _make_reviewer(**kwargs):
    """Build a DebateReview with a fixed key + base_url (no env/network)."""
    return DebateReview(
        api_key=kwargs.get("api_key", "test-key"),
        base_url=kwargs.get("base_url", "https://example.com/v1"),
        models=kwargs.get("models"),
    )


# ═══════════════════════════════════════════════════════════════════════
# ReviewResult
# ═══════════════════════════════════════════════════════════════════════

class TestReviewResult:
    def test_create_with_required_fields(self):
        r = ReviewResult(answer="答案", mode="2模型一致", n_models=2, conflict=False)
        assert r.answer == "答案"
        assert r.mode == "2模型一致"
        assert r.n_models == 2
        assert r.conflict is False

    def test_defaults(self):
        r = ReviewResult(answer="", mode="降级单模型", n_models=0, conflict=False)
        assert r.winner == ""
        assert r.models_used == []
        assert r.latency_ms == 0

    def test_to_dict(self):
        r = ReviewResult(
            answer="x", mode="3模型投票", n_models=3, conflict=True,
            winner="B", models_used=["a", "b", "c"], latency_ms=42,
        )
        d = r.to_dict()
        assert d["mode"] == "3模型投票"
        assert d["n_models"] == 3
        assert d["winner"] == "B"
        assert d["models_used"] == ["a", "b", "c"]
        assert d["latency_ms"] == 42


# ═══════════════════════════════════════════════════════════════════════
# parse_disagree / parse_vote
# ═══════════════════════════════════════════════════════════════════════

class TestParseDisagree:
    def test_conflict(self):
        assert parse_disagree("冲突") is True
        assert parse_disagree("冲突，两者答案不同") is True
        assert parse_disagree("冲突。") is True

    def test_agree(self):
        assert parse_disagree("一致") is False
        assert parse_disagree("一致，两者结论相同") is False

    def test_empty_and_noise(self):
        assert parse_disagree("") is False
        assert parse_disagree("   ") is False
        assert parse_disagree("无法判断") is False  # 非"冲突"开头 → 一致


class TestParseVote:
    def test_a(self):
        assert parse_vote("A") == "A"
        assert parse_vote("a") == "A"
        assert parse_vote("A 更正确") == "A"

    def test_b(self):
        assert parse_vote("B") == "B"
        assert parse_vote("b") == "B"
        assert parse_vote("B 更正确") == "B"

    def test_default_to_a(self):
        assert parse_vote("") == "A"
        assert parse_vote("   ") == "A"
        assert parse_vote("两个都不对") == "A"


# ═══════════════════════════════════════════════════════════════════════
# DebateReview constructor
# ═══════════════════════════════════════════════════════════════════════

class TestConstructor:
    def test_default_models(self):
        reviewer = _make_reviewer()
        assert reviewer.models == DEFAULT_MODELS
        assert reviewer.models["a"] == "deepseek-v3.2"
        assert reviewer.models["b"] == "qwen3.7-max"
        assert reviewer.models["c"] == "hy3-preview"
        assert reviewer.models["judge"] == "qwen3.7-max"

    def test_custom_models_merge(self):
        reviewer = _make_reviewer(models={"a": "custom-a", "judge": "custom-j"})
        assert reviewer.models["a"] == "custom-a"
        assert reviewer.models["judge"] == "custom-j"
        # 未覆盖的保持默认
        assert reviewer.models["b"] == DEFAULT_MODELS["b"]
        assert reviewer.models["c"] == DEFAULT_MODELS["c"]

    def test_explicit_api_key_wins_over_env(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=True):
            reviewer = _make_reviewer(api_key="sk-explicit")
            assert reviewer.api_key == "sk-explicit"

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}, clear=True):
            reviewer = DebateReview(base_url="https://example.com/v1")
            assert reviewer.api_key == "sk-env"

    def test_api_key_env_fallback_order(self):
        # ANDLAPI_API_KEY 在 DEEPSEEK_API_KEY 缺失时生效
        env = {"DEEPSEEK_API_KEY": "", "ANDLAPI_API_KEY": "sk-andlapi", "DRAGON_API_KEY": "sk-dragon"}
        with patch.dict(os.environ, env, clear=True):
            reviewer = DebateReview(base_url="https://example.com/v1")
            assert reviewer.api_key == "sk-andlapi"

    def test_base_url_strips_trailing_slash(self):
        reviewer = DebateReview(api_key="k", base_url="https://example.com/v1/")
        assert reviewer.base_url == "https://example.com/v1"


# ═══════════════════════════════════════════════════════════════════════
# review() paths (async, mocked)
# ═══════════════════════════════════════════════════════════════════════

class TestReview:
    def test_agree_path(self):
        """两个回答一致 → 裁判判一致 → 取 A，仅 2 模型。"""
        reviewer = _make_reviewer()
        reviewer._answer = AsyncMock(side_effect=["答案一", "答案二"])
        reviewer._judge_disagree = AsyncMock(return_value=False)
        reviewer._vote = AsyncMock(return_value="A")

        result = asyncio.run(reviewer.review("问题"))

        assert result.mode == "2模型一致"
        assert result.n_models == 2
        assert result.conflict is False
        assert result.winner == ""
        assert result.answer == "答案一"
        assert result.models_used == [DEFAULT_MODELS["a"], DEFAULT_MODELS["b"]]
        reviewer._vote.assert_not_awaited()  # 一致路径不应触发投票

    def test_conflict_path_vote_b(self):
        """冲突 → 第三模型投 B → 取模型 B 的回答。"""
        reviewer = _make_reviewer()
        reviewer._answer = AsyncMock(side_effect=["答案一", "答案二"])
        reviewer._judge_disagree = AsyncMock(return_value=True)
        reviewer._vote = AsyncMock(return_value="B")

        result = asyncio.run(reviewer.review("问题"))

        assert result.mode == "3模型投票"
        assert result.n_models == 3
        assert result.conflict is True
        assert result.winner == "B"
        assert result.answer == "答案二"  # winner B → a2
        assert result.models_used == [DEFAULT_MODELS["a"], DEFAULT_MODELS["b"], DEFAULT_MODELS["c"]]

    def test_conflict_path_vote_a(self):
        """冲突 → 第三模型投 A → 取模型 A 的回答。"""
        reviewer = _make_reviewer()
        reviewer._answer = AsyncMock(side_effect=["答案一", "答案二"])
        reviewer._judge_disagree = AsyncMock(return_value=True)
        reviewer._vote = AsyncMock(return_value="A")

        result = asyncio.run(reviewer.review("问题"))

        assert result.mode == "3模型投票"
        assert result.winner == "A"
        assert result.answer == "答案一"

    def test_judge_failure_falls_back_to_agree(self):
        """裁判调用失败 → 按一致处理（取 A）。"""
        reviewer = _make_reviewer()
        reviewer._answer = AsyncMock(side_effect=["答案一", "答案二"])
        reviewer._judge_disagree = AsyncMock(side_effect=Exception("judge down"))

        result = asyncio.run(reviewer.review("问题"))

        assert result.mode == "2模型一致"
        assert result.conflict is False
        assert result.answer == "答案一"

    def test_single_model_fallback(self):
        """模型 A 失败、模型 B 成功 → 降级用 B 的回答。"""
        reviewer = _make_reviewer()
        reviewer._answer = AsyncMock(side_effect=[Exception("A down"), "答案B"])
        reviewer._judge_disagree = AsyncMock(return_value=False)

        result = asyncio.run(reviewer.review("问题"))

        assert result.mode == "2模型一致"
        assert result.n_models == 2
        assert result.answer == "答案B"

    def test_both_models_fail_degraded(self):
        """双模型都失败（含重试）→ 降级单模型，空回答。"""
        reviewer = _make_reviewer()
        # gather 2 次 + 降级重试 2 次 = 4 次调用，全部失败
        reviewer._answer = AsyncMock(
            side_effect=[Exception("a1"), Exception("b1"), Exception("a2"), Exception("b2")]
        )

        result = asyncio.run(reviewer.review("问题"))

        assert result.mode == "降级单模型"
        assert result.n_models == 0
        assert result.answer == ""
        assert result.conflict is False
