"""
Unit tests for CostOptimizer and trivial query detection.
"""
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest
from panda.utils.cost import (
    CostOptimizer, is_trivial_query, MODEL_TIERS,
    _find_tier_for_model, _calculate_cost, _calculate_cost_for_model,
)


# ── Trivial Query Detection ──────────────────────────────────────────

class TestTrivialQueryDetection:
    def test_greeting_is_trivial(self):
        assert is_trivial_query("你好") is True
        assert is_trivial_query("hello") is True
        assert is_trivial_query("谢谢") is True
        assert is_trivial_query("再见") is True

    def test_empty_query_is_trivial(self):
        assert is_trivial_query("") is True
        assert is_trivial_query("   ") is True

    def test_very_short_is_trivial(self):
        assert is_trivial_query("ab") is True   # less than 5 chars

    def test_real_query_is_not_trivial(self):
        assert is_trivial_query("如何评估一家AI公司的投资价值？") is False
        assert is_trivial_query("What is the capital of France?") is False

    def test_simple_arithmetic_is_trivial(self):
        assert is_trivial_query("1+1=") is True
        assert is_trivial_query("2 + 2 =") is True

    def test_date_query_is_trivial(self):
        assert is_trivial_query("今天日期") is True
        assert is_trivial_query("现在时间") is True

    def test_interjection_only_is_trivial(self):
        assert is_trivial_query("噢") is True
        assert is_trivial_query("嗯嗯") is True


# ── Model Tier Lookup ────────────────────────────────────────────────

class TestModelTierLookup:
    def test_find_deepseek_chat_tier(self):
        tier = _find_tier_for_model("deepseek-chat")
        assert tier == "tier2_medium"

    def test_find_gpt4o_tier(self):
        tier = _find_tier_for_model("gpt-4o")
        assert tier == "tier4_premium"

    def test_find_qwen3_8b_tier(self):
        tier = _find_tier_for_model("qwen3-8b")
        assert tier == "tier1_small"

    def test_find_deepseek_reasoner_tier(self):
        tier = _find_tier_for_model("deepseek-reasoner")
        assert tier == "tier3_large"

    def test_find_unknown_model_returns_none(self):
        tier = _find_tier_for_model("unknown-model-xyz")
        assert tier is None

    def test_case_insensitive(self):
        tier = _find_tier_for_model("DEEPSEEK-CHAT")
        assert tier == "tier2_medium"


# ── Cost Calculation ─────────────────────────────────────────────────

class TestCostCalculation:
    def test_tier0_local_free(self):
        cost = _calculate_cost(1000, 500, "tier0_local")
        assert cost == 0.0

    def test_tier2_medium_cost(self):
        cost = _calculate_cost(1000000, 1000000, "tier2_medium")
        assert cost == pytest.approx(0.27 + 1.10, rel=1e-6)

    def test_tier4_premium_cost(self):
        cost = _calculate_cost(1000000, 1000000, "tier4_premium")
        assert cost == pytest.approx(2.50 + 10.00, rel=1e-6)

    def test_small_token_counts(self):
        cost = _calculate_cost(500, 200, "tier2_medium")
        expected = (500 / 1_000_000) * 0.27 + (200 / 1_000_000) * 1.10
        assert cost == pytest.approx(expected, rel=1e-6)

    def test_unknown_tier_returns_zero(self):
        cost = _calculate_cost(1000, 500, "nonexistent_tier")
        assert cost == 0.0


class TestCostForModel:
    def test_deepseek_chat_cost(self):
        cost = _calculate_cost_for_model(1000000, 1000000, "deepseek-chat")
        assert cost == pytest.approx(1.37, rel=1e-6)

    def test_unknown_model_cost(self):
        cost = _calculate_cost_for_model(1000, 500, "unknown-model")
        assert cost == 0.0


# ── CostOptimizer ────────────────────────────────────────────────────

class TestCostOptimizer:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "cost_test.db")

    def teardown_method(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.tmpdir)

    def test_initialization(self):
        opt = CostOptimizer(daily_budget=2.0, db_path=self.db_path)
        assert opt.daily_budget == 2.0
        assert opt.db_path == self.db_path
        assert opt.budget_remaining == 2.0

    def test_select_model_simple(self):
        opt = CostOptimizer(db_path=self.db_path)
        model = opt.select_model(difficulty="simple")
        assert model == "qwen3-8b"  # tier1_small cheapest

    def test_select_model_medium(self):
        opt = CostOptimizer(db_path=self.db_path)
        model = opt.select_model(difficulty="medium")
        assert model == "deepseek-chat"  # tier2_medium cheapest

    def test_select_model_complex_with_budget(self):
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        model = opt.select_model(difficulty="complex")
        assert model == "deepseek-reasoner"  # tier3_large

    def test_select_model_complex_low_budget_falls_back(self):
        # With zero budget, tier3 should be unaffordable → fall to tier2
        opt = CostOptimizer(daily_budget=0.0, db_path=self.db_path)
        opt._spent_today = 0.0  # exactly exhausted
        model = opt.select_model(difficulty="complex")
        assert model == "deepseek-chat"  # tier2 fallback

    def test_select_model_unknown_difficulty_safe_default(self):
        opt = CostOptimizer(db_path=self.db_path)
        model = opt.select_model(difficulty="impossible")
        assert model == "qwen3-8b"  # tier1_small safe fallback

    def test_record_usage_updates_budget(self):
        opt = CostOptimizer(daily_budget=1.0, db_path=self.db_path)
        cost = opt.record_usage("deepseek-chat", tokens_in=100000, tokens_out=50000)
        assert cost > 0
        assert opt.budget_remaining < 1.0

    def test_record_usage_tier0_no_cost(self):
        opt = CostOptimizer(daily_budget=1.0, db_path=self.db_path)
        cost = opt.record_usage("qwen3-0.6b", tokens_in=1000000, tokens_out=500000)
        assert cost == 0.0
        assert opt.budget_remaining == 1.0

    def test_can_afford_tier(self):
        opt = CostOptimizer(daily_budget=1.0, db_path=self.db_path)
        assert opt.can_afford("tier2_medium") is True

    def test_cannot_afford_when_budget_exhausted(self):
        opt = CostOptimizer(daily_budget=0.0001, db_path=self.db_path)
        opt._spent_today = 0.0001  # exactly exhausted
        assert opt.can_afford("tier4_premium") is False

    def test_should_escalate_low_confidence(self):
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        assert opt.should_escalate(0.3, attempts=2) is True

    def test_should_not_escalate_high_confidence(self):
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        assert opt.should_escalate(0.8, attempts=5) is False

    def test_should_not_escalate_too_few_attempts(self):
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        assert opt.should_escalate(0.3, attempts=1) is False

    def test_should_not_escalate_budget_exhausted(self):
        opt = CostOptimizer(daily_budget=0.001, db_path=self.db_path)
        opt._spent_today = 0.00099
        assert opt.should_escalate(0.3, attempts=5) is False

    def test_max_2_escalations_per_query(self):
        opt = CostOptimizer(daily_budget=10.0, db_path=self.db_path)
        assert opt.should_escalate(0.3, attempts=3, query_id="q1") is True
        assert opt.should_escalate(0.3, attempts=4, query_id="q1") is True
        assert opt.should_escalate(0.3, attempts=5, query_id="q1") is False  # cap

    def test_stats_structure(self):
        opt = CostOptimizer(db_path=self.db_path)
        stats = opt.get_stats()
        assert "today_cost" in stats
        assert "budget_remaining" in stats
        assert "daily_budget" in stats

    def test_usage_persisted_to_db(self):
        opt = CostOptimizer(db_path=self.db_path)
        opt.record_usage("deepseek-chat", tokens_in=1000, tokens_out=500)

        # Read directly from DB
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT COUNT(*) FROM usage").fetchall()
        conn.close()
        assert rows[0][0] >= 1


# ── MODEL_TIERS Structure ────────────────────────────────────────────

class TestModelTiersStructure:
    def test_all_tiers_have_required_keys(self):
        required = {"label", "models", "price_per_1M_in", "price_per_1M_out", "notes"}
        for tier_name, tier_info in MODEL_TIERS.items():
            assert required.issubset(set(tier_info.keys())), f"Tier {tier_name} missing keys"

    def test_tiers_are_sorted_by_cost(self):
        # Verify increasing cost order
        costs_in = [MODEL_TIERS[t]["price_per_1M_in"] for t in MODEL_TIERS]
        for i in range(len(costs_in) - 1):
            assert costs_in[i] <= costs_in[i + 1]
