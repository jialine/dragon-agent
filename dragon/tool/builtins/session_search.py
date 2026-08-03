"""
Dragon Agent — Session Search Tool
===================================

Search past conversation sessions stored in SQLite (FTS5 full-text search).
Supports listing recent sessions and keyword search across all stored conversations.

Tools:
    - session_search: Search or list past conversation sessions

Uses the SessionStore from dragon.session for storage and FTS5 search.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from dragon.session import SessionStore

logger = logging.getLogger("dragon.tool.builtins.session_search")

# ── Singleton SessionStore (lazy-init with thread safety) ────────────

_store: Optional[SessionStore] = None


def _get_store(db_path: str = "") -> SessionStore:
    """Get or create the SessionStore singleton."""
    global _store
    if _store is None:
        resolved = db_path if db_path else "dragon_data/sessions.db"
        _store = SessionStore(db_path=resolved)
    elif db_path and Path(db_path).resolve() != Path(_store.db_path).resolve():
        # If caller explicitly requests a different path, switch
        _store = SessionStore(db_path=db_path)
    return _store


# ── Helpers ──────────────────────────────────────────────────────────


def _make_preview(store: SessionStore, session_id: str, max_len: int = 100) -> str:
    """Get a preview from the first user message of a session."""
    try:
        msgs = store.get_messages(session_id, limit=1, offset=0)
        if msgs and msgs[0].content:
            content = msgs[0].content.strip()
            if len(content) > max_len:
                content = content[:max_len].rstrip() + "…"
            return content
    except Exception:
        pass
    return ""


def _format_timestamp(iso_str: str) -> str:
    """Format ISO timestamp to a human-readable short form."""
    try:
        # e.g., "2025-06-26T14:30:00+00:00" → "2025-06-26 14:30"
        return iso_str[:16].replace("T", " ")
    except Exception:
        return iso_str


# ── Tool Implementation ──────────────────────────────────────────────


async def tool_session_search(
    query: str = "",
    limit: int = 3,
    role_filter: str = None,
) -> str:
    """Search or list past conversation sessions.

    Two modes:
    1. No query (query=""): Returns the most recent sessions with titles,
       previews (first message), and timestamps.
    2. With query: Performs FTS5 full-text search across all session messages
       and returns matching sessions with snippets.

    Args:
        query: Search query for FTS5 full-text search. Leave empty to list
            recent sessions.
        limit: Maximum number of sessions to return (default: 5, max: 20).
        platform: Optional filter by platform (e.g., "feishu", "api").

    Returns:
        JSON with a sessions list, each containing title, created_at, summary,
        session_id, platform, and optional snippet (for search mode).
    """
    limit = max(1, min(limit, 20))
    store = _get_store()

    results = []

    if not query or not query.strip():
        # ── Mode 1: List recent sessions ──────────────────────────
        sessions = store.list_recent(
            limit=limit,
            platform=platform.strip() if platform else None,
        )
        for sess in sessions:
            preview = _make_preview(store, sess.id)
            results.append({
                "session_id": sess.id,
                "title": sess.title,
                "created_at": _format_timestamp(sess.created_at),
                "updated_at": _format_timestamp(sess.updated_at),
                "role_filter": sess.platform,
                "message_count": sess.message_count,
                "preview": preview,
            })
    else:
        # ── Mode 2: FTS5 search ───────────────────────────────────
        search_results = store.search(query.strip(), limit=limit)
        for sr in search_results:
            preview = _make_preview(store, sr["session_id"])
            # Look up full session to get created_at (search() only returns updated_at)
            sess = store.get(sr["session_id"])
            created_at = sess.created_at if sess else ""
            results.append({
                "session_id": sr["session_id"],
                "title": sr["title"],
                "created_at": _format_timestamp(created_at),
                "updated_at": _format_timestamp(sr.get("updated_at", "")),
                "role_filter": sr.get("role_filter", ""),
                "message_count": sr.get("message_count", 0),
                "snippet": sr.get("snippet", ""),
                "preview": preview,
            })

    return json.dumps({
        "mode": "search" if query.strip() else "recent",
        "query": query.strip() if query.strip() else None,
        "total": len(results),
        "sessions": results,
    })
