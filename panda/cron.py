"""
Panda Cron — Lightweight task scheduler for recurring jobs.

Supports:
- Interval schedules ("30m", "2h", "every 6 hours")
- Cron expressions ("0 9 * * *")
- One-shot scheduled tasks (ISO timestamp)
- Job persistence (SQLite)
- Background execution with asyncio

Inspired by Hermes Agent's cron system, but simpler.

Usage::

    from panda.cron import CronScheduler

    scheduler = CronScheduler(db_path="panda_data/cron.db")
    scheduler.start()

    scheduler.add(
        name="daily-report",
        schedule="0 9 * * *",
        task="Generate daily summary report",
    )

    scheduler.add(
        name="health-check",
        schedule="30m",
        task="Check all services are up",
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from croniter import croniter
    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False

logger = logging.getLogger("panda.cron")


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

class JobStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CronJob:
    id: str = ""
    name: str = ""
    schedule: str = ""         # "30m", "2h", "0 9 * * *", ISO timestamp
    task: str = ""             # description or prompt
    status: str = "active"     # active, paused, completed, failed
    last_run_at: str = ""
    next_run_at: str = ""
    run_count: int = 0
    max_runs: int = 0          # 0 = unlimited
    created_at: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_due(self) -> bool:
        if self.status != "active":
            return False
        if not self.next_run_at:
            return True
        return datetime.now(timezone.utc).isoformat() >= self.next_run_at

    def compute_next_run(self, from_time: Optional[datetime] = None) -> Optional[str]:
        """Compute the next run time from the schedule."""
        from_time = from_time or datetime.now(timezone.utc)

        # Parse interval: "30m", "2h", "every 6 hours"
        import re
        interval_match = re.match(r'^(?:every\s+)?(\d+)\s*(m|min|h|hour|d|day)s?$', self.schedule.lower())
        if interval_match:
            value = int(interval_match.group(1))
            unit = interval_match.group(2)
            if unit in ('m', 'min'):
                delta_seconds = value * 60
            elif unit in ('h', 'hour'):
                delta_seconds = value * 3600
            elif unit in ('d', 'day'):
                delta_seconds = value * 86400
            else:
                return None
            next_time = from_time.timestamp() + delta_seconds
            return datetime.fromtimestamp(next_time, tz=timezone.utc).isoformat()

        # Parse cron expression: "0 9 * * *"
        if HAS_CRONITER and re.match(r'^[\d*,/\-]+\s+[\d*,/\-]+\s+[\d*,/\-]+\s+[\d*,/\-]+\s+[\d*,/\-]+$', self.schedule.strip()):
            try:
                cron = croniter(self.schedule, from_time)
                return cron.get_next(datetime).isoformat()
            except Exception:
                return None

        # ISO timestamp (one-shot)
        if 'T' in self.schedule and '-' in self.schedule:
            return self.schedule

        return None


# ────────────────────────────────────────────────────────────────────
# Cron Scheduler
# ────────────────────────────────────────────────────────────────────


class CronScheduler:
    """Lightweight job scheduler.

    Parameters
    ----------
    db_path : str
        Path to SQLite database for job persistence.
    tick_interval_secs : float
        How often to check for due jobs (default: 30s).
    """

    def __init__(
        self,
        db_path: str = "panda_data/cron.db",
        tick_interval_secs: float = 30.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.tick_interval = tick_interval_secs

        self._jobs: Dict[str, CronJob] = {}
        self._handlers: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._init_db()
        self._load_jobs()
        logger.info("CronScheduler ready (%d jobs)", len(self._jobs))

    # ── DB Schema ──────────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cron_jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    task TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    last_run_at TEXT NOT NULL DEFAULT '',
                    next_run_at TEXT NOT NULL DEFAULT '',
                    run_count INTEGER NOT NULL DEFAULT 0,
                    max_runs INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    meta TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cron_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    success INTEGER NOT NULL DEFAULT 0,
                    output TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    FOREIGN KEY (job_id) REFERENCES cron_jobs(id)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _load_jobs(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                "SELECT id, name, schedule, task, status, last_run_at, "
                "next_run_at, run_count, max_runs, created_at, meta FROM cron_jobs"
            ).fetchall()
        finally:
            conn.close()

        self._jobs = {}
        for r in rows:
            job = CronJob(
                id=r[0], name=r[1], schedule=r[2], task=r[3], status=r[4],
                last_run_at=r[5], next_run_at=r[6], run_count=r[7],
                max_runs=r[8], created_at=r[9], meta=json.loads(r[10]),
            )
            self._jobs[job.id] = job

    def _save_job(self, job: CronJob) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO cron_jobs
                   (id, name, schedule, task, status, last_run_at, next_run_at,
                    run_count, max_runs, created_at, meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job.id, job.name, job.schedule, job.task, job.status,
                 job.last_run_at, job.next_run_at, job.run_count,
                 job.max_runs, job.created_at, json.dumps(job.meta)),
            )
            conn.commit()
        finally:
            conn.close()

    def _save_run(self, job_id: str, success: bool, output: str, error: str) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE cron_runs SET finished_at = ?, success = ?, output = ?, error = ?
                   WHERE job_id = ? AND finished_at IS NULL""",
                (now, int(success), output, error, job_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Job CRUD ───────────────────────────────────────────────────

    def add(
        self,
        name: str,
        schedule: str,
        task: str = "",
        max_runs: int = 0,
        meta: Optional[Dict] = None,
    ) -> CronJob:
        """Add a new cron job."""
        job = CronJob(
            name=name, schedule=schedule, task=task,
            max_runs=max_runs, meta=meta or {},
        )
        job.next_run_at = job.compute_next_run()

        with self._lock:
            self._jobs[job.id] = job
            self._save_job(job)

        logger.info("Added cron job: %s [%s] → %s", job.name, job.schedule, job.next_run_at)
        return job

    def get(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)

    def list_jobs(self, status: Optional[str] = None) -> List[CronJob]:
        jobs = list(self._jobs.values())
        if status:
            jobs = [j for j in jobs if j.status == status]
        return sorted(jobs, key=lambda j: j.created_at)

    def pause(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.PAUSED.value
            self._save_job(job)
            return True
        return False

    def resume(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.status = JobStatus.ACTIVE.value
            job.next_run_at = job.compute_next_run()
            self._save_job(job)
            return True
        return False

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                conn = sqlite3.connect(str(self.db_path))
                try:
                    conn.execute("DELETE FROM cron_jobs WHERE id = ?", (job_id,))
                    conn.execute("DELETE FROM cron_runs WHERE job_id = ?", (job_id,))
                    conn.commit()
                finally:
                    conn.close()
                return True
        return False

    def run_now(self, job_id: str) -> bool:
        """Trigger a job immediately."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        asyncio.create_task(self._execute_job(job))
        return True

    # ── Handler Registration ──────────────────────────────────────

    def register_handler(self, pattern: str, handler: Callable) -> None:
        """Register a handler for jobs matching a name pattern.

        Handler signature: async def handler(job: CronJob) -> str
        Returns the output string.
        """
        self._handlers[pattern] = handler

    # ── Scheduler Loop ────────────────────────────────────────────

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return

        self._running = True
        logger.info("CronScheduler started (tick=%.0fs)", self.tick_interval)

        while self._running:
            await self._tick()
            await asyncio.sleep(self.tick_interval)

    def start_background(self) -> None:
        """Start the scheduler in the background."""
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self.start())

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("CronScheduler stopped")

    async def _tick(self) -> None:
        """Check for due jobs and execute them."""
        due_jobs = []

        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            for job in self._jobs.values():
                if job.is_due:
                    due_jobs.append(job)

        for job in due_jobs:
            asyncio.create_task(self._execute_job(job))

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job."""
        start = time.monotonic()
        run_id = str(int(time.time()))

        # Record run start
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO cron_runs (job_id, started_at) VALUES (?, ?)",
                (job.id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Running job: %s [%s]", job.name, job.id)

        try:
            # Try to find a matching handler
            output = ""
            handler = None
            for pattern, h in self._handlers.items():
                if pattern in job.name:
                    handler = h
                    break

            if handler:
                if asyncio.iscoroutinefunction(handler):
                    output = await handler(job)
                else:
                    output = handler(job)
            else:
                output = f"Job executed: {job.task}"

            # Update job
            with self._lock:
                job.last_run_at = datetime.now(timezone.utc).isoformat()
                job.next_run_at = job.compute_next_run()
                job.run_count += 1

                if job.max_runs > 0 and job.run_count >= job.max_runs:
                    job.status = JobStatus.COMPLETED.value

                self._save_job(job)

            self._save_run(job.id, True, str(output)[:5000], "")
            logger.info("Job completed: %s (%.1fs)", job.name, time.monotonic() - start)

        except Exception as e:
            logger.exception("Job failed: %s", job.name)
            with self._lock:
                job.last_run_at = datetime.now(timezone.utc).isoformat()
                job.next_run_at = job.compute_next_run()
                job.run_count += 1
                self._save_job(job)
            self._save_run(job.id, False, "", str(e))

    def stats(self) -> Dict[str, Any]:
        """Return scheduler statistics."""
        active = sum(1 for j in self._jobs.values() if j.status == "active")
        return {
            "total_jobs": len(self._jobs),
            "active_jobs": active,
            "paused_jobs": sum(1 for j in self._jobs.values() if j.status == "paused"),
            "running": self._running,
            "tick_interval_secs": self.tick_interval,
        }
