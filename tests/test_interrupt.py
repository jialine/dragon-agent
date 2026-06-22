"""
Unit tests for InterruptManager — task lifecycle and interruption.
"""
import threading
import time

import pytest

from dragon.interrupt import (
    InterruptManager,
    TaskInterrupted,
    TaskState,
    TaskStatus,
    get_interrupt_manager,
)


# ---------------------------------------------------------------------------
# TaskState enum
# ---------------------------------------------------------------------------


class TestTaskState:
    def test_enum_values(self):
        assert TaskState.RUNNING.value == "running"
        assert TaskState.INTERRUPTING.value == "interrupting"
        assert TaskState.INTERRUPTED.value == "interrupted"
        assert TaskState.COMPLETED.value == "completed"
        assert TaskState.FAILED.value == "failed"

    def test_enum_length(self):
        assert len(TaskState) == 5

    def test_enum_isinstance(self):
        for state in TaskState:
            assert isinstance(state, TaskState)


# ---------------------------------------------------------------------------
# TaskStatus dataclass
# ---------------------------------------------------------------------------


class TestTaskStatus:
    def test_dataclass_defaults(self):
        ts = TaskStatus(session_id="s1", state=TaskState.RUNNING, started_at=100.0)
        assert ts.session_id == "s1"
        assert ts.state == TaskState.RUNNING
        assert ts.progress == ""
        assert ts.progress_pct == 0.0
        assert ts.error is None
        assert ts.interrupted_at is None
        assert ts.completed_at is None

    def test_full_fields(self):
        ts = TaskStatus(
            session_id="s1",
            state=TaskState.COMPLETED,
            started_at=100.0,
            interrupted_at=None,
            completed_at=200.0,
            progress="Done",
            progress_pct=100.0,
            error=None,
        )
        assert ts.completed_at == 200.0
        assert ts.progress == "Done"
        assert ts.progress_pct == 100.0

    def test_interrupted_fields(self):
        ts = TaskStatus(
            session_id="s1",
            state=TaskState.INTERRUPTED,
            started_at=100.0,
            interrupted_at=150.0,
            completed_at=150.0,
            progress="一半",
            progress_pct=50.0,
        )
        assert ts.interrupted_at == 150.0
        assert ts.progress_pct == 50.0

    def test_failed_fields(self):
        ts = TaskStatus(
            session_id="s1",
            state=TaskState.FAILED,
            started_at=100.0,
            completed_at=200.0,
            error="Something crashed",
        )
        assert ts.error == "Something crashed"
        assert ts.state == TaskState.FAILED


# ---------------------------------------------------------------------------
# TaskInterrupted exception
# ---------------------------------------------------------------------------


class TestTaskInterrupted:
    def test_exception_message(self):
        exc = TaskInterrupted("sess_x", "testing")
        assert str(exc) == "Task sess_x interrupted: testing"
        assert exc.session_id == "sess_x"
        assert exc.reason == "testing"

    def test_default_reason(self):
        exc = TaskInterrupted("sess_y")
        assert "interrupted" in str(exc)
        assert exc.session_id == "sess_y"

    def test_is_exception(self):
        exc = TaskInterrupted("sess_z")
        assert isinstance(exc, Exception)

    def test_can_be_caught(self):
        try:
            raise TaskInterrupted("s", "stop")
        except TaskInterrupted as e:
            assert e.session_id == "s"


# ---------------------------------------------------------------------------
# InterruptManager — basic lifecycle
# ---------------------------------------------------------------------------


class TestInterruptManager:
    def setup_method(self):
        self.im = InterruptManager()

    def test_initial_state(self):
        tasks = self.im.list_tasks()
        assert tasks == {}

    def test_start_task(self):
        self.im.start_task("sess_1", description="Test task")
        status = self.im.get_status("sess_1")
        assert status is not None
        assert status.state == TaskState.RUNNING

    def test_start_task_returns_status(self):
        status = self.im.start_task("sess_1", description="Test")
        assert isinstance(status, TaskStatus)
        assert status.session_id == "sess_1"
        assert status.state == TaskState.RUNNING
        assert status.progress == "Test"

    def test_start_task_twice_overwrites(self):
        self.im.start_task("sess_1", description="First")
        self.im.start_task("sess_1", description="Second")
        status = self.im.get_status("sess_1")
        assert status.progress == "Second"

    def test_request_interrupt(self):
        self.im.start_task("sess_2")
        ok = self.im.request_interrupt("sess_2", "User cancel")
        assert ok is True
        status = self.im.get_status("sess_2")
        assert status.state == TaskState.INTERRUPTING

    def test_interrupt_nonexistent(self):
        ok = self.im.request_interrupt("ghost", "test")
        assert ok is False

    def test_interrupt_sets_interrupted_at(self):
        self.im.start_task("sess_t")
        self.im.request_interrupt("sess_t")
        status = self.im.get_status("sess_t")
        assert status.interrupted_at is not None

    def test_complete_task(self):
        self.im.start_task("sess_3")
        self.im.complete_task("sess_3")
        status = self.im.get_status("sess_3")
        assert status.state == TaskState.COMPLETED

    def test_complete_task_sets_completed_at(self):
        self.im.start_task("sess_3")
        self.im.complete_task("sess_3")
        status = self.im.get_status("sess_3")
        assert status.completed_at is not None

    def test_fail_task_via_complete_with_error(self):
        self.im.start_task("sess_4")
        self.im.complete_task("sess_4", error="Something broke")
        status = self.im.get_status("sess_4")
        assert status.state == TaskState.FAILED
        assert status.error == "Something broke"

    def test_list_tasks_multiple(self):
        self.im.start_task("a")
        self.im.start_task("b")
        self.im.complete_task("a")
        tasks = self.im.list_tasks()
        assert len(tasks) == 2
        assert tasks["a"].state == TaskState.COMPLETED
        assert tasks["b"].state == TaskState.RUNNING

    def test_get_status_nonexistent(self):
        assert self.im.get_status("ghost") is None


# ---------------------------------------------------------------------------
# InterruptManager — interrupt workflow
# ---------------------------------------------------------------------------


class TestInterruptWorkflow:
    def setup_method(self):
        self.im = InterruptManager()

    def test_interrupt_then_complete_results_interrupted(self):
        self.im.start_task("sess_x")
        self.im.request_interrupt("sess_x", "cancel")
        # completing an interrupting task marks it INTERRUPTED
        self.im.complete_task("sess_x")
        status = self.im.get_status("sess_x")
        assert status.state == TaskState.INTERRUPTED

    def test_is_interrupted_after_request(self):
        self.im.start_task("sess_x")
        assert self.im.is_interrupted("sess_x") is False
        self.im.request_interrupt("sess_x")
        assert self.im.is_interrupted("sess_x") is True

    def test_is_interrupted_nonexistent(self):
        assert self.im.is_interrupted("ghost") is False

    def test_check_raises_when_interrupted(self):
        self.im.start_task("sess_x")
        self.im.request_interrupt("sess_x")
        with pytest.raises(TaskInterrupted) as exc_info:
            self.im.check("sess_x")
        assert exc_info.value.session_id == "sess_x"

    def test_check_does_not_raise_when_not_interrupted(self):
        self.im.start_task("sess_x")
        # Should not raise
        self.im.check("sess_x")

    def test_check_marks_as_interrupted(self):
        self.im.start_task("sess_x")
        self.im.request_interrupt("sess_x")
        try:
            self.im.check("sess_x")
        except TaskInterrupted:
            pass
        status = self.im.get_status("sess_x")
        assert status.state == TaskState.INTERRUPTED

    def test_double_interrupt_idempotent(self):
        self.im.start_task("sess_x")
        assert self.im.request_interrupt("sess_x") is True
        assert self.im.request_interrupt("sess_x") is True  # second still True
        status = self.im.get_status("sess_x")
        assert status.state == TaskState.INTERRUPTING


# ---------------------------------------------------------------------------
# InterruptManager — progress updates
# ---------------------------------------------------------------------------


class TestProgressUpdates:
    def setup_method(self):
        self.im = InterruptManager()

    def test_update_progress(self):
        self.im.start_task("sess_1", description="Starting")
        self.im.update_progress("sess_1", "Step 1 done", pct=25.0)
        status = self.im.get_status("sess_1")
        assert status.progress == "Step 1 done"
        assert status.progress_pct == 25.0

    def test_update_progress_clamps_pct(self):
        self.im.start_task("sess_1")
        self.im.update_progress("sess_1", "Over", pct=150.0)
        assert self.im.get_status("sess_1").progress_pct == 100.0
        self.im.update_progress("sess_1", "Low", pct=5.0)
        assert self.im.get_status("sess_1").progress_pct == 5.0
        self.im.update_progress("sess_1", "Zero", pct=0.0)
        assert self.im.get_status("sess_1").progress_pct == 0.0

    def test_update_progress_negative_pct_ignored(self):
        self.im.start_task("sess_1")
        self.im.update_progress("sess_1", "Keep old", pct=-1)
        status = self.im.get_status("sess_1")
        # -1 means "don't update" so pct stays at 0
        assert status.progress_pct == 0.0

    def test_update_progress_nonexistent_noop(self):
        # Should not raise
        self.im.update_progress("ghost", "test", pct=50.0)


# ---------------------------------------------------------------------------
# InterruptManager — concurrent / multiple tasks
# ---------------------------------------------------------------------------


class TestMultipleConcurrentTasks:
    def setup_method(self):
        self.im = InterruptManager()

    def test_multiple_tasks_independent_interrupts(self):
        self.im.start_task("a")
        self.im.start_task("b")
        self.im.request_interrupt("a", "stop a")
        assert self.im.is_interrupted("a") is True
        assert self.im.is_interrupted("b") is False

    def test_many_tasks(self):
        for i in range(100):
            self.im.start_task(f"task_{i}")
        tasks = self.im.list_tasks()
        assert len(tasks) == 100
        for i in range(100):
            assert tasks[f"task_{i}"].state == TaskState.RUNNING

    def test_concurrent_interrupts_thread_safety(self):
        """Exercise thread safety with concurrent start/interrupt."""
        self.im.start_task("shared")

        def _interrupter():
            self.im.request_interrupt("shared", "from thread")

        threads = [threading.Thread(target=_interrupter) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert self.im.is_interrupted("shared") is True

    def test_complete_with_no_error_no_crash(self):
        self.im.start_task("s")
        self.im.complete_task("s")
        # completing again should be safe
        self.im.complete_task("s")


# ---------------------------------------------------------------------------
# InterruptManager — cleanup stale
# ---------------------------------------------------------------------------


class TestCleanupStale:
    def setup_method(self):
        self.im = InterruptManager()

    def test_cleanup_stale_removes_old(self):
        self.im.start_task("old_task")
        self.im.complete_task("old_task")
        # Manually set completed_at far in the past
        status = self.im._task_status["old_task"]
        status.completed_at = time.time() - 99999  # very old
        self.im.cleanup_stale(max_age_secs=1)
        assert self.im.get_status("old_task") is None

    def test_cleanup_stale_keeps_recent(self):
        self.im.start_task("recent_task")
        self.im.complete_task("recent_task")
        self.im.cleanup_stale(max_age_secs=3600)
        assert self.im.get_status("recent_task") is not None

    def test_cleanup_stale_keeps_running(self):
        self.im.start_task("running_task")
        self.im.cleanup_stale(max_age_secs=1)
        assert self.im.get_status("running_task") is not None

    def test_cleanup_stale_empty(self):
        # Should not raise
        self.im.cleanup_stale()


# ---------------------------------------------------------------------------
# InterruptManager — callbacks
# ---------------------------------------------------------------------------


class TestInterruptCallbacks:
    def setup_method(self):
        self.im = InterruptManager()

    def test_on_interrupt_callback_fires_on_complete(self):
        results = []

        def callback():
            results.append("fired")

        self.im.start_task("cb_test")
        self.im.on_interrupt("cb_test", callback)
        self.im.request_interrupt("cb_test")
        self.im.complete_task("cb_test")
        assert results == ["fired"]

    def test_multiple_callbacks(self):
        results = []

        self.im.start_task("cb_test")
        self.im.on_interrupt("cb_test", lambda: results.append(1))
        self.im.on_interrupt("cb_test", lambda: results.append(2))
        self.im.request_interrupt("cb_test")
        self.im.complete_task("cb_test")
        assert results == [1, 2]


# ---------------------------------------------------------------------------
# InterruptManager — async context manager (sync test of TaskContext wiring)
# ---------------------------------------------------------------------------


class TestTaskContext:
    """Tests for TaskContext creation — actual async usage in async tests below."""

    def test_task_context_creation(self):
        im = InterruptManager()
        ctx = im.task("test_session")
        assert ctx.session_id == "test_session"
        assert ctx.manager is im

    @pytest.mark.asyncio
    async def test_task_context_success(self):
        im = InterruptManager()
        async with im.task("ac_test") as check:
            check()  # should not raise
        status = im.get_status("ac_test")
        assert status.state == TaskState.COMPLETED

    @pytest.mark.asyncio
    async def test_task_context_interrupted(self):
        im = InterruptManager()
        async with im.task("ac_test") as check:
            im.request_interrupt("ac_test")
            with pytest.raises(TaskInterrupted):
                check()
        # check() already set state to INTERRUPTED; __aexit__ calls complete_task
        # which sees INTERRUPTED (not INTERRUPTING) and sets COMPLETED
        status = im.get_status("ac_test")
        assert status.state == TaskState.COMPLETED
        assert status.interrupted_at is not None

    @pytest.mark.asyncio
    async def test_task_context_exception(self):
        im = InterruptManager()
        with pytest.raises(ValueError):
            async with im.task("ac_test"):
                raise ValueError("boom")
        status = im.get_status("ac_test")
        assert status.state == TaskState.FAILED
        assert "boom" in status.error


# ---------------------------------------------------------------------------
# InterruptManager — singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_interrupt_manager_returns_same_instance(self):
        im1 = get_interrupt_manager()
        im2 = get_interrupt_manager()
        assert im1 is im2

    def test_get_interrupt_manager_is_interrupt_manager(self):
        im = get_interrupt_manager()
        assert isinstance(im, InterruptManager)


# ---------------------------------------------------------------------------
# InterruptManager — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def setup_method(self):
        self.im = InterruptManager()

    def test_complete_nonexistent_noop(self):
        # Should not raise
        self.im.complete_task("ghost")

    def test_long_session_ids(self):
        long_id = "x" * 1000
        self.im.start_task(long_id)
        assert self.im.get_status(long_id) is not None

    def test_special_chars_in_session_id(self):
        sid = "session/with:special@chars#123"
        self.im.start_task(sid)
        assert self.im.get_status(sid) is not None
        self.im.request_interrupt(sid)
        assert self.im.is_interrupted(sid) is True

    def test_empty_description(self):
        status = self.im.start_task("sess_empty")
        assert status.progress == ""

    def test_null_error(self):
        self.im.start_task("sess_null")
        self.im.complete_task("sess_null", error=None)
        status = self.im.get_status("sess_null")
        assert status.state == TaskState.COMPLETED
        assert status.error is None
