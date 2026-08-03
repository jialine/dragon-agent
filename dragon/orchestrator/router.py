"""
Tier Router — dispatches to appropriate model tier based on classification.

Tier 1 → local Qwen2.5-1.5B (in-process, fast, cheap)
Tier 2 → remote medium model (qwen3.6-flash, single call)
Tier 3 → multi-model debate → vote → plan (deepseek-v4-pro + qwen3.7-max + glm-5.2)
"""

import asyncio
import time
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

from .classifier import classify, Tier, Classification

logger = logging.getLogger("dragon.orchestrator")


# ── Model configs per tier ─────────────────────────────────────────

TIER_MODELS = {
    Tier.SIMPLE: {
        "provider": "local",
        "model": "local",
        "max_tokens": 512,
        "temperature": 0.5,
    },
    Tier.MEDIUM: {
        "provider": "openai",
        "model": "qwen3.6-flash",
        "max_tokens": 2048,
        "temperature": 0.7,
    },
    Tier.COMPLEX: {
        # Multi-model ensemble — see debater.py
        "models": [
            {"provider": "openai", "model": "deepseek-v4-pro", "max_tokens": 2048},
            {"provider": "openai", "model": "qwen3.7-max", "max_tokens": 2048},
            {"provider": "openai", "model": "glm-5.2", "max_tokens": 2048},
        ],
        "temperature": 0.7,
    },
}


@dataclass
class RouteResult:
    tier: Tier
    classification: Classification
    content: str
    model_used: str
    latency_ms: float
    debate_verdict: Optional[str] = None  # for Tier 3: voting summary


class TierRouter:
    """Routes messages to appropriate model tier."""

    def __init__(self, provider_registry: Any):
        self.registry = provider_registry

    def classify(self, text: str) -> Classification:
        return classify(text)

    async def route(
        self,
        messages: List[Dict[str, str]],
        tier: Optional[Tier] = None,
        user_text: str = "",
    ) -> RouteResult:
        """Route a message through the appropriate tier."""
        t0 = time.monotonic()

        # Classify if not provided
        if tier is None:
            classification = classify(user_text or self._extract_user_text(messages))
        else:
            classification = Classification(tier, 1.0, "user-forced")

        tier = classification.tier
        logger.info(
            "Routing: tier=%d confidence=%.2f reason=%s",
            tier, classification.confidence, classification.reason,
        )

        if tier == Tier.SIMPLE:
            return await self._route_simple(messages, t0)
        elif tier == Tier.MEDIUM:
            return await self._route_medium(messages, t0)
        else:
            return await self._route_complex(messages, classification, t0)

    async def _route_simple(
        self, messages: List[Dict[str, str]], t0: float
    ) -> RouteResult:
        """Tier 1: local model quick answer."""
        cfg = TIER_MODELS[Tier.SIMPLE]
        try:
            result = await self.registry.call(
                provider_name=cfg["provider"],
                model=cfg["model"],
                messages=messages,
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
            )
            return RouteResult(
                tier=Tier.SIMPLE,
                classification=Classification(Tier.SIMPLE, 1.0, ""),
                content=result.content,
                model_used=cfg["model"],
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            # Fall back to medium on error
            logger.warning("Tier 1 failed: %s — falling back to Tier 2", e)
            return await self._route_medium(messages, t0)

    async def _route_medium(
        self, messages: List[Dict[str, str]], t0: float
    ) -> RouteResult:
        """Tier 2: single remote medium model."""
        cfg = TIER_MODELS[Tier.MEDIUM]
        try:
            result = await self.registry.call(
                provider_name=cfg["provider"],
                model=cfg["model"],
                messages=messages,
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
            )
            return RouteResult(
                tier=Tier.MEDIUM,
                classification=Classification(Tier.MEDIUM, 1.0, ""),
                content=result.content,
                model_used=cfg["model"],
                latency_ms=(time.monotonic() - t0) * 1000,
            )
        except Exception as e:
            # Fall back to local
            logger.warning("Tier 2 failed: %s — falling back to local", e)
            return await self._route_simple(messages, t0)

    async def _route_complex(
        self, messages: List[Dict[str, str]], classification: Classification, t0: float
    ) -> RouteResult:
        """Tier 3: multi-model debate + voting."""
        from .debater import DebateEngine
        engine = DebateEngine(self.registry)
        try:
            result = await engine.debate(messages, TIER_MODELS[Tier.COMPLEX])
            return RouteResult(
                tier=Tier.COMPLEX,
                classification=classification,
                content=result["final_answer"],
                model_used="ensemble(deepseek-v4-pro+qwen3.7-max+glm-5.2)",
                latency_ms=(time.monotonic() - t0) * 1000,
                debate_verdict=result.get("verdict"),
            )
        except Exception as e:
            # Fall back to medium on complete failure
            logger.warning("Tier 3 failed: %s — falling back to Tier 2", e)
            return await self._route_medium(messages, t0)

    @staticmethod
    def _extract_user_text(messages: List[Dict[str, str]]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""
