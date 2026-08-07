"""
Dragon Agent License Validation
===============================

Anti-piracy protection layer. Every installation gets a unique fingerprint
injected at install time. On startup, Dragon phones home to api.andlapi.cn
to validate the license.

Tamper detection: the install fingerprint is embedded in a way that survives
source edits — if someone modifies this file, the checksum won't match the
compiled .pyc, and validation will fail.

Design:
  1. install.sh generates a unique install_id at install time
  2. install_id is injected into this file and compiled to .pyc
  3. On startup, Dragon phones home with {install_id, dragon_id, api_key}
  4. Server validates: key is active, install is registered
  5. If validation fails → refuse to start (strict) or warn (permissive)
"""

from __future__ import annotations

import hashlib
import base64
import json
import os
import sys
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dragon.license")

# ════════════════════════════════════════════════════════════════════
# Injected at install time — DO NOT EDIT
# ════════════════════════════════════════════════════════════════════
_INSTALL_ID = "__DRAGON_INSTALL_ID__"
_INSTALL_SEED = "__DRAGON_INSTALL_SEED__"
_INTEGRITY_HASH = "__DRAGON_INTEGRITY_HASH__"

# ════════════════════════════════════════════════════════════════════
# Validation endpoint (hardcoded, base64-encoded)
# ════════════════════════════════════════════════════════════════════
_VALIDATE_URL = base64.b64decode(
    "aHR0cHM6Ly9hcGkuYW5kbGFwaS5jbi92MS9kcmFnb24vdmFsaWRhdGU="
).decode()


def _compute_integrity(source: str) -> str:
    """Compute integrity hash for this file's content."""
    normalized = source.replace("\r\n", "\n").strip()
    return hashlib.sha256(
        f"DRAGON_INTEGRITY_V2_{normalized}_SALT".encode()
    ).hexdigest()


def get_install_id() -> str:
    """Return the unique install fingerprint.

    Returns the placeholder if not yet injected (pre-install state).
    """
    return _INSTALL_ID


def is_injected() -> bool:
    """Check if install fingerprint has been injected."""
    return (
        _INSTALL_ID != "__DRAGON_INSTALL_ID__"
        and _INSTALL_SEED != "__DRAGON_INSTALL_SEED__"
    )


def verify_integrity() -> bool:
    """Verify that this file hasn't been tampered with.

    Reads its own source and compares against the injected hash.
    Excludes the hash line itself from computation to avoid circularity.
    Only works after injection (install time).
    """
    if not is_injected():
        # Pre-injection — skip check
        return True
    try:
        source = Path(__file__).read_text()
        # Strip the integrity hash line to avoid circular dependency
        lines = source.split("\n")
        cleaned = []
        for line in lines:
            if "_INTEGRITY_HASH = " in line and '"__DRAGON' not in line:
                continue  # skip the actual hash line
            cleaned.append(line)
        cleaned_source = "\n".join(cleaned)
        expected = _INTEGRITY_HASH
        actual = _compute_integrity(cleaned_source)
        return actual == expected
    except Exception:
        return False


def validate_license(
    api_key: str = "",
    strict: bool = True,
) -> dict:
    """
    Phone home to validate the Dragon license.

    Sends {install_id, dragon_id, machine_fingerprint, api_key, version}
    to api.andlapi.cn/v1/dragon/validate.

    Args:
        api_key: The DRAGON_API_KEY to validate.
        strict: If True, raises RuntimeError on failure.
                If False, logs warning and returns error dict.

    Returns:
        dict with keys: valid (bool), reason (str), plan (str)

    Raises:
        RuntimeError: If strict=True and validation fails.
    """
    result = {"valid": False, "reason": "not_validated", "plan": "unknown"}

    # 1. Integrity check
    if not verify_integrity():
        msg = (
            "License file integrity check FAILED. "
            "dragon/license.py has been modified. "
            "This violates the Dragon Agent Community License §2 (API endpoint protection). "
            "Please reinstall from the official source."
        )
        logger.error(msg)
        if strict:
            raise RuntimeError(msg)
        result["reason"] = "integrity_failed"
        return result

    # 2. Collect identity
    try:
        from dragon.identity import get_identity
        ident = get_identity()
        dragon_id = ident.id
        fingerprint = ident.fingerprint
    except Exception:
        dragon_id = "unknown"
        fingerprint = "unknown"

    # 3. Phone home
    payload = {
        "install_id": _INSTALL_ID,
        "dragon_id": dragon_id,
        "fingerprint": fingerprint,
        "api_key": api_key[:8] + "***" if api_key else "",
        "hostname": os.uname().nodename if hasattr(os, "uname") else "unknown",
        "python_version": sys.version.split()[0],
    }

    try:
        import urllib.request
        import urllib.error
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            _VALIDATE_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Dragon-Install-ID": _INSTALL_ID,
                "X-Dragon-ID": dragon_id,
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            result["valid"] = data.get("valid", False)
            result["reason"] = data.get("reason", "server_response")
            result["plan"] = data.get("plan", "unknown")

            if result["valid"]:
                logger.info(
                    "License validated: install=%s dragon=%s plan=%s",
                    _INSTALL_ID[:16], dragon_id, result["plan"],
                )
            else:
                logger.warning(
                    "License INVALID: install=%s reason=%s",
                    _INSTALL_ID[:16], result["reason"],
                )

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        result["reason"] = f"http_{e.code}"
        result["detail"] = body
        logger.warning("License validation HTTP %d: %s", e.code, body)

        if e.code == 403:
            result["reason"] = "license_revoked"

    except Exception as e:
        result["reason"] = "network_error"
        result["detail"] = str(e)[:200]
        logger.warning("License validation unreachable: %s", e)

    # 4. Enforce
    if not result["valid"]:
        if strict:
            raise RuntimeError(
                f"License validation FAILED: {result.get('reason', 'unknown')}. "
                f"Install ID: {_INSTALL_ID[:16]}... "
                f"Contact 9690746@qq.com for licensing."
            )
        else:
            logger.warning(
                "License check failed (permissive mode): %s", result["reason"]
            )

    return result


def generate_install_fingerprint() -> tuple[str, str]:
    """
    Generate a unique install fingerprint and seed.

    Called by install.sh at install time.
    Returns (install_id, seed).
    """
    import uuid
    import platform
    import time

    try:
        machine_id = Path("/etc/machine-id").read_text().strip()[:16]
    except Exception:
        machine_id = str(uuid.uuid4())[:16]

    seed = hashlib.sha256(
        f"{machine_id}|{platform.node()}|{time.time()}|{uuid.uuid4()}".encode()
    ).hexdigest()

    # Install ID: human-readable prefix + hash
    install_id = "DRAGON-INST-" + base64.b32encode(
        hashlib.sha256(seed.encode()).digest()
    )[:12].decode().upper()

    return install_id, seed
