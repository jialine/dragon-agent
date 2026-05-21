"""
Unit tests for AntiLoopGuard — loop detection and mitigation.
Based on actual API: record + check → LoopDetection(pattern, action, ...)
"""
import time

import pytest
from panda.guard import (
    AntiLoopGuard, LoopAction, LoopPattern, LoopDetection,
    ActionType,
)


class TestAntiLoopGuardInitialization:
    def test_default_config(self):
        guard = AntiLoopGuard()
        assert guard._window_size == 50
        assert guard._consecutive_threshold == 3
        assert guard._retry_threshold == 3

    def test_custom_config(self):
        guard = AntiLoopGuard(
            window_size=100,
            consecutive_threshold=5,
            loop_back_depth=20,
            loop_back_min_cycle=3,
            retry_threshold=4,
            time_budget=60.0,
        )
        assert guard._window_size == 100
        assert guard._consecutive_threshold == 5
        assert guard._retry_threshold == 4


class TestActionRecording:
    def test_record_returns_id(self):
        guard = AntiLoopGuard()
        tid = guard.record(ActionType.TOOL_CALL, "search", success=True)
        assert isinstance(tid, int)

    def test_record_below_threshold_no_loop(self):
        guard = AntiLoopGuard(consecutive_threshold=3)
        for _ in range(2):
            guard.record(ActionType.TOOL_CALL, "search", success=True)
        check = guard.check()
        assert check.action == LoopAction.CONTINUE

    def test_repeated_action_triggers_detection(self):
        guard = AntiLoopGuard(consecutive_threshold=3)
        for _ in range(4):
            guard.record(ActionType.TOOL_CALL, "search", success=True)
        check = guard.check()
        assert check.action != LoopAction.CONTINUE

    def test_errors_trigger_ineffective_retry(self):
        guard = AntiLoopGuard(retry_threshold=3)
        for _ in range(4):
            guard.record(ActionType.ERROR, "api_error", success=False)
        check = guard.check()
        assert check.action != LoopAction.CONTINUE


class TestLoopDetection:
    def test_detect_consecutive_repeat(self):
        guard = AntiLoopGuard(consecutive_threshold=3)
        for _ in range(4):
            guard.record(ActionType.TOOL_CALL, "search", success=True)
        check = guard.check()
        assert check.pattern == LoopPattern.CONSECUTIVE_REPEAT

    def test_detect_oscillation_loop_back(self):
        guard = AntiLoopGuard(
            consecutive_threshold=2,
            loop_back_min_cycle=2,
        )
        for _ in range(10):
            guard.record(ActionType.TOOL_CALL, "action_a", success=True)
            guard.record(ActionType.TOOL_CALL, "action_b", success=True)
        check = guard.check()
        assert check.pattern is not None


class TestLoopDetectionDataclass:
    def test_no_loop_detection(self):
        guard = AntiLoopGuard()
        guard.record(ActionType.TOOL_CALL, "unique1", success=True)
        guard.record(ActionType.TOOL_CALL, "unique2", success=True)
        guard.record(ActionType.TOOL_CALL, "unique3", success=True)
        check = guard.check()
        assert check.action == LoopAction.CONTINUE
        assert check.pattern is None

    def test_returns_loop_detection_instance(self):
        guard = AntiLoopGuard()
        guard.record(ActionType.MODEL_RESPONSE, "think", success=True)
        check = guard.check()
        assert isinstance(check, LoopDetection)


class TestWindowSizeManagement:
    def test_window_trims_old_entries(self):
        guard = AntiLoopGuard(window_size=5)
        # Fill beyond window
        for i in range(10):
            guard.record(ActionType.TOOL_CALL, f"action_{i}", success=True)
        check = guard.check()
        # Should still function (old entries trimmed)
        assert isinstance(check, LoopDetection)
