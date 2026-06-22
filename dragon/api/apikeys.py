"""
Dragon Agent — API Key Management Module

Provides:
  - API key creation (format: dragon_v1_<32_hex>)
  - List user's keys
  - Revoke / disable keys
  - Key rotation (revoke old + create new)
  - Per-key usage statistics

Keys are SHA-256 hashed before storage. The full key is returned
only once at creation time — after that, only the prefix is visible.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from dragon.api.db import get_db
from dragon.api.models import ApiKey, UsageLog, User
from dragon.api.deps import get_current_user

logger = logging.getLogger("dragon.api.apikeys")

router = APIRouter()

# ────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────

KEY_PREFIX = "dragon_v1"
KEY_RANDOM_BYTES = 32          # 256-bit random key

# Per-tier limits
TIER_KEY_LIMITS = {
    "free": 1,
    "pro": 5,
    "team": 20,
    "enterprise": 0,  # unlimited
}


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def generate_api_key() -> str:
    """Generate a new API key: dragon_v1_<64_hex_chars>."""
    random_part = secrets.token_hex(KEY_RANDOM_BYTES)
    return f"{KEY_PREFIX}_{random_part}"


def hash_key(full_key: str) -> str:
    """SHA-256 hash of the full API key."""
    return hashlib.sha256(full_key.encode()).hexdigest()


def key_prefix(full_key: str) -> str:
    """First 16 characters for display."""
    return full_key[:16]


def get_user_key_limit(user: User) -> int:
    """Get the maximum number of active API keys for the user."""
    # Get current subscription tier
    # For simplicity, check role for now — billing integration later
    if user.role == "admin":
        return 0  # unlimited
    return 5  # Default: pro tier limit


# ════════════════════════════════════════════════════════════════════
# Pydantic Schemas
# ════════════════════════════════════════════════════════════════════


class CreateKeyRequest(BaseModel):
    """Create a new API key."""

    name: str = Field(default="Default", max_length=100)


class ApiKeyResponse(BaseModel):
    """API key returned at creation time (includes full key)."""

    id: str
    name: str
    key_prefix: str
    full_key: str             # ONLY returned at creation — save it now!
    created_at: str
    is_active: bool = True


class ApiKeyInfo(BaseModel):
    """API key metadata (no full key — safe for listing)."""

    id: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: Optional[str] = None
    is_active: bool


class UsageStats(BaseModel):
    """Per-key usage statistics."""

    key_id: str
    key_name: str
    tokens_today: int = 0
    tokens_this_month: int = 0
    requests_today: int = 0
    requests_this_month: int = 0


# ════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════


@router.post("/", response_model=ApiKeyResponse, status_code=201)
async def create_key(
    req: CreateKeyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new API key.

    **The full key is returned only once. Save it immediately.**
    After this response, the full key cannot be retrieved again.
    """
    # Check key count limit
    limit = get_user_key_limit(user)
    if limit > 0:
        active_count = db.query(ApiKey).filter(
            ApiKey.user_id == user.id,
            ApiKey.is_active == True,
        ).count()
        if active_count >= limit:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {limit} active API keys reached. Revoke an existing key first.",
            )

    # Generate key
    full_key = generate_api_key()
    key_hash = hash_key(full_key)
    prefix = key_prefix(full_key)

    api_key = ApiKey(
        id=str(uuid.uuid4()),
        user_id=user.id,
        key_prefix=prefix,
        key_hash=key_hash,
        name=req.name,
        is_active=True,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    logger.info(
        "API key created for user %s: %s (prefix=%s)",
        user.email, api_key.id, prefix,
    )

    return ApiKeyResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=prefix,
        full_key=full_key,
        created_at=api_key.created_at.isoformat() if api_key.created_at else "",
        is_active=True,
    )


@router.get("/", response_model=list[ApiKeyInfo])
async def list_keys(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all API keys for the current user.

    Only metadata is returned — never the full key.
    """
    keys = db.query(ApiKey).filter(
        ApiKey.user_id == user.id,
    ).order_by(ApiKey.created_at.desc()).all()

    return [
        ApiKeyInfo(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            created_at=k.created_at.isoformat() if k.created_at else "",
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            is_active=k.is_active,
        )
        for k in keys
    ]


@router.post("/{key_id}/revoke")
async def revoke_key(
    key_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Revoke an API key — immediately disables it.

    Revoked keys cannot be re-enabled. Create a new key instead.
    """
    api_key = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.user_id == user.id,
    ).first()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    if not api_key.is_active:
        raise HTTPException(status_code=400, detail="API key is already revoked")

    api_key.is_active = False
    api_key.revoked_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        "API key revoked: %s (user=%s, prefix=%s)",
        key_id, user.email, api_key.key_prefix,
    )

    return {"message": "API key revoked", "key_id": key_id}


@router.post("/{key_id}/rotate", response_model=ApiKeyResponse)
async def rotate_key(
    key_id: str,
    req: CreateKeyRequest = CreateKeyRequest(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Rotate an API key — revoke the old key and create a new one.

    The old key is immediately disabled. A new full key is returned.
    """
    old_key = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.user_id == user.id,
    ).first()

    if not old_key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Revoke old key
    old_key.is_active = False
    old_key.revoked_at = datetime.now(timezone.utc)

    # Create new key
    full_key = generate_api_key()
    new_key = ApiKey(
        id=str(uuid.uuid4()),
        user_id=user.id,
        key_prefix=key_prefix(full_key),
        key_hash=hash_key(full_key),
        name=req.name or old_key.name,
        is_active=True,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    logger.info(
        "API key rotated: %s → %s (user=%s)",
        key_id, new_key.id, user.email,
    )

    return ApiKeyResponse(
        id=new_key.id,
        name=new_key.name,
        key_prefix=new_key.key_prefix,
        full_key=full_key,
        created_at=new_key.created_at.isoformat() if new_key.created_at else "",
        is_active=True,
    )


@router.get("/{key_id}/stats", response_model=UsageStats)
async def key_stats(
    key_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get usage statistics for a specific API key.

    Shows tokens and requests for today and the current month.
    """
    api_key = db.query(ApiKey).filter(
        ApiKey.id == key_id,
        ApiKey.user_id == user.id,
    ).first()

    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Today's usage
    today_query = db.query(
        func.coalesce(func.sum(UsageLog.tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.requests), 0).label("requests"),
    ).filter(
        UsageLog.api_key_id == key_id,
        UsageLog.created_at >= today_start,
    ).first()

    # Month's usage
    month_query = db.query(
        func.coalesce(func.sum(UsageLog.tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.requests), 0).label("requests"),
    ).filter(
        UsageLog.api_key_id == key_id,
        UsageLog.created_at >= month_start,
    ).first()

    return UsageStats(
        key_id=key_id,
        key_name=api_key.name,
        tokens_today=int(today_query.tokens) if today_query else 0,
        tokens_this_month=int(month_query.tokens) if month_query else 0,
        requests_today=int(today_query.requests) if today_query else 0,
        requests_this_month=int(month_query.requests) if month_query else 0,
    )
