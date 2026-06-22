"""
Dragon Session — SQLite session store with FTS5 full-text search.

Inspired by Hermes Agent's hermes_state.py, but simpler and more focused.
Each session stores messages as compressed JSON with metadata for fast retrieval.

Features:
- FTS5 full-text search across all sessions
- Message storage with role/content/timestamp
- Session metadata (title, model, token count, platform)
- List recent, get by ID, search, delete, rename

Schema::

    sessions:
        id TEXT PRIMARY KEY
        title TEXT
        created_at TEXT
        updated_at TEXT
        platform TEXT
        model TEXT
        token_count INTEGER
        message_count INTEGER
        meta TEXT (JSON)

    messages:
        id INTEGER PRIMARY KEY AUTOINCREMENT
        session_id TEXT REFERENCES sessions(id)
        role TEXT
        content TEXT
        timestamp TEXT
        tool_calls TEXT (JSON)

    sessions_fts (FTS5 virtual table):
        title, content
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.session")


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class SessionMessage:
    role: str
    content: str
    timestamp: str = ""
    tool_calls: Optional[List[Dict]] = None

    def __post_init__(self):
        # Only auto-set timestamp when it's the default None value.
        # Allow empty string "" to pass through (from from_dict minimal).
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "tool_calls": self.tool_calls,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "SessionMessage":
        return cls(
            role=d["role"],
            content=d.get("content", ""),
            timestamp=d.get("timestamp", ""),
            tool_calls=d.get("tool_calls"),
        )


@dataclass
class Session:
    id: str
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    platform: str = "api"
    model: str = ""
    token_count: int = 0
    message_count: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "platform": self.platform,
            "model": self.model,
            "token_count": self.token_count,
            "message_count": self.message_count,
            "meta": self.meta,
        }


# ────────────────────────────────────────────────────────────────────
# SessionStore
# ────────────────────────────────────────────────────────────────────


class SessionStore:
    """SQLite-backed session store with FTS5 full-text search.

    Usage::

        store = SessionStore(db_path="dragon_data/sessions.db")
        sess = store.create(title="Hello", platform="feishu")
        store.add_message(sess.id, "user", "What is AI?")
        store.add_message(sess.id, "assistant", "AI is...")

        # Search
        results = store.search("AI model")
        for s in results:
            print(s.title)
    """

    def __init__(self, db_path: str = "dragon_data/sessions.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        logger.info("SessionStore ready at %s", self.db_path)

    # ── Schema ─────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        platform TEXT NOT NULL DEFAULT 'api',
                        model TEXT NOT NULL DEFAULT '',
                        token_count INTEGER NOT NULL DEFAULT 0,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        meta TEXT NOT NULL DEFAULT '{}'
                    )
                """)

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL DEFAULT '',
                        timestamp TEXT NOT NULL,
                        tool_calls TEXT NOT NULL DEFAULT '[]',
                        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                    )
                """)

                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")

                # FTS5 virtual table
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                        title, content
                    )
                """)

                conn.commit()
            finally:
                conn.close()

    # ── Session CRUD ───────────────────────────────────────────────

    def create(
        self,
        title: str = "",
        platform: str = "api",
        model: str = "",
        meta: Optional[Dict] = None,
    ) -> Session:
        """Create a new session."""
        sess = Session(
            id=uuid.uuid4().hex[:12],
            title=title or "New Session",
            platform=platform,
            model=model,
            meta=meta or {},
        )

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    """INSERT INTO sessions (id, title, created_at, updated_at,
                       platform, model, token_count, message_count, meta)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (sess.id, sess.title, sess.created_at, sess.updated_at,
                     sess.platform, sess.model, 0, 0, json.dumps(sess.meta)),
                )
                self._rebuild_fts(conn)
                conn.commit()
            finally:
                conn.close()

        logger.info("Created session: %s (%s)", sess.id, sess.title)
        return sess

    def get(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at, platform, model, "
                "token_count, message_count, meta FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return Session(
            id=row[0], title=row[1], created_at=row[2], updated_at=row[3],
            platform=row[4], model=row[5], token_count=row[6],
            message_count=row[7], meta=json.loads(row[8]),
        )

    def list_recent(self, limit: int = 20, platform: Optional[str] = None) -> List[Session]:
        """List recent sessions, optionally filtered by platform."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            if platform:
                rows = conn.execute(
                    """SELECT id, title, created_at, updated_at, platform, model,
                       token_count, message_count, meta FROM sessions
                       WHERE platform = ? ORDER BY updated_at DESC LIMIT ?""",
                    (platform, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, title, created_at, updated_at, platform, model,
                       token_count, message_count, meta FROM sessions
                       ORDER BY updated_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        finally:
            conn.close()

        return [
            Session(
                id=r[0], title=r[1], created_at=r[2], updated_at=r[3],
                platform=r[4], model=r[5], token_count=r[6],
                message_count=r[7], meta=json.loads(r[8]),
            )
            for r in rows
        ]

    def update_meta(self, session_id: str, **kwargs) -> bool:
        """Update session metadata fields."""
        valid_fields = {"title", "model", "platform", "token_count", "meta"}
        updates = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not updates:
            return False

        # Serialize meta to JSON if it's a dict
        if "meta" in updates and isinstance(updates["meta"], dict):
            updates["meta"] = json.dumps(updates["meta"])

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [session_id]

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    f"UPDATE sessions SET {set_clause} WHERE id = ?", values
                )
                conn.commit()
            finally:
                conn.close()

        return True

    def delete(self, session_id: str) -> bool:
        """Delete a session and all its messages."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                affected = conn.total_changes
            finally:
                conn.close()

        return affected > 0

    # ── Messages ───────────────────────────────────────────────────

    def add_message(self, session_id: str, role: str, content: str, tool_calls: Optional[List] = None) -> None:
        """Add a message to a session and update FTS index."""
        msg = SessionMessage(role=role, content=content, tool_calls=tool_calls)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                # Insert message
                conn.execute(
                    """INSERT INTO messages (session_id, role, content, timestamp, tool_calls)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, msg.role, msg.content, msg.timestamp,
                     json.dumps(msg.tool_calls or [])),
                )

                # Update session stats
                conn.execute(
                    """UPDATE sessions SET
                       message_count = message_count + 1,
                       updated_at = ?
                       WHERE id = ?""",
                    (msg.timestamp, session_id),
                )

                # Update FTS index
                self._rebuild_fts(conn)

                conn.commit()
            finally:
                conn.close()

    def get_messages(self, session_id: str, limit: int = 100, offset: int = 0) -> List[SessionMessage]:
        """Get messages for a session, most recent last."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                """SELECT role, content, timestamp, tool_calls FROM messages
                   WHERE session_id = ? ORDER BY id ASC LIMIT ? OFFSET ?""",
                (session_id, limit, offset),
            ).fetchall()
        finally:
            conn.close()

        return [
            SessionMessage(
                role=r[0], content=r[1], timestamp=r[2],
                tool_calls=json.loads(r[3]) if r[3] else None,
            )
            for r in rows
        ]

    # ── Search ─────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Full-text search across all sessions."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            try:
                rows = conn.execute(
                    """SELECT rowid, title,
                              snippet(sessions_fts, 1, '<mark>', '</mark>', '...', 40)
                       FROM sessions_fts
                       WHERE sessions_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

            results = []
            for r in rows:
                fts_rowid = r[0]
                srow = conn.execute(
                    "SELECT id, title, platform, updated_at, message_count FROM sessions WHERE rowid = ?",
                    (fts_rowid,),
                ).fetchone()
                if srow:
                    results.append({
                        "session_id": srow[0], "title": srow[1], "platform": srow[2],
                        "updated_at": srow[3], "message_count": srow[4], "snippet": r[2],
                    })
            return results
        finally:
            conn.close()

    def search_sessions_by_title(self, title_query: str, limit: int = 10) -> List[Session]:
        """Simple LIKE search on session titles (fallback when FTS unavailable)."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute(
                """SELECT id, title, created_at, updated_at, platform, model,
                   token_count, message_count, meta FROM sessions
                   WHERE title LIKE ? ORDER BY updated_at DESC LIMIT ?""",
                (f"%{title_query}%", limit),
            ).fetchall()
        finally:
            conn.close()

        return [
            Session(
                id=r[0], title=r[1], created_at=r[2], updated_at=r[3],
                platform=r[4], model=r[5], token_count=r[6],
                message_count=r[7], meta=json.loads(r[8]),
            )
            for r in rows
        ]

    # ── Helpers ────────────────────────────────────────────────────

    def _rebuild_fts(self, conn) -> None:
        """Rebuild FTS index from sessions and messages."""
        conn.execute("DELETE FROM sessions_fts")
        conn.execute("""
            INSERT INTO sessions_fts(rowid, title, content)
            SELECT s.rowid, s.title, GROUP_CONCAT(m.content, ' ')
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
        """)

    def stats(self) -> Dict[str, Any]:
        """Return store statistics."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            message_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            latest = conn.execute("SELECT MAX(updated_at) FROM sessions").fetchone()[0]
        finally:
            conn.close()

        return {
            "sessions": session_count,
            "messages": message_count,
            "latest_activity": latest or "never",
            "db_path": str(self.db_path),
        }
