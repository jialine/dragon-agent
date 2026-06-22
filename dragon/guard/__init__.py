"""
Dragon Agent — AntiLoop Guard Module
====================================

Detects and mitigates agent loops in real-time. Records every action trace,
analyzes patterns, and returns recommended mitigation actions.

Loop Patterns Detected:
  - CONSECUTIVE_REPEAT : same action+args repeated 3+ times consecutively
  - LOOP_BACK          : oscillating A→B→A→B cycle
  - INEFFECTIVE_RETRY  : same error encountered 3+ times
  - TIME_EXCEEDED      : task exceeds its allocated time budget

Loop Actions (mitigations):
  - CONTINUE           : no loop detected, proceed normally
  - STRATEGY_SWITCH    : change exploration/decomposition strategy
  - ESCALATE           : escalate to a higher-capability model or subsystem
  - HUMAN_ASK          : pause and ask the user for guidance
  - GOAL_RESET         : reformulate or simplify the current goal
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

try:
    import structlog
    logger = structlog.get_logger()
except ImportError:
    import logging
    logger = logging.getLogger("dragon.guard")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActionType(Enum):
    """Categories of traceable actions."""

    TOOL_CALL = auto()
    MODEL_RESPONSE = auto()
    ERROR = auto()
    SYSTEM = auto()


class LoopPattern(Enum):
    """The kind of loop that was detected."""

    CONSECUTIVE_REPEAT = auto()
    LOOP_BACK = auto()
    INEFFECTIVE_RETRY = auto()
    TIME_EXCEEDED = auto()


class LoopAction(Enum):
    """Recommended remediation when a loop is detected."""

    CONTINUE = auto()
    STRATEGY_SWITCH = auto()
    ESCALATE = auto()
    HUMAN_ASK = auto()
    GOAL_RESET = auto()


# ---------------------------------------------------------------------------
# Strategy mapping (used by STRATEGY_SWITCH)
# ---------------------------------------------------------------------------

# When the guard recommends STRATEGY_SWITCH, it also returns a suggested
# alternative strategy.  The mapping below provides sensible defaults.
_STRATEGY_ALTERNATIVES: Dict[str, str] = {
    "DIVERSE": "DEPTH_FIRST",
    "DEPTH_FIRST": "BREADTH_FIRST",
    "BREADTH_FIRST": "DIVERSE",
    "GREEDY": "BEAM_SEARCH",
    "BEAM_SEARCH": "GREEDY",
    "RECURSIVE": "ITERATIVE",
    "ITERATIVE": "RECURSIVE",
    "DEFAULT": "DEPTH_FIRST",
}


def _suggest_alternative(current_strategy: Optional[str]) -> str:
    """Return a suggested alternative strategy name."""
    if current_strategy is None:
        return _STRATEGY_ALTERNATIVES["DEFAULT"]
    return _STRATEGY_ALTERNATIVES.get(
        current_strategy.upper(), _STRATEGY_ALTERNATIVES["DEFAULT"]
    )


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ActionTrace:
    """A single recorded action that the guard inspects for loops.

    Attributes
    ----------
    timestamp : float
        ``time.monotonic()`` when the action occurred.
    action_type : ActionType
        Broad category (tool call, model response, error, …).
    action_name : str
        Human-readable name — tool name, model ID, error type, etc.
    action_hash : str
        Stable content-hash of the action's payload (tool name + arguments).
    result_hash : str
        Stable content-hash of the result returned by the action.
    success : bool
        Whether the action completed without error.
    meta : dict
        Arbitrary extra data attached by the caller.
    """

    timestamp: float
    action_type: ActionType
    action_name: str
    action_hash: str
    result_hash: str = ""
    success: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LoopDetection:
    """Result of a loop-detection pass.

    Attributes
    ----------
    pattern : LoopPattern or None
        The detected loop pattern (``None`` means no loop).
    action : LoopAction
        Recommended remediation (``LoopAction.CONTINUE`` when no loop).
    strategy_suggestion : str or None
        When ``action == STRATEGY_SWITCH``, the suggested alternative.
    detail : str
        Human-readable explanation of the detection.
    implicated_traces : list[int]
        Indices (into the sliding window) of traces that triggered detection.
    """

    pattern: Optional[LoopPattern]
    action: LoopAction
    strategy_suggestion: Optional[str] = None
    detail: str = ""
    implicated_traces: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_content(content: Any) -> str:
    """Return a short, deterministic hash for arbitrary serializable content."""
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _hash_action(action_name: str, arguments: Any) -> str:
    """Produce a combined hash of *what* was invoked."""
    return _hash_content({"name": action_name, "args": arguments})


def _hash_result(result: Any) -> str:
    """Produce a hash of a result payload."""
    return _hash_content(result)


# ---------------------------------------------------------------------------
# AntiLoopGuard
# ---------------------------------------------------------------------------


class AntiLoopGuard:
    """Sliding-window loop detector for agent execution traces.

    Thread-safe.  Designed to be called at the *beginning* of every agent step
    (``record + check``) or in a two-phase fashion (``record`` then ``check``).

    Parameters
    ----------
    window_size : int
        Maximum number of traces to retain.  Older entries are pruned
        automatically.  Default: 50.
    consecutive_threshold : int
        How many identical consecutive actions trigger CONSECUTIVE_REPEAT.
        Default: 3.
    loop_back_depth : int
        Size of the history window inspected for A→B→A→B cycles.  Default: 10.
    loop_back_min_cycle : int
        Minimum cycle repetitions before LOOP_BACK fires.  Default: 2 (i.e.,
        A→B→A→B counts as one repetition of the pair).
    retry_threshold : int
        How many consecutive identical errors (by hash) trigger
        INEFFECTIVE_RETRY.  Default: 3.
    time_budget : float or None
        If set, any action whose ``meta["task_start"]`` was set and whose
        elapsed wall-clock exceeds this budget triggers TIME_EXCEEDED.
    """

    def __init__(
        self,
        window_size: int = 50,
        consecutive_threshold: int = 3,
        loop_back_depth: int = 10,
        loop_back_min_cycle: int = 2,
        retry_threshold: int = 3,
        time_budget: Optional[float] = None,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be >= 1")
        self._window_size = window_size
        self._consecutive_threshold = consecutive_threshold
        self._loop_back_depth = loop_back_depth
        self._loop_back_min_cycle = loop_back_min_cycle
        self._retry_threshold = retry_threshold
        self._time_budget = time_budget

        # OrderedDict preserves insertion order for the sliding window while
        # giving O(1) append + prune.  We use it as a ring via a deque index.
        self._traces: OrderedDict[int, ActionTrace] = OrderedDict()
        self._next_id: int = 0
        self._lock = threading.Lock()

        logger.info(
            "AntiLoopGuard.initialized",
            window_size=window_size,
            consecutive_threshold=consecutive_threshold,
            loop_back_depth=loop_back_depth,
            loop_back_min_cycle=loop_back_min_cycle,
            retry_threshold=retry_threshold,
            time_budget=time_budget,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def trace_count(self) -> int:
        """Number of traces currently in the window (read-only)."""
        with self._lock:
            return len(self._traces)

    def record(
        self,
        action_type: ActionType,
        action_name: str,
        arguments: Any = None,
        result: Any = None,
        success: bool = True,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Record a new action trace and return its unique ID.

        Parameters
        ----------
        action_type : ActionType
        action_name : str
            Tool name, model ID, error type, etc.
        arguments :
            Serializable payload that describes *what* was invoked.
        result :
            Serializable result returned by the action.
        success : bool
        meta : dict or None
            Arbitrary key-value pairs (e.g. ``{"task_start": 12345.0}``).

        Returns
        -------
        int
            Monotonically-increasing trace ID.
        """
        now = time.monotonic()
        action_hash = _hash_action(action_name, arguments if arguments is not None else {})
        result_hash = _hash_result(result) if result is not None else ""

        trace = ActionTrace(
            timestamp=now,
            action_type=action_type,
            action_name=action_name,
            action_hash=action_hash,
            result_hash=result_hash,
            success=success,
            meta=meta or {},
        )

        with self._lock:
            trace_id = self._next_id
            self._next_id += 1
            self._traces[trace_id] = trace
            self._prune()
            return trace_id

    def check(
        self,
        current_strategy: Optional[str] = None,
    ) -> LoopDetection:
        """Inspect the current trace window for loop patterns.

        Parameters
        ----------
        current_strategy : str or None
            The name of the currently-active strategy (used to suggest
            alternatives when ``STRATEGY_SWITCH`` is recommended).

        Returns
        -------
        LoopDetection
        """
        with self._lock:
            # Make a snapshot so we don't hold the lock during analysis.
            traces = list(self._traces.values())

        # Check patterns in priority order (first match wins).
        detection = self._detect_consecutive_repeat(traces)
        if detection.pattern is not None:
            return detection

        detection = self._detect_loop_back(traces)
        if detection.pattern is not None:
            return detection

        detection = self._detect_ineffective_retry(traces)
        if detection.pattern is not None:
            return detection

        detection = self._detect_time_exceeded(traces)
        if detection.pattern is not None:
            return detection

        return self._no_loop(current_strategy)

    def record_and_check(
        self,
        action_type: ActionType,
        action_name: str,
        arguments: Any = None,
        result: Any = None,
        success: bool = True,
        meta: Optional[Dict[str, Any]] = None,
        current_strategy: Optional[str] = None,
    ) -> Tuple[int, LoopDetection]:
        """Atomically record a trace and run loop detection.

        This is the recommended convenience method for the common case.

        Returns
        -------
        tuple[int, LoopDetection]
            (trace_id, detection)
        """
        trace_id = self.record(
            action_type=action_type,
            action_name=action_name,
            arguments=arguments,
            result=result,
            success=success,
            meta=meta,
        )
        detection = self.check(current_strategy=current_strategy)
        return trace_id, detection

    def reset(self) -> None:
        """Clear all traces and reset the ID counter."""
        with self._lock:
            self._traces.clear()
            self._next_id = 0
        logger.info("AntiLoopGuard.reset")

    def get_traces(self) -> List[ActionTrace]:
        """Return a snapshot of all traces currently in the window."""
        with self._lock:
            return list(self._traces.values())

    # ------------------------------------------------------------------
    # Detection logic (private)
    # ------------------------------------------------------------------

    @staticmethod
    def _no_loop(strategy: Optional[str]) -> LoopDetection:
        """Build the 'all-clear' detection."""
        return LoopDetection(
            pattern=None,
            action=LoopAction.CONTINUE,
        )

    def _detect_consecutive_repeat(self, traces: List[ActionTrace]) -> LoopDetection:
        """CONSECUTIVE_REPEAT: same ``action_hash`` repeated N times in a row."""
        if len(traces) < self._consecutive_threshold:
            return self._no_loop(None)

        # Walk backwards looking for a run of identical hashes.
        target_hash = traces[-1].action_hash
        count = 0
        implicated: List[int] = []
        for i in range(len(traces) - 1, -1, -1):
            if traces[i].action_hash == target_hash:
                count += 1
                implicated.append(i)
            else:
                break

        if count >= self._consecutive_threshold:
            implicated.reverse()
            return LoopDetection(
                pattern=LoopPattern.CONSECUTIVE_REPEAT,
                action=LoopAction.STRATEGY_SWITCH,
                detail=(
                    f"Action '{traces[-1].action_name}' repeated {count} times "
                    f"consecutively (threshold={self._consecutive_threshold})"
                ),
                implicated_traces=implicated,
            )
        return self._no_loop(None)

    def _detect_loop_back(self, traces: List[ActionTrace]) -> LoopDetection:
        """LOOP_BACK: detect A→B→A→B oscillation cycles."""
        window = traces[-self._loop_back_depth :]
        if len(window) < 4:
            return self._no_loop(None)

        # Build a sequence of action hashes for the window.
        seq = [t.action_hash for t in window]

        # Look for any repeating pair pattern.  We scan for the smallest cycle.
        # Strategy: for each possible pair-length (2, 3, 4), check if the tail
        # of the sequence is composed of that pair repeated.
        for pair_len in range(2, 5):  # pairs of 2, 3, or 4 actions
            if len(seq) < pair_len * self._loop_back_min_cycle:
                continue

            pair = tuple(seq[-pair_len:])
            repeats = 0
            idx = len(seq) - pair_len
            while idx >= 0:
                if tuple(seq[idx : idx + pair_len]) == pair:
                    repeats += 1
                    idx -= pair_len
                else:
                    break

            if repeats >= self._loop_back_min_cycle:
                action_names = [window[-(pair_len - j)].action_name for j in range(pair_len)]
                cycle_desc = " → ".join(action_names)
                return LoopDetection(
                    pattern=LoopPattern.LOOP_BACK,
                    action=LoopAction.STRATEGY_SWITCH,
                    detail=(
                        f"Loop-back cycle detected: {cycle_desc} repeated "
                        f"{repeats} times in last {len(window)} actions"
                    ),
                    implicated_traces=list(
                        range(len(traces) - pair_len * repeats, len(traces))
                    ),
                )

        return self._no_loop(None)

    def _detect_ineffective_retry(self, traces: List[ActionTrace]) -> LoopDetection:
        """IN EFFECTIVE_RETRY: same error hash repeated N times."""
        # Only consider error-type traces.
        errors = [t for t in traces if t.action_type == ActionType.ERROR]
        if len(errors) < self._retry_threshold:
            return self._no_loop(None)

        # Look at the most recent errors.
        recent_errors = errors[-self._retry_threshold :]
        target_hash = recent_errors[-1].action_hash

        if all(e.action_hash == target_hash for e in recent_errors):
            # Verify they're consecutive error traces (no successful intervening
            # action of the same type).  The simplest check: the last N errors
            # share the same hash.
            return LoopDetection(
                pattern=LoopPattern.INEFFECTIVE_RETRY,
                action=LoopAction.ESCALATE,
                detail=(
                    f"Same error '{recent_errors[-1].action_name}' encountered "
                    f"{self._retry_threshold} times without resolution"
                ),
                implicated_traces=[
                    i
                    for i, t in enumerate(traces)
                    if t.action_type == ActionType.ERROR
                    and t.action_hash == target_hash
                ],
            )

        return self._no_loop(None)

    def _detect_time_exceeded(self, traces: List[ActionTrace]) -> LoopDetection:
        """TIME_EXCEEDED: a task has exceeded its time budget."""
        if self._time_budget is None or not traces:
            return self._no_loop(None)

        now = time.monotonic()

        # Look for the most recent trace that carries task_start.
        for trace in reversed(traces):
            task_start = trace.meta.get("task_start")
            if task_start is not None:
                elapsed = now - task_start
                if elapsed > self._time_budget:
                    return LoopDetection(
                        pattern=LoopPattern.TIME_EXCEEDED,
                        action=LoopAction.GOAL_RESET,
                        detail=(
                            f"Task elapsed {elapsed:.1f}s exceeds budget of "
                            f"{self._time_budget:.1f}s"
                        ),
                        implicated_traces=[traces.index(trace)],
                    )
                # Only check the most recent task_start — earlier ones may
                # already have completed.
                break

        return self._no_loop(None)

    # ------------------------------------------------------------------
    # Strategy suggestion
    # ------------------------------------------------------------------

    def suggest_alternative(self, current_strategy: Optional[str] = None) -> str:
        """Return a suggested alternative strategy name.

        This is a convenience wrapper so callers can query the strategy
        suggestion independently of a detection pass.
        """
        return _suggest_alternative(current_strategy)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        """Drop oldest traces when the window overflows."""
        while len(self._traces) > self._window_size:
            # OrderedDict.popitem(last=False) removes the first (oldest) item.
            oldest_id, _ = self._traces.popitem(last=False)
            logger.debug("AntiLoopGuard.pruned", trace_id=oldest_id)

    def __repr__(self) -> str:
        return (
            f"AntiLoopGuard(traces={len(self._traces)}/{self._window_size}, "
            f"next_id={self._next_id})"
        )


# ---------------------------------------------------------------------------
# Module-level convenience (singleton-style access)
# ---------------------------------------------------------------------------

_default_guard: Optional[AntiLoopGuard] = None
_default_lock = threading.Lock()


def get_default_guard(**kwargs: Any) -> AntiLoopGuard:
    """Return a process-wide singleton ``AntiLoopGuard``.

    On first call you may pass constructor keyword arguments to configure the
    singleton; subsequent calls ignore ``kwargs``.
    """
    global _default_guard
    if _default_guard is None:
        with _default_lock:
            if _default_guard is None:
                _default_guard = AntiLoopGuard(**kwargs)
    return _default_guard


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "ActionTrace",
    "ActionType",
    "AntiLoopGuard",
    "LoopAction",
    "LoopDetection",
    "LoopPattern",
    "_hash_action",
    "_hash_content",
    "_hash_result",
    "get_default_guard",
]
