"""
Dragon Agent — Kanban Project Management Tools
==============================================

Built-in tools for Kanban board management with JSON file storage.

Storage: ~/.dragon/kanban/<board_name>.json

Tools:
    - kanban_create_board: Create a new Kanban board
    - kanban_add_task: Add a task to a board
    - kanban_list: List tasks on a board (with optional status filter)
    - kanban_move: Move a task to a new status column
    - kanban_delete_task: Delete a task from a board
    - kanban_list_boards: List all Kanban boards

Status columns: todo, in_progress, review, done
Priority levels: low, medium, high, critical
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dragon.tool.builtins.kanban")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

# Canonical statuses
VALID_STATUSES = frozenset({"todo", "in_progress", "review", "done"})

# Canonical priorities
VALID_PRIORITIES = frozenset({"low", "medium", "high", "critical"})

# Storage directory
STORAGE_DIR = Path.home() / ".dragon" / "kanban"


# ────────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────────


def _ensure_storage_dir() -> Path:
    """Create the kanban storage directory if it doesn't exist."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STORAGE_DIR


def _board_path(name: str) -> Path:
    """Get the JSON file path for a board name."""
    # Sanitise name to a safe filename
    safe = name.strip().replace("/", "_").replace("\\", "_").replace(" ", "_")
    if not safe:
        safe = "untitled"
    return _ensure_storage_dir() / f"{safe}.json"


def _load_board(name: str) -> Optional[Dict[str, Any]]:
    """Load a board from disk. Returns None if not found."""
    path = _board_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load board '%s': %s", name, exc)
        return None


def _save_board(board: Dict[str, Any]) -> None:
    """Persist a board dict to disk."""
    path = _board_path(board["name"])
    board["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_emoji(status: str) -> str:
    return {
        "todo": "📋",
        "in_progress": "🔄",
        "review": "👀",
        "done": "✅",
    }.get(status, "❓")


def _priority_emoji(priority: str) -> str:
    return {
        "low": "🟢",
        "medium": "🟡",
        "high": "🟠",
        "critical": "🔴",
    }.get(priority, "⚪")


# ────────────────────────────────────────────────────────────────────
# Tool implementations
# ────────────────────────────────────────────────────────────────────


async def tool_kanban_create_board(name: str) -> str:
    """Create a new Kanban board.

    Args:
        name: The display name for the board (stored as sanitised filename).

    Returns:
        JSON with board name and creation status.
    """
    if not name or not name.strip():
        return json.dumps({"error": "Board name cannot be empty"})

    name = name.strip()
    path = _board_path(name)

    if path.exists():
        existing = _load_board(name)
        if existing is not None:
            return json.dumps({
                "board": existing["name"],
                "file": str(path),
                "created_at": existing["created_at"],
                "task_count": len(existing.get("tasks", [])),
                "status": "exists",
                "message": f"Board '{name}' already exists",
            })

    board: Dict[str, Any] = {
        "name": name,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "tasks": [],
    }
    _save_board(board)
    logger.info("Created kanban board: %s", name)

    return json.dumps({
        "board": name,
        "file": str(path),
        "created_at": board["created_at"],
        "task_count": 0,
        "status": "created",
        "message": f"Board '{name}' created successfully",
    })


async def tool_kanban_add_task(
    board: str,
    title: str,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
) -> str:
    """Add a task to a Kanban board.

    Args:
        board: Name of the board to add the task to.
        title: The task title (required).
        description: Optional task description.
        status: Initial status column (default: "todo").
        priority: Priority level (default: "medium").

    Returns:
        JSON with task details.
    """
    if not board or not board.strip():
        return json.dumps({"error": "Board name cannot be empty"})
    if not title or not title.strip():
        return json.dumps({"error": "Task title cannot be empty"})

    board_name = board.strip()
    title = title.strip()
    description = (description or "").strip()
    status = status.strip().lower()
    priority = priority.strip().lower()

    # Validate status
    if status not in VALID_STATUSES:
        return json.dumps({
            "error": f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}",
        })

    # Validate priority
    if priority not in VALID_PRIORITIES:
        return json.dumps({
            "error": f"Invalid priority '{priority}'. Must be one of: {sorted(VALID_PRIORITIES)}",
        })

    board_data = _load_board(board_name)
    if board_data is None:
        return json.dumps({"error": f"Board '{board_name}' not found"})

    task_id = str(uuid.uuid4())
    now = _utc_now_iso()
    task: Dict[str, Any] = {
        "id": task_id,
        "title": title,
        "description": description,
        "status": status,
        "priority": priority,
        "created_at": now,
        "updated_at": now,
    }

    board_data.setdefault("tasks", []).append(task)
    _save_board(board_data)
    logger.info("Added task '%s' to board '%s' (id=%s)", title, board_name, task_id)

    return json.dumps({
        "board": board_name,
        "task": task,
        "message": f"Task '{title}' added to board '{board_name}'",
    })


async def tool_kanban_list(board: str, status: str | None = None) -> str:
    """List tasks on a Kanban board, optionally filtered by status.

    Args:
        board: Name of the board.
        status: Optional status filter (e.g., "todo", "in_progress").

    Returns:
        JSON with board name, tasks list, and summary.
    """
    if not board or not board.strip():
        return json.dumps({"error": "Board name cannot be empty"})

    board_name = board.strip()
    board_data = _load_board(board_name)
    if board_data is None:
        return json.dumps({"error": f"Board '{board_name}' not found"})

    tasks = board_data.get("tasks", [])

    if status is not None:
        status = status.strip().lower()
        if status not in VALID_STATUSES:
            return json.dumps({
                "error": f"Invalid status filter '{status}'. Must be one of: {sorted(VALID_STATUSES)}",
            })
        tasks = [t for t in tasks if t["status"] == status]

    # Build status summary counts
    status_counts: Dict[str, int] = {}
    for t in board_data.get("tasks", []):
        s = t["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    # Build a nice summary line per task
    task_summaries = []
    for t in tasks:
        task_summaries.append({
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "status_emoji": _status_emoji(t["status"]),
            "priority": t["priority"],
            "priority_emoji": _priority_emoji(t["priority"]),
            "description": t.get("description", ""),
            "created_at": t["created_at"],
            "updated_at": t["updated_at"],
        })

    return json.dumps({
        "board": board_name,
        "tasks": task_summaries,
        "count": len(task_summaries),
        "total_tasks": len(board_data.get("tasks", [])),
        "status_counts": status_counts,
        "filter": status,
        "created_at": board_data["created_at"],
        "updated_at": board_data["updated_at"],
    })


async def tool_kanban_move(task_id: str, board: str, new_status: str) -> str:
    """Move a task to a new status column.

    Args:
        task_id: The UUID of the task to move.
        board: The board the task belongs to.
        new_status: The target status column (todo, in_progress, review, done).

    Returns:
        JSON with the updated task details.
    """
    if not board or not board.strip():
        return json.dumps({"error": "Board name cannot be empty"})
    if not task_id or not task_id.strip():
        return json.dumps({"error": "Task ID cannot be empty"})
    if not new_status or not new_status.strip():
        return json.dumps({"error": "New status cannot be empty"})

    board_name = board.strip()
    task_id = task_id.strip()
    new_status = new_status.strip().lower()

    if new_status not in VALID_STATUSES:
        return json.dumps({
            "error": f"Invalid status '{new_status}'. Must be one of: {sorted(VALID_STATUSES)}",
        })

    board_data = _load_board(board_name)
    if board_data is None:
        return json.dumps({"error": f"Board '{board_name}' not found"})

    tasks: List[Dict[str, Any]] = board_data.get("tasks", [])
    for task in tasks:
        if task["id"] == task_id:
            old_status = task["status"]
            task["status"] = new_status
            task["updated_at"] = _utc_now_iso()
            _save_board(board_data)
            logger.info(
                "Moved task '%s' from '%s' to '%s' on board '%s'",
                task["title"], old_status, new_status, board_name,
            )
            return json.dumps({
                "board": board_name,
                "task": task,
                "old_status": old_status,
                "new_status": new_status,
                "message": (
                    f"Task '{task['title']}' moved from "
                    f"{_status_emoji(old_status)} {old_status} → "
                    f"{_status_emoji(new_status)} {new_status}"
                ),
            })

    return json.dumps({"error": f"Task '{task_id}' not found on board '{board_name}'"})


async def tool_kanban_delete_task(task_id: str, board: str) -> str:
    """Delete a task from a Kanban board.

    Args:
        task_id: The UUID of the task to delete.
        board: The board the task belongs to.

    Returns:
        JSON with deletion status.
    """
    if not board or not board.strip():
        return json.dumps({"error": "Board name cannot be empty"})
    if not task_id or not task_id.strip():
        return json.dumps({"error": "Task ID cannot be empty"})

    board_name = board.strip()
    task_id = task_id.strip()

    board_data = _load_board(board_name)
    if board_data is None:
        return json.dumps({"error": f"Board '{board_name}' not found"})

    tasks: List[Dict[str, Any]] = board_data.get("tasks", [])
    for i, task in enumerate(tasks):
        if task["id"] == task_id:
            removed = tasks.pop(i)
            _save_board(board_data)
            logger.info(
                "Deleted task '%s' (id=%s) from board '%s'",
                removed["title"], task_id, board_name,
            )
            return json.dumps({
                "board": board_name,
                "task": removed,
                "message": f"Task '{removed['title']}' deleted from board '{board_name}'",
                "remaining_tasks": len(tasks),
            })

    return json.dumps({"error": f"Task '{task_id}' not found on board '{board_name}'"})


async def tool_kanban_list_boards() -> str:
    """List all Kanban boards.

    Returns:
        JSON with board names, file paths, created dates, and task counts.
    """
    _ensure_storage_dir()
    boards: List[Dict[str, Any]] = []

    for path in sorted(STORAGE_DIR.glob("*.json")):
        try:
            board = json.loads(path.read_text(encoding="utf-8"))
            boards.append({
                "name": board.get("name", path.stem),
                "file": str(path),
                "created_at": board.get("created_at", ""),
                "updated_at": board.get("updated_at", ""),
                "task_count": len(board.get("tasks", [])),
            })
        except (json.JSONDecodeError, OSError):
            logger.warning("Skipping unreadable kanban file: %s", path)
            continue

    return json.dumps({
        "boards": boards,
        "count": len(boards),
    })
