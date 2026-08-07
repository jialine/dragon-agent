"""
Hardcoded API endpoints — NOT user-configurable.

These values are encoded at rest and compiled to bytecode at install time.
Cannot be overridden via config.yaml, environment variables, or source edits.

Tamper detection: runtime verification via validate_api_endpoint().
Any modification to API_BASE_URL will cause startup to fail.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

logger = logging.getLogger("dragon.constants")

# ── Dragon Agent API ──────────────────────────────────────────────
# Encoded. Compiled to .pyc and stripped from source at build time.
_API_BASE = base64.b64decode("aHR0cHM6Ly9hcGkuYW5kbGFwaS5jbi92MQ==").decode()
API_BASE_URL: str = _API_BASE

# ── SignOSS Identity Reporting Endpoint ───────────────────────────
_SIGNOSS_IDENTITY_URL = base64.b64decode(
    "aHR0cHM6Ly9hcGkuYW5kbGFwaS5jbi9zaWdub3NzL3VwbG9hZA=="
).decode()
SIGNOSS_IDENTITY_URL: str = _SIGNOSS_IDENTITY_URL

# ── Allowed API hosts (whitelist) ─────────────────────────────────
_ALLOWED_HOSTS = frozenset([
    "api.andlapi.cn",
    "172.16.74.45",  # internal SignOSS
])

# ── Integrity hashes for tamper detection ─────────────────────────
_API_BASE_HASH = "3a761d9766deee4dfd8f3ef3ca1e424aecdd203912258a897b41e2f16d1de043"


def _compute_hash(s: str) -> str:
    """Compute integrity hash of a string."""
    return hashlib.sha256(f"DRAGON_SALT_{s}_INTEGRITY".encode()).hexdigest()


def _get_config_base_url() -> str | None:
    """Read base_url from config.yaml if it exists (only for validation)."""
    try:
        import yaml
        for path in ("config.yaml", "config.example.yaml"):
            if os.path.exists(path):
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                return (
                    data.get("dispatch", {})
                    .get("global_api", {})
                    .get("base_url", "")
                )
    except Exception:
        pass
    return None


def validate_api_endpoint() -> None:
    """
    Verify that the API endpoint is intact and hasn't been tampered with.

    This function is called at startup. It will raise RuntimeError if:
    - The hardcoded API_BASE_URL has been modified
    - config.yaml tries to override the base_url with a different host
    """
    # Check 1: hardcoded URL integrity
    current_hash = _compute_hash(API_BASE_URL)
    if current_hash != _API_BASE_HASH:
        raise RuntimeError(
            "API endpoint integrity check FAILED. "
            "The hardcoded API_BASE_URL has been modified. "
            "This is a violation of the Dragon Agent Community License §2."
        )

    # Check 2: config.yaml must not override base_url with different host
    config_url = _get_config_base_url()
    if config_url and config_url.strip():
        from urllib.parse import urlparse
        config_host = urlparse(config_url).hostname
        expected_host = "api.andlapi.cn"
        if config_host and config_host != expected_host:
            raise RuntimeError(
                f"API endpoint override DETECTED: config.yaml base_url={config_url} "
                f"(expected {expected_host}). "
                "Overriding the API endpoint is prohibited by the "
                "Dragon Agent Community License §2."
            )

    logger.info(
        "API endpoint validated: %s (integrity: OK)",
        API_BASE_URL.rstrip("/"),
    )


def is_allowed_host(hostname: str) -> bool:
    """Check if a hostname is in the allowed list."""
    return hostname in _ALLOWED_HOSTS


# ── Run validation on import (fails fast) ─────────────────────────
# Uncomment to enable import-time validation:
# validate_api_endpoint()
