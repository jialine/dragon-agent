"""
Unit tests for dragon.consult — Expert Consultation Module.

Covers pure functions and data classes only (no async consult flow):
  - DIFFICULTY_SUCCESS_TABLE structure and all bands
  - _lookup_success_rate across all score boundaries
  - ELITE_PANEL structure and required keys
  - ConsultationAssessment creation, defaults, to_dict
  - ConsultationResult creation (solved=True/False), to_dict
  - _find_tier with known model names
  - ExpertConsultation._explorations_to_proposals with 0, 1, 3 explorations
  - ExpertConsultation._build_failure_reason for DEADLOCK, SPLIT, LOW_CONFIDENCE verdicts
  - get_difficulty_band returns correct structure
  - should_consult threshold logic
  - ExpertConsultation constructor and CONSULT_THRESHOLD
"""
from unittest.mock import MagicMock

import pytest

from dragon.consult import (
    DIFFICULTY_SUCCESS_TABLE,
    ELITE_PANEL,
    ConsultationAssessment,
    ConsultationResult,
    ExpertConsultation,
    _lookup_success_rate,
    get_difficulty_band,
    should_consult,
)
from dragon.explorer import ExplorationResult
from dragon.jury import JuryVerdict, VoteDecision


# ════════════════════════════════════════════════════════════════════
# 1. DIFFICULTY_SUCCESS_TABLE
# ════════════════════════════════════════════════════════════════════

class TestDifficultySuccessTable:
    """Tests for the global DIFFICULTY_SUCCESS_TABLE constant."""

    BAND_KEYS = ["0-2", "3-4", "5-6", "7", "8", "9", "10"]
    REQUIRED_KEYS = {
        "min_score", "max_score", "success_rate", "label",
        "needs_consultation", "recommendation",
    }

    def test_all_seven_bands_present(self):
        """All 7 difficulty bands must be keys in the table."""
        for band in self.BAND_KEYS:
            assert band in DIFFICULTY_SUCCESS_TABLE, f"Missing band: {band}"
        assert len(DIFFICULTY_SUCCESS_TABLE) == 7

    def test_each_band_has_all_required_fields(self):
        """Every band must contain the 6 required keys."""
        for band_key, band in DIFFICULTY_SUCCESS_TABLE.items():
            band_keys = set(band.keys())
            missing = self.REQUIRED_KEYS - band_keys
            assert not missing, f"Band {band_key} missing keys: {missing}"

    def test_low_bands_no_consultation(self):
        """Bands 0-2, 3-4, 5-6 have needs_consultation=False."""
        for band_key in ["0-2", "3-4", "5-6"]:
            assert DIFFICULTY_SUCCESS_TABLE[band_key]["needs_consultation"] is False

    def test_high_bands_need_consultation(self):
        """Bands 7, 8, 9, 10 have needs_consultation=True."""
        for band_key in ["7", "8", "9", "10"]:
            assert DIFFICULTY_SUCCESS_TABLE[band_key]["needs_consultation"] is True

    def test_ranges_are_valid(self):
        """min_score <= max_score in every band."""
        for band in DIFFICULTY_SUCCESS_TABLE.values():
            assert band["min_score"] <= band["max_score"], \
                f"Invalid range: {band['min_score']} > {band['max_score']}"

    def test_success_rates_decreasing(self):
        """Higher difficulty bands should have lower success rates."""
        rates = [DIFFICULTY_SUCCESS_TABLE[k]["success_rate"] for k in self.BAND_KEYS]
        for i in range(len(rates) - 1):
            assert rates[i] >= rates[i + 1], \
                f"Band {self.BAND_KEYS[i]} rate {rates[i]} < {self.BAND_KEYS[i+1]} rate {rates[i+1]}"

    def test_success_rates_in_range(self):
        """All success rates must be between 0.0 and 1.0."""
        for band in DIFFICULTY_SUCCESS_TABLE.values():
            assert 0.0 <= band["success_rate"] <= 1.0

    def test_band_10_min_equals_max(self):
        """Band '10' has min_score == max_score == 10.0 (singleton band)."""
        band = DIFFICULTY_SUCCESS_TABLE["10"]
        assert band["min_score"] == 10.0
        assert band["max_score"] == 10.0

    def test_label_is_non_empty_string(self):
        """Every band label must be a non-empty string."""
        for band in DIFFICULTY_SUCCESS_TABLE.values():
            assert isinstance(band["label"], str)
            assert len(band["label"]) > 0

    def test_recommendation_is_non_empty_string(self):
        """Every band recommendation must be a non-empty string."""
        for band in DIFFICULTY_SUCCESS_TABLE.values():
            assert isinstance(band["recommendation"], str)
            assert len(band["recommendation"]) > 0


# ════════════════════════════════════════════════════════════════════
# 2. _lookup_success_rate
# ════════════════════════════════════════════════════════════════════

class TestLookupSuccessRate:
    """Tests for _lookup_success_rate(difficulty_score)."""

    def test_score_zero(self):
        """Score 0.0 → band '0-2'."""
        rate, label, needs, rec = _lookup_success_rate(0.0)
        assert rate == 0.98
        assert "极简单" in label
        assert needs is False

    def test_score_one(self):
        """Score 1.0 → band '0-2'."""
        rate, label, needs, rec = _lookup_success_rate(1.0)
        assert rate == 0.98
        assert needs is False

    def test_score_two(self):
        """Score 2.0 (upper bound of '0-2') → band '0-2'."""
        rate, _, needs, _ = _lookup_success_rate(2.0)
        assert rate == 0.98
        assert needs is False

    def test_score_three(self):
        """Score 3.0 → band '3-4'."""
        rate, label, needs, _ = _lookup_success_rate(3.0)
        assert rate == 0.92
        assert "简单" in label
        assert needs is False

    def test_score_five(self):
        """Score 5.0 → band '5-6'."""
        rate, label, needs, _ = _lookup_success_rate(5.0)
        assert rate == 0.80
        assert "中等" in label
        assert needs is False

    def test_score_seven(self):
        """Score 7.0 → band '7'."""
        rate, label, needs, _ = _lookup_success_rate(7.0)
        assert rate == 0.65
        assert "困难" in label
        assert needs is True

    def test_score_seven_point_five(self):
        """Score 7.5 → band '7' (falls within [7.0, 7.99])."""
        rate, label, needs, _ = _lookup_success_rate(7.5)
        assert rate == 0.65
        assert needs is True

    def test_score_eight(self):
        """Score 8.0 → band '8'."""
        rate, label, needs, _ = _lookup_success_rate(8.0)
        assert rate == 0.45
        assert "很困难" in label
        assert needs is True

    def test_score_nine(self):
        """Score 9.0 → band '9'."""
        rate, label, needs, _ = _lookup_success_rate(9.0)
        assert rate == 0.25
        assert "极困难" in label
        assert needs is True

    def test_score_ten(self):
        """Score 10.0 → band '10'."""
        rate, label, needs, _ = _lookup_success_rate(10.0)
        assert rate == 0.10
        assert "无法解决" in label or "重新表述" in label
        assert needs is True

    def test_score_above_ten_clamps_down(self):
        """Score > 10.0 is clamped to 10.0 → band '10'."""
        rate, label, needs, _ = _lookup_success_rate(15.0)
        assert rate == 0.10
        assert needs is True

    def test_score_above_ten_large_value(self):
        """Score 100.0 clamped → band '10'."""
        rate, _, needs, _ = _lookup_success_rate(100.0)
        assert rate == 0.10

    def test_score_below_zero_clamps_up(self):
        """Score < 0.0 is clamped to 0.0 → band '0-2'."""
        rate, label, needs, _ = _lookup_success_rate(-5.0)
        assert rate == 0.98
        assert needs is False

    def test_score_negative_one(self):
        """Score -1.0 clamped to 0.0 → band '0-2'."""
        rate, _, needs, _ = _lookup_success_rate(-1.0)
        assert rate == 0.98

    def test_score_exact_six(self):
        """Score 6.0 (upper bound of '5-6') → band '5-6'."""
        rate, _, needs, _ = _lookup_success_rate(6.0)
        assert rate == 0.80
        assert needs is False

    def test_score_just_below_seven(self):
        """Score 6.99 falls in gap between '5-6' (max 6.0) and '7' (min 7.0) → fallback."""
        rate, _, needs, _ = _lookup_success_rate(6.99)
        assert rate == 0.5  # fallback rate
        assert needs is True  # fallback defaults to needs_consultation=True

    def test_score_exact_four(self):
        """Score 4.0 (upper bound of '3-4') → band '3-4'."""
        rate, _, needs, _ = _lookup_success_rate(4.0)
        assert rate == 0.92
        assert needs is False

    def test_return_is_four_tuple(self):
        """Return value is always a 4-tuple of the correct types."""
        result = _lookup_success_rate(5.5)
        assert isinstance(result, tuple)
        assert len(result) == 4
        assert isinstance(result[0], float)  # success_rate
        assert isinstance(result[1], str)     # label
        assert isinstance(result[2], bool)    # needs_consultation
        assert isinstance(result[3], str)     # recommendation


# ════════════════════════════════════════════════════════════════════
# 3. ELITE_PANEL
# ════════════════════════════════════════════════════════════════════

class TestElitePanel:
    """Tests for the global ELITE_PANEL list."""

    REQUIRED_KEYS = {"name", "model", "provider", "api_key_env", "perspective", "system_prompt"}

    def test_has_three_entries(self):
        """ELITE_PANEL must contain exactly 3 expert entries."""
        assert len(ELITE_PANEL) == 3

    def test_each_entry_has_required_keys(self):
        """Every panel entry must have all 6 required keys."""
        for i, entry in enumerate(ELITE_PANEL):
            entry_keys = set(entry.keys())
            missing = self.REQUIRED_KEYS - entry_keys
            assert not missing, f"Entry {i} ({entry.get('name', '?')}) missing: {missing}"

    def test_names_are_unique(self):
        """Each panel member must have a unique name."""
        names = [entry["name"] for entry in ELITE_PANEL]
        assert len(names) == len(set(names))

    def test_models_are_known(self):
        """Panel models should include deepseek-reasoner, gpt-4o, claude-sonnet-4."""
        models = {entry["model"] for entry in ELITE_PANEL}
        assert "deepseek-reasoner" in models
        assert "gpt-4o" in models
        assert "claude-sonnet-4" in models

    def test_perspectives_are_unique(self):
        """Each panel member should have a distinct analytical perspective."""
        perspectives = [entry["perspective"] for entry in ELITE_PANEL]
        assert len(perspectives) == len(set(perspectives))

    def test_api_key_envs_are_valid(self):
        """API key environment variables should be well-formed."""
        for entry in ELITE_PANEL:
            assert entry["api_key_env"].endswith("_API_KEY")

    def test_system_prompts_non_empty(self):
        """System prompts must be non-empty strings."""
        for entry in ELITE_PANEL:
            assert isinstance(entry["system_prompt"], str)
            assert len(entry["system_prompt"]) > 100


# ════════════════════════════════════════════════════════════════════
# 4. ConsultationAssessment
# ════════════════════════════════════════════════════════════════════

class TestConsultationAssessment:
    """Tests for the ConsultationAssessment dataclass."""

    def test_creation_with_required_fields(self):
        """Can create with only required fields (difficulty_score, estimated_success, needs_consultation)."""
        a = ConsultationAssessment(
            difficulty_score=8.5,
            estimated_success=0.45,
            needs_consultation=True,
        )
        assert a.difficulty_score == 8.5
        assert a.estimated_success == 0.45
        assert a.needs_consultation is True

    def test_default_values(self):
        """Optional fields have correct defaults."""
        a = ConsultationAssessment(7.0, 0.65, True)
        assert a.recommended_panel == []
        assert a.estimated_cost == 0.0
        assert a.warning_message == ""
        assert a.difficulty_label == ""
        assert a.recommendation == ""

    def test_full_creation(self):
        """Can populate all fields."""
        a = ConsultationAssessment(
            difficulty_score=9.5,
            estimated_success=0.25,
            needs_consultation=True,
            recommended_panel=["deepseek-reasoner", "gpt-4o"],
            estimated_cost=0.123,
            warning_message="⚠️ Warning",
            difficulty_label="极困难",
            recommendation="Reformulate the question",
        )
        assert a.difficulty_score == 9.5
        assert a.recommended_panel == ["deepseek-reasoner", "gpt-4o"]
        assert a.estimated_cost == 0.123
        assert a.warning_message == "⚠️ Warning"
        assert a.difficulty_label == "极困难"
        assert a.recommendation == "Reformulate the question"

    def test_to_dict_rounds_floats(self):
        """to_dict() rounds estimated_success to 4 decimals and estimated_cost to 6."""
        a = ConsultationAssessment(
            difficulty_score=7.2,
            estimated_success=0.654321,
            needs_consultation=True,
            estimated_cost=0.12345678,
        )
        d = a.to_dict()
        assert d["estimated_success"] == 0.6543
        assert d["estimated_cost"] == 0.123457

    def test_to_dict_includes_all_keys(self):
        """to_dict() output contains all expected keys."""
        a = ConsultationAssessment(8.0, 0.5, True)
        d = a.to_dict()
        expected_keys = {
            "difficulty_score", "estimated_success", "needs_consultation",
            "recommended_panel", "estimated_cost", "warning_message",
            "difficulty_label", "recommendation",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_preserves_panel_list(self):
        """to_dict() preserves the recommended_panel list."""
        a = ConsultationAssessment(
            7.5, 0.6, True,
            recommended_panel=["model-a", "model-b"],
        )
        assert a.to_dict()["recommended_panel"] == ["model-a", "model-b"]

    def test_to_dict_preserves_warning_message(self):
        """to_dict() preserves warning_message exactly."""
        a = ConsultationAssessment(7.0, 0.5, True, warning_message="⚠️ danger")
        assert a.to_dict()["warning_message"] == "⚠️ danger"


# ════════════════════════════════════════════════════════════════════
# 5. ConsultationResult
# ════════════════════════════════════════════════════════════════════

class TestConsultationResult:
    """Tests for the ConsultationResult dataclass."""

    def test_solved_true_creation(self):
        """Can create a result for a successfully solved case."""
        r = ConsultationResult(
            solved=True,
            solution="The answer is 42.",
            confidence=0.92,
            panel_used=["gpt-4o", "claude-sonnet-4"],
            debate_rounds=3,
            cost_usd=0.05,
            verdict_decision="consensus",
            exploration_count=3,
            elapsed_ms=15234.5,
        )
        assert r.solved is True
        assert r.solution == "The answer is 42."
        assert r.confidence == 0.92
        assert r.panel_used == ["gpt-4o", "claude-sonnet-4"]
        assert r.debate_rounds == 3
        assert r.cost_usd == 0.05
        assert r.verdict_decision == "consensus"
        assert r.exploration_count == 3
        assert r.elapsed_ms == 15234.5

    def test_solved_false_creation(self):
        """Can create a result for an unsolved case."""
        r = ConsultationResult(
            solved=False,
            solution="",
            confidence=0.15,
            cannot_solve_reason="Deadlock in jury debate.",
            verdict_decision="deadlock",
        )
        assert r.solved is False
        assert r.solution == ""
        assert r.confidence == 0.15
        assert r.cannot_solve_reason == "Deadlock in jury debate."
        assert r.verdict_decision == "deadlock"

    def test_defaults(self):
        """Optional fields have correct defaults."""
        r = ConsultationResult(solved=False, solution="", confidence=0.0)
        assert r.panel_used == []
        assert r.debate_rounds == 0
        assert r.cost_usd == 0.0
        assert r.minority_opinions == []
        assert r.cannot_solve_reason == ""
        assert r.verdict_decision == ""
        assert r.exploration_count == 0
        assert r.elapsed_ms == 0.0

    def test_to_dict_solved(self):
        """to_dict() for solved case includes all fields."""
        r = ConsultationResult(
            solved=True,
            solution="Solution text",
            confidence=0.87654321,
            panel_used=["gpt-4o"],
            debate_rounds=2,
            cost_usd=0.12345678,
            minority_opinions=["Minority note"],
            verdict_decision="majority",
            exploration_count=2,
            elapsed_ms=5000.123,
        )
        d = r.to_dict()
        assert d["solved"] is True
        assert d["solution"] == "Solution text"
        assert d["confidence"] == 0.8765
        assert d["panel_used"] == ["gpt-4o"]
        assert d["debate_rounds"] == 2
        assert d["cost_usd"] == 0.123457
        assert d["minority_opinions"] == ["Minority note"]
        assert d["verdict_decision"] == "majority"
        assert d["exploration_count"] == 2
        assert d["elapsed_ms"] == 5000.1

    def test_to_dict_unsolved(self):
        """to_dict() for unsolved case retains cannot_solve_reason."""
        r = ConsultationResult(
            solved=False, solution="", confidence=0.1,
            cannot_solve_reason="Not enough proposals",
            verdict_decision="split",
        )
        d = r.to_dict()
        assert d["solved"] is False
        assert d["solution"] == ""
        assert d["cannot_solve_reason"] == "Not enough proposals"
        assert d["verdict_decision"] == "split"

    def test_to_dict_keys_complete(self):
        """to_dict() output contains all 11 expected keys."""
        r = ConsultationResult(solved=False, solution="", confidence=0.0)
        expected_keys = {
            "solved", "solution", "confidence", "panel_used",
            "debate_rounds", "cost_usd", "minority_opinions",
            "cannot_solve_reason", "verdict_decision",
            "exploration_count", "elapsed_ms",
        }
        assert set(r.to_dict().keys()) == expected_keys


# ════════════════════════════════════════════════════════════════════
# 6. _find_tier
# ════════════════════════════════════════════════════════════════════

class TestFindTier:
    """Tests for the static _find_tier(model) method."""

    def test_deepseek_reasoner_tier3(self):
        """deepseek-reasoner → tier3_large."""
        assert ExpertConsultation._find_tier("deepseek-reasoner") == "tier3_large"

    def test_gpt4o_tier4(self):
        """gpt-4o → tier4_premium."""
        assert ExpertConsultation._find_tier("gpt-4o") == "tier4_premium"

    def test_claude_sonnet_tier4(self):
        """claude-sonnet-4 → tier4_premium."""
        assert ExpertConsultation._find_tier("claude-sonnet-4") == "tier4_premium"

    def test_deepseek_chat_tier2(self):
        """deepseek-chat → tier2_medium."""
        assert ExpertConsultation._find_tier("deepseek-chat") == "tier2_medium"

    def test_qwen3_8b_tier1(self):
        """qwen3-8b → tier1_small."""
        assert ExpertConsultation._find_tier("qwen3-8b") == "tier1_small"

    def test_qwen3_06b_tier0(self):
        """qwen3-0.6b → tier0_local."""
        assert ExpertConsultation._find_tier("qwen3-0.6b") == "tier0_local"

    def test_unknown_model_returns_none(self):
        """Unknown model → None."""
        assert ExpertConsultation._find_tier("nonexistent-model-v99") is None

    def test_empty_string_returns_none(self):
        """Empty string → None."""
        assert ExpertConsultation._find_tier("") is None

    def test_case_insensitive(self):
        """Model name lookup is case-insensitive."""
        assert ExpertConsultation._find_tier("GPT-4o") == "tier4_premium"
        assert ExpertConsultation._find_tier("DeepSeek-Reasoner") == "tier3_large"

    def test_partial_substring_match(self):
        """Fuzzy match: model name containing a tier model name works."""
        assert ExpertConsultation._find_tier("gpt-4o-2024-08-06") == "tier4_premium"
        assert ExpertConsultation._find_tier("deepseek-reasoner-v2-beta") == "tier3_large"


# ════════════════════════════════════════════════════════════════════
# 7. ExpertConsultation._explorations_to_proposals
# ════════════════════════════════════════════════════════════════════

class TestExplorationsToProposals:
    """Tests for ExpertConsultation._explorations_to_proposals(explorations)."""

    def _make_exploration(self, name, model, findings=None, approach="", caveats="", raw=""):
        """Factory helper for ExplorationResult."""
        return ExplorationResult(
            explorer_name=name,
            model_used=model,
            findings=findings or [],
            approach=approach,
            confidence=0.8,
            caveats=caveats,
            cost_usd=0.01,
            raw_content=raw,
        )

    def test_empty_list(self):
        """Empty explorations list → empty proposals dict."""
        result = ExpertConsultation._explorations_to_proposals([])
        assert result == {}
        assert isinstance(result, dict)

    def test_single_exploration(self):
        """Single exploration → one proposal labeled 'A'."""
        exp = self._make_exploration(
            "首席分析官", "deepseek-reasoner",
            findings=["Finding 1", "Finding 2"],
            approach="深度推理",
            caveats="Limited data",
            raw="Full analysis content",
        )
        result = ExpertConsultation._explorations_to_proposals([exp])
        assert len(result) == 1
        assert "A" in result
        assert result["A"]["author"] == "首席分析官 (deepseek-reasoner)"
        assert "Finding 1" in result["A"]["summary"]
        assert "Finding 2" in result["A"]["summary"]
        assert "深度推理" in result["A"]["summary"]
        assert "Limited data" in result["A"]["summary"]
        assert "Full analysis content" in result["A"]["summary"]

    def test_three_explorations(self):
        """Three explorations → proposals A, B, C."""
        exps = [
            self._make_exploration("Exp1", "model-a", findings=["A1"]),
            self._make_exploration("Exp2", "model-b", findings=["B1", "B2"]),
            self._make_exploration("Exp3", "model-c", findings=["C1"]),
        ]
        result = ExpertConsultation._explorations_to_proposals(exps)
        assert len(result) == 3
        for label in ["A", "B", "C"]:
            assert label in result
        assert result["A"]["author"] == "Exp1 (model-a)"
        assert result["B"]["author"] == "Exp2 (model-b)"
        assert result["C"]["author"] == "Exp3 (model-c)"

    def test_summary_limits_findings_to_five(self):
        """Only first 5 findings are included in summary."""
        exp = self._make_exploration(
            "Test", "model",
            findings=[f"Finding {i}" for i in range(10)],
        )
        result = ExpertConsultation._explorations_to_proposals([exp])
        summary = result["A"]["summary"]
        for i in range(5):
            assert f"Finding {i}" in summary
        assert "Finding 5" not in summary

    def test_raw_content_truncated_to_3000(self):
        """Raw content in summary is truncated to 3000 chars."""
        long_raw = "x" * 5000
        exp = self._make_exploration("Test", "model", raw=long_raw)
        result = ExpertConsultation._explorations_to_proposals([exp])
        # The raw content inside summary is prefixed with "## 完整分析\n" and truncated
        assert "完整分析" in result["A"]["summary"]

    def test_no_findings_falls_back_to_raw(self):
        """Exploration with no findings uses raw_content as summary."""
        exp = self._make_exploration(
            "Test", "model", findings=[], approach="", caveats="", raw="Raw fallback text"
        )
        result = ExpertConsultation._explorations_to_proposals([exp])
        assert "Raw fallback text" in result["A"]["summary"]

    def test_preserves_chinese_labels(self):
        """Chinese section labels appear correctly in summaries."""
        exp = self._make_exploration(
            "首席分析官", "deepseek-reasoner",
            findings=["关键发现1"],
            approach="推理方法",
            caveats="数据局限",
        )
        result = ExpertConsultation._explorations_to_proposals([exp])
        summary = result["A"]["summary"]
        assert "关键发现" in summary
        assert "分析视角" in summary
        assert "局限性" in summary


# ════════════════════════════════════════════════════════════════════
# 8. ExpertConsultation._build_failure_reason
# ════════════════════════════════════════════════════════════════════

class TestBuildFailureReason:
    """Tests for ExpertConsultation._build_failure_reason(verdict, explorations)."""

    def _make_verdict(self, decision, winner="", confidence=0.3,
                      minority_report="", deception_flags=None):
        """Factory helper for JuryVerdict."""
        return JuryVerdict(
            decision=decision,
            winner=winner,
            ballots=[],
            debate_transcript=[],
            minority_report=minority_report,
            deception_flags=deception_flags or [],
            confidence=confidence,
            recommendation="",
        )

    def _make_exploration(self, name, model, confidence=0.5):
        """Factory helper for ExplorationResult with minimal fields."""
        return ExplorationResult(
            explorer_name=name,
            model_used=model,
            confidence=confidence,
        )

    def test_deadlock(self):
        """DEADLOCK verdict → reason mentions 僵局/DEADLOCK."""
        verdict = self._make_verdict(VoteDecision.DEADLOCK)
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "无法解决" in reason
        assert "DEADLOCK" in reason or "僵局" in reason

    def test_split(self):
        """SPLIT verdict → reason mentions 分裂/SPLIT."""
        verdict = self._make_verdict(VoteDecision.SPLIT)
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "无法解决" in reason
        assert "SPLIT" in reason or "分裂" in reason

    def test_low_confidence_consensus(self):
        """CONSENSUS but low confidence → reason mentions threshold."""
        verdict = self._make_verdict(VoteDecision.CONSENSUS, winner="A", confidence=0.15)
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "无法解决" in reason
        assert "置信度" in reason
        assert "consensus" in reason.lower()

    def test_includes_minority_report(self):
        """Minority report text is included in the reason."""
        verdict = self._make_verdict(
            VoteDecision.DEADLOCK,
            minority_report="Expert B dissented on cost analysis",
        )
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "少数意见" in reason
        assert "Expert B dissented" in reason

    def test_includes_deception_flags(self):
        """Deception flags appear in the reason under 评审异常标记."""
        verdict = self._make_verdict(
            VoteDecision.DEADLOCK,
            deception_flags=["Flag: Circular reasoning", "Flag: Data hallucination"],
        )
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "评审异常标记" in reason
        assert "Circular reasoning" in reason
        assert "Data hallucination" in reason

    def test_includes_exploration_summary(self):
        """Exploration phase summary when explorations are provided."""
        exps = [
            self._make_exploration("E1", "m1", 0.6),
            self._make_exploration("E2", "m2", 0.4),
        ]
        verdict = self._make_verdict(VoteDecision.DEADLOCK)
        reason = ExpertConsultation._build_failure_reason(verdict, exps)
        assert "探索阶段摘要" in reason
        assert "2 位" in reason
        assert "平均置信度" in reason
        assert "最低置信度" in reason

    def test_ends_with_suggestions(self):
        """Failure reason always ends with 建议 section."""
        verdict = self._make_verdict(VoteDecision.DEADLOCK)
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "建议" in reason
        assert "重新表述" in reason
        assert "拆分" in reason

    def test_no_explorations_skips_summary(self):
        """Empty explorations list → no 探索阶段摘要 section."""
        verdict = self._make_verdict(VoteDecision.DEADLOCK)
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        assert "探索阶段摘要" not in reason

    def test_minority_report_truncated(self):
        """Minority report is truncated to 500 characters."""
        long_report = "X" * 1000
        verdict = self._make_verdict(
            VoteDecision.DEADLOCK, minority_report=long_report,
        )
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        # Should contain the truncated portion but not the full 1000 chars
        assert "X" * 450 in reason  # safely within 500 limit

    def test_deception_flags_limited_to_three(self):
        """At most 3 deception flags are shown."""
        verdict = self._make_verdict(
            VoteDecision.DEADLOCK,
            deception_flags=[f"Flag {i}" for i in range(10)],
        )
        reason = ExpertConsultation._build_failure_reason(verdict, [])
        # First 3 flags present
        for i in range(3):
            assert f"Flag {i}" in reason
        # Flag 3+ not present
        assert "Flag 3" not in reason


# ════════════════════════════════════════════════════════════════════
# 9. get_difficulty_band
# ════════════════════════════════════════════════════════════════════

class TestGetDifficultyBand:
    """Tests for the module-level convenience function get_difficulty_band()."""

    def test_returns_dict(self):
        """Returns a dict with the expected keys."""
        band = get_difficulty_band(8.5)
        assert isinstance(band, dict)
        expected_keys = {"success_rate", "label", "needs_consultation", "recommendation"}
        assert set(band.keys()) == expected_keys

    def test_high_score_needs_consultation(self):
        """Score >= 7 → needs_consultation=True."""
        band = get_difficulty_band(7.0)
        assert band["needs_consultation"] is True
        assert band["success_rate"] == 0.65

    def test_low_score_no_consultation(self):
        """Score < 7 → needs_consultation=False."""
        band = get_difficulty_band(3.0)
        assert band["needs_consultation"] is False
        assert band["success_rate"] == 0.92

    def test_values_match_lookup(self):
        """Values match _lookup_success_rate for same score."""
        band = get_difficulty_band(5.5)
        rate, label, needs, rec = _lookup_success_rate(5.5)
        assert band["success_rate"] == rate
        assert band["label"] == label
        assert band["needs_consultation"] == needs
        assert band["recommendation"] == rec

    def test_clamped_negative_score(self):
        """Negative score clamped to 0 → low band."""
        band = get_difficulty_band(-10)
        assert band["needs_consultation"] is False
        assert band["success_rate"] == 0.98

    def test_clamped_above_ten(self):
        """Score > 10 clamped to 10 → band '10'."""
        band = get_difficulty_band(99)
        assert band["needs_consultation"] is True
        assert band["success_rate"] == 0.10


# ════════════════════════════════════════════════════════════════════
# 10. should_consult
# ════════════════════════════════════════════════════════════════════

class TestShouldConsult:
    """Tests for should_consult(difficulty_score)."""

    def test_exactly_threshold(self):
        """Score == 7.0 → True."""
        assert should_consult(7.0) is True

    def test_above_threshold(self):
        """Score > 7.0 → True."""
        assert should_consult(8.5) is True
        assert should_consult(10.0) is True
        assert should_consult(7.01) is True

    def test_below_threshold(self):
        """Score < 7.0 → False."""
        assert should_consult(6.99) is False
        assert should_consult(5.0) is False
        assert should_consult(0.0) is False

    def test_negative_score(self):
        """Negative scores → False (below threshold)."""
        assert should_consult(-5.0) is False

    def test_very_large_score(self):
        """Very large scores → True (above threshold)."""
        assert should_consult(100.0) is True

    def test_returns_bool(self):
        """Always returns a bool."""
        assert isinstance(should_consult(3.0), bool)
        assert isinstance(should_consult(8.0), bool)


# ════════════════════════════════════════════════════════════════════
# 11. ExpertConsultation constructor
# ════════════════════════════════════════════════════════════════════

class TestExpertConsultationConstructor:
    """Tests for ExpertConsultation class-level constants and constructor."""

    def test_consult_threshold_class_constant(self):
        """CONSULT_THRESHOLD is 7.0."""
        assert ExpertConsultation.CONSULT_THRESHOLD == 7.0

    def test_min_solve_consensus_constant(self):
        """MIN_SOLVE_CONSENSUS is 0.6."""
        assert ExpertConsultation.MIN_SOLVE_CONSENSUS == 0.6

    def test_min_solve_confidence_constant(self):
        """MIN_SOLVE_CONFIDENCE is 0.4."""
        assert ExpertConsultation.MIN_SOLVE_CONFIDENCE == 0.4

    def test_explorer_timeout_constant(self):
        """EXPLORER_TIMEOUT is 45.0 seconds."""
        assert ExpertConsultation.EXPLORER_TIMEOUT == 45.0

    def test_constructor_with_dispatcher_and_jury(self):
        """Constructor accepts a dispatcher and jury, builds elite panel."""
        dispatcher = MagicMock()
        jury = MagicMock()
        consult = ExpertConsultation(dispatcher, jury)
        assert consult._dispatcher is dispatcher
        assert consult._jury is jury
        # Elite panel is built internally
        assert len(consult._panel_model_names) == 3
        assert len(consult._elite_explorers) == 3

    def test_constructor_creates_cost_optimizer_if_none(self):
        """If no cost_optimizer given, one is created internally with default budget."""
        consult = ExpertConsultation(MagicMock(), MagicMock())
        assert consult._cost is not None
        # Default daily budget is 5.0
        assert consult._cost.daily_budget == 5.0

    def test_constructor_creates_anti_loop_guard_if_none(self):
        """If no guard given, an AntiLoopGuard is created internally."""
        consult = ExpertConsultation(MagicMock(), MagicMock())
        assert consult._guard is not None

    def test_constructor_accepts_custom_cost_optimizer(self):
        """Custom cost optimizer is used when provided."""
        from dragon.utils.cost import CostOptimizer
        custom_cost = CostOptimizer(daily_budget=10.0)
        consult = ExpertConsultation(MagicMock(), MagicMock(), cost_optimizer=custom_cost)
        assert consult._cost is custom_cost

    def test_constructor_accepts_custom_guard(self):
        """Custom guard is used when provided."""
        from dragon.guard import AntiLoopGuard
        custom_guard = AntiLoopGuard()
        consult = ExpertConsultation(MagicMock(), MagicMock(), guard=custom_guard)
        assert consult._guard is custom_guard

    def test_panel_model_names_include_all_elite_models(self):
        """_panel_model_names matches ELITE_PANEL model names."""
        consult = ExpertConsultation(MagicMock(), MagicMock())
        expected_models = {entry["model"] for entry in ELITE_PANEL}
        actual_models = set(consult._panel_model_names)
        assert actual_models == expected_models

    def test_elite_explorers_keys_are_panel_names(self):
        """_elite_explorers dict keys are the panel entry names."""
        consult = ExpertConsultation(MagicMock(), MagicMock())
        expected_names = {entry["name"] for entry in ELITE_PANEL}
        assert set(consult._elite_explorers.keys()) == expected_names

    def test_should_consult_uses_class_constant(self):
        """should_consult() uses ExpertConsultation.CONSULT_THRESHOLD."""
        # Cross-reference: module-level should_consult matches the class constant
        threshold = ExpertConsultation.CONSULT_THRESHOLD
        assert should_consult(threshold) is True
        assert should_consult(threshold - 0.01) is False
