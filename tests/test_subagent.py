"""
Unit tests for dragon.subagent — SubagentStatus, dataclasses, Subagent (pure methods),
and SubagentOrchestrator (constructor only). No async execute or actual LLM calls.
"""
from __future__ import annotations

import pytest
from dragon.subagent import (
    SubagentStatus,
    SubagentConfig,
    SubagentResult,
    DebateResult,
    Subagent,
    SubagentOrchestrator,
)


# ══════════════════════════════════════════════════════════════════════
# SubagentStatus Enum Tests
# ══════════════════════════════════════════════════════════════════════

class TestSubagentStatusEnum:
    """Test all 6 enum values exist and have correct string values."""

    def test_pending_value(self):
        assert SubagentStatus.PENDING.value == "pending"

    def test_running_value(self):
        assert SubagentStatus.RUNNING.value == "running"

    def test_completed_value(self):
        assert SubagentStatus.COMPLETED.value == "completed"

    def test_failed_value(self):
        assert SubagentStatus.FAILED.value == "failed"

    def test_timeout_value(self):
        assert SubagentStatus.TIMEOUT.value == "timeout"

    def test_cancelled_value(self):
        assert SubagentStatus.CANCELLED.value == "cancelled"

    def test_all_members_count(self):
        """Ensure all 6 members are present."""
        members = list(SubagentStatus)
        assert len(members) == 6
        names = {m.name for m in members}
        assert names == {"PENDING", "RUNNING", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"}


# ══════════════════════════════════════════════════════════════════════
# SubagentConfig Dataclass Tests
# ══════════════════════════════════════════════════════════════════════

class TestSubagentConfig:
    """Test SubagentConfig creation, defaults, and field values."""

    def test_default_creation(self):
        cfg = SubagentConfig()
        assert cfg.name == ""
        assert cfg.model == ""
        assert cfg.provider == ""
        assert cfg.system_prompt == ""
        assert cfg.temperature == 0.7
        assert cfg.max_tokens == 4096
        assert cfg.max_iterations == 10
        assert cfg.timeout_secs == 300.0
        assert cfg.context_limit == 128000

    def test_custom_creation(self):
        cfg = SubagentConfig(
            name="researcher",
            model="gpt-4o",
            provider="openai",
            system_prompt="You are a research agent.",
            temperature=0.2,
            max_tokens=8192,
            max_iterations=20,
            timeout_secs=600.0,
            context_limit=256000,
        )
        assert cfg.name == "researcher"
        assert cfg.model == "gpt-4o"
        assert cfg.provider == "openai"
        assert cfg.system_prompt == "You are a research agent."
        assert cfg.temperature == 0.2
        assert cfg.max_tokens == 8192
        assert cfg.max_iterations == 20
        assert cfg.timeout_secs == 600.0
        assert cfg.context_limit == 256000

    def test_temperature_is_float(self):
        cfg = SubagentConfig(temperature=1.0)
        assert isinstance(cfg.temperature, float)

    def test_timeout_is_float(self):
        cfg = SubagentConfig(timeout_secs=123.456)
        assert isinstance(cfg.timeout_secs, float)

    def test_max_tokens_is_int(self):
        cfg = SubagentConfig(max_tokens=2048)
        assert isinstance(cfg.max_tokens, int)

    def test_partial_override(self):
        """Fields not specified should use defaults."""
        cfg = SubagentConfig(name="custom", temperature=0.0)
        assert cfg.name == "custom"
        assert cfg.temperature == 0.0
        assert cfg.model == ""       # default
        assert cfg.provider == ""    # default
        assert cfg.max_tokens == 4096  # default


# ══════════════════════════════════════════════════════════════════════
# SubagentResult Dataclass Tests
# ══════════════════════════════════════════════════════════════════════

class TestSubagentResult:
    """Test SubagentResult creation and defaults."""

    def test_minimal_creation(self):
        result = SubagentResult(
            task_id="abc123",
            goal="Research topic",
            status=SubagentStatus.COMPLETED,
        )
        assert result.task_id == "abc123"
        assert result.goal == "Research topic"
        assert result.status == SubagentStatus.COMPLETED
        assert result.summary == ""
        assert result.findings == []
        assert result.error == ""
        assert result.tokens_used == 0
        assert result.latency_ms == 0.0
        assert result.tool_calls == 0
        assert result.confidence == 0.5

    def test_full_creation(self):
        result = SubagentResult(
            task_id="xyz789",
            goal="Analyze data",
            status=SubagentStatus.FAILED,
            summary="Analysis failed due to API error.",
            findings=["Finding 1", "Finding 2", "Finding 3"],
            error="Rate limit exceeded",
            tokens_used=500,
            latency_ms=1234.5,
            tool_calls=3,
            confidence=0.1,
        )
        assert result.task_id == "xyz789"
        assert result.goal == "Analyze data"
        assert result.status == SubagentStatus.FAILED
        assert result.summary == "Analysis failed due to API error."
        assert len(result.findings) == 3
        assert result.findings[0] == "Finding 1"
        assert result.error == "Rate limit exceeded"
        assert result.tokens_used == 500
        assert result.latency_ms == 1234.5
        assert result.tool_calls == 3
        assert result.confidence == 0.1

    def test_default_confidence_is_0_5(self):
        result = SubagentResult(
            task_id="t1", goal="g1", status=SubagentStatus.PENDING,
        )
        assert result.confidence == 0.5

    def test_findings_default_is_empty_list(self):
        result = SubagentResult(
            task_id="t1", goal="g1", status=SubagentStatus.PENDING,
        )
        assert result.findings == []
        assert isinstance(result.findings, list)

    def test_all_status_values_accepted(self):
        """Ensure all SubagentStatus values work as the status field."""
        for status in SubagentStatus:
            result = SubagentResult(task_id="t", goal="g", status=status)
            assert result.status == status

    def test_findings_are_mutable_list(self):
        result = SubagentResult(
            task_id="t1", goal="g1", status=SubagentStatus.COMPLETED,
            findings=["a", "b"],
        )
        result.findings.append("c")
        assert result.findings == ["a", "b", "c"]


# ══════════════════════════════════════════════════════════════════════
# DebateResult Dataclass Tests
# ══════════════════════════════════════════════════════════════════════

class TestDebateResult:
    """Test DebateResult creation and defaults."""

    def test_minimal_creation(self):
        result = DebateResult(
            task_id="debate-001",
            goal="Which framework is best?",
        )
        assert result.task_id == "debate-001"
        assert result.goal == "Which framework is best?"
        assert result.consensus == ""
        assert result.agent_a_position == ""
        assert result.agent_b_position == ""
        assert result.agreement is False
        assert result.key_differences == []
        assert result.confidence == 0.0

    def test_full_creation(self):
        result = DebateResult(
            task_id="deb-002",
            goal="Is Python better than JavaScript?",
            consensus="Both have strengths depending on use case.",
            agent_a_position="Python is better for data science.",
            agent_b_position="JavaScript dominates web development.",
            agreement=True,
            key_differences=["Ecosystem", "Performance model", "Type system"],
            confidence=0.85,
        )
        assert result.task_id == "deb-002"
        assert "Python" in result.agent_a_position
        assert "JavaScript" in result.agent_b_position
        assert result.agreement is True
        assert len(result.key_differences) == 3
        assert result.confidence == 0.85

    def test_default_agreement_is_false(self):
        result = DebateResult(task_id="d1", goal="g1")
        assert result.agreement is False

    def test_default_confidence_is_zero(self):
        result = DebateResult(task_id="d1", goal="g1")
        assert result.confidence == 0.0

    def test_default_key_differences_empty(self):
        result = DebateResult(task_id="d1", goal="g1")
        assert result.key_differences == []
        assert isinstance(result.key_differences, list)


# ══════════════════════════════════════════════════════════════════════
# Subagent Constructor + Pure Method Tests
# ══════════════════════════════════════════════════════════════════════

class TestSubagentConstructor:
    """Test Subagent instantiation with various constructor arguments."""

    def test_constructor_with_config_only(self):
        cfg = SubagentConfig(name="test-agent")
        agent = Subagent(config=cfg)
        assert agent.config is cfg
        assert agent.config.name == "test-agent"
        assert agent.provider_registry is None
        assert agent.tool_registry is None
        assert agent.session_store is None

    def test_constructor_with_all_registries(self):
        cfg = SubagentConfig()
        mock_pr = object()
        mock_tr = object()
        mock_ss = object()
        agent = Subagent(
            config=cfg,
            provider_registry=mock_pr,
            tool_registry=mock_tr,
            session_store=mock_ss,
        )
        assert agent.provider_registry is mock_pr
        assert agent.tool_registry is mock_tr
        assert agent.session_store is mock_ss

    def test_constructor_stores_config_by_reference(self):
        cfg = SubagentConfig(name="mutable")
        agent = Subagent(config=cfg)
        cfg.name = "changed"
        assert agent.config.name == "changed"

    def test_status_starts_as_pending(self):
        agent = Subagent(config=SubagentConfig())
        assert agent.status == SubagentStatus.PENDING

    def test_status_is_readonly_property(self):
        agent = Subagent(config=SubagentConfig())
        with pytest.raises(AttributeError):
            agent.status = SubagentStatus.RUNNING


class TestSubagentParseToolCalls:
    """Test _parse_tool_calls with various input formats."""

    def test_parse_backtick_tool_call_format(self):
        agent = Subagent(config=SubagentConfig())
        content = '```tool_call\n{"name": "search", "arguments": {"query": "Python"}}\n```'
        result = agent._parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert result[0]["arguments"]["query"] == "Python"

    def test_parse_angle_bracket_tool_call_format(self):
        agent = Subagent(config=SubagentConfig())
        content = '<tool_call>{"name": "read_file", "arguments": {"path": "/tmp/x"}}</tool_call>'
        result = agent._parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["name"] == "read_file"
        assert result[0]["arguments"]["path"] == "/tmp/x"

    def test_no_tool_calls_in_plain_text(self):
        agent = Subagent(config=SubagentConfig())
        content = "Here is a plain text response with no tool calls at all."
        result = agent._parse_tool_calls(content)
        assert result == []

    def test_malformed_json_in_backtick_format(self):
        agent = Subagent(config=SubagentConfig())
        content = '```tool_call\n{not valid json!!!}\n```'
        result = agent._parse_tool_calls(content)
        # Malformed JSON should be silently skipped
        assert result == []

    def test_malformed_json_in_angle_bracket_format(self):
        agent = Subagent(config=SubagentConfig())
        content = "<tool_call>{broken json}</tool_call>"
        result = agent._parse_tool_calls(content)
        assert result == []

    def test_multiple_tool_calls_mixed_formats(self):
        agent = Subagent(config=SubagentConfig())
        content = (
            '```tool_call\n{"name": "search", "arguments": {"q": "a"}}\n```\n'
            'Some text in between.\n'
            '<tool_call>{"name": "fetch", "arguments": {"url": "http://x"}}</tool_call>\n'
            '```tool_call\n{"name": "parse", "arguments": {}}\n```'
        )
        result = agent._parse_tool_calls(content)
        assert len(result) == 3
        # Backtick-format matches are found first (in order),
        # then angle-bracket matches.
        names = [c["name"] for c in result]
        assert "search" in names
        assert "fetch" in names
        assert "parse" in names

    def test_tool_call_without_arguments_key(self):
        agent = Subagent(config=SubagentConfig())
        content = '<tool_call>{"name": "ping"}</tool_call>'
        result = agent._parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["name"] == "ping"
        # No arguments key present
        assert "arguments" not in result[0]

    def test_empty_content_returns_empty_list(self):
        agent = Subagent(config=SubagentConfig())
        result = agent._parse_tool_calls("")
        assert result == []

    def test_backtick_block_with_newline_before_json(self):
        agent = Subagent(config=SubagentConfig())
        content = '```tool_call\n{"name": "x", "arguments": {"k": "v"}}\n```'
        result = agent._parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["name"] == "x"

    def test_mixed_valid_and_invalid(self):
        """Only valid JSON tool calls are returned; invalid ones are skipped."""
        agent = Subagent(config=SubagentConfig())
        content = (
            '```tool_call\n{"name": "good", "arguments": {}}\n```\n'
            '<tool_call>{bad}</tool_call>\n'
            '<tool_call>{"name": "also_good", "arguments": {"x": 1}}</tool_call>'
        )
        result = agent._parse_tool_calls(content)
        assert len(result) == 2
        names = [c["name"] for c in result]
        assert names == ["good", "also_good"]

    def test_multiple_tool_calls_same_format(self):
        """Multiple tool calls in the same format (backtick only)."""
        agent = Subagent(config=SubagentConfig())
        content = (
            '```tool_call\n{"name": "a", "arguments": {}}\n```\n'
            '```tool_call\n{"name": "b", "arguments": {}}\n```'
        )
        result = agent._parse_tool_calls(content)
        assert len(result) == 2
        assert result[0]["name"] == "a"
        assert result[1]["name"] == "b"


class TestSubagentExtractFindings:
    """Test _extract_findings with various input formats."""

    def test_bullet_with_dash(self):
        agent = Subagent(config=SubagentConfig())
        content = (
            "Here are the results:\n"
            "- First finding\n"
            "- Second finding\n"
            "- Third finding\n"
        )
        findings = agent._extract_findings(content)
        assert findings == ["First finding", "Second finding", "Third finding"]

    def test_bullet_with_asterisk(self):
        agent = Subagent(config=SubagentConfig())
        content = "* Alpha\n* Beta\n* Gamma\n"
        findings = agent._extract_findings(content)
        assert findings == ["Alpha", "Beta", "Gamma"]

    def test_bullet_with_bullet_char(self):
        agent = Subagent(config=SubagentConfig())
        content = "• Point one\n• Point two\n• Point three\n"
        findings = agent._extract_findings(content)
        assert findings == ["Point one", "Point two", "Point three"]

    def test_no_bullet_points(self):
        agent = Subagent(config=SubagentConfig())
        content = "This is a paragraph with no bullet points.\nJust plain text."
        findings = agent._extract_findings(content)
        assert findings == []

    def test_caps_at_five_findings(self):
        agent = Subagent(config=SubagentConfig())
        content = "\n".join(f"- finding {i}" for i in range(1, 11))
        findings = agent._extract_findings(content)
        assert len(findings) == 5
        assert findings == [
            "finding 1", "finding 2", "finding 3", "finding 4", "finding 5",
        ]

    def test_mixed_bullet_styles(self):
        agent = Subagent(config=SubagentConfig())
        content = "- dash bullet\n* star bullet\n• dot bullet\n"
        findings = agent._extract_findings(content)
        assert len(findings) == 3
        assert findings == ["dash bullet", "star bullet", "dot bullet"]

    def test_non_bullet_dash_in_text(self):
        """Lines starting with dash but not a space after ' -' should not match."""
        agent = Subagent(config=SubagentConfig())
        content = "This is-not a bullet\n- actual bullet\nAnother-line\n"
        findings = agent._extract_findings(content)
        assert findings == ["actual bullet"]

    def test_empty_content(self):
        agent = Subagent(config=SubagentConfig())
        findings = agent._extract_findings("")
        assert findings == []

    def test_leading_whitespace_bullets(self):
        """Bullet points with leading spaces/tabs should still be detected."""
        agent = Subagent(config=SubagentConfig())
        content = "  - indented finding\n\t- tabbed finding\n"
        findings = agent._extract_findings(content)
        assert findings == ["indented finding", "tabbed finding"]

    def test_trimmed_contents(self):
        """Bullet text is stripped of leading/trailing whitespace.

        Note: _extract_findings takes ``stripped[2:]`` which skips the
        bullet char + exactly one space.  Extra spaces after the bullet
        marker remain — the method strips the full line but only slices
        after index 2.
        """
        agent = Subagent(config=SubagentConfig())
        content = "- single_space\n-  double_space\n"
        findings = agent._extract_findings(content)
        assert findings == ["single_space", " double_space"]

    def test_returns_list_always(self):
        """Even with no matches, return type is always list."""
        agent = Subagent(config=SubagentConfig())
        findings = agent._extract_findings("no bullets here")
        assert isinstance(findings, list)
        assert findings == []


# ══════════════════════════════════════════════════════════════════════
# SubagentOrchestrator Constructor Tests
# ══════════════════════════════════════════════════════════════════════

class TestSubagentOrchestratorConstructor:
    """Test SubagentOrchestrator instantiation with defaults and custom values."""

    def test_default_constructor(self):
        orch = SubagentOrchestrator()
        assert orch.provider_registry is None
        assert orch.tool_registry is None
        assert orch.session_store is None
        assert orch.default_model == "gpt-4o-mini"
        assert orch.default_provider == "openai"
        assert orch.max_concurrent == 3

    def test_custom_model_and_provider(self):
        orch = SubagentOrchestrator(
            default_model="claude-sonnet-4",
            default_provider="anthropic",
        )
        assert orch.default_model == "claude-sonnet-4"
        assert orch.default_provider == "anthropic"
        # Other defaults unchanged
        assert orch.max_concurrent == 3

    def test_custom_max_concurrent(self):
        orch = SubagentOrchestrator(max_concurrent=10)
        assert orch.max_concurrent == 10

    def test_full_custom_constructor(self):
        mock_pr = object()
        mock_tr = object()
        mock_ss = object()
        orch = SubagentOrchestrator(
            provider_registry=mock_pr,
            tool_registry=mock_tr,
            session_store=mock_ss,
            default_model="gpt-4o",
            default_provider="azure",
            max_concurrent=5,
        )
        assert orch.provider_registry is mock_pr
        assert orch.tool_registry is mock_tr
        assert orch.session_store is mock_ss
        assert orch.default_model == "gpt-4o"
        assert orch.default_provider == "azure"
        assert orch.max_concurrent == 5

    def test_max_concurrent_is_int(self):
        orch = SubagentOrchestrator(max_concurrent=7)
        assert isinstance(orch.max_concurrent, int)

    def test_zero_max_concurrent_allowed(self):
        """max_concurrent=0 should be accepted (constructor doesn't validate)."""
        orch = SubagentOrchestrator(max_concurrent=0)
        assert orch.max_concurrent == 0
