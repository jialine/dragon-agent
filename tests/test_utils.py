"""
Tests for dragon.utils — covering __init__.py exports, cost helper functions,
CostOptimizer methods not already tested in test_cost.py, and singleton.

Tests added for: UsageEntry, _get_default_db_path, _init_db, get_today_usage,
get_daily_breakdown, __repr__, should_skip_exploration, get_cost_optimizer,
select_model with prefer_cheapest=False, and more.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import dragon.utils
from dragon.utils import __all__ as utils_all
from dragon.utils.cost import CostOptimizer, MODEL_TIERS
from dragon.utils.cost import (
    UsageEntry,
    _get_default_db_path,
    _init_db,
    _calculate_cost,
    _calculate_cost_for_model,
    _find_tier_for_model,
    is_trivial_query,
    get_cost_optimizer,
)


# ── dragon.utils.__init__ exports ───────────────────────────────────────

class TestUtilsInit:
    """Test that the utils package exports the right symbols."""

    def test_all_exports_cost_optimizer(self):
        assert "CostOptimizer" in utils_all

    def test_all_exports_model_tiers(self):
        assert "MODEL_TIERS" in utils_all

    def test_cost_optimizer_importable(self):
        from dragon.utils.cost import CostOptimizer as CO
        assert CO is CostOptimizer

    def test_model_tiers_importable(self):
        from dragon.utils.cost import MODEL_TIERS as MT
        assert MT is MODEL_TIERS


# ── UsageEntry ─────────────────────────────────────────────────────────

class TestUsageEntry:
    """Test UsageEntry dataclass."""

    def test_create_usage_entry(self):
        entry = UsageEntry(
            date="2026-05-20",
            model="deepseek-chat",
            tokens_in=500,
            tokens_out=200,
            cost_usd=0.0005,
            timestamp="2026-05-20T12:00:00+00:00",
        )
        assert entry.date == "2026-05-20"
        assert entry.model == "deepseek-chat"
        assert entry.tokens_in == 500
        assert entry.tokens_out == 200
        assert entry.cost_usd == pytest.approx(0.0005)
        assert entry.timestamp == "2026-05-20T12:00:00+00:00"

    def test_usage_entry_defaults(self):
        entry = UsageEntry(
            date="",
            model="",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            timestamp="",
        )
        assert entry.tokens_in == 0
        assert entry.cost_usd == 0.0


# ── _get_default_db_path ──────────────────────────────────────────────

class TestGetDefaultDbPath:
    """Test _get_default_db_path resolution."""

    def test_returns_string(self):
        path = _get_default_db_path()
        assert isinstance(path, str)

    def test_ends_with_cost_db(self):
        path = _get_default_db_path()
        assert path.endswith("cost.db")

    def test_contains_dragon_data(self):
        path = _get_default_db_path()
        assert "dragon_data" in path

    def test_is_absolute(self):
        path = _get_default_db_path()
        assert os.path.isabs(path)


# ── _init_db ──────────────────────────────────────────────────────────

class TestInitDb:
    """Test _init_db database initialization."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_cost.db")

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.tmpdir)

    def test_creates_table(self):
        _init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='usage'"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "usage"
        finally:
            conn.close()

    def test_creates_indexes(self):
        _init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            indexes = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            index_names = [r[0] for r in indexes]
            assert "idx_usage_date" in index_names
            assert "idx_usage_model" in index_names
        finally:
            conn.close()

    def test_idempotent(self):
        """Calling _init_db twice does not raise errors."""
        _init_db(self.db_path)
        _init_db(self.db_path)  # Should not raise

    def test_table_has_correct_columns(self):
        _init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("PRAGMA table_info(usage)")
            columns = {row[1] for row in cursor.fetchall()}
            expected = {"id", "date", "model", "tokens_in", "tokens_out", "cost_usd", "timestamp"}
            assert columns == expected
        finally:
            conn.close()


# ── CostOptimizer: Additional Methods ──────────────────────────────────

class TestCostOptimizerAdditional:
    """Test CostOptimizer methods not covered by test_cost.py."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cost_test.db")

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.tmpdir)

    def test_repr(self):
        opt = CostOptimizer(daily_budget=2.5, db_path=self.db_path)
        r = repr(opt)
        assert "CostOptimizer" in r
        assert "2.50" in r
        assert "remaining" in r

    def test_should_skip_exploration_trivial(self):
        opt = CostOptimizer(db_path=self.db_path)
        assert opt.should_skip_exploration("你好") is True
        assert opt.should_skip_exploration("hello") is True

    def test_should_skip_exploration_nontrivial(self):
        opt = CostOptimizer(db_path=self.db_path)
        assert opt.should_skip_exploration("如何评估一家AI公司？") is False

    def test_get_today_usage_empty(self):
        opt = CostOptimizer(db_path=self.db_path)
        usage = opt.get_today_usage()
        assert isinstance(usage, list)
        assert len(usage) == 0

    def test_get_today_usage_with_data(self):
        opt = CostOptimizer(db_path=self.db_path)
        opt.record_usage("deepseek-chat", tokens_in=1000, tokens_out=500)
        opt.record_usage("gpt-4o", tokens_in=200, tokens_out=100)

        usage = opt.get_today_usage()
        assert len(usage) == 2
        assert all(isinstance(u, UsageEntry) for u in usage)
        assert usage[0].model == "deepseek-chat"
        assert usage[1].model == "gpt-4o"

    def test_get_daily_breakdown(self):
        opt = CostOptimizer(db_path=self.db_path)
        opt.record_usage("deepseek-chat", tokens_in=1000, tokens_out=500)

        breakdown = opt.get_daily_breakdown(days=7)
        assert isinstance(breakdown, list)
        assert len(breakdown) >= 1
        assert "date" in breakdown[0]
        assert "cost_usd" in breakdown[0]
        assert "calls" in breakdown[0]

    def test_get_daily_breakdown_custom_days(self):
        opt = CostOptimizer(db_path=self.db_path)
        breakdown = opt.get_daily_breakdown(days=3)
        assert isinstance(breakdown, list)

    def test_reset_if_new_day_noop_same_day(self):
        """_reset_if_new_day should not change state on same day."""
        opt = CostOptimizer(daily_budget=2.0, db_path=self.db_path)
        original_today = opt._today
        original_spent = opt._spent_today
        opt._reset_if_new_day()
        assert opt._today == original_today
        assert opt._spent_today == original_spent

    def test_reset_if_new_day_different_day(self):
        """_reset_if_new_day resets when day changes."""
        opt = CostOptimizer(daily_budget=2.0, db_path=self.db_path)
        opt._spent_today = 1.5  # Simulate spending
        # Force day change
        opt._today = "1999-01-01"
        opt._reset_if_new_day()
        # Should reload from DB (which is empty for 1999-01-01)
        assert opt._spent_today == 0.0

    def test_load_spent_today(self):
        opt = CostOptimizer(db_path=self.db_path)
        opt.record_usage("deepseek-chat", tokens_in=1000000, tokens_out=1000000)
        spent = opt._load_spent_today()
        assert spent > 0

    def test_select_model_prefer_cheapest_false(self):
        """select_model with prefer_cheapest=False returns last model in tier."""
        opt = CostOptimizer(db_path=self.db_path)
        model = opt.select_model(difficulty="medium", prefer_cheapest=False)
        # tier2_medium: ["deepseek-chat", "qwen3-14b"] → last is qwen3-14b
        assert model == "qwen3-14b"

    def test_select_model_simple_not_cheapest(self):
        opt = CostOptimizer(db_path=self.db_path)
        model = opt.select_model(difficulty="simple", prefer_cheapest=False)
        # tier1_small: ["qwen3-8b"] → only one, so it's the same
        assert model == "qwen3-8b"

    def test_select_model_complex_not_cheapest_with_budget(self):
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        model = opt.select_model(difficulty="complex", prefer_cheapest=False)
        # tier3_large: ["deepseek-reasoner"] → only one
        assert model == "deepseek-reasoner"

    def test_can_afford_unknown_tier(self):
        opt = CostOptimizer(db_path=self.db_path)
        assert opt.can_afford("nonexistent_tier") is False

    def test_can_afford_tier0_is_always_affordable(self):
        opt = CostOptimizer(daily_budget=0.0, db_path=self.db_path)
        opt._spent_today = 0.0
        assert opt.can_afford("tier0_local") is True

    def test_record_usage_thread_safety(self):
        """Record usage concurrently from multiple threads."""
        opt = CostOptimizer(daily_budget=100.0, db_path=self.db_path)
        errors = []

        def record():
            try:
                for _ in range(10):
                    opt.record_usage("deepseek-chat", tokens_in=100, tokens_out=50)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors: {errors}"

        # Verify all records are in the DB
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        conn.close()
        assert count == 50  # 5 threads × 10 iterations


# ── get_cost_optimizer Singleton ──────────────────────────────────────

class TestGetCostOptimizer:
    """Test the get_cost_optimizer singleton factory."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cost_singleton.db")

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.tmpdir)

    def test_returns_cost_optimizer(self):
        opt = get_cost_optimizer(daily_budget=5.0, db_path=self.db_path)
        assert isinstance(opt, CostOptimizer)
        assert opt.daily_budget == 5.0

    def test_singleton_returns_same_instance(self):
        opt1 = get_cost_optimizer(daily_budget=5.0, db_path=self.db_path)
        opt2 = get_cost_optimizer(daily_budget=10.0, db_path=self.db_path)
        # Should return the same singleton instance
        assert opt1 is opt2
        # Budget stays as first-created value
        assert opt1.daily_budget == 5.0

    def test_singleton_thread_safety(self):
        """Verify singleton is thread-safe."""
        import dragon.utils.cost as cost_module
        # Reset singleton for clean test
        cost_module._default_optimizer = None

        results = []

        def get_opt():
            opt = get_cost_optimizer(daily_budget=3.0, db_path=self.db_path)
            results.append(opt)

        threads = [threading.Thread(target=get_opt) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should be the same instance
        first = results[0]
        assert all(r is first for r in results)


# ── Additional Trivial Query Edge Cases ────────────────────────────────

class TestTrivialQueryEdgeCases:
    """Edge cases for is_trivial_query not covered by test_cost.py."""

    def test_none_query_handled_gracefully(self):
        # is_trivial_query handles None without raising
        result = is_trivial_query(None)  # type: ignore
        assert isinstance(result, bool)

    def test_whitespace_only_variants(self):
        assert is_trivial_query("  ") is True  # empty after strip is True for is_trivial
        assert is_trivial_query("\t\n") is True

    def test_long_but_trivial_interjections(self):
        # Pure interjections matching regex
        assert is_trivial_query("哈哈哈哈") is True
        assert is_trivial_query("嘿嘿嘿") is True

    def test_arithmetic_patterns(self):
        assert is_trivial_query("3 * 5 =") is True
        assert is_trivial_query("100-50") is True

    def test_case_insensitive_greetings(self):
        assert is_trivial_query("HELLO") is True
        assert is_trivial_query("Thanks") is True

    def test_date_pattern_variants(self):
        assert is_trivial_query("今天日期") is True
        assert is_trivial_query("今天星期几") is True


# ── Additional Tier Lookup Edge Cases ──────────────────────────────────

class TestTierLookupEdgeCases:
    """Edge cases for _find_tier_for_model."""

    def test_fuzzy_matching(self):
        # Should find via substring match
        tier = _find_tier_for_model("claude-sonnet-4-20250514")
        assert tier == "tier4_premium"

    def test_qwen3_14b(self):
        tier = _find_tier_for_model("qwen3-14b")
        assert tier == "tier2_medium"

    def test_qwen2_1_5b_local(self):
        tier = _find_tier_for_model("qwen2-1.5b")
        assert tier == "tier0_local"


# ── Additional Cost Calculation Edge Cases ─────────────────────────────

class TestCostCalculationEdgeCases:
    """Edge cases for cost calculation functions."""

    def test_zero_tokens_zero_cost(self):
        cost = _calculate_cost(0, 0, "tier4_premium")
        assert cost == 0.0

    def test_cost_for_model_zero_tokens(self):
        cost = _calculate_cost_for_model(0, 0, "deepseek-chat")
        assert cost == 0.0

    def test_cost_rounding(self):
        """Cost should be rounded to 8 decimal places."""
        cost = _calculate_cost(1, 1, "tier2_medium")
        # (1/1M * 0.27) + (1/1M * 1.10) = 0.00000137
        expected = round((1 / 1_000_000) * 0.27 + (1 / 1_000_000) * 1.10, 8)
        assert cost == pytest.approx(expected)


# ── CostOptimizer should_escalate Edge Cases ───────────────────────────

class TestShouldEscalateEdgeCases:
    """Edge cases for CostOptimizer.should_escalate."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cost_escalate.db")

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.tmpdir)

    def test_escalate_without_query_id(self):
        """Without query_id, escalate can fire unlimited times."""
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        for _ in range(10):
            assert opt.should_escalate(0.3, attempts=3) is True

    def test_escalate_exact_confidence_boundary(self):
        """Confidence exactly 0.5 should NOT escalate."""
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        assert opt.should_escalate(0.5, attempts=10) is False

    def test_escalate_exact_budget_boundary(self):
        """Budget exactly 0.01 should NOT escalate."""
        opt = CostOptimizer(daily_budget=0.011, db_path=self.db_path)
        opt._spent_today = 0.001  # remaining = 0.01
        assert opt.should_escalate(0.3, attempts=5) is False

    def test_escalate_with_override_budget(self):
        """Explicit budget_remaining parameter overrides live budget."""
        opt = CostOptimizer(daily_budget=0.001, db_path=self.db_path)
        opt._spent_today = 0.00099  # Live remaining is low
        # But override with high budget
        assert opt.should_escalate(0.3, attempts=5, budget_remaining=10.0) is True

    def test_escalate_different_query_ids_separate(self):
        """Escalation counts are per-query_id."""
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        assert opt.should_escalate(0.3, attempts=3, query_id="q1") is True
        assert opt.should_escalate(0.3, attempts=4, query_id="q1") is True
        assert opt.should_escalate(0.3, attempts=5, query_id="q1") is False  # capped
        # Different query_id gets its own 2-escalation limit
        assert opt.should_escalate(0.3, attempts=3, query_id="q2") is True
        assert opt.should_escalate(0.3, attempts=4, query_id="q2") is True
