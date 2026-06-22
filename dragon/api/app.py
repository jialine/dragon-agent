"""
Dragon Agent — FastAPI Application Factory.

Creates and configures a FastAPI app with all API routers mounted.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dragon.api.db import init_db

logger = logging.getLogger("dragon.api.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — initialize database on startup."""
    db_path = app.state.db_path if hasattr(app.state, "db_path") else "~/.dragon/server.db"
    init_db(db_path)
    logger.info("Dragon API started (db=%s)", db_path)
    yield
    logger.info("Dragon API shutting down")


def create_app(
    db_path: str = "~/.dragon/server.db",
    title: str = "Dragon Agent API",
    version: str = "1.0.0",
) -> FastAPI:
    """
    Create a FastAPI application with all routers mounted.

    Args:
        db_path: Path to the SQLite database file.
        title: API title for OpenAPI docs.
        version: API version string.

    Returns:
        A configured FastAPI application instance.

    Usage::

        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=8780)
    """
    app = FastAPI(
        title=title,
        version=version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # Store config on app state
    app.state.db_path = db_path

    # CORS — allow all origins in dev; restrict in prod
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health Check ────────────────────────────────────────────────
    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "version": version}

    # ── Mount Routers ────────────────────────────────────────────────
    from dragon.api.auth import router as auth_router
    from dragon.api.billing import router as billing_router
    from dragon.api.apikeys import router as apikeys_router

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
    app.include_router(billing_router, prefix="/api/v1/billing", tags=["Billing"])
    app.include_router(apikeys_router, prefix="/api/v1/keys", tags=["API Keys"])

    return app
