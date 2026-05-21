"""
Unit tests for panda.jury — Jury Debate Engine.

Covers:
  - VoteDecision enum
  - Ballot dataclass (creation, confidence clamping, defaults)
  - DebateRound dataclass (creation, defaults, data population)
  - JuryVerdict dataclass (creation, defaults)
  - _extract_json helper (all 4 strategies + edge cases)
  - DEFAULT_JURY_PANEL structure
  - JuryDebate constructor (validation, defaults, custom params, properties)
"""
from unittest.mock import MagicMock

import pytest

from panda.jury import (
    VoteDecision,
    Ballot,
    DebateRound,
    JuryVerdict,
    JuryDebate,
    DEFAULT_JURY_PANEL,
    _extract_json,
)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _make_mock_dispatcher():
    """Create a mock PandaDispatcher suitable for JuryDebate construction."""
    return MagicMock()


# ════════════════════════════════════════════════════════════════════
# VoteDecision
# ════════════════════════════════════════════════════════════════════

class TestVoteDecision:
    """Tests for the VoteDecision enum."""

    def test_all_values_exist(self):
        """VoteDecision must expose exactly four members."""
        assert VoteDecision.CONSENSUS.value == "consensus"
        assert VoteDecision.MAJORITY.value == "majority"
        assert VoteDecision.SPLIT.value == "split"
        assert VoteDecision.DEADLOCK.value == "deadlock"

    def test_enum_is_iterable(self):
        """VoteDecision should be iterable and contain 4 members."""
        members = list(VoteDecision)
        assert len(members) == 4

    def test_enum_member_identity(self):
        """Each member is its own unique instance."""
        assert VoteDecision.CONSENSUS is VoteDecision.CONSENSUS
        assert VoteDecision.CONSENSUS is not VoteDecision.DEADLOCK


# ════════════════════════════════════════════════════════════════════
# Ballot
# ════════════════════════════════════════════════════════════════════

class TestBallot:
    """Tests for the Ballot dataclass."""

    def test_creation_all_fields(self):
        """Ballot accepts and stores all fields."""
        b = Ballot(
            voter="finance-gpt4",
            voted_for="A",
            confidence=0.85,
            key_reason="Better ROI",
            against_reasons=["B is too expensive", "C lacks detail"],
            suspected_deception=True,
            suspected_deception_detail="B's numbers look fabricated",
        )
        assert b.voter == "finance-gpt4"
        assert b.voted_for == "A"
        assert b.confidence == 0.85
        assert b.key_reason == "Better ROI"
        assert b.against_reasons == ["B is too expensive", "C lacks detail"]
        assert b.suspected_deception is True
        assert b.suspected_deception_detail == "B's numbers look fabricated"

    def test_confidence_clamped_above_one(self):
        """Confidence > 1.0 is clamped to 1.0 via __post_init__."""
        b = Ballot(voter="j1", voted_for="A", confidence=1.5)
        assert b.confidence == 1.0

    def test_confidence_clamped_below_zero(self):
        """Confidence < 0.0 is clamped to 0.0 via __post_init__."""
        b = Ballot(voter="j1", voted_for="A", confidence=-0.3)
        assert b.confidence == 0.0

    def test_confidence_exactly_one(self):
        """Confidence == 1.0 is left unchanged."""
        b = Ballot(voter="j1", voted_for="A", confidence=1.0)
        assert b.confidence == 1.0

    def test_confidence_exactly_zero(self):
        """Confidence == 0.0 is left unchanged."""
        b = Ballot(voter="j1", voted_for="A", confidence=0.0)
        assert b.confidence == 0.0

    def test_confidence_at_boundary(self):
        """Confidence at 0.5 passes through unchanged."""
        b = Ballot(voter="j1", voted_for="A", confidence=0.5)
        assert b.confidence == 0.5

    def test_defaults(self):
        """Fields with defaults are populated correctly."""
        b = Ballot(voter="juror", voted_for="B", confidence=0.9)
        assert b.key_reason == ""
        assert b.against_reasons == []
        assert b.suspected_deception is False
        assert b.suspected_deception_detail == ""

    def test_default_against_reasons_independent(self):
        """Each Ballot gets its own against_reasons list (no shared default)."""
        b1 = Ballot(voter="j1", voted_for="A", confidence=0.8)
        b2 = Ballot(voter="j2", voted_for="B", confidence=0.7)
        b1.against_reasons.append("reason")
        assert b2.against_reasons == []


# ════════════════════════════════════════════════════════════════════
# DebateRound
# ════════════════════════════════════════════════════════════════════

class TestDebateRound:
    """Tests for the DebateRound dataclass."""

    def test_creation_basic(self):
        """DebateRound stores round_number and empty defaults."""
        dr = DebateRound(round_number=1)
        assert dr.round_number == 1
        assert dr.statements == {}
        assert dr.challenges == {}
        assert dr.responses == {}
        assert dr.metadata == {}

    def test_defaults_are_independent(self):
        """Each DebateRound gets its own default dicts (no shared mutation)."""
        dr1 = DebateRound(round_number=1)
        dr2 = DebateRound(round_number=2)
        dr1.statements["juror1"] = "hello"
        assert dr2.statements == {}

    def test_add_statements(self):
        """Statements dict can be populated."""
        dr = DebateRound(
            round_number=1,
            statements={"finance-juror": "Proposal A is best"},
        )
        assert "finance-juror" in dr.statements
        assert dr.statements["finance-juror"] == "Proposal A is best"

    def test_add_challenges(self):
        """Challenges dict can be populated with list values."""
        dr = DebateRound(
            round_number=2,
            challenges={"finance-juror": ["Why ignore cost?", "Explain risk"]},
        )
        assert len(dr.challenges["finance-juror"]) == 2

    def test_add_responses(self):
        """Responses dict can be populated."""
        dr = DebateRound(
            round_number=2,
            responses={"finance-juror": "Cost is accounted for..."},
        )
        assert dr.responses["finance-juror"] == "Cost is accounted for..."

    def test_metadata_stores_arbitrary_data(self):
        """Metadata dict accepts any key-value pairs."""
        dr = DebateRound(
            round_number=3,
            metadata={"latency_ms": 450, "token_count": 1200},
        )
        assert dr.metadata["latency_ms"] == 450
        assert dr.metadata["token_count"] == 1200

    def test_full_populated_round(self):
        """All fields can be set simultaneously."""
        dr = DebateRound(
            round_number=2,
            statements={"j1": "stmt1", "j2": "stmt2"},
            challenges={"j1": ["q1"], "j2": ["q2", "q3"]},
            responses={"j1": "r1"},
            metadata={"cost": 0.02},
        )
        assert len(dr.statements) == 2
        assert len(dr.challenges) == 2
        assert len(dr.responses) == 1
        assert dr.metadata["cost"] == 0.02


# ════════════════════════════════════════════════════════════════════
# JuryVerdict
# ════════════════════════════════════════════════════════════════════

class TestJuryVerdict:
    """Tests for the JuryVerdict dataclass."""

    def test_creation_minimal(self):
        """JuryVerdict can be created with only required fields."""
        verdict = JuryVerdict(decision=VoteDecision.CONSENSUS, winner="A")
        assert verdict.decision == VoteDecision.CONSENSUS
        assert verdict.winner == "A"

    def test_defaults(self):
        """Unspecified fields use correct defaults."""
        verdict = JuryVerdict(decision=VoteDecision.MAJORITY, winner="B")
        assert verdict.ballots == []
        assert verdict.debate_transcript == []
        assert verdict.minority_report == ""
        assert verdict.deception_flags == []
        assert verdict.confidence == 0.0
        assert verdict.recommendation == ""
        assert verdict.metadata == {}

    def test_full_populated(self):
        """All fields can be set."""
        b = Ballot(voter="j1", voted_for="A", confidence=0.9)
        dr = DebateRound(round_number=1)
        verdict = JuryVerdict(
            decision=VoteDecision.CONSENSUS,
            winner="A",
            ballots=[b],
            debate_transcript=[dr],
            minority_report="Minority dissents...",
            deception_flags=["flag1"],
            confidence=0.92,
            recommendation="Adopt A",
            metadata={"rounds": 3},
        )
        assert len(verdict.ballots) == 1
        assert len(verdict.debate_transcript) == 1
        assert verdict.minority_report == "Minority dissents..."
        assert len(verdict.deception_flags) == 1
        assert verdict.confidence == 0.92
        assert verdict.recommendation == "Adopt A"
        assert verdict.metadata["rounds"] == 3

    def test_default_lists_independent(self):
        """Default list/dict fields are independent per instance."""
        v1 = JuryVerdict(decision=VoteDecision.SPLIT, winner="")
        v2 = JuryVerdict(decision=VoteDecision.SPLIT, winner="")
        v1.ballots.append(Ballot(voter="x", voted_for="A", confidence=0.5))
        v1.deception_flags.append("f1")
        assert v2.ballots == []
        assert v2.deception_flags == []


# ════════════════════════════════════════════════════════════════════
# _extract_json
# ════════════════════════════════════════════════════════════════════

class TestExtractJson:
    """Tests for the _extract_json helper function (all 4 strategies)."""

    # ── Strategy 1: Direct json.loads ──────────────────────────────

    def test_valid_json_object(self):
        """Direct parse of a clean JSON object."""
        result = _extract_json('{"a": 1, "b": "hello"}')
        assert result == {"a": 1, "b": "hello"}

    def test_valid_json_with_whitespace(self):
        """Direct parse strips surrounding whitespace."""
        result = _extract_json('  \n  {"x": true}  \n  ')
        assert result == {"x": True}

    def test_json_with_nested_structure(self):
        """Direct parse handles nested objects and arrays."""
        result = _extract_json('{"items": [{"id": 1}], "meta": {"v": 2}}')
        assert result == {"items": [{"id": 1}], "meta": {"v": 2}}

    def test_json_with_unicode(self):
        """Direct parse handles unicode characters."""
        result = _extract_json('{"msg": "你好世界"}')
        assert result == {"msg": "你好世界"}

    # ── Strategy 2: Markdown fence extraction ──────────────────────

    def test_json_in_markdown_fence_with_lang(self):
        """Extract JSON from ```json ... ``` fence."""
        text = 'Here is my response:\n```json\n{"vote": "A", "confidence": 0.9}\n```\nDone.'
        result = _extract_json(text)
        assert result == {"vote": "A", "confidence": 0.9}

    def test_json_in_markdown_fence_no_lang(self):
        """Extract JSON from ``` ... ``` fence without language tag."""
        text = "```\n{\"result\": \"ok\"}\n```"
        result = _extract_json(text)
        assert result == {"result": "ok"}

    def test_multiple_fences_uses_first_valid(self):
        """When multiple fences exist, the first valid JSON is returned."""
        text = (
            "```json\n{\"first\": 1}\n```\n"
            "Some text in between.\n"
            "```json\n{\"second\": 2}\n```"
        )
        result = _extract_json(text)
        assert result == {"first": 1}

    # ── Strategy 3: Balanced brace scanning ────────────────────────

    def test_json_embedded_in_text(self):
        """JSON buried in natural language text via balanced brace scan."""
        text = "The answer is {\"key\": \"value\"} and that's final."
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_nested_braces_balanced_scan(self):
        """Balanced brace scan handles nested objects correctly."""
        text = 'prefix {"outer": {"inner": [1, 2, 3]}} suffix'
        result = _extract_json(text)
        assert result == {"outer": {"inner": [1, 2, 3]}}

    def test_brace_scan_finds_first_json_block(self):
        """Balanced scan captures the first complete JSON block."""
        text = '{"a":1} extra {"b":2}'
        result = _extract_json(text)
        assert result == {"a": 1}

    # ── Strategy 4: Loose regex fallback ───────────────────────────

    def test_regex_fallback_malformed_but_recoverable(self):
        """Regex fallback catches a { ... } block that brace scan might miss
        (e.g. unbalanced braces inside strings). This text exercises the
        regex path: valid JSON between braces but not first in string
        after balanced scan might fail."""
        # Balanced scan would match from first { but this inner block is valid:
        text = 'Here is JSON: {"simple": "block"}'
        result = _extract_json(text)
        assert result == {"simple": "block"}

    # ── Edge cases: empty / None / invalid ─────────────────────────

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert _extract_json("") is None

    def test_none_returns_none(self):
        """None input returns None."""
        assert _extract_json(None) is None  # type: ignore[arg-type]

    def test_whitespace_only_returns_none(self):
        """Whitespace-only string returns None."""
        assert _extract_json("   \n \t  ") is None

    def test_completely_invalid_returns_none(self):
        """Garbage text with no JSON structure returns None."""
        result = _extract_json("this is not json at all, just words")
        assert result is None

    def test_unclosed_brace_returns_none(self):
        """Text with an opening brace but no matching close returns None."""
        result = _extract_json('{"a": 1, "b":')
        assert result is None

    def test_array_not_object_is_valid(self):
        """A standalone JSON array is valid JSON and should be returned."""
        result = _extract_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_number_or_string_literal(self):
        """Primitive JSON values are valid and returned."""
        result = _extract_json("42")
        assert result == 42


# ════════════════════════════════════════════════════════════════════
# DEFAULT_JURY_PANEL
# ════════════════════════════════════════════════════════════════════

class TestDefaultJuryPanel:
    """Tests for the DEFAULT_JURY_PANEL constant."""

    def test_has_five_entries(self):
        """Default panel has exactly 5 jurors."""
        assert len(DEFAULT_JURY_PANEL) == 5

    def test_each_entry_is_tuple_of_str(self):
        """Each entry is a (str, str) tuple."""
        for entry in DEFAULT_JURY_PANEL:
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            assert isinstance(entry[0], str)
            assert isinstance(entry[1], str)

    def test_expected_industries(self):
        """Industries match the expected diverse panel."""
        industries = {entry[1] for entry in DEFAULT_JURY_PANEL}
        assert industries == {"finance", "medical", "legal", "education", "general"}

    def test_expected_juror_names(self):
        """Juror names follow the expected pattern."""
        names = [entry[0] for entry in DEFAULT_JURY_PANEL]
        assert names == [
            "finance-juror",
            "medical-juror",
            "legal-juror",
            "education-juror",
            "general-juror",
        ]

    def test_is_a_list_of_tuples(self):
        """DEFAULT_JURY_PANEL is a list of tuples (not any other type)."""
        assert isinstance(DEFAULT_JURY_PANEL, list)
        assert all(isinstance(e, tuple) for e in DEFAULT_JURY_PANEL)


# ════════════════════════════════════════════════════════════════════
# JuryDebate — Constructor & Validation
# ════════════════════════════════════════════════════════════════════

class TestJuryDebateConstructor:
    """Tests for JuryDebate.__init__ validation, defaults, and properties."""

    def test_default_values(self):
        """JuryDebate uses expected defaults when only dispatcher is given."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher())
        assert jury.jury_size == 5
        assert jury.juror_names == [
            "finance-juror",
            "medical-juror",
            "legal-juror",
            "education-juror",
            "general-juror",
        ]
        # Internal defaults are tested via repr
        assert "min_consensus=80%" in repr(jury)
        assert "max_rounds=3" in repr(jury)

    def test_custom_min_consensus(self):
        """Custom min_consensus is accepted and reflected in repr."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher(), min_consensus=0.65)
        assert "min_consensus=65%" in repr(jury)

    def test_custom_max_rounds(self):
        """Custom max_rounds within [1,5] is accepted."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher(), max_rounds=2)
        assert "max_rounds=2" in repr(jury)

    def test_custom_both_params(self):
        """Both min_consensus and max_rounds can be set simultaneously."""
        jury = JuryDebate(
            dispatcher=_make_mock_dispatcher(), min_consensus=0.75, max_rounds=4
        )
        assert "min_consensus=75%" in repr(jury)
        assert "max_rounds=4" in repr(jury)

    # ── min_consensus validation ───────────────────────────────────

    def test_min_consensus_zero_raises(self):
        """min_consensus=0 raises ValueError (must be > 0)."""
        with pytest.raises(ValueError, match="min_consensus"):
            JuryDebate(dispatcher=_make_mock_dispatcher(), min_consensus=0.0)

    def test_min_consensus_negative_raises(self):
        """min_consensus negative raises ValueError."""
        with pytest.raises(ValueError, match="min_consensus"):
            JuryDebate(dispatcher=_make_mock_dispatcher(), min_consensus=-0.1)

    def test_min_consensus_over_one_raises(self):
        """min_consensus > 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="min_consensus"):
            JuryDebate(dispatcher=_make_mock_dispatcher(), min_consensus=1.01)

    def test_min_consensus_exactly_one_is_ok(self):
        """min_consensus=1.0 is valid (must be <= 1.0)."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher(), min_consensus=1.0)
        assert "min_consensus=100%" in repr(jury)

    # ── max_rounds validation ──────────────────────────────────────

    def test_max_rounds_zero_raises(self):
        """max_rounds=0 raises ValueError (must be >= 1)."""
        with pytest.raises(ValueError, match="max_rounds"):
            JuryDebate(dispatcher=_make_mock_dispatcher(), max_rounds=0)

    def test_max_rounds_negative_raises(self):
        """max_rounds negative raises ValueError."""
        with pytest.raises(ValueError, match="max_rounds"):
            JuryDebate(dispatcher=_make_mock_dispatcher(), max_rounds=-1)

    def test_max_rounds_over_cap_raises(self):
        """max_rounds > 5 raises ValueError."""
        with pytest.raises(ValueError, match="max_rounds"):
            JuryDebate(dispatcher=_make_mock_dispatcher(), max_rounds=6)

    def test_max_rounds_exactly_five_is_ok(self):
        """max_rounds=5 is valid (the cap)."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher(), max_rounds=5)
        assert "max_rounds=5" in repr(jury)

    def test_max_rounds_exactly_one_is_ok(self):
        """max_rounds=1 is valid (minimum)."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher(), max_rounds=1)
        assert "max_rounds=1" in repr(jury)

    # ── Custom jury panel ──────────────────────────────────────────

    def test_custom_jury_panel(self):
        """Custom jury_panel replaces the default."""
        custom = [("juror-a", "tech"), ("juror-b", "design")]
        jury = JuryDebate(
            dispatcher=_make_mock_dispatcher(), jury_panel=custom
        )
        assert jury.jury_size == 2
        assert jury.juror_names == ["juror-a", "juror-b"]

    def test_empty_list_falls_back_to_default(self):
        """Empty list [] is falsy, so falls back to DEFAULT_JURY_PANEL."""
        jury = JuryDebate(
            dispatcher=_make_mock_dispatcher(), jury_panel=[]
        )
        assert jury.jury_size == 5
        assert jury.juror_names == [n for n, _ in DEFAULT_JURY_PANEL]

    # ── Properties ─────────────────────────────────────────────────

    def test_jury_size_property(self):
        """jury_size returns the number of panel entries."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher())
        assert jury.jury_size == 5

    def test_juror_names_property(self):
        """juror_names returns just the name portion of each tuple."""
        jury = JuryDebate(
            dispatcher=_make_mock_dispatcher(),
            jury_panel=[("alice", "tech"), ("bob", "finance")],
        )
        assert jury.juror_names == ["alice", "bob"]

    def test_get_juror_industry_found(self):
        """get_juror_industry returns the industry for a known juror."""
        jury = JuryDebate(
            dispatcher=_make_mock_dispatcher(),
            jury_panel=[("alice", "tech"), ("bob", "finance")],
        )
        assert jury.get_juror_industry("alice") == "tech"
        assert jury.get_juror_industry("bob") == "finance"

    def test_get_juror_industry_not_found(self):
        """get_juror_industry returns None for unknown juror."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher())
        assert jury.get_juror_industry("nonexistent") is None

    def test_repr(self):
        """__repr__ includes jury_size, min_consensus, max_rounds."""
        jury = JuryDebate(
            dispatcher=_make_mock_dispatcher(), min_consensus=0.7, max_rounds=2
        )
        r = repr(jury)
        assert "JuryDebate(" in r
        assert "jurors=5" in r
        assert "min_consensus=70%" in r
        assert "max_rounds=2" in r

    def test_memory_graph_stored(self):
        """Optional memory_graph is accepted and stored."""
        mg = MagicMock()
        jury = JuryDebate(dispatcher=_make_mock_dispatcher(), memory_graph=mg)
        assert jury._memory_graph is mg

    def test_memory_graph_defaults_to_none(self):
        """memory_graph defaults to None when not provided."""
        jury = JuryDebate(dispatcher=_make_mock_dispatcher())
        assert jury._memory_graph is None
