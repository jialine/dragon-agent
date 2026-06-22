# Dragon Agent — P1 模块技术规格

> P1 目标：从「可演示」到「可商用」— 用户认证、计费、API Key、置信度校准
> 集成点：所有模块顶层暴露 FastAPI Router，由统一 DragonAPI app 挂载

---

## 架构总览

```
                        ┌──────────────────────┐
                        │    DragonAPI (FastAPI) │
                        │    port 8780           │
                        └──────┬───────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
   ┌─────────────┐    ┌─────────────┐    ┌──────────────────┐
   │ /auth/*     │    │ /billing/*  │    │ /keys/*           │
   │ P1.1 Auth   │    │ P1.2 Billing│    │ P1.3 API Key Mgmt │
   └─────────────┘    └─────────────┘    └──────────────────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   SQLite (DragonDB)   │
                    │   users / subs / keys │
                    └──────────────────────┘

独立模块:
   ┌──────────────────────────┐
   │ P1.4 Confidence Calibrator │  ← 离线训练，无 HTTP 依赖
   └──────────────────────────┘
```

---

## 0. DragonAPI — FastAPI 应用骨架

### 0.1 设计决策

- **为什么是 FastAPI**：Python 生态首选，Pydantic 原生支持，自动 OpenAPI 文档
- **为什么是 SQLite**：零配置、单文件备份、与现有 SessionStore/Insights 一致
- **为什么不是 PostgreSQL**：MVP 阶段单机够用；SQLAlchemy ORM 抽象层可后续切换
- **端口 8780**：不与常见服务冲突

### 0.2 接口

```python
# dragon/api/app.py
from fastapi import FastAPI
from dragon.api import auth, billing, apikeys
from dragon.api.db import DragonDB

def create_app(db_path: str = "~/.dragon/server.db") -> FastAPI:
    app = FastAPI(title="Dragon Agent API", version="1.0.0")
    db = DragonDB(db_path)

    app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
    app.include_router(billing.router, prefix="/api/v1/billing", tags=["Billing"])
    app.include_router(apikeys.router, prefix="/api/v1/keys", tags=["API Keys"])

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    return app
```

### 0.3 目录

```
dragon/api/
├── __init__.py
├── app.py          # FastAPI app factory
├── db.py           # SQLAlchemy engine + session
├── models.py       # Shared Pydantic models (User, Subscription, ApiKey)
├── deps.py         # FastAPI dependencies (get_db, get_current_user)
├── auth.py         # P1.1
├── billing.py      # P1.2
├── apikeys.py      # P1.3
└── schema.sql      # 建表 DDL (备查)
```

### 0.4 数据库模型 (SQLAlchemy)

```python
# dragon/api/models.py
from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, ForeignKey, Enum
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime, timezone
import enum

class Base(DeclarativeBase):
    pass

class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"

class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True)          # UUID
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), default="user")
    oauth_provider = Column(String(20), nullable=True)  # "wechat" | "github" | None
    oauth_id = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)
    # relations
    subscriptions = relationship("Subscription", back_populates="user")
    api_keys = relationship("ApiKey", back_populates="user")

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    tier = Column(String(20), default="free")
    status = Column(String(20), default="active")  # active | cancelled | expired
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)
    auto_renew = Column(Boolean, default=False)
    # relations
    user = relationship("User", back_populates="subscriptions")

class ApiKey(Base):
    __tablename__ = "api_keys"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    key_prefix = Column(String(8), nullable=False)      # First 8 chars, stored plain
    key_hash = Column(String(64), nullable=False)       # SHA-256 hex
    name = Column(String(100), default="Default")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    # relations
    user = relationship("User", back_populates="api_keys")
```

### 0.5 依赖注入

```python
# dragon/api/deps.py
from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session
from dragon.api.db import get_db

def get_current_user(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> User:
    """Extract user from Bearer token (JWT or API Key)."""
    ...

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin required")
    return user
```

---

## 1. Auth (用户认证) — P1.1

### 1.1 接口

```python
# dragon/api/auth.py

from pydantic import BaseModel, EmailStr

class RegisterRequest(BaseModel):
    email: str                          # 邮箱
    password: str                       # 密码 (min 8 chars)
    phone: str | None = None            # 手机号 (可选, 中国市场)

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str                   # JWT
    refresh_token: str                  # 刷新令牌
    token_type: str = "bearer"
    expires_in: int = 3600              # 秒

class OAuthRequest(BaseModel):
    provider: str                       # "wechat" | "github"
    code: str                           # OAuth authorization code
    redirect_uri: str

class PasswordResetRequest(BaseModel):
    email: str

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str

# ── Router ──────────────────────────────────────────────────────────

router = APIRouter()

@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """注册新用户，返回 JWT token 对"""
    ...

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    """邮箱密码登录"""
    ...

@router.post("/oauth/{provider}", response_model=TokenResponse)
async def oauth_login(provider: str, req: OAuthRequest, db: Session = Depends(get_db)):
    """OAuth 登录 (微信/GitHub)"""
    ...

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(refresh_token: str = Body(...)):
    """用 refresh_token 换新 access_token"""
    ...

@router.post("/password/reset")
async def request_password_reset(req: PasswordResetRequest):
    """发送密码重置邮件"""
    ...

@router.post("/password/reset/confirm")
async def confirm_password_reset(req: PasswordResetConfirm, db: Session = Depends(get_db)):
    """确认密码重置"""
    ...

@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return {"id": user.id, "email": user.email, "role": user.role}
```

### 1.2 JWT 设计

```
Header:  {"alg": "HS256", "typ": "JWT"}
Payload: {
    "sub": "<user_id>",
    "email": "<email>",
    "role": "user|admin",
    "iat": 1717833600,
    "exp": 1717837200,        # +1h
    "type": "access"          # "access" | "refresh"
}
Secret:  DRAGON_JWT_SECRET 环境变量 (默认随机生成)
```

### 1.3 安全策略

| 项目 | 策略 |
|------|------|
| 密码哈希 | bcrypt (cost=12) |
| Token 有效期 | access: 1h, refresh: 30d |
| 速率限制 | /login: 5/分钟/IP; /register: 3/小时/IP |
| Token 轮换 | refresh 时旧 refresh_token 失效 |
| 密码强度 | 最少 8 字符，建议含大小写+数字 |

### 1.4 OAuth 流程 (微信)

```
1. 前端获取微信 code → POST /auth/oauth/wechat {code, redirect_uri}
2. 后端用 code 换 access_token + openid (微信 API)
3. 查找或创建用户 (by openid)
4. 返回 JWT
```

### 1.5 依赖

- `passlib[bcrypt]` — 密码哈希
- `python-jose[cryptography]` — JWT
- `httpx` — OAuth API 调用 (已有)
- `dragon.api.db` — SQLAlchemy session
- `dragon.rate_limiter` — 登录速率限制 (已有)

---

## 2. Billing (计费系统) — P1.2

### 2.1 接口

```python
# dragon/api/billing.py

class PlanInfo(BaseModel):
    tier: str                           # "free" | "pro" | "team" | "enterprise"
    name: str                           # 展示名
    price_monthly: float                # 月费 (元)
    price_yearly: float                 # 年费 (元)
    token_quota: int                    # 月 Token 配额 (0=无限)
    features: list[str]                 # 功能列表

class SubscribeRequest(BaseModel):
    tier: str                           # 订阅等级
    billing_cycle: str = "monthly"      # "monthly" | "yearly"

class PaymentResult(BaseModel):
    payment_url: str | None = None      # 支付链接 (H5/二维码)
    order_id: str
    status: str                         # "pending" | "paid" | "failed"

class SubscriptionInfo(BaseModel):
    tier: str
    status: str
    started_at: str
    expires_at: str | None
    tokens_used_this_month: int
    tokens_quota: int
    auto_renew: bool

class InvoiceInfo(BaseModel):
    invoice_id: str
    amount: float
    currency: str = "CNY"
    status: str
    created_at: str
    download_url: str | None

# ── Router ──────────────────────────────────────────────────────────

router = APIRouter()

@router.get("/plans", response_model=list[PlanInfo])
async def list_plans():
    """获取所有订阅计划"""
    ...

@router.post("/subscribe", response_model=PaymentResult)
async def subscribe(
    req: SubscribeRequest,
    user: User = Depends(get_current_user),
):
    """创建订阅订单 → 返回支付链接"""
    ...

@router.get("/subscription", response_model=SubscriptionInfo)
async def get_subscription(user: User = Depends(get_current_user)):
    """获取当前订阅状态"""
    ...

@router.post("/subscription/cancel")
async def cancel_subscription(user: User = Depends(get_current_user)):
    """取消自动续费"""
    ...

@router.get("/invoices", response_model=list[InvoiceInfo])
async def list_invoices(user: User = Depends(get_current_user)):
    """获取发票列表"""
    ...

@router.post("/payment/notify")
async def payment_notify(req: Request):
    """支付回调 (支付宝/微信异步通知)"""
    ...
```

### 2.2 订阅套餐

| | Free | Pro | Team | Enterprise |
|---|---|---|---|---|
| 月费 (¥) | 0 | 29 | 99 | 议价 |
| 年费 (¥) | 0 | 290 | 990 | 议价 |
| Token/月 | 10K | 500K | 3M | 无限 |
| 模型 | 基础 | 全部 | 全部 | 全部 |
| 并发 | 1 | 3 | 10 | 自定义 |
| 历史记录 | 7天 | 30天 | 90天 | 永久 |
| API Key | 1个 | 5个 | 20个 | 自定义 |
| 技术支持 | 社区 | 邮件 | 优先 | 专属 |

### 2.3 支付集成

```
支付流程:
  1. POST /billing/subscribe {tier: "pro", billing_cycle: "monthly"}
  2. 后端生成订单 (order_id, 金额, 用户)
  3. 调用支付网关 → 获取 payment_url (H5) 或 QR code
  4. 用户支付 → 支付网关 POST /billing/payment/notify
  5. 后端验签 → 更新 Subscription → 返回成功
```

**MVP 阶段**：先做支付宝/微信支付的沙箱对接。生产环境需要商户资质。

### 2.4 用量追踪

```python
# 每次 API 调用后:
async def track_usage(user: User, tokens: int):
    """写入 SQLite usage_log 表"""
    ...

async def check_quota(user: User) -> bool:
    """检查用户是否超出月配额"""
    current_usage = await get_monthly_usage(user.id)
    quota = get_user_quota(user)
    return current_usage < quota or quota == 0  # 0 = 无限
```

### 2.5 依赖

- `dragon.api.db` — SQLAlchemy
- `dragon.usage_pricing` — Token 定价 (已有)
- `dragon.insights` — 用量统计 (已有)
- 支付宝/微信支付 SDK (pip: `alipay-python-sdk`, `wechatpayv3`)

---

## 3. API Key Management — P1.3

### 3.1 接口

```python
# dragon/api/apikeys.py

class CreateKeyRequest(BaseModel):
    name: str = "Default"

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str                     # 前8位 (dk-xxxx...)
    full_key: str                       # 完整 Key (仅创建时返回一次!)
    created_at: str
    is_active: bool

class ApiKeyInfo(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    is_active: bool

class UsageStats(BaseModel):
    key_id: str
    tokens_today: int
    tokens_this_month: int
    requests_today: int
    requests_this_month: int

# ── Router ──────────────────────────────────────────────────────────

router = APIRouter()

@router.post("/", response_model=ApiKeyResponse, status_code=201)
async def create_key(
    req: CreateKeyRequest,
    user: User = Depends(get_current_user),
):
    """创建新 API Key — 返回完整 Key (仅此一次)"""
    ...

@router.get("/", response_model=list[ApiKeyInfo])
async def list_keys(user: User = Depends(get_current_user)):
    """列出用户的所有 API Key"""
    ...

@router.post("/{key_id}/revoke")
async def revoke_key(
    key_id: str,
    user: User = Depends(get_current_user),
):
    """撤销 API Key (立即失效)"""
    ...

@router.post("/{key_id}/rotate", response_model=ApiKeyResponse)
async def rotate_key(
    key_id: str,
    user: User = Depends(get_current_user),
):
    """轮换 Key — 撤销旧 Key，创建新 Key"""
    ...

@router.get("/{key_id}/stats", response_model=UsageStats)
async def key_stats(
    key_id: str,
    user: User = Depends(get_current_user),
):
    """查看 Key 用量统计"""
    ...
```

### 3.2 Key 格式

```
dragon_v1_<32_random_hex>

示例: dragon_v1_a3f8c2b1d4e5f6a7b8c9d0e1f2a3b4c5

存储:
  - key_prefix = "dragon_v1_a3f8c2b1" (前16 chars，用于识别)
  - key_hash   = SHA-256(full_key).hex()    (64 chars)
```

### 3.3 Key 鉴权流程

```python
# dragon/api/deps.py
async def get_current_user_from_key(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> User:
    """
    支持两种鉴权方式:
      1. Bearer <JWT_token>   → 用户直接操作
      2. Bearer <API_key>     → API 调用
    """
    token = authorization.removeprefix("Bearer ")

    # 1. Try JWT
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        return db.get(User, payload["sub"])
    except JWTError:
        pass

    # 2. Try API Key
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    api_key = db.query(ApiKey).filter(
        ApiKey.key_hash == key_hash,
        ApiKey.is_active == True,
    ).first()

    if not api_key:
        raise HTTPException(401, "Invalid token or API key")

    # Update last_used_at
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()

    return api_key.user
```

### 3.4 速率限制接入

```python
# 在 deps.py 中整合 rate_limiter
from dragon.rate_limiter import RateLimiter

async def check_rate_limit(
    user: User = Depends(get_current_user),
    limiter: RateLimiter = Depends(get_rate_limiter),
):
    """
    按订阅等级施加速率限制:
      - free:    10 req/min
      - pro:     60 req/min
      - team:    300 req/min
      - enterprise: 无限制
    """
    rpm_map = {"free": 10, "pro": 60, "team": 300, "enterprise": 0}
    rpm = rpm_map.get(user.subscription_tier, 10)

    if rpm == 0:
        return  # 无限制

    if not await limiter.acquire(f"user:{user.id}", rpm=rpm):
        raise HTTPException(429, "Rate limit exceeded")
```

### 3.5 依赖

- `dragon.rate_limiter` — 令牌桶 (已有, 631 LOC)
- `dragon.api.db` — SQLAlchemy
- `dragon.api.auth` — JWT 验证

---

## 4. Confidence Calibration (置信度校准) — P1.4

### 4.1 问题定义

```
模型自评："我的置信度是 95%"
实际准确率: 70%

→ 需要校准函数: P(actual_correct | model_confidence) → calibrated_confidence
```

### 4.2 接口

```python
# dragon/confidence.py

@dataclass
class CalibrationPoint:
    model_confidence: float      # 模型自评 (0-1)
    actual_correct: bool          # 实际上是否正确
    claim_text: str
    model_name: str
    timestamp: float

@dataclass
class CalibrationResult:
    raw_confidence: float         # 原始置信度
    calibrated: float             # 校准后置信度
    method: str                   # "platt" | "isotonic" | "none"
    expected_accuracy: float      # 期望准确率 (ECE)
    calibration_gap: float        # raw - expected_accuracy

class ConfidenceCalibrator:
    """置信度校准器 — 将模型自评置信度映射到真实准确率"""

    def __init__(self, model_path: str = "~/.dragon/calibration/"):
        self.calibration_points: list[CalibrationPoint] = []
        self._platt_params: tuple[float, float] | None = None   # (A, B)
        self._isotonic_bins: list[tuple[float, float]] | None = None

    def record(
        self,
        model_confidence: float,
        actual_correct: bool,
        claim_text: str,
        model_name: str,
    ) -> None:
        """
        记录一次预测结果，累积校准数据。
        每次 FactChecker 验证后自动调用。
        """
        ...

    def calibrate(
        self,
        raw_confidence: float,
        method: str = "auto",
    ) -> CalibrationResult:
        """
        将模型自评置信度映射到校准后置信度。

        method:
          - "auto": 样本 > 1000 用 isotonic，否则用 platt
          - "platt": Platt Scaling (sigmoid)
          - "isotonic": Isotonic Regression
          - "none": 不校准
        """
        ...

    def fit(self, method: str = "platt") -> None:
        """用累积的校准数据重新拟合参数"""
        ...

    def expected_calibration_error(self) -> float:
        """计算 ECE (Expected Calibration Error)"""
        ...

    def save(self) -> None:
        """保存校准参数到磁盘 (JSON)"""
        ...

    def load(self) -> None:
        """从磁盘加载校准参数"""
        ...
```

### 4.3 Platt Scaling

```
算法:
  P(correct | confidence) = 1 / (1 + exp(-(A * logit(confidence) + B)))

步骤:
  1. 将 confidence 转为 logit: z = log(confidence / (1 - confidence))
  2. 对 (z, actual_correct) 拟合逻辑回归
  3. 拟合参数 (A, B)

实现 (sklearn):
  from sklearn.linear_model import LogisticRegression
  X = [[logit(c)] for c in model_confidences]
  y = [1 if correct else 0 for correct in actual_corrects]
  lr = LogisticRegression().fit(X, y)
  A, B = lr.coef_[0][0], lr.intercept_[0]
```

### 4.4 Isotonic Regression

```
算法:
  非参数方法，学习单调递增函数 f: confidence → accuracy

步骤:
  1. 按 confidence 排序
  2. PAV (Pool Adjacent Violators) 算法 → 分段常数函数
  3. 插值连续值

实现 (sklearn):
  from sklearn.isotonic import IsotonicRegression
  ir = IsotonicRegression(out_of_bounds='clip').fit(X, y)
```

### 4.5 ECE 计算

```
ECE = Σ (|B_b| / N) * |acc(B_b) - conf(B_b)|

  其中:
    - 将 [0,1] 分为 M=10 个等宽 bin
    - B_b: 落入第 b 个 bin 的样本集合
    - acc(B_b): 该 bin 的实际准确率
    - conf(B_b): 该 bin 的平均置信度
```

### 4.6 集成点

```python
# 在 Consensus 输出中应用校准:
class ConsensusBuilder:
    def __init__(self, fact_checker: FactChecker, calibrator: ConfidenceCalibrator):
        ...

    async def build(self, verdict: JuryVerdict, question: str) -> ConsensusResult:
        # ... 验证声明 ...

        # 校准模型自评置信度
        calibrated = self.calibrator.calibrate(verdict.confidence)

        return ConsensusResult(
            ...
            confidence=calibrated.calibrated,
            calibration_gap=calibrated.calibration_gap,
        )
```

### 4.7 冷启动策略

```
阶段 1 (0-100 样本): 不校准 — 返回原始置信度，标记 "[置信度未经校准]"
阶段 2 (100-1000 样本): Platt Scaling — 低成本，快速拟合
阶段 3 (>1000 样本): Isotonic Regression — 更精确的非参数方法
```

### 4.8 依赖

- `scikit-learn` — LogisticRegression, IsotonicRegression
- `numpy` — 基础计算 (已有)
- `dragon.factcheck` — 验证结果供给校准数据 (P0 已完成)
- `dragon.consensus` — 消费校准结果 (P0 已完成)

---

## 5. 开发顺序

| 顺序 | 模块 | 依赖 | 预估 LOC |
|:--:|------|------|:--:|
| 0 | **DragonAPI 骨架** (app, db, models, deps) | 无 | 400 |
| 1 | **Auth** | API 骨架 + rate_limiter | 1,000 |
| 2 | **API Key Management** | Auth + rate_limiter | 800 |
| 3 | **Billing** | API 骨架 + usage_pricing | 1,500 |
| 4 | **Confidence Calibration** | scikit-learn + factcheck | 600 |

**总计: ~4,300 LOC**

### 里程碑

```
Week 5-6:  DragonAPI 骨架 + Auth + API Key
          → 🎯 可内测 (邀请制，API Key 鉴权)

Week 7-8:  Billing + Confidence Calibration
          → 🎯 可公测收费
```

---

## 6. 新增依赖

```toml
# 需添加到项目依赖:
fastapi = "^0.115"
uvicorn = "^0.34"
sqlalchemy = "^2.0"
passlib[bcrypt] = "^1.7"
python-jose[cryptography] = "^3.3"
python-multipart = "^0.0"          # FastAPI OAuth form
scikit-learn = "^1.5"               # 置信度校准
```
