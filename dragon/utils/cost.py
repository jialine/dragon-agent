"""
Dragon Agent cost optimization module.

Tiered model selection with budget-aware routing, usage tracking, and
trivial query detection to minimize API spend while maintaining quality.

Architecture:
  - MODEL_TIERS: 5 tiers from local-free to premium with real API pricing
  - CostOptimizer: per-session optimizer with SQLite-backed daily budgets
  - Trivial query detection skips expensive LLM calls entirely
  - Escalation logic with per-query caps (max 2 escalations)
  - Thread-safe writes via threading.Lock

Usage:
    from dragon.utils.cost import CostOptimizer

    opt = CostOptimizer(daily_budget=1.0)
    model = opt.select_model(difficulty="medium", industry="general")
    opt.record_usage(model, tokens_in=500, tokens_out=200)
    print(opt.budget_remaining)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants — model tiers with real API pricing (as of 2026-05)
# ---------------------------------------------------------------------------

MODEL_TIERS: Dict[str, Dict[str, Any]] = {
    "tier0_local": {
        "label": "Local (free)",
        "models": ["qwen2-1.5b"],
        "price_per_1M_in": 0.0,
        "price_per_1M_out": 0.0,
        "notes": "Runs locally, no API cost",
    },
    "tier1_small": {
        "label": "Small hosted",
        "models": ["qwen3-8b"],
        "price_per_1M_in": 0.07,
        "price_per_1M_out": 0.07,
        "notes": "Cheap hosted inference",
    },
    "tier2_medium": {
        "label": "Medium hosted",
        "models": ["deepseek-chat", "qwen3-14b"],
        "price_per_1M_in": 0.27,
        "price_per_1M_out": 1.10,
        "notes": "Balanced price/performance",
    },
    "tier3_large": {
        "label": "Large / reasoning",
        "models": ["deepseek-reasoner"],
        "price_per_1M_in": 0.55,
        "price_per_1M_out": 2.19,
        "notes": "Complex reasoning tasks",
    },
    "tier4_premium": {
        "label": "Premium",
        "models": ["gpt-4o", "claude-sonnet-4"],
        "price_per_1M_in": 2.50,
        "price_per_1M_out": 10.00,
        "notes": "Highest capability, highest cost",
    },
}

# ---------------------------------------------------------------------------
# Trivial query detection patterns
# ---------------------------------------------------------------------------

# Queries that should skip expensive LLM exploration entirely
_TRIVIAL_GREETINGS: Set[str] = {
    "你好", "您好", "嗨", "哈喽", "hello", "hi", "hey",
    "谢谢", "感谢", "thanks", "thank you",
    "再见", "拜拜", "bye", "goodbye", "see you",
    "好的", "ok", "okay", "嗯", "哦",
}

_TRIVIAL_FACTS: Set[str] = {
    "今天日期", "现在时间", "今天星期几",
    "1+1", "2+2", "1+2",
    "你是谁", "你的名字",
}

_TRIVIAL_PATTERNS: List[re.Pattern] = [
    re.compile(r"^[你好嗨哈噢哦嗯啊诶哎]+[！!！~～。.]?$"),          # pure interjections
    re.compile(r"^\d+\s*[\+\-\*/]\s*\d+\s*[=＝]?\s*$"),            # simple arithmetic
    re.compile(r"^(today|今天|现在|now)(的|是)?(日期|时间|星期|周几)\??$"),  # date/time
]

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_default_db_path() -> str:
    """Return default path for the cost tracking SQLite database."""
    # Resolve relative to the project root (parent of the dragon package)
    this_dir = Path(__file__).resolve().parent  # dragon/utils/
    project_root = this_dir.parent.parent       # dragon-agent/
    data_dir = project_root / "dragon_data"
    return str(data_dir / "cost.db")


def _init_db(db_path: str) -> None:
    """Create the usage table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT    NOT NULL,   -- YYYY-MM-DD UTC
                model     TEXT    NOT NULL,
                tokens_in INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                cost_usd  REAL    NOT NULL DEFAULT 0.0,
                timestamp TEXT    NOT NULL    -- ISO-8601
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_date
            ON usage(date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_usage_model
            ON usage(model)
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helper: find which tier a model belongs to
# ---------------------------------------------------------------------------

def _find_tier_for_model(model: str) -> Optional[str]:
    """Return the tier name (e.g. 'tier2_medium') for a given model, or None."""
    model_lower = model.lower()
    for tier_name, tier_info in MODEL_TIERS.items():
        for m in tier_info["models"]:
            if m.lower() == model_lower:
                return tier_name
    # Fuzzy match: if model contains a tier model name
    for tier_name, tier_info in MODEL_TIERS.items():
        for m in tier_info["models"]:
            if m.lower() in model_lower:
                return tier_name
    return None


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

def _calculate_cost(tokens_in: int, tokens_out: int, tier_name: str) -> float:
    """Calculate USD cost for the given token counts at a specific tier."""
    tier = MODEL_TIERS.get(tier_name)
    if tier is None:
        return 0.0
    cost_in = (tokens_in / 1_000_000) * tier["price_per_1M_in"]
    cost_out = (tokens_out / 1_000_000) * tier["price_per_1M_out"]
    return round(cost_in + cost_out, 8)


def _calculate_cost_for_model(
    tokens_in: int, tokens_out: int, model: str
) -> float:
    """Calculate USD cost by looking up which tier the model belongs to."""
    tier = _find_tier_for_model(model)
    if tier is None:
        return 0.0
    return _calculate_cost(tokens_in, tokens_out, tier)


# ---------------------------------------------------------------------------
# Trivial query detection (module-level for reuse / testing)
# ---------------------------------------------------------------------------

def is_trivial_query(query: str) -> bool:
    """Return True if *query* is a trivial query that should skip LLM exploration.

    Detection rules:
      1. Exact match against known greetings / simple facts
      2. Very short queries (< 5 characters after stripping whitespace)
      3. Regex patterns for arithmetic, interjections, date queries
    """
    if not query:
        return True

    stripped = query.strip().lower()

    # Rule 1: exact match
    if stripped in _TRIVIAL_GREETINGS:
        return True
    if stripped in _TRIVIAL_FACTS:
        return True

    # Rule 2: very short
    if len(stripped) < 5:
        return True

    # Rule 3: regex patterns
    for pat in _TRIVIAL_PATTERNS:
        if pat.search(stripped):
            return True

    return False


# ---------------------------------------------------------------------------
# CostOptimizer
# ---------------------------------------------------------------------------

@dataclass
class UsageEntry:
    """A single usage record."""
    date: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    timestamp: str


class CostOptimizer:
    """Tiered-cost-aware model selector with budget control.

    Manages a daily budget, tracks usage in a local SQLite database,
    and selects the cheapest appropriate model tier for each request.

    Parameters
    ----------
    daily_budget : float
        Maximum USD to spend per calendar day (UTC).  Default: $1.00.
    db_path : str | None
        Path to the SQLite database.  Default: ``dragon_data/cost.db``.
    """

    # Class-level lock for DB writes (shared across all instances)
    _db_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        daily_budget: float = 1.0,
        db_path: Optional[str] = None,
    ) -> None:
        self.daily_budget: float = daily_budget
        self.db_path: str = db_path or _get_default_db_path()

        self._logger = logging.getLogger("dragon.cost")
        self._session_lock = threading.Lock()

        # Per-session escalation tracking: query_id → escalation count
        self._session_escalations: Dict[str, int] = {}

        # Ensure DB exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        _init_db(self.db_path)

        # Load today's spend
        self._today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._spent_today: float = self._load_spent_today()

        self._logger.debug(
            "CostOptimizer ready — budget=$%.4f/day, spent=$%.6f today, db=%s",
            self.daily_budget,
            self._spent_today,
            self.db_path,
        )

    # ------------------------------------------------------------------
    # Budget remaining property
    # ------------------------------------------------------------------

    @property
    def budget_remaining(self) -> float:
        """Remaining budget for today in USD."""
        self._reset_if_new_day()
        return max(0.0, self.daily_budget - self._spent_today)

    # ------------------------------------------------------------------
    # Tier affordability
    # ------------------------------------------------------------------

    def can_afford(self, tier_name: str) -> bool:
        """Check whether the remaining budget can cover a call at *tier_name*.

        Returns True if budget_remaining >= tier's input price for 1M tokens
        (heuristic: at least one call's worth of budget remains).
        """
        tier = MODEL_TIERS.get(tier_name)
        if tier is None:
            return False
        min_cost = tier["price_per_1M_in"] / 1_000_000  # 1 token worth
        return self.budget_remaining >= min_cost

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    def select_model(
        self,
        difficulty: str = "medium",
        industry: str = "general",
        prefer_cheapest: bool = True,
    ) -> str:
        """Select the appropriate model for a query based on difficulty and budget.

        Parameters
        ----------
        difficulty : str
            One of ``"simple"``, ``"medium"``, ``"complex"``.
        industry : str
            Industry domain hint (currently reserved for future use).
        prefer_cheapest : bool
            If True, pick the cheapest model within the assigned tier.

        Returns
        -------
        str
            Model name string, e.g. ``"deepseek-chat"``.
        """
        self._reset_if_new_day()

        difficulty = difficulty.lower()

        if difficulty == "simple":
            tier = "tier1_small"
        elif difficulty == "medium":
            tier = "tier2_medium"
        elif difficulty == "complex":
            # Prefer tier3_large, fall back to tier2 if budget is low
            if self.can_afford("tier3_large"):
                tier = "tier3_large"
            else:
                self._logger.info(
                    "Budget too low for tier3_large, falling back to tier2_medium"
                )
                tier = "tier2_medium"
        else:
            # Unknown difficulty → safe default
            self._logger.warning(
                "Unknown difficulty '%s', defaulting to tier1_small", difficulty
            )
            tier = "tier1_small"

        models = MODEL_TIERS[tier]["models"]
        if prefer_cheapest:
            # pick the first model (assumed cheapest in the tier)
            return models[0]
        else:
            return models[-1]

    # ------------------------------------------------------------------
    # Escalation logic
    # ------------------------------------------------------------------

    def should_escalate(
        self,
        result_confidence: float,
        attempts: int,
        budget_remaining: Optional[float] = None,
        query_id: str = "",
    ) -> bool:
        """Determine whether to escalate to a higher-cost model tier.

        Rules:
          - confidence < 0.5
          - attempts >= 2
          - budget > $0.01
          - Max 2 escalations per query_id in this session

        Parameters
        ----------
        result_confidence : float
            Confidence score of the current result (0.0–1.0).
        attempts : int
            Number of attempts made so far for this query.
        budget_remaining : float, optional
            Override budget check (uses live remaining if None).
        query_id : str
            Session-unique query identifier for tracking escalation count.

        Returns
        -------
        bool
            True if escalation is warranted.
        """
        if budget_remaining is None:
            budget_remaining = self.budget_remaining

        # Core escalation rules
        if result_confidence >= 0.5:
            return False
        if attempts < 2:
            return False
        if budget_remaining <= 0.01:
            return False

        # Per-query cap
        if query_id:
            with self._session_lock:
                count = self._session_escalations.get(query_id, 0)
                if count >= 2:
                    return False
                self._session_escalations[query_id] = count + 1

        return True

    # ------------------------------------------------------------------
    # Trivial query detection (instance wrapper)
    # ------------------------------------------------------------------

    def should_skip_exploration(self, query: str) -> bool:
        """Return True if *query* is trivial and should skip expensive LLM calls.

        Delegates to the module-level :func:`is_trivial_query`.
        """
        return is_trivial_query(query)

    # ------------------------------------------------------------------
    # Usage recording
    # ------------------------------------------------------------------

    def record_usage(
        self,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> float:
        """Record token usage and return the cost in USD.

        Thread-safe: uses a class-level lock for DB writes.

        Parameters
        ----------
        model : str
            Model name used.
        tokens_in : int
            Number of input/prompt tokens.
        tokens_out : int
            Number of output/completion tokens.

        Returns
        -------
        float
            Cost of this call in USD.
        """
        self._reset_if_new_day()

        cost = _calculate_cost_for_model(tokens_in, tokens_out, model)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()

        with CostOptimizer._db_lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT INTO usage (date, model, tokens_in, tokens_out, cost_usd, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (today, model, tokens_in, tokens_out, cost, now_iso),
                )
                conn.commit()
            finally:
                conn.close()

        with self._session_lock:
            self._spent_today += cost

        self._logger.debug(
            "record_usage: model=%s, in=%d, out=%d → $%.6f [today spent $%.4f / $%.2f]",
            model, tokens_in, tokens_out, cost,
            self._spent_today, self.daily_budget,
        )

        return cost

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return cost statistics.

        Returns
        -------
        dict
            Keys: ``today_cost``, ``this_month_cost``, ``total_cost``,
            ``calls_today``, ``budget_remaining``, ``daily_budget``.
        """
        self._reset_if_new_day()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        conn = sqlite3.connect(self.db_path)
        try:
            # Today's cost
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM usage WHERE date = ?",
                (today,),
            ).fetchone()
            today_cost, calls_today = row[0], row[1]

            # This month's cost
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE date LIKE ?",
                (month + "%",),
            ).fetchone()
            month_cost = row[0]

            # All-time cost
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage"
            ).fetchone()
            total_cost = row[0]
        finally:
            conn.close()

        return {
            "today_cost": round(today_cost, 6),
            "this_month_cost": round(month_cost, 6),
            "total_cost": round(total_cost, 6),
            "calls_today": calls_today,
            "budget_remaining": round(self.budget_remaining, 6),
            "daily_budget": self.daily_budget,
        }

    def get_today_usage(self) -> List[UsageEntry]:
        """Return all usage records for today."""
        self._reset_if_new_day()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT date, model, tokens_in, tokens_out, cost_usd, timestamp
                   FROM usage WHERE date = ? ORDER BY timestamp""",
                (today,),
            ).fetchall()
        finally:
            conn.close()

        return [UsageEntry(*row) for row in rows]

    def get_daily_breakdown(self, days: int = 7) -> List[Dict[str, Any]]:
        """Return daily cost totals for the last *days* days."""
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT date, COALESCE(SUM(cost_usd), 0), COUNT(*)
                   FROM usage
                   GROUP BY date
                   ORDER BY date DESC
                   LIMIT ?""",
                (days,),
            ).fetchall()
        finally:
            conn.close()

        return [
            {"date": r[0], "cost_usd": round(r[1], 6), "calls": r[2]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_if_new_day(self) -> None:
        """Reset daily counter if we've crossed midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today:
            self._today = today
            self._spent_today = self._load_spent_today()

    def _load_spent_today(self) -> float:
        """Query today's spend from the database."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE date = ?",
                (self._today,),
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def __repr__(self) -> str:
        return (
            f"CostOptimizer(budget=${self.daily_budget:.2f}/day, "
            f"spent=${self._spent_today:.6f}, "
            f"remaining=${self.budget_remaining:.6f})"
        )


# ---------------------------------------------------------------------------
# Convenience: singleton / default instance
# ---------------------------------------------------------------------------

_default_optimizer: Optional[CostOptimizer] = None
_default_lock = threading.Lock()


def get_cost_optimizer(
    daily_budget: float = 1.0,
    db_path: Optional[str] = None,
) -> CostOptimizer:
    """Return a process-wide singleton CostOptimizer instance.

    Thread-safe lazy initialization.
    """
    global _default_optimizer
    if _default_optimizer is None:
        with _default_lock:
            if _default_optimizer is None:
                _default_optimizer = CostOptimizer(
                    daily_budget=daily_budget,
                    db_path=db_path,
                )
    return _default_optimizer
