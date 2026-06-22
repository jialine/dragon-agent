"""
Dragon Agent — Tool Guardrails
==============================

Pre- and post-execution safety layer for the tool system. Provides:

1. **Pre-execution validation** — tool existence, permissions, arg schema validation
2. **Post-execution filtering** — strip sensitive data from tool output
3. **Result size limits** — prevent context flooding from oversized outputs
4. **Dangerous tool confirmation** — flag commands like rm, sudo, chmod
5. **Integration** — wraps dragon.tool.registry.ToolRegistry execution

Inspired by Hermes Agent's ``agent/tool_guardrails.py`` but adapted for
Dragon's data-class-based ToolDef / ToolResult architecture.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from dragon.tool.registry import ToolDef, ToolResult, ToolOutcome, CircuitState

logger = logging.getLogger("dragon.tool.guardrails")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

# Tools that only read data — safe to retry without side effects
IDEMPOTENT_TOOL_NAMES: FrozenSet[str] = frozenset({
    "search", "file_read", "http_get", "search_files",
    "read_file", "list_directory", "get_file_info",
    "internal_search", "fetch_url", "web_search",
})

# Tools that mutate state — should be carefully managed
MUTATING_TOOL_NAMES: FrozenSet[str] = frozenset({
    "file_write", "execute", "terminal", "patch",
    "delete", "move", "rename", "chmod", "chown",
    "send_message", "http_post", "http_put", "http_delete",
})

# Commands that warrant extra caution
DANGEROUS_COMMAND_PATTERNS: List[str] = [
    r"\brm\b",           # remove
    r"\bsudo\b",         # super-user
    r"\bchmod\b",        # change permissions
    r"\bchown\b",        # change ownership
    r"\bdd\b",           # disk destroyer
    r"\bmkfs\b",         # make filesystem
    r"\bfdisk\b",        # partition manipulation
    r"\breboot\b",       # system reboot
    r"\bshutdown\b",     # system shutdown
    r"\bkill\b",         # process kill
    r"\bpkill\b",        # process kill by name
    r"\b:(){ :|:& };:\b", # fork bomb
    r">\s*/dev/\w+",     # writing to device files
    r"\bmount\b",        # mount operations
    r"\bumount\b",       # unmount
    r"\bfind\b.*\b-exec\b",  # dangerous find -exec
    r"\bxargs\b.*\brm\b",    # dangerous xargs rm
]

# Sensitive output patterns to strip from tool results
SENSITIVE_OUTPUT_PATTERNS = [
    r'(?:api[_-]?key|apikey|api_secret|secret[_-]?key)\s*[:=]\s*[\'"]?[A-Za-z0-9_\-\.]{8,}[\'"]?',
    r'(?:token|access_token|auth_token|refresh_token)\s*[:=]\s*[\'"]?[A-Za-z0-9_\-\.]{8,}[\'"]?',
    r'(?:password|passwd|pwd)\s*[:=]\s*[\'"]?\S+[\'"]?',
    r'(?:credential|private[_-]?key)\s*[:=]\s*[\'"]?[A-Za-z0-9_\-\.]{8,}[\'"]?',
    r'sk-[A-Za-z0-9_-]{10,}',
    r'ghp_[A-Za-z0-9]{10,}',
    r'xox[baprs]-[A-Za-z0-9-]{10,}',
    r'AIza[A-Za-z0-9_-]{30,}',
    r'AKIA[A-Z0-9]{16}',
    r'-----BEGIN.*?PRIVATE KEY-----[\s\S]*?-----END.*?PRIVATE KEY-----',
    r'Authorization:\s*Bearer\s+\S+',
    r'eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}',
]

# Compiled regex (lazy, compiled on first use)
_SENSITIVE_RE: Optional[re.Pattern] = None

# Default max result size (characters) before truncation
DEFAULT_MAX_RESULT_SIZE = 50_000


# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────

class GuardrailAction(Enum):
    """Decision returned by guardrail checks."""
    ALLOW = "allow"       # proceed normally
    WARN = "warn"         # allow but emit warning
    BLOCK = "block"       # prevent tool execution
    HALT = "halt"         # stop the current task entirely


class CheckType(Enum):
    """Category of guardrail check."""
    PRE_EXECUTION = "pre"
    POST_EXECUTION = "post"
    RESULT_SIZE = "size"
    DANGEROUS = "dangerous"
    SENSITIVE = "sensitive"


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class GuardrailCheck:
    """Result of a single guardrail check."""
    check_type: CheckType
    action: GuardrailAction
    tool_name: str = ""
    message: str = ""
    check_name: str = ""

    @property
    def blocked(self) -> bool:
        return self.action in {GuardrailAction.BLOCK, GuardrailAction.HALT}

    @property
    def warned(self) -> bool:
        return self.action == GuardrailAction.WARN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check_name or self.check_type.value,
            "action": self.action.value,
            "tool": self.tool_name,
            "message": self.message,
        }


@dataclass
class GuardrailConfig:
    """Configuration for tool guardrails.

    Can be instantiated directly or built from a YAML mapping
    (e.g. ``DragonConfig.guard`` section).
    """

    # Pre-execution checks
    require_tool_exists: bool = True
    validate_args_schema: bool = True
    confirm_dangerous: bool = True

    # Post-execution checks
    strip_sensitive_outputs: bool = True
    max_result_size: int = DEFAULT_MAX_RESULT_SIZE

    # Failure loop detection
    max_consecutive_failures: int = 3
    max_same_tool_failures: int = 5

    # Hard stops (opt-in — when True, block / halt instead of just warn)
    hard_stop_enabled: bool = False
    exact_failure_block_after: int = 5
    idempotent_no_progress_block_after: int = 5

    # Tool categorisation
    idempotent_tools: FrozenSet[str] = IDEMPOTENT_TOOL_NAMES
    mutating_tools: FrozenSet[str] = MUTATING_TOOL_NAMES
    dangerous_patterns: List[str] = field(
        default_factory=lambda: list(DANGEROUS_COMMAND_PATTERNS)
    )
    sensitive_patterns: List[str] = field(
        default_factory=lambda: list(SENSITIVE_OUTPUT_PATTERNS)
    )

    @classmethod
    def from_mapping(cls, data: Dict[str, Any] | None) -> "GuardrailConfig":
        """Build config from a dictionary (e.g. YAML ``tool_guardrails`` section)."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            require_tool_exists=data.get("require_tool_exists", True),
            validate_args_schema=data.get("validate_args_schema", True),
            confirm_dangerous=data.get("confirm_dangerous", True),
            strip_sensitive_outputs=data.get("strip_sensitive_outputs", True),
            max_result_size=data.get("max_result_size", DEFAULT_MAX_RESULT_SIZE),
            max_consecutive_failures=data.get("max_consecutive_failures", 3),
            max_same_tool_failures=data.get("max_same_tool_failures", 5),
            hard_stop_enabled=data.get("hard_stop_enabled", False),
            exact_failure_block_after=data.get("exact_failure_block_after", 5),
            idempotent_no_progress_block_after=data.get("idempotent_no_progress_block_after", 5),
        )


# ────────────────────────────────────────────────────────────────────
# Tool Call Signatures (for failure-loop detection)
# ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCallSignature:
    """Stable identity for a tool name + canonical args."""
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Dict[str, Any] | None) -> "ToolCallSignature":
        return cls(
            tool_name=tool_name,
            args_hash=cls._canonical_hash(args or {}),
        )

    @staticmethod
    def _canonical_hash(args: Dict[str, Any]) -> str:
        """SHA-256 of canonical JSON for the args dict."""
        import hashlib
        canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


# ────────────────────────────────────────────────────────────────────
# Core Guardrail Engine
# ────────────────────────────────────────────────────────────────────

class ToolGuardrails:
    """Pre- and post-execution safety checks for the tool system.

    Wraps ``ToolRegistry.call()`` with validation, sensitive-output
    filtering, result-size capping, and failure-loop detection.

    Usage::

        guardrails = ToolGuardrails()
        result = await guardrails.checked_call(registry, tool_name, args)
        if result.outcome == ToolOutcome.ERROR:
            print(result.error)

    For direct pre/post checks without the full call wrapper::

        pre_checks = guardrails.pre_check(tool_def, args)
        for check in pre_checks:
            if check.blocked:
                return
        # ... execute tool ...
        post_result = guardrails.post_filter(raw_output)
    """

    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self.config = config or GuardrailConfig()
        self._failure_counts: Dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: Dict[str, int] = {}
        self._no_progress: Dict[ToolCallSignature, Tuple[str, int]] = {}
        self._dangerous_confirmed: Set[str] = set()
        logger.info("ToolGuardrails initialized (hard_stop=%s)", self.config.hard_stop_enabled)

    # ── Public API ──────────────────────────────────────────────────

    def reset_for_turn(self) -> None:
        """Reset per-turn failure tracking. Call at start of each user message."""
        self._failure_counts.clear()
        self._same_tool_failure_counts.clear()
        self._no_progress.clear()

    def pre_check(
        self,
        tool_def: ToolDef | None,
        args: Dict[str, Any],
        *,
        tool_name: str = "",
    ) -> List[GuardrailCheck]:
        """Run all pre-execution checks. Returns list of GuardrailCheck results.

        Args:
            tool_def: The ToolDef from registry, or None if tool not found.
            args: Arguments for the tool call.
            tool_name: Tool name (used if tool_def is None).
        """
        name = tool_name or (tool_def.name if tool_def else "unknown")
        checks: List[GuardrailCheck] = []

        # Check 1: Tool exists
        if self.config.require_tool_exists and tool_def is None:
            checks.append(GuardrailCheck(
                check_type=CheckType.PRE_EXECUTION,
                action=GuardrailAction.BLOCK,
                tool_name=name,
                check_name="tool_exists",
                message=f"Tool '{name}' is not registered",
            ))
            return checks  # short-circuit — no further checks possible

        assert tool_def is not None  # for type checker

        # Check 2: Dangerous command detection
        if self.config.confirm_dangerous and tool_def.name == "execute":
            danger_check = self._check_dangerous(tool_def, args)
            if danger_check:
                checks.append(danger_check)

        # Check 3: Arg schema validation (basic)
        if self.config.validate_args_schema:
            schema_check = self._validate_args(tool_def, args)
            if schema_check:
                checks.append(schema_check)

        return checks

    def post_filter(
        self,
        result: ToolResult,
        tool_def: ToolDef | None = None,
    ) -> ToolResult:
        """Apply post-execution filtering to a ToolResult.

        - Truncates oversized output.
        - Strips sensitive data patterns from output strings.

        Returns the (possibly modified) ToolResult.
        """
        if result.output is None:
            return result

        output = result.output

        # Convert to string for filtering
        if isinstance(output, str):
            text = output
        else:
            try:
                text = json.dumps(output, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                text = str(output)

        # Filter sensitive data
        if self.config.strip_sensitive_outputs:
            text = self._strip_sensitive(text)

        # Enforce max result size
        if self.config.max_result_size and len(text) > self.config.max_result_size:
            text = text[:self.config.max_result_size]
            if isinstance(output, str):
                text += f"\n\n[output truncated to {self.config.max_result_size} chars]"
            logger.debug("Tool '%s' output truncated from %d to %d chars",
                         result.tool_name, len(text), self.config.max_result_size)

        # Preserve original type if possible
        if isinstance(result.output, str):
            result.output = text

        return result

    def post_filter_text(self, text: str) -> str:
        """Apply post-execution filtering to raw text (without a ToolResult)."""
        if not isinstance(text, str):
            return text
        if self.config.strip_sensitive_outputs:
            text = self._strip_sensitive(text)
        if self.config.max_result_size and len(text) > self.config.max_result_size:
            text = text[:self.config.max_result_size]
            text += f"\n\n[output truncated to {self.config.max_result_size} chars]"
        return text

    async def checked_call(
        self,
        registry: Any,  # ToolRegistry (avoid circular import)
        tool_name: str,
        args: Dict[str, Any],
        timeout_secs: Optional[float] = None,
    ) -> ToolResult:
        """Execute a tool through the registry with full guardrail protection.

        Args:
            registry: ToolRegistry instance.
            tool_name: Tool name to execute.
            args: Tool arguments.
            timeout_secs: Optional timeout override.

        Returns:
            ToolResult with guardrail checks applied.
        """
        tool_def = registry.get(tool_name)

        # Pre-execution checks
        pre_checks = self.pre_check(tool_def, args, tool_name=tool_name)
        blocks = [c for c in pre_checks if c.blocked]
        warnings = [c for c in pre_checks if c.warned]

        for warn in warnings:
            logger.warning("Guardrail WARN: %s — %s", warn.check_name, warn.message)

        if blocks:
            block = blocks[0]
            logger.error("Guardrail BLOCK: %s — %s", block.check_name, block.message)
            return ToolResult(
                tool_name=tool_name,
                outcome=ToolOutcome.ERROR,
                error=block.message,
            )

        # Execute
        result = await registry.call(tool_name, args, timeout_secs=timeout_secs)

        # Post-execution filter
        result = self.post_filter(result, tool_def)

        # Track failures for loop detection
        if not result.success:
            sig = ToolCallSignature.from_call(tool_name, args)
            self._failure_counts[sig] = self._failure_counts.get(sig, 0) + 1
            self._same_tool_failure_counts[tool_name] = (
                self._same_tool_failure_counts.get(tool_name, 0) + 1
            )
        else:
            sig = ToolCallSignature.from_call(tool_name, args)
            self._failure_counts.pop(sig, None)
            self._same_tool_failure_counts.pop(tool_name, None)

        return result

    # ── Internal Checks ─────────────────────────────────────────────

    def _check_dangerous(
        self, tool_def: ToolDef, args: Dict[str, Any]
    ) -> Optional[GuardrailCheck]:
        """Check if a shell command contains dangerous patterns.

        For ``execute`` tools, scan the command arg for dangerous commands
        like rm, sudo, chmod, etc.
        """
        command = args.get("command", "")
        if not command or not isinstance(command, str):
            return None

        for pattern in self.config.dangerous_patterns:
            if re.search(pattern, command):
                return GuardrailCheck(
                    check_type=CheckType.DANGEROUS,
                    action=GuardrailAction.WARN,
                    tool_name=tool_def.name,
                    check_name="dangerous_command",
                    message=f"Dangerous command pattern detected in '{tool_def.name}': {command[:80]}",
                )
        return None

    def _validate_args(
        self, tool_def: ToolDef, args: Dict[str, Any]
    ) -> Optional[GuardrailCheck]:
        """Basic schema validation against the tool's inferred schema."""
        schema = tool_def.schema
        if not schema or "properties" not in schema:
            return None

        required = schema.get("required", [])
        for key in required:
            if key not in args:
                return GuardrailCheck(
                    check_type=CheckType.PRE_EXECUTION,
                    action=GuardrailAction.WARN,
                    tool_name=tool_def.name,
                    check_name="missing_required_arg",
                    message=f"Missing required argument '{key}' for tool '{tool_def.name}'",
                )
        return None

    def _strip_sensitive(self, text: str) -> str:
        """Strip sensitive data patterns from text."""
        global _SENSITIVE_RE

        if _SENSITIVE_RE is None:
            _SENSITIVE_RE = re.compile(
                "|".join(f"({p})" for p in self.config.sensitive_patterns),
                re.IGNORECASE,
            )

        return _SENSITIVE_RE.sub("[REDACTED]", text)

    # ── Failure Loop Detection ─────────────────────────────────────

    def check_failure_loop(
        self, tool_name: str, args: Dict[str, Any],
    ) -> Optional[GuardrailCheck]:
        """Check if the same failing tool call is being retried too many times.

        Returns a GuardrailCheck with BLOCK/HALT if a loop is detected,
        or None if execution should proceed.
        """
        sig = ToolCallSignature.from_call(tool_name, args)
        exact_count = self._failure_counts.get(sig, 0)
        same_tool_count = self._same_tool_failure_counts.get(tool_name, 0)

        # Exact failure loop
        if exact_count >= self.config.exact_failure_block_after:
            action = GuardrailAction.BLOCK if self.config.hard_stop_enabled else GuardrailAction.WARN
            return GuardrailCheck(
                check_type=CheckType.PRE_EXECUTION,
                action=action,
                tool_name=tool_name,
                check_name="exact_failure_loop",
                message=(
                    f"Tool '{tool_name}' has failed {exact_count} times "
                    f"with identical arguments — possible loop detected"
                ),
            )

        # Same-tool failure threshold
        if same_tool_count >= self.config.max_same_tool_failures:
            action = GuardrailAction.BLOCK if self.config.hard_stop_enabled else GuardrailAction.WARN
            return GuardrailCheck(
                check_type=CheckType.PRE_EXECUTION,
                action=action,
                tool_name=tool_name,
                check_name="same_tool_failure_loop",
                message=(
                    f"Tool '{tool_name}' has failed {same_tool_count} times "
                    f"in sequence — possible loop detected"
                ),
            )

        return None


# ────────────────────────────────────────────────────────────────────
# Standalone utility: classify a tool result as failure
# ────────────────────────────────────────────────────────────────────

def classify_tool_failure(tool_name: str, output: str | None) -> tuple:
    """Heuristic classification of whether a tool result indicates failure.

    Returns ``(is_failure: bool, suffix: str)`` suitable for display.
    Ported from Hermes's ``classify_tool_failure``.

    Args:
        tool_name: Name of the tool.
        output: Raw output string from tool execution.

    Returns:
        ``(True, " [exit 1]")`` or ``(False, "")`` etc.
    """
    if output is None:
        return False, ""

    # Terminal / execute: check exit code
    if tool_name in ("execute", "terminal"):
        try:
            data = json.loads(output)
            if isinstance(data, dict):
                exit_code = data.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    return True, f" [exit {exit_code}]"
        except (json.JSONDecodeError, TypeError):
            pass
        return False, ""

    # Generic error detection
    lower = output[:500].lower()
    if '"error"' in lower or '"failed"' in lower or output.startswith("Error"):
        return True, " [error]"

    return False, ""
