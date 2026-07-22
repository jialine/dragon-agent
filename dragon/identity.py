"""
Dragon Agent — Global Unique Identity
======================================

Every Dragon instance gets a globally unique, machine-bound identity
automatically computed on first run. Like a national ID card for the agent.

Algorithm:
    DRAGON-{base32(sha256(machine_id + mac + hostname)[:15])}

Sources of entropy:
    1. /etc/machine-id — OS-level UUID, unique per install
    2. Primary MAC address — hardware-bound
    3. Hostname — deployment context
    4. Fallback: /var/lib/dbus/machine-id or random UUID

The identity is computed once, persisted to dragon_data/identity.json,
and never changes for the life of the installation.

Usage:
    from dragon.identity import get_identity
    ident = get_identity()
    print(ident.id)          # "DRAGON-7KJ9M2XP4QVW8F3"
    print(ident.machine_id)  # raw machine-id
    print(ident.fingerprint) # full SHA256 fingerprint
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────

IDENTITY_FILE = "identity.json"
IDENTITY_PREFIX = "DRAGON"
ID_HASH_CHARS = 15  # base32 chars for the short ID

# RFC 4648 base32 alphabet (uppercase, no padding, human-friendly)
_BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


# ────────────────────────────────────────────────────────────────
# Identity data class
# ────────────────────────────────────────────────────────────────

@dataclass
class DragonIdentity:
    """Immutable identity for a Dragon Agent instance."""

    id: str  # "DRAGON-7KJ9M2XP4QVW8F3"
    fingerprint: str  # full SHA256 hex
    machine_id: str  # raw source machine-id
    mac_address: str  # primary MAC
    hostname: str  # system hostname
    created_at: str  # ISO timestamp of first generation

    def __repr__(self) -> str:
        return f"DragonIdentity({self.id})"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "fingerprint": self.fingerprint,
            "machine_id": self.machine_id,
            "mac_address": self.mac_address,
            "hostname": self.hostname,
            "created_at": self.created_at,
        }


# ────────────────────────────────────────────────────────────────
# Core logic
# ────────────────────────────────────────────────────────────────

def _base32_encode(data: bytes) -> str:
    """RFC 4648 base32 encode (uppercase, no padding)."""
    result = []
    bits = 0
    value = 0
    for byte in data:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            result.append(_BASE32_ALPHABET[(value >> bits) & 0x1F])
    if bits > 0:
        result.append(_BASE32_ALPHABET[(value << (5 - bits)) & 0x1F])
    return "".join(result)


def _get_machine_id() -> str:
    """Get the OS-level machine ID, with fallbacks."""
    paths = ["/etc/machine-id", "/var/lib/dbus/machine-id"]
    for p in paths:
        try:
            content = Path(p).read_text().strip()
            if content and len(content) >= 32:
                return content
        except (OSError, PermissionError):
            continue
    # Last resort: random UUID
    return str(uuid.uuid4())


def _get_primary_mac() -> str:
    """Get the primary network interface MAC address."""
    try:
        import uuid as _uuid
        return ":".join(
            f"{((_uuid.getnode() >> elements) & 0xFF):02x}"
            for elements in range(40, -1, -8)
        )
    except Exception:
        return "00:00:00:00:00:00"


def compute_identity() -> DragonIdentity:
    """Compute a fresh identity from machine attributes."""
    machine_id = _get_machine_id()
    mac = _get_primary_mac()
    hostname = platform.node() or "unknown"

    # Create a fingerprint from machine attributes
    seed = f"{machine_id}|{mac}|{hostname}|dragon-agent"
    fingerprint = hashlib.sha256(seed.encode()).hexdigest()

    # Short base32 ID for display
    short_hash = _base32_encode(hashlib.sha256(seed.encode()).digest())[:ID_HASH_CHARS]
    agent_id = f"{IDENTITY_PREFIX}-{short_hash}"

    from datetime import datetime, timezone

    return DragonIdentity(
        id=agent_id,
        fingerprint=fingerprint,
        machine_id=machine_id,
        mac_address=mac,
        hostname=hostname,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ────────────────────────────────────────────────────────────────
# Persistence & singleton
# ────────────────────────────────────────────────────────────────

_identity: Optional[DragonIdentity] = None


def _get_data_dir() -> Path:
    """Find the dragon_data directory."""
    # Try relative to this file (inside dragon package)
    this_file = Path(__file__).resolve()
    # dragon/identity.py → dragon_data/
    candidates = [
        this_file.parent.parent / "dragon_data",
        Path.home() / ".dragon",
        Path("/var/lib/dragon"),
    ]
    for d in candidates:
        if d.exists():
            return d
    # Create default
    default = this_file.parent.parent / "dragon_data"
    default.mkdir(parents=True, exist_ok=True)
    return default


def get_identity(data_dir: Optional[Path] = None) -> DragonIdentity:
    """
    Get the Dragon instance identity.

    Loads from disk if already generated; otherwise computes, persists,
    and returns a new globally unique identity.

    Args:
        data_dir: Optional path to dragon_data directory. Auto-detected if not given.

    Returns:
        DragonIdentity with id, fingerprint, machine_id, etc.
    """
    global _identity

    if _identity is not None:
        return _identity

    if data_dir is None:
        data_dir = _get_data_dir()

    identity_path = data_dir / IDENTITY_FILE

    # Try loading existing identity
    if identity_path.exists():
        try:
            data = json.loads(identity_path.read_text())
            _identity = DragonIdentity(
                id=data["id"],
                fingerprint=data["fingerprint"],
                machine_id=data.get("machine_id", ""),
                mac_address=data.get("mac_address", ""),
                hostname=data.get("hostname", ""),
                created_at=data.get("created_at", ""),
            )
            return _identity
        except (json.JSONDecodeError, KeyError):
            # Corrupted — regenerate
            pass

    # Compute fresh identity
    _identity = compute_identity()

    # Persist
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(
        json.dumps(_identity.to_dict(), indent=2, ensure_ascii=False)
    )

    return _identity


# ────────────────────────────────────────────────────────────────
# CLI helper
# ────────────────────────────────────────────────────────────────

def show_identity(data_dir: Optional[Path] = None):
    """Print identity info to stdout (for CLI usage)."""
    ident = get_identity(data_dir)
    print(f"Dragon Agent Identity")
    print(f"  ID:          {ident.id}")
    print(f"  Fingerprint: {ident.fingerprint}")
    print(f"  Machine:     {ident.hostname}")
    print(f"  Created:     {ident.created_at}")


if __name__ == "__main__":
    show_identity()
