"""
Panda Profile — Multi-tenant isolation for independent agent instances.

Each profile has its own:
- Configuration (config.yaml)
- API keys (.env)
- Sessions (sessions.db)
- Skills (skills/)
- Memory (knowledge graph)

Inspired by Hermes Agent's profile system.

Usage::

    from panda.profile import ProfileManager

    pm = ProfileManager(base_dir="~/.panda/profiles")
    pm.create("work", clone_from="default")
    pm.create("personal")

    profile = pm.get("work")
    # Now use profile.config, profile.session_store, etc.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("panda.profile")


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class Profile:
    name: str
    base_dir: Path
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    last_used_at: str = ""
    is_default: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    # ── Directory Paths ──────────────────────────────────────────

    @property
    def config_dir(self) -> Path:
        return self.base_dir

    @property
    def config_file(self) -> Path:
        return self.base_dir / "config.yaml"

    @property
    def env_file(self) -> Path:
        return self.base_dir / ".env"

    @property
    def sessions_dir(self) -> Path:
        return self.base_dir / "sessions"

    @property
    def skills_dir(self) -> Path:
        return self.base_dir / "skills"

    @property
    def memory_dir(self) -> Path:
        return self.base_dir / "memory"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    def ensure_dirs(self) -> None:
        """Create all profile directories."""
        for d in [
            self.config_dir, self.sessions_dir, self.skills_dir,
            self.memory_dir, self.data_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_dir": str(self.base_dir),
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "is_default": self.is_default,
            "metadata": self.metadata,
        }


# ────────────────────────────────────────────────────────────────────
# Profile Manager
# ────────────────────────────────────────────────────────────────────


class ProfileManager:
    """Manage multiple isolated profiles.

    Parameters
    ----------
    base_dir : str
        Root directory for all profiles (default: ~/.panda/profiles).
    """

    def __init__(self, base_dir: str = "~/.panda/profiles") -> None:
        self.base_dir = Path(base_dir).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self._profiles: Dict[str, Profile] = {}
        self._default_profile: Optional[str] = None

        # State file
        self._state_file = self.base_dir / "profiles.json"

        self._load_state()
        logger.info("ProfileManager ready (%d profiles)", len(self._profiles))

    # ── State Persistence ─────────────────────────────────────────

    def _load_state(self) -> None:
        if not self._state_file.exists():
            return

        try:
            data = json.loads(self._state_file.read_text())
            self._default_profile = data.get("default")
            for pdata in data.get("profiles", []):
                profile = Profile(
                    name=pdata["name"],
                    base_dir=Path(pdata["base_dir"]),
                    created_at=pdata.get("created_at", ""),
                    last_used_at=pdata.get("last_used_at", ""),
                    is_default=pdata.get("is_default", False),
                    metadata=pdata.get("metadata", {}),
                )
                self._profiles[profile.name] = profile
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load profiles state: %s", e)

    def _save_state(self) -> None:
        data = {
            "default": self._default_profile,
            "profiles": [p.to_dict() for p in self._profiles.values()],
        }
        # Atomic write
        tmp = self._state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, self._state_file)

    # ── Profile CRUD ──────────────────────────────────────────────

    def create(
        self,
        name: str,
        clone_from: Optional[str] = None,
        set_default: bool = False,
        metadata: Optional[Dict] = None,
    ) -> Profile:
        """Create a new profile, optionally cloning from an existing one.

        Args:
            name: Profile name (lowercase, no spaces).
            clone_from: Name of profile to clone config from.
            set_default: Make this the default profile.
            metadata: Arbitrary metadata dict.
        """
        name = name.lower().replace(" ", "-")

        if name in self._profiles:
            raise ValueError(f"Profile '{name}' already exists")

        profile_dir = self.base_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)

        profile = Profile(
            name=name,
            base_dir=profile_dir,
            metadata=metadata or {},
        )
        profile.ensure_dirs()

        # Clone from existing profile
        if clone_from and clone_from in self._profiles:
            source = self._profiles[clone_from]
            self._clone_config(source, profile)

        # Create default config.yaml if it doesn't exist
        if not profile.config_file.exists():
            profile.config_file.write_text(
                "# Panda Agent Configuration\n"
                f"# Profile: {name}\n"
                "router:\n"
                "  model_path: models/qwen3-0.6b-q4_k_m.gguf\n"
                "  n_threads: 4\n"
                "server:\n"
                "  host: 0.0.0.0\n"
                "  port: 8000\n"
            )

        self._profiles[name] = profile
        if set_default or self._default_profile is None:
            self._default_profile = name
            profile.is_default = True

        self._save_state()
        logger.info("Created profile: %s", name)
        return profile

    def get(self, name: str) -> Optional[Profile]:
        return self._profiles.get(name)

    def get_default(self) -> Optional[Profile]:
        if self._default_profile:
            return self._profiles.get(self._default_profile)
        return None

    def set_default(self, name: str) -> bool:
        if name not in self._profiles:
            return False
        # Unset old default
        if self._default_profile and self._default_profile in self._profiles:
            self._profiles[self._default_profile].is_default = False
        self._default_profile = name
        self._profiles[name].is_default = True
        self._save_state()
        return True

    def list_profiles(self) -> List[Profile]:
        return sorted(self._profiles.values(), key=lambda p: p.name)

    def rename(self, old_name: str, new_name: str) -> bool:
        if old_name not in self._profiles:
            return False
        new_name = new_name.lower().replace(" ", "-")
        if new_name in self._profiles:
            return False

        profile = self._profiles.pop(old_name)
        old_dir = profile.base_dir
        new_dir = self.base_dir / new_name

        shutil.move(str(old_dir), str(new_dir))
        profile.name = new_name
        profile.base_dir = new_dir
        self._profiles[new_name] = profile

        if self._default_profile == old_name:
            self._default_profile = new_name

        self._save_state()
        return True

    def delete(self, name: str) -> bool:
        if name not in self._profiles:
            return False

        profile = self._profiles.pop(name)

        # Remove directory
        if profile.base_dir.exists():
            shutil.rmtree(str(profile.base_dir))

        if self._default_profile == name:
            self._default_profile = next(iter(self._profiles), None)

        self._save_state()
        return True

    # ── Export / Import ───────────────────────────────────────────

    def export_profile(self, name: str, output_path: str) -> bool:
        """Export a profile to a tar.gz archive."""
        profile = self._profiles.get(name)
        if profile is None:
            return False

        output = Path(output_path)
        with tarfile.open(output, "w:gz") as tar:
            tar.add(str(profile.base_dir), arcname=name)

        logger.info("Exported profile '%s' to %s", name, output_path)
        return True

    def import_profile(self, archive_path: str, new_name: Optional[str] = None) -> Optional[Profile]:
        """Import a profile from a tar.gz archive."""
        archive = Path(archive_path)
        if not archive.exists():
            return None

        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(tmp)

            # Find the profile directory inside
            extracted = list(Path(tmp).iterdir())
            if not extracted:
                return None

            source_name = extracted[0].name
            target_name = new_name or source_name

            if target_name in self._profiles:
                logger.warning("Profile '%s' already exists, skipping import", target_name)
                return None

            target_dir = self.base_dir / target_name
            shutil.copytree(str(extracted[0]), str(target_dir))

            profile = Profile(name=target_name, base_dir=target_dir)
            profile.ensure_dirs()
            self._profiles[target_name] = profile
            self._save_state()

            logger.info("Imported profile '%s' from %s", target_name, archive_path)
            return profile

    # ── Helpers ───────────────────────────────────────────────────

    def _clone_config(self, source: Profile, target: Profile) -> None:
        """Clone config files from source profile to target."""
        for filename in ["config.yaml", ".env"]:
            src_file = source.base_dir / filename
            if src_file.exists():
                dst_file = target.base_dir / filename
                shutil.copy2(str(src_file), str(dst_file))

    def stats(self) -> Dict[str, Any]:
        total_size = 0
        for p in self._profiles.values():
            for root, dirs, files in os.walk(p.base_dir):
                for f in files:
                    fp = Path(root) / f
                    if fp.exists():
                        total_size += fp.stat().st_size

        return {
            "total_profiles": len(self._profiles),
            "default_profile": self._default_profile,
            "total_size_mb": round(total_size / (1024 * 1024), 1),
            "profiles": [p.name for p in self.list_profiles()],
        }
