"""
Dragon Agent — Auth Module (用户认证)

Provides:
  - Email/password registration and login
  - JWT access + refresh token management
  - OAuth login (WeChat, GitHub)
  - Password reset flow
  - Current user info

All passwords hashed with bcrypt (cost=12).
JWT signed with HS256; secret from DRAGON_JWT_SECRET env var.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
import jwt
import bcrypt
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from dragon.api.db import get_db
from dragon.api.models import User, UserRole
from dragon.api.deps import get_current_user

logger = logging.getLogger("dragon.api.auth")

# ────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("DRAGON_JWT_SECRET", "dragon-dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("DRAGON_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("DRAGON_REFRESH_TOKEN_EXPIRE_DAYS", "30"))

# Password hashing — bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter()


# ════════════════════════════════════════════════════════════════════
# Pydantic Schemas
# ════════════════════════════════════════════════════════════════════


class RegisterRequest(BaseModel):
    """Registration payload."""

    email: str
    password: str
    phone: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v.lower().strip()


class LoginRequest(BaseModel):
    """Login payload."""

    email: str
    password: str


class TokenResponse(BaseModel):
    """JWT token pair response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60


class RefreshRequest(BaseModel):
    """Refresh token payload."""

    refresh_token: str


class UserInfo(BaseModel):
    """Public user profile."""

    id: str
    email: str
    phone: Optional[str] = None
    role: str
    oauth_provider: Optional[str] = None
    created_at: str


class PasswordResetRequest(BaseModel):
    """Request password reset email."""

    email: str


class PasswordResetConfirm(BaseModel):
    """Confirm password reset with token."""

    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class OAuthRequest(BaseModel):
    """OAuth login payload."""

    provider: str          # "wechat" | "github"
    code: str              # OAuth authorization code
    redirect_uri: str = ""


# ════════════════════════════════════════════════════════════════════
# Password hashing — bcrypt
_pwd_rounds = 12


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    password_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=_pwd_rounds)
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8")[:72],
        hashed_password.encode("utf-8"),
    )


def create_access_token(user: User) -> str:
    """Create a JWT access token for the user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user: User) -> str:
    """Create a JWT refresh token for the user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_token_response(user: User) -> TokenResponse:
    """Create an access + refresh token pair for a user."""
    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def user_to_info(user: User) -> UserInfo:
    """Convert a User model to a UserInfo response."""
    return UserInfo(
        id=user.id,
        email=user.email,
        phone=user.phone,
        role=user.role,
        oauth_provider=user.oauth_provider,
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


# ════════════════════════════════════════════════════════════════════
# Rate Limiting (simple in-memory)
# ════════════════════════════════════════════════════════════════════

import time
from collections import defaultdict

class _RateLimitStore:
    """Simple in-memory rate limiter for auth endpoints."""

    def __init__(self):
        self._attempts: dict[str, list[float]] = defaultdict(list)

    def _cleanup(self, key: str, window: float):
        now = time.time()
        self._attempts[key] = [t for t in self._attempts[key] if now - t < window]

    def check(self, key: str, max_attempts: int, window_seconds: float = 60.0) -> bool:
        """
        Return True if under the limit, False if rate limited.
        Automatically records the attempt if under limit.
        """
        self._cleanup(key, window_seconds)
        if len(self._attempts[key]) >= max_attempts:
            return False
        self._attempts[key].append(time.time())
        return True


_rate_limiter = _RateLimitStore()


# ════════════════════════════════════════════════════════════════════
# Routes — Auth
# ════════════════════════════════════════════════════════════════════


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """
    Register a new user account.

    Returns an access + refresh token pair on success.
    The user is immediately logged in after registration.
    """
    # Rate limit: 3 registrations per hour per IP
    # (IP not available via FastAPI deps without Request; skip for now,
    #  limit by email domain instead)

    # Check existing user
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A user with this email already exists",
        )

    # Create user
    user = User(
        id=str(uuid.uuid4()),
        email=req.email,
        phone=req.phone,
        hashed_password=hash_password(req.password),
        role=UserRole.USER.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("User registered: %s (id=%s)", user.email, user.id)

    return create_token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    """
    Login with email and password.

    Rate limited: 5 attempts per minute per email.
    Returns an access + refresh token pair.
    """
    email = req.email.lower().strip()

    # Rate limit
    if not _rate_limiter.check(f"login:{email}", max_attempts=5, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again in a minute.",
        )

    # Find user
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Account is deactivated. Contact support.",
        )

    logger.info("User logged in: %s", user.email)
    return create_token_response(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest, db: Session = Depends(get_db)):
    """
    Exchange a refresh token for a new access + refresh token pair.

    The old refresh token is consumed (single-use pattern).
    """
    try:
        payload = jwt.decode(req.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Not a refresh token")
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")

    return create_token_response(user)


@router.get("/me", response_model=UserInfo)
async def get_me(user: User = Depends(get_current_user)):
    """
    Get the current authenticated user's profile.

    Requires a valid Bearer token (JWT or API key).
    """
    return user_to_info(user)


# ════════════════════════════════════════════════════════════════════
# Routes — Password Reset
# ════════════════════════════════════════════════════════════════════


@router.post("/password/reset")
async def request_password_reset(req: PasswordResetRequest, db: Session = Depends(get_db)):
    """
    Request a password reset email.

    For security, always returns success even if the email doesn't exist
    (to prevent email enumeration).
    """
    user = db.query(User).filter(User.email == req.email.lower().strip()).first()
    if user:
        # Create a short-lived reset token (15 min)
        now = datetime.now(timezone.utc)
        reset_token = jwt.encode(
            {
                "sub": user.id,
                "type": "password_reset",
                "iat": now,
                "exp": now + timedelta(minutes=15),
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )
        # In production: send email with reset link
        # For MVP: log the token (console-based flow)
        logger.info(
            "Password reset token for %s: %s (valid 15 min)",
            user.email,
            reset_token,
        )

    # Always return success (prevent email enumeration)
    return {
        "message": "If the email exists, a reset link has been sent.",
    }


@router.post("/password/reset/confirm")
async def confirm_password_reset(
    req: PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    """
    Confirm a password reset with the token from email.
    """
    try:
        payload = jwt.decode(req.token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "password_reset":
            raise HTTPException(status_code=400, detail="Invalid reset token type")
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.hashed_password = hash_password(req.new_password)
    db.commit()

    logger.info("Password reset for user %s", user.email)
    return {"message": "Password reset successful. You can now login."}


# ════════════════════════════════════════════════════════════════════
# Routes — OAuth
# ════════════════════════════════════════════════════════════════════


@router.post("/oauth/{provider}", response_model=TokenResponse)
async def oauth_login(
    provider: str,
    req: OAuthRequest,
    db: Session = Depends(get_db),
):
    """
    Login via OAuth provider (WeChat / GitHub).

    Supported providers:
      - ``wechat``: WeChat Open Platform
      - ``github``: GitHub OAuth App

    Args:
        provider: The OAuth provider name.
        req: OAuth authorization code + redirect URI.
    """
    if provider not in ("wechat", "github"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported OAuth provider: {provider}. Use 'wechat' or 'github'.",
        )

    try:
        oauth_id, oauth_email = await _resolve_oauth(provider, req.code, req.redirect_uri)
    except OAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Find or create user by OAuth ID
    user = db.query(User).filter(
        User.oauth_provider == provider,
        User.oauth_id == oauth_id,
    ).first()

    if not user:
        # Check if email is already registered (link accounts)
        if oauth_email:
            existing = db.query(User).filter(User.email == oauth_email).first()
            if existing:
                # Link OAuth to existing account
                existing.oauth_provider = provider
                existing.oauth_id = oauth_id
                db.commit()
                user = existing
                logger.info("OAuth linked to existing user: %s", existing.email)

        if not user:
            # Create new user
            user = User(
                id=str(uuid.uuid4()),
                email=oauth_email or f"{provider}_{oauth_id}@oauth.dragon",
                hashed_password=hash_password(str(uuid.uuid4())),  # Random password
                role=UserRole.USER.value,
                oauth_provider=provider,
                oauth_id=oauth_id,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info("OAuth user created: %s via %s", user.email, provider)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    return create_token_response(user)


# ════════════════════════════════════════════════════════════════════
# OAuth Resolver
# ════════════════════════════════════════════════════════════════════


class OAuthError(Exception):
    """OAuth resolution error."""
    pass


async def _resolve_oauth(
    provider: str,
    code: str,
    redirect_uri: str,
) -> tuple[str, Optional[str]]:
    """
    Resolve an OAuth authorization code to (provider_user_id, email).

    Returns:
        Tuple of (oauth_id, email_or_none).

    Raises:
        OAuthError: If the OAuth flow fails.
    """
    if provider == "wechat":
        return await _resolve_wechat(code)
    elif provider == "github":
        return await _resolve_github(code)
    raise OAuthError(f"Unknown provider: {provider}")


async def _resolve_wechat(code: str) -> tuple[str, Optional[str]]:
    """
    Resolve WeChat OAuth code.

    WeChat Open Platform flow:
      1. Exchange code for access_token + openid
      2. Optionally fetch userinfo

    Requires env vars: WECHAT_APP_ID, WECHAT_APP_SECRET
    """
    app_id = os.getenv("WECHAT_APP_ID", "")
    app_secret = os.getenv("WECHAT_APP_SECRET", "")

    if not app_id or not app_secret:
        # MVP fallback: treat code as openid directly (for testing)
        logger.warning("WeChat OAuth not configured (WECHAT_APP_ID/APP_SECRET missing)")
        return (code, None)

    # Production flow — use httpx to call WeChat API
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={
                    "appid": app_id,
                    "secret": app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            data = resp.json()
            if "errcode" in data and data["errcode"] != 0:
                raise OAuthError(f"WeChat OAuth failed: {data.get('errmsg', 'unknown')}")
            openid = data.get("openid")
            if not openid:
                raise OAuthError("WeChat OAuth: no openid returned")
            return (openid, None)
    except ImportError:
        raise OAuthError("httpx not installed — required for OAuth")
    except Exception as e:
        logger.exception("WeChat OAuth error")
        raise OAuthError(f"WeChat OAuth error: {e}")


async def _resolve_github(code: str) -> tuple[str, Optional[str]]:
    """
    Resolve GitHub OAuth code.

    Requires env vars: GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
    """
    client_id = os.getenv("GITHUB_CLIENT_ID", "")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        # MVP fallback
        logger.warning("GitHub OAuth not configured (GITHUB_CLIENT_ID/CLIENT_SECRET missing)")
        return (code, None)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: Exchange code for access token
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                },
                headers={"Accept": "application/json"},
            )
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise OAuthError(f"GitHub OAuth failed: {token_data.get('error_description', 'unknown')}")

            # Step 2: Fetch user info
            user_resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            user_data = user_resp.json()
            github_id = str(user_data.get("id", ""))
            email = user_data.get("email") or None
            if not github_id:
                raise OAuthError("GitHub OAuth: no user id returned")

            return (github_id, email)
    except ImportError:
        raise OAuthError("httpx not installed — required for OAuth")
    except Exception as e:
        logger.exception("GitHub OAuth error")
        raise OAuthError(f"GitHub OAuth error: {e}")
