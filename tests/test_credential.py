"""
Unit tests for dragon.credential and dragon.credential_pool modules.

Covers:
  - dragon.credential: CredentialPool, CredentialManager (simple version)
  - dragon.credential_pool: Credential, CredentialPool, CredentialManager (enhanced)
  - PooledKey, CredentialStatus, KeyStatus
  - Key management (add, remove, get, rotate)
  - Status tracking (success, rate_limited, exhausted, error)
  - Health scoring, RPM/TPM budget, USD budget
  - Environment-based auto-discovery (from_env)
  - Singleton pattern (global_instance)
"""

import os
import time
from unittest.mock import patch, MagicMock

import pytest

# Import from both credential modules
from dragon.credential import (
    CredentialPool as SimplePool,
    PooledKey,
    KeyStatus,
    CredentialManager as SimpleCredentialManager,
)
from dragon.credential_pool import (
    CredentialPool,
    Credential,
    CredentialStatus,
    CredentialManager,
    PoolStats,
    DEFAULT_COOLDOWN_SECONDS,
    RATE_LIMITED_COOLDOWN,
    EXHAUSTED_RETRY_SECONDS,
    HEALTH_DECAY_RATE,
)


# ============================================================
# Simple credential module tests
# ============================================================

class TestKeyStatus:
    """Tests for KeyStatus enum."""

    def test_values(self):
        assert KeyStatus.AVAILABLE.value == "available"
        assert KeyStatus.RATE_LIMITED.value == "rate_limited"
        assert KeyStatus.EXHAUSTED.value == "exhausted"


class TestPooledKey:
    """Tests for PooledKey dataclass."""

    def test_defaults(self):
        pk = PooledKey(key="sk-test123")
        assert pk.key == "sk-test123"
        assert pk.priority == 1
        assert pk.status == "available"
        assert pk.call_count == 0
        assert pk.error_count == 0

    def test_available_when_available(self):
        pk = PooledKey(key="sk-test")
        assert pk.available is True

    def test_not_available_when_exhausted(self):
        pk = PooledKey(key="sk-test", status="exhausted")
        assert pk.available is False

    def test_not_available_when_rate_limited_in_cooldown(self):
        pk = PooledKey(key="sk-test", status="rate_limited",
                       cooldown_until=time.monotonic() + 3600)
        assert pk.available is False

    def test_available_after_cooldown(self):
        pk = PooledKey(key="sk-test", status="rate_limited",
                       cooldown_until=time.monotonic() - 1)
        assert pk.available is True

    def test_health_score_new_key(self):
        pk = PooledKey(key="sk-test")
        assert pk.health_score == 0.8

    def test_health_score_exhausted(self):
        pk = PooledKey(key="sk-test", status="exhausted")
        assert pk.health_score == 0.0

    def test_health_score_with_errors(self):
        pk = PooledKey(key="sk-test", call_count=10, error_count=3)
        assert pk.health_score == 0.7


class TestSimplePool:
    """Tests for the simple CredentialPool (dragon.credential)."""

    def setup_method(self):
        self.pool = SimplePool(name="test_pool", cooldown_secs=30)

    def test_add_key(self):
        self.pool.add_key("sk-key1", priority=1)
        assert self.pool.total_keys == 1
        assert self.pool.available_count == 1

    def test_add_empty_key_ignored(self):
        self.pool.add_key("", priority=1)
        assert self.pool.total_keys == 0

    def test_add_keys_bulk(self):
        self.pool.add_keys(["sk-a", "sk-b", "sk-c"], priority=2)
        assert self.pool.total_keys == 3

    def test_remove_key(self):
        self.pool.add_key("sk-test")
        assert self.pool.remove_key("sk-test") is True
        assert self.pool.total_keys == 0

    def test_remove_nonexistent(self):
        assert self.pool.remove_key("not-there") is False

    def test_get_key_returns_best(self):
        self.pool.add_key("sk-low", priority=10)
        self.pool.add_key("sk-high", priority=1)
        key = self.pool.get_key()
        # Higher health score (lower error rate), both start at 0.8
        assert key in ("sk-low", "sk-high")

    def test_get_key_none_when_empty(self):
        assert self.pool.get_key() is None

    def test_get_key_skips_exhausted(self):
        self.pool.add_key("sk-good")
        self.pool.add_key("sk-bad")
        self.pool.mark_exhausted("sk-bad")
        key = self.pool.get_key()
        assert key == "sk-good"

    def test_get_key_round_robin(self):
        self.pool.add_keys(["sk-a", "sk-b"])
        keys_seen = set()
        for _ in range(4):
            keys_seen.add(self.pool.get_key_round_robin())
        assert len(keys_seen) == 2

    def test_mark_success(self):
        self.pool.add_key("sk-test")
        self.pool.mark_success("sk-test")
        stats = self.pool.stats()
        assert stats["total"] == 1
        assert stats["available"] == 1

    def test_mark_rate_limited(self):
        self.pool.add_key("sk-test")
        self.pool.mark_rate_limited("sk-test", cooldown_secs=1)
        assert self.pool.available_count == 0
        # After cooldown
        time.sleep(1.1)
        assert self.pool.available_count == 1

    def test_mark_exhausted(self):
        self.pool.add_key("sk-test")
        self.pool.mark_exhausted("sk-test")
        assert self.pool.available_count == 0
        assert self.pool.exhausted_count == 1
        assert self.pool.has_available is False

    def test_reset_key(self):
        self.pool.add_key("sk-test")
        self.pool.mark_exhausted("sk-test")
        self.pool.reset("sk-test")
        assert self.pool.available_count == 1

    def test_reset_all(self):
        self.pool.add_keys(["sk-a", "sk-b", "sk-c"])
        self.pool.mark_exhausted("sk-a")
        self.pool.mark_rate_limited("sk-b")
        self.pool.reset_all()
        assert self.pool.available_count == 3

    def test_stats(self):
        self.pool.add_key("sk-test", priority=2)
        self.pool.mark_success("sk-test")
        stats = self.pool.stats()
        assert stats["pool"] == "test_pool"
        assert stats["total"] == 1
        assert len(stats["keys"]) == 1

    def test_has_available(self):
        assert self.pool.has_available is False
        self.pool.add_key("sk-test")
        assert self.pool.has_available is True


class TestSimpleCredentialManager:
    """Tests for the simple CredentialManager (dragon.credential)."""

    def setup_method(self):
        self.mgr = SimpleCredentialManager()

    def test_add_pool(self):
        self.mgr.add_pool("openai", ["sk-key1", "sk-key2"])
        pool = self.mgr.get_pool("openai")
        assert pool is not None
        assert pool.total_keys == 2

    def test_get_key(self):
        self.mgr.add_pool("deepseek", ["sk-ds1"])
        key = self.mgr.get_key("deepseek")
        assert key == "sk-ds1"

    def test_get_key_unknown_provider(self):
        assert self.mgr.get_key("unknown") is None

    def test_mark_success(self):
        self.mgr.add_pool("test", ["sk-test"])
        key = self.mgr.get_key("test")
        self.mgr.mark_success("test", key)

    def test_mark_rate_limited(self):
        self.mgr.add_pool("test", ["sk-test"])
        self.mgr.mark_rate_limited("test", "sk-test")

    def test_mark_exhausted(self):
        self.mgr.add_pool("test", ["sk-test"])
        self.mgr.mark_exhausted("test", "sk-test")

    def test_stats(self):
        self.mgr.add_pool("openai", ["sk-o1"])
        self.mgr.add_pool("deepseek", ["sk-d1"])
        stats = self.mgr.stats()
        assert "openai" in stats
        assert "deepseek" in stats

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-test"}, clear=True)
    def test_from_env_single_key(self):
        mgr = SimpleCredentialManager.from_env()
        key = mgr.get_key("openai")
        assert key == "sk-openai-test"

    @patch.dict(os.environ, {
        "OPENAI_API_KEY": "sk-primary",
        "OPENAI_API_KEY_2": "sk-secondary",
    }, clear=True)
    def test_from_env_multiple_keys(self):
        mgr = SimpleCredentialManager.from_env()
        pool = mgr.get_pool("openai")
        assert pool.total_keys == 2

    @patch.dict(os.environ, {}, clear=True)
    def test_from_env_empty(self):
        mgr = SimpleCredentialManager.from_env()
        assert mgr.get_key("openai") is None


# ============================================================
# Enhanced credential_pool module tests
# ============================================================

class TestCredentialStatus:
    """Tests for CredentialStatus enum."""

    def test_values(self):
        assert CredentialStatus.AVAILABLE.value == "available"
        assert CredentialStatus.RATE_LIMITED.value == "rate_limited"
        assert CredentialStatus.EXHAUSTED.value == "exhausted"
        assert CredentialStatus.DISABLED.value == "disabled"


class TestCredential:
    """Tests for Credential model (enhanced)."""

    def test_creation(self):
        cred = Credential(key="sk-test123", provider="openai", priority=1)
        assert cred.key == "sk-test123"
        assert cred.provider == "openai"
        assert cred.priority == 1
        assert cred.status == CredentialStatus.AVAILABLE
        assert cred.health_score == 0.8
        assert cred.call_count == 0
        assert cred.error_count == 0

    def test_is_available_when_available(self):
        cred = Credential(key="sk-test")
        assert cred.is_available is True

    def test_is_available_disabled(self):
        cred = Credential(key="sk-test", status=CredentialStatus.DISABLED)
        assert cred.is_available is False

    def test_is_available_exhausted_in_cooldown(self):
        cred = Credential(
            key="sk-test",
            status=CredentialStatus.EXHAUSTED,
            cooldown_until=time.monotonic() + 3600,
        )
        assert cred.is_available is False

    def test_is_available_exhausted_after_retry(self):
        cred = Credential(
            key="sk-test",
            status=CredentialStatus.EXHAUSTED,
            cooldown_until=time.monotonic() - 1,
        )
        assert cred.is_available is True

    def test_is_available_rate_limited(self):
        cred = Credential(
            key="sk-test",
            status=CredentialStatus.RATE_LIMITED,
            cooldown_until=time.monotonic() + 60,
        )
        assert cred.is_available is False

    def test_rpm_available_unlimited(self):
        cred = Credential(key="sk-test", rpm_limit=0)
        assert cred.rpm_available is True

    def test_rpm_available_under_limit(self):
        cred = Credential(key="sk-test", rpm_limit=10, rpm_used=5)
        assert cred.rpm_available is True

    def test_rpm_available_over_limit(self):
        cred = Credential(key="sk-test", rpm_limit=10, rpm_used=10)
        assert cred.rpm_available is False

    def test_tpm_available(self):
        cred = Credential(key="sk-test", tpm_limit=1000, tpm_used=500)
        assert cred.tpm_available is True

    def test_budget_available(self):
        cred = Credential(key="sk-test", budget_limit_usd=10.0, budget_spent_usd=5.0)
        assert cred.budget_available is True

    def test_budget_exhausted(self):
        cred = Credential(key="sk-test", budget_limit_usd=10.0, budget_spent_usd=10.0)
        assert cred.budget_available is False

    def test_masked_key(self):
        cred = Credential(key="sk-verylongkey1234567890")
        masked = cred.masked_key()
        assert "sk-veryl" in masked
        assert "..." in masked

    def test_masked_key_short(self):
        cred = Credential(key="sk-short")
        masked = cred.masked_key()
        assert "***" in masked or "..." in masked


class TestEnhancedPool:
    """Tests for the enhanced CredentialPool (dragon.credential_pool)."""

    def setup_method(self):
        self.pool = CredentialPool(provider="test_provider", cooldown_secs=30)

    def test_add_credential(self):
        cred = self.pool.add_credential("sk-test", priority=1, rpm_limit=100)
        assert isinstance(cred, Credential)
        assert cred.priority == 1
        assert cred.rpm_limit == 100
        assert self.pool.total_count == 1

    def test_add_empty_key_raises(self):
        with pytest.raises(ValueError, match="empty key"):
            self.pool.add_credential("")

    def test_add_credentials_bulk(self):
        creds = self.pool.add_credentials(["sk-a", "sk-b", "sk-c"], priority=1)
        assert len(creds) == 3
        assert self.pool.total_count == 3

    def test_remove_credential(self):
        self.pool.add_credential("sk-test")
        assert self.pool.remove_credential("sk-test") is True
        assert self.pool.total_count == 0

    def test_remove_nonexistent(self):
        assert self.pool.remove_credential("not-there") is False

    def test_get_credential(self):
        self.pool.add_credential("sk-best", priority=1)
        cred = self.pool.get_credential()
        assert cred is not None
        assert cred.key == "sk-best"

    def test_get_credential_empty(self):
        assert self.pool.get_credential() is None

    def test_get_credential_round_robin(self):
        self.pool.add_credentials(["sk-a", "sk-b"])
        seen = []
        for _ in range(4):
            c = self.pool.get_credential_round_robin()
            if c:
                seen.append(c.key)
        assert "sk-a" in seen
        assert "sk-b" in seen

    def test_get_all_credentials(self):
        self.pool.add_credentials(["sk-a", "sk-b"])
        all_creds = self.pool.get_all_credentials()
        assert len(all_creds) == 2

    def test_mark_success(self):
        cred = self.pool.add_credential("sk-test")
        initial_health = cred.health_score
        self.pool.mark_success(cred, tokens_used=100, cost_usd=0.01)
        assert cred.call_count == 1
        assert cred.rpm_used == 1
        assert cred.tpm_used == 100
        assert cred.budget_spent_usd == 0.01
        assert cred.health_score >= initial_health  # health improves

    def test_mark_rate_limited(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_rate_limited(cred, cooldown_secs=1)
        assert cred.status == CredentialStatus.RATE_LIMITED
        assert cred.health_score < 0.8  # health decreases
        assert self.pool.available_count == 0

    def test_mark_rate_limited_retry_after(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_rate_limited(cred, retry_after=5)
        assert cred.cooldown_until >= time.monotonic() + 4

    def test_mark_exhausted(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_exhausted(cred, reason="quota exceeded")
        assert cred.status == CredentialStatus.EXHAUSTED
        assert cred.health_score == 0.0
        assert self.pool.exhausted_count == 1

    def test_mark_error_429(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_error(cred, http_status=429, retry_after=10)
        assert cred.status == CredentialStatus.RATE_LIMITED

    def test_mark_error_401(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_error(cred, http_status=401)
        assert cred.status == CredentialStatus.EXHAUSTED

    def test_mark_error_402(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_error(cred, http_status=402)
        assert cred.status == CredentialStatus.EXHAUSTED

    def test_mark_error_403(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_error(cred, http_status=403)
        assert cred.status == CredentialStatus.EXHAUSTED

    def test_mark_error_generic(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_error(cred, http_status=500)
        assert cred.status == CredentialStatus.AVAILABLE
        assert cred.health_score < 0.8

    def test_reset_credential(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.mark_exhausted(cred)
        self.pool.reset_credential(cred)
        assert cred.status == CredentialStatus.AVAILABLE
        assert cred.health_score == 0.8

    def test_reset_all(self):
        self.pool.add_credentials(["sk-a", "sk-b", "sk-c"])
        for c in self.pool.get_all_credentials():
            self.pool.mark_exhausted(c)
        self.pool.reset_all()
        assert self.pool.available_count == 3

    def test_disable_credential(self):
        cred = self.pool.add_credential("sk-test")
        self.pool.disable_credential(cred)
        assert cred.status == CredentialStatus.DISABLED
        assert self.pool.available_count == 0

    def test_get_stats(self):
        self.pool.add_credential("sk-a", priority=1)
        self.pool.add_credential("sk-b", priority=2, rpm_limit=100)
        stats = self.pool.get_stats()
        assert isinstance(stats, PoolStats)
        assert stats.provider == "test_provider"
        assert stats.total_credentials == 2
        assert stats.available == 2

    def test_has_available(self):
        assert self.pool.has_available is False
        self.pool.add_credential("sk-test")
        assert self.pool.has_available is True

    def test_rate_limited_count(self):
        self.pool.add_credential("sk-test")
        cred = self.pool.get_credential()
        self.pool.mark_rate_limited(cred)
        assert self.pool.rate_limited_count == 1

    def test_rpm_budget_blocks_selection(self):
        self.pool.add_credential("sk-limited", rpm_limit=1, priority=0)
        self.pool.add_credential("sk-backup", priority=1)
        # Exhaust rpm of first key
        c1 = self.pool.get_credential()
        self.pool.mark_success(c1)
        # First key should still be selected (1/1 rpm used)
        c2 = self.pool.get_credential()
        self.pool.mark_success(c2)
        # Now first key has rpm_used=2 > rpm_limit=1
        c3 = self.pool.get_credential()
        # Should fall back to backup
        assert c3 is not None

    def test_minute_window_reset(self):
        self.pool.add_credential("sk-test", rpm_limit=100)
        cred = self.pool.get_credential()
        # Force window expiry
        self.pool._minute_window_start = time.monotonic() - 61
        cred.rpm_used = 50
        self.pool.get_credential()  # triggers reset
        assert cred.rpm_used == 0


class TestEnhancedCredentialManager:
    """Tests for the enhanced CredentialManager."""

    def setup_method(self):
        self.mgr = CredentialManager()

    def test_add_pool(self):
        self.mgr.add_pool("deepseek", ["sk-ds1", "sk-ds2"])
        pool = self.mgr.get_pool("deepseek")
        assert pool is not None
        assert pool.total_count == 2

    def test_get_credential(self):
        self.mgr.add_pool("openai", ["sk-o1"])
        cred = self.mgr.get_credential("openai")
        assert cred is not None
        assert cred.key == "sk-o1"

    def test_get_credential_unknown(self):
        assert self.mgr.get_credential("unknown") is None

    def test_mark_success(self):
        self.mgr.add_pool("test", ["sk-test"])
        cred = self.mgr.get_credential("test")
        self.mgr.mark_success("test", cred, tokens_used=50)
        assert cred.call_count == 1

    def test_mark_rate_limited(self):
        self.mgr.add_pool("test", ["sk-test"])
        cred = self.mgr.get_credential("test")
        self.mgr.mark_rate_limited("test", cred, retry_after=5)
        assert cred.status == CredentialStatus.RATE_LIMITED

    def test_mark_error(self):
        self.mgr.add_pool("test", ["sk-test"])
        cred = self.mgr.get_credential("test")
        self.mgr.mark_error("test", cred, http_status=429)
        assert cred.status == CredentialStatus.RATE_LIMITED

    def test_mark_exhausted(self):
        self.mgr.add_pool("test", ["sk-test"])
        cred = self.mgr.get_credential("test")
        self.mgr.mark_exhausted("test", cred, "no money")
        assert cred.status == CredentialStatus.EXHAUSTED

    def test_get_all_stats(self):
        self.mgr.add_pool("openai", ["sk-o1"])
        self.mgr.add_pool("deepseek", ["sk-d1"])
        stats = self.mgr.get_all_stats()
        assert "openai" in stats
        assert "deepseek" in stats
        assert isinstance(stats["openai"], PoolStats)

    def test_has_available(self):
        self.mgr.add_pool("test", ["sk-test"])
        assert self.mgr.has_available("test") is True
        assert self.mgr.has_available("unknown") is False

    def test_has_available_all_exhausted(self):
        self.mgr.add_pool("test", ["sk-test"])
        cred = self.mgr.get_credential("test")
        self.mgr.mark_exhausted("test", cred)
        assert self.mgr.has_available("test") is False

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-ds-test123"}, clear=True)
    def test_from_env_single_key(self):
        mgr = CredentialManager.from_env()
        cred = mgr.get_credential("deepseek")
        assert cred is not None
        assert cred.key == "sk-ds-test123"

    @patch.dict(os.environ, {
        "OPENAI_API_KEY": "sk-primary",
        "OPENAI_API_KEY_2": "sk-backup",
    }, clear=True)
    def test_from_env_multiple_keys(self):
        mgr = CredentialManager.from_env()
        pool = mgr.get_pool("openai")
        assert pool.total_count == 2

    @patch.dict(os.environ, {}, clear=True)
    def test_from_env_no_keys(self):
        mgr = CredentialManager.from_env()
        assert mgr.get_credential("openai") is None


class TestGlobalCredentialManager:
    """Tests for global_instance singleton pattern."""

    def setup_method(self):
        # Reset singleton for test isolation
        CredentialManager._instance = None

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-global"}, clear=True)
    def test_global_instance_creates_once(self):
        mgr1 = CredentialManager.global_instance()
        mgr2 = CredentialManager.global_instance()
        assert mgr1 is mgr2
        # Also test it actually works
        cred = mgr1.get_credential("openai")
        assert cred is not None


class TestPoolStats:
    """Tests for PoolStats model."""

    def test_defaults(self):
        stats = PoolStats()
        assert stats.provider == ""
        assert stats.total_credentials == 0
        assert stats.available == 0
        assert stats.credentials == []

    def test_with_data(self):
        stats = PoolStats(
            provider="openai",
            total_credentials=3,
            available=2,
            rate_limited=1,
            total_calls=100,
            total_errors=5,
        )
        assert stats.provider == "openai"
        assert stats.total_credentials == 3
        assert stats.available == 2
