"""
Dragon API — FastAPI Dependencies.

Reusable dependency injection functions for authentication,
rate limiting, and database access.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
import jwt
from sqlalchemy.orm import Session

from dragon.api.models import User, ApiKey, UserRole
from dragon.api.db import get_db

logger = logging.getLogger("dragon.api.deps")

# ────────────────────────────────────────────────────────────────────
# JWT Configuration
# ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("DRAGON_JWT_SECRET", "dragon-dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30


# ────────────────────────────────────────────────────────────────────
# Authentication
# ────────────────────────────────────────────────────────────────────


def _verify_api_key(token: str, db: Session) -> Optional[User]:
    """Try to authenticate via API key."""
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    api_key = db.query(ApiKey).filter(
        ApiKey.key_hash == key_hash,
        ApiKey.is_active == True,
    ).first()

    if not api_key:
        return None

    # Update last_used_at
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()

    return api_key.user


def _verify_jwt(token: str, db: Session) -> Optional[User]:
    """Try to authenticate via JWT."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
    except Exception:
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> User:
    """
    Extract authenticated user from Bearer token.

    Supports both JWT tokens and API keys::

        Authorization: Bearer <jwt_or_api_key>

    Raises:
        HTTPException 401: If the token is invalid or expired.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Empty token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try JWT first, then API key
    user = _verify_jwt(token, db)
    if user is None:
        user = _verify_api_key(token, db)

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_optional_user(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Optionally authenticate — returns None if no token provided.
    Use for endpoints that work both authenticated and anonymously.
    """
    if not authorization:
        return None
    try:
        return await get_current_user(authorization, db)
    except HTTPException:
        return None


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Require admin role. Returns user if admin, raises 403 otherwise."""
    if user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required",
        )
    return user
