"""
Panda Agent Task Interrupt Manager.

Enables graceful interruption of long-running agent tasks.
Inspired by Hermes Agent's _interrupt_requested pattern.

Usage:
    im = InterruptManager()
    
    async with im.task("sess_123") as check:
        for step in pipeline:
            check()  # raises TaskInterrupted if /v1/interrupt/sess_123 was called
            await do_work()
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional, Set

logger = logging.getLogger("panda.interrupt")


class TaskInterrupted(Exception):
    """Raised when a task is interrupted by user request."""
    def __init__(self, session_id: str, reason: str = "User requested interrupt"):
        self.session_id = session_id
        self.reason = reason
        super().__init__(f"Task {session_id} interrupted: {reason}")


class TaskState(Enum):
    RUNNING = "running"
    INTERRUPTING = "interrupting"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskStatus:
    session_id: str
    state: TaskState
    started_at: float
    interrupted_at: Optional[float] = None
    completed_at: Optional[float] = None
    progress: str = ""          # human-readable progress description
    progress_pct: float = 0.0   # 0-100
    error: Optional[str] = None


class InterruptManager:
    """
    Manages interruptible tasks.
    
    Thread-safe. One instance per process (singleton).
    
    Flow:
    1. Client calls POST /v1/chat → creates task "sess_123"
    2. Pipeline checks task.interrupt_requested periodically
    3. Client calls POST /v1/interrupt/sess_123 → sets flag
    4. Pipeline raises TaskInterrupted → returns partial result
    """

    def __init__(self):
        self._interrupt_flags: Dict[str, threading.Event] = {}
        self._task_status: Dict[str, TaskStatus] = {}
        self._lock = threading.Lock()
        self._callbacks: Dict[str, list[Callable]] = {}

    # ── Interrupt API ──────────────────────────────────────

    def request_interrupt(self, session_id: str, reason: str = "User requested interrupt") -> bool:
        """
        Request interruption of a running task.
        
        Returns:
            True if task was found and flagged, False if no such task.
        """
        with self._lock:
            if session_id not in self._task_status:
                return False
            
            event = self._interrupt_flags.get(session_id)
            if event:
                event.set()
            
            status = self._task_status[session_id]
            status.state = TaskState.INTERRUPTING
            status.interrupted_at = time.time()
            
            logger.info("Interrupt requested: session=%s reason=%s", session_id, reason)
            return True

    def is_interrupted(self, session_id: str) -> bool:
        """Check if a task has been interrupted."""
        event = self._interrupt_flags.get(session_id)
        return event.is_set() if event else False

    def check(self, session_id: str):
        """
        Check interrupt flag and raise TaskInterrupted if set.
        
        Usage in pipeline:
            interrupt_mgr.check(session_id)
        """
        if self.is_interrupted(session_id):
            status = self._task_status.get(session_id)
            reason = "User requested interrupt"
            if status:
                status.state = TaskState.INTERRUPTED
            raise TaskInterrupted(session_id, reason)

    def on_interrupt(self, session_id: str, callback: Callable):
        """Register a cleanup callback for when task is interrupted."""
        with self._lock:
            if session_id not in self._callbacks:
                self._callbacks[session_id] = []
            self._callbacks[session_id].append(callback)

    # ── Task lifecycle ─────────────────────────────────────

    def start_task(self, session_id: str, description: str = "") -> TaskStatus:
        """Register a new running task."""
        with self._lock:
            event = threading.Event()
            self._interrupt_flags[session_id] = event
            
            status = TaskStatus(
                session_id=session_id,
                state=TaskState.RUNNING,
                started_at=time.time(),
                progress=description,
            )
            self._task_status[session_id] = status
            
            logger.debug("Task started: session=%s desc=%s", session_id, description)
            return status

    def update_progress(self, session_id: str, progress: str, pct: float = -1):
        """Update task progress description."""
        with self._lock:
            if session_id in self._task_status:
                s = self._task_status[session_id]
                s.progress = progress
                if pct >= 0:
                    s.progress_pct = min(100, max(0, pct))

    def complete_task(self, session_id: str, error: str = None):
        """Mark task as completed or failed."""
        with self._lock:
            if session_id in self._task_status:
                s = self._task_status[session_id]
                if error:
                    s.state = TaskState.FAILED
                    s.error = error
                elif s.state == TaskState.INTERRUPTING:
                    s.state = TaskState.INTERRUPTED
                else:
                    s.state = TaskState.COMPLETED
                s.completed_at = time.time()
            
            # Fire cleanup callbacks
            if session_id in self._callbacks:
                for cb in self._callbacks[session_id]:
                    try:
                        cb()
                    except Exception:
                        logger.exception("Callback failed for %s", session_id)
                del self._callbacks[session_id]

            # Clean up (keep status for 5 minutes for querying)
            self._interrupt_flags.pop(session_id, None)

    def get_status(self, session_id: str) -> Optional[TaskStatus]:
        """Get current task status."""
        with self._lock:
            return self._task_status.get(session_id)

    def list_tasks(self) -> Dict[str, TaskStatus]:
        """List all tasks."""
        with self._lock:
            return dict(self._task_status)

    def cleanup_stale(self, max_age_secs: float = 300):
        """Remove completed task statuses older than max_age_secs."""
        now = time.time()
        with self._lock:
            stale = [
                sid for sid, s in self._task_status.items()
                if s.completed_at and (now - s.completed_at) > max_age_secs
            ]
            for sid in stale:
                del self._task_status[sid]
                self._interrupt_flags.pop(sid, None)

    # ── Async context manager ──────────────────────────────

    class TaskContext:
        """Async context manager for interruptible tasks."""
        
        def __init__(self, manager: "InterruptManager", session_id: str):
            self.manager = manager
            self.session_id = session_id
            self._interrupted = False

        async def __aenter__(self):
            self.manager.start_task(self.session_id)
            return self._check

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            is_interrupt = isinstance(exc_val, TaskInterrupted)
            error = str(exc_val) if exc_val and not is_interrupt else None
            self.manager.complete_task(self.session_id, error=error)
            return is_interrupt  # suppress TaskInterrupted

        def _check(self):
            """Check interrupt flag."""
            self.manager.check(self.session_id)

    def task(self, session_id: str) -> TaskContext:
        """Create an async context manager for an interruptible task."""
        return self.TaskContext(self, session_id)

    # ── Async interruptible helper ─────────────────────────

    async def run_interruptible(
        self,
        session_id: str,
        coro,
        progress_callback: Callable[[str, float], None] = None,
    ):
        """
        Run a coroutine with interrupt checking.
        
        Aborts and returns partial result on interrupt.
        """
        try:
            async with self.task(session_id) as check:
                check()
                result = await coro(check=check, progress=progress_callback)
                return {"status": "completed", "result": result}
        except TaskInterrupted:
            return {"status": "interrupted", "result": None}


# ── Process-wide singleton ─────────────────────────────────

_interrupt_manager: Optional[InterruptManager] = None
_lock = threading.Lock()


def get_interrupt_manager() -> InterruptManager:
    """Get or create the process-wide InterruptManager singleton."""
    global _interrupt_manager
    if _interrupt_manager is None:
        with _lock:
            if _interrupt_manager is None:
                _interrupt_manager = InterruptManager()
    return _interrupt_manager
