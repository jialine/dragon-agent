"""
Dragon Agent — Auxiliary Client (Model Fallback Chain)
======================================================

Provides a multi-provider, multi-model dispatch chain with:
  - Price-aware routing (cheapest model meeting quality threshold)
  - Error-based intelligent fallback (429 → rotate key, 503 → switch model, 401 → skip)
  - Latency tracking with adaptive model selection
  - Budget-aware dispatch (stay within user budget)

Architecture::

    AuxiliaryClient
        │
        ├── ModelChain (primary → fallback_0 → fallback_1 → ...)
        │   ├── ModelSlot(provider, model, api_key, price_per_1k, quality_score)
        │   └── RoutingStrategy
        │       ├── cheapest_first  — price-aware, quality-gated
        │       ├── quality_first   — best quality, budget-constrained
        │       └── priority_chain  — strict fallback order
        │
        ├── ErrorFallbackEngine
        │   ├── classify_error()
        │   ├── 429 → try next key for same provider, then next model
        │   ├── 503 → switch model
        │   ├── 401 → skip provider entirely
        │   └── 402 → skip provider (billing)
        │
        └── LatencyTracker
            └── Per-model: avg, p50, p95, sample_count

Usage::

    from dragon.auxiliary import AuxiliaryClient, ModelSlot

    client = AuxiliaryClient()
    client.add_slot(ModelSlot(
        name="gpt4o",
        provider="openai",
        model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
        price_per_1k_tokens=0.005,
        quality_score=0.95,
    ))
    client.add_slot(ModelSlot(
        name="haiku",
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
        price_per_1k_tokens=0.001,
        quality_score=0.75,
    ))

    response = await client.route(
        messages=[{"role": "user", "content": "Summarize this article"}],
        budget=0.01,
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from dragon.error_classifier import (
    classify_api_error,
    ClassifiedError,
    ErrorCategory,
)

logger = logging.getLogger("dragon.auxiliary")


# ══════════════════════════════════════════════════════════════════════
# Routing Strategy
# ══════════════════════════════════════════════════════════════════════


class RoutingStrategy(Enum):
    """How the auxiliary client selects the next model to try."""

    PRIORITY_CHAIN = "priority_chain"   # Try slots in registration order
    CHEAPEST_FIRST = "cheapest_first"   # Cheapest model that meets quality floor
    QUALITY_FIRST = "quality_first"     # Best quality within budget
    LOWEST_LATENCY = "lowest_latency"   # Fastest model (if latency data available)


# ══════════════════════════════════════════════════════════════════════
# Model Slot
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ModelSlot:
    """A specific model + provider combination in the fallback chain.

    Attributes:
        name: Human-readable slot name (e.g. ``"gpt4o-primary"``).
        provider: Provider namespace (``"openai"``, ``"anthropic"``, ``"deepseek"``, …).
        model: Model identifier (e.g. ``"gpt-4o"``, ``"deepseek-chat"``).
        api_key_env: Environment variable holding the API key.
        api_key: Direct API key value (takes precedence over *api_key_env*).
        base_url: OpenAI-compatible base URL (``None`` = provider default).
        price_per_1k_tokens: Estimated price in USD per 1000 tokens (input+output avg).
        quality_score: Subjective quality score (0.0–1.0). Higher = better.
        max_tokens: Default max output tokens.
        temperature: Default temperature.
        timeout_secs: HTTP request timeout.
        extra_headers: Additional HTTP headers for this slot.
        enabled: Whether this slot is active.
        metadata: Arbitrary extra data (tags, region, etc.).
    """

    name: str
    provider: str
    model: str
    api_key_env: str = ""
    api_key: str = ""
    base_url: Optional[str] = None
    price_per_1k_tokens: float = 0.0
    quality_score: float = 0.7
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout_secs: float = 120.0
    extra_headers: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def resolve_api_key(self) -> str:
        """Resolve the API key from direct value or environment variable."""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.getenv(self.api_key_env, "")
        return ""


# ══════════════════════════════════════════════════════════════════════
# Latency Metrics
# ══════════════════════════════════════════════════════════════════════


@dataclass
class LatencyMetrics:
    """Per-model latency statistics."""

    samples: List[float] = field(default_factory=list)
    max_samples: int = 100

    @property
    def avg(self) -> float:
        """Average latency in milliseconds."""
        if not self.samples:
            return float("inf")
        return statistics.mean(self.samples)

    @property
    def p50(self) -> float:
        """50th percentile (median) latency in milliseconds."""
        if not self.samples:
            return float("inf")
        sorted_samples = sorted(self.samples)
        n = len(sorted_samples)
        return sorted_samples[n // 2]

    @property
    def p95(self) -> float:
        """95th percentile latency in milliseconds."""
        if not self.samples:
            return float("inf")
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    @property
    def count(self) -> int:
        """Number of recorded samples."""
        return len(self.samples)

    def record(self, latency_ms: float) -> None:
        """Record a latency sample. Evicts oldest when over capacity."""
        self.samples.append(latency_ms)
        if len(self.samples) > self.max_samples:
            self.samples = self.samples[-self.max_samples:]

    def reset(self) -> None:
        """Clear all recorded samples."""
        self.samples.clear()


# ══════════════════════════════════════════════════════════════════════
# Route Result
# ══════════════════════════════════════════════════════════════════════


@dataclass
class RouteResult:
    """Result from a :meth:`AuxiliaryClient.route` call.

    Attributes:
        content: Response text content.
        model: Model that produced the response.
        provider: Provider that produced the response.
        slot_name: Name of the :class:`ModelSlot` used.
        usage: Token usage dict (``prompt_tokens``, ``completion_tokens``, ``total_tokens``).
        latency_ms: End-to-end latency (including retries/fallbacks).
        cost_estimate: Estimated cost in USD.
        fallback_used: ``True`` if the primary slot failed.
        attempts: Total attempts made (including failures).
        error_context: Error from the last failed attempt, if any (for diagnostics).
    """

    content: str
    model: str
    provider: str
    slot_name: str
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    cost_estimate: float = 0.0
    fallback_used: bool = False
    attempts: int = 1
    error_context: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════
# Auxiliary Client
# ══════════════════════════════════════════════════════════════════════


class AuxiliaryClient:
    """Multi-model dispatch client with intelligent fallback.

    Manages a chain of :class:`ModelSlot` instances and routes requests
    through them based on the configured :class:`RoutingStrategy`.

    Features:
        - **Price-aware routing**: pick cheapest model meeting quality threshold
        - **Error-based fallback**: 429 → next key, 503 → next model, 401 → skip provider
        - **Latency tracking**: adaptive model selection based on real-time metrics
        - **Budget enforcement**: stop when estimated cost exceeds budget
        - **Circuit breaker**: per-model cooldown after repeated failures

    Example::

        client = AuxiliaryClient(strategy=RoutingStrategy.CHEAPEST_FIRST)

        client.add_slot(ModelSlot(
            name="gpt4o", provider="openai", model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
            price_per_1k_tokens=0.005, quality_score=0.95,
        ))
        client.add_slot(ModelSlot(
            name="deepseek", provider="deepseek", model="deepseek-chat",
            api_key_env="DEEPSEEK_API_KEY",
            price_per_1k_tokens=0.0003, quality_score=0.85,
        ))
        client.set_quality_floor(0.70)  # Minimum quality for cheapest_first

        result = await client.route(
            messages=[{"role": "user", "content": "Explain quantum computing"}],
            budget=0.02,
        )
        print(result.content)
    """

    def __init__(
        self,
        strategy: RoutingStrategy = RoutingStrategy.PRIORITY_CHAIN,
        quality_floor: float = 0.0,
        max_budget: float = 1.0,
        max_total_attempts: int = 5,
        circuit_cooldown_secs: float = 30.0,
        circuit_failure_threshold: int = 3,
    ):
        """Initialize the auxiliary client.

        Args:
            strategy: Default routing strategy.
            quality_floor: Minimum quality score (0.0–1.0) for ``CHEAPEST_FIRST``.
            max_budget: Hard budget cap in USD (per route call).
            max_total_attempts: Max total attempts across all fallbacks.
            circuit_cooldown_secs: Cooldown after circuit breaker trips.
            circuit_failure_threshold: Consecutive failures to trip circuit.
        """
        self._slots: List[ModelSlot] = []
        self._slot_index: Dict[str, ModelSlot] = {}
        self._strategy = strategy
        self._quality_floor = quality_floor
        self._max_budget = max_budget
        self._max_total_attempts = max_total_attempts

        # Per-slot latency tracking
        self._latency: Dict[str, LatencyMetrics] = {}

        # Per-slot circuit breaker state
        self._circuit_failures: Dict[str, int] = {}
        self._circuit_cooldown_until: Dict[str, float] = {}
        self._circuit_cooldown_secs = circuit_cooldown_secs
        self._circuit_failure_threshold = circuit_failure_threshold

        # Per-slot API key rotation state (for 429 handling)
        self._key_pools: Dict[str, List[str]] = {}  # slot_name → [key1, key2, ...]
        self._key_index: Dict[str, int] = {}         # slot_name → current key index

        self._lock = asyncio.Lock()

        # Provider adapter registry
        self._provider_callbacks: Dict[str, Callable[..., Any]] = {}

        logger.info(
            "AuxiliaryClient initialized (strategy=%s, quality_floor=%.2f, max_budget=$%.4f)",
            strategy.value, quality_floor, max_budget,
        )

    # ── Slot Management ──────────────────────────────────────────────────

    def add_slot(self, slot: ModelSlot) -> None:
        """Add a model slot to the chain.

        Slots are tried in the order determined by the routing strategy.
        For ``PRIORITY_CHAIN``, slots are tried in registration order.

        Args:
            slot: :class:`ModelSlot` to add.
        """
        self._slots.append(slot)
        self._slot_index[slot.name] = slot
        self._latency.setdefault(slot.name, LatencyMetrics())
        logger.info(
            "Added slot '%s' (provider=%s, model=%s, price=$%.5f/1k, quality=%.2f)",
            slot.name, slot.provider, slot.model,
            slot.price_per_1k_tokens, slot.quality_score,
        )

    def remove_slot(self, name: str) -> bool:
        """Remove a model slot by name.

        Args:
            name: Slot name.

        Returns:
            ``True`` if removed.
        """
        slot = self._slot_index.pop(name, None)
        if slot:
            self._slots = [s for s in self._slots if s.name != name]
            self._latency.pop(name, None)
            self._circuit_failures.pop(name, None)
            self._circuit_cooldown_until.pop(name, None)
            logger.info("Removed slot '%s'", name)
            return True
        return False

    def add_api_key(self, slot_name: str, api_key: str) -> None:
        """Add an additional API key for a slot (for 429 key rotation).

        Args:
            slot_name: The slot to add a key to.
            api_key: The API key string.
        """
        if slot_name not in self._key_pools:
            # Start with the primary key
            slot = self._slot_index.get(slot_name)
            primary = slot.resolve_api_key() if slot else ""
            self._key_pools[slot_name] = [primary] if primary else []
            self._key_index[slot_name] = 0
        if api_key and api_key not in self._key_pools[slot_name]:
            self._key_pools[slot_name].append(api_key)
            logger.debug(
                "Added API key #%d for slot '%s'",
                len(self._key_pools[slot_name]), slot_name,
            )

    def set_strategy(self, strategy: RoutingStrategy) -> None:
        """Change the routing strategy."""
        self._strategy = strategy
        logger.info("Routing strategy changed to %s", strategy.value)

    def set_quality_floor(self, floor: float) -> None:
        """Set the minimum quality score for ``CHEAPEST_FIRST`` routing."""
        self._quality_floor = max(0.0, min(1.0, floor))
        logger.info("Quality floor set to %.2f", self._quality_floor)

    def set_max_budget(self, budget: float) -> None:
        """Set the hard budget cap."""
        self._max_budget = budget
        logger.info("Max budget set to $%.4f", budget)

    def register_provider_adapter(
        self, provider: str, callback: Callable[..., Any]
    ) -> None:
        """Register a custom provider adapter for non-OpenAI-compatible providers.

        Args:
            provider: Provider name (e.g. ``"anthropic"``).
            callback: Async callable ``(slot, messages, **kwargs) → RouteResult-like``.
        """
        self._provider_callbacks[provider] = callback
        logger.info("Registered provider adapter for '%s'", provider)

    # ── Routing ──────────────────────────────────────────────────────────

    async def route(
        self,
        messages: List[Dict[str, str]],
        industry: str = "general",
        budget: Optional[float] = None,
        quality_floor: Optional[float] = None,
        strategy: Optional[RoutingStrategy] = None,
        system_prompt: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> RouteResult:
        """Route a request through the model chain with intelligent fallback.

        Args:
            messages: Chat messages in OpenAI format
                (``[{"role": "user", "content": "..."}]``).
            industry: Target industry/department (for logging and slot filtering).
            budget: Max budget for this call in USD (overrides default).
            quality_floor: Minimum quality score (overrides default).
            strategy: Routing strategy for this call (overrides default).
            system_prompt: Optional system message.
            temperature: Sampling temperature (overrides slot default).
            max_tokens: Max output tokens (overrides slot default).
            **kwargs: Additional keyword arguments passed to the provider call.

        Returns:
            :class:`RouteResult` with response content and metadata.

        Raises:
            RuntimeError: If all slots in the chain fail.
            ValueError: If no slots are configured.
        """
        if not self._slots:
            raise ValueError("No model slots configured. Use add_slot() first.")

        effective_budget = budget if budget is not None else self._max_budget
        effective_quality = quality_floor if quality_floor is not None else self._quality_floor
        effective_strategy = strategy or self._strategy

        # Build the ordered list of slots to try
        ordered_slots = self._order_slots(effective_strategy, effective_quality)
        if not ordered_slots:
            raise RuntimeError(
                f"No slots meet quality floor {effective_quality}. "
                f"Configured slots: {[s.name for s in self._slots]}"
            )

        # Inject system prompt if provided
        built_messages = list(messages)
        if system_prompt:
            # Check if a system message already exists
            has_system = any(m.get("role") == "system" for m in built_messages)
            if not has_system:
                built_messages.insert(0, {"role": "system", "content": system_prompt})

        total_attempts = 0
        total_cost = 0.0
        last_error: Optional[str] = None
        start_time = time.monotonic()
        skipped_providers: set = set()  # Providers to skip (401, 402)

        for slot in ordered_slots:
            if not slot.enabled:
                continue

            # Skip providers with permanent failures
            if slot.provider in skipped_providers:
                logger.debug(
                    "Skipping slot '%s' (provider '%s' is blocked)",
                    slot.name, slot.provider,
                )
                continue

            # Check circuit breaker
            if self._is_circuit_open(slot.name):
                logger.debug(
                    "Skipping slot '%s' (circuit breaker open)",
                    slot.name,
                )
                continue

            # Check budget
            cost_estimate = self._estimate_cost(
                slot, built_messages, max_tokens or slot.max_tokens
            )
            if total_cost + cost_estimate > effective_budget:
                logger.info(
                    "Skipping slot '%s' (would exceed budget: $%.5f + $%.5f > $%.5f)",
                    slot.name, total_cost, cost_estimate, effective_budget,
                )
                continue

            total_attempts += 1
            if total_attempts > self._max_total_attempts:
                raise RuntimeError(
                    f"Exceeded max attempts ({self._max_total_attempts}). "
                    f"Last error: {last_error}"
                )

            try:
                result = await self._call_slot(
                    slot=slot,
                    messages=built_messages,
                    temperature=temperature or slot.temperature,
                    max_tokens=max_tokens or slot.max_tokens,
                    **kwargs,
                )

                # Success! Record and return
                self._on_success(slot.name, result.latency_ms)
                result.fallback_used = (total_attempts > 1)
                result.attempts = total_attempts
                result.latency_ms = (time.monotonic() - start_time) * 1000
                result.error_context = last_error

                # Calculate actual cost
                prompt_tokens = result.usage.get("prompt_tokens", 0)
                completion_tokens = result.usage.get("completion_tokens", 0)
                result.cost_estimate = (
                    (prompt_tokens + completion_tokens) / 1000 * slot.price_per_1k_tokens
                )

                logger.info(
                    "Route succeeded via slot '%s' (attempt #%d, "
                    "%.0f ms, %d tokens, $%.6f)",
                    slot.name, total_attempts, result.latency_ms,
                    result.usage.get("total_tokens", 0), result.cost_estimate,
                )
                return result

            except Exception as exc:
                error_type = type(exc).__name__
                logger.warning(
                    "Slot '%s' failed (attempt #%d/%d): %s: %s",
                    slot.name, total_attempts,
                    self._max_total_attempts, error_type, exc,
                )
                last_error = str(exc)

                # Classify the error and apply fallback logic
                classified = classify_api_error(
                    exc, provider=slot.provider, model=slot.model,
                )

                self._on_failure(slot.name)

                await self._apply_error_fallback(
                    slot=slot,
                    classified=classified,
                    skipped_providers=skipped_providers,
                )

        # All slots exhausted
        raise RuntimeError(
            f"All {len(ordered_slots)} slot(s) failed. "
            f"Last error: {last_error}"
        )

    async def route_stream(
        self,
        messages: List[Dict[str, str]],
        industry: str = "general",
        budget: Optional[float] = None,
        quality_floor: Optional[float] = None,
        strategy: Optional[RoutingStrategy] = None,
        system_prompt: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Any:
        """Streaming version of :meth:`route`.

        Yields text chunks from the first successful slot.

        Returns:
            An async generator yielding content strings.
        """
        # For simplicity, use non-streaming with the route method
        # and yield the result as a single chunk.
        # In production, you'd implement proper SSE streaming per slot.
        result = await self.route(
            messages=messages,
            industry=industry,
            budget=budget,
            quality_floor=quality_floor,
            strategy=strategy,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        # Yield as single chunk (adapter for streaming consumers)
        async def _gen():
            yield result.content

        return _gen()

    # ── Slot Ordering ────────────────────────────────────────────────────

    def _order_slots(
        self,
        strategy: RoutingStrategy,
        quality_floor: float,
    ) -> List[ModelSlot]:
        """Order slots according to the routing strategy.

        Returns:
            Ordered list of :class:`ModelSlot`.
        """
        active = [s for s in self._slots if s.enabled]

        if strategy == RoutingStrategy.PRIORITY_CHAIN:
            # Respect registration order
            return active

        elif strategy == RoutingStrategy.CHEAPEST_FIRST:
            # Filter by quality, sort by price
            qualified = [s for s in active if s.quality_score >= quality_floor]
            if not qualified:
                # If no slot meets quality floor, fall back to all active
                logger.warning(
                    "No slots meet quality floor %.2f — using all slots",
                    quality_floor,
                )
                qualified = active
            return sorted(qualified, key=lambda s: s.price_per_1k_tokens)

        elif strategy == RoutingStrategy.QUALITY_FIRST:
            # Sort by quality (descending), then price (ascending)
            return sorted(
                active,
                key=lambda s: (-s.quality_score, s.price_per_1k_tokens),
            )

        elif strategy == RoutingStrategy.LOWEST_LATENCY:
            # Sort by recorded average latency (ascending)
            def _latency_key(s: ModelSlot) -> float:
                m = self._latency.get(s.name)
                return m.avg if m else float("inf")

            return sorted(active, key=_latency_key)

        return active

    # ── Provider Call ────────────────────────────────────────────────────

    async def _call_slot(
        self,
        slot: ModelSlot,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> RouteResult:
        """Execute a provider call for a specific slot.

        Uses the registered provider adapter if available; otherwise
        uses the generic OpenAI-compatible HTTP client.
        """
        adapter = self._provider_callbacks.get(slot.provider)
        if adapter:
            return await adapter(slot, messages, temperature, max_tokens, **kwargs)

        return await self._call_openai_compatible(
            slot, messages, temperature, max_tokens, **kwargs
        )

    async def _call_openai_compatible(
        self,
        slot: ModelSlot,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> RouteResult:
        """Call an OpenAI-compatible chat completions endpoint.

        Uses httpx for HTTP so we don't depend on the openai SDK being
        installed. Handles API key rotation for 429 errors.
        """
        import httpx

        api_key = self._get_active_key(slot.name) or slot.resolve_api_key()
        if not api_key:
            raise ValueError(
                f"No API key available for slot '{slot.name}'. "
                f"Set {slot.api_key_env} environment variable."
            )

        base_url = (slot.base_url or f"https://api.openai.com/v1").rstrip("/")
        url = f"{base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "DragonAgent/1.0",
            **slot.extra_headers,
        }

        payload = {
            "model": slot.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        start = time.monotonic()

        async with httpx.AsyncClient(timeout=slot.timeout_secs) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code == 429:
                # Try next API key, if available
                next_key = self._rotate_key(slot.name)
                if next_key and next_key != api_key:
                    logger.info(
                        "Slot '%s': 429 — rotating to next API key", slot.name
                    )
                    headers["Authorization"] = f"Bearer {next_key}"
                    resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code >= 400:
                # Build a proper exception with status code for the classifier
                error_body = None
                try:
                    error_body = resp.json()
                except Exception:
                    pass

                error_msg = f"HTTP {resp.status_code}"
                if error_body:
                    error_msg = str(error_body.get("error", {}).get(
                        "message", error_body.get("message", error_msg)
                    ))

                # Create an exception that the error classifier can work with
                exc = _ProviderHTTPError(
                    status_code=resp.status_code,
                    message=error_msg,
                    body=error_body,
                )
                raise exc

            data = resp.json()
            latency_ms = (time.monotonic() - start) * 1000

            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})

            return RouteResult(
                content=content,
                model=data.get("model", slot.model),
                provider=slot.provider,
                slot_name=slot.name,
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                latency_ms=latency_ms,
            )

    # ── Error Fallback Engine ────────────────────────────────────────────

    async def _apply_error_fallback(
        self,
        slot: ModelSlot,
        classified: ClassifiedError,
        skipped_providers: set,
    ) -> None:
        """Apply fallback logic based on error classification.

        Rules:
            - 429 (rate_limit): rotate API key for same slot
            - 401/402 (auth/billing): skip this provider entirely
            - 503 (overloaded): circuit-break and move on
            - context_overflow: no special action (slot already tried)
        """
        category = classified.category

        if category == ErrorCategory.rate_limit:
            # Rotate key for this slot so next attempt uses a different key
            logger.info("Slot '%s': rate limited — rotating API key", slot.name)
            self._rotate_key(slot.name)

        elif category in (ErrorCategory.auth, ErrorCategory.auth_permanent):
            logger.warning(
                "Slot '%s': auth failure — blocking provider '%s'",
                slot.name, slot.provider,
            )
            skipped_providers.add(slot.provider)

        elif category == ErrorCategory.billing:
            logger.warning(
                "Slot '%s': billing exhausted — blocking provider '%s'",
                slot.name, slot.provider,
            )
            skipped_providers.add(slot.provider)

        elif category == ErrorCategory.overloaded:
            logger.warning(
                "Slot '%s': provider overloaded — circuit breaker tripped",
                slot.name,
            )
            # Circuit breaker already recorded in _on_failure

        elif category == ErrorCategory.model_not_found:
            logger.error(
                "Slot '%s': model '%s' not found — check model name",
                slot.name, slot.model,
            )
            # Don't retry this slot for this call

    # ── Circuit Breaker ──────────────────────────────────────────────────

    def _is_circuit_open(self, slot_name: str) -> bool:
        """Check if the circuit breaker is open for a slot."""
        cooldown_until = self._circuit_cooldown_until.get(slot_name, 0)
        if time.monotonic() < cooldown_until:
            return True
        return False

    def _on_success(self, slot_name: str, latency_ms: float) -> None:
        """Handle successful call: reset circuit, record latency."""
        self._circuit_failures[slot_name] = 0
        self._circuit_cooldown_until.pop(slot_name, None)

        metrics = self._latency.get(slot_name)
        if metrics:
            metrics.record(latency_ms)

    def _on_failure(self, slot_name: str) -> None:
        """Handle failed call: increment circuit breaker counter."""
        failures = self._circuit_failures.get(slot_name, 0) + 1
        self._circuit_failures[slot_name] = failures

        if failures >= self._circuit_failure_threshold:
            self._circuit_cooldown_until[slot_name] = (
                time.monotonic() + self._circuit_cooldown_secs
            )
            logger.warning(
                "Slot '%s': circuit breaker OPEN (%d consecutive failures)",
                slot_name, failures,
            )

    def reset_circuit(self, slot_name: str) -> None:
        """Manually reset a slot's circuit breaker."""
        self._circuit_failures.pop(slot_name, None)
        self._circuit_cooldown_until.pop(slot_name, None)
        logger.info("Circuit reset for slot '%s'", slot_name)

    # ── API Key Rotation ─────────────────────────────────────────────────

    def _get_active_key(self, slot_name: str) -> str:
        """Get the currently active API key for a slot."""
        pool = self._key_pools.get(slot_name, [])
        if not pool:
            return ""
        idx = self._key_index.get(slot_name, 0)
        return pool[idx % len(pool)]

    def _rotate_key(self, slot_name: str) -> str:
        """Rotate to the next API key in the pool. Returns the new key."""
        pool = self._key_pools.get(slot_name, [])
        if not pool:
            return ""
        idx = (self._key_index.get(slot_name, 0) + 1) % len(pool)
        self._key_index[slot_name] = idx
        logger.debug(
            "Rotated API key for slot '%s' → key #%d/%d",
            slot_name, idx + 1, len(pool),
        )
        return pool[idx]

    # ── Cost Estimation ──────────────────────────────────────────────────

    @staticmethod
    def _estimate_cost(
        slot: ModelSlot, messages: List[Dict[str, str]], max_tokens: int
    ) -> float:
        """Rough cost estimate based on message count and max tokens.

        Uses a simple heuristic: ~4 chars per token on average.
        """
        total_chars = sum(len(m.get("content", "")) for m in messages)
        est_input_tokens = max(total_chars // 4, 10)
        est_output_tokens = max_tokens
        return (est_input_tokens + est_output_tokens) / 1000 * slot.price_per_1k_tokens

    # ── Latency Stats ────────────────────────────────────────────────────

    def get_latency_report(self) -> Dict[str, Dict[str, Any]]:
        """Get latency statistics for all slots.

        Returns:
            Dict mapping slot_name → {avg, p50, p95, count}.
        """
        return {
            name: {
                "avg": m.avg,
                "p50": m.p50,
                "p95": m.p95,
                "count": m.count,
            }
            for name, m in self._latency.items()
            if m.count > 0
        }

    def get_status_report(self) -> str:
        """Generate a human-readable status report for all slots."""
        lines = ["🐉 Auxiliary Client — Slot Status", "=" * 50]

        for slot in self._slots:
            status = "✅" if slot.enabled else "⏸️"
            if self._is_circuit_open(slot.name):
                status = "🔴"

            m = self._latency.get(slot.name)
            latency_str = (
                f"avg={m.avg:.0f}ms, p95={m.p95:.0f}ms"
                if m and m.count > 0
                else "no data"
            )

            lines.append(
                f"\n{status} {slot.name} "
                f"({slot.provider}/{slot.model})"
            )
            lines.append(f"   Quality: {slot.quality_score:.2f}  "
                         f"Price: ${slot.price_per_1k_tokens:.5f}/1k tokens")
            lines.append(f"   Latency: {latency_str}")

        lines.append(f"\n{'=' * 50}")
        lines.append(
            f"Strategy: {self._strategy.value} | "
            f"Quality floor: {self._quality_floor:.2f} | "
            f"Max budget: ${self._max_budget:.4f}"
        )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Internal Exception for Error Classification
# ══════════════════════════════════════════════════════════════════════


class _ProviderHTTPError(Exception):
    """Internal exception wrapping HTTP errors for the error classifier.

    Carries status_code and body so :func:`classify_api_error` can
    properly categorize the failure.
    """

    def __init__(
        self,
        status_code: int,
        message: str = "",
        body: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ══════════════════════════════════════════════════════════════════════
# Convenience: Integration with dragon.dispatch
# ══════════════════════════════════════════════════════════════════════


def create_dispatch_chain(
    providers: List[Dict[str, Any]],
    strategy: RoutingStrategy = RoutingStrategy.CHEAPEST_FIRST,
    quality_floor: float = 0.6,
) -> AuxiliaryClient:
    """Quick factory to create an :class:`AuxiliaryClient` from a list of dicts.

    Each dict should have keys matching :class:`ModelSlot` fields.

    Example::

        client = create_dispatch_chain([
            {
                "name": "gpt4o",
                "provider": "openai",
                "model": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
                "price_per_1k_tokens": 0.005,
                "quality_score": 0.95,
            },
            {
                "name": "deepseek",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key_env": "DEEPSEEK_API_KEY",
                "price_per_1k_tokens": 0.0003,
                "quality_score": 0.85,
            },
        ])

        result = await client.route(messages=[...])
    """
    client = AuxiliaryClient(strategy=strategy, quality_floor=quality_floor)
    for cfg in providers:
        slot = ModelSlot(**cfg)
        client.add_slot(slot)
    return client


# ══════════════════════════════════════════════════════════════════════
# Module Exports
# ══════════════════════════════════════════════════════════════════════

__all__ = [
    "AuxiliaryClient",
    "ModelSlot",
    "RouteResult",
    "RoutingStrategy",
    "LatencyMetrics",
    "create_dispatch_chain",
]
