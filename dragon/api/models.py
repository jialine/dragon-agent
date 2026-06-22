"""
Dragon API — SQLAlchemy Models

Shared database models for Auth, Billing, and API Key management.
Single SQLite database file (configurable path).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    DateTime,
    Boolean,
    ForeignKey,
    Enum,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


# ════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


# ════════════════════════════════════════════════════════════════════
# Models
# ════════════════════════════════════════════════════════════════════


class User(Base):
    """Dragon Agent user account."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), default=UserRole.USER.value)
    oauth_provider = Column(String(20), nullable=True)   # "wechat" | "github"
    oauth_id = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relations
    subscriptions = relationship("Subscription", back_populates="user")
    api_keys = relationship("ApiKey", back_populates="user")


class Subscription(Base):
    """User subscription record."""

    __tablename__ = "subscriptions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    tier = Column(String(20), default=SubscriptionTier.FREE.value)
    status = Column(String(20), default=SubscriptionStatus.ACTIVE.value)
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)
    auto_renew = Column(Boolean, default=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relations
    user = relationship("User", back_populates="subscriptions")


class ApiKey(Base):
    """User API key for programmatic access."""

    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    key_prefix = Column(String(16), nullable=False)        # First 16 chars
    key_hash = Column(String(64), nullable=False, unique=True, index=True)  # SHA-256
    name = Column(String(100), default="Default")
    is_active = Column(Boolean, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone.utc), nullable=True)

    # Relations
    user = relationship("User", back_populates="api_keys")


class PaymentOrder(Base):
    """Payment order for subscription billing."""

    __tablename__ = "payment_orders"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    order_no = Column(String(32), unique=True, nullable=False, index=True)
    tier = Column(String(20), nullable=False)
    billing_cycle = Column(String(10), nullable=False)   # "monthly" | "yearly"
    amount = Column(Float, nullable=False)                # 元 (CNY)
    currency = Column(String(10), default="CNY")
    status = Column(String(20), default=PaymentStatus.PENDING.value)
    payment_method = Column(String(20), nullable=True)    # "alipay" | "wechat"
    payment_url = Column(Text, nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class UsageLog(Base):
    """Per-API-key usage tracking."""

    __tablename__ = "usage_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    tokens = Column(Integer, default=0)
    requests = Column(Integer, default=1)
    model = Column(String(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
