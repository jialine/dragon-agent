'''
Cronjob tool for Dragon Agent — Hermes-aligned.
'''

import json
import logging
from dragon.cron import CronScheduler, CronJob

logger = logging.getLogger("dragon.tool.cronjob")

# Global scheduler instance
_scheduler: CronScheduler = None


def get_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler(db_path="/home/jialine/dragon-agent/dragon_data/cron.db")
        _scheduler.start_background()
        logger.info("Cronjob scheduler started")
    return _scheduler


async def tool_cronjob(
    action: str,
    name: str = "",
    schedule: str = "",
    prompt: str = "",
    job_id: str = "",
    max_runs: int = 0,
) -> str:
    """Manage scheduled cron jobs. Hermes-aligned.

    Use action='create' to schedule a job from a prompt.
    Use action='list' to inspect jobs.
    Use action='update', 'pause', 'resume', 'remove', or 'run' to manage an existing job.

    On create:
    - name: Human-friendly job name
    - schedule: '30m', '2h', '0 9 * * *', or ISO timestamp
    - prompt: The task to execute
    - max_runs: Optional max runs (0 = unlimited)
    """
    scheduler = get_scheduler()

    try:
        if action == "create":
            job = scheduler.add(
                name=name or prompt[:40],
                schedule=schedule,
                task=prompt,
                max_runs=max_runs,
            )
            return json.dumps({
                "status": "created",
                "job_id": job.id,
                "name": job.name,
                "schedule": job.schedule,
                "next_run": job.next_run_at,
            }, ensure_ascii=False)

        elif action == "list":
            jobs = scheduler.list_jobs()
            result = []
            for j in jobs:
                result.append({
                    "id": j.id,
                    "name": j.name,
                    "schedule": j.schedule,
                    "status": j.status,
                    "next_run": j.next_run_at,
                    "run_count": j.run_count,
                })
            return json.dumps({"jobs": result}, ensure_ascii=False)

        elif action == "pause":
            ok = scheduler.pause(job_id)
            return json.dumps({"status": "paused" if ok else "not_found", "job_id": job_id})

        elif action == "resume":
            ok = scheduler.resume(job_id)
            return json.dumps({"status": "resumed" if ok else "not_found", "job_id": job_id})

        elif action == "remove":
            ok = scheduler.remove(job_id)
            return json.dumps({"status": "removed" if ok else "not_found", "job_id": job_id})

        elif action == "run":
            ok = scheduler.run_now(job_id)
            return json.dumps({"status": "triggered" if ok else "not_found", "job_id": job_id})

        elif action == "stats":
            stats = scheduler.stats()
            return json.dumps(stats)

        else:
            return json.dumps({"error": f"Unknown action: {action}. Valid: create, list, pause, resume, remove, run, stats"})

    except Exception as e:
        return json.dumps({"error": str(e)})
