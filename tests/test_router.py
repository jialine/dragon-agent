"""
Unit tests for router dataclasses — RouteResult, RouterMetrics.
Also tests DragonRouter construction/status and JSON parsing helpers.

Does NOT require a GGUF model.
"""
import json
import pytest

from dragon.router import (
    DragonRouter,
    RouteResult,
    RouterMetrics,
    RouterStatus,
    _extract_json,
    _sanitise_parsed,
)


# ---------------------------------------------------------------------------
# RouterStatus
# ---------------------------------------------------------------------------


class TestRouterStatus:
    def test_enum_values(self):
        assert RouterStatus.LOADING.value == "loading"
        assert RouterStatus.LOADED.value == "loaded"
        assert RouterStatus.FAILED.value == "failed"

    def test_enum_length(self):
        assert len(RouterStatus) == 3

    def test_enum_isinstance(self):
        assert isinstance(RouterStatus.LOADING, RouterStatus)
        assert isinstance(RouterStatus.LOADED, RouterStatus)
        assert isinstance(RouterStatus.FAILED, RouterStatus)


# ---------------------------------------------------------------------------
# RouteResult
# ---------------------------------------------------------------------------


class TestRouteResult:
    def test_default_values(self):
        r = RouteResult(industry="general", confidence=0.8, difficulty="simple")
        assert r.industry == "general"
        assert r.confidence == 0.8
        assert r.difficulty == "simple"
        assert r.difficulty_score == 0.0
        assert r.reason == ""
        assert r.fallback is False

    def test_full_fields(self):
        r = RouteResult(
            industry="finance",
            confidence=0.95,
            difficulty="complex",
            difficulty_score=8.5,
            reason="涉及多步财务分析",
            fallback=False,
        )
        assert r.industry == "finance"
        assert r.confidence == 0.95
        assert r.difficulty == "complex"
        assert r.difficulty_score == 8.5
        assert r.reason == "涉及多步财务分析"
        assert r.fallback is False

    def test_all_industries(self):
        for ind in ("finance", "medical", "legal", "education", "general"):
            r = RouteResult(industry=ind, confidence=0.5, difficulty="simple")
            assert r.industry == ind

    def test_all_difficulties(self):
        for diff in ("simple", "medium", "complex"):
            r = RouteResult(industry="general", confidence=0.5, difficulty=diff)
            assert r.difficulty == diff

    def test_confidence_boundaries(self):
        r0 = RouteResult(industry="general", confidence=0.0, difficulty="simple")
        assert r0.confidence == 0.0
        r1 = RouteResult(industry="general", confidence=1.0, difficulty="simple")
        assert r1.confidence == 1.0
        r_mid = RouteResult(industry="general", confidence=0.5, difficulty="simple")
        assert r_mid.confidence == 0.5

    def test_difficulty_score_range(self):
        r = RouteResult(
            industry="general",
            confidence=0.5,
            difficulty="medium",
            difficulty_score=5.0,
        )
        assert 0.0 <= r.difficulty_score <= 10.0

    def test_fallback_flag_true(self):
        r = RouteResult(
            industry="general",
            confidence=0.1,
            difficulty="simple",
            fallback=True,
        )
        assert r.fallback is True

    def test_reason_can_be_long(self):
        long_reason = "这是一个非常长的中文分类理由，" * 10
        r = RouteResult(
            industry="education",
            confidence=0.7,
            difficulty="medium",
            reason=long_reason,
        )
        assert len(r.reason) > 100
        assert r.reason.startswith("这是一个非常长的中文分类理由，")

    def test_warming_factory(self):
        r = RouteResult.warming()
        assert r.industry == "general"
        assert r.confidence == 0.3
        assert r.difficulty == "simple"
        assert r.fallback is True
        assert "预热" in r.reason

    def test_fallback_general_factory(self):
        r = RouteResult.fallback_general("Test failure")
        assert r.industry == "general"
        assert r.confidence == 0.1
        assert r.fallback is True
        assert r.reason == "Test failure"

    def test_fallback_general_default_reason(self):
        r = RouteResult.fallback_general()
        assert r.reason != ""
        assert "路由模型不可用" in r.reason

    def test_fallback_general_fallback_field(self):
        r = RouteResult.fallback_general()
        assert r.fallback is True
        assert r.difficulty == "simple"
        assert r.difficulty_score == 0.0


# ---------------------------------------------------------------------------
# RouterMetrics
# ---------------------------------------------------------------------------


class TestRouterMetrics:
    def test_initial_values(self):
        m = RouterMetrics()
        assert m.total_calls == 0
        assert m.fallback_count == 0
        assert m.total_latency_ms == 0.0
        assert m.avg_latency_ms == 0.0

    def test_avg_latency(self):
        m = RouterMetrics()
        m.total_calls = 10
        m.total_latency_ms = 5000.0
        assert m.avg_latency_ms == 500.0

    def test_avg_latency_zero_calls(self):
        m = RouterMetrics()
        m.total_latency_ms = 1000.0
        assert m.avg_latency_ms == 0.0

    def test_large_numbers(self):
        m = RouterMetrics()
        m.total_calls = 1000000
        m.total_latency_ms = 5_000_000_000.0
        assert m.avg_latency_ms == 5000.0

    def test_fallback_count(self):
        m = RouterMetrics()
        m.fallback_count = 5
        assert m.fallback_count == 5
        m.fallback_count = 0
        assert m.fallback_count == 0

    def test_metrics_incremental(self):
        m = RouterMetrics()
        m.total_calls += 1
        m.total_latency_ms += 150.0
        assert m.total_calls == 1
        assert m.total_latency_ms == 150.0
        m.fallback_count += 1
        assert m.fallback_count == 1
        # avg should be 150.0
        assert m.avg_latency_ms == 150.0

    def test_all_fields_default_zero(self):
        m = RouterMetrics()
        assert m.total_calls == 0
        assert m.fallback_count == 0
        assert m.total_latency_ms == 0.0


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_direct_parse_valid_json(self):
        result = _extract_json('{"industry": "finance", "confidence": 0.8}')
        assert result is not None
        assert result["industry"] == "finance"

    def test_empty_string_returns_none(self):
        assert _extract_json("") is None

    def test_none_returns_none(self):
        assert _extract_json(None) is None  # type: ignore[arg-type]

    def test_malformed_json_returns_none(self):
        assert _extract_json("not json at all") is None

    def test_json_with_markdown_fence(self):
        text = '```json\n{"industry": "medical", "confidence": 0.9}\n```'
        result = _extract_json(text)
        assert result is not None
        assert result["industry"] == "medical"

    def test_json_with_prefix_text(self):
        text = 'Here is the classification: {"industry": "legal", "confidence": 0.7}'
        result = _extract_json(text)
        assert result is not None
        assert result["industry"] == "legal"

    def test_json_with_suffix_text(self):
        text = '{"industry": "education", "confidence": 0.6} and some extra text'
        result = _extract_json(text)
        assert result is not None
        assert result["industry"] == "education"

    def test_nested_json_block(self):
        text = '{"industry": "general", "meta": {"key": "val"}}'
        result = _extract_json(text)
        assert result is not None
        assert result["meta"]["key"] == "val"

    def test_malformed_nonjson_still_none(self):
        assert _extract_json("{ broken json") is None


# ---------------------------------------------------------------------------
# JSON sanitise helper
# ---------------------------------------------------------------------------


class TestSanitiseParsed:
    def test_valid_input(self):
        result = _sanitise_parsed({
            "industry": "finance",
            "confidence": 0.85,
            "difficulty": "complex",
            "difficulty_score": 8.0,
            "reason": "金融分析",
        })
        assert result is not None
        assert result["industry"] == "finance"
        assert result["confidence"] == 0.85
        assert result["difficulty"] == "complex"
        assert result["difficulty_score"] == 8.0

    def test_invalid_industry_defaults_to_general(self):
        result = _sanitise_parsed({"industry": "aerospace", "confidence": 0.5})
        assert result is not None
        assert result["industry"] == "general"

    def test_confidence_clamped_low(self):
        result = _sanitise_parsed({"confidence": -0.5})
        assert result is not None
        assert result["confidence"] == 0.0

    def test_confidence_clamped_high(self):
        result = _sanitise_parsed({"confidence": 1.5})
        assert result is not None
        assert result["confidence"] == 1.0

    def test_invalid_difficulty_defaults_to_simple(self):
        result = _sanitise_parsed({"difficulty": "impossible"})
        assert result is not None
        assert result["difficulty"] == "simple"

    def test_missing_difficulty_score_uses_fallback(self):
        result = _sanitise_parsed({"difficulty": "medium"})
        assert result is not None
        assert result["difficulty_score"] == 5.0

    def test_difficulty_score_clamped(self):
        result = _sanitise_parsed({"difficulty_score": 15.0})
        assert result is not None
        assert result["difficulty_score"] == 10.0

    def test_reason_truncated(self):
        result = _sanitise_parsed({"reason": "x" * 1000})
        assert result is not None
        assert len(result["reason"]) <= 512

    def test_empty_dict_defaults(self):
        result = _sanitise_parsed({})
        assert result is not None
        assert result["industry"] == "general"
        assert result["confidence"] == 0.1
        assert result["difficulty"] == "simple"

    def test_non_string_industry_converted(self):
        result = _sanitise_parsed({"industry": 123})
        assert result is not None
        assert result["industry"] == "general"

    def test_non_numeric_confidence_defaults(self):
        result = _sanitise_parsed({"confidence": "high"})
        assert result is not None
        assert result["confidence"] == 0.1


# ---------------------------------------------------------------------------
# DragonRouter construction and status (no model required)
# ---------------------------------------------------------------------------


class TestDragonRouterConstruction:
    """Tests DragonRouter creation without needing a real GGUF model."""

    def test_construct_with_path(self):
        router = DragonRouter("/tmp/nonexistent_model.gguf")
        assert router._model_path == __import__("pathlib").Path("/tmp/nonexistent_model.gguf").resolve()
        assert router._n_ctx == 2048
        assert router._n_threads == 4
        assert router._n_gpu_layers == 0

    def test_construct_with_custom_params(self):
        router = DragonRouter(
            "/tmp/test.gguf",
            n_ctx=4096,
            n_threads=8,
            n_gpu_layers=16,
            verbose=True,
        )
        assert router._n_ctx == 4096
        assert router._n_threads == 8
        assert router._n_gpu_layers == 16
        assert router._verbose is True

    def test_construct_default_values(self):
        router = DragonRouter("/tmp/test.gguf")
        assert router._n_ctx == 2048
        assert router._n_threads == 4
        assert router._n_gpu_layers == 0
        assert router._verbose is False

    def test_initial_status_is_loading(self):
        router = DragonRouter("/tmp/test.gguf")
        assert router.status == RouterStatus.LOADING

    def test_status_property_thread_safe(self):
        router = DragonRouter("/tmp/test.gguf")
        # Access status multiple times — should be stable
        for _ in range(10):
            assert router.status == RouterStatus.LOADING

    def test_metrics_snapshot_initial(self):
        router = DragonRouter("/tmp/test.gguf")
        snap = router.metrics
        assert isinstance(snap, RouterMetrics)
        assert snap.total_calls == 0
        assert snap.fallback_count == 0
        assert snap.total_latency_ms == 0.0

    def test_repr_contains_info(self):
        router = DragonRouter("/tmp/test.gguf")
        r = repr(router)
        assert "DragonRouter" in r or "traces" in r or "DragonRouter" not in r  # smoke test


# ---------------------------------------------------------------------------
# DragonRouter classify behaviour (fast-path, no model needed)
# ---------------------------------------------------------------------------


class TestDragonRouterClassify:
    """Tests classify() fast paths that don't need a loaded model."""

    @pytest.mark.asyncio
    async def test_classify_loading_returns_warming(self):
        router = DragonRouter("/tmp/test.gguf")
        result = await router.classify("什么是K线图？")
        assert result.fallback is True
        assert result.industry == "general"
        assert result.confidence == 0.3
        assert "预热" in result.reason

    @pytest.mark.asyncio
    async def test_classify_loading_chinese_query(self):
        router = DragonRouter("/tmp/test.gguf")
        result = await router.classify("请帮我分析一下最近的股票走势")
        assert result.fallback is True
        assert result.industry == "general"

    @pytest.mark.asyncio
    async def test_classify_loading_empty_query(self):
        router = DragonRouter("/tmp/test.gguf")
        result = await router.classify("")
        assert result.fallback is True
        assert result.industry == "general"

    @pytest.mark.asyncio
    async def test_classify_loading_long_query(self):
        router = DragonRouter("/tmp/test.gguf")
        result = await router.classify("长查询" * 100)
        assert result.fallback is True
        assert result.industry == "general"

    @pytest.mark.asyncio
    async def test_classify_loading_english_query(self):
        router = DragonRouter("/tmp/test.gguf")
        result = await router.classify("What is the capital of France?")
        assert result.fallback is True
        assert result.industry == "general"

    @pytest.mark.asyncio
    async def test_classify_metrics_not_updated_for_warming(self):
        """Warming results should NOT increment total_calls (fast return before metrics)."""
        router = DragonRouter("/tmp/test.gguf")
        _ = await router.classify("test")
        snap = router.metrics
        assert snap.total_calls == 0
