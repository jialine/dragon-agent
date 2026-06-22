"""
Dragon API — Database connection manager.

Provides a singleton SQLAlchemy engine + session factory.
Uses SQLite with WAL mode for concurrent read support.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from dragon.api.models import Base

logger = logging.getLogger("dragon.api.db")

# ────────────────────────────────────────────────────────────────────
# Singleton state
# ────────────────────────────────────────────────────────────────────

_engine = None
_SessionLocal: sessionmaker | None = None
_DB_PATH: str = ""


def _resolve_path(db_path: str) -> str:
    """Resolve ~ and environment variables in path."""
    path = os.path.expanduser(db_path)
    path = os.path.expandvars(path)
    return path


def init_db(db_path: str = "~/.dragon/server.db") -> sessionmaker:
    """
    Initialize the database engine and create all tables.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A sessionmaker factory for creating new sessions.
    """
    global _engine, _SessionLocal, _DB_PATH

    resolved = _resolve_path(db_path)
    _DB_PATH = resolved

    # Ensure parent directory exists
    parent = Path(resolved).parent
    parent.mkdir(parents=True, exist_ok=True)

    # SQLite with WAL mode for concurrent reads
    engine = create_engine(
        f"sqlite:///{resolved}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        """Enable WAL mode and foreign keys on each connection."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Create all tables
    Base.metadata.create_all(engine)
    logger.info("Database initialized at %s", resolved)

    _engine = engine
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    return _SessionLocal


def get_session() -> Session:
    """Get a new database session (non-context-manager, for FastAPI deps)."""
    if _SessionLocal is None:
        raise RuntimeError(
            "Database not initialized. Call init_db() first."
        )
    return _SessionLocal()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: yields a database session and closes it after use.

    Usage::

        @router.get("/me")
        async def me(db: Session = Depends(get_db)):
            ...
    """
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def get_db_path() -> str:
    """Return the configured database path."""
    return _DB_PATH
