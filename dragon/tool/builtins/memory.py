"""
Dragon Agent — Persistent Memory Tool
=====================================

Agent-callable tool for managing persistent memories stored in a
simple JSON file at ~/.dragon/memory.json.

Supports three actions (add, replace, remove) and two targets
(user, memory).

Data format::

    {
        "user": [
            {"content": "User prefers short answers", "created_at": "2026-01-01T00:00:00"},
        ],
        "memory": [
            {"content": "Working on dragon-agent project", "created_at": "2026-01-01T00:00:00"},
        ],
    }

Tools:
    - memory: Manage persistent agent memories (add / replace / remove)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dragon.tool.memory")

# ── Memory file path ─────────────────────────────────────────────────
MEMORY_FILE = Path.home() / ".dragon" / "memory.json"


# ── Helpers ──────────────────────────────────────────────────────────


def _load_memory() -> Dict[str, List[Dict[str, str]]]:
    """Load the memory JSON file, returning defaults if it doesn't exist."""
    if not MEMORY_FILE.exists():
        return {"user": [], "memory": []}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"user": [], "memory": []}
        for key in ("user", "memory"):
            if key not in data or not isinstance(data[key], list):
                data[key] = []
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load memory file: %s", e)
        return {"user": [], "memory": []}


def _save_memory(data: Dict[str, List[Dict[str, str]]]) -> None:
    """Save the memory dict to the JSON file, creating parents as needed."""
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ── Tool Implementation ──────────────────────────────────────────────


async def tool_memory(
    action: str = "add",
    target: str = "memory",
    content: str = "",
) -> str:
    """Manage persistent agent memories.

    Use this tool to remember facts, preferences, and context across sessions.
    Memories are stored in ~/.dragon/memory.json and automatically loaded into
    the agent's system prompt on startup.

    Args:
        action: The memory operation to perform:
            - 'add': Append a new memory entry to the target list.
            - 'replace': Replace all entries for the target with a single new entry.
            - 'remove': Remove entries from the target. If content is empty,
              removes ALL entries for the target. Otherwise removes entries
              whose content exactly matches.
        target: Either 'user' (personal preferences, background, facts about the user)
            or 'memory' (project context, environment, task-related information).
        content: The text content to store/remove. Required for 'add' and 'replace'.
            Optional for 'remove' (omit to clear all entries).

    Returns:
        JSON with the operation result, affected count, and updated entries.
    """
    # ── Validate inputs ──────────────────────────────────────────
    action = (action or "").strip().lower()
    target = (target or "").strip().lower()

    if action not in ("add", "replace", "remove"):
        return json.dumps({
            "error": f"Invalid action '{action}'. Must be 'add', 'replace', or 'remove'.",
        })

    if target not in ("user", "memory"):
        return json.dumps({
            "error": f"Invalid target '{target}'. Must be 'user' or 'memory'.",
        })

    if action in ("add", "replace") and not content.strip():
        return json.dumps({
            "error": f"Content is required for action '{action}'.",
        })

    content = content.strip() if content else ""

    # ── Load current data ────────────────────────────────────────
    data = _load_memory()

    # ── Perform action ───────────────────────────────────────────
    if action == "add":
        entry = {"content": content, "created_at": _now_iso()}
        data[target].append(entry)
        _save_memory(data)
        return json.dumps({
            "action": "add",
            "target": target,
            "entry": entry,
            "total": len(data[target]),
            "entries": data[target],
        })

    elif action == "replace":
        old_count = len(data[target])
        entry = {"content": content, "created_at": _now_iso()}
        data[target] = [entry]
        _save_memory(data)
        return json.dumps({
            "action": "replace",
            "target": target,
            "replaced": old_count,
            "entry": entry,
            "total": 1,
            "entries": data[target],
        })

    elif action == "remove":
        before_count = len(data[target])

        if not content:
            # Remove all entries for this target
            data[target] = []
            removed_count = before_count
        else:
            # Remove exact content matches
            new_list = [e for e in data[target] if e.get("content") != content]
            removed_count = before_count - len(new_list)
            data[target] = new_list

        _save_memory(data)
        return json.dumps({
            "action": "remove",
            "target": target,
            "removed": removed_count,
            "remaining": len(data[target]),
            "entries": data[target],
        })


# ── Public helper (used by main.py lifespan) ─────────────────────────


def load_memory_for_prompt() -> str:
    """Load all memories and format them for injection into a system prompt.

    Returns a multi-line string suitable for appending to a system prompt,
    or an empty string if no memories exist.
    """
    data = _load_memory()
    parts = []

    for key, label in [("user", "User Memory"), ("memory", "System Memory")]:
        entries = data.get(key, [])
        if entries:
            lines = [f"## {label}"]
            for entry in entries:
                lines.append(f"- {entry['content']}")
            parts.append("\n".join(lines))

    if not parts:
        return ""

    return "\n\n" + "\n\n".join(parts)
