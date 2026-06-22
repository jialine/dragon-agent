"""
Dragon Agent — Dispatcher Module

Intelligent dispatch of user queries to industry-specific LLMs
with circuit breaker, retry, fallback, and streaming support.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, AsyncIterator, Any
import asyncio
import time
import logging
import os

import httpx
from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

# ────────────────────────────────────────────────────────────────────
# Structured logging
# ────────────────────────────────────────────────────────────────────
logger = logging.getLogger("dragon.dispatch")


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════

class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"          # Normal operation — requests pass through
    OPEN = "open"              # Circuit tripped — requests fail fast
    HALF_OPEN = "half_open"    # Testing if service recovered — single probe


@dataclass
class ProviderProfile:
    """
    Configuration profile for an industry-specific LLM provider.

    Attributes:
        name: Unique provider name (e.g., "legal-gpt4", "medical-claude")
        provider: Provider namespace (e.g., "openai", "anthropic", "deepseek")
        model: Model identifier (e.g., "gpt-4-turbo", "deepseek-v3")
        api_key_env: Environment variable name holding the API key
        base_url: OpenAI-compatible API base URL (None = provider default)
        system_prompt: System-level prompt for the industry domain
        timeout: Request timeout in seconds
        max_retries: Max retry attempts (informational; tenacity handles actual retries)
    """

    name: str
    provider: str
    model: str
    api_key_env: str
    base_url: Optional[str] = None
    system_prompt: str = ""
    timeout: float = 60.0
    max_retries: int = 2


@dataclass
class Usage:
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_openai(cls, usage_obj: Any) -> "Usage":
        """Create Usage from an OpenAI API usage object."""
        if usage_obj is None:
            return cls()
        return cls(
            prompt_tokens=getattr(usage_obj, "prompt_tokens", 0),
            completion_tokens=getattr(usage_obj, "completion_tokens", 0),
            total_tokens=getattr(usage_obj, "total_tokens", 0),
        )


@dataclass
class DispatchResult:
    """Result from a dispatch call."""

    industry: str
    provider: str
    model: str
    content: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    knowledge_used: bool = False
    fallback_used: bool = False
    streamed: bool = False


@dataclass
class StreamChunk:
    """A single chunk from a streaming response."""

    content: str
    finish_reason: Optional[str] = None
    usage: Optional[Usage] = None


# ════════════════════════════════════════════════════════════════════
# Exceptions
# ════════════════════════════════════════════════════════════════════

class DispatchError(Exception):
    """Base exception for dispatch failures."""
    pass


class CircuitBreakerOpenError(DispatchError):
    """Raised when a circuit breaker is open and blocking requests."""

    def __init__(self, provider_name: str, retry_after: float):
        self.provider_name = provider_name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker OPEN for '{provider_name}'. "
            f"Retry available in {retry_after:.1f}s"
        )


class ProviderNotFoundError(DispatchError):
    """Raised when no provider is registered for an industry."""
    pass


class AllProvidersFailedError(DispatchError):
    """Raised when both industry and fallback providers fail."""
    pass


# ════════════════════════════════════════════════════════════════════
# Circuit Breaker
# ════════════════════════════════════════════════════════════════════

@dataclass
class _CircuitState:
    """Internal state for a single circuit breaker."""

    failures: int = 0
    last_failure_time: float = 0.0
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    half_open_probe: bool = False


class CircuitBreaker:
    """
    Thread-safe circuit breaker pattern.

    After ``failure_threshold`` consecutive failures, the circuit opens
    for ``recovery_timeout`` seconds. Once the timeout elapses, the circuit
    transitions to HALF_OPEN — the next call is a probe. If it succeeds,
    the circuit closes; if it fails, it re-opens immediately.

    All mutations are protected by an ``asyncio.Lock`` so this is safe to
    use concurrently from multiple async tasks.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._circuits: Dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def _get_state(self, name: str) -> _CircuitState:
        """Get-or-create circuit state for *name* (caller must hold lock)."""
        if name not in self._circuits:
            self._circuits[name] = _CircuitState()
        return self._circuits[name]

    async def before_call(self, name: str) -> None:
        """
        Check whether the circuit allows a call.

        Raises:
            CircuitBreakerOpenError: If the circuit is OPEN.
        """
        async with self._lock:
            state = await self._get_state(name)

            if state.state == CircuitBreakerState.CLOSED:
                return  # All good

            if state.state == CircuitBreakerState.OPEN:
                elapsed = time.monotonic() - state.last_failure_time
                if elapsed >= self.recovery_timeout:
                    # Transition to HALF_OPEN — let one probe through
                    state.state = CircuitBreakerState.HALF_OPEN
                    state.half_open_probe = True
                    logger.info(
                        "Circuit HALF_OPEN for '%s' — sending probe request",
                        name,
                    )
                    return
                else:
                    retry_after = self.recovery_timeout - elapsed
                    raise CircuitBreakerOpenError(name, retry_after)

            if state.state == CircuitBreakerState.HALF_OPEN:
                if state.half_open_probe:
                    # This is the probe — allow it
                    return
                else:
                    # Another task is already probing; block this one
                    elapsed = time.monotonic() - state.last_failure_time
                    retry_after = max(self.recovery_timeout - elapsed, 0)
                    raise CircuitBreakerOpenError(name, retry_after)

    async def on_success(self, name: str) -> None:
        """Report a successful call — reset the circuit to CLOSED."""
        async with self._lock:
            state = await self._get_state(name)
            state.failures = 0
            state.state = CircuitBreakerState.CLOSED
            state.half_open_probe = False
            logger.debug("Circuit CLOSED for '%s'", name)

    async def on_failure(self, name: str) -> None:
        """Report a failed call — increment counter, possibly open circuit."""
        async with self._lock:
            state = await self._get_state(name)
            state.failures += 1
            state.last_failure_time = time.monotonic()

            if state.state == CircuitBreakerState.HALF_OPEN:
                # Probe failed — circuit re-opens immediately
                state.state = CircuitBreakerState.OPEN
                logger.warning(
                    "Circuit OPEN for '%s' (probe failed, total failures=%d)",
                    name,
                    state.failures,
                )
                return

            if state.failures >= self.failure_threshold:
                state.state = CircuitBreakerState.OPEN
                logger.warning(
                    "Circuit OPEN for '%s' after %d consecutive failures",
                    name,
                    state.failures,
                )

    async def reset(self, name: str) -> None:
        """Manually reset a circuit to CLOSED."""
        async with self._lock:
            self._circuits[name] = _CircuitState()
            logger.info("Circuit RESET for '%s'", name)


# ════════════════════════════════════════════════════════════════════
# Dragon Dispatcher
# ════════════════════════════════════════════════════════════════════

class DragonDispatcher:
    """
    Intelligent dispatcher that routes user queries to industry-specific LLMs.

    Features
    --------
    * **Provider registry** — map industry keys to :class:`ProviderProfile`
    * **Circuit breaker** — per-provider; 3 failures → 60 s open
    * **Retry** — via ``tenacity``: max 2 retries, exponential backoff
    * **Fallback chain** — industry provider → general fallback → error
    * **Streaming** — SSE streaming via ``dispatch_stream()`` async generator
    * **Token usage tracking** — captured in :class:`DispatchResult`
    * **Thread-safe** — async throughout; uses ``httpx.AsyncClient`` pool

    Quickstart
    ----------
    ::

        dispatcher = DragonDispatcher()

        # Register industry provider
        dispatcher.register(
            industry="legal",
            profile=ProviderProfile(
                name="legal-gpt4",
                provider="openai",
                model="gpt-4-turbo",
                api_key_env="OPENAI_API_KEY",
                system_prompt="You are a legal expert...",
            ),
        )

        # Set fallback
        dispatcher.set_fallback(
            ProviderProfile(
                name="general-gpt4o",
                provider="openai",
                model="gpt-4o",
                api_key_env="OPENAI_API_KEY",
                system_prompt="You are a helpful assistant.",
            ),
        )

        # Non-streaming dispatch
        result = await dispatcher.dispatch(
            industry="legal",
            messages=[{"role": "user", "content": "What is contract law?"}],
            knowledge="Contract law basics: ...",
            stream=False,
        )
        print(result.content)

        # Streaming dispatch
        async for chunk in dispatcher.dispatch_stream("legal", messages):
            print(chunk.content, end="", flush=True)
    """

    # Tenacity retry defaults (can be overridden per provider via ProviderProfile)
    RETRY_MAX_ATTEMPTS = 3       # initial attempt + 2 retries
    RETRY_MIN_WAIT = 1.0         # seconds
    RETRY_MAX_WAIT = 10.0        # seconds

    def __init__(
        self,
        circuit_failure_threshold: int = 3,
        circuit_recovery_timeout: float = 60.0,
        default_timeout: float = 60.0,
    ):
        """
        Initialize the dispatcher.

        Args:
            circuit_failure_threshold: Consecutive failures before circuit opens.
            circuit_recovery_timeout: Seconds the circuit stays open.
            default_timeout: Default HTTP request timeout in seconds.
        """
        # Industry → ProviderProfile
        self._registry: Dict[str, ProviderProfile] = {}

        # Provider name → AsyncOpenAI client (lazily built)
        self._clients: Dict[str, AsyncOpenAI] = {}

        # Fallback provider
        self._fallback_profile: Optional[ProviderProfile] = None
        self._fallback_client: Optional[AsyncOpenAI] = None

        self._circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout=circuit_recovery_timeout,
        )
        self._default_timeout = default_timeout
        self._lock = asyncio.Lock()
        logger.info(
            "DragonDispatcher initialized (circuit: %d failures → %.0fs open)",
            circuit_failure_threshold,
            circuit_recovery_timeout,
        )

    # ── Registry Management ────────────────────────────────────────────

    def register(self, industry: str, profile: ProviderProfile) -> None:
        """
        Register (or replace) an industry provider.

        Args:
            industry: Industry key (e.g. ``"legal"``, ``"medical"``).
            profile: :class:`ProviderProfile` with connection details.
        """
        self._registry[industry] = profile
        # Invalidate any cached client so it rebuilds with the new profile
        self._clients.pop(profile.name, None)
        logger.info(
            "Registered '%s' for industry '%s' (model=%s, base_url=%s)",
            profile.name,
            industry,
            profile.model,
            profile.base_url or "(default)",
        )

    def unregister(self, industry: str) -> None:
        """Remove a registered industry provider."""
        profile = self._registry.pop(industry, None)
        if profile:
            self._clients.pop(profile.name, None)
            logger.info("Unregistered industry '%s' (was '%s')", industry, profile.name)

    def set_fallback(self, profile: ProviderProfile) -> None:
        """
        Set the fallback (general-purpose) provider.

        The fallback is used when the industry-specific provider fails.
        """
        self._fallback_profile = profile
        self._fallback_client = None  # rebuild on next use
        logger.info("Fallback provider set to '%s' (model=%s)", profile.name, profile.model)

    # ── Client Management ──────────────────────────────────────────────

    @staticmethod
    def _get_api_key(profile: ProviderProfile) -> str:
        """Resolve API key from the environment variable named in the profile."""
        api_key = os.getenv(profile.api_key_env)
        if not api_key:
            raise DispatchError(
                f"API key not found: environment variable "
                f"'{profile.api_key_env}' is not set (required by provider "
                f"'{profile.name}')"
            )
        return api_key

    def _build_client(self, profile: ProviderProfile) -> AsyncOpenAI:
        """Build (or retrieve from cache) an :class:`AsyncOpenAI` client."""
        if profile.name in self._clients:
            return self._clients[profile.name]

        api_key = self._get_api_key(profile)

        client_kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "timeout": httpx.Timeout(profile.timeout),
            "max_retries": 0,  # We handle retries ourselves via tenacity
        }

        if profile.base_url:
            client_kwargs["base_url"] = profile.base_url

        client = AsyncOpenAI(**client_kwargs)
        self._clients[profile.name] = client
        logger.debug("Built AsyncOpenAI client for '%s'", profile.name)
        return client

    def _get_client(self, profile: ProviderProfile) -> AsyncOpenAI:
        """Get-or-build client for a profile."""
        return self._build_client(profile)

    def _get_fallback_client(self) -> Optional[AsyncOpenAI]:
        """Get-or-build the fallback client, if configured."""
        if self._fallback_profile is None:
            return None
        if self._fallback_client is None:
            self._fallback_client = self._build_client(self._fallback_profile)
        return self._fallback_client

    # ── Message Construction ───────────────────────────────────────────

    @staticmethod
    def _build_messages(
        messages: List[Dict[str, str]],
        system_prompt: str,
        knowledge: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        Assemble the full message list.

        The system prompt is injected as the ``system`` role message.
        If *knowledge* is provided, it is appended to the system message
        under a ``## Reference Knowledge`` heading.
        """
        built: List[Dict[str, str]] = []

        system_content = system_prompt
        if knowledge:
            system_content += f"\n\n## Reference Knowledge\n{knowledge}"

        if system_content:
            built.append({"role": "system", "content": system_content})

        built.extend(messages)
        return built

    # ── Retry Helper ───────────────────────────────────────────────────

    def _retry_decorator(self, provider_name: str):
        """
        Build a ``tenacity`` retry decorator for the given provider.

        Retries on transport-level errors (HTTP status, connection, timeout)
        but *not* on semantic errors (bad request, auth failure, etc.).
        """
        return retry(
            stop=stop_after_attempt(self.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(
                multiplier=1,
                min=self.RETRY_MIN_WAIT,
                max=self.RETRY_MAX_WAIT,
            ),
            retry=retry_if_exception_type((
                httpx.HTTPStatusError,
                httpx.RequestError,
                httpx.TimeoutException,
            )),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    # ════════════════════════════════════════════════════════════════════
    # Non-streaming Dispatch
    # ════════════════════════════════════════════════════════════════════

    async def dispatch(
        self,
        industry: str,
        messages: List[Dict[str, str]],
        knowledge: Optional[str] = None,
        stream: bool = False,
    ) -> DispatchResult:
        """
        Dispatch a query to the industry-specific LLM (non-streaming).

        Flow
        ----
        1. Look up provider for *industry*
        2. Check circuit breaker
        3. Build messages (system prompt + optional knowledge)
        4. Call LLM with retry
        5. On failure → try fallback provider
        6. On fallback failure → raise :class:`AllProvidersFailedError`

        Args:
            industry: Target industry key.
            messages: Chat messages (``role`` + ``content`` dicts).
            knowledge: Optional reference knowledge injected into the system prompt.
            stream: Must be ``False`` here; use :meth:`dispatch_stream` for streaming.

        Returns:
            :class:`DispatchResult` with content, usage, latency, etc.

        Raises:
            ValueError: If ``stream=True`` (use ``dispatch_stream`` instead).
            ProviderNotFoundError: No provider registered for *industry*.
            CircuitBreakerOpenError: Circuit is open for the provider.
            AllProvidersFailedError: Both industry and fallback failed.
        """
        if stream:
            raise ValueError(
                "Use dispatch_stream() for streaming. "
                "dispatch() only supports non-streaming mode."
            )

        profile = self._registry.get(industry)
        if profile is None:
            raise ProviderNotFoundError(
                f"No provider registered for industry '{industry}'. "
                f"Available industries: {list(self._registry.keys())}"
            )

        knowledge_used = knowledge is not None

        # ── Attempt 1: industry provider ──
        try:
            return await self._call_provider(
                profile=profile,
                messages=messages,
                knowledge=knowledge,
                industry=industry,
                knowledge_used=knowledge_used,
            )
        except CircuitBreakerOpenError:
            # Circuit is open — don't retry, go straight to fallback
            logger.warning(
                "Circuit OPEN for '%s' — skipping to fallback", profile.name
            )
        except Exception as exc:
            logger.warning(
                "Industry provider '%s' failed: %s. Falling back.",
                profile.name,
                exc,
            )

        # ── Attempt 2: fallback provider ──
        if self._fallback_profile is None:
            raise AllProvidersFailedError(
                f"Industry provider '{profile.name}' failed and no "
                f"fallback provider is configured."
            )

        try:
            result = await self._call_provider(
                profile=self._fallback_profile,
                messages=messages,
                knowledge=knowledge,
                industry=industry,
                knowledge_used=knowledge_used,
            )
            result.fallback_used = True
            logger.info(
                "Fallback '%s' succeeded for industry '%s'",
                self._fallback_profile.name,
                industry,
            )
            return result
        except Exception as exc:
            raise AllProvidersFailedError(
                f"Both industry provider '{profile.name}' and fallback "
                f"'{self._fallback_profile.name}' failed. Last error: {exc}"
            ) from exc

    async def _call_provider(
        self,
        profile: ProviderProfile,
        messages: List[Dict[str, str]],
        knowledge: Optional[str],
        industry: str,
        knowledge_used: bool,
    ) -> DispatchResult:
        """Execute a single provider call with circuit breaker + retry."""
        # 1. Check circuit breaker
        await self._circuit_breaker.before_call(profile.name)

        client = self._get_client(profile)
        built_messages = self._build_messages(
            messages, profile.system_prompt, knowledge
        )

        # 2. Wrap the raw API call with tenacity retry
        call_with_retry = self._retry_decorator(profile.name)(self._do_api_call)

        start = time.monotonic()
        try:
            response = await call_with_retry(client, profile.model, built_messages)
            latency_ms = (time.monotonic() - start) * 1000

            # Success → close the circuit
            await self._circuit_breaker.on_success(profile.name)

            content = response.choices[0].message.content or ""
            usage = Usage.from_openai(response.usage)

            logger.debug(
                "Provider '%s' responded in %.0f ms (tokens: %d)",
                profile.name,
                latency_ms,
                usage.total_tokens,
            )

            return DispatchResult(
                industry=industry,
                provider=profile.name,
                model=profile.model,
                content=content,
                usage=usage,
                latency_ms=latency_ms,
                knowledge_used=knowledge_used,
            )

        except Exception:
            # Failure → record in circuit breaker (may open it)
            await self._circuit_breaker.on_failure(profile.name)
            raise

    @staticmethod
    async def _do_api_call(
        client: AsyncOpenAI,
        model: str,
        messages: List[Dict[str, str]],
    ) -> Any:
        """
        Raw chat-completion API call.

        This is the innermost call wrapped by tenacity for retries.
        """
        return await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
        )

    # ════════════════════════════════════════════════════════════════════
    # Streaming Dispatch
    # ════════════════════════════════════════════════════════════════════

    async def dispatch_stream(
        self,
        industry: str,
        messages: List[Dict[str, str]],
        knowledge: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Dispatch with SSE streaming.

        Yields :class:`StreamChunk` objects as tokens arrive from the LLM.
        The final chunk (if supported by the provider) includes ``usage``
        information for token tracking.

        Falls back to the general provider if the industry provider fails.

        Usage::

            async for chunk in dispatcher.dispatch_stream("legal", messages):
                print(chunk.content, end="", flush=True)
        """
        profile = self._registry.get(industry)
        if profile is None:
            raise ProviderNotFoundError(
                f"No provider registered for industry '{industry}'. "
                f"Available: {list(self._registry.keys())}"
            )

        # ── Attempt 1: industry provider ──
        try:
            async for chunk in self._stream_provider(
                profile=profile,
                messages=messages,
                knowledge=knowledge,
            ):
                yield chunk
            return
        except CircuitBreakerOpenError:
            logger.warning(
                "Circuit OPEN for '%s' — streaming via fallback", profile.name
            )
        except Exception as exc:
            logger.warning(
                "Industry provider '%s' stream failed: %s", profile.name, exc
            )

        # ── Attempt 2: fallback ──
        if self._fallback_profile is None:
            raise AllProvidersFailedError(
                f"Stream provider '{profile.name}' failed and no fallback configured."
            )

        try:
            async for chunk in self._stream_provider(
                profile=self._fallback_profile,
                messages=messages,
                knowledge=knowledge,
            ):
                yield chunk
        except Exception as exc:
            raise AllProvidersFailedError(
                f"Both stream providers failed. Last error: {exc}"
            ) from exc

    async def _stream_provider(
        self,
        profile: ProviderProfile,
        messages: List[Dict[str, str]],
        knowledge: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream from a single provider with circuit breaker protection."""
        await self._circuit_breaker.before_call(profile.name)

        client = self._get_client(profile)
        built_messages = self._build_messages(
            messages, profile.system_prompt, knowledge
        )

        try:
            stream = await client.chat.completions.create(
                model=profile.model,
                messages=built_messages,
                temperature=0.7,
                stream=True,
                stream_options={"include_usage": True},
            )

            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield StreamChunk(
                            content=delta.content,
                            finish_reason=chunk.choices[0].finish_reason,
                        )
                elif hasattr(chunk, "usage") and chunk.usage:
                    # Final usage chunk (OpenAI stream_options include_usage)
                    yield StreamChunk(
                        content="",
                        finish_reason="stop",
                        usage=Usage.from_openai(chunk.usage),
                    )

            # Stream completed successfully
            await self._circuit_breaker.on_success(profile.name)

        except Exception:
            await self._circuit_breaker.on_failure(profile.name)
            raise

    # ── Utilities ──────────────────────────────────────────────────────

    @property
    def registered_industries(self) -> List[str]:
        """List of all registered industry keys."""
        return list(self._registry.keys())

    def get_profile(self, industry: str) -> Optional[ProviderProfile]:
        """Get the :class:`ProviderProfile` for an industry, or ``None``."""
        return self._registry.get(industry)

    async def circuit_status(self, name: str) -> Dict[str, Any]:
        """Get the current circuit breaker status for a provider."""
        async with self._circuit_breaker._lock:
            state = self._circuit_breaker._circuits.get(name)
            if state is None:
                return {"name": name, "state": "unknown"}
            return {
                "name": name,
                "state": state.state.value,
                "failures": state.failures,
                "last_failure_time": state.last_failure_time,
            }

    async def reset_circuit(self, name: str) -> None:
        """Manually reset a provider's circuit breaker to CLOSED."""
        await self._circuit_breaker.reset(name)

    async def close(self) -> None:
        """Close all underlying HTTP clients and release resources."""
        for name, client in self._clients.items():
            await client.close()
            logger.debug("Closed client for '%s'", name)
        if self._fallback_client:
            await self._fallback_client.close()
        self._clients.clear()
        self._fallback_client = None
        logger.info("DragonDispatcher closed — all clients shut down")

    # ── Async Context Manager ──────────────────────────────────────────

    async def __aenter__(self) -> "DragonDispatcher":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ════════════════════════════════════════════════════════════════════
# Exports
# ════════════════════════════════════════════════════════════════════

__all__ = [
    # Core dispatcher
    "DragonDispatcher",
    # Data classes
    "ProviderProfile",
    "DispatchResult",
    "StreamChunk",
    "Usage",
    # Circuit breaker
    "CircuitBreakerState",
    "CircuitBreaker",
    # Exceptions
    "DispatchError",
    "CircuitBreakerOpenError",
    "ProviderNotFoundError",
    "AllProvidersFailedError",
]
