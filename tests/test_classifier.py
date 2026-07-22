"""
Unit tests for dragon.orchestrator.classifier and dragon.error_classifier.

Covers:
  - Tier classification (classify): Tier 1/2/3 routing
  - Classification dataclass, Tier enum
  - classify_api_error: all error categories and status codes
  - Extraction helpers (_extract_status_code, _extract_error_body, etc.)
  - Convenience functions (is_retryable, get_recovery_action, format_chinese_error)
  - ErrorCategory enum and ClassifiedError dataclass
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from dragon.orchestrator.classifier import (
    classify,
    Tier,
    Classification,
    T1_GREETINGS,
    T1_FACT_PATTERNS,
    T2_TRIGGERS,
    T3_TRIGGERS,
    T1_MAX_LENGTH,
    T3_MIN_LENGTH,
)
from dragon.error_classifier import (
    classify_api_error,
    is_retryable,
    get_recovery_action,
    format_chinese_error,
    ErrorCategory,
    ClassifiedError,
    _extract_status_code,
    _extract_error_body,
    _extract_error_code,
    _extract_message,
    _build_combined_message,
    _matches_any,
    _classify_by_error_code,
)


# ============================================================
# Tier enum and Classification
# ============================================================

class TestTier:
    """Tests for Tier enum."""

    def test_values(self):
        assert Tier.SIMPLE.value == 1
        assert Tier.MEDIUM.value == 2
        assert Tier.COMPLEX.value == 3


class TestClassification:
    """Tests for Classification dataclass."""

    def test_creation(self):
        c = Classification(tier=Tier.SIMPLE, confidence=0.9, reason="greeting")
        assert c.tier == Tier.SIMPLE
        assert c.confidence == 0.9
        assert c.reason == "greeting"


# ============================================================
# Tier 1 (simple) classification
# ============================================================

class TestClassifyTier1:
    """Tests for Tier 1 (simple) classification."""

    def test_greeting_chinese(self):
        result = classify("你好")
        assert result.tier == Tier.SIMPLE
        assert result.confidence == 1.0
        assert "greeting" in result.reason

    def test_greeting_english(self):
        result = classify("hello")
        assert result.tier == Tier.SIMPLE
        assert result.confidence == 1.0

    def test_thanks(self):
        result = classify("thanks")
        assert result.tier == Tier.SIMPLE

    def test_bye(self):
        result = classify("再见")
        assert result.tier == Tier.SIMPLE

    def test_fact_what_is(self):
        result = classify("什么是Python")
        assert result.tier == Tier.SIMPLE

    def test_fact_who_is(self):
        result = classify("谁是爱因斯坦")
        assert result.tier == Tier.SIMPLE

    def test_arithmetic(self):
        result = classify("1+1=?")
        assert result.tier == Tier.SIMPLE

    def test_translate(self):
        result = classify("翻译 hello")
        assert result.tier == Tier.SIMPLE

    def test_very_short_query(self):
        result = classify("嗯")
        assert result.tier == Tier.SIMPLE

    def test_short_english_question(self):
        result = classify("What is AI?")
        assert result.tier == Tier.SIMPLE


# ============================================================
# Tier 2 (medium) classification
# ============================================================

class TestClassifyTier2:
    """Tests for Tier 2 (medium) classification."""

    def test_how_to_write(self):
        result = classify("怎么写一个Python函数")
        assert result.tier == Tier.MEDIUM

    def test_bug_fix(self):
        result = classify("我的代码有个bug，报错了")
        assert result.tier == Tier.MEDIUM

    def test_comparison(self):
        result = classify("python和javascript的区别")
        assert result.tier == Tier.MEDIUM

    def test_recommendation(self):
        result = classify("推荐一个好用的数据库")
        assert result.tier == Tier.MEDIUM

    def test_configuration(self):
        result = classify("如何配置nginx")
        assert result.tier == Tier.MEDIUM

    def test_explanation(self):
        result = classify("解释一下HTTP协议的原理")
        assert result.tier == Tier.MEDIUM

    def test_git_question(self):
        result = classify("git如何回滚commit")
        assert result.tier == Tier.MEDIUM

    def test_default_medium_for_long_text(self):
        long_text = "a" * 40  # > 30 chars, no triggers
        result = classify(long_text)
        assert result.tier == Tier.MEDIUM


# ============================================================
# Tier 3 (complex) classification
# ============================================================

class TestClassifyTier3:
    """Tests for Tier 3 (complex) classification."""

    def test_design_task(self):
        text = "帮我设计一个微服务架构，需要支持高并发和弹性扩展"
        result = classify(text)
        assert result.tier == Tier.COMPLEX

    def test_architecture(self):
        text = "设计一个分布式系统的架构方案"
        result = classify(text)
        assert result.tier == Tier.COMPLEX

    def test_analysis(self):
        text = "深入分析一下当前系统的性能瓶颈，给出优化建议"
        result = classify(text)
        assert result.tier == Tier.COMPLEX

    def test_planning(self):
        text = "帮我规划一个项目的完整开发计划，包括技术选型、里程碑和风险评估"
        result = classify(text)
        assert result.tier == Tier.COMPLEX

    def test_compare_pros_cons(self):
        text = "对比一下Kubernetes和Docker Swarm的优劣"
        result = classify(text)
        assert result.tier == Tier.COMPLEX

    def test_long_text_with_complex_trigger(self):
        text = "重构" + "x" * 50
        result = classify(text)
        assert result.tier == Tier.COMPLEX

    def test_code_review(self):
        result = classify("code review: 帮我review这段代码")
        assert result.tier == Tier.COMPLEX

    def test_not_complex_with_short_text_single_trigger(self):
        # Single T3 trigger but short text (< 50 chars)
        result = classify("设计方案")
        # Should be simple (short) or medium, not complex
        assert result.tier != Tier.COMPLEX


# ============================================================
# Edge cases
# ============================================================

class TestClassifyEdgeCases:
    """Tests for classifier edge cases."""

    def test_empty_string(self):
        result = classify("")
        assert result.tier == Tier.SIMPLE

    def test_whitespace_only(self):
        result = classify("   ")
        assert result.tier == Tier.SIMPLE

    def test_default_simple_short(self):
        result = classify("abc")  # 3 chars, no triggers
        assert result.tier == Tier.SIMPLE

    def test_case_insensitive(self):
        result = classify("HELLO")
        assert result.tier == Tier.SIMPLE


# ============================================================
# ErrorCategory and ClassifiedError
# ============================================================

class TestErrorCategory:
    """Tests for ErrorCategory enum."""

    def test_values(self):
        assert ErrorCategory.retryable.value == "retryable"
        assert ErrorCategory.auth.value == "auth"
        assert ErrorCategory.billing.value == "billing"
        assert ErrorCategory.rate_limit.value == "rate_limit"
        assert ErrorCategory.timeout.value == "timeout"
        assert ErrorCategory.unknown.value == "unknown"


class TestClassifiedError:
    """Tests for ClassifiedError dataclass."""

    def test_defaults(self):
        ce = ClassifiedError()
        assert ce.category == ErrorCategory.unknown
        assert ce.retryable is True
        assert ce.should_rotate_credential is False
        assert ce.should_fallback is False
        assert ce.should_compress is False

    def test_full_creation(self):
        ce = ClassifiedError(
            category=ErrorCategory.billing,
            status_code=402,
            provider="openai",
            model="gpt-4o",
            message="Insufficient credits",
            raw_message="Error: insufficient credits",
            retryable=False,
            should_fallback=True,
            recovery_suggestion="请充值",
        )
        assert ce.category == ErrorCategory.billing
        assert ce.status_code == 402
        assert ce.provider == "openai"
        assert ce.model == "gpt-4o"
        assert ce.retryable is False
        assert ce.should_fallback is True


# ============================================================
# Extraction helpers — _extract_status_code
# ============================================================

class TestExtractStatusCode:
    """Tests for _extract_status_code()."""

    def test_from_status_code_attr(self):
        exc = Exception("test")
        exc.status_code = 429
        assert _extract_status_code(exc) == 429

    def test_from_status_attr(self):
        exc = Exception("test")
        exc.status = 500
        assert _extract_status_code(exc) == 500

    def test_from_http_status_attr(self):
        exc = Exception("test")
        exc.http_status = 503
        assert _extract_status_code(exc) == 503

    def test_from_response_object(self):
        exc = Exception("test")
        exc.response = MagicMock(status_code=404)
        assert _extract_status_code(exc) == 404

    def test_no_status_code(self):
        exc = Exception("plain error")
        assert _extract_status_code(exc) is None

    def test_int_input(self):
        assert _extract_status_code(429) == 429

    def test_invalid_range_ignored(self):
        exc = Exception("test")
        exc.status_code = 999
        assert _extract_status_code(exc) is None


# ============================================================
# Extraction helpers — _extract_error_body
# ============================================================

class TestExtractErrorBody:
    """Tests for _extract_error_body()."""

    def test_from_body_attr_dict(self):
        exc = Exception("test")
        exc.body = {"error": {"message": "Something went wrong"}}
        body = _extract_error_body(exc)
        assert body == {"error": {"message": "Something went wrong"}}

    def test_from_response_json(self):
        exc = Exception("test")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": "rate_limit"}}
        exc.response = mock_resp
        body = _extract_error_body(exc)
        assert body == {"error": {"code": "rate_limit"}}

    def test_from_response_text_json(self):
        exc = Exception("test")
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.text = '{"code": "timeout"}'
        exc.response = mock_resp
        body = _extract_error_body(exc)
        assert body == {"code": "timeout"}

    def test_from_exception_string_json(self):
        exc = Exception('{"error": {"type": "auth_error"}}')
        body = _extract_error_body(exc)
        assert body == {"error": {"type": "auth_error"}}

    def test_no_body(self):
        exc = Exception("plain text")
        assert _extract_error_body(exc) is None


# ============================================================
# Extraction helpers — _extract_error_code
# ============================================================

class TestExtractErrorCode:
    """Tests for _extract_error_code()."""

    def test_openai_style(self):
        body = {"error": {"code": "context_length_exceeded"}}
        assert _extract_error_code(body) == "context_length_exceeded"

    def test_openai_type(self):
        body = {"error": {"type": "invalid_request_error"}}
        assert _extract_error_code(body) == "invalid_request_error"

    def test_simple_code(self):
        body = {"code": "rate_limit_exceeded"}
        assert _extract_error_code(body) == "rate_limit_exceeded"

    def test_error_code_field(self):
        body = {"error_code": "AUTH_FAILED"}
        assert _extract_error_code(body) == "AUTH_FAILED"

    def test_none_body(self):
        assert _extract_error_code(None) is None

    def test_empty_body(self):
        assert _extract_error_code({}) is None


# ============================================================
# Helper functions
# ============================================================

class TestMatchesAny:
    """Tests for _matches_any()."""

    def test_match_found(self):
        assert _matches_any("Rate limit exceeded", ["rate limit", "quota"]) is True

    def test_no_match(self):
        assert _matches_any("All good", ["error", "fail"]) is False

    def test_case_insensitive(self):
        assert _matches_any("RATE LIMIT", ["rate limit"]) is True

    def test_partial_match(self):
        assert _matches_any("this has a timeout message", ["timeout"]) is True


class TestBuildCombinedMessage:
    """Tests for _build_combined_message()."""

    def test_string_only(self):
        result = _build_combined_message("Error message", None)
        assert "error message" in result

    def test_with_body_error_message(self):
        body = {"error": {"message": "Insufficient credits"}}
        result = _build_combined_message("Something failed", body)
        assert "insufficient credits" in result

    def test_with_body_simple_message(self):
        body = {"message": "Quota exceeded"}
        result = _build_combined_message("Error", body)
        assert "quota exceeded" in result


class TestClassifyByErrorCode:
    """Tests for _classify_by_error_code()."""

    def test_context_overflow(self):
        assert _classify_by_error_code("context_length_exceeded", None) == ErrorCategory.context_overflow

    def test_billing(self):
        assert _classify_by_error_code("insufficient_quota", None) == ErrorCategory.billing

    def test_rate_limit(self):
        assert _classify_by_error_code("rate_limit_exceeded", None) == ErrorCategory.rate_limit

    def test_auth(self):
        assert _classify_by_error_code("invalid_api_key", None) == ErrorCategory.auth

    def test_model_not_found(self):
        assert _classify_by_error_code("model_not_found", None) == ErrorCategory.model_not_found

    def test_unknown_code(self):
        assert _classify_by_error_code("unknown_error_code", None) is None


# ============================================================
# classify_api_error — status code based
# ============================================================

class TestClassifyApiErrorByStatusCode:
    """Tests for classify_api_error() with HTTP status codes."""

    def test_429_rate_limit(self):
        result = classify_api_error(
            Exception("Too many requests"),
            provider="openai", model="gpt-4o", status_code=429,
        )
        assert result.category == ErrorCategory.rate_limit
        assert result.status_code == 429

    def test_429_with_billing_message(self):
        result = classify_api_error(
            Exception("insufficient_quota: you have exceeded your quota"),
            status_code=429,
        )
        assert result.category == ErrorCategory.billing
        assert result.should_fallback is True

    def test_400_format_error(self):
        result = classify_api_error(
            Exception("Bad request"),
            status_code=400,
        )
        assert result.category == ErrorCategory.format_error

    def test_400_context_overflow(self):
        result = classify_api_error(
            Exception("context length exceeded"),
            status_code=400,
        )
        assert result.category == ErrorCategory.context_overflow
        assert result.should_compress is True

    def test_400_with_auth_pattern(self):
        result = classify_api_error(
            Exception("invalid api key"),
            status_code=400,
        )
        assert result.category == ErrorCategory.auth

    def test_401_auth(self):
        result = classify_api_error(
            Exception("Unauthorized"),
            status_code=401,
        )
        assert result.category == ErrorCategory.auth
        assert result.should_rotate_credential is True

    def test_401_expired(self):
        result = classify_api_error(
            Exception("Token expired"),
            status_code=401,
        )
        assert result.category == ErrorCategory.auth

    def test_401_permanently_revoked(self):
        result = classify_api_error(
            Exception("API key permanently revoked"),
            status_code=401,
        )
        assert result.category == ErrorCategory.auth_permanent
        assert result.retryable is False

    def test_402_billing(self):
        result = classify_api_error(
            Exception("Payment required"),
            status_code=402,
        )
        assert result.category == ErrorCategory.billing
        assert result.retryable is False
        assert result.should_fallback is True

    def test_404_model_not_found(self):
        result = classify_api_error(
            Exception("Model not found"),
            status_code=404,
        )
        assert result.category == ErrorCategory.model_not_found

    def test_408_timeout(self):
        result = classify_api_error(
            Exception("Request timeout"),
            status_code=408,
        )
        assert result.category == ErrorCategory.timeout

    def test_413_payload_too_large(self):
        result = classify_api_error(
            Exception("Request entity too large"),
            status_code=413,
        )
        assert result.category == ErrorCategory.payload_too_large

    def test_500_server_error(self):
        result = classify_api_error(
            Exception("Internal server error"),
            status_code=500,
        )
        assert result.category == ErrorCategory.server_error

    def test_502_server_error(self):
        result = classify_api_error(
            Exception("Bad gateway"),
            status_code=502,
        )
        assert result.category == ErrorCategory.server_error

    def test_503_overloaded(self):
        result = classify_api_error(
            Exception("Service unavailable"),
            status_code=503,
        )
        assert result.category == ErrorCategory.overloaded

    def test_504_timeout(self):
        result = classify_api_error(
            Exception("Gateway timeout"),
            status_code=504,
        )
        assert result.category == ErrorCategory.timeout

    def test_529_overloaded(self):
        result = classify_api_error(
            Exception("Overloaded"),
            status_code=529,
        )
        assert result.category == ErrorCategory.overloaded


# ============================================================
# classify_api_error — message pattern based
# ============================================================

class TestClassifyApiErrorByMessage:
    """Tests for classify_api_error() with message pattern matching."""

    def test_billing_insufficient_credits(self):
        result = classify_api_error(
            Exception("Insufficient credits. Please top up."),
        )
        assert result.category == ErrorCategory.billing
        assert result.retryable is False

    def test_billing_quota_exceeded(self):
        result = classify_api_error(
            Exception("You have exceeded your current quota"),
        )
        assert result.category == ErrorCategory.billing

    def test_rate_limit_too_many_requests(self):
        result = classify_api_error(
            Exception("Too many requests. Try again in 30 seconds."),
        )
        assert result.category == ErrorCategory.rate_limit

    def test_rate_limit_throttled(self):
        result = classify_api_error(
            Exception("Request was throttled"),
        )
        assert result.category == ErrorCategory.rate_limit

    def test_context_overflow_token_limit(self):
        result = classify_api_error(
            Exception("This model's maximum context length is 8192 tokens"),
        )
        assert result.category == ErrorCategory.context_overflow
        assert result.should_compress is True

    def test_context_overflow_reduce_length(self):
        result = classify_api_error(
            Exception("Please reduce the length of the messages"),
        )
        assert result.category == ErrorCategory.context_overflow

    def test_auth_invalid_key(self):
        result = classify_api_error(
            Exception("Invalid API key provided"),
        )
        assert result.category == ErrorCategory.auth

    def test_model_not_found(self):
        result = classify_api_error(
            Exception("The model gpt-5 does not exist"),
        )
        assert result.category == ErrorCategory.model_not_found

    def test_server_error(self):
        result = classify_api_error(
            Exception("Internal server error occurred"),
        )
        assert result.category == ErrorCategory.server_error


# ============================================================
# classify_api_error — transport errors
# ============================================================

class TestClassifyApiErrorTransport:
    """Tests for transport-level error classification."""

    def test_timeout_by_type_name(self):
        # Create exception with name in _TRANSPORT_ERROR_TYPES
        exc = TimeoutError("Connection timed out")
        result = classify_api_error(exc)
        assert result.category == ErrorCategory.timeout

    def test_timeout_by_message(self):
        result = classify_api_error(
            Exception("The request timed out after 30 seconds"),
        )
        assert result.category == ErrorCategory.timeout

    def test_connection_refused(self):
        result = classify_api_error(
            Exception("Connection refused"),
        )
        assert result.category == ErrorCategory.connection_error

    def test_connection_reset(self):
        result = classify_api_error(
            Exception("Connection reset by peer"),
        )
        assert result.category == ErrorCategory.connection_error

    def test_no_route_to_host(self):
        result = classify_api_error(
            Exception("No route to host"),
        )
        assert result.category == ErrorCategory.connection_error


# ============================================================
# classify_api_error — string input
# ============================================================

class TestClassifyApiErrorString:
    """Tests for classify_api_error() with string input."""

    def test_string_input(self):
        result = classify_api_error("Rate limit exceeded")
        assert result.category == ErrorCategory.rate_limit

    def test_string_billing(self):
        result = classify_api_error("Insufficient credits", status_code=402)
        assert result.category == ErrorCategory.billing


# ============================================================
# Convenience functions
# ============================================================

class TestConvenienceFunctions:
    """Tests for is_retryable, get_recovery_action, format_chinese_error."""

    def test_is_retryable_rate_limit(self):
        assert is_retryable(Exception("Rate limit"), status_code=429) is True

    def test_is_retryable_billing(self):
        assert is_retryable(Exception("Insufficient credits")) is False

    def test_is_retryable_timeout(self):
        assert is_retryable(Exception("timed out")) is True

    def test_get_recovery_action_returns_string(self):
        result = get_recovery_action(Exception("Rate limit"), provider="openai")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_recovery_action_billing(self):
        result = get_recovery_action(
            Exception("Insufficient credits"), provider="deepseek"
        )
        assert "余额" in result or "配额" in result or "充值" in result or "服务商" in result

    def test_format_chinese_error(self):
        result = format_chinese_error(
            Exception("Rate limit exceeded"),
            provider="openai",
            model="gpt-4o",
        )
        assert "Dragon Agent" in result
        assert "openai" in result or "gpt-4o" in result
        assert isinstance(result, str)

    def test_format_chinese_error_with_status(self):
        result = format_chinese_error(
            Exception("Unauthorized"),
            provider="deepseek",
            model="deepseek-chat",
            status_code=401,
        )
        assert "401" in result

    def test_format_chinese_error_no_provider(self):
        result = format_chinese_error(Exception("Some error"))
        assert "Dragon Agent" in result


# ============================================================
# classify_api_error — edge cases
# ============================================================

class TestClassifyApiErrorEdgeCases:
    """Edge case tests for error classification."""

    def test_empty_exception(self):
        result = classify_api_error(Exception())
        assert result.category == ErrorCategory.unknown
        assert result.retryable is True

    def test_unknown_fallback(self):
        result = classify_api_error(Exception("Some random error text"))
        assert result.category == ErrorCategory.unknown
        assert result.retryable is True

    def test_error_code_from_body(self):
        exc = Exception("Error")
        exc.body = {"error": {"code": "context_length_exceeded"}}
        result = classify_api_error(exc)
        assert result.category == ErrorCategory.context_overflow

    def test_provider_and_model_preserved(self):
        result = classify_api_error(
            Exception("Rate limit"),
            provider="deepseek",
            model="deepseek-v3",
            status_code=429,
        )
        assert result.provider == "deepseek"
        assert result.model == "deepseek-v3"

    def test_recovery_suggestion_not_empty(self):
        result = classify_api_error(Exception("Server error"), status_code=500)
        assert len(result.recovery_suggestion) > 0

    def test_context_overflow_heuristic(self):
        # approx_tokens > 85% of context_length with server disconnect
        result = classify_api_error(
            Exception("Server disconnected without sending a response."),
            approx_tokens=180000, context_length=200000,
        )
        assert result.category == ErrorCategory.context_overflow
        assert result.should_compress is True
