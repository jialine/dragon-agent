"""
Dragon Agent — Plugin Hook System
=================================

Defines the standard lifecycle hooks that plugins can subscribe to,
along with typed hook payloads for type-safe hook invocation.

Hook Lifecycle Flow::

    on_session_start
        │
        ▼
    before_request ──▶ [provider.complete()] ──▶ after_response
        │                                              │
        ├── on_error (on failure)                      │
        │                                              │
        └──────────────────────────────────────────────┘
        │
        ▼
    on_session_end

Plugin authors register for hooks via ``ctx.register_hook(name, callback)``.
The callback signature depends on the hook — see each hook's docstring.

Usage::

    from dragon.plugin.hooks import (
        HookSystem,
        BeforeRequestPayload,
        AfterResponsePayload,
        ErrorPayload,
    )

    hooks = HookSystem(plugin_manager)
    await hooks.fire_before_request(messages=[...], model="gpt-4o")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("dragon.plugin.hooks")


# ══════════════════════════════════════════════════════════════════════
# Hook Names (canonical registry)
# ══════════════════════════════════════════════════════════════════════

# Standard hook names — all plugins can subscribe to these.
# Plugins register callbacks via ``ctx.register_hook(name, callback)``.

VALID_HOOKS: frozenset = frozenset({
    # ── Session lifecycle ───────────────────────────────────────────
    "on_session_start",       # New conversation session begins
    "on_session_end",         # Conversation session ends
    "on_session_reset",       # Session is manually reset/cleared

    # ── Request pipeline ────────────────────────────────────────────
    "before_request",         # Before any LLM API request
    "after_response",         # After receiving LLM API response

    # ── Streaming ───────────────────────────────────────────────────
    "on_stream_chunk",        # Each streaming chunk received

    # ── Error handling ──────────────────────────────────────────────
    "on_error",               # On any API or operational error

    # ── Tool lifecycle ──────────────────────────────────────────────
    "pre_tool_call",          # Before a tool is invoked
    "post_tool_call",         # After a tool invocation completes
    "transform_tool_result",  # Transform tool output before returning to LLM

    # ── Message transformation ──────────────────────────────────────
    "transform_request",      # Transform outgoing request (messages, params)
    "transform_response",     # Transform incoming response text

    # ── Provider events ─────────────────────────────────────────────
    "on_provider_switch",     # When the system switches providers (fallback)
    "on_model_select",        # When a model is selected for a request

    # ── Gateway events ──────────────────────────────────────────────
    "pre_gateway_dispatch",   # Before gateway dispatches an incoming message
    "post_gateway_dispatch",  # After gateway finishes handling a message

    # ── System events ───────────────────────────────────────────────
    "on_startup",             # Agent process starts
    "on_shutdown",            # Agent process shutting down
    "on_config_change",       # Configuration is updated (hot-reload)
})


# ══════════════════════════════════════════════════════════════════════
# Hook Payloads (typed data passed to callbacks)
# ══════════════════════════════════════════════════════════════════════


@dataclass
class BeforeRequestPayload:
    """Payload for ``before_request`` hook.

    Plugins can modify ``messages`` and ``params`` to influence the request.
    """

    messages: List[Dict[str, str]]
    model: str
    provider: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class AfterResponsePayload:
    """Payload for ``after_response`` hook.

    Plugins can inspect or transform the response content.
    """

    content: str
    model: str
    provider: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class StreamChunkPayload:
    """Payload for ``on_stream_chunk`` hook."""

    content: str
    model: str = ""
    finish_reason: Optional[str] = None
    chunk_index: int = 0
    session_id: str = ""


@dataclass
class ErrorPayload:
    """Payload for ``on_error`` hook.

    Plugins can inspect the error and suggest recovery actions.
    """

    error: Any
    category: str = "unknown"
    status_code: Optional[int] = None
    provider: str = ""
    model: str = ""
    session_id: str = ""
    retry_count: int = 0
    recovery_suggestion: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolCallPayload:
    """Payload for ``pre_tool_call`` / ``post_tool_call`` hooks."""

    tool_name: str
    tool_args: Dict[str, Any]
    session_id: str = ""
    # post_tool_call only:
    result: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProviderSwitchPayload:
    """Payload for ``on_provider_switch`` hook."""

    from_provider: str
    from_model: str
    to_provider: str
    to_model: str
    reason: str = ""
    error: Optional[Any] = None
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class GatewayDispatchPayload:
    """Payload for gateway dispatch hooks."""

    message_text: str
    platform: str = ""
    user_id: str = ""
    channel_id: str = ""
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════
# Hook System
# ══════════════════════════════════════════════════════════════════════


class HookSystem:
    """Typed hook firing interface for the Dragon Agent core.

    Wraps :class:`PluginManager.invoke_hook` with typed payloads
    and provides convenience methods for each standard hook.

    Usage::

        hooks = HookSystem(plugin_manager)

        # Fire before_request hook
        payload = BeforeRequestPayload(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4o",
        )
        modified = await hooks.fire_before_request(payload)
        # modified.messages may have been transformed by plugins
    """

    def __init__(self, plugin_manager: Any) -> None:
        """Initialize hook system.

        Args:
            plugin_manager: A :class:`PluginManager` instance.
        """
        self._pm = plugin_manager

    # ── Fire Methods ─────────────────────────────────────────────────────

    async def fire_before_request(
        self, payload: BeforeRequestPayload
    ) -> BeforeRequestPayload:
        """Fire ``before_request`` hook.

        Plugins may return a modified ``BeforeRequestPayload`` to
        transform messages or params. The first non-None payload returned
        by a plugin is used.

        Args:
            payload: The outgoing request payload.

        Returns:
            Possibly modified payload.
        """
        results = await self._pm.invoke_hook_async(
            "before_request",
            payload=payload,
        )
        for r in results:
            if isinstance(r, BeforeRequestPayload):
                payload = r
                break
            elif isinstance(r, dict):
                # Allow dict-based partial updates
                payload = BeforeRequestPayload(
                    messages=r.get("messages", payload.messages),
                    model=r.get("model", payload.model),
                    provider=r.get("provider", payload.provider),
                    params=r.get("params", payload.params),
                    session_id=r.get("session_id", payload.session_id),
                )
                break
        return payload

    async def fire_after_response(
        self, payload: AfterResponsePayload
    ) -> AfterResponsePayload:
        """Fire ``after_response`` hook.

        Plugins may transform the response content.

        Args:
            payload: The received response payload.

        Returns:
            Possibly modified payload.
        """
        results = await self._pm.invoke_hook_async(
            "after_response",
            payload=payload,
        )
        for r in results:
            if isinstance(r, AfterResponsePayload):
                payload = r
                break
            elif isinstance(r, str) and r.strip():
                payload.content = r
                break
        return payload

    async def fire_on_error(self, payload: ErrorPayload) -> None:
        """Fire ``on_error`` hook.

        Plugins can inspect the error and potentially mutate
        ``payload.recovery_suggestion``. All callbacks are invoked
        (fire-and-forget pattern).

        Args:
            payload: The error context.
        """
        results = await self._pm.invoke_hook_async(
            "on_error",
            payload=payload,
        )
        # Allow plugins to provide recovery suggestions
        for r in results:
            if isinstance(r, str) and r.strip():
                payload.recovery_suggestion = r
                break

    async def fire_pre_tool_call(self, payload: ToolCallPayload) -> ToolCallPayload:
        """Fire ``pre_tool_call`` hook.

        Plugins may modify tool arguments or skip the call.
        """
        results = await self._pm.invoke_hook_async(
            "pre_tool_call",
            payload=payload,
        )
        for r in results:
            if isinstance(r, ToolCallPayload):
                payload = r
                break
            elif isinstance(r, dict):
                payload.tool_args = r.get("tool_args", payload.tool_args)
                break
        return payload

    async def fire_post_tool_call(self, payload: ToolCallPayload) -> ToolCallPayload:
        """Fire ``post_tool_call`` hook."""
        results = await self._pm.invoke_hook_async(
            "post_tool_call",
            payload=payload,
        )
        for r in results:
            if isinstance(r, ToolCallPayload):
                payload = r
                break
            elif isinstance(r, dict):
                payload.result = r.get("result", payload.result)
                break
        return payload

    async def fire_transform_tool_result(
        self, tool_name: str, result: str, session_id: str = ""
    ) -> str:
        """Fire ``transform_tool_result`` hook.

        Plugins can transform tool output text. The first non-empty
        string returned by a plugin replaces the result.

        Returns:
            Possibly transformed result string.
        """
        results = await self._pm.invoke_hook_async(
            "transform_tool_result",
            tool_name=tool_name,
            result=result,
            session_id=session_id,
        )
        for r in results:
            if isinstance(r, str) and r.strip():
                return r
        return result

    async def fire_on_provider_switch(
        self, payload: ProviderSwitchPayload
    ) -> None:
        """Fire ``on_provider_switch`` hook (fire-and-forget)."""
        await self._pm.invoke_hook_async(
            "on_provider_switch",
            payload=payload,
        )

    async def fire_on_session_start(
        self, session_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Fire ``on_session_start`` hook."""
        await self._pm.invoke_hook_async(
            "on_session_start",
            session_id=session_id,
            metadata=metadata or {},
        )

    async def fire_on_session_end(
        self, session_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Fire ``on_session_end`` hook."""
        await self._pm.invoke_hook_async(
            "on_session_end",
            session_id=session_id,
            metadata=metadata or {},
        )

    async def fire_on_startup(self) -> None:
        """Fire ``on_startup`` hook."""
        await self._pm.invoke_hook_async("on_startup")

    async def fire_on_shutdown(self) -> None:
        """Fire ``on_shutdown`` hook."""
        await self._pm.invoke_hook_async("on_shutdown")

    async def fire_on_config_change(
        self, changes: Dict[str, Any]
    ) -> None:
        """Fire ``on_config_change`` hook."""
        await self._pm.invoke_hook_async(
            "on_config_change",
            changes=changes,
        )

    # ── Convenience: Fire-and-forget (sync) ──────────────────────────────

    def fire_before_request_sync(self, payload: BeforeRequestPayload) -> BeforeRequestPayload:
        """Synchronous version of :meth:`fire_before_request`."""
        results = self._pm.invoke_hook("before_request", payload=payload)
        for r in results:
            if isinstance(r, BeforeRequestPayload):
                return r
            elif isinstance(r, dict):
                return BeforeRequestPayload(
                    messages=r.get("messages", payload.messages),
                    model=r.get("model", payload.model),
                    provider=r.get("provider", payload.provider),
                    params=r.get("params", payload.params),
                    session_id=r.get("session_id", payload.session_id),
                )
        return payload

    def fire_on_error_sync(self, payload: ErrorPayload) -> None:
        """Synchronous version of :meth:`fire_on_error`."""
        self._pm.invoke_hook("on_error", payload=payload)


# ══════════════════════════════════════════════════════════════════════
# Module Exports
# ══════════════════════════════════════════════════════════════════════

__all__ = [
    "HookSystem",
    "VALID_HOOKS",
    "BeforeRequestPayload",
    "AfterResponsePayload",
    "StreamChunkPayload",
    "ErrorPayload",
    "ToolCallPayload",
    "ProviderSwitchPayload",
    "GatewayDispatchPayload",
]
