"""
Dragon Workflow Store — 小型本地 SQLite 数据库

记录工作流状态、节点任务分配、执行详情。
防止 Agent 跨 Session 失忆。

Usage::

    from dragon.workflow_store import WorkflowStore

    store = WorkflowStore()

    # 开始工作流
    wf_id = store.start_workflow("彩票分析-26071期", {"budget": 800})

    # 分配任务给节点
    t1 = store.assign_task(wf_id, "main", "data_collect", "抓取26070期开奖+天气")
    t2 = store.assign_task(wf_id, "worker-1", "backtest", "回测3种胆码策略")
    t3 = store.assign_task(wf_id, "worker-2", "analysis", "前区热号分析")

    # 更新任务状态
    store.update_task(t1, "done", "26070: 04,05,15,21,32+02,11")
    store.update_task(t2, "running", result="")

    # 记录执行步骤
    store.log_step(t2, "计算中", "14胆方案回溯1356期...")

    # 查询
    store.get_active_workflows()
    store.get_node_tasks("main")

Tables:
  workflow_runs   — 工作流运行记录
  task_nodes      — 节点任务分配
  execution_log   — 执行步骤日志
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════

@dataclass
class WorkflowRun:
    id: str
    name: str
    status: str  # pending | running | done | failed
    context: Dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float = 0.0
    summary: str = ""


@dataclass
class TaskNode:
    id: str
    workflow_run_id: str
    node_id: str
    task_type: str
    task_content: str
    status: str  # pending | running | done | failed
    result: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class ExecutionStep:
    id: str
    task_node_id: str
    step_name: str
    action: str
    output: str = ""
    timestamp: float = 0.0


# ═══════════════════════════════════════════════════════════════
# Store
# ═══════════════════════════════════════════════════════════════

class WorkflowStore:
    """SQLite-backed workflow state store."""

    def __init__(self, db_path: str = "~/.dragon/workflow_store.db") -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    context_json  TEXT DEFAULT '{}',
                    started_at    REAL NOT NULL,
                    updated_at    REAL NOT NULL,
                    finished_at   REAL DEFAULT 0,
                    summary       TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS task_nodes (
                    id              TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id),
                    node_id         TEXT NOT NULL,
                    task_type       TEXT NOT NULL,
                    task_content    TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    result          TEXT DEFAULT '',
                    started_at      REAL DEFAULT 0,
                    finished_at     REAL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS execution_log (
                    id            TEXT PRIMARY KEY,
                    task_node_id  TEXT NOT NULL REFERENCES task_nodes(id),
                    step_name     TEXT NOT NULL,
                    action        TEXT NOT NULL,
                    output        TEXT DEFAULT '',
                    timestamp     REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_wf_status ON workflow_runs(status);
                CREATE INDEX IF NOT EXISTS idx_task_wf ON task_nodes(workflow_run_id);
                CREATE INDEX IF NOT EXISTS idx_task_node ON task_nodes(node_id);
                CREATE INDEX IF NOT EXISTS idx_task_status ON task_nodes(status);
                CREATE INDEX IF NOT EXISTS idx_log_task ON execution_log(task_node_id);
            """)

    # ── Workflow CRUD ─────────────────────────────────────────

    def start_workflow(self, name: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Create a new workflow run. Returns workflow_run_id."""
        wf_id = uuid.uuid4().hex[:12]
        now = time.time()
        ctx_json = json.dumps(context or {}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO workflow_runs (id, name, status, context_json, started_at, updated_at)
                   VALUES (?, ?, 'running', ?, ?, ?)""",
                (wf_id, name, ctx_json, now, now),
            )
        return wf_id

    def update_workflow(self, wf_id: str, status: str = "", summary: str = "") -> None:
        """Update workflow status and/or summary."""
        now = time.time()
        fields = ["updated_at = ?"]
        params: list = [now]
        if status:
            fields.append("status = ?")
            params.append(status)
            if status in ("done", "failed"):
                fields.append("finished_at = ?")
                params.append(now)
        if summary:
            fields.append("summary = ?")
            params.append(summary)
        params.append(wf_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE workflow_runs SET {', '.join(fields)} WHERE id = ?",
                params,
            )

    def get_workflow(self, wf_id: str) -> Optional[WorkflowRun]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (wf_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_wf(row)

    def get_active_workflows(self, limit: int = 10) -> List[WorkflowRun]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs WHERE status IN ('pending','running') "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_wf(r) for r in rows]

    def list_workflows(self, limit: int = 20) -> List[WorkflowRun]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_wf(r) for r in rows]

    @staticmethod
    def _row_to_wf(row: sqlite3.Row) -> WorkflowRun:
        ctx = {}
        try:
            ctx = json.loads(row["context_json"] or "{}")
        except json.JSONDecodeError:
            pass
        return WorkflowRun(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            context=ctx,
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"] or 0,
            summary=row["summary"] or "",
        )

    # ── Task CRUD ─────────────────────────────────────────────

    def assign_task(
        self,
        workflow_run_id: str,
        node_id: str,
        task_type: str,
        task_content: str,
    ) -> str:
        """Assign a task to a node. Returns task_id."""
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO task_nodes
                   (id, workflow_run_id, node_id, task_type, task_content, status, started_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (task_id, workflow_run_id, node_id, task_type, task_content, now),
            )
            conn.execute(
                "UPDATE workflow_runs SET updated_at = ? WHERE id = ?",
                (now, workflow_run_id),
            )
        return task_id

    def update_task(self, task_id: str, status: str, result: str = "") -> None:
        """Update task status and result."""
        now = time.time()
        fields = []
        params: list = []
        if status:
            fields.append("status = ?")
            params.append(status)
            if status in ("done", "failed"):
                fields.append("finished_at = ?")
                params.append(now)
        if result:
            fields.append("result = ?")
            params.append(result)
        params.append(task_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE task_nodes SET {', '.join(fields)} WHERE id = ?", params
            )
            row = conn.execute(
                "SELECT workflow_run_id FROM task_nodes WHERE id = ?", (task_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE workflow_runs SET updated_at = ? WHERE id = ?",
                    (now, row["workflow_run_id"]),
                )

    def get_node_tasks(self, node_id: str, limit: int = 20) -> List[TaskNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM task_nodes WHERE node_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (node_id, limit),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_workflow_tasks(self, wf_id: str) -> List[TaskNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM task_nodes WHERE workflow_run_id = ? "
                "ORDER BY started_at",
                (wf_id,),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get_active_tasks(self, node_id: str = "") -> List[TaskNode]:
        query = "SELECT * FROM task_nodes WHERE status IN ('pending','running')"
        params: tuple = ()
        if node_id:
            query += " AND node_id = ?"
            params = (node_id,)
        query += " ORDER BY started_at DESC LIMIT 50"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskNode:
        return TaskNode(
            id=row["id"],
            workflow_run_id=row["workflow_run_id"],
            node_id=row["node_id"],
            task_type=row["task_type"],
            task_content=row["task_content"],
            status=row["status"],
            result=row["result"] or "",
            started_at=row["started_at"] or 0,
            finished_at=row["finished_at"] or 0,
        )

    # ── Execution Log ─────────────────────────────────────────

    def log_step(
        self,
        task_node_id: str,
        step_name: str,
        action: str,
        output: str = "",
    ) -> str:
        """Record an execution step. Returns log_id."""
        log_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO execution_log (id, task_node_id, step_name, action, output, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (log_id, task_node_id, step_name, action, output[:2000], now),
            )
        return log_id

    def get_task_log(self, task_node_id: str, limit: int = 50) -> List[ExecutionStep]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_log WHERE task_node_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (task_node_id, limit),
            ).fetchall()
        return [self._row_to_log(r) for r in rows]

    def get_recent_logs(self, limit: int = 50) -> List[ExecutionStep]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_log(r) for r in rows]

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> ExecutionStep:
        return ExecutionStep(
            id=row["id"],
            task_node_id=row["task_node_id"],
            step_name=row["step_name"],
            action=row["action"],
            output=row["output"] or "",
            timestamp=row["timestamp"],
        )

    # ── Summary Queries ───────────────────────────────────────

    def get_status_summary(self) -> Dict[str, Any]:
        """One-shot status overview for the agent to recover context."""
        with self._conn() as conn:
            active_wfs = conn.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE status IN ('pending','running')"
            ).fetchone()[0]
            active_tasks = conn.execute(
                "SELECT COUNT(*) FROM task_nodes WHERE status IN ('pending','running')"
            ).fetchone()[0]
            recent_wf = conn.execute(
                "SELECT id, name, status, updated_at FROM workflow_runs "
                "ORDER BY updated_at DESC LIMIT 5"
            ).fetchall()
            recent_tasks = conn.execute(
                "SELECT tn.id, tn.node_id, tn.task_type, tn.task_content, tn.status, wf.name "
                "FROM task_nodes tn JOIN workflow_runs wf ON tn.workflow_run_id = wf.id "
                "ORDER BY tn.started_at DESC LIMIT 5"
            ).fetchall()

        return {
            "active_workflows": active_wfs,
            "active_tasks": active_tasks,
            "recent_workflows": [
                {"id": r["id"], "name": r["name"], "status": r["status"]}
                for r in recent_wf
            ],
            "recent_tasks": [
                {
                    "id": r["id"],
                    "node": r["node_id"],
                    "type": r["task_type"],
                    "content": r["task_content"][:80],
                    "status": r["status"],
                    "workflow": r["name"],
                }
                for r in recent_tasks
            ],
        }

    def close(self) -> None:
        pass  # sqlite3 connections are auto-closed via context manager
