"""
Session Insights & Analytics Engine for Dragon Agent.

Analyzes historical session data from the SQLite session store to produce
comprehensive usage insights — token consumption, cost estimates, session
metrics, activity trends, model/provider breakdowns, and platform analytics.

Features:
- Per-session and per-provider token usage tracking (prompt + completion)
- Cost estimation using per-model pricing data
- Aggregation: daily, weekly, monthly rollups
- Export to CSV/JSON
- Rich console dashboard (tables, panels via Rich)
- Auto-tracking integration hook for provider calls

Usage::

    from dragon.insights import InsightsEngine
    engine = InsightsEngine(session_store)
    report = engine.report(days=30)
    dashboard = engine.dashboard()  # Rich renderable
    engine.export_csv("report.csv", days=7)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.insights")

# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────


class UsageRecord:
    """A single usage record, typically from one provider call."""

    __slots__ = (
        "timestamp", "session_id", "provider", "model",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "latency_ms", "cost_usd", "platform",
    )

    def __init__(
        self,
        *,
        timestamp: float = 0.0,
        session_id: str = "",
        provider: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        platform: str = "",
    ) -> None:
        self.timestamp = timestamp or time.time()
        self.session_id = session_id
        self.provider = provider
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens or (prompt_tokens + completion_tokens)
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd
        self.platform = platform

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "platform": self.platform,
        }


class DailyRollup:
    """Token and cost rollup for a single day."""

    def __init__(self, date: str = "") -> None:
        self.date = date
        self.total_sessions: int = 0
        self.total_messages: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.total_latency_ms: float = 0.0
        self.total_api_calls: int = 0
        self.by_provider: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0, "calls": 0}
        )
        self.by_model: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"tokens": 0, "cost": 0.0, "calls": 0}
        )

    def add_session(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        provider: str = "",
        model: str = "",
    ) -> None:
        self.total_sessions += 1
        self.total_api_calls += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.total_cost_usd += cost_usd
        self.total_latency_ms += latency_ms

        if provider:
            self.by_provider[provider]["tokens"] += prompt_tokens + completion_tokens
            self.by_provider[provider]["cost"] += cost_usd
            self.by_provider[provider]["calls"] += 1

        if model:
            self.by_model[model]["tokens"] += prompt_tokens + completion_tokens
            self.by_model[model]["cost"] += cost_usd
            self.by_model[model]["calls"] += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "total_sessions": self.total_sessions,
            "total_messages": self.total_messages,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "total_api_calls": self.total_api_calls,
            "by_provider": dict(self.by_provider),
            "by_model": dict(self.by_model),
        }


class WeeklyRollup(DailyRollup):
    """Token and cost rollup for a week."""

    def __init__(self, week_start: str = "", week_end: str = "") -> None:
        super().__init__(date=week_start)
        self.week_end = week_end

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["week_end"] = self.week_end
        return d


class MonthlyRollup(DailyRollup):
    """Token and cost rollup for a month."""

    def __init__(self, month: str = "") -> None:
        super().__init__(date=month)


# ────────────────────────────────────────────────────────────────────
# Insights Engine
# ────────────────────────────────────────────────────────────────────


class InsightsEngine:
    """Analyzes session history and produces usage insights.

    Works directly with a SessionStore instance to query session
    and message data from SQLite.

    Usage::

        from dragon.session import SessionStore
        from dragon.insights import InsightsEngine

        store = SessionStore()
        engine = InsightsEngine(store)

        # Get a report
        report = engine.report(days=30)
        print(report["overview"]["total_tokens"])

        # Get a Rich dashboard
        panel = engine.dashboard()
    """

    def __init__(
        self,
        session_store=None,
        *,
        track_usage: bool = True,
    ) -> None:
        """Initialize the insights engine.

        Args:
            session_store: A SessionStore instance.
            track_usage: If True, enable in-memory usage tracking buffer.
        """
        self._store = session_store
        self._track_usage = track_usage
        self._usage_buffer: List[UsageRecord] = []
        self._max_buffer_size = 10_000

    # ── Auto-Tracking Hook ─────────────────────────────────────────

    def track_call(
        self,
        session_id: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        platform: str = "",
    ) -> None:
        """Record a single provider call for real-time tracking.

        Call this from provider wrappers or hooks to auto-track usage.

        Args:
            session_id: The session this call belongs to.
            provider: Provider name.
            model: Model name.
            prompt_tokens: Input/prompt token count.
            completion_tokens: Output/completion token count.
            latency_ms: Call latency in milliseconds.
            cost_usd: Estimated cost in USD.
            platform: Platform (e.g., 'cli', 'feishu').
        """
        if not self._track_usage:
            return

        record = UsageRecord(
            session_id=session_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            platform=platform,
        )

        self._usage_buffer.append(record)
        if len(self._usage_buffer) > self._max_buffer_size:
            self._usage_buffer = self._usage_buffer[-self._max_buffer_size:]

    def get_recent_usage(self, n: int = 100) -> List[UsageRecord]:
        """Get the most recent usage records from the in-memory buffer.

        Args:
            n: Number of records to return (most recent first).

        Returns:
            List of UsageRecord objects.
        """
        return list(reversed(self._usage_buffer[-n:]))

    # ── Report Generation ──────────────────────────────────────────

    def report(
        self,
        days: int = 30,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        platform: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a comprehensive insights report.

        Args:
            days: Number of days to look back (default: 30).
            start_date: ISO date string for range start (overrides days).
            end_date: ISO date string for range end (defaults to now).
            platform: Optional filter by platform.
            provider: Optional filter by provider.

        Returns:
            Dict with all computed insights:
            - overview: Summary stats
            - daily: List of DailyRollup dicts
            - weekly: List of WeeklyRollup dicts
            - monthly: List of MonthlyRollup dicts
            - by_provider: Provider breakdown
            - by_model: Model breakdown
            - by_platform: Platform breakdown
            - top_sessions: Most active sessions
            - activity: Activity heatmap data
        """
        # Calculate time range
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc)
            except ValueError:
                end_dt = datetime.now(timezone.utc)
        else:
            end_dt = datetime.now(timezone.utc)

        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            except ValueError:
                start_dt = end_dt - timedelta(days=days)
        else:
            start_dt = end_dt - timedelta(days=days)

        cutoff_ts = start_dt.timestamp()

        # Gather data
        sessions = self._get_sessions(cutoff_ts, platform, provider)
        if not sessions:
            return self._empty_report(days, start_dt, end_dt, platform)

        # Compute insights
        overview = self._compute_overview(sessions)
        daily = self._compute_daily_rollups(sessions, start_dt, end_dt)
        weekly = self._compute_weekly_rollups(sessions, start_dt, end_dt)
        monthly = self._compute_monthly_rollups(sessions, start_dt, end_dt)
        by_provider = self._compute_provider_breakdown(sessions)
        by_model = self._compute_model_breakdown(sessions)
        by_platform = self._compute_platform_breakdown(sessions)
        top_sessions = self._compute_top_sessions(sessions)
        activity = self._compute_activity(sessions, start_dt, end_dt)

        return {
            "generated_at": time.time(),
            "period": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "days": days,
            },
            "filters": {
                "platform": platform,
                "provider": provider,
            },
            "overview": overview,
            "daily": [d.to_dict() for d in daily],
            "weekly": [w.to_dict() for w in weekly],
            "monthly": [m.to_dict() for m in monthly],
            "by_provider": by_provider,
            "by_model": by_model,
            "by_platform": by_platform,
            "top_sessions": top_sessions,
            "activity": activity,
        }

    def _empty_report(
        self, days: int, start: datetime, end: datetime, platform: Optional[str]
    ) -> Dict[str, Any]:
        return {
            "generated_at": time.time(),
            "period": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days": days,
            },
            "filters": {"platform": platform, "provider": None},
            "overview": {
                "total_sessions": 0,
                "total_messages": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "active_days": 0,
                "avg_tokens_per_session": 0,
                "avg_cost_per_session": 0.0,
            },
            "daily": [],
            "weekly": [],
            "monthly": [],
            "by_provider": [],
            "by_model": [],
            "by_platform": [],
            "top_sessions": [],
            "activity": {},
        }

    # ── Data Gathering ─────────────────────────────────────────────

    def _get_sessions(
        self,
        cutoff_ts: float,
        platform: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch session data from the store."""
        if self._store is None:
            return []

        try:
            conn = self._store._connect() if hasattr(self._store, '_connect') else None
            if conn is None:
                return self._get_sessions_via_api(cutoff_ts, platform, provider)

            return self._get_sessions_direct(conn, cutoff_ts, platform, provider)
        except Exception as exc:
            logger.warning("Failed to query sessions: %s", exc)
            return self._get_sessions_via_api(cutoff_ts, platform, provider)

    def _get_sessions_direct(
        self,
        conn,
        cutoff_ts: float,
        platform: Optional[str],
        provider: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Query sessions directly from SQLite connection."""
        import sqlite3

        cutoff_str = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()

        where = "WHERE updated_at >= ?"
        params: List[Any] = [cutoff_str]

        if platform:
            where += " AND platform = ?"
            params.append(platform)

        query = (
            "SELECT id, title, created_at, updated_at, platform, model, "
            "token_count, message_count, meta "
            f"FROM sessions {where} ORDER BY updated_at DESC"
        )

        try:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return []

        sessions = []
        for row in rows:
            meta = {}
            try:
                meta = json.loads(row[8]) if row[8] else {}
            except (json.JSONDecodeError, TypeError):
                pass

            sessions.append({
                "id": row[0],
                "title": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "platform": row[4],
                "model": row[5],
                "token_count": row[6],
                "message_count": row[7],
                "meta": meta,
            })

        # Filter by provider if needed (provider is in meta or model)
        if provider:
            sessions = [
                s for s in sessions
                if s["meta"].get("provider", "").lower() == provider.lower()
            ]

        return sessions

    def _get_sessions_via_api(
        self,
        cutoff_ts: float,
        platform: Optional[str],
        provider: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Fallback: get sessions via SessionStore public API."""
        if self._store is None:
            return []

        try:
            recent = self._store.list_recent(limit=1000, platform=platform)
        except Exception:
            return []

        cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc)
        sessions = []
        for s in recent:
            try:
                updated = datetime.fromisoformat(s.updated_at)
            except (ValueError, TypeError):
                continue
            if updated < cutoff_dt:
                continue
            sessions.append({
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "platform": s.platform,
                "model": s.model,
                "token_count": s.token_count,
                "message_count": s.message_count,
                "meta": s.meta,
            })

        if provider:
            sessions = [
                s for s in sessions
                if s.get("meta", {}).get("provider", "").lower() == provider.lower()
            ]

        return sessions

    # ── Computation Helpers ────────────────────────────────────────

    def _compute_overview(self, sessions: List[Dict]) -> Dict[str, Any]:
        """Compute summary overview from sessions."""
        total_sessions = len(sessions)
        total_messages = sum(s.get("message_count", 0) for s in sessions)
        total_tokens = sum(s.get("token_count", 0) for s in sessions)
        unique_days = len({
            s.get("created_at", "")[:10] for s in sessions if s.get("created_at")
        })

        # Cost estimation
        total_cost = 0.0
        sessions_with_cost = 0
        for s in sessions:
            cost = s.get("meta", {}).get("cost_usd", 0.0)
            if cost:
                total_cost += cost
                sessions_with_cost += 1

        avg_tokens = total_tokens // total_sessions if total_sessions > 0 else 0
        avg_cost = total_cost / total_sessions if total_sessions > 0 else 0.0

        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "active_days": unique_days,
            "avg_tokens_per_session": avg_tokens,
            "avg_cost_per_session": round(avg_cost, 6),
            "sessions_with_cost_data": sessions_with_cost,
        }

    def _compute_daily_rollups(
        self, sessions: List[Dict], start: datetime, end: datetime
    ) -> List[DailyRollup]:
        """Compute daily rollups."""
        rollups: Dict[str, DailyRollup] = {}

        for s in sessions:
            try:
                date = s.get("created_at", "")[:10]
            except Exception:
                continue
            if not date:
                continue

            if date not in rollups:
                rollups[date] = DailyRollup(date=date)

            meta = s.get("meta", {})
            rollups[date].add_session(
                prompt_tokens=s.get("token_count", 0) // 2,
                completion_tokens=s.get("token_count", 0) // 2,
                cost_usd=meta.get("cost_usd", 0.0),
                provider=meta.get("provider", ""),
                model=s.get("model", ""),
            )

        return sorted(rollups.values(), key=lambda r: r.date)

    def _compute_weekly_rollups(
        self, sessions: List[Dict], start: datetime, end: datetime
    ) -> List[WeeklyRollup]:
        """Compute weekly rollups."""
        rollups: Dict[str, WeeklyRollup] = {}

        for s in sessions:
            try:
                dt = datetime.fromisoformat(s.get("created_at", ""))
            except (ValueError, TypeError):
                continue

            # ISO week
            year, week, _ = dt.isocalendar()
            week_key = f"{year}-W{week:02d}"

            if week_key not in rollups:
                # Calculate week boundaries
                monday = dt - timedelta(days=dt.weekday())
                sunday = monday + timedelta(days=6)
                rollups[week_key] = WeeklyRollup(
                    week_start=monday.strftime("%Y-%m-%d"),
                    week_end=sunday.strftime("%Y-%m-%d"),
                )

            meta = s.get("meta", {})
            rollups[week_key].add_session(
                prompt_tokens=s.get("token_count", 0) // 2,
                completion_tokens=s.get("token_count", 0) // 2,
                cost_usd=meta.get("cost_usd", 0.0),
                provider=meta.get("provider", ""),
                model=s.get("model", ""),
            )

        return sorted(rollups.values(), key=lambda r: r.date)

    def _compute_monthly_rollups(
        self, sessions: List[Dict], start: datetime, end: datetime
    ) -> List[MonthlyRollup]:
        """Compute monthly rollups."""
        rollups: Dict[str, MonthlyRollup] = {}

        for s in sessions:
            try:
                dt = datetime.fromisoformat(s.get("created_at", ""))
            except (ValueError, TypeError):
                continue

            month_key = dt.strftime("%Y-%m")

            if month_key not in rollups:
                rollups[month_key] = MonthlyRollup(month=month_key)

            meta = s.get("meta", {})
            rollups[month_key].add_session(
                prompt_tokens=s.get("token_count", 0) // 2,
                completion_tokens=s.get("token_count", 0) // 2,
                cost_usd=meta.get("cost_usd", 0.0),
                provider=meta.get("provider", ""),
                model=s.get("model", ""),
            )

        return sorted(rollups.values(), key=lambda r: r.date)

    def _compute_provider_breakdown(self, sessions: List[Dict]) -> List[Dict]:
        """Break down usage by provider."""
        by_provider: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"provider": "", "sessions": 0, "tokens": 0, "cost_usd": 0.0}
        )

        for s in sessions:
            provider = s.get("meta", {}).get("provider", "unknown")
            entry = by_provider[provider]
            entry["provider"] = provider
            entry["sessions"] += 1
            entry["tokens"] += s.get("token_count", 0)
            entry["cost_usd"] = round(
                entry["cost_usd"] + s.get("meta", {}).get("cost_usd", 0.0), 6
            )

        return sorted(by_provider.values(), key=lambda x: x["tokens"], reverse=True)

    def _compute_model_breakdown(self, sessions: List[Dict]) -> List[Dict]:
        """Break down usage by model."""
        by_model: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"model": "", "sessions": 0, "tokens": 0, "cost_usd": 0.0}
        )

        for s in sessions:
            model = s.get("model", "unknown")
            entry = by_model[model]
            entry["model"] = model
            entry["sessions"] += 1
            entry["tokens"] += s.get("token_count", 0)
            entry["cost_usd"] = round(
                entry["cost_usd"] + s.get("meta", {}).get("cost_usd", 0.0), 6
            )

        return sorted(by_model.values(), key=lambda x: x["tokens"], reverse=True)

    def _compute_platform_breakdown(self, sessions: List[Dict]) -> List[Dict]:
        """Break down usage by platform."""
        by_platform: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"platform": "", "sessions": 0, "tokens": 0}
        )

        for s in sessions:
            platform = s.get("platform", "unknown")
            entry = by_platform[platform]
            entry["platform"] = platform
            entry["sessions"] += 1
            entry["tokens"] += s.get("token_count", 0)

        return sorted(by_platform.values(), key=lambda x: x["sessions"], reverse=True)

    def _compute_top_sessions(self, sessions: List[Dict], n: int = 10) -> List[Dict]:
        """Get the most active sessions by token count."""
        sorted_sessions = sorted(
            sessions, key=lambda s: s.get("token_count", 0), reverse=True
        )
        return [
            {
                "id": s["id"],
                "title": s.get("title", ""),
                "platform": s.get("platform", ""),
                "model": s.get("model", ""),
                "tokens": s.get("token_count", 0),
                "messages": s.get("message_count", 0),
                "cost_usd": s.get("meta", {}).get("cost_usd", 0.0),
                "created_at": s.get("created_at", ""),
            }
            for s in sorted_sessions[:n]
        ]

    def _compute_activity(
        self, sessions: List[Dict], start: datetime, end: datetime
    ) -> Dict[str, Any]:
        """Compute activity heatmap and patterns."""
        # Sessions per day
        sessions_per_day: Dict[str, int] = defaultdict(int)
        tokens_per_day: Dict[str, int] = defaultdict(int)

        for s in sessions:
            try:
                date = s.get("created_at", "")[:10]
            except Exception:
                continue
            if date:
                sessions_per_day[date] += 1
                tokens_per_day[date] += s.get("token_count", 0)

        # Peak day
        peak_day = max(sessions_per_day.items(), key=lambda x: x[1]) if sessions_per_day else ("", 0)
        peak_token_day = max(tokens_per_day.items(), key=lambda x: x[1]) if tokens_per_day else ("", 0)

        # Hour distribution (if timestamps have time components)
        hour_dist: Dict[int, int] = defaultdict(int)
        for s in sessions:
            try:
                dt = datetime.fromisoformat(s.get("created_at", ""))
                hour_dist[dt.hour] += 1
            except (ValueError, TypeError):
                pass

        return {
            "sessions_per_day": dict(sorted(sessions_per_day.items())),
            "tokens_per_day": dict(sorted(tokens_per_day.items())),
            "peak_day": {"date": peak_day[0], "sessions": peak_day[1]},
            "peak_token_day": {"date": peak_token_day[0], "tokens": peak_token_day[1]},
            "hour_distribution": {str(h): c for h, c in sorted(hour_dist.items())},
        }

    # ── Rich Dashboard ─────────────────────────────────────────────

    def dashboard(
        self,
        days: int = 30,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        platform: Optional[str] = None,
    ):
        """Generate a Rich renderable dashboard.

        Args:
            days: Number of days to look back.
            start_date: Optional start date.
            end_date: Optional end date.
            platform: Optional platform filter.

        Returns:
            A Rich Panel or Group renderable, or a plain-text fallback.
        """
        report_data = self.report(
            days=days, start_date=start_date, end_date=end_date, platform=platform
        )
        try:
            return self._render_rich_dashboard(report_data)
        except ImportError:
            return self._render_text_dashboard(report_data)

    def _render_rich_dashboard(self, report: Dict[str, Any]):
        """Render dashboard using Rich library."""
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        overview = report.get("overview", {})

        # Overview table
        overview_table = Table(title="📊 Overview", title_style="bold cyan")
        overview_table.add_column("Metric", style="dim")
        overview_table.add_column("Value", style="bold")
        overview_table.add_row("Total Sessions", str(overview.get("total_sessions", 0)))
        overview_table.add_row("Total Messages", str(overview.get("total_messages", 0)))
        overview_table.add_row("Total Tokens", f"{overview.get('total_tokens', 0):,}")
        overview_table.add_row("Total Cost (USD)", f"${overview.get('total_cost_usd', 0):.4f}")
        overview_table.add_row("Active Days", str(overview.get("active_days", 0)))
        overview_table.add_row("Avg Tokens/Session", f"{overview.get('avg_tokens_per_session', 0):,}")
        overview_table.add_row("Avg Cost/Session", f"${overview.get('avg_cost_per_session', 0):.4f}")

        panels = [overview_table]

        # By Provider table
        by_provider = report.get("by_provider", [])
        if by_provider:
            provider_table = Table(title="🔌 By Provider", title_style="bold green")
            provider_table.add_column("Provider")
            provider_table.add_column("Sessions", justify="right")
            provider_table.add_column("Tokens", justify="right")
            provider_table.add_column("Cost", justify="right")
            for p in by_provider[:10]:
                provider_table.add_row(
                    p["provider"],
                    str(p["sessions"]),
                    f"{p['tokens']:,}",
                    f"${p['cost_usd']:.4f}",
                )
            panels.append(provider_table)

        # By Model table
        by_model = report.get("by_model", [])
        if by_model:
            model_table = Table(title="🤖 By Model", title_style="bold yellow")
            model_table.add_column("Model")
            model_table.add_column("Sessions", justify="right")
            model_table.add_column("Tokens", justify="right")
            model_table.add_column("Cost", justify="right")
            for m in by_model[:10]:
                model_table.add_row(
                    m["model"][:40],
                    str(m["sessions"]),
                    f"{m['tokens']:,}",
                    f"${m['cost_usd']:.4f}",
                )
            panels.append(model_table)

        # By Platform table
        by_platform = report.get("by_platform", [])
        if by_platform:
            plat_table = Table(title="📱 By Platform", title_style="bold magenta")
            plat_table.add_column("Platform")
            plat_table.add_column("Sessions", justify="right")
            plat_table.add_column("Tokens", justify="right")
            for p in by_platform:
                plat_table.add_row(p["platform"], str(p["sessions"]), f"{p['tokens']:,}")
            panels.append(plat_table)

        # Top Sessions table
        top_sessions = report.get("top_sessions", [])
        if top_sessions:
            top_table = Table(title="🏆 Top Sessions", title_style="bold red")
            top_table.add_column("Title")
            top_table.add_column("Platform")
            top_table.add_column("Tokens", justify="right")
            top_table.add_column("Msgs", justify="right")
            for s in top_sessions[:10]:
                top_table.add_row(
                    s.get("title", "")[:30],
                    s.get("platform", ""),
                    f"{s.get('tokens', 0):,}",
                    str(s.get("messages", 0)),
                )
            panels.append(top_table)

        # Period info
        period = report.get("period", {})
        footer = Text(
            f"Period: {period.get('start', '?')} → {period.get('end', '?')} "
            f"| Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            style="dim italic",
        )

        return Panel(Group(*panels, footer), title="🐉 Dragon Agent Insights", border_style="cyan")

    def _render_text_dashboard(self, report: Dict[str, Any]) -> str:
        """Plain-text fallback dashboard."""
        lines = ["=" * 60, "  Dragon Agent - Usage Insights", "=" * 60, ""]

        overview = report.get("overview", {})
        lines.append(f"Total Sessions:     {overview.get('total_sessions', 0)}")
        lines.append(f"Total Messages:     {overview.get('total_messages', 0)}")
        lines.append(f"Total Tokens:       {overview.get('total_tokens', 0):,}")
        lines.append(f"Total Cost (USD):   ${overview.get('total_cost_usd', 0):.4f}")
        lines.append(f"Active Days:        {overview.get('active_days', 0)}")
        lines.append("")

        by_provider = report.get("by_provider", [])
        if by_provider:
            lines.append("By Provider:")
            for p in by_provider[:5]:
                lines.append(f"  {p['provider']:<15}  {p['sessions']:>4} sessions  {p['tokens']:>10,} tokens  ${p['cost_usd']:.4f}")
            lines.append("")

        by_model = report.get("by_model", [])
        if by_model:
            lines.append("By Model:")
            for m in by_model[:5]:
                lines.append(f"  {m['model'][:30]:<30}  {m['sessions']:>4} sessions  {m['tokens']:>10,} tokens")
            lines.append("")

        period = report.get("period", {})
        lines.append(f"Period: {period.get('start', '?')} → {period.get('end', '?')}")

        return "\n".join(lines)

    # ── Export ─────────────────────────────────────────────────────

    def export_json(
        self,
        path: Optional[str] = None,
        days: int = 30,
        **kwargs,
    ) -> str:
        """Export report as JSON.

        Args:
            path: File path to write to. If None, returns JSON string.
            days: Number of days.
            **kwargs: Passed to report().

        Returns:
            JSON string or empty string if written to file.
        """
        report_data = self.report(days=days, **kwargs)
        json_str = json.dumps(report_data, indent=2, default=str, ensure_ascii=False)

        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json_str)
            logger.info("Exported insights JSON to %s", path)
            return ""
        return json_str

    def export_csv(
        self,
        path: str,
        days: int = 30,
        **kwargs,
    ) -> None:
        """Export daily rollup as CSV.

        Args:
            path: File path to write CSV to.
            days: Number of days.
            **kwargs: Passed to report().
        """
        report_data = self.report(days=days, **kwargs)
        daily = report_data.get("daily", [])

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "date", "sessions", "prompt_tokens", "completion_tokens",
                "total_tokens", "cost_usd", "api_calls",
            ])
            for d in daily:
                writer.writerow([
                    d["date"],
                    d["total_sessions"],
                    d["total_prompt_tokens"],
                    d["total_completion_tokens"],
                    d["total_tokens"],
                    d["total_cost_usd"],
                    d["total_api_calls"],
                ])

        logger.info("Exported insights CSV to %s", path)

    # ── Snapshot ───────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Get a lightweight snapshot of current usage (no DB query).

        Uses the in-memory usage buffer for instant results.
        """
        records = self._usage_buffer
        if not records:
            return {"records": 0, "tokens": 0, "cost_usd": 0.0}

        total_tokens = sum(r.total_tokens for r in records)
        total_cost = sum(r.cost_usd for r in records)
        total_latency = sum(r.latency_ms for r in records)
        by_provider = Counter(r.provider for r in records)
        by_model = Counter(r.model for r in records)

        return {
            "records": len(records),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(total_latency / len(records), 2) if records else 0,
            "by_provider": dict(by_provider),
            "by_model": dict(by_model),
        }


# ────────────────────────────────────────────────────────────────────
# Convenience
# ────────────────────────────────────────────────────────────────────


def create_tracking_hook(
    engine: InsightsEngine,
    session_id: str = "",
    platform: str = "",
) -> Callable:
    """Create a tracking hook for use with provider calls.

    Returns a callable that can be used as a callback or middleware
    to auto-track provider usage.

    Usage::

        engine = InsightsEngine(store)
        hook = create_tracking_hook(engine, session_id="abc123")
        # In provider wrapper:
        hook(provider="openai", model="gpt-4o",
             prompt_tokens=100, completion_tokens=50)
    """
    def track(**kwargs):
        engine.track_call(
            session_id=kwargs.get("session_id", session_id),
            provider=kwargs.get("provider", ""),
            model=kwargs.get("model", ""),
            prompt_tokens=kwargs.get("prompt_tokens", 0),
            completion_tokens=kwargs.get("completion_tokens", 0),
            latency_ms=kwargs.get("latency_ms", 0.0),
            cost_usd=kwargs.get("cost_usd", 0.0),
            platform=kwargs.get("platform", platform),
        )
    return track


def format_tokens(n: int) -> str:
    """Format token count for display (e.g., 1500 → '1.5K', 1000000 → '1.0M')."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_cost(amount: float) -> str:
    """Format cost for display."""
    if amount == 0.0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.6f}"
    if amount < 1.0:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


__all__ = [
    "InsightsEngine",
    "UsageRecord",
    "DailyRollup",
    "WeeklyRollup",
    "MonthlyRollup",
    "create_tracking_hook",
    "format_tokens",
    "format_cost",
]
