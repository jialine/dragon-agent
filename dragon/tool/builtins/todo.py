"""
Dragon Agent — Todo Tool (Hermes-aligned)
==========================================

Session-level todo list management with merge/overwrite semantics.
Hermes alignment: todo(todos=None, merge=False).

Storage: in-memory (session-scoped). Lost on restart.
Statuses: pending, in_progress, completed, cancelled.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dragon.tool.builtins.todo")

# ── In-memory session store ──────────────────────────────────────────

_todos: List[Dict[str, Any]] = []

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


# ── Tool Implementation ──────────────────────────────────────────────


async def tool_todo(
    todos: Optional[list] = None,
    merge: bool = False,
) -> str:
    """Manage a session-level todo list.

    Hermes-aligned: todo(todos=None, merge=False)

    - Call with todos=None to view current list.
    - Call with todos=JSON array to set (merge=False) or merge (merge=True) items.
    - Each item: {id, content, status}

    Args:
        todos: JSON array of todo items, each with:
            - id (str, optional): Item identifier. Auto-generated if omitted.
            - content (str, required): Description of the task.
            - status (str, optional): One of pending|in_progress|completed|cancelled.
              Defaults to "pending".
        merge: If True, merge the provided items with existing list (upsert by id).
            If False, replace the entire list with the provided items.

    Returns:
        JSON with current todo list and summary counts.
    """
    global _todos

    if todos is not None:
        try:
            new_items = json.loads(todos) if isinstance(todos, str) else todos
            if not isinstance(new_items, list):
                return json.dumps({"error": "todos must be a JSON array"})
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        # Validate and normalise items
        validated = []
        for item in new_items:
            if not isinstance(item, dict):
                return json.dumps({"error": f"Each todo item must be an object, got {type(item).__name__}"})

            content = item.get("content", "")
            if not content or not str(content).strip():
                return json.dumps({"error": "Each todo item must have a non-empty 'content' field"})

            status = item.get("status", "pending")
            if status not in VALID_STATUSES:
                return json.dumps({
                    "error": f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
                })

            item_id = item.get("id") or str(uuid.uuid4())[:8]
            validated.append({
                "id": str(item_id),
                "content": str(content).strip(),
                "status": status,
            })

        if merge:
            # Upsert: update items with matching id, append new ones
            existing_ids = {t["id"] for t in _todos}
            for item in validated:
                if item["id"] in existing_ids:
                    # Update existing
                    for i, t in enumerate(_todos):
                        if t["id"] == item["id"]:
                            _todos[i] = item
                            break
                else:
                    # Append new
                    _todos.append(item)
            logger.info("Merged %d todo items (total: %d)", len(validated), len(_todos))
        else:
            # Replace entire list
            _todos = validated
            logger.info("Set todo list to %d items", len(_todos))
    else:
        logger.debug("Listing %d todo items", len(_todos))

    # Build summary
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
    for t in _todos:
        s = t.get("status", "pending")
        if s in counts:
            counts[s] += 1

    return json.dumps({
        "todos": _todos,
        "total": len(_todos),
        "counts": counts,
    }, ensure_ascii=False)


def _reset_todos():
    """Reset the todo list (useful for testing)."""
    global _todos
    _todos = []
