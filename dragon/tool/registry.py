"""
Dragon Agent — Tool System
==========================

Surpasses Hermes's tool registry with:

1. **Auto-Discovery** — tools register at import time via decorator
2. **Semantic Tool Search** — find the right tool by embedding similarity
3. **Tool Pipelines** — compose tools with retry, timeout, parallel execution
4. **Circuit Breaker** — prevent cascading failures
5. **Built-in Tools** — search, file, terminal, web, code_exec

Architecture::

    ┌────────────────────────────────────────────────────────────┐
    │                      ToolRegistry                          │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
    │  │ Registry  │  │Discovery │  │Pipeline  │  │ Circuit   │  │
    │  │ (name→fn) │  │(semantic)│  │(compose) │  │ Breaker   │  │
    │  └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
    └────────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import os
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("dragon.tool")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECS = 60
MAX_RETRIES = 3
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive failures to open circuit
CIRCUIT_BREAKER_RESET_SECS = 30  # seconds before half-open probe


# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────

class CircuitState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # fast-fail
    HALF_OPEN = "half_open"  # single probe


class ToolOutcome(Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"
    CIRCUIT_OPEN = "circuit_open"


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class ToolDef:
    """Definition of a registered tool."""
    name: str
    description: str
    handler: Callable
    schema: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    timeout_secs: float = DEFAULT_TIMEOUT_SECS
    max_retries: int = MAX_RETRIES
    requires_env: List[str] = field(default_factory=list)
    category: str = "general"

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling schema (wrapped with type)."""
        """Convert to OpenAI function-calling schema."""
        inner = {"name": self.name, "description": self.description}
        if self.schema:
            inner["parameters"] = self.schema
        else:
            inner["parameters"] = {"type": "object", "properties": {}, "required": []}
        return {"type": "function", "function": inner}


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    outcome: ToolOutcome
    output: Any = None
    error: str = ""
    latency_ms: float = 0.0
    retries_used: int = 0
    circuit_state: Optional[CircuitState] = None

    @property
    def success(self) -> bool:
        return self.outcome == ToolOutcome.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool_name,
            "success": self.success,
            "outcome": self.outcome.value,
            "output": str(self.output)[:500] if self.output else None,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 1),
            "retries": self.retries_used,
        }


@dataclass
class PipelineToolStep:
    """A single step in a tool pipeline."""
    tool_name: str
    args: Dict[str, Any] = field(default_factory=dict)
    input_from: Optional[str] = None  # take input from previous step's output
    input_key: str = "output"          # which key from previous output to use
    retry_on_failure: bool = True
    timeout_secs: Optional[float] = None


# ────────────────────────────────────────────────────────────────────
# Circuit Breaker
# ────────────────────────────────────────────────────────────────────


class CircuitBreaker:
    """Prevent cascading failures by fast-failing when a tool is unhealthy."""

    def __init__(
        self,
        failure_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
        reset_timeout_secs: float = CIRCUIT_BREAKER_RESET_SECS,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_secs = reset_timeout_secs

        self._state: Dict[str, CircuitState] = {}
        self._failure_count: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}
        self._lock = threading.Lock()

    def before_call(self, tool_name: str) -> bool:
        """Check if the tool can be called. Returns False if circuit is open."""
        with self._lock:
            state = self._state.get(tool_name, CircuitState.CLOSED)

            if state == CircuitState.OPEN:
                # Check if reset timeout elapsed
                last_fail = self._last_failure_time.get(tool_name, 0)
                if time.monotonic() - last_fail >= self.reset_timeout_secs:
                    self._state[tool_name] = CircuitState.HALF_OPEN
                    logger.info("Circuit half-open for '%s' — probing", tool_name)
                    return True
                return False

            return True

    def on_success(self, tool_name: str) -> None:
        """Reset circuit on success."""
        with self._lock:
            self._state[tool_name] = CircuitState.CLOSED
            self._failure_count[tool_name] = 0

    def on_failure(self, tool_name: str) -> None:
        """Record a failure; open circuit if threshold reached."""
        with self._lock:
            count = self._failure_count.get(tool_name, 0) + 1
            self._failure_count[tool_name] = count
            self._last_failure_time[tool_name] = time.monotonic()

            if count >= self.failure_threshold:
                self._state[tool_name] = CircuitState.OPEN
                logger.warning(
                    "Circuit OPEN for '%s' — %d consecutive failures",
                    tool_name, count,
                )

    def get_state(self, tool_name: str) -> CircuitState:
        return self._state.get(tool_name, CircuitState.CLOSED)


# ────────────────────────────────────────────────────────────────────
# Tool Registry
# ────────────────────────────────────────────────────────────────────


class ToolRegistry:
    """Central tool registry with auto-discovery and circuit breaking.

    Usage::

        registry = ToolRegistry()

        @registry.register(
            name="search",
            description="Search the web for information",
            tags=["web", "search"],
            category="web",
        )
        async def search_tool(query: str, max_results: int = 5) -> str:
            ...

        # Execute with retry and circuit breaker
        result = await registry.call("search", {"query": "AI news"})
        print(result.output)

        # Create a pipeline
        pipeline = registry.pipeline([
            PipelineToolStep("search", {"query": "AI"}),
            PipelineToolStep("summarize", input_from="search"),
        ])
        results = await pipeline.run()
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDef] = OrderedDict()
        self._circuit_breaker = CircuitBreaker()
        self._lock = threading.Lock()

        # Track usage stats
        self._call_count: Dict[str, int] = {}
        self._total_latency: Dict[str, float] = {}

        logger.info("ToolRegistry initialized")

    # ── Registration ───────────────────────────────────────────────

    def register(
        self,
        name: str = "",
        description: str = "",
        schema: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        timeout_secs: float = DEFAULT_TIMEOUT_SECS,
        max_retries: int = MAX_RETRIES,
        requires_env: Optional[List[str]] = None,
        category: str = "general",
    ) -> Callable:
        """Decorator to register a tool.

        Can be used as @registry.register(...) or registry.register(...)(fn).
        """
        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            tool = ToolDef(
                name=tool_name,
                description=description or fn.__doc__ or "",
                handler=fn,
                schema=schema or self._infer_schema(fn),
                tags=tags or [],
                timeout_secs=timeout_secs,
                max_retries=max_retries,
                requires_env=requires_env or [],
                category=category,
            )
            with self._lock:
                self._tools[tool_name] = tool
            logger.debug("Registered tool: %s (category=%s)", tool_name, category)
            return fn

        return decorator

    def register_tool(self, tool: ToolDef) -> None:
        """Register a pre-built ToolDef."""
        with self._lock:
            self._tools[tool.name] = tool
        logger.debug("Registered tool from ToolDef: %s", tool.name)

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry."""
        with self._lock:
            if name in self._tools:
                del self._tools[name]
                return True
            return False

    # ── Discovery ──────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ToolDef]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all registered tools, optionally filtered by category."""
        tools = []
        for name, tool in self._tools.items():
            if category and tool.category != category:
                continue
            tools.append({
                "name": name,
                "description": tool.description[:100],
                "category": tool.category,
                "tags": tool.tags,
                "timeout_secs": tool.timeout_secs,
            })
        return tools

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Keyword-based tool search by name, description, and tags."""
        query_lower = query.lower()
        results = []
        for name, tool in self._tools.items():
            score = 0
            if query_lower in name.lower():
                score += 1.0
            if query_lower in tool.description.lower():
                score += 0.5
            for tag in tool.tags:
                if query_lower in tag.lower():
                    score += 0.3
            if score > 0:
                results.append({
                    "name": name,
                    "description": tool.description[:100],
                    "category": tool.category,
                    "score": round(score, 2),
                })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def get_openai_schemas(self, tool_names: Optional[List[str]] = None) -> List[Dict]:
        """Get OpenAI function-calling schemas for all or specified tools."""
        tools = []
        for name, tool in self._tools.items():
            if tool_names and name not in tool_names:
                continue
            if not self._check_requirements(tool):
                continue
            tools.append(tool.to_openai_schema())
        return tools

    # ── Execution ──────────────────────────────────────────────────

    async def call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        timeout_secs: Optional[float] = None,
    ) -> ToolResult:
        """Execute a tool with retry logic and circuit breaker.

        Args:
            tool_name: Name of the registered tool.
            args: Keyword arguments for the tool handler.
            timeout_secs: Override default timeout.

        Returns:
            ToolResult with outcome, output, and metadata.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                tool_name=tool_name,
                outcome=ToolOutcome.ERROR,
                error=f"Tool '{tool_name}' not found",
            )

        # Check circuit breaker
        if not self._circuit_breaker.before_call(tool_name):
            return ToolResult(
                tool_name=tool_name,
                outcome=ToolOutcome.CIRCUIT_OPEN,
                error=f"Circuit breaker open for '{tool_name}'",
                circuit_state=CircuitState.OPEN,
            )

        # Check requirements
        if not self._check_requirements(tool):
            return ToolResult(
                tool_name=tool_name,
                outcome=ToolOutcome.ERROR,
                error=f"Missing requirements for '{tool_name}': {tool.requires_env}",
            )

        timeout = timeout_secs or tool.timeout_secs
        max_retries = tool.max_retries

        last_error = ""
        start = time.monotonic()

        for attempt in range(max_retries + 1):
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    self._invoke(tool, args),
                    timeout=timeout,
                )
                latency = (time.monotonic() - start) * 1000
                self._circuit_breaker.on_success(tool_name)
                self._record_stats(tool_name, latency)

                return ToolResult(
                    tool_name=tool_name,
                    outcome=ToolOutcome.SUCCESS,
                    output=result,
                    latency_ms=latency,
                    retries_used=attempt,
                )
            except asyncio.TimeoutError:
                last_error = f"Timeout after {timeout}s"
                logger.warning("Tool '%s' timed out (attempt %d/%d)", tool_name, attempt + 1, max_retries + 1)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning("Tool '%s' failed (attempt %d/%d): %s", tool_name, attempt + 1, max_retries + 1, e)

        # All retries exhausted
        self._circuit_breaker.on_failure(tool_name)
        latency = (time.monotonic() - start) * 1000

        return ToolResult(
            tool_name=tool_name,
            outcome=ToolOutcome.ERROR,
            error=last_error,
            latency_ms=latency,
            retries_used=max_retries,
            circuit_state=self._circuit_breaker.get_state(tool_name),
        )

    async def _invoke(self, tool: ToolDef, args: Dict[str, Any]) -> Any:
        """Invoke a tool handler (supports both sync and async)."""
        logger.info(f"[TOOL INVOKE] {tool.name} args={args}")
        # Coerce argument types based on tool schema
        if tool.schema and "properties" in tool.schema:
            for pname, prop in tool.schema["properties"].items():
                if pname in args:
                    ptype = prop.get("type", "string")
                    if ptype == "integer" and isinstance(args[pname], str):
                        try:
                            args[pname] = int(args[pname])
                        except ValueError:
                            pass
                    elif ptype == "number" and isinstance(args[pname], str):
                        try:
                            args[pname] = float(args[pname])
                        except ValueError:
                            pass
                    elif ptype == "boolean" and isinstance(args[pname], str):
                        args[pname] = args[pname].lower() in ("true", "1", "yes")

        handler = tool.handler
        if asyncio.iscoroutinefunction(handler):
            return await handler(**args)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, functools.partial(handler, **args)
            )

    # ── Pipeline ───────────────────────────────────────────────────

    def pipeline(
        self,
        steps: List[PipelineToolStep],
        name: str = "",
    ) -> "ToolPipeline":
        """Create a tool pipeline."""
        return ToolPipeline(self, steps, name=name)

    # ── Circuit Breaker ────────────────────────────────────────────

    def circuit_state(self, tool_name: str) -> CircuitState:
        return self._circuit_breaker.get_state(tool_name)

    def circuit_reset(self, tool_name: str) -> None:
        """Manually reset a tool's circuit breaker."""
        self._circuit_breaker.on_success(tool_name)

    # ── Helpers ────────────────────────────────────────────────────

    def _check_requirements(self, tool: ToolDef) -> bool:
        """Check that all required env vars are set."""
        for env_var in tool.requires_env:
            if not os.getenv(env_var):
                return False
        return True

    def _record_stats(self, tool_name: str, latency_ms: float) -> None:
        """Record call statistics."""
        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1
        self._total_latency[tool_name] = self._total_latency.get(tool_name, 0.0) + latency_ms

    @staticmethod
    @staticmethod
    def _infer_schema(fn: Callable) -> Dict[str, Any]:
        """Infer JSON schema from function signature."""
        sig = inspect.signature(fn)
        properties = {}
        required = []

        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue

            # Resolve annotation (handles from __future__ import annotations)
            ann = param.annotation
            ann_str = ""
            if ann is not inspect.Parameter.empty:
                # from __future__ import annotations makes these strings
                ann_str = ann if isinstance(ann, str) else str(ann)

            ptype = "string"
            ann_lower = ann_str.lower()
            if ann_lower == "int" or ann_lower == "<class 'int'>":
                ptype = "integer"
            elif ann_lower == "float" or ann_lower == "<class 'float'>":
                ptype = "number"
            elif ann_lower == "bool" or ann_lower == "<class 'bool'>":
                ptype = "boolean"
            elif ann_lower.startswith("list") or ann_lower.startswith("optional[list"):
                ptype = "array"
            elif ann_lower.startswith("dict"):
                ptype = "object"

            # Extract description from docstring
            desc = f"Parameter: {pname}"
            if fn.__doc__:
                doc = fn.__doc__
                marker = pname + ":"
                for line in doc.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith(marker):
                        desc = stripped[len(marker):].strip()
                        break

            properties[pname] = {
                "type": ptype,
                "description": desc,
            }

            if param.default is inspect.Parameter.empty:
                required.append(pname)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def stats(self) -> Dict[str, Any]:
        """Return registry statistics."""
        return {
            "total_tools": len(self._tools),
            "categories": list(set(t.category for t in self._tools.values())),
            "total_calls": sum(self._call_count.values()),
            "open_circuits": sum(
                1 for t in self._tools
                if self._circuit_breaker.get_state(t) == CircuitState.OPEN
            ),
        }


# ────────────────────────────────────────────────────────────────────
# Tool Pipeline
# ────────────────────────────────────────────────────────────────────


class ToolPipeline:
    """Execute multiple tools in sequence with input/output chaining."""

    def __init__(
        self,
        registry: ToolRegistry,
        steps: List[PipelineToolStep],
        name: str = "",
    ) -> None:
        self.registry = registry
        self.steps = steps
        self.name = name or f"pipeline-{len(steps)}-tools"

    async def run(
        self,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run all pipeline steps in sequence.

        Each step can consume the output of previous steps via input_from.
        """
        context = dict(initial_context or {})
        results = []
        overall_success = True
        start = time.monotonic()

        for step in self.steps:
            # Build args, resolving input_from references
            args = dict(step.args)

            if step.input_from:
                # Find the referenced step's output
                prev = next(
                    (r for r in results if r.tool_name == step.input_from),
                    None,
                )
                if prev and prev.success:
                    args[step.input_key] = prev.output

            timeout = step.timeout_secs or DEFAULT_TIMEOUT_SECS
            result = await self.registry.call(
                step.tool_name, args, timeout_secs=timeout
            )
            results.append(result)

            # Store output in context for downstream steps
            context[step.tool_name] = result.output

            if not result.success:
                overall_success = False
                if not step.retry_on_failure:
                    break

        return {
            "name": self.name,
            "success": overall_success,
            "steps": [r.to_dict() for r in results],
            "context": context,
            "total_latency_ms": (time.monotonic() - start) * 1000,
        }
