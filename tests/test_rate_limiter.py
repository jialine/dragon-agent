"""Tests for dragon/rate_limiter.py — TokenBucket, RateLimiter, parse_retry_after"""
import sys, os
sys.path.insert(0, '/home/jialine/dragon-agent')

import time
import pytest
from dragon.rate_limiter import (
    TokenBucket, RateLimiter, RateLimitConfig, RateLimitStats,
    CircuitState, parse_retry_after, get_rate_limiter,
)


class TestTokenBucket:
    def test_initial_tokens(self):
        tb = TokenBucket(capacity=10, refill_rate=5)
        assert tb._tokens == 10  # starts at capacity

    def test_consume_within_limit(self):
        tb = TokenBucket(capacity=50, refill_rate=100)
        assert tb.consume(30) is True
        assert 19 <= tb.available() <= 25

    def test_consume_refuses_over_capacity(self):
        tb = TokenBucket(capacity=10, refill_rate=5)
        assert tb.consume(20) is False

    def test_refill_over_time(self):
        tb = TokenBucket(capacity=100, refill_rate=100)
        tb._tokens = 0
        tb._last_refill = time.monotonic() - 1.0
        tb._refill()
        assert tb.available() >= 99

    def test_refill_caps_at_capacity(self):
        tb = TokenBucket(capacity=100, refill_rate=1000)
        tb._tokens = 50
        tb._last_refill = time.monotonic() - 999
        tb._refill()
        assert tb.available() == 100


class TestRateLimiter:
    def setup_method(self):
        self.rl = RateLimiter()

    def test_configure_creates_buckets(self):
        self.rl.configure('test', rpm=60, tpm=10000)
        stats = self.rl.stats('test')
        assert stats is not None

    def test_acquire_sync_success(self):
        self.rl.configure('test', rpm=6000, tpm=100000)
        assert self.rl.acquire_sync('test', tokens=100) is True

    def test_acquire_sync_no_config(self):
        assert self.rl.acquire_sync('unknown', tokens=100) is True

    def test_record_success_updates_stats(self):
        self.rl.configure('test', rpm=60, tpm=10000)
        self.rl.record_success('test', tokens=500)
        stats = self.rl.stats('test')
        assert stats.request_count == 1
        assert stats.success_count == 1

    def test_record_error_updates_stats(self):
        self.rl.configure('test', rpm=60, tpm=10000)
        self.rl.record_error('test', status_code=500)
        self.rl.record_success('test', tokens=100)
        stats = self.rl.stats('test')
        assert stats.request_count == 2
        assert stats.error_count == 1

    def test_circuit_breaker_opens(self):
        self.rl.configure('test', rpm=60, tpm=10000, circuit_threshold=3, circuit_cooldown_secs=0.5)
        for _ in range(3):
            self.rl.record_error('test', status_code=500)
        assert self.rl.circuit_state('test') == CircuitState.OPEN

    def test_circuit_manual_reset(self):
        self.rl.configure('test', rpm=60, tpm=10000, circuit_threshold=1)
        self.rl.record_error('test', status_code=500)
        assert self.rl.circuit_state('test') in (CircuitState.OPEN, CircuitState.HALF_OPEN)
        self.rl.circuit_reset('test')
        assert self.rl.circuit_state('test') == CircuitState.CLOSED

    def test_all_stats(self):
        self.rl.configure('a', rpm=60, tpm=10000)
        self.rl.configure('b', rpm=30, tpm=5000)
        stats = self.rl.all_stats()
        assert 'a' in stats
        assert 'b' in stats

    def test_bucket_status(self):
        self.rl.configure('test', rpm=60, tpm=10000)
        status = self.rl.bucket_status('test')
        assert 'rpm_available' in status


class TestParseRetryAfter:
    def test_integer_seconds(self):
        assert parse_retry_after({'Retry-After': '120'}) == 120.0

    def test_missing_header(self):
        assert parse_retry_after({}) == 0.0

    def test_none_headers(self):
        # parse_retry_after expects dict, None would fail
        with pytest.raises(AttributeError):
            parse_retry_after(None)


class TestRateLimitConfig:
    def test_defaults(self):
        cfg = RateLimitConfig()
        assert cfg.requests_per_minute == 30
        assert cfg.tokens_per_minute == 500000

    def test_custom(self):
        cfg = RateLimitConfig(
            requests_per_minute=120, tokens_per_minute=50000,
            circuit_threshold=5
        )
        assert cfg.requests_per_minute == 120
        assert cfg.circuit_threshold == 5


class TestRateLimitStats:
    def test_success_rate_all_ok(self):
        s = RateLimitStats(request_count=10, success_count=10, error_count=0)
        assert s.success_rate == 1.0

    def test_success_rate_mixed(self):
        s = RateLimitStats(request_count=10, success_count=7, error_count=3)
        assert s.success_rate == 0.7

    def test_to_dict(self):
        s = RateLimitStats(request_count=5, success_count=4, error_count=1)
        d = s.to_dict()
        assert d['requests'] == 5
        assert d['errors'] == 1


class TestGlobalRateLimiter:
    def test_get_rate_limiter_singleton(self):
        rl1 = get_rate_limiter()
        rl2 = get_rate_limiter()
        assert rl1 is rl2
