"""
Dragon Gateway — DM Pairing System
===================================

Code-based approval flow for authorizing new users on messaging platforms.
When an unknown user DMs the bot, they receive a one-time pairing code
that must be approved via CLI or API.

Security:
  - 8-char codes from 32-char unambiguous alphabet (excludes 0/O/1/I)
  - Cryptographic randomness via secrets.choice()
  - 1-hour code expiry
  - Max 3 pending codes per platform
  - Rate limiting: 1 request per user per 10 minutes
  - Lockout after 5 failed approval attempts (1 hour)

Storage: ~/.hermes/platforms/pairing/
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Dict


# --- Constants ---
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_TTL_SECONDS = 3600
RATE_LIMIT_SECONDS = 600
LOCKOUT_SECONDS = 3600
MAX_PENDING_PER_PLATFORM = 3
MAX_FAILED_ATTEMPTS = 5


def _pairing_dir() -> Path:
    """Get the pairing data directory."""
    p = Path(os.path.expanduser("~/.hermes/platforms/pairing"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _secure_write(path: Path, data: str) -> None:
    """Atomic write with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class PairingStore:
    """Manages pairing codes and approved user lists."""

    def __init__(self):
        self._dir = _pairing_dir()
        self._lock = threading.RLock()

    # --- Path helpers ---
    def _pending_path(self, platform: str) -> Path:
        return self._dir / f"{platform}-pending.json"

    def _approved_path(self, platform: str) -> Path:
        return self._dir / f"{platform}-approved.json"

    def _rate_limit_path(self) -> Path:
        return self._dir / "_rate_limits.json"

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_json(self, path: Path, data: dict) -> None:
        _secure_write(path, json.dumps(data, indent=2, ensure_ascii=False))

    # --- Approved users ---
    def is_approved(self, platform: str, user_id: str) -> bool:
        approved = self._load_json(self._approved_path(platform))
        return user_id in approved

    def list_approved(self, platform: str | None = None) -> list:
        results = []
        platforms = [platform] if platform else self._all_platforms("approved")
        for p in platforms:
            approved = self._load_json(self._approved_path(p))
            for uid, info in approved.items():
                results.append({"platform": p, "user_id": uid, **info})
        return results

    def revoke(self, platform: str, user_id: str) -> bool:
        path = self._approved_path(platform)
        with self._lock:
            approved = self._load_json(path)
            if user_id in approved:
                del approved[user_id]
                self._save_json(path, approved)
                return True
        return False

    # --- Code generation ---
    def generate_code(
        self, platform: str, user_id: str, user_name: str = ""
    ) -> Optional[str]:
        with self._lock:
            self._cleanup_expired(platform)
            if self._is_locked_out(platform):
                return None
            if self._is_rate_limited(platform, user_id):
                return None
            pending = self._load_json(self._pending_path(platform))
            if len(pending) >= MAX_PENDING_PER_PLATFORM:
                return None

            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
            pending[code] = {
                "user_id": user_id,
                "user_name": user_name,
                "created_at": time.time(),
            }
            self._save_json(self._pending_path(platform), pending)
            self._record_rate_limit(platform, user_id)
            return code

    def approve_code(self, platform: str, code: str) -> Optional[dict]:
        with self._lock:
            self._cleanup_expired(platform)
            code = code.upper().strip()
            if self._is_locked_out(platform):
                return None
            pending = self._load_json(self._pending_path(platform))
            if code not in pending:
                self._record_failed_attempt(platform)
                return None
            entry = pending.pop(code)
            self._save_json(self._pending_path(platform), pending)
            self._approve_user(platform, entry["user_id"], entry.get("user_name", ""))
            return {"user_id": entry["user_id"], "user_name": entry.get("user_name", "")}

    def list_pending(self, platform: str | None = None) -> list:
        results = []
        platforms = [platform] if platform else self._all_platforms("pending")
        for p in platforms:
            self._cleanup_expired(p)
            pending = self._load_json(self._pending_path(p))
            for code, info in pending.items():
                age_min = int((time.time() - info["created_at"]) / 60)
                results.append({
                    "platform": p, "code": code,
                    "user_id": info["user_id"],
                    "user_name": info.get("user_name", ""),
                    "age_minutes": age_min,
                })
        return results

    # --- Rate limiting ---
    def _is_rate_limited(self, platform: str, user_id: str) -> bool:
        limits = self._load_json(self._rate_limit_path())
        key = f"{platform}:{user_id}"
        last_request = limits.get(key, 0)
        return (time.time() - last_request) < RATE_LIMIT_SECONDS

    def _record_rate_limit(self, platform: str, user_id: str) -> None:
        limits = self._load_json(self._rate_limit_path())
        limits[f"{platform}:{user_id}"] = time.time()
        self._save_json(self._rate_limit_path(), limits)

    def _is_locked_out(self, platform: str) -> bool:
        limits = self._load_json(self._rate_limit_path())
        lockout_until = limits.get(f"_lockout:{platform}", 0)
        return time.time() < lockout_until

    def _record_failed_attempt(self, platform: str) -> None:
        limits = self._load_json(self._rate_limit_path())
        fails = limits.get(f"_failures:{platform}", 0) + 1
        limits[f"_failures:{platform}"] = fails
        if fails >= MAX_FAILED_ATTEMPTS:
            limits[f"_lockout:{platform}"] = time.time() + LOCKOUT_SECONDS
            limits[f"_failures:{platform}"] = 0
        self._save_json(self._rate_limit_path(), limits)

    # --- Cleanup ---
    def _cleanup_expired(self, platform: str) -> None:
        path = self._pending_path(platform)
        pending = self._load_json(path)
        now = time.time()
        expired = [
            code for code, info in pending.items()
            if (now - info["created_at"]) > CODE_TTL_SECONDS
        ]
        if expired:
            for code in expired:
                del pending[code]
            self._save_json(path, pending)

    def _approve_user(self, platform: str, user_id: str, user_name: str = "") -> None:
        approved = self._load_json(self._approved_path(platform))
        approved[user_id] = {"user_name": user_name, "approved_at": time.time()}
        self._save_json(self._approved_path(platform), approved)

    def _all_platforms(self, suffix: str) -> list:
        platforms = []
        if self._dir.exists():
            for f in self._dir.iterdir():
                if f.name.endswith(f"-{suffix}.json") and not f.name.startswith("_"):
                    platforms.append(f.name.replace(f"-{suffix}.json", ""))
        return platforms
