"""Tests for dragon/tool/guardrails.py"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
import pytest
from dragon.tool.guardrails import (
    classify_tool_failure, ToolGuardrails, GuardrailConfig,
    GuardrailCheck, GuardrailAction, CheckType, ToolCallSignature,
)

class TestToolCallSignature:
    def test_same_args_same_hash(self):
        s1 = ToolCallSignature.from_call("test", {"a": 1, "b": 2})
        s2 = ToolCallSignature.from_call("test", {"b": 2, "a": 1})
        assert hash(s1) == hash(s2)

    def test_different_tools(self):
        s1 = ToolCallSignature.from_call("tool_a", {"x": 1})
        s2 = ToolCallSignature.from_call("tool_b", {"x": 1})
        assert s1 != s2

class TestGuardrailCheck:
    def test_blocked(self):
        c = GuardrailCheck(action=GuardrailAction.BLOCK, check_type=CheckType.DANGEROUS, message="no")
        assert c.blocked is True

    def test_warned(self):
        c = GuardrailCheck(action=GuardrailAction.WARN, check_type=CheckType.PRE_EXECUTION, message="careful")
        assert c.warned is True

    def test_allow(self):
        c = GuardrailCheck(action=GuardrailAction.ALLOW, check_type=CheckType.PRE_EXECUTION, message="ok")
        assert c.blocked is False

    def test_to_dict(self):
        c = GuardrailCheck(action=GuardrailAction.BLOCK, check_type=CheckType.DANGEROUS, message="bad")
        d = c.to_dict()
        assert isinstance(d, dict)

class TestGuardrailConfig:
    def test_defaults(self):
        cfg = GuardrailConfig()
        assert cfg is not None

    def test_from_mapping(self):
        cfg = GuardrailConfig.from_mapping({"max_args_size": 1000})
        assert cfg is not None

class TestToolGuardrails:
    def test_reset_for_turn(self):
        g = ToolGuardrails(config=GuardrailConfig())
        g.reset_for_turn()

    def test_post_filter_text(self):
        g = ToolGuardrails(config=GuardrailConfig())
        result = g.post_filter_text("Hello world")
        assert isinstance(result, str)

class TestClassifyToolFailure:
    def test_returns_tuple(self):
        result = classify_tool_failure("terminal", "timed out")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_handles_various_messages(self):
        for msg in ["Permission denied", "404 Not Found", "Connection refused"]:
            result = classify_tool_failure("test", msg)
            assert isinstance(result, tuple)
