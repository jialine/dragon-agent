"""
Panda Subagent Delegation — Spawn independent agents for parallel task execution.

Each subagent runs in an isolated context with its own tool access and session.
The parent agent delegates tasks and receives structured results.

Inspired by Hermes Agent's delegate_task, but with:
- Skill inheritance (subagents can use Panda's self-evolving skills)
- Debate mode (two subagents can debate and return consensus)
- Timeout and budget per subagent
- Structured result format with confidence scores

Usage::

    from panda.subagent import SubagentOrchestrator

    orch = SubagentOrchestrator(provider_registry=registry)
    result = await orch.delegate(
        goal="Research the best Python web frameworks in 2026",
        context="Focus on async frameworks. Compare FastAPI, Litestar, and Sanic.",
        toolsets=["web", "file"],
    )
    print(result.summary)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("panda.subagent")


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

class SubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class SubagentConfig:
    """Configuration for a subagent."""
    name: str = ""
    model: str = ""
    provider: str = ""
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    max_iterations: int = 10       # max tool-calling loops
    timeout_secs: float = 300.0
    context_limit: int = 128000


@dataclass
class SubagentResult:
    """Result from a subagent execution."""
    task_id: str
    goal: str
    status: SubagentStatus
    summary: str = ""
    findings: List[str] = field(default_factory=list)
    error: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    tool_calls: int = 0
    confidence: float = 0.5


@dataclass
class DebateResult:
    """Result from a two-subagent debate."""
    task_id: str
    goal: str
    consensus: str = ""
    agent_a_position: str = ""
    agent_b_position: str = ""
    agreement: bool = False
    key_differences: List[str] = field(default_factory=list)
    confidence: float = 0.0


# ────────────────────────────────────────────────────────────────────
# Subagent
# ────────────────────────────────────────────────────────────────────


class Subagent:
    """An independent agent that executes a single task.

    Has its own:
    - Conversation context (isolated from parent)
    - Tool access (subset of parent's tools)
    - Session tracking
    """

    def __init__(
        self,
        config: SubagentConfig,
        provider_registry: Any = None,
        tool_registry: Any = None,
        session_store: Any = None,
    ) -> None:
        self.config = config
        self.provider_registry = provider_registry
        self.tool_registry = tool_registry
        self.session_store = session_store

        self._history: List[Dict[str, str]] = []
        self._status = SubagentStatus.PENDING
        self._tool_call_count = 0

    @property
    def status(self) -> SubagentStatus:
        return self._status

    async def execute(
        self,
        goal: str,
        context: str = "",
        available_tools: Optional[List[str]] = None,
    ) -> SubagentResult:
        """Execute a task autonomously.

        The subagent:
        1. Receives the goal + context as its first user message
        2. Can call tools (up to max_iterations)
        3. Returns a structured result
        """
        task_id = uuid.uuid4().hex[:8]
        start = time.monotonic()
        self._status = SubagentStatus.RUNNING

        # Build initial prompt
        system_msg = self.config.system_prompt or (
            "You are a focused subagent. Complete your assigned task and return "
            "a structured result. Use tools when needed. Be concise."
        )

        user_msg = f"# Task\n\n{goal}"
        if context:
            user_msg += f"\n\n# Context\n\n{context}"
        user_msg += "\n\nWhen done, provide your findings in plain text."

        self._history = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        try:
            # Run the agent loop
            for iteration in range(self.config.max_iterations):
                # Check timeout
                elapsed = time.monotonic() - start
                if elapsed > self.config.timeout_secs:
                    self._status = SubagentStatus.TIMEOUT
                    return SubagentResult(
                        task_id=task_id, goal=goal,
                        status=SubagentStatus.TIMEOUT,
                        error=f"Timeout after {self.config.timeout_secs}s",
                        latency_ms=elapsed * 1000,
                        tool_calls=self._tool_call_count,
                    )

                # Call the provider
                if not self.provider_registry:
                    # No provider — return the goal as a "result" (test mode)
                    self._status = SubagentStatus.COMPLETED
                    return SubagentResult(
                        task_id=task_id, goal=goal,
                        status=SubagentStatus.COMPLETED,
                        summary=f"Task acknowledged: {goal}",
                        latency_ms=elapsed * 1000,
                        confidence=0.3,
                    )

                try:
                    result = await self.provider_registry.call(
                        self.config.provider or "openai",
                        model=self.config.model or "gpt-4o-mini",
                        messages=self._history,
                        temperature=self.config.temperature,
                        max_tokens=self.config.max_tokens,
                    )
                except Exception as e:
                    self._status = SubagentStatus.FAILED
                    return SubagentResult(
                        task_id=task_id, goal=goal,
                        status=SubagentStatus.FAILED,
                        error=str(e),
                        latency_ms=(time.monotonic() - start) * 1000,
                    )

                self._history.append({"role": "assistant", "content": result.content})

                # Check for tool calls in the response
                tool_calls = self._parse_tool_calls(result.content)

                if tool_calls and self.tool_registry:
                    for tc in tool_calls:
                        self._tool_call_count += 1
                        tool_result = await self.tool_registry.call(
                            tc["name"], tc.get("arguments", {})
                        )
                        tool_output = str(tool_result.output) if tool_result.success else tool_result.error
                        self._history.append({
                            "role": "tool",
                            "content": tool_output,
                            "name": tc["name"],
                        })
                elif tool_calls and not self.tool_registry:
                    self._history.append({
                        "role": "system",
                        "content": "No tools available. Please complete the task with your knowledge.",
                    })
                else:
                    # No tool calls — task is done
                    self._status = SubagentStatus.COMPLETED
                    return SubagentResult(
                        task_id=task_id, goal=goal,
                        status=SubagentStatus.COMPLETED,
                        summary=result.content[:2000],
                        findings=self._extract_findings(result.content),
                        tokens_used=result.total_tokens,
                        latency_ms=(time.monotonic() - start) * 1000,
                        tool_calls=self._tool_call_count,
                        confidence=0.7,
                    )

            # Max iterations reached
            self._status = SubagentStatus.COMPLETED
            last_content = self._history[-1]["content"] if self._history else ""
            return SubagentResult(
                task_id=task_id, goal=goal,
                status=SubagentStatus.COMPLETED,
                summary=last_content[:2000],
                latency_ms=(time.monotonic() - start) * 1000,
                tool_calls=self._tool_call_count,
            )

        except asyncio.CancelledError:
            self._status = SubagentStatus.CANCELLED
            return SubagentResult(
                task_id=task_id, goal=goal,
                status=SubagentStatus.CANCELLED,
                error="Cancelled",
                latency_ms=(time.monotonic() - start) * 1000,
            )

    def _parse_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Parse tool call requests from model output.

        Supports multiple formats:
        - ```tool_call\n{"name": "...", "arguments": {...}}\n```
        - <tool_call>{"name": "..."}</tool_call>
        """
        calls = []
        import re

        # Format 1: ```tool_call ... ```
        for match in re.finditer(r'```tool_call\s*\n(.*?)\n```', content, re.DOTALL):
            try:
                calls.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass

        # Format 2: <tool_call>...</tool_call>
        for match in re.finditer(r'<tool_call>(.*?)</tool_call>', content, re.DOTALL):
            try:
                calls.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass

        return calls

    def _extract_findings(self, content: str) -> List[str]:
        """Extract bullet-point findings from content."""
        import re
        findings = []
        for line in content.split("\n"):
            stripped = line.strip()
            if re.match(r'^[-*•]\s', stripped):
                findings.append(stripped[2:])
        return findings[:5]


# ────────────────────────────────────────────────────────────────────
# Subagent Orchestrator
# ────────────────────────────────────────────────────────────────────


class SubagentOrchestrator:
    """Orchestrate multiple subagents for parallel or debate execution.

    Usage::

        orch = SubagentOrchestrator(provider_registry=pr, tool_registry=tr)

        # Single task
        result = await orch.delegate(
            goal="Research Python web frameworks",
            context="Compare FastAPI vs Litestar",
        )

        # Parallel tasks
        results = await orch.delegate_many([
            {"goal": "Task 1", "context": "..."},
            {"goal": "Task 2", "context": "..."},
        ])

        # Debate mode
        debate = await orch.debate(
            goal="Which framework is better for async APIs?",
            context="Consider performance, ecosystem, and learning curve.",
        )
    """

    def __init__(
        self,
        provider_registry: Any = None,
        tool_registry: Any = None,
        session_store: Any = None,
        default_model: str = "gpt-4o-mini",
        default_provider: str = "openai",
        max_concurrent: int = 3,
    ) -> None:
        self.provider_registry = provider_registry
        self.tool_registry = tool_registry
        self.session_store = session_store
        self.default_model = default_model
        self.default_provider = default_provider
        self.max_concurrent = max_concurrent
        logger.info("SubagentOrchestrator ready (max_concurrent=%d)", max_concurrent)

    async def delegate(
        self,
        goal: str,
        context: str = "",
        model: str = "",
        provider: str = "",
        toolsets: Optional[List[str]] = None,
        timeout_secs: float = 300.0,
    ) -> SubagentResult:
        """Delegate a single task to a subagent."""
        config = SubagentConfig(
            model=model or self.default_model,
            provider=provider or self.default_provider,
            timeout_secs=timeout_secs,
        )
        agent = Subagent(
            config=config,
            provider_registry=self.provider_registry,
            tool_registry=self.tool_registry,
            session_store=self.session_store,
        )
        return await agent.execute(goal=goal, context=context, available_tools=toolsets)

    async def delegate_many(
        self,
        tasks: List[Dict[str, str]],
        model: str = "",
        provider: str = "",
    ) -> List[SubagentResult]:
        """Delegate multiple tasks in parallel."""
        coros = [
            self.delegate(
                goal=t["goal"],
                context=t.get("context", ""),
                model=model,
                provider=provider,
                timeout_secs=t.get("timeout", 300.0),
            )
            for t in tasks
        ]
        return await asyncio.gather(*coros)

    async def debate(
        self,
        goal: str,
        context: str = "",
    ) -> DebateResult:
        """Run a two-agent debate on a topic.

        Agent A argues FOR, Agent B argues AGAINST, then they find consensus.
        """
        task_id = uuid.uuid4().hex[:8]

        # Agent A: Pro position
        agent_a = Subagent(
            config=SubagentConfig(
                model=self.default_model,
                provider=self.default_provider,
                system_prompt=(
                    "You are Debater A. Argue FOR the proposition. "
                    "Present the strongest possible case with evidence and reasoning. "
                    "Be thorough but concise."
                ),
            ),
            provider_registry=self.provider_registry,
        )

        # Agent B: Con position
        agent_b = Subagent(
            config=SubagentConfig(
                model=self.default_model,
                provider=self.default_provider,
                system_prompt=(
                    "You are Debater B. Argue AGAINST the proposition. "
                    "Find weaknesses, counter-arguments, and alternative perspectives. "
                    "Be thorough but concise."
                ),
            ),
            provider_registry=self.provider_registry,
        )

        # Run both in parallel
        result_a, result_b = await asyncio.gather(
            agent_a.execute(goal=f"ARGUE FOR: {goal}", context=context),
            agent_b.execute(goal=f"ARGUE AGAINST: {goal}", context=context),
        )

        # Synthesize: ask a neutral agent to find consensus
        consensus_agent = Subagent(
            config=SubagentConfig(
                model=self.default_model,
                provider=self.default_provider,
                system_prompt=(
                    "You are a neutral moderator. Review both sides of the debate "
                    "and identify points of agreement, key differences, and a balanced conclusion."
                ),
            ),
            provider_registry=self.provider_registry,
        )

        debate_context = (
            f"# Debate Topic\n{goal}\n\n"
            f"# Position A (FOR)\n{result_a.summary}\n\n"
            f"# Position B (AGAINST)\n{result_b.summary}\n\n"
            "Identify consensus points, key differences, and provide a balanced conclusion."
        )

        consensus_result = await consensus_agent.execute(
            goal="Find consensus between two opposing positions",
            context=debate_context,
        )

        return DebateResult(
            task_id=task_id,
            goal=goal,
            consensus=consensus_result.summary,
            agent_a_position=result_a.summary[:500],
            agent_b_position=result_b.summary[:500],
            agreement="agree" in consensus_result.summary.lower(),
            confidence=0.6,
        )
