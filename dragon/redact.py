"""
Dragon Agent — Privacy Redactor
===============================

Regex-based PII and secret redaction for logs, tool output, and
provider responses. Applies pattern matching to mask sensitive data
before it reaches log files, verbose output, or gateway messages.

Provides:

1. **PII Detection** — phone numbers (CN/US), emails, ID cards, IPs, API keys
2. **Redaction** — replace with ``[REDACTED:type]`` markers
3. **Industry Rules** — configurable redaction rules per vertical
   (finance: redact amounts, medical: redact names/DOB)
4. **Log Sanitisation** — ``RedactingFormatter`` for Python logging
5. **Secret Masking** — known API key prefixes (sk-, ghp_, AIza, etc.)

Ported from Hermes Agent's ``agent/redact.py`` with additional
PRC-specific PII patterns for the Dragon ecosystem.

Usage::

    from dragon.redact import redact_sensitive_text, RedactingFormatter

    safe = redact_sensitive_text("My email is user@example.com")
    # → "My email is [REDACTED:email]"

    # Log sanitisation
    handler.setFormatter(RedactingFormatter("%(message)s"))
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────
# Redaction enabled flag (snapshot at import time)
# ────────────────────────────────────────────────────────────────────

_REDACT_ENABLED = os.getenv(
    "DRAGON_REDACT_SECRETS", "true"
).lower() in {"1", "true", "yes", "on"}


# ────────────────────────────────────────────────────────────────────
# Sensitive key names (for query-string / JSON redaction)
# ────────────────────────────────────────────────────────────────────

_SENSITIVE_QUERY_PARAMS: FrozenSet[str] = frozenset({
    "access_token", "refresh_token", "id_token", "token",
    "api_key", "apikey", "client_secret", "password", "auth",
    "jwt", "session", "secret", "key",
    "code",           # OAuth authorization codes
    "signature",      # pre-signed URL signatures
    "x-amz-signature",
})

_SENSITIVE_BODY_KEYS: FrozenSet[str] = frozenset({
    "access_token", "refresh_token", "id_token", "token",
    "api_key", "apikey", "client_secret", "password", "auth",
    "jwt", "secret", "private_key", "authorization", "key",
})


# ────────────────────────────────────────────────────────────────────
# PII Patterns (international)
# ────────────────────────────────────────────────────────────────────

# Email addresses
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# US phone numbers: (123) 456-7890, 123-456-7890, +1-123-456-7890
_US_PHONE_RE = re.compile(
    r"(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b"
    r"|(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)

# Chinese mainland mobile: 1[3-9]\d{9}
_CN_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")

# Chinese ID card (18-digit, last digit can be X)
_CN_ID_CARD_RE = re.compile(
    r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"
)

# IPv4 addresses
_IPV4_RE = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\."
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

# Credit card numbers (basic: 13-19 digits, may have spaces/dashes)
_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d[ -]*?){12,18}\d\b"
)


# ────────────────────────────────────────────────────────────────────
# Known API key prefix patterns
# ────────────────────────────────────────────────────────────────────

_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",           # OpenAI / Anthropic / OpenRouter
    r"ghp_[A-Za-z0-9]{10,}",            # GitHub PAT (classic)
    r"github_pat_[A-Za-z0-9_]{10,}",    # GitHub PAT (fine-grained)
    r"gho_[A-Za-z0-9]{10,}",            # GitHub OAuth
    r"ghu_[A-Za-z0-9]{10,}",            # GitHub user-to-server
    r"ghs_[A-Za-z0-9]{10,}",            # GitHub server-to-server
    r"ghr_[A-Za-z0-9]{10,}",            # GitHub refresh token
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack tokens
    r"AIza[A-Za-z0-9_-]{30,}",          # Google API keys
    r"pplx-[A-Za-z0-9]{10,}",           # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",          # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",             # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",      # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",        # Codex encrypted tokens
    r"AKIA[A-Z0-9]{16}",                # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",        # Stripe live secret
    r"sk_test_[A-Za-z0-9]{10,}",        # Stripe test secret
    r"rk_live_[A-Za-z0-9]{10,}",        # Stripe restricted key
    r"SG\.[A-Za-z0-9_-]{10,}",          # SendGrid API key
    r"hf_[A-Za-z0-9]{10,}",             # HuggingFace token
    r"r8_[A-Za-z0-9]{10,}",             # Replicate API token
    r"npm_[A-Za-z0-9]{10,}",            # npm access token
    r"pypi-[A-Za-z0-9_-]{10,}",         # PyPI API token
    r"dop_v1_[A-Za-z0-9]{10,}",         # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",         # DigitalOcean OAuth
    r"gsk_[A-Za-z0-9]{10,}",            # Groq Cloud API key
    r"syt_[A-Za-z0-9]{10,}",            # Matrix access token
    r"mem0_[A-Za-z0-9]{10,}",           # Mem0 Platform API key
    r"hsk-[A-Za-z0-9]{10,}",            # Hindsight API key
    r"brv_[A-Za-z0-9]{10,}",            # ByteRover API key
    # Dragon-specific
    r"dragon_[A-Za-z0-9]{10,}",         # Dragon API key
    r"dsk-[A-Za-z0-9]{10,}",            # DeepSeek API key (overlap with sk-)
]


# ────────────────────────────────────────────────────────────────────
# Environment / config value patterns
# ────────────────────────────────────────────────────────────────────

_SECRET_ENV_NAMES = (
    r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
)

_ENV_ASSIGN_RE = re.compile(
    rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2",
    re.IGNORECASE,
)

# JSON field patterns: "apiKey": "value", "token": "value", etc.
_JSON_KEY_NAMES = (
    r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|"
    r"auth_token|bearer|secret_value|raw_secret|secret_input|key_material)"
)

_JSON_FIELD_RE = re.compile(
    rf'(\"{_JSON_KEY_NAMES}\")\s*:\s*\"([^\"]+)\"',
    re.IGNORECASE,
)

# Authorization headers
_AUTH_HEADER_RE = re.compile(
    r"(Authorization:\s*Bearer\s+)(\S+)",
    re.IGNORECASE,
)

# Private key blocks
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

# Database connection strings: protocol://user:PASSWORD@host
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)

# JWT tokens: header.payload[.signature] — always start with "eyJ" (base64 for "{")
_JWT_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}"            # Header (always starts with eyJ)
    r"(?:\.[A-Za-z0-9_=-]{4,}){0,2}"    # Optional payload and/or signature
)

# Telegram bot tokens: bot<digits>:<token> or <digits>:<token>
_TELEGRAM_RE = re.compile(
    r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})",
)

# URLs containing query strings
_URL_WITH_QUERY_RE = re.compile(
    r"(https?|wss?|ftp)://"           # scheme
    r"([^\s/?#]+)"                      # authority (may include userinfo)
    r"([^\s?#]*)"                       # path
    r"\?([^\s#]+)"                      # query (required)
    r"(#\S*)?",                         # optional fragment
)

# URLs containing userinfo: scheme://user:password@host
_URL_USERINFO_RE = re.compile(
    r"(https?|wss?|ftp)://([^/\s:@]+):([^/\s@]+)@",
)

# Form-urlencoded body detection (conservative)
_FORM_BODY_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*(?:&[A-Za-z_][A-Za-z0-9_.-]*=[^&\s]*)+$"
)

# E.164 phone numbers: +<country><number>, 7-15 digits
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# Discord user/role mentions: <@***> or <@!***>
_DISCORD_MENTION_RE = re.compile(r"<@!?(\d{17,20})>")

# Compile known prefix patterns into one alternation
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])"
)


# ────────────────────────────────────────────────────────────────────
# Industry-specific redaction rules
# ────────────────────────────────────────────────────────────────────

class IndustryRules:
    """Additional redaction rules per industry vertical.

    Usage::

        rules = IndustryRules.for_industry("finance")
        text = redact_sensitive_text(text, industry_rules=rules)
    """

    def __init__(
        self,
        patterns: Optional[List[Tuple[str, str]]] = None,
    ) -> None:
        """Configure industry-specific patterns.

        Args:
            patterns: List of (regex_pattern, redaction_label) tuples.
        """
        self.patterns: List[Tuple[re.Pattern, str]] = []
        if patterns:
            for pattern, label in patterns:
                self.patterns.append((re.compile(pattern, re.IGNORECASE), label))

    def apply(self, text: str) -> str:
        """Apply industry-specific redaction rules to text."""
        for pattern, label in self.patterns:
            text = pattern.sub(lambda m: f"[REDACTED:{label}]", text)
        return text

    @classmethod
    def for_industry(cls, industry: str) -> "IndustryRules":
        """Return pre-configured rules for a given industry.

        Supported industries:
            - ``finance``: redact monetary amounts, account numbers, SWIFT/BIC codes
            - ``medical``: redact patient names, DOB, medical record numbers
            - ``legal``: redact case numbers, client references
            - ``education``: redact student IDs, grades
            - ``default``: no additional rules
        """
        industry = industry.lower()

        if industry == "finance":
            return cls(patterns=[
                # Monetary amounts with currency symbols
                (r"[$€£¥]\s?\d{1,3}(?:[,.]\d{3})*(?:[.,]\d{2})?", "amount"),
                # IBAN numbers
                (r"\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b", "iban"),
                # SWIFT/BIC codes
                (r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", "swift"),
                # Bank account numbers (8-20 digits)
                (r"\b\d{8,20}\b", "account_number"),
            ])

        elif industry == "medical":
            return cls(patterns=[
                # Patient names (capitalised word pairs — conservative)
                (r"Patient:?\s+[A-Z][a-z]+\s+[A-Z][a-z]+", "patient_name"),
                # Dates of birth (various formats)
                (r"\bDOB:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", "dob"),
                (r"\b\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4}\b", "dob"),
                # Medical record numbers
                (r"\bMRN:?\s*\d{6,12}\b", "medical_record"),
                # US SSN
                (r"\b\d{3}-\d{2}-\d{4}\b", "ssn"),
            ])

        elif industry == "legal":
            return cls(patterns=[
                # Case numbers (e.g., 2024-CV-00123)
                (r"\b\d{4}-[A-Z]{2,4}-\d{4,6}\b", "case_number"),
                # Docket numbers
                (r"\bDocket\s*#?\s*\d{2,6}\b", "docket"),
                # Client references
                (r"\bClient\s*(?:ID|Ref)?:?\s*[A-Z0-9]{4,12}\b", "client_ref"),
            ])

        elif industry == "education":
            return cls(patterns=[
                # Student IDs
                (r"\bStudent\s*(?:ID|Number)?:?\s*\d{5,10}\b", "student_id"),
                # Grade values
                (r"\bGrade:?\s*[A-F][+-]?\b", "grade"),
                # GPA values
                (r"\bGPA:?\s*\d\.\d{1,2}\b", "gpa"),
            ])

        else:
            return cls()


# ────────────────────────────────────────────────────────────────────
# Core redaction functions
# ────────────────────────────────────────────────────────────────────

def mask_secret(
    value: str,
    *,
    head: int = 4,
    tail: int = 4,
    floor: int = 12,
    placeholder: str = "***",
    empty: str = "",
) -> str:
    """Mask a secret for display, preserving head and tail characters.

    Used for display-time redaction — e.g. showing ``sk-pr...7890``
    instead of the full key.

    Args:
        value: The secret to mask. ``None``/empty returns ``empty``.
        head: Leading characters to preserve. Default 4.
        tail: Trailing characters to preserve. Default 4.
        floor: Values shorter than ``head + tail + floor_margin`` are
               fully masked. Default 12.
        placeholder: Value returned for too-short inputs. Default ``"***"``.
        empty: Value returned when ``value`` is falsy.

    Examples:
        >>> mask_secret("sk-pro-1234567890abcdef")
        'sk-p...cdef'
        >>> mask_secret("short")
        '***'
        >>> mask_secret("")
        ''
    """
    if not value:
        return empty
    if len(value) < floor:
        return placeholder
    return f"{value[:head]}...{value[-tail:]}"


def _mask_token(token: str) -> str:
    """Mask a log token — conservative 18-char floor, preserves 6 prefix / 4 suffix."""
    if not token:
        return "***"
    return mask_secret(token, head=6, tail=4, floor=18)


def _redact_query_string(query: str) -> str:
    """Redact sensitive parameter values in a URL query string."""
    if not query:
        return query
    parts = []
    for pair in query.split("&"):
        if "=" not in pair:
            parts.append(pair)
            continue
        key, _, value = pair.partition("=")
        if key.lower() in _SENSITIVE_QUERY_PARAMS:
            parts.append(f"{key}=***")
        else:
            parts.append(pair)
    return "&".join(parts)


def _redact_url_query_params(text: str) -> str:
    """Scan text for URLs with query strings and redact sensitive params."""
    def _sub(m: re.Match) -> str:
        scheme = m.group(1)
        authority = m.group(2)
        path = m.group(3)
        query = _redact_query_string(m.group(4))
        fragment = m.group(5) or ""
        return f"{scheme}://{authority}{path}?{query}{fragment}"
    return _URL_WITH_QUERY_RE.sub(_sub, text)


def _redact_url_userinfo(text: str) -> str:
    """Strip user:password@ from HTTP/WS/FTP URLs."""
    return _URL_USERINFO_RE.sub(
        lambda m: f"{m.group(1)}://{m.group(2)}:***@",
        text,
    )


def _redact_form_body(text: str) -> str:
    """Redact sensitive values in a form-urlencoded body.

    Only applies when the entire input looks like a pure form body
    (k=v&k=v with no newlines, no other text).
    """
    if not text or "\n" in text or "&" not in text:
        return text
    if not _FORM_BODY_RE.match(text.strip()):
        return text
    return _redact_query_string(text.strip())


# ────────────────────────────────────────────────────────────────────
# Main redaction entry point
# ────────────────────────────────────────────────────────────────────

# Map of PII types to regex patterns (used in non-prefix redaction pass)
_PII_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (_EMAIL_RE, "email"),
    (_CN_ID_CARD_RE, "cn_id_card"),
    (_CREDIT_CARD_RE, "credit_card"),
]


def redact_sensitive_text(
    text: str | None,
    *,
    force: bool = False,
    code_file: bool = False,
    industry_rules: Optional[IndustryRules] = None,
) -> str | None:
    """Apply all redaction patterns to a block of text.

    Safe to call on any string — non-matching text passes through unchanged.
    Disabled by default via ``DRAGON_REDACT_SECRETS`` env var. Set
    ``force=True`` for safety boundaries that must never return raw secrets.

    Args:
        text: Input text to redact.
        force: If True, redact regardless of ``DRAGON_REDACT_SECRETS`` setting.
        code_file: If True, skip ENV-assignment and JSON-field patterns
                   (false positives in source code).
        industry_rules: Optional ``IndustryRules`` for additional per-industry
                        patterns.

    Returns:
        Redacted text, or None if input was None.

    Examples:
        >>> redact_sensitive_text("sk-pro-abc123def456")
        'sk-p...f456'
        >>> redact_sensitive_text("Email: user@example.com")
        'Email: [REDACTED:email]'
        >>> redact_sensitive_text("13812345678")  # Chinese mobile
        '[REDACTED:cn_phone]'
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not (force or _REDACT_ENABLED):
        return text

    # ── Pass 1: Known API key prefixes ─────────────────────────────
    text = _PREFIX_RE.sub(lambda m: _mask_token(m.group(1)), text)

    # ── Pass 2: ENV assignments (skip for code files) ──────────────
    if not code_file:
        def _redact_env(m):
            name, quote, value = m.group(1), m.group(2), m.group(3)
            return f"{name}={quote}{_mask_token(value)}{quote}"
        text = _ENV_ASSIGN_RE.sub(_redact_env, text)

        # JSON fields: "apiKey": "***"
        def _redact_json(m):
            key, value = m.group(1), m.group(2)
            return f'{key}: "{_mask_token(value)}"'
        text = _JSON_FIELD_RE.sub(_redact_json, text)

    # ── Pass 3: PII patterns ───────────────────────────────────────
    # Chinese mobile phone
    text = _CN_PHONE_RE.sub("[REDACTED:cn_phone]", text)

    # US phone (only if it matches a full phone pattern with country code or area code)
    def _redact_us_phone(m):
        matched = m.group(0).strip()
        # Only redact if it looks like a proper phone number (not random digits)
        digits_only = re.sub(r"[^\d]", "", matched)
        if len(digits_only) >= 10:
            return "[REDACTED:us_phone]"
        return matched
    text = _US_PHONE_RE.sub(_redact_us_phone, text)

    # General PII
    for pattern, label in _PII_PATTERNS:
        text = pattern.sub(lambda m: f"[REDACTED:{label}]", text)

    # ── Pass 4: IP addresses (only standalone, not part of other tokens) ──
    text = _IPV4_RE.sub("[REDACTED:ipv4]", text)

    # ── Pass 5: Infrastructure secrets ─────────────────────────────
    # Authorization headers
    text = _AUTH_HEADER_RE.sub(
        lambda m: m.group(1) + _mask_token(m.group(2)),
        text,
    )

    # Telegram bot tokens
    def _redact_telegram(m):
        prefix = m.group(1) or ""
        digits = m.group(2)
        return f"{prefix}{digits}:***"
    text = _TELEGRAM_RE.sub(_redact_telegram, text)

    # Private key blocks
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)

    # Database connection string passwords
    text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)

    # JWT tokens (eyJ... — base64-encoded JSON headers)
    text = _JWT_RE.sub(lambda m: _mask_token(m.group(0)), text)

    # URL userinfo (http(s)://user:pass@host)
    text = _redact_url_userinfo(text)

    # URL query params containing opaque tokens
    text = _redact_url_query_params(text)

    # Form-urlencoded bodies
    text = _redact_form_body(text)

    # Discord user/role mentions
    text = _DISCORD_MENTION_RE.sub(
        lambda m: f"<@{'!' if '!' in m.group(0) else ''}***>",
        text,
    )

    # E.164 phone numbers (Signal, WhatsApp)
    def _redact_phone(m):
        phone = m.group(1)
        if len(phone) <= 8:
            return phone[:2] + "****" + phone[-2:]
        return phone[:4] + "****" + phone[-4:]
    text = _SIGNAL_PHONE_RE.sub(_redact_phone, text)

    # ── Pass 6: Industry-specific rules ────────────────────────────
    if industry_rules is not None:
        text = industry_rules.apply(text)

    return text


# ────────────────────────────────────────────────────────────────────
# Convenience functions
# ────────────────────────────────────────────────────────────────────

def redact_for_logs(text: str, *, force: bool = True) -> str:
    """Redact sensitive content specifically for log output.

    Always forces redaction (ignores DRAGON_REDACT_SECRETS) since
    logs are a permanent record.
    """
    result = redact_sensitive_text(text, force=force)
    return result if result is not None else ""


def redact_for_display(text: str, max_length: int = 200) -> str:
    """Redact and truncate for user-facing display.

    Redacts sensitive content and truncates to ``max_length`` chars,
    suitable for status messages, tool output previews, etc.
    """
    result = redact_sensitive_text(text, force=True)
    if result is None:
        return ""
    if len(result) > max_length:
        return result[:max_length - 3] + "..."
    return result


# ────────────────────────────────────────────────────────────────────
# Logging formatter
# ────────────────────────────────────────────────────────────────────

class RedactingFormatter(logging.Formatter):
    """Log formatter that redacts secrets from all log messages.

    Usage::

        import logging
        from dragon.redact import RedactingFormatter

        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        logging.getLogger().addHandler(handler)
    """

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: str = "%",
        validate: bool = True,
        *,
        defaults: Optional[Dict] = None,
    ) -> None:
        super().__init__(fmt, datefmt, style, validate, defaults=defaults)  # type: ignore

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_text(original) or original
