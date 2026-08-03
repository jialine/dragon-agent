"""
Dragon Orchestrator — 3-tier intelligent routing.

Tier 1 (simple)  → local Qwen2.5-1.5B, fast direct answer
Tier 2 (medium)  → remote qwen3.6-flash, single model
Tier 3 (complex) → ≥3 large models debate → vote → synthesized answer
"""

from .classifier import classify, Tier, Classification
from .router import TierRouter, RouteResult
from .debater import DebateEngine

__all__ = [
    "classify", "Tier", "Classification",
    "TierRouter", "RouteResult",
    "DebateEngine",
]
