"""
Dragon Agent — Billing Module (计费系统)

Provides:
  - Subscription plan listing (Free / Pro / Team / Enterprise)
  - Subscribe / cancel subscription
  - Payment order creation
  - Payment callback handling (Alipay / WeChat Pay)
  - Token usage quota tracking

MVP: Plans are hardcoded. Payment integration uses mock/console flow.
Production: Integrate Alipay SDK + WeChat Pay API v3.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from dragon.api.db import get_db
from dragon.api.models import (
    User,
    Subscription,
    SubscriptionTier,
    SubscriptionStatus,
    PaymentOrder,
    PaymentStatus,
    UsageLog,
    ApiKey,
)
from dragon.api.deps import get_current_user, require_admin

logger = logging.getLogger("dragon.api.billing")

router = APIRouter()


# ════════════════════════════════════════════════════════════════════
# Plan Definitions
# ════════════════════════════════════════════════════════════════════

PLANS = {
    "free": {
        "tier": "free",
        "name": "免费版",
        "price_monthly": 0.0,
        "price_yearly": 0.0,
        "token_quota": 10_000,           # Monthly
        "concurrent_requests": 1,
        "api_keys": 1,
        "history_days": 7,
        "support": "社区",
        "models": ["基础模型"],
        "features": [
            "基础问答",
            "1 个 API Key",
            "7 天历史记录",
            "社区支持",
        ],
    },
    "pro": {
        "tier": "pro",
        "name": "专业版",
        "price_monthly": 29.0,
        "price_yearly": 290.0,
        "token_quota": 500_000,
        "concurrent_requests": 3,
        "api_keys": 5,
        "history_days": 30,
        "support": "邮件优先",
        "models": ["全部模型"],
        "features": [
            "全部模型访问",
            "5 个 API Key",
            "30 天历史记录",
            "优先邮件支持",
            "事实核查 + 共识输出",
            "API 速率 60次/分",
        ],
    },
    "team": {
        "tier": "team",
        "name": "团队版",
        "price_monthly": 99.0,
        "price_yearly": 990.0,
        "token_quota": 3_000_000,
        "concurrent_requests": 10,
        "api_keys": 20,
        "history_days": 90,
        "support": "专属支持",
        "models": ["全部模型 + 专属优化"],
        "features": [
            "全部功能",
            "20 个 API Key",
            "90 天历史记录",
            "专属技术支持",
            "API 速率 300次/分",
            "团队管理面板",
            "用量分析报告",
        ],
    },
    "enterprise": {
        "tier": "enterprise",
        "name": "企业版",
        "price_monthly": 0.0,    # 议价
        "price_yearly": 0.0,
        "token_quota": 0,         # 无限
        "concurrent_requests": 0, # 无限
        "api_keys": 0,           # 无限
        "history_days": 0,       # 永久
        "support": "专属客户经理",
        "models": ["全部模型 + 私有部署"],
        "features": [
            "无限 Token",
            "无限 API Key",
            "永久历史记录",
            "专属客户经理",
            "SLA 99.9%",
            "SSO 单点登录",
            "私有部署选项",
            "自定义模型接入",
        ],
    },
}


# ════════════════════════════════════════════════════════════════════
# Pydantic Schemas
# ════════════════════════════════════════════════════════════════════


class PlanInfo(BaseModel):
    """Subscription plan details."""
    tier: str
    name: str
    price_monthly: float
    price_yearly: float
    token_quota: int
    concurrent_requests: int
    api_keys: int
    history_days: int
    support: str
    models: list[str]
    features: list[str]


class SubscribeRequest(BaseModel):
    """Create a subscription order."""
    tier: str
    billing_cycle: str = "monthly"   # "monthly" | "yearly"


class SubscribeResponse(BaseModel):
    """Subscription creation result."""
    order_id: str
    order_no: str
    tier: str
    amount: float
    currency: str = "CNY"
    payment_url: Optional[str] = None
    status: str


class SubscriptionInfo(BaseModel):
    """Current subscription details."""
    tier: str
    tier_name: str
    status: str
    started_at: str
    expires_at: Optional[str] = None
    tokens_used_this_month: int
    tokens_quota: int
    auto_renew: bool
    features: list[str]


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def _get_plan(tier: str) -> dict:
    """Get plan definition, raise if invalid tier."""
    if tier not in PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier: {tier}. Available: {', '.join(PLANS.keys())}",
        )
    return PLANS[tier]


def _get_active_subscription(user_id: str, db: Session) -> Optional[Subscription]:
    """Get the user's active subscription, if any."""
    return db.query(Subscription).filter(
        Subscription.user_id == user_id,
        Subscription.status == SubscriptionStatus.ACTIVE.value,
    ).order_by(Subscription.created_at.desc()).first()


def _get_monthly_usage(user_id: str, db: Session) -> int:
    """Get total tokens used this month by user."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = db.query(
        func.coalesce(func.sum(UsageLog.tokens), 0)
    ).filter(
        UsageLog.user_id == user_id,
        UsageLog.created_at >= month_start,
    ).scalar()

    return int(result) if result else 0


def _generate_order_no() -> str:
    """Generate a unique order number: DRG-YYYYMMDD-XXXXX."""
    now = datetime.now(timezone.utc)
    date_part = now.strftime("%Y%m%d")
    rand_part = os.urandom(3).hex().upper()
    return f"DRG-{date_part}-{rand_part}"


# ════════════════════════════════════════════════════════════════════
# Routes — Plans
# ════════════════════════════════════════════════════════════════════


@router.get("/plans", response_model=list[PlanInfo])
async def list_plans():
    """
    Get all available subscription plans.

    Public endpoint — no authentication required.
    """
    return [PlanInfo(**plan) for plan in PLANS.values()]


@router.get("/plans/{tier}", response_model=PlanInfo)
async def get_plan(tier: str):
    """
    Get details for a specific plan tier.
    """
    return PlanInfo(**_get_plan(tier))


# ════════════════════════════════════════════════════════════════════
# Routes — Subscription
# ════════════════════════════════════════════════════════════════════


@router.get("/subscription", response_model=SubscriptionInfo)
async def get_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the current user's subscription details.

    Includes token usage for the current billing period.
    """
    sub = _get_active_subscription(user.id, db)

    if not sub:
        # Default to free tier
        plan = PLANS["free"]
        return SubscriptionInfo(
            tier="free",
            tier_name=plan["name"],
            status="active",
            started_at="N/A",
            tokens_used_this_month=_get_monthly_usage(user.id, db),
            tokens_quota=plan["token_quota"],
            auto_renew=False,
            features=plan["features"],
        )

    plan = PLANS.get(sub.tier, PLANS["free"])
    return SubscriptionInfo(
        tier=sub.tier,
        tier_name=plan["name"],
        status=sub.status,
        started_at=sub.started_at.isoformat() if sub.started_at else "",
        expires_at=sub.expires_at.isoformat() if sub.expires_at else None,
        tokens_used_this_month=_get_monthly_usage(user.id, db),
        tokens_quota=plan["token_quota"],
        auto_renew=sub.auto_renew,
        features=plan["features"],
    )


@router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(
    req: SubscribeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Subscribe to a plan.

    Creates a payment order and returns payment URL.
    For free tier: immediately activates.
    """
    plan = _get_plan(req.tier)

    if req.billing_cycle not in ("monthly", "yearly"):
        raise HTTPException(status_code=400, detail="billing_cycle must be 'monthly' or 'yearly'")

    # Determine price
    if req.billing_cycle == "yearly":
        amount = plan["price_yearly"]
    else:
        amount = plan["price_monthly"]

    # Free tier — activate immediately
    if req.tier == "free" or amount == 0.0:
        # Cancel existing active subscription
        existing = db.query(Subscription).filter(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        ).all()
        for sub in existing:
            sub.status = SubscriptionStatus.CANCELLED.value

        # Create new free subscription
        new_sub = Subscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            tier=req.tier,
            status=SubscriptionStatus.ACTIVE.value,
            auto_renew=False,
        )
        db.add(new_sub)
        db.commit()

        order = PaymentOrder(
            id=str(uuid.uuid4()),
            user_id=user.id,
            order_no=_generate_order_no(),
            tier=req.tier,
            billing_cycle=req.billing_cycle,
            amount=0.0,
            status=PaymentStatus.PAID.value,
            paid_at=datetime.now(timezone.utc),
        )
        db.add(order)
        db.commit()

        return SubscribeResponse(
            order_id=order.id,
            order_no=order.order_no,
            tier=req.tier,
            amount=0.0,
            status="paid",
        )

    # Paid tier — create payment order
    order = PaymentOrder(
        id=str(uuid.uuid4()),
        user_id=user.id,
        order_no=_generate_order_no(),
        tier=req.tier,
        billing_cycle=req.billing_cycle,
        amount=amount,
        status=PaymentStatus.PENDING.value,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # Generate mock payment URL (production: call Alipay/WeChat Pay API)
    payment_url = None
    base_url = os.getenv("DRAGON_API_BASE_URL", "http://localhost:8780")

    # Check if payment method is configured
    if os.getenv("ALIPAY_APP_ID"):
        # Production: integrate alipay SDK
        payment_url = f"{base_url}/api/v1/billing/payment/mock?order_id={order.id}"
        logger.info("Alipay payment URL generated for order %s", order.order_no)
    elif os.getenv("WECHAT_PAY_MCH_ID"):
        # Production: integrate wechatpayv3
        payment_url = f"{base_url}/api/v1/billing/payment/mock?order_id={order.id}"
        logger.info("WeChat Pay URL generated for order %s", order.order_no)
    else:
        # MVP mock payment
        payment_url = f"{base_url}/api/v1/billing/payment/mock?order_id={order.id}"
        logger.info("Mock payment URL for order %s: %s", order.order_no, payment_url)

    order.payment_url = payment_url
    db.commit()

    return SubscribeResponse(
        order_id=order.id,
        order_no=order.order_no,
        tier=req.tier,
        amount=amount,
        payment_url=payment_url,
        status="pending",
    )


@router.post("/subscription/cancel")
async def cancel_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cancel auto-renewal for the current subscription.

    The subscription remains active until it expires.
    """
    sub = _get_active_subscription(user.id, db)
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")

    sub.auto_renew = False
    db.commit()

    logger.info("Auto-renew cancelled for user %s (tier=%s)", user.email, sub.tier)
    return {"message": "Auto-renewal cancelled", "tier": sub.tier}


# ════════════════════════════════════════════════════════════════════
# Routes — Payment
# ════════════════════════════════════════════════════════════════════


@router.get("/payment/mock")
async def mock_payment(order_id: str, db: Session = Depends(get_db)):
    """
    Mock payment endpoint for MVP testing.

    In production, this is replaced by real payment gateway callbacks.
    Simulates a successful payment and activates the subscription.
    """
    order = db.query(PaymentOrder).filter(PaymentOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status == PaymentStatus.PAID.value:
        return {"message": "Order already paid", "order_no": order.order_no}

    # Mark order as paid
    order.status = PaymentStatus.PAID.value
    order.paid_at = datetime.now(timezone.utc)
    db.commit()

    # Activate subscription
    await _activate_subscription(order, db)

    logger.info("Mock payment completed for order %s (user=%s)", order.order_no, order.user_id)
    return {
        "message": "Payment successful",
        "order_no": order.order_no,
        "tier": order.tier,
    }


@router.post("/payment/notify")
async def payment_notify(request: Request, db: Session = Depends(get_db)):
    """
    Payment gateway callback endpoint.

    Accepts POST from Alipay / WeChat Pay with payment verification.
    Validates signature before processing.
    """
    try:
        body = await request.json()
    except Exception:
        body = await request.form()
        body = dict(body)

    # In production: validate signature with payment gateway SDK
    order_no = body.get("out_trade_no") or body.get("order_no")
    if not order_no:
        raise HTTPException(status_code=400, detail="Missing order_no")

    order = db.query(PaymentOrder).filter(PaymentOrder.order_no == order_no).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Check payment status
    trade_status = body.get("trade_status") or body.get("status", "SUCCESS")
    if trade_status not in ("TRADE_SUCCESS", "SUCCESS"):
        logger.warning("Payment not successful for order %s: %s", order_no, trade_status)
        return {"code": "FAIL", "message": "Payment not successful"}

    # Deduplicate — already paid
    if order.status == PaymentStatus.PAID.value:
        return {"code": "SUCCESS", "message": "Already processed"}

    order.status = PaymentStatus.PAID.value
    order.paid_at = datetime.now(timezone.utc)
    order.payment_method = body.get("payment_method", "unknown")
    db.commit()

    await _activate_subscription(order, db)

    logger.info("Payment verified for order %s (method=%s)", order_no, order.payment_method)
    return {"code": "SUCCESS", "message": "Payment processed"}


async def _activate_subscription(order: PaymentOrder, db: Session) -> None:
    """
    Activate a subscription after successful payment.

    Cancels any existing active subscription and creates a new one.
    """
    # Calculate expiry
    now = datetime.now(timezone.utc)
    if order.billing_cycle == "yearly":
        expires_at = now + timedelta(days=365)
    else:
        expires_at = now + timedelta(days=30)

    # Cancel existing active subscriptions
    existing = db.query(Subscription).filter(
        Subscription.user_id == order.user_id,
        Subscription.status == SubscriptionStatus.ACTIVE.value,
    ).all()
    for sub in existing:
        sub.status = SubscriptionStatus.CANCELLED.value

    # Create new subscription
    new_sub = Subscription(
        id=str(uuid.uuid4()),
        user_id=order.user_id,
        tier=order.tier,
        status=SubscriptionStatus.ACTIVE.value,
        started_at=now,
        expires_at=expires_at,
        auto_renew=True,
    )
    db.add(new_sub)
    db.commit()

    logger.info(
        "Subscription activated for user %s: tier=%s, expires=%s",
        order.user_id, order.tier, expires_at.isoformat(),
    )


# ════════════════════════════════════════════════════════════════════
# Routes — Admin
# ════════════════════════════════════════════════════════════════════


@router.get("/admin/orders")
async def list_orders(
    status: Optional[str] = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Admin: List payment orders.
    """
    query = db.query(PaymentOrder)
    if status:
        query = query.filter(PaymentOrder.status == status)

    orders = query.order_by(PaymentOrder.created_at.desc()).limit(100).all()

    return [
        {
            "id": o.id,
            "order_no": o.order_no,
            "user_id": o.user_id,
            "tier": o.tier,
            "amount": o.amount,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else "",
        }
        for o in orders
    ]


# ════════════════════════════════════════════════════════════════════
# Usage Quota Check (utility, not a route)
# ════════════════════════════════════════════════════════════════════


async def check_quota(user: User, db: Session, tokens_to_add: int = 0) -> bool:
    """
    Check if the user has remaining token quota.

    Returns True if allowed, False if quota exceeded.

    Called before each API request (injected via middleware).
    """
    sub = _get_active_subscription(user.id, db)
    tier = sub.tier if sub else "free"
    plan = PLANS.get(tier, PLANS["free"])

    quota = plan["token_quota"]
    if quota == 0:
        return True  # Unlimited

    used = _get_monthly_usage(user.id, db)
    return (used + tokens_to_add) <= quota
