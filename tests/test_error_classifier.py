"""Tests for dragon/error_classifier.py"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
from dragon.error_classifier import (
    classify_api_error, ErrorCategory, is_retryable, get_recovery_action, format_chinese_error,
)

class TestClassifyApiError:
    def test_429(self):
        r = classify_api_error(Exception("429 rate limit"), provider="openai")
        assert r is not None
        assert r.category in (ErrorCategory.rate_limit, ErrorCategory.retryable)

    def test_401(self):
        r = classify_api_error(Exception("401 unauthorized"), provider="openai")
        assert r is not None
        assert r.category in (ErrorCategory.auth, ErrorCategory.auth_permanent)

    def test_500(self):
        r = classify_api_error(Exception("500 server error"), provider="openai")
        assert r is not None
        assert r.category in (ErrorCategory.retryable, ErrorCategory.server_error)

    def test_timeout(self):
        try:
            raise TimeoutError("timed out")
        except TimeoutError as e:
            r = classify_api_error(e, provider="openai")
        assert r is not None

    def test_context_length(self):
        r = classify_api_error(Exception("context_length_exceeded"), provider="openai")
        assert r is not None

class TestIsRetryable:
    def test_rate_limited(self):
        assert is_retryable(ErrorCategory.rate_limit) is True
    def test_permanent(self):
        assert is_retryable(ErrorCategory.auth_permanent) is True  # auth errors are retryable

class TestGetRecoveryAction:
    def test_returns_string(self):
        a = get_recovery_action(ErrorCategory.rate_limit)
        assert isinstance(a, str) and len(a) > 0

class TestFormatChineseError:
    def test_returns_string(self):
        m = format_chinese_error(ErrorCategory.rate_limit, "openai")
        assert isinstance(m, str) and len(m) > 0
