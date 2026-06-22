"""
Dragon Credential Pool — Multi-key rotation with automatic failover.

Enhanced version of dragon/credential.py adding:
  - Per-key RPM/TPM budget tracking
  - Rate-limit detection (429) → automatic key rotation
  - Health scoring with decay
  - Budget exhaustion tracking
  - Integration hooks for dragon.provider

Usage::

    from dragon.credential_pool import CredentialPool

    pool = CredentialPool(provider="deepseek")
    pool.add_credential("sk-key1", priority=1)
    pool.add_credential("sk-key2", priority=2)

    cred = pool.get_credential()          # -> Credential or None
    ...
    pool.mark_success(cred)               # key worked
    pool.mark_rate_limited(cred)          # 429 → auto-switch
    pool.mark_exhausted(cred)             # out of quota → auto-switch
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger("dragon.credential_pool")


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

DEFAULT_COOLDOWN_SECONDS = 60.0
RATE_LIMITED_COOLDOWN = 65.0  # Be slightly longer than typical 60s windows
EXHAUSTED_RETRY_SECONDS = 3600.0  # Retry exhausted keys after 1 hour
HEALTH_DECAY_RATE = 0.05  # Per successful call decay toward 1.0


# ────────────────────────────────────────────────────────────────────
# Pydantic Models
# ────────────────────────────────────────────────────────────────────


class CredentialStatus(str, Enum):
    """Status of a credential in the pool."""

    AVAILABLE = "available"
    """Credential is healthy and ready for use."""

    RATE_LIMITED = "rate_limited"
    """Temporarily rate-limited; will auto-recover after cooldown."""

    EXHAUSTED = "exhausted"
    """Permanently exhausted (out of quota/budget); retry after long wait."""

    DISABLED = "disabled"
    """Administratively disabled."""


class Credential(BaseModel):
    """A single API credential with health and usage tracking.

    Attributes:
        key: The actual API key string.
        key_id: Hashed identifier for safe logging.
        provider: Provider name this credential belongs to.
        priority: Lower = higher priority (default 0).
        status: Current availability status.
        cooldown_until: Monotonic timestamp when rate-limited cooldown ends.
        call_count: Total successful calls made with this key.
        error_count: Total errors encountered.
        last_used_at: Monotonic timestamp of last usage.
        rpm_limit: Requests-per-minute limit (0 = unlimited).
        tpm_limit: Tokens-per-minute limit (0 = unlimited).
        rpm_used: Requests used in current minute window.
        tpm_used: Tokens used in current minute window.
        budget_limit_usd: USD budget limit (0 = unlimited).
        budget_spent_usd: USD spent on this key.
        health_score: 0-1 health metric (higher = healthier).
    """

    key: str
    key_id: str = ""
    provider: str = ""
    priority: int = 0
    status: CredentialStatus = CredentialStatus.AVAILABLE
    cooldown_until: float = 0.0
    call_count: int = 0
    error_count: int = 0
    last_used_at: float = 0.0
    rpm_limit: int = 0
    tpm_limit: int = 0
    rpm_used: int = 0
    tpm_used: int = 0
    budget_limit_usd: float = 0.0
    budget_spent_usd: float = 0.0
    health_score: float = 0.8

    @property
    def is_available(self) -> bool:
        """Check if this credential can currently be used."""
        if self.status == CredentialStatus.DISABLED:
            return False
        if self.status == CredentialStatus.EXHAUSTED:
            # Re-check after long retry period
            if time.monotonic() >= self.cooldown_until:
                return True
            return False
        if self.status == CredentialStatus.RATE_LIMITED:
            if time.monotonic() >= self.cooldown_until:
                return True
            return False
        return True

    @property
    def rpm_available(self) -> bool:
        """Check if this key has remaining RPM budget."""
        if self.rpm_limit <= 0:
            return True
        return self.rpm_used < self.rpm_limit

    @property
    def tpm_available(self) -> bool:
        """Check if this key has remaining TPM budget."""
        if self.tpm_limit <= 0:
            return True
        return self.tpm_used < self.tpm_limit

    @property
    def budget_available(self) -> bool:
        """Check if this key has remaining USD budget."""
        if self.budget_limit_usd <= 0:
            return True
        return self.budget_spent_usd < self.budget_limit_usd

    def masked_key(self, visible: int = 8) -> str:
        """Return a masked version of the key for safe logging."""
        if len(self.key) <= visible + 4:
            return self.key[:4] + "***"
        return self.key[:visible] + "..." + self.key[-4:]


class PoolStats(BaseModel):
    """Aggregate statistics for a credential pool."""

    provider: str = ""
    total_credentials: int = 0
    available: int = 0
    rate_limited: int = 0
    exhausted: int = 0
    disabled: int = 0
    total_calls: int = 0
    total_errors: int = 0
    total_budget_spent_usd: float = 0.0
    avg_health_score: float = 0.0
    credentials: List[Dict[str, Any]] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# Credential Pool
# ────────────────────────────────────────────────────────────────────


class CredentialPool:
    """Multi-key credential pool with health tracking and automatic rotation.

    Features:
      - Multi-key rotation by health score and priority.
      - Rate-limit detection (HTTP 429) → auto-switch to next key.
      - RPM/TPM budget tracking with sliding windows.
      - USD budget exhaustion tracking.
      - Health scoring with exponential decay toward 1.0.
      - Thread-safe operations.

    Parameters
    ----------
    provider : str
        Provider name this pool serves (e.g., "deepseek", "openai").
    cooldown_secs : float
        Default cooldown duration for rate-limited keys (seconds).
    """

    def __init__(
        self,
        provider: str = "default",
        cooldown_secs: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self.provider = provider
        self.cooldown_secs = cooldown_secs
        self._credentials: Dict[str, Credential] = {}
        self._lock = threading.Lock()
        self._rotation_index: int = 0
        # Track per-minute window for RPM/TPM
        self._minute_window_start: float = time.monotonic()
        logger.info("CredentialPool[%s] initialized (cooldown=%.0fs)", provider, cooldown_secs)

    # ── Credential Management ──────────────────────────────────────

    def add_credential(
        self,
        key: str,
        priority: int = 0,
        rpm_limit: int = 0,
        tpm_limit: int = 0,
        budget_limit_usd: float = 0.0,
    ) -> Credential:
        """Add a credential to the pool.

        Args:
            key: The API key string.
            priority: Lower = higher priority (default 0).
            rpm_limit: Requests-per-minute limit (0 = unlimited).
            tpm_limit: Tokens-per-minute limit (0 = unlimited).
            budget_limit_usd: USD spending limit (0 = unlimited).

        Returns:
            The created Credential object.
        """
        if not key:
            raise ValueError("Cannot add empty key to pool")

        key_id = self._make_key_id(key)
        cred = Credential(
            key=key,
            key_id=key_id,
            provider=self.provider,
            priority=priority,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            budget_limit_usd=budget_limit_usd,
        )

        with self._lock:
            self._credentials[key_id] = cred

        logger.debug(
            "CredentialPool[%s]: added %s (priority=%d rpm=%d tpm=%d budget=%.2f)",
            self.provider, cred.masked_key(), priority, rpm_limit, tpm_limit,
            budget_limit_usd,
        )
        return cred

    def add_credentials(
        self,
        keys: List[str],
        priority: int = 0,
        **kwargs,
    ) -> List[Credential]:
        """Add multiple credentials at once.

        Args:
            keys: List of API key strings.
            priority: Priority for all keys.
            **kwargs: Passed to add_credential().

        Returns:
            List of created Credential objects.
        """
        creds = []
        for k in keys:
            creds.append(self.add_credential(k, priority=priority, **kwargs))
        return creds

    def remove_credential(self, key: str) -> bool:
        """Remove a credential from the pool.

        Args:
            key: The API key string to remove.

        Returns:
            True if the key was found and removed.
        """
        key_id = self._make_key_id(key)
        with self._lock:
            if key_id in self._credentials:
                del self._credentials[key_id]
                logger.debug("CredentialPool[%s]: removed %s...", self.provider, key[:8])
                return True
        return False

    # ── Credential Selection ───────────────────────────────────────

    def get_credential(self) -> Optional[Credential]:
        """Get the best available credential.

        Selection order:
        1. Available credentials (not exhausted, not rate-limited).
        2. Sorted by health_score (descending), then priority (ascending).
        3. Filters out keys that have exceeded RPM/TPM limits.

        Returns:
            The best available Credential, or None if none available.
        """
        self._maybe_reset_minute_window()

        with self._lock:
            available = [
                c for c in self._credentials.values()
                if c.is_available
                and c.rpm_available
                and c.tpm_available
                and c.budget_available
            ]

            if not available:
                logger.warning(
                    "CredentialPool[%s]: no available credentials (%d total, "
                    "%d exhausted, %d rate_limited)",
                    self.provider, self.total_count,
                    self.exhausted_count, self.rate_limited_count,
                )
                return None

            # Sort: healthiest first, then highest priority (lowest number)
            available.sort(key=lambda c: (-c.health_score, c.priority))

            return available[0]

    def get_credential_round_robin(self) -> Optional[Credential]:
        """Get the next credential in round-robin order.

        Useful for even distribution across keys with the same priority.

        Returns:
            The next Credential in rotation, or None.
        """
        self._maybe_reset_minute_window()

        with self._lock:
            available = [
                c for c in self._credentials.values()
                if c.is_available
                and c.rpm_available
                and c.tpm_available
                and c.budget_available
            ]

            if not available:
                return None

            self._rotation_index = (self._rotation_index + 1) % len(available)
            return available[self._rotation_index]

    def get_all_credentials(self) -> List[Credential]:
        """Return all credentials in the pool (for debugging/inspection)."""
        with self._lock:
            return list(self._credentials.values())

    # ── Status Tracking ────────────────────────────────────────────

    def mark_success(
        self,
        credential: Credential,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Mark a credential as successfully used.

        Updates call count, health score, RPM/TPM usage, and budget.

        Args:
            credential: The credential that was used.
            tokens_used: Number of tokens consumed in this call.
            cost_usd: USD cost of this call.
        """
        key_id = credential.key_id
        with self._lock:
            cred = self._credentials.get(key_id)
            if cred is None:
                return

            cred.call_count += 1
            cred.last_used_at = time.monotonic()
            cred.rpm_used += 1
            cred.tpm_used += tokens_used
            cred.budget_spent_usd += cost_usd

            # Exponential decay toward 1.0 health
            cred.health_score = min(
                1.0,
                cred.health_score + (1.0 - cred.health_score) * HEALTH_DECAY_RATE,
            )

            logger.debug(
                "CredentialPool[%s]: %s success (health=%.2f calls=%d)",
                self.provider, cred.masked_key(), cred.health_score, cred.call_count,
            )

    def mark_rate_limited(
        self,
        credential: Credential,
        cooldown_secs: Optional[float] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        """Mark a credential as rate-limited (HTTP 429).

        The credential enters a cooldown period. After cooldown_secs,
        it automatically becomes available again.

        Args:
            credential: The rate-limited credential.
            cooldown_secs: Override default cooldown duration.
            retry_after: Server-specified retry delay (from Retry-After header).
        """
        key_id = credential.key_id
        cooldown = cooldown_secs or self.cooldown_secs

        # Respect server's Retry-After header if provided
        if retry_after is not None and retry_after > 0:
            cooldown = retry_after

        with self._lock:
            cred = self._credentials.get(key_id)
            if cred is None:
                return

            cred.status = CredentialStatus.RATE_LIMITED
            cred.cooldown_until = time.monotonic() + cooldown
            cred.error_count += 1
            cred.call_count += 1
            cred.health_score = max(0.0, cred.health_score - 0.3)

            logger.warning(
                "CredentialPool[%s]: %s RATE LIMITED (cooldown=%.0fs health=%.2f)",
                self.provider, cred.masked_key(), cooldown, cred.health_score,
            )

    def mark_exhausted(
        self,
        credential: Credential,
        reason: str = "",
    ) -> None:
        """Mark a credential as permanently exhausted.

        Used when out of quota, budget depleted, or auth invalid.
        Will retry after EXHAUSTED_RETRY_SECONDS.

        Args:
            credential: The exhausted credential.
            reason: Human-readable reason for exhaustion.
        """
        key_id = credential.key_id
        with self._lock:
            cred = self._credentials.get(key_id)
            if cred is None:
                return

            cred.status = CredentialStatus.EXHAUSTED
            cred.cooldown_until = time.monotonic() + EXHAUSTED_RETRY_SECONDS
            cred.error_count += 1
            cred.call_count += 1
            cred.health_score = 0.0

            logger.warning(
                "CredentialPool[%s]: %s EXHAUSTED (%s). %d remaining.",
                self.provider, cred.masked_key(), reason, self.available_count,
            )

    def mark_error(
        self,
        credential: Credential,
        http_status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        """Smart error handler that routes to appropriate status.

        Args:
            credential: The credential that failed.
            http_status: HTTP status code (429 = rate limited, 402/403 = exhausted).
            retry_after: Server-specified retry delay.
        """
        if http_status == 429:
            self.mark_rate_limited(credential, retry_after=retry_after)
        elif http_status in (402, 403, 401):
            self.mark_exhausted(
                credential,
                reason=f"HTTP {http_status}",
            )
        else:
            # Generic error — just decrement health
            key_id = credential.key_id
            with self._lock:
                cred = self._credentials.get(key_id)
                if cred:
                    cred.error_count += 1
                    cred.call_count += 1
                    cred.health_score = max(0.0, cred.health_score - 0.1)
                    logger.debug(
                        "CredentialPool[%s]: %s error (status=%s health=%.2f)",
                        self.provider, cred.masked_key(), http_status, cred.health_score,
                    )

    def reset_credential(self, credential: Credential) -> None:
        """Reset a credential back to available (manual override).

        Args:
            credential: The credential to reset.
        """
        key_id = credential.key_id
        with self._lock:
            cred = self._credentials.get(key_id)
            if cred:
                cred.status = CredentialStatus.AVAILABLE
                cred.cooldown_until = 0.0
                cred.rpm_used = 0
                cred.tpm_used = 0
                cred.health_score = 0.8
                logger.info(
                    "CredentialPool[%s]: reset %s to available",
                    self.provider, cred.masked_key(),
                )

    def reset_all(self) -> None:
        """Reset all credentials to available."""
        with self._lock:
            for cred in self._credentials.values():
                cred.status = CredentialStatus.AVAILABLE
                cred.cooldown_until = 0.0
                cred.rpm_used = 0
                cred.tpm_used = 0
                cred.health_score = 0.8
        logger.info("CredentialPool[%s]: all credentials reset", self.provider)

    def disable_credential(self, credential: Credential) -> None:
        """Administratively disable a credential.

        Args:
            credential: The credential to disable.
        """
        key_id = credential.key_id
        with self._lock:
            cred = self._credentials.get(key_id)
            if cred:
                cred.status = CredentialStatus.DISABLED
                logger.info("CredentialPool[%s]: disabled %s", self.provider, cred.masked_key())

    # ── Properties & Stats ────────────────────────────────────────

    @property
    def total_count(self) -> int:
        return len(self._credentials)

    @property
    def available_count(self) -> int:
        """Count of credentials currently available."""
        with self._lock:
            return sum(1 for c in self._credentials.values() if c.is_available)

    @property
    def rate_limited_count(self) -> int:
        """Count of rate-limited credentials."""
        with self._lock:
            return sum(1 for c in self._credentials.values()
                       if c.status == CredentialStatus.RATE_LIMITED)

    @property
    def exhausted_count(self) -> int:
        """Count of exhausted credentials."""
        with self._lock:
            return sum(1 for c in self._credentials.values()
                       if c.status == CredentialStatus.EXHAUSTED)

    @property
    def has_available(self) -> bool:
        """True if at least one credential is available."""
        return self.available_count > 0

    def get_stats(self) -> PoolStats:
        """Get aggregate statistics for the pool."""
        with self._lock:
            creds = list(self._credentials.values())
            total_calls = sum(c.call_count for c in creds)
            total_errors = sum(c.error_count for c in creds)
            total_budget = sum(c.budget_spent_usd for c in creds)
            avg_health = (
                sum(c.health_score for c in creds) / len(creds)
                if creds else 0.0
            )

            return PoolStats(
                provider=self.provider,
                total_credentials=len(creds),
                available=self.available_count,
                rate_limited=self.rate_limited_count,
                exhausted=self.exhausted_count,
                disabled=sum(1 for c in creds if c.status == CredentialStatus.DISABLED),
                total_calls=total_calls,
                total_errors=total_errors,
                total_budget_spent_usd=total_budget,
                avg_health_score=round(avg_health, 3),
                credentials=[
                    {
                        "masked_key": c.masked_key(),
                        "priority": c.priority,
                        "status": c.status.value,
                        "health": round(c.health_score, 3),
                        "calls": c.call_count,
                        "errors": c.error_count,
                        "rpm": f"{c.rpm_used}/{c.rpm_limit}" if c.rpm_limit else "unlimited",
                        "tpm": f"{c.tpm_used}/{c.tpm_limit}" if c.tpm_limit else "unlimited",
                        "budget": f"${c.budget_spent_usd:.4f}/{c.budget_limit_usd:.2f}"
                        if c.budget_limit_usd else "unlimited",
                    }
                    for c in sorted(creds, key=lambda x: x.priority)
                ],
            )

    # ── Private Helpers ────────────────────────────────────────────

    @staticmethod
    def _make_key_id(key: str) -> str:
        """Create a deterministic ID from an API key."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _maybe_reset_minute_window(self) -> None:
        """Reset RPM/TPM counters when the minute window elapses."""
        now = time.monotonic()
        if now - self._minute_window_start >= 60.0:
            with self._lock:
                for cred in self._credentials.values():
                    cred.rpm_used = 0
                    cred.tpm_used = 0
            self._minute_window_start = now
            logger.debug("CredentialPool[%s]: RPM/TPM window reset", self.provider)


# ────────────────────────────────────────────────────────────────────
# Multi-Pool Manager
# ────────────────────────────────────────────────────────────────────


class CredentialManager:
    """Manage multiple credential pools across different providers.

    Usage::

        mgr = CredentialManager()
        mgr.add_pool("deepseek", ["sk-key1", "sk-key2"])
        mgr.add_pool("openai", ["sk-key3"])

        cred = mgr.get_credential("deepseek")
        ...
        mgr.mark_success("deepseek", cred)
    """

    def __init__(self) -> None:
        self._pools: Dict[str, CredentialPool] = {}

    def add_pool(
        self,
        provider: str,
        keys: List[str],
        cooldown_secs: float = DEFAULT_COOLDOWN_SECONDS,
        **cred_kwargs,
    ) -> CredentialPool:
        """Create and register a credential pool for a provider.

        Args:
            provider: Provider name (e.g., "openai", "deepseek").
            keys: List of API key strings.
            cooldown_secs: Default rate-limit cooldown.
            **cred_kwargs: Passed to add_credential (rpm_limit, etc.).

        Returns:
            The newly created CredentialPool.
        """
        pool = CredentialPool(provider=provider, cooldown_secs=cooldown_secs)
        pool.add_credentials(keys, **cred_kwargs)
        self._pools[provider] = pool
        logger.info(
            "CredentialManager: added pool '%s' with %d keys", provider, len(keys),
        )
        return pool

    def get_pool(self, provider: str) -> Optional[CredentialPool]:
        """Get the credential pool for a specific provider.

        Args:
            provider: Provider name.

        Returns:
            CredentialPool or None if not registered.
        """
        return self._pools.get(provider)

    def get_credential(self, provider: str) -> Optional[Credential]:
        """Get the best credential for a provider.

        Args:
            provider: Provider name.

        Returns:
            Best available Credential, or None.
        """
        pool = self._pools.get(provider)
        return pool.get_credential() if pool else None

    def mark_success(
        self,
        provider: str,
        credential: Credential,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Report a successful API call."""
        pool = self._pools.get(provider)
        if pool:
            pool.mark_success(credential, tokens_used=tokens_used, cost_usd=cost_usd)

    def mark_rate_limited(
        self,
        provider: str,
        credential: Credential,
        retry_after: Optional[float] = None,
    ) -> None:
        """Report a rate-limited API call (HTTP 429)."""
        pool = self._pools.get(provider)
        if pool:
            pool.mark_rate_limited(credential, retry_after=retry_after)

    def mark_error(
        self,
        provider: str,
        credential: Credential,
        http_status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        """Smart error routing based on HTTP status."""
        pool = self._pools.get(provider)
        if pool:
            pool.mark_error(credential, http_status=http_status, retry_after=retry_after)

    def mark_exhausted(self, provider: str, credential: Credential, reason: str = "") -> None:
        """Mark a credential as exhausted."""
        pool = self._pools.get(provider)
        if pool:
            pool.mark_exhausted(credential, reason=reason)

    def get_all_stats(self) -> Dict[str, PoolStats]:
        """Get stats for all managed pools."""
        return {name: pool.get_stats() for name, pool in self._pools.items()}

    def has_available(self, provider: str) -> bool:
        """Check if a provider has any available credentials."""
        pool = self._pools.get(provider)
        return pool.has_available if pool else False

    # ── Auto-Discovery from Environment ────────────────────────────

    @classmethod
    def from_env(cls) -> CredentialManager:
        """Auto-detect credentials from environment variables.

        Reads:
            OPENAI_API_KEY, OPENAI_API_KEY_2, OPENAI_API_KEY_3, ...
            DEEPSEEK_API_KEY, DEEPSEEK_API_KEY_2, ...
            ANTHROPIC_API_KEY, ANTHROPIC_API_KEY_2, ...
            GOOGLE_API_KEY, GOOGLE_API_KEY_2, ...
            XAI_API_KEY, XAI_API_KEY_2, ...
            TOGETHER_API_KEY, TOGETHER_API_KEY_2, ...
            GROQ_API_KEY, GROQ_API_KEY_2, ...
            MISTRAL_API_KEY, MISTRAL_API_KEY_2, ...
            MOONSHOT_API_KEY, MOONSHOT_API_KEY_2, ...
            OPENROUTER_API_KEY, OPENROUTER_API_KEY_2, ...
            COHERE_API_KEY, COHERE_API_KEY_2, ...
            PERPLEXITY_API_KEY, PERPLEXITY_API_KEY_2, ...
        """
        import os

        mgr = cls()

        provider_prefixes = [
            ("openai", "OPENAI_API_KEY"),
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("google", "GOOGLE_API_KEY"),
            ("xai", "XAI_API_KEY"),
            ("together", "TOGETHER_API_KEY"),
            ("groq", "GROQ_API_KEY"),
            ("mistral", "MISTRAL_API_KEY"),
            ("moonshot", "MOONSHOT_API_KEY"),
            ("openrouter", "OPENROUTER_API_KEY"),
            ("cohere", "COHERE_API_KEY"),
            ("perplexity", "PERPLEXITY_API_KEY"),
            ("fireworks", "FIREWORKS_API_KEY"),
            ("replicate", "REPLICATE_API_KEY"),
        ]

        for provider, env_prefix in provider_prefixes:
            keys = []
            primary = os.getenv(env_prefix, "")
            if primary:
                keys.append(primary)

            for i in range(2, 10):
                extra = os.getenv(f"{env_prefix}_{i}", "")
                if extra:
                    keys.append(extra)
                else:
                    break

            if keys:
                mgr.add_pool(provider, keys)

        return mgr

    # ── Singleton Access ───────────────────────────────────────────

    _instance: Optional[CredentialManager] = None
    _instance_lock = threading.Lock()

    @classmethod
    def global_instance(cls) -> CredentialManager:
        """Get or create the global singleton CredentialManager.

        Lazy-initializes from environment variables on first access.
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls.from_env()
                    logger.info("CredentialManager: global instance initialized from env")
        return cls._instance
