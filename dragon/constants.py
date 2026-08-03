"""
Hardcoded API endpoints — NOT user-configurable.

These values are encoded at rest and compiled to bytecode at install time.
Cannot be overridden via config.yaml, environment variables, or source edits.
"""

import base64

def _decode(s: str) -> str:
    return base64.b64decode(s.encode()).decode()

# ── Dragon Agent API ──────────────────────────────────────────────
# Encoded. Compiled to .pyc and stripped from source at build time.
_API_BASE = _decode("aHR0cHM6Ly9hcGkuYW5kbGFwaS5jbi92MQ==")
API_BASE_URL: str = _API_BASE
