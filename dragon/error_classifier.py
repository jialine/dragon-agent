"""
Dragon Agent — Error Classifier
===============================

Classifies API errors from LLM providers to enable smart failover,
automatic recovery, and user-friendly error messages in Chinese.

Architecture::

    classify_api_error()
        │
        ├── 1. Extract HTTP status code from exception
        ├── 2. Extract structured error body (JSON or string)
        ├── 3. Run priority-ordered classification pipeline:
        │      a. Status code → broad category
        │      b. Error code / type field from body
        │      c. Message pattern matching (billing, rate-limit, context, auth, …)
        │      d. Transport error heuristics (timeout, connection)
        │      e. Fallback → unknown (retryable)
        └── 4. Return ClassifiedError with recovery action hints
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("dragon.error_classifier")


# ══════════════════════════════════════════════════════════════════════
# Error Taxonomy
# ══════════════════════════════════════════════════════════════════════


class ErrorCategory(enum.Enum):
    """Top-level error category — determines high-level recovery strategy."""

    retryable = "retryable"            # Transient error, safe to retry with backoff
    auth = "auth"                      # Authentication failure — rotate credential
    auth_permanent = "auth_permanent"  # Auth permanently revoked — abort
    billing = "billing"                # Insufficient credits / quota — switch provider
    rate_limit = "rate_limit"          # Rate limited (429) — backoff + retry or rotate
    server_error = "server_error"      # Provider-side failure (5xx) — retry or fallback
    overloaded = "overloaded"          # Provider overloaded (503/529) — backoff then rotate
    timeout = "timeout"                # Request timed out — rebuild client + retry
    context_overflow = "context_overflow"  # Context window exceeded — compress
    payload_too_large = "payload_too_large"  # Request body too large (413)
    model_not_found = "model_not_found"    # Invalid or unknown model name
    format_error = "format_error"          # Malformed request (400) — abort or strip + retry
    connection_error = "connection_error"  # Network-level failure
    unknown = "unknown"                    # Unclassifiable — retry with backoff


# ── Pattern libraries ────────────────────────────────────────────────────

# Patterns indicating billing/credit exhaustion
_BILLING_PATTERNS: List[str] = [
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "exceeded your current quota",
    "account is deactivated",
    "plan does not include",
    "充值",
    "余额不足",
    "配额已用尽",
    "credit limit reached",
    "quota exceeded",
]

# Patterns indicating transient rate limiting
_RATE_LIMIT_PATTERNS: List[str] = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "requests per day",
    "try again in",
    "please retry after",
    "resource_exhausted",
    "rate increased too quickly",
    "throttlingexception",
    "too many concurrent requests",
    "servicequotaexceededexception",
    "访问频率",
    "请求过于频繁",
    "限流",
]

# Context window overflow patterns
_CONTEXT_OVERFLOW_PATTERNS: List[str] = [
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "reduce the length",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "prompt exceeds max length",
    "maximum number of tokens",
    "exceeds the max_model_len",
    "max_model_len",
    "prompt length",
    "input is too long",
    "maximum model length",
    "context length exceeded",
    "truncating input",
    "slot context",
    "n_ctx_slot",
    "max input token",
    "input token",
    "超过最大长度",
    "上下文长度",
    "令牌数量超出",
    "exceeds the maximum number of input tokens",
]

# Auth failure patterns
_AUTH_PATTERNS: List[str] = [
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid token",
    "token expired",
    "token revoked",
    "access denied",
    "api key not valid",
    "api key not found",
    "认证失败",
    "密钥无效",
]

# Model not found patterns
_MODEL_NOT_FOUND_PATTERNS: List[str] = [
    "is not a valid model",
    "invalid model",
    "model not found",
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "unsupported model",
    "模型不存在",
    "模型无效",
]

# Transport error type names
_TRANSPORT_ERROR_TYPES: frozenset = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError",
    "TimeoutError", "ReadError",
    "ServerDisconnectedError",
    "SSLError", "SSLZeroReturnError",
    "APIConnectionError", "APITimeoutError",
})

# Timeout message patterns (for non-transport exceptions)
_TIMEOUT_MESSAGE_PATTERNS: List[str] = [
    "timed out",
    "turn timed out",
    "request timed out",
    "deadline exceeded",
    "operation timed out",
    "upstream timed out",
    "请求超时",
    "连接超时",
]

# Server error patterns (overloaded / unavailable)
_SERVER_ERROR_PATTERNS: List[str] = [
    "service unavailable",
    "server error",
    "internal server error",
    "bad gateway",
    "temporarily unavailable",
    "maintenance",
    "服务不可用",
    "服务器错误",
]


# ══════════════════════════════════════════════════════════════════════
# Result Data Class
# ══════════════════════════════════════════════════════════════════════


@dataclass
class ClassifiedError:
    """Structured classification of an API error with recovery hints.

    Attributes:
        category: The error category from the taxonomy.
        status_code: HTTP status code if available.
        provider: Provider name (e.g. ``"openai"``, ``"deepseek"``).
        model: Model slug (e.g. ``"gpt-4o"``).
        message: Human-readable error message.
        raw_message: Original exception message.
        retryable: Whether the error is safe to retry.
        should_rotate_credential: Whether to try a different API key.
        should_fallback: Whether to switch to a fallback provider.
        should_compress: Whether context compression might help.
        recovery_suggestion: Human-readable recovery guidance in Chinese.
        error_context: Additional diagnostic context (dict).
    """

    category: ErrorCategory = ErrorCategory.unknown
    status_code: Optional[int] = None
    provider: str = ""
    model: str = ""
    message: str = ""
    raw_message: str = ""
    retryable: bool = True
    should_rotate_credential: bool = False
    should_fallback: bool = False
    should_compress: bool = False
    recovery_suggestion: str = ""
    error_context: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════
# Recovery Messages (Chinese)
# ══════════════════════════════════════════════════════════════════════

_RECOVERY_MESSAGES: Dict[ErrorCategory, str] = {
    ErrorCategory.retryable: "临时性错误，系统将自动重试。如持续失败，请检查网络连接。",
    ErrorCategory.auth: "API 密钥无效或已过期，请检查密钥配置。正在尝试切换备用密钥…",
    ErrorCategory.auth_permanent: "API 密钥已被永久撤销，请前往服务商后台重新生成密钥，并在配置中更新。",
    ErrorCategory.billing: "账户余额不足或配额已用尽，请充值或等待配额重置。系统将自动切换到备用服务商。",
    ErrorCategory.rate_limit: "请求频率超限，系统将自动等待后重试。如频繁触发，请考虑切换模型或调整调用频率。",
    ErrorCategory.server_error: "服务商服务器异常，系统将自动重试。如持续失败，将切换到备用服务商。",
    ErrorCategory.overloaded: "服务商过载，系统将等待后重试。如持续过载，将自动切换到备用服务商。",
    ErrorCategory.timeout: "请求超时，系统将自动重试。如持续超时，请检查网络或切换服务商。",
    ErrorCategory.context_overflow: "上下文超出模型限制，系统将自动压缩历史会话后重试。",
    ErrorCategory.payload_too_large: "请求体过大，请缩短消息或减少附件。",
    ErrorCategory.model_not_found: "指定的模型不存在，请检查模型名称是否正确。系统将尝试使用备用模型。",
    ErrorCategory.format_error: "请求格式错误，系统将尝试修正后重试。",
    ErrorCategory.connection_error: "网络连接异常，系统将自动重试。请检查网络状况。",
    ErrorCategory.unknown: "未知错误，系统将自动重试。如持续失败，请查看日志排查。",
}

# Status code → category mapping
_STATUS_CODE_CATEGORY: Dict[int, ErrorCategory] = {
    400: ErrorCategory.format_error,
    401: ErrorCategory.auth,
    402: ErrorCategory.billing,
    403: ErrorCategory.auth,
    404: ErrorCategory.model_not_found,
    408: ErrorCategory.timeout,
    413: ErrorCategory.payload_too_large,
    429: ErrorCategory.rate_limit,
    500: ErrorCategory.server_error,
    502: ErrorCategory.server_error,
    503: ErrorCategory.overloaded,
    504: ErrorCategory.timeout,
    529: ErrorCategory.overloaded,
}


# ══════════════════════════════════════════════════════════════════════
# Classification Pipeline
# ══════════════════════════════════════════════════════════════════════


def classify_api_error(
    error: Union[Exception, str],
    *,
    provider: str = "",
    model: str = "",
    status_code: Optional[int] = None,
    approx_tokens: int = 0,
    context_length: int = 200000,
) -> ClassifiedError:
    """Classify an API error into a structured recovery recommendation.

    Priority-ordered classification pipeline:
        1. Explicit status_code if provided
        2. HTTP status code extracted from exception attributes
        3. Error type name heuristics (transport errors)
        4. Message pattern matching (billing, rate-limit, context, auth, …)
        5. Fallback: ``ErrorCategory.unknown`` (retryable)

    Args:
        error: The exception from the API call, or a raw error string.
        provider: Current provider name (e.g. ``"openai"``).
        model: Current model slug.
        status_code: Explicit HTTP status code (overrides extraction).
        approx_tokens: Approximate token count for context-overflow detection.
        context_length: Model's maximum context window size.

    Returns:
        :class:`ClassifiedError` with category and recovery hints.

    Example::

        from dragon.error_classifier import classify_api_error

        try:
            await provider.complete(...)
        except Exception as exc:
            classified = classify_api_error(
                exc, provider="deepseek", model="deepseek-chat",
            )
            if classified.should_fallback:
                await fallback_provider.complete(...)
    """
    # Normalize input
    if isinstance(error, str):
        error_obj: Any = Exception(error)
        raw_msg = error
    else:
        error_obj = error
        raw_msg = str(error)

    error_type = type(error_obj).__name__
    error_msg = raw_msg.lower()

    # ├─ 1. Extract status code ──────────────────────────────────────
    sc = status_code or _extract_status_code(error_obj)
    body = _extract_error_body(error_obj)
    error_code = _extract_error_code(body)

    # Combine all message sources for pattern matching
    combined_msg = _build_combined_message(error_msg, body)

    # Helper to build result
    def _result(cat: ErrorCategory, **overrides) -> ClassifiedError:
        kwargs: Dict[str, Any] = {
            "category": cat,
            "status_code": sc,
            "provider": provider,
            "model": model,
            "message": _extract_message(error_obj, body),
            "raw_message": raw_msg,
            "recovery_suggestion": _RECOVERY_MESSAGES.get(cat, _RECOVERY_MESSAGES[ErrorCategory.unknown]),
        }
        kwargs.update(overrides)
        return ClassifiedError(**kwargs)

    # ├─ 2. Status code → broad category (if status code is clear) ──
    if sc is not None and sc in _STATUS_CODE_CATEGORY:
        base_cat = _STATUS_CODE_CATEGORY[sc]

        # Refine 400 — could be format_error OR context_overflow
        if sc == 400:
            if _matches_any(combined_msg, _CONTEXT_OVERFLOW_PATTERNS):
                return _result(ErrorCategory.context_overflow, should_compress=True)
            if _matches_any(combined_msg, _AUTH_PATTERNS):
                return _result(ErrorCategory.auth, should_rotate_credential=True)
            return _result(base_cat)

        # Refine 429 — could be rate_limit OR billing (usage exhausted)
        if sc == 429:
            if _matches_any(combined_msg, _BILLING_PATTERNS):
                return _result(ErrorCategory.billing,
                               retryable=False, should_fallback=True)
            return _result(base_cat)

        # Refine 401/403 — permanent vs transient
        if sc in (401, 403):
            if "expired" in combined_msg:
                return _result(
                    ErrorCategory.auth,
                    retryable=True, should_rotate_credential=True,
                )
            if "revoked" in combined_msg or "permanently" in combined_msg:
                return _result(
                    ErrorCategory.auth_permanent,
                    retryable=False, should_rotate_credential=False,
                )
            return _result(base_cat, should_rotate_credential=True)

        # Refine 402 — billing (should trigger provider fallback)
        if sc == 402:
            return _result(ErrorCategory.billing,
                           retryable=False, should_fallback=True)

        # Refine 408/504 — timeout
        if sc in (408, 504):
            return _result(ErrorCategory.timeout)

        return _result(base_cat)

    # ├─ 3. No status code — type-based heuristics ──────────────────
    if error_type in _TRANSPORT_ERROR_TYPES:
        return _result(ErrorCategory.timeout)

    # Check timeout patterns in message
    if _matches_any(combined_msg, _TIMEOUT_MESSAGE_PATTERNS):
        return _result(ErrorCategory.timeout)

    # Check connection patterns
    if _matches_any(combined_msg, [
        "connection refused", "connection reset", "no route to host",
        "network is unreachable", "name resolution", "dns",
    ]):
        return _result(ErrorCategory.connection_error)

    # ├─ 4. Message pattern matching ────────────────────────────────
    if _matches_any(combined_msg, _BILLING_PATTERNS):
        return _result(ErrorCategory.billing,
                       retryable=False, should_fallback=True)

    if _matches_any(combined_msg, _RATE_LIMIT_PATTERNS):
        return _result(ErrorCategory.rate_limit)

    if _matches_any(combined_msg, _CONTEXT_OVERFLOW_PATTERNS):
        return _result(ErrorCategory.context_overflow, should_compress=True)

    if _matches_any(combined_msg, _AUTH_PATTERNS):
        return _result(ErrorCategory.auth, should_rotate_credential=True)

    if _matches_any(combined_msg, _MODEL_NOT_FOUND_PATTERNS):
        return _result(ErrorCategory.model_not_found,
                       retryable=False, should_fallback=True)

    if _matches_any(combined_msg, _SERVER_ERROR_PATTERNS):
        return _result(ErrorCategory.server_error)

    # ├─ 5. Error code from body ────────────────────────────────────
    if error_code:
        result = _classify_by_error_code(error_code, sc)
        if result:
            return _result(result)

    # ├─ 6. Context overflow heuristic (no status code) ─────────────
    if approx_tokens > 0 and context_length > 0:
        if approx_tokens > context_length * 0.85:
            if _matches_any(combined_msg, [
                "server disconnected", "peer closed connection",
                "incomplete chunked read",
            ]):
                return _result(ErrorCategory.context_overflow, should_compress=True)

    # ├─ 7. Fallback ────────────────────────────────────────────────
    return _result(ErrorCategory.unknown)


# ══════════════════════════════════════════════════════════════════════
# Extraction Helpers
# ══════════════════════════════════════════════════════════════════════


def _extract_status_code(error: Any) -> Optional[int]:
    """Extract HTTP status code from an exception object.

    Checks common attributes: status_code, status, http_status, response.status_code.
    """
    if isinstance(error, int):
        return error

    for attr in ("status_code", "status", "http_status"):
        val = getattr(error, attr, None)
        if isinstance(val, int) and 100 <= val < 600:
            return val

    # Check nested response object
    response = getattr(error, "response", None)
    if response is not None:
        sc = getattr(response, "status_code", None)
        if isinstance(sc, int) and 100 <= sc < 600:
            return sc

    return None


def _extract_error_body(error: Any) -> Optional[Dict[str, Any]]:
    """Extract structured error body from an exception.

    Checks: body (dict), response.json(), response.text → JSON.
    """
    # Direct body attribute (httpx, some SDKs)
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body

    # Response with JSON body
    response = getattr(error, "response", None)
    if response is not None:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        # Try text body parsed as JSON
        text = getattr(response, "text", "") or ""
        if text:
            import json as _json
            try:
                data = _json.loads(text)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass

    # Try parsing str(error) as JSON (common in OpenAI SDK)
    raw = str(error)
    try:
        import json as _json
        data = _json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return None


def _extract_error_code(body: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract structured error code from API error body.

    Checks: error.code, error.type, code, type fields.
    """
    if not body:
        return None

    # OpenAI-style: {"error": {"code": "...", "type": "..."}}
    error_obj = body.get("error", {})
    if isinstance(error_obj, dict):
        code = error_obj.get("code")
        if isinstance(code, str) and code:
            return code
        type_ = error_obj.get("type")
        if isinstance(type_, str) and type_:
            return type_

    # Simple: {"code": "...", "type": "..."}
    for key in ("code", "type", "error_code", "error_type"):
        val = body.get(key)
        if isinstance(val, str) and val:
            return val

    return None


def _extract_message(error: Any, body: Optional[Dict[str, Any]]) -> str:
    """Extract human-readable error message."""
    if isinstance(error, str):
        return error

    # Try error body message
    if body:
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            msg = error_obj.get("message", "")
            if msg:
                return str(msg)
        msg = body.get("message", "")
        if msg:
            return str(msg)

    # Fall back to str(error)
    msg = str(error)
    # Truncate extremely long messages
    if len(msg) > 500:
        msg = msg[:497] + "..."
    return msg


def _build_combined_message(raw_msg: str, body: Optional[Dict[str, Any]]) -> str:
    """Combine raw error message with body message for comprehensive matching."""
    parts = [raw_msg.lower()]
    if body:
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            body_msg = str(error_obj.get("message", "")).lower()
            if body_msg and body_msg not in parts[0]:
                parts.append(body_msg)
        else:
            body_msg = str(body.get("message", "")).lower()
            if body_msg and body_msg not in parts[0]:
                parts.append(body_msg)
    return " ".join(parts)


def _matches_any(text: str, patterns: List[str]) -> bool:
    """Check if any pattern (case-insensitive) appears in text."""
    text_lower = text.lower()
    return any(p.lower() in text_lower for p in patterns)


def _classify_by_error_code(
    error_code: str, status_code: Optional[int]
) -> Optional[ErrorCategory]:
    """Map well-known API error codes to categories."""
    code_map: Dict[str, ErrorCategory] = {
        "context_length_exceeded": ErrorCategory.context_overflow,
        "context_too_large": ErrorCategory.context_overflow,
        "token_limit_exceeded": ErrorCategory.context_overflow,
        "insufficient_quota": ErrorCategory.billing,
        "rate_limit_exceeded": ErrorCategory.rate_limit,
        "invalid_api_key": ErrorCategory.auth,
        "authentication_error": ErrorCategory.auth,
        "invalid_request_error": ErrorCategory.format_error,
        "server_error": ErrorCategory.server_error,
        "service_unavailable": ErrorCategory.overloaded,
        "model_not_found": ErrorCategory.model_not_found,
        "timeout": ErrorCategory.timeout,
        "api_connection_error": ErrorCategory.connection_error,
    }
    return code_map.get(error_code.lower())


# ══════════════════════════════════════════════════════════════════════
# High-Level Convenience Functions
# ══════════════════════════════════════════════════════════════════════


def is_retryable(
    error: Union[Exception, str],
    provider: str = "",
    model: str = "",
) -> bool:
    """Quick check: should this error be retried?

    Returns ``True`` for transient errors (rate limits, server errors, timeouts);
    ``False`` for fatal errors (billing, permanent auth, model not found).

    Example::

        for attempt in range(3):
            try:
                return await provider.complete(...)
            except Exception as exc:
                if not is_retryable(exc):
                    raise  # Don't waste retries on fatal errors
                await asyncio.sleep(2 ** attempt)
    """
    classified = classify_api_error(error, provider=provider, model=model)
    return classified.retryable


def get_recovery_action(
    error: Union[Exception, str],
    provider: str = "",
    model: str = "",
) -> str:
    """Get a human-readable Chinese recovery suggestion for an error.

    Example::

        try:
            await provider.complete(...)
        except Exception as exc:
            suggestion = get_recovery_action(exc, provider="openai")
            print(f"错误: {suggestion}")
    """
    classified = classify_api_error(error, provider=provider, model=model)
    return classified.recovery_suggestion


def format_chinese_error(
    error: Union[Exception, str],
    provider: str = "",
    model: str = "",
) -> str:
    """Format a user-friendly Chinese error message.

    Returns a multi-line string with:
        - Error summary (in Chinese)
        - Recovery suggestion
        - Technical details (provider + model)

    Example::

        try:
            await provider.complete(...)
        except Exception as exc:
            print(format_chinese_error(exc, provider="deepseek"))
    """
    classified = classify_api_error(error, provider=provider, model=model)

    category_labels: Dict[ErrorCategory, str] = {
        ErrorCategory.retryable: "临时错误",
        ErrorCategory.auth: "认证失败",
        ErrorCategory.auth_permanent: "密钥失效",
        ErrorCategory.billing: "余额不足",
        ErrorCategory.rate_limit: "频率限制",
        ErrorCategory.server_error: "服务器错误",
        ErrorCategory.overloaded: "服务过载",
        ErrorCategory.timeout: "请求超时",
        ErrorCategory.context_overflow: "上下文溢出",
        ErrorCategory.payload_too_large: "请求过大",
        ErrorCategory.model_not_found: "模型不存在",
        ErrorCategory.format_error: "请求格式错误",
        ErrorCategory.connection_error: "网络异常",
        ErrorCategory.unknown: "未知错误",
    }

    label = category_labels.get(classified.category, "未知错误")
    parts = [f"🐉 Dragon Agent — {label}"]

    if classified.status_code:
        parts.append(f"HTTP 状态码: {classified.status_code}")

    parts.append(f"错误信息: {classified.message or str(error)}")

    if classified.recovery_suggestion:
        parts.append(f"\n💡 建议: {classified.recovery_suggestion}")

    tech_parts = []
    if provider:
        tech_parts.append(f"服务商: {provider}")
    if model:
        tech_parts.append(f"模型: {model}")
    if tech_parts:
        parts.append(f"\n🔧 技术信息: {', '.join(tech_parts)}")

    return "\n".join(parts)
