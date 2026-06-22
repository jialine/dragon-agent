"""
Dragon Credential Pool — Multi-key rotation with automatic fallback.

When one API key hits rate limits or exhausts, the pool rotates to the next.
Supports exhaustion tracking and automatic cooldown.

Usage::

    from dragon.credential import CredentialPool

    pool = CredentialPool("openai")
    pool.add_key("sk-key1", priority=1)
    pool.add_key("sk-key2", priority=2)
    pool.add_key("sk-key3", priority=3)

    key = pool.get_key()         # returns the best available key
    pool.mark_success(key)       # key worked fine
    pool.mark_rate_limited(key)  # key hit rate limit, cooldown for 60s
    pool.mark_exhausted(key)     # key is out of quota
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dragon.credential")


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

class KeyStatus(Enum):
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"   # temporary, auto-recover
    EXHAUSTED = "exhausted"         # permanently out of quota


@dataclass
class PooledKey:
    key: str
    priority: int = 1                 # lower = higher priority
    status: str = "available"
    cooldown_until: float = 0.0       # timestamp when key becomes available again
    call_count: int = 0
    error_count: int = 0
    last_used_at: float = 0.0

    @property
    def available(self) -> bool:
        if self.status == "exhausted":
            return False
        if self.status == "rate_limited" and time.monotonic() < self.cooldown_until:
            return False
        return True

    @property
    def health_score(self) -> float:
        """0-1 score. Higher = healthier."""
        if self.status == "exhausted":
            return 0.0
        if self.call_count == 0:
            return 0.8
        error_rate = self.error_count / max(1, self.call_count)
        return max(0.0, 1.0 - error_rate)


# ────────────────────────────────────────────────────────────────────
# Credential Pool
# ────────────────────────────────────────────────────────────────────


class CredentialPool:
    """Multi-key credential pool with rotation and health tracking.

    Parameters
    ----------
    name : str
        Pool name (e.g., "openai", "deepseek").
    cooldown_secs : float
        Default cooldown for rate-limited keys (seconds).
    """

    def __init__(
        self,
        name: str = "default",
        cooldown_secs: float = 60.0,
    ) -> None:
        self.name = name
        self.cooldown_secs = cooldown_secs
        self._keys: Dict[str, PooledKey] = {}
        self._lock = threading.Lock()
        self._rotation_index: int = 0
        logger.info("CredentialPool '%s' initialized", name)

    # ── Key Management ────────────────────────────────────────────

    def add_key(self, key: str, priority: int = 1) -> None:
        """Add a key to the pool."""
        if not key:
            return
        key_id = self._key_id(key)
        with self._lock:
            self._keys[key_id] = PooledKey(key=key, priority=priority)
        logger.debug("Added key to pool '%s': %s... (priority=%d)", self.name, key[:8], priority)

    def add_keys(self, keys: List[str], priority: int = 1) -> None:
        for k in keys:
            self.add_key(k, priority)

    def remove_key(self, key: str) -> bool:
        key_id = self._key_id(key)
        with self._lock:
            if key_id in self._keys:
                del self._keys[key_id]
                return True
        return False

    # ── Key Selection ─────────────────────────────────────────────

    def get_key(self) -> Optional[str]:
        """Get the best available key.

        Priority order:
        1. Available keys sorted by health_score desc, then priority asc
        2. Rate-limited keys that have cooled down
        3. None if all exhausted
        """
        with self._lock:
            available = [
                k for k in self._keys.values()
                if k.available and k.status != "exhausted"
            ]

            if not available:
                return None

            # Sort: healthiest first, then by priority
            available.sort(key=lambda k: (-k.health_score, k.priority))

            return available[0].key

    def get_key_round_robin(self) -> Optional[str]:
        """Get next key in round-robin order."""
        with self._lock:
            available = [k for k in self._keys.values() if k.available]
            if not available:
                return None

            self._rotation_index = (self._rotation_index + 1) % len(available)
            return available[self._rotation_index].key

    # ── Status Tracking ───────────────────────────────────────────

    def mark_success(self, key: str) -> None:
        """Mark a key as successfully used."""
        key_id = self._key_id(key)
        with self._lock:
            if key_id in self._keys:
                k = self._keys[key_id]
                k.call_count += 1
                k.last_used_at = time.monotonic()

    def mark_rate_limited(self, key: str, cooldown_secs: Optional[float] = None) -> None:
        """Mark a key as rate-limited (temporary)."""
        key_id = self._key_id(key)
        cooldown = cooldown_secs or self.cooldown_secs

        with self._lock:
            if key_id in self._keys:
                k = self._keys[key_id]
                k.status = KeyStatus.RATE_LIMITED.value
                k.cooldown_until = time.monotonic() + cooldown
                k.error_count += 1
                k.call_count += 1
                logger.warning(
                    "Key %s... rate-limited in pool '%s', cooldown %.0fs",
                    key[:8], self.name, cooldown,
                )

    def mark_exhausted(self, key: str) -> None:
        """Mark a key as permanently exhausted (out of quota)."""
        key_id = self._key_id(key)
        with self._lock:
            if key_id in self._keys:
                k = self._keys[key_id]
                k.status = KeyStatus.EXHAUSTED.value
                k.error_count += 1
                k.call_count += 1
                logger.warning(
                    "Key %s... EXHAUSTED in pool '%s'. %d keys remaining.",
                    key[:8], self.name, self.available_count,
                )

    def reset(self, key: str) -> None:
        """Reset a key back to available."""
        key_id = self._key_id(key)
        with self._lock:
            if key_id in self._keys:
                k = self._keys[key_id]
                k.status = KeyStatus.AVAILABLE.value
                k.cooldown_until = 0.0
                logger.info("Key %s... reset to available in pool '%s'", key[:8], self.name)

    def reset_all(self) -> None:
        """Reset all keys to available."""
        with self._lock:
            for k in self._keys.values():
                k.status = KeyStatus.AVAILABLE.value
                k.cooldown_until = 0.0
        logger.info("All keys reset in pool '%s'", self.name)

    # ── Properties ─────────────────────────────────────────────────

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    @property
    def available_count(self) -> int:
        return sum(1 for k in self._keys.values() if k.available)

    @property
    def exhausted_count(self) -> int:
        return sum(1 for k in self._keys.values() if k.status == "exhausted")

    @property
    def has_available(self) -> bool:
        return self.available_count > 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            keys_info = []
            for k in sorted(self._keys.values(), key=lambda k: k.priority):
                keys_info.append({
                    "key_preview": k.key[:8] + "...",
                    "priority": k.priority,
                    "status": k.status,
                    "health": round(k.health_score, 2),
                    "calls": k.call_count,
                    "errors": k.error_count,
                })

        return {
            "pool": self.name,
            "total": self.total_keys,
            "available": self.available_count,
            "rate_limited": sum(1 for k in self._keys.values() if k.status == "rate_limited"),
            "exhausted": self.exhausted_count,
            "keys": keys_info,
        }

    @staticmethod
    def _key_id(key: str) -> str:
        """Generate a short ID from a key."""
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# ────────────────────────────────────────────────────────────────────
# Multi-Pool Manager
# ────────────────────────────────────────────────────────────────────


class CredentialManager:
    """Manage multiple credential pools for different providers.

    Usage::

        mgr = CredentialManager()
        mgr.add_pool("openai", ["sk-key1", "sk-key2"])
        mgr.add_pool("deepseek", ["sk-ds1"])

        key = mgr.get_key("openai")
        mgr.mark_success("openai", key)
    """

    def __init__(self) -> None:
        self._pools: Dict[str, CredentialPool] = {}

    def add_pool(self, name: str, keys: List[str], cooldown_secs: float = 60.0) -> CredentialPool:
        pool = CredentialPool(name=name, cooldown_secs=cooldown_secs)
        pool.add_keys(keys)
        self._pools[name] = pool
        return pool

    def get_pool(self, name: str) -> Optional[CredentialPool]:
        return self._pools.get(name)

    def get_key(self, provider: str) -> Optional[str]:
        pool = self._pools.get(provider)
        return pool.get_key() if pool else None

    def mark_success(self, provider: str, key: str) -> None:
        pool = self._pools.get(provider)
        if pool:
            pool.mark_success(key)

    def mark_rate_limited(self, provider: str, key: str) -> None:
        pool = self._pools.get(provider)
        if pool:
            pool.mark_rate_limited(key)

    def mark_exhausted(self, provider: str, key: str) -> None:
        pool = self._pools.get(provider)
        if pool:
            pool.mark_exhausted(key)

    def stats(self) -> Dict[str, Any]:
        return {name: pool.stats() for name, pool in self._pools.items()}

    @classmethod
    def from_env(cls) -> "CredentialManager":
        """Auto-detect keys from environment variables.

        Reads:
        - OPENAI_API_KEY, OPENAI_API_KEY_2, OPENAI_API_KEY_3
        - DEEPSEEK_API_KEY, DEEPSEEK_API_KEY_2
        - ANTHROPIC_API_KEY, ANTHROPIC_API_KEY_2
        """
        import os
        mgr = cls()

        for provider, env_prefix in [
            ("openai", "OPENAI_API_KEY"),
            ("deepseek", "DEEPSEEK_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("google", "GOOGLE_API_KEY"),
            ("xai", "XAI_API_KEY"),
        ]:
            keys = []
            # Primary key
            primary = os.getenv(env_prefix, "")
            if primary:
                keys.append(primary)
            # Additional keys: OPENAI_API_KEY_2, OPENAI_API_KEY_3, ...
            for i in range(2, 10):
                extra = os.getenv(f"{env_prefix}_{i}", "")
                if extra:
                    keys.append(extra)
                else:
                    break

            if keys:
                mgr.add_pool(provider, keys)

        return mgr
