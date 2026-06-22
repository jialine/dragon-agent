"""
Per-model pricing data for Dragon Agent.

Stores pricing information for all major LLM providers (OpenAI, Anthropic,
DeepSeek, Google, xAI, local providers, etc.) and provides cost estimation
utilities. Used by the insights engine for cost tracking.

Pricing is stored per 1,000 tokens for input and output, with optional
image pricing. Currency conversion helpers are included.

Usage::

    from dragon.usage_pricing import get_cost, list_models, ModelPricing
    cost = get_cost("openai", "gpt-4o", prompt_tokens=1500, completion_tokens=800)
    print(f"Estimated cost: ${cost:.4f}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────


@dataclass
class ModelPricing:
    """Pricing information for a specific model."""

    provider: str
    model: str
    input_price_per_1k: float  # USD per 1K input tokens
    output_price_per_1k: float  # USD per 1K output tokens
    cache_read_price_per_1k: float = 0.0
    cache_write_price_per_1k: float = 0.0
    image_price: Optional[float] = None  # Per-image cost
    source_url: str = ""
    notes: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class CostResult:
    """Result of a cost estimation."""

    amount_usd: float
    prompt_cost: float
    completion_cost: float
    cache_read_cost: float = 0.0
    cache_write_cost: float = 0.0
    image_cost: float = 0.0
    model: str = ""
    provider: str = ""
    is_estimate: bool = True


# ────────────────────────────────────────────────────────────────────
# Pricing Database
# ────────────────────────────────────────────────────────────────────

_PRICING: Dict[Tuple[str, str], ModelPricing] = {}

# ── OpenAI ──────────────────────────────────────────────────────────

_PRICING[("openai", "gpt-4o")] = ModelPricing(
    provider="openai", model="gpt-4o",
    input_price_per_1k=0.0025, output_price_per_1k=0.010,
    cache_read_price_per_1k=0.00125,
    source_url="https://openai.com/api/pricing/",
    notes="GPT-4o, 2025 pricing",
)

_PRICING[("openai", "gpt-4o-mini")] = ModelPricing(
    provider="openai", model="gpt-4o-mini",
    input_price_per_1k=0.00015, output_price_per_1k=0.0006,
    cache_read_price_per_1k=0.000075,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "gpt-4.1")] = ModelPricing(
    provider="openai", model="gpt-4.1",
    input_price_per_1k=0.002, output_price_per_1k=0.008,
    cache_read_price_per_1k=0.0005,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "gpt-4.1-mini")] = ModelPricing(
    provider="openai", model="gpt-4.1-mini",
    input_price_per_1k=0.0004, output_price_per_1k=0.0016,
    cache_read_price_per_1k=0.0001,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "gpt-4.1-nano")] = ModelPricing(
    provider="openai", model="gpt-4.1-nano",
    input_price_per_1k=0.0001, output_price_per_1k=0.0004,
    cache_read_price_per_1k=0.000025,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "o3")] = ModelPricing(
    provider="openai", model="o3",
    input_price_per_1k=0.010, output_price_per_1k=0.040,
    cache_read_price_per_1k=0.0025,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "o3-mini")] = ModelPricing(
    provider="openai", model="o3-mini",
    input_price_per_1k=0.0011, output_price_per_1k=0.0044,
    cache_read_price_per_1k=0.00055,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "o1")] = ModelPricing(
    provider="openai", model="o1",
    input_price_per_1k=0.015, output_price_per_1k=0.060,
    cache_read_price_per_1k=0.0075,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "o1-mini")] = ModelPricing(
    provider="openai", model="o1-mini",
    input_price_per_1k=0.003, output_price_per_1k=0.012,
    cache_read_price_per_1k=0.0015,
    source_url="https://openai.com/api/pricing/",
)

_PRICING[("openai", "gpt-4-turbo")] = ModelPricing(
    provider="openai", model="gpt-4-turbo",
    input_price_per_1k=0.01, output_price_per_1k=0.03,
    source_url="https://openai.com/api/pricing/",
)

# ── Anthropic ───────────────────────────────────────────────────────

_PRICING[("anthropic", "claude-opus-4-7")] = ModelPricing(
    provider="anthropic", model="claude-opus-4-7",
    input_price_per_1k=0.005, output_price_per_1k=0.025,
    cache_read_price_per_1k=0.0005, cache_write_price_per_1k=0.00625,
    source_url="https://www.anthropic.com/pricing",
)

_PRICING[("anthropic", "claude-sonnet-4-7")] = ModelPricing(
    provider="anthropic", model="claude-sonnet-4-7",
    input_price_per_1k=0.003, output_price_per_1k=0.015,
    cache_read_price_per_1k=0.0003, cache_write_price_per_1k=0.00375,
    source_url="https://www.anthropic.com/pricing",
)

_PRICING[("anthropic", "claude-haiku-4-5")] = ModelPricing(
    provider="anthropic", model="claude-haiku-4-5",
    input_price_per_1k=0.001, output_price_per_1k=0.005,
    cache_read_price_per_1k=0.0001, cache_write_price_per_1k=0.00125,
    source_url="https://www.anthropic.com/pricing",
)

_PRICING[("anthropic", "claude-opus-4-5")] = ModelPricing(
    provider="anthropic", model="claude-opus-4-5",
    input_price_per_1k=0.005, output_price_per_1k=0.025,
    cache_read_price_per_1k=0.0005, cache_write_price_per_1k=0.00625,
    source_url="https://www.anthropic.com/pricing",
)

_PRICING[("anthropic", "claude-sonnet-4-5")] = ModelPricing(
    provider="anthropic", model="claude-sonnet-4-5",
    input_price_per_1k=0.003, output_price_per_1k=0.015,
    cache_read_price_per_1k=0.0003, cache_write_price_per_1k=0.00375,
    source_url="https://www.anthropic.com/pricing",
)

_PRICING[("anthropic", "claude-opus-4")] = ModelPricing(
    provider="anthropic", model="claude-opus-4",
    input_price_per_1k=0.015, output_price_per_1k=0.075,
    cache_read_price_per_1k=0.0015, cache_write_price_per_1k=0.01875,
    source_url="https://www.anthropic.com/pricing",
)

_PRICING[("anthropic", "claude-sonnet-4")] = ModelPricing(
    provider="anthropic", model="claude-sonnet-4",
    input_price_per_1k=0.003, output_price_per_1k=0.015,
    cache_read_price_per_1k=0.0003, cache_write_price_per_1k=0.00375,
    source_url="https://www.anthropic.com/pricing",
)

# ── DeepSeek ────────────────────────────────────────────────────────

_PRICING[("deepseek", "deepseek-chat")] = ModelPricing(
    provider="deepseek", model="deepseek-chat",
    input_price_per_1k=0.00027, output_price_per_1k=0.0011,
    source_url="https://api-docs.deepseek.com/quick_start/pricing",
    notes="DeepSeek-V3, 2025 pricing",
)

_PRICING[("deepseek", "deepseek-reasoner")] = ModelPricing(
    provider="deepseek", model="deepseek-reasoner",
    input_price_per_1k=0.00055, output_price_per_1k=0.00219,
    source_url="https://api-docs.deepseek.com/quick_start/pricing",
    notes="DeepSeek-R1 reasoning model",
)

_PRICING[("deepseek", "deepseek-v3")] = ModelPricing(
    provider="deepseek", model="deepseek-v3",
    input_price_per_1k=0.00027, output_price_per_1k=0.0011,
    source_url="https://api-docs.deepseek.com/quick_start/pricing",
)

# ── Google / Gemini ─────────────────────────────────────────────────

_PRICING[("google", "gemini-2.5-pro")] = ModelPricing(
    provider="google", model="gemini-2.5-pro",
    input_price_per_1k=0.00125, output_price_per_1k=0.010,
    cache_read_price_per_1k=0.0003125,
    source_url="https://ai.google.dev/pricing",
)

_PRICING[("google", "gemini-2.5-flash")] = ModelPricing(
    provider="google", model="gemini-2.5-flash",
    input_price_per_1k=0.00015, output_price_per_1k=0.0006,
    cache_read_price_per_1k=0.0000375,
    source_url="https://ai.google.dev/pricing",
)

_PRICING[("google", "gemini-2.0-flash")] = ModelPricing(
    provider="google", model="gemini-2.0-flash",
    input_price_per_1k=0.0001, output_price_per_1k=0.0004,
    source_url="https://ai.google.dev/pricing",
)

_PRICING[("google", "gemini-1.5-pro")] = ModelPricing(
    provider="google", model="gemini-1.5-pro",
    input_price_per_1k=0.00125, output_price_per_1k=0.005,
    source_url="https://ai.google.dev/pricing",
)

# ── xAI / Grok ──────────────────────────────────────────────────────

_PRICING[("xai", "grok-3-beta")] = ModelPricing(
    provider="xai", model="grok-3-beta",
    input_price_per_1k=0.005, output_price_per_1k=0.015,
    source_url="https://x.ai/api/pricing",
)

_PRICING[("xai", "grok-3-mini")] = ModelPricing(
    provider="xai", model="grok-3-mini",
    input_price_per_1k=0.0006, output_price_per_1k=0.002,
    source_url="https://x.ai/api/pricing",
)

# ── Mistral ─────────────────────────────────────────────────────────

_PRICING[("mistral", "mistral-large-latest")] = ModelPricing(
    provider="mistral", model="mistral-large-latest",
    input_price_per_1k=0.002, output_price_per_1k=0.006,
    source_url="https://mistral.ai/technology/#pricing",
)

_PRICING[("mistral", "mistral-small-latest")] = ModelPricing(
    provider="mistral", model="mistral-small-latest",
    input_price_per_1k=0.0002, output_price_per_1k=0.0006,
    source_url="https://mistral.ai/technology/#pricing",
)

_PRICING[("mistral", "codestral-latest")] = ModelPricing(
    provider="mistral", model="codestral-latest",
    input_price_per_1k=0.0003, output_price_per_1k=0.0009,
    source_url="https://mistral.ai/technology/#pricing",
)

# ── Cohere ──────────────────────────────────────────────────────────

_PRICING[("cohere", "command-r-plus")] = ModelPricing(
    provider="cohere", model="command-r-plus",
    input_price_per_1k=0.0025, output_price_per_1k=0.010,
    source_url="https://cohere.com/pricing",
)

_PRICING[("cohere", "command-r")] = ModelPricing(
    provider="cohere", model="command-r",
    input_price_per_1k=0.0005, output_price_per_1k=0.0015,
    source_url="https://cohere.com/pricing",
)

# ── Moonshot / Kimi ─────────────────────────────────────────────────

_PRICING[("moonshot", "moonshot-v1-8k")] = ModelPricing(
    provider="moonshot", model="moonshot-v1-8k",
    input_price_per_1k=0.00034, output_price_per_1k=0.00068,
    source_url="https://platform.moonshot.cn/docs/pricing",
)

_PRICING[("moonshot", "moonshot-v1-32k")] = ModelPricing(
    provider="moonshot", model="moonshot-v1-32k",
    input_price_per_1k=0.00068, output_price_per_1k=0.00136,
    source_url="https://platform.moonshot.cn/docs/pricing",
)

_PRICING[("moonshot", "moonshot-v1-128k")] = ModelPricing(
    provider="moonshot", model="moonshot-v1-128k",
    input_price_per_1k=0.0034, output_price_per_1k=0.0068,
    source_url="https://platform.moonshot.cn/docs/pricing",
)

# ── Local / Free ────────────────────────────────────────────────────

_PRICING[("local", "local-model")] = ModelPricing(
    provider="local", model="local-model",
    input_price_per_1k=0.0, output_price_per_1k=0.0,
    notes="Locally hosted model — no API cost",
)

_PRICING[("ollama", "llama3")] = ModelPricing(
    provider="ollama", model="llama3",
    input_price_per_1k=0.0, output_price_per_1k=0.0,
    notes="Locally hosted via Ollama — no API cost",
)

_PRICING[("openrouter", "router-default")] = ModelPricing(
    provider="openrouter", model="router-default",
    input_price_per_1k=0.0, output_price_per_1k=0.0,
    notes="OpenRouter pricing varies by underlying model — use specific model key",
)

# ── Together AI ─────────────────────────────────────────────────────

_PRICING[("together", "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8")] = ModelPricing(
    provider="together", model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    input_price_per_1k=0.0008, output_price_per_1k=0.0008,
    source_url="https://www.together.ai/pricing",
)

# ── Groq ────────────────────────────────────────────────────────────

_PRICING[("groq", "llama-4-maverick-17b-128e-instruct")] = ModelPricing(
    provider="groq", model="llama-4-maverick-17b-128e-instruct",
    input_price_per_1k=0.0002, output_price_per_1k=0.0006,
    source_url="https://groq.com/pricing/",
)

_PRICING[("groq", "llama-3.3-70b-versatile")] = ModelPricing(
    provider="groq", model="llama-3.3-70b-versatile",
    input_price_per_1k=0.00059, output_price_per_1k=0.00079,
    source_url="https://groq.com/pricing/",
)

# ── Perplexity ──────────────────────────────────────────────────────

_PRICING[("perplexity", "sonar-pro")] = ModelPricing(
    provider="perplexity", model="sonar-pro",
    input_price_per_1k=0.001, output_price_per_1k=0.001,
    source_url="https://docs.perplexity.ai/guides/pricing",
)


# ────────────────────────────────────────────────────────────────────
# Model Name Normalization
# ────────────────────────────────────────────────────────────────────

_MODEL_ALIASES: Dict[str, Tuple[str, str]] = {
    # OpenAI aliases
    "gpt4o": ("openai", "gpt-4o"),
    "gpt4": ("openai", "gpt-4-turbo"),
    # Anthropic aliases
    "claude": ("anthropic", "claude-sonnet-4-7"),
    "opus": ("anthropic", "claude-opus-4-7"),
    "sonnet": ("anthropic", "claude-sonnet-4-7"),
    "haiku": ("anthropic", "claude-haiku-4-5"),
    # DeepSeek aliases
    "deepseek": ("deepseek", "deepseek-chat"),
    # Google aliases
    "gemini": ("google", "gemini-2.5-pro"),
}


def _normalize_key(provider: str, model: str) -> Tuple[str, str]:
    """Normalize provider+model into a canonical lookup key."""
    p = provider.lower().strip()
    m = model.lower().strip()
    # Check aliases first
    if m in _MODEL_ALIASES:
        return _MODEL_ALIASES[m]
    return (p, m)


def _fuzzy_match(model: str, provider: str = "") -> Optional[ModelPricing]:
    """Find pricing by fuzzy-matching model name against known entries."""
    model_lower = model.lower().strip()
    provider_lower = provider.lower().strip() if provider else ""

    candidates: List[Tuple[float, ModelPricing]] = []

    for (p_key, m_key), pricing in _PRICING.items():
        if provider_lower and p_key != provider_lower:
            continue
        if m_key == model_lower:
            return pricing
        # Partial match: model name contains the query or vice versa
        if model_lower in m_key or m_key in model_lower:
            score = len(set(model_lower) & set(m_key)) / max(len(model_lower), len(m_key))
            candidates.append((score, pricing))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return None


# ────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────


def get_pricing(provider: str, model: str) -> Optional[ModelPricing]:
    """Look up pricing data for a provider+model combination.

    Args:
        provider: Provider name (e.g., 'openai', 'anthropic')
        model: Model name (e.g., 'gpt-4o', 'claude-sonnet-4-7')

    Returns:
        ModelPricing if found, None otherwise.
    """
    key = _normalize_key(provider, model)
    if key in _PRICING:
        return _PRICING[key]
    # Try fuzzy match with provider
    return _fuzzy_match(model, provider)


def get_cost(
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    image_count: int = 0,
) -> CostResult:
    """Estimate the cost of an API call.

    Args:
        provider: Provider name.
        model: Model name.
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.
        cache_read_tokens: Number of cache read tokens.
        cache_write_tokens: Number of cache write tokens.
        image_count: Number of images processed.

    Returns:
        CostResult with breakdown of costs.
    """
    pricing = get_pricing(provider, model)
    if pricing is None:
        pricing = ModelPricing(
            provider=provider, model=model,
            input_price_per_1k=0.0, output_price_per_1k=0.0,
        )

    prompt_cost = (prompt_tokens / 1000.0) * pricing.input_price_per_1k
    completion_cost = (completion_tokens / 1000.0) * pricing.output_price_per_1k

    cache_read_cost = 0.0
    if cache_read_tokens and pricing.cache_read_price_per_1k:
        cache_read_cost = (cache_read_tokens / 1000.0) * pricing.cache_read_price_per_1k

    cache_write_cost = 0.0
    if cache_write_tokens and pricing.cache_write_price_per_1k:
        cache_write_cost = (cache_write_tokens / 1000.0) * pricing.cache_write_price_per_1k

    image_cost = 0.0
    if image_count and pricing.image_price:
        image_cost = image_count * pricing.image_price

    total = prompt_cost + completion_cost + cache_read_cost + cache_write_cost + image_cost

    return CostResult(
        amount_usd=round(total, 6),
        prompt_cost=round(prompt_cost, 6),
        completion_cost=round(completion_cost, 6),
        cache_read_cost=round(cache_read_cost, 6),
        cache_write_cost=round(cache_write_cost, 6),
        image_cost=round(image_cost, 6),
        model=pricing.model,
        provider=pricing.provider,
        is_estimate=True,
    )


def list_models(provider: Optional[str] = None) -> List[ModelPricing]:
    """List all priced models, optionally filtered by provider.

    Args:
        provider: Optional provider name to filter by.

    Returns:
        List of ModelPricing entries.
    """
    result = []
    for (p_key, m_key), pricing in _PRICING.items():
        if provider and p_key != provider.lower():
            continue
        result.append(pricing)
    return sorted(result, key=lambda x: (x.provider, x.model))


def list_providers() -> List[str]:
    """List all providers with pricing data."""
    providers = {p for (p, _) in _PRICING.keys()}
    return sorted(providers)


def get_provider_models(provider: str) -> List[ModelPricing]:
    """Get all models for a specific provider."""
    return list_models(provider=provider)


def convert_currency(usd_amount: float, currency: str = "CNY") -> float:
    """Convert USD to another currency at approximate rates.

    Args:
        usd_amount: Amount in USD.
        currency: Target currency code (CNY, EUR, GBP, JPY, etc.).

    Returns:
        Converted amount (rounded to 4 decimal places).
    """
    rates: Dict[str, float] = {
        "CNY": 7.25,
        "EUR": 0.92,
        "GBP": 0.79,
        "JPY": 150.0,
        "KRW": 1350.0,
        "INR": 83.0,
        "CAD": 1.36,
        "AUD": 1.52,
        "CHF": 0.88,
        "HKD": 7.82,
        "SGD": 1.34,
        "BRL": 5.05,
        "MXN": 17.1,
    }
    rate = rates.get(currency.upper(), 1.0)
    return round(usd_amount * rate, 4)


def format_cost(amount_usd: float, currency: Optional[str] = None) -> str:
    """Format a cost amount for display.

    Args:
        amount_usd: Cost in USD.
        currency: Optional currency code for conversion.

    Returns:
        Formatted string like '$0.0123' or '¥0.0892'.
    """
    symbols: Dict[str, str] = {
        "USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£",
        "JPY": "¥", "KRW": "₩", "INR": "₹",
    }
    cur = (currency or "USD").upper()
    if cur != "USD" and cur in symbols:
        converted = convert_currency(amount_usd, cur)
        return f"{symbols[cur]}{converted:.4f}"
    return f"${amount_usd:.4f}"


# ────────────────────────────────────────────────────────────────────
# Bulk estimation for session data
# ────────────────────────────────────────────────────────────────────


def estimate_session_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> CostResult:
    """Estimate cost for a session's total token usage.

    Convenience wrapper around get_cost() with session-friendly parameter names.

    Args:
        provider: Provider name.
        model: Model name.
        input_tokens: Total input tokens in the session.
        output_tokens: Total output tokens in the session.
        cache_read_tokens: Cache read tokens.
        cache_write_tokens: Cache write tokens.

    Returns:
        CostResult with cost breakdown.
    """
    return get_cost(
        provider=provider,
        model=model,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def has_known_pricing(provider: str, model: str) -> bool:
    """Check if we have pricing data for a model.

    Args:
        provider: Provider name.
        model: Model name.

    Returns:
        True if pricing data exists.
    """
    return get_pricing(provider, model) is not None


__all__ = [
    "ModelPricing",
    "CostResult",
    "get_pricing",
    "get_cost",
    "list_models",
    "list_providers",
    "get_provider_models",
    "convert_currency",
    "format_cost",
    "estimate_session_cost",
    "has_known_pricing",
]
