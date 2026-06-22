"""
Dragon Agent — Rate Limiter
===========================

Token-bucket-based rate limiting for LLM provider API calls, with
automatic 429 backoff, circuit breaking, and per-provider statistics.

Provides:

1. **Token Bucket Algorithm** — per-provider rate limiting with configurable
   capacity and refill rate.
2. **429 Response Detection** — automatic exponential backoff with jitter
   when a provider returns HTTP 429 ("Too Many Requests").
3. **Circuit Breaker** — after N consecutive failures, skip the provider
   for a cooldown period before probing again.
4. **Statistics** — request_count, error_count, backoff_count per provider.

Inspired by Hermes Agent's ``agent/rate_limit_tracker.py`` but designed
as a pre-request gate rather than a post-response tracker.

Usage::

    limiter = RateLimiter()
    limiter.configure("openai", rpm=100, tpm=1_000_000)
    limiter.configure("deepseek", rpm=50, tpm=500_000)

    # Before each API call:
    if not await limiter.acquire("deepseek"):
        raise RateLimitExceeded("deepseek rate limit reached")

    # After a 429 response:
    limiter.record_429("deepseek", retry_after=30)

    # After success:
    limiter.record_success("deepseek")

    # Stats:
    print(limiter.stats("deepseek"))
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("dragon.rate_limiter")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

# Default token bucket sizes
DEFAULT_REQUESTS_PER_MINUTE = 30
DEFAULT_TOKENS_PER_MINUTE = 500_000
DEFAULT_REQUESTS_PER_HOUR = 500
DEFAULT_TOKENS_PER_HOUR = 5_000_000

# Circuit breaker defaults
DEFAULT_CIRCUIT_THRESHOLD = 5       # consecutive failures to open circuit
DEFAULT_CIRCUIT_COOLDOWN_SECS = 60  # cooldown period
DEFAULT_CIRCUIT_HALF_OPEN_DELAY = 30  # before first probe

# Backoff defaults
DEFAULT_BASE_DELAY_SECS = 1.0
DEFAULT_MAX_DELAY_SECS = 120.0
DEFAULT_JITTER_FACTOR = 0.1


# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────

class CircuitState(Enum):
    """Provider circuit-breaker state."""
    CLOSED = "closed"         # normal operation, requests flow
    OPEN = "open"             # fast-fail, all requests rejected
    HALF_OPEN = "half_open"   # single probe allowed


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class TokenBucket:
    """A token bucket for rate limiting.

    Tokens are added at a fixed rate (refill_rate per second) up to
    a maximum capacity. Each request consumes one or more tokens.
    """
    capacity: float            # max tokens in bucket
    refill_rate: float         # tokens per second
    _tokens: float = field(init=False, default=0.0)
    _last_refill: float = field(init=False, default=0.0)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)

    def __post_init__(self):
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Add tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume *tokens* from the bucket.

        Returns True if consumed, False if insufficient tokens.
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def available(self) -> float:
        """Return current available tokens."""
        with self._lock:
            self._refill()
            return self._tokens

    async def acquire(self, tokens: float = 1.0, timeout_secs: float = 10.0) -> bool:
        """Block until tokens are available or timeout.

        Returns True if acquired, False on timeout.
        """
        deadline = time.monotonic() + timeout_secs
        while True:
            if self.consume(tokens):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            # Estimate wait time
            needed = tokens - self.available()
            wait = min(needed / self.refill_rate if self.refill_rate > 0 else 0.5, remaining)
            await asyncio.sleep(max(0.05, wait))


@dataclass
class RateLimitConfig:
    """Per-provider rate limit configuration."""
    provider: str = ""
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE
    tokens_per_minute: int = DEFAULT_TOKENS_PER_MINUTE
    requests_per_hour: int = DEFAULT_REQUESTS_PER_HOUR
    tokens_per_hour: int = DEFAULT_TOKENS_PER_HOUR

    # Circuit breaker
    circuit_threshold: int = DEFAULT_CIRCUIT_THRESHOLD
    circuit_cooldown_secs: float = DEFAULT_CIRCUIT_COOLDOWN_SECS

    # Backoff
    base_delay_secs: float = DEFAULT_BASE_DELAY_SECS
    max_delay_secs: float = DEFAULT_MAX_DELAY_SECS
    jitter_factor: float = DEFAULT_JITTER_FACTOR


@dataclass
class RateLimitStats:
    """Per-provider rate limit statistics."""
    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    rate_limit_count: int = 0        # 429 responses received
    backoff_count: int = 0            # times we intentionally delayed
    circuit_trips: int = 0            # times circuit breaker opened
    last_request_time: float = 0.0
    last_error_time: float = 0.0
    last_429_time: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.request_count == 0:
            return 1.0
        return self.success_count / self.request_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requests": self.request_count,
            "successes": self.success_count,
            "errors": self.error_count,
            "rate_limits": self.rate_limit_count,
            "backoffs": self.backoff_count,
            "circuit_trips": self.circuit_trips,
            "success_rate": round(self.success_rate, 4),
            "last_request": self.last_request_time,
            "last_error": self.last_error_time,
            "last_429": self.last_429_time,
        }


# ────────────────────────────────────────────────────────────────────
# Rate Limiter
# ────────────────────────────────────────────────────────────────────

class RateLimiter:
    """Multi-provider rate limiter with token buckets and circuit breakers.

    Usage::

        limiter = RateLimiter()
        limiter.configure("openai", rpm=100, tpm=1_000_000)

        # Blocking acquire
        if limiter.acquire_sync("openai"):
            make_api_call()

        # Async acquire
        if await limiter.acquire("openai"):
            await make_api_call()

        # Record results
        limiter.record_success("openai", tokens=1500)
        limiter.record_429("openai", retry_after=30)
    """

    def __init__(self) -> None:
        self._configs: Dict[str, RateLimitConfig] = {}
        self._stats: Dict[str, RateLimitStats] = {}

        # Token buckets
        self._buckets_rpm: Dict[str, TokenBucket] = {}
        self._buckets_rph: Dict[str, TokenBucket] = {}
        self._buckets_tpm: Dict[str, TokenBucket] = {}
        self._buckets_tph: Dict[str, TokenBucket] = {}

        # Circuit breaker state
        self._circuit_state: Dict[str, CircuitState] = {}
        self._circuit_failures: Dict[str, int] = {}
        self._circuit_last_failure: Dict[str, float] = {}
        self._circuit_opened_at: Dict[str, float] = {}

        # 429 backoff tracking
        self._backoff_until: Dict[str, float] = {}
        self._consecutive_429: Dict[str, int] = {}

        self._lock = threading.Lock()
        logger.info("RateLimiter initialized")

    # ── Configuration ──────────────────────────────────────────────────

    def configure(
        self,
        provider: str,
        *,
        rpm: int | None = None,
        tpm: int | None = None,
        rph: int | None = None,
        tph: int | None = None,
        circuit_threshold: int | None = None,
        circuit_cooldown_secs: float | None = None,
        base_delay_secs: float | None = None,
        max_delay_secs: float | None = None,
    ) -> None:
        """Configure rate limits for a provider.

        Args:
            provider: Provider name (e.g. 'openai', 'deepseek').
            rpm: Requests per minute.
            tpm: Tokens per minute.
            rph: Requests per hour.
            tph: Tokens per hour.
            circuit_threshold: Consecutive failures before circuit opens.
            circuit_cooldown_secs: Circuit cooldown duration.
            base_delay_secs: Base backoff delay for 429 handling.
            max_delay_secs: Maximum backoff delay.
        """
        with self._lock:
            cfg = self._configs.get(provider) or RateLimitConfig(provider=provider)
            if rpm is not None:
                cfg.requests_per_minute = rpm
            if tpm is not None:
                cfg.tokens_per_minute = tpm
            if rph is not None:
                cfg.requests_per_hour = rph
            if tph is not None:
                cfg.tokens_per_hour = tph
            if circuit_threshold is not None:
                cfg.circuit_threshold = circuit_threshold
            if circuit_cooldown_secs is not None:
                cfg.circuit_cooldown_secs = circuit_cooldown_secs
            if base_delay_secs is not None:
                cfg.base_delay_secs = base_delay_secs
            if max_delay_secs is not None:
                cfg.max_delay_secs = max_delay_secs

            self._configs[provider] = cfg
            self._ensure_buckets(provider, cfg)
            self._ensure_stats(provider)
            logger.debug("Rate limiter configured: %s rpm=%d tpm=%d", provider, cfg.requests_per_minute, cfg.tokens_per_minute)

    def _ensure_buckets(self, provider: str, cfg: RateLimitConfig) -> None:
        """Create token buckets if they don't exist."""
        if provider not in self._buckets_rpm:
            self._buckets_rpm[provider] = TokenBucket(
                capacity=cfg.requests_per_minute,
                refill_rate=cfg.requests_per_minute / 60.0,
            )
            self._buckets_rph[provider] = TokenBucket(
                capacity=cfg.requests_per_hour,
                refill_rate=cfg.requests_per_hour / 3600.0,
            )
            self._buckets_tpm[provider] = TokenBucket(
                capacity=cfg.tokens_per_minute,
                refill_rate=cfg.tokens_per_minute / 60.0,
            )
            self._buckets_tph[provider] = TokenBucket(
                capacity=cfg.tokens_per_hour,
                refill_rate=cfg.tokens_per_hour / 3600.0,
            )

    def _ensure_stats(self, provider: str) -> None:
        """Create stats entry if it doesn't exist."""
        if provider not in self._stats:
            self._stats[provider] = RateLimitStats()
            self._circuit_state[provider] = CircuitState.CLOSED
            self._circuit_failures[provider] = 0
            self._circuit_last_failure[provider] = 0.0

    # ── Rate Limit Checking ────────────────────────────────────────────

    def acquire_sync(self, provider: str, tokens: int = 0) -> bool:
        """Synchronous check: can we make a request?

        Args:
            provider: Provider name.
            tokens: Estimated output tokens (0 = don't check token limits).

        Returns:
            True if the request can proceed.
        """
        cfg = self._configs.get(provider)
        if cfg is None:
            return True  # unconfigured = unlimited

        # Check circuit breaker
        circuit_allowed = self._check_circuit(provider, cfg)
        if not circuit_allowed:
            return False

        # Check backoff (429 handling)
        with self._lock:
            backoff_until = self._backoff_until.get(provider, 0)
        if time.monotonic() < backoff_until:
            return False

        # Check token buckets
        rpm_bucket = self._buckets_rpm.get(provider)
        rph_bucket = self._buckets_rph.get(provider)

        if rpm_bucket and not rpm_bucket.consume():
            self._record_rejected(provider)
            return False
        if rph_bucket and not rph_bucket.consume():
            self._record_rejected(provider)
            return False

        if tokens > 0:
            tpm_bucket = self._buckets_tpm.get(provider)
            tph_bucket = self._buckets_tph.get(provider)
            if tpm_bucket and not tpm_bucket.consume(tokens):
                self._record_rejected(provider)
                return False
            if tph_bucket and not tph_bucket.consume(tokens):
                self._record_rejected(provider)
                return False

        return True

    async def acquire(
        self,
        provider: str,
        tokens: int = 0,
        timeout_secs: float = 30.0,
    ) -> bool:
        """Async check with waiting: can we make a request?

        Args:
            provider: Provider name.
            tokens: Estimated output tokens.
            timeout_secs: Maximum wait time.

        Returns:
            True if acquired, False on timeout or circuit-open.
        """
        deadline = time.monotonic() + timeout_secs
        while True:
            if self.acquire_sync(provider, tokens):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.5, remaining))

    # ── Response Recording ─────────────────────────────────────────────

    def record_success(self, provider: str, tokens: int = 0) -> None:
        """Record a successful API call."""
        with self._lock:
            stats = self._stats.get(provider)
            if stats:
                stats.request_count += 1
                stats.success_count += 1
                stats.last_request_time = time.monotonic()

            # Reset circuit breaker on success
            if provider in self._circuit_state:
                self._circuit_state[provider] = CircuitState.CLOSED
                self._circuit_failures[provider] = 0
                self._consecutive_429[provider] = 0

            # Clear backoff
            self._backoff_until.pop(provider, None)

    def record_error(self, provider: str, status_code: int = 0) -> None:
        """Record an API error (non-429)."""
        with self._lock:
            stats = self._stats.get(provider)
            if stats:
                stats.request_count += 1
                stats.error_count += 1
                stats.last_error_time = time.monotonic()
                stats.last_request_time = time.monotonic()

            self._increment_circuit_failures(provider)

    def record_429(self, provider: str, retry_after: float = 0) -> None:
        """Record a 429 ("Too Many Requests") response.

        Triggers exponential backoff with jitter and increments the
        circuit breaker failure count.

        Args:
            provider: Provider name.
            retry_after: Seconds to wait (from Retry-After header or default).
        """
        with self._lock:
            stats = self._stats.get(provider)
            if stats:
                stats.request_count += 1
                stats.error_count += 1
                stats.rate_limit_count += 1
                stats.backoff_count += 1
                stats.last_error_time = time.monotonic()
                stats.last_429_time = time.monotonic()
                stats.last_request_time = time.monotonic()

            cfg = self._configs.get(provider)
            if cfg:
                self._consecutive_429[provider] = self._consecutive_429.get(provider, 0) + 1
                consecutive = self._consecutive_429[provider]

                # Exponential backoff with jitter
                delay = min(
                    cfg.base_delay_secs * (2 ** (consecutive - 1)),
                    cfg.max_delay_secs,
                )
                if retry_after > 0:
                    delay = max(delay, retry_after)
                jitter = delay * cfg.jitter_factor * random.random()
                delay += jitter

                self._backoff_until[provider] = time.monotonic() + delay
                logger.warning(
                    "Rate limited on %s (consecutive=%d), backing off %.1fs",
                    provider, consecutive, delay,
                )

            self._increment_circuit_failures(provider)

    # ── Circuit Breaker ────────────────────────────────────────────────

    def _check_circuit(self, provider: str, cfg: RateLimitConfig) -> bool:
        """Check if circuit allows requests. Returns False if circuit is open."""
        with self._lock:
            state = self._circuit_state.get(provider, CircuitState.CLOSED)

            if state == CircuitState.CLOSED:
                return True

            if state == CircuitState.OPEN:
                opened_at = self._circuit_opened_at.get(provider, 0)
                if time.monotonic() - opened_at >= cfg.circuit_cooldown_secs:
                    self._circuit_state[provider] = CircuitState.HALF_OPEN
                    logger.info("Circuit half-open for '%s' — probing", provider)
                    return True
                return False

            # HALF_OPEN — allow single probe
            return True

    def _increment_circuit_failures(self, provider: str) -> None:
        """Record a failure and open circuit if threshold reached."""
        cfg = self._configs.get(provider)
        if cfg is None:
            return

        current = self._circuit_state.get(provider, CircuitState.CLOSED)

        if current == CircuitState.HALF_OPEN:
            # Probe failed — reopen
            self._circuit_state[provider] = CircuitState.OPEN
            self._circuit_opened_at[provider] = time.monotonic()
            if provider in self._stats:
                self._stats[provider].circuit_trips += 1
            logger.warning("Circuit OPEN for '%s' — half-open probe failed", provider)

        elif current == CircuitState.CLOSED:
            self._circuit_failures[provider] = self._circuit_failures.get(provider, 0) + 1
            failures = self._circuit_failures[provider]
            self._circuit_last_failure[provider] = time.monotonic()

            if failures >= cfg.circuit_threshold:
                self._circuit_state[provider] = CircuitState.OPEN
                self._circuit_opened_at[provider] = time.monotonic()
                if provider in self._stats:
                    self._stats[provider].circuit_trips += 1
                logger.warning(
                    "Circuit OPEN for '%s' — %d consecutive failures",
                    provider, failures,
                )

    def circuit_reset(self, provider: str) -> None:
        """Manually reset the circuit breaker for a provider."""
        with self._lock:
            self._circuit_state[provider] = CircuitState.CLOSED
            self._circuit_failures[provider] = 0
            self._backoff_until.pop(provider, None)
            self._consecutive_429[provider] = 0
            logger.info("Circuit reset for '%s'", provider)

    def circuit_state(self, provider: str) -> CircuitState:
        """Return current circuit breaker state for a provider."""
        return self._circuit_state.get(provider, CircuitState.CLOSED)

    # ── Statistics ─────────────────────────────────────────────────────

    def stats(self, provider: str) -> RateLimitStats:
        """Return current statistics for a provider."""
        return self._stats.get(provider, RateLimitStats())

    def all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Return statistics for all configured providers."""
        return {p: s.to_dict() for p, s in self._stats.items()}

    def bucket_status(self, provider: str) -> Dict[str, Any]:
        """Return current token bucket status for a provider."""
        return {
            "rpm_available": self._buckets_rpm.get(provider, TokenBucket(1, 1)).available()
            if provider in self._buckets_rpm else None,
            "rph_available": self._buckets_rph.get(provider, TokenBucket(1, 1)).available()
            if provider in self._buckets_rph else None,
            "tpm_available": self._buckets_tpm.get(provider, TokenBucket(1, 1)).available()
            if provider in self._buckets_tpm else None,
            "tph_available": self._buckets_tph.get(provider, TokenBucket(1, 1)).available()
            if provider in self._buckets_tph else None,
            "circuit_state": self._circuit_state.get(provider, CircuitState.CLOSED).value,
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _record_rejected(self, provider: str) -> None:
        """Record that a request was rejected by rate limiting."""
        with self._lock:
            stats = self._stats.get(provider)
            if stats:
                stats.backoff_count += 1


# ────────────────────────────────────────────────────────────────────
# Standalone utilities
# ────────────────────────────────────────────────────────────────────

def parse_retry_after(headers: Dict[str, str]) -> float:
    """Parse the Retry-After header into seconds.

    Supports both delta-seconds and HTTP-date formats per RFC 7231.

    Args:
        headers: HTTP response headers (case-insensitive).

    Returns:
        Seconds to wait, or 0 if header not present / unparseable.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    value = lowered.get("retry-after", "")
    if not value:
        return 0.0

    # Try delta-seconds (integer or float)
    try:
        delay = float(value)
        if delay >= 0:
            return delay
    except ValueError:
        pass

    # Try HTTP-date format
    try:
        from email.utils import parsedate_to_datetime
        retry_time = parsedate_to_datetime(value)
        delay = (retry_time.timestamp() - time.time())
        return max(0.0, delay)
    except (ValueError, TypeError, ImportError):
        pass

    return 0.0


DEFAULT_RATE_LIMITER: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the global RateLimiter singleton."""
    global DEFAULT_RATE_LIMITER
    if DEFAULT_RATE_LIMITER is None:
        DEFAULT_RATE_LIMITER = RateLimiter()
    return DEFAULT_RATE_LIMITER
