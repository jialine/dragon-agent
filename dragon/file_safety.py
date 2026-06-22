"""
Path traversal and file operation safety for Dragon Agent.

Validates that file paths are within allowed directories, rejects
symlink traversal, '../' attacks, and access to sensitive system files.
Configurable allowed paths, file extensions, and size limits.

Usage::

    from dragon.file_safety import SafetyValidator
    validator = SafetyValidator(allowed_dirs=["/home/user/project", "/tmp"])
    safe_path = validator.validate_read("/home/user/project/data.txt")
    safe_path = validator.validate_write("/home/user/project/output.txt")
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Union


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

# System paths that should NEVER be accessible
_DEFAULT_DENIED_PATHS: Set[str] = {
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/etc/ssh/ssh_host_rsa_key",
    "/etc/ssh/ssh_host_ed25519_key",
    "/etc/ssh/ssh_host_ecdsa_key",
    "/etc/ssl/private",
    "/root/.ssh/id_rsa",
    "/root/.ssh/id_ed25519",
    "/root/.bash_history",
    "/proc/1/environ",
    "/proc/kcore",
    "/proc/kallsyms",
    "/sys/kernel",
    "/dev/mem",
    "/dev/kmem",
}

# Directory prefixes that should be denied for read/write
_DEFAULT_DENIED_PREFIXES: List[str] = [
    "/etc/ssh",
    "/root/.ssh",
    "/root/.aws",
    "/root/.gnupg",
    "/root/.kube",
    "/proc/",
    "/sys/",
]

# File extensions that are always safe to read
_DEFAULT_SAFE_EXTENSIONS: FrozenSet[str] = frozenset({
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css", ".scss", ".less",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".xml",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".rb", ".php", ".pl", ".sh", ".bash", ".zsh",
    ".sql", ".graphql",
    ".r", ".m", ".jl",
    ".vim", ".lua", ".el",
    ".log",
    ".env.example", ".env.sample",
    ".gitignore", ".dockerignore",
    "Dockerfile", "Makefile", "CMakeLists.txt",
})

# File extensions that should be blocked for writing
_DEFAULT_BLOCKED_WRITE_EXTENSIONS: FrozenSet[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib",
    ".sh", ".bash", ".zsh", ".fish",
    ".bat", ".cmd", ".ps1",
    ".crt", ".pem", ".key", ".p12", ".pfx",
    ".env", ".envrc",
})

_DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────


@dataclass
class SafePath:
    """A validated safe path that can be used for file operations."""

    path: str
    resolved: str
    is_safe: bool = True
    reason: str = ""

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path

    def as_path(self) -> Path:
        return Path(self.path)

    def exists(self) -> bool:
        return os.path.exists(self.resolved)

    def is_file(self) -> bool:
        return os.path.isfile(self.resolved)

    def is_directory(self) -> bool:
        return os.path.isdir(self.resolved)


@dataclass
class PathRejection:
    """Details about a rejected path."""

    path: str
    resolved: str
    reason: str
    rule: str  # e.g., 'path_traversal', 'symlink', 'denied_path', 'extension_blocked'


# ────────────────────────────────────────────────────────────────────
# Safety Validator
# ────────────────────────────────────────────────────────────────────


class SafetyValidator:
    """Validates file paths for safe read/write operations.

    Prevents:
    - Path traversal attacks ('../', symlinks escaping allowed dirs)
    - Access to sensitive system files (/etc/passwd, /etc/shadow, etc.)
    - Writing to blocked file extensions (.exe, .sh, etc.)
    - Files exceeding size limits

    Usage::

        validator = SafetyValidator(
            allowed_dirs=["/home/user/project", "/tmp"],
        )
        safe = validator.validate_read("data.txt")  # relative to CWD
        safe = validator.validate_write("/home/user/project/output.txt")
    """

    def __init__(
        self,
        allowed_dirs: Optional[List[str]] = None,
        denied_paths: Optional[Set[str]] = None,
        denied_prefixes: Optional[List[str]] = None,
        safe_extensions: Optional[FrozenSet[str]] = None,
        blocked_write_extensions: Optional[FrozenSet[str]] = None,
        max_file_size: int = _DEFAULT_MAX_FILE_SIZE,
        allow_symlinks: bool = False,
        home_as_root: bool = True,  # Use ~/.dragon as allowed root
    ) -> None:
        self._allowed_dirs: List[str] = [
            os.path.realpath(os.path.expanduser(d))
            for d in (allowed_dirs or [])
        ]

        # Default allowed: project root (discovered from cwd or env)
        # Plus ~/.dragon for cache/config files
        if not self._allowed_dirs:
            self._allowed_dirs = self._discover_allowed_dirs(home_as_root)

        self._denied_paths: Set[str] = {
            os.path.realpath(p) for p in (denied_paths or _DEFAULT_DENIED_PATHS)
        }
        self._denied_prefixes: List[str] = [
            os.path.realpath(os.path.expanduser(p)) + os.sep
            for p in (denied_prefixes or _DEFAULT_DENIED_PREFIXES)
        ]
        self._safe_extensions = safe_extensions or _DEFAULT_SAFE_EXTENSIONS
        self._blocked_write_extensions = (
            blocked_write_extensions or _DEFAULT_BLOCKED_WRITE_EXTENSIONS
        )
        self._max_file_size = max_file_size
        self._allow_symlinks = allow_symlinks

    @staticmethod
    def _discover_allowed_dirs(home_as_root: bool) -> List[str]:
        """Discover default allowed directories."""
        dirs: List[str] = []

        # Current working directory
        try:
            dirs.append(os.path.realpath(os.getcwd()))
        except Exception:
            pass

        # /tmp for temporary files
        dirs.append("/tmp")

        # ~/.dragon for agent data
        if home_as_root:
            dragon_home = os.path.expanduser("~/.dragon")
            dirs.append(os.path.realpath(dragon_home))

        # Environment override
        env_dir = os.getenv("DRAGON_SAFE_ROOT")
        if env_dir:
            try:
                dirs.append(os.path.realpath(os.path.expanduser(env_dir)))
            except Exception:
                pass

        return dirs

    # ── Public API ─────────────────────────────────────────────────

    def validate_read(self, path: Union[str, Path]) -> SafePath:
        """Validate a path for reading.

        Returns a SafePath if valid, raises PathRejection if blocked.
        Also checks file size limits.
        """
        path_str = str(path)
        resolved = os.path.realpath(os.path.expanduser(path_str))

        rejection = self._check_basic_safety(path_str, resolved)
        if rejection:
            raise ValueError(
                f"Read path rejected: {rejection.reason} "
                f"(path={path_str}, rule={rejection.rule})"
            )

        # Size check
        if os.path.isfile(resolved):
            try:
                size = os.path.getsize(resolved)
                if size > self._max_file_size:
                    raise ValueError(
                        f"Read path rejected: file too large "
                        f"({size} bytes, max {self._max_file_size})"
                    )
            except OSError:
                pass  # Can't stat, but path is safe — let caller handle

        return SafePath(path=path_str, resolved=resolved, is_safe=True)

    def validate_write(self, path: Union[str, Path]) -> SafePath:
        """Validate a path for writing.

        Returns a SafePath if valid, raises PathRejection if blocked.
        Includes extension checks for write safety.
        """
        path_str = str(path)
        resolved = os.path.realpath(os.path.expanduser(path_str))

        rejection = self._check_basic_safety(path_str, resolved)
        if rejection:
            raise ValueError(
                f"Write path rejected: {rejection.reason} "
                f"(path={path_str}, rule={rejection.rule})"
            )

        # Extension check for writes
        ext = os.path.splitext(path_str)[1].lower()
        if ext in self._blocked_write_extensions:
            raise ValueError(
                f"Write path rejected: blocked file extension '{ext}' "
                f"(path={path_str}, rule=extension_blocked)"
            )

        return SafePath(path=path_str, resolved=resolved, is_safe=True)

    def is_allowed_read(self, path: Union[str, Path]) -> bool:
        """Check if a path is allowed for reading without raising."""
        try:
            self.validate_read(path)
            return True
        except ValueError:
            return False

    def is_allowed_write(self, path: Union[str, Path]) -> bool:
        """Check if a path is allowed for writing without raising."""
        try:
            self.validate_write(path)
            return True
        except ValueError:
            return False

    def get_allowed_dirs(self) -> List[str]:
        """Return the list of allowed directories."""
        return list(self._allowed_dirs)

    def add_allowed_dir(self, directory: str) -> None:
        """Add a new allowed directory at runtime."""
        resolved = os.path.realpath(os.path.expanduser(directory))
        if resolved not in self._allowed_dirs:
            self._allowed_dirs.append(resolved)

    # ── Internal Checks ────────────────────────────────────────────

    def _check_basic_safety(
        self, original: str, resolved: str
    ) -> Optional[PathRejection]:
        """Run all safety checks on a resolved path."""
        # Check 1: Path traversal patterns
        rejection = self._check_traversal(original)
        if rejection:
            return rejection

        # Check 2: Symlink validation
        rejection = self._check_symlinks(original, resolved)
        if rejection:
            return rejection

        # Check 3: Denied paths
        rejection = self._check_denied(resolved)
        if rejection:
            return rejection

        # Check 4: Allowed dirs
        rejection = self._check_in_allowed(resolved)
        if rejection:
            return rejection

        return None

    def _check_traversal(self, path: str) -> Optional[PathRejection]:
        """Check for path traversal attacks."""
        # Null byte injection
        if "\x00" in path:
            return PathRejection(
                path=path, resolved="",
                reason="Null byte in path", rule="null_byte",
            )

        # Double-dot traversal that bypasses realpath
        parts = path.replace("\\", "/").split("/")
        depth = 0
        for part in parts:
            if part == "..":
                depth -= 1
            elif part and part != ".":
                depth += 1

        # Allow .. if it resolves within allowed dirs (handled by realpath)
        # But flag excessive traversal
        if depth < -10:
            return PathRejection(
                path=path, resolved="",
                reason="Excessive path traversal depth", rule="path_traversal",
            )

        return None

    def _check_symlinks(
        self, original: str, resolved: str
    ) -> Optional[PathRejection]:
        """Check for symlink escaping allowed directories."""
        if self._allow_symlinks:
            return None

        # Check if path contains symlinks by comparing realpath to abspath
        try:
            abspath = os.path.abspath(os.path.expanduser(original))
            if abspath != resolved:
                # Walk from root to check for intermediate symlinks
                current = Path(original).expanduser()
                parts_to_check = []
                while current != current.parent:
                    parts_to_check.append(current)
                    current = current.parent
                    if str(current) == "/":
                        break

                for part in reversed(parts_to_check):
                    if part.is_symlink():
                        link_target = os.path.realpath(str(part))
                        # Check if link target is within allowed dirs
                        target_in_allowed = any(
                            link_target == allowed
                            or link_target.startswith(allowed + os.sep)
                            for allowed in self._allowed_dirs
                        )
                        if not target_in_allowed:
                            return PathRejection(
                                path=original, resolved=resolved,
                                reason=f"Symlink at '{part}' points outside allowed directories",
                                rule="symlink_escape",
                            )
        except Exception:
            pass  # If we can't check, allow — os.realpath already resolved it

        return None

    def _check_denied(self, resolved: str) -> Optional[PathRejection]:
        """Check against denied paths and prefixes."""
        # Exact denied paths
        if resolved in self._denied_paths:
            return PathRejection(
                path=resolved, resolved=resolved,
                reason=f"Path is in the denied list",
                rule="denied_path",
            )

        # Denied prefixes
        for prefix in self._denied_prefixes:
            if resolved == prefix.rstrip(os.sep) or resolved.startswith(prefix):
                return PathRejection(
                    path=resolved, resolved=resolved,
                    reason=f"Path is under denied directory: {prefix.rstrip(os.sep)}",
                    rule="denied_prefix",
                )

        return None

    def _check_in_allowed(self, resolved: str) -> Optional[PathRejection]:
        """Check that path is within allowed directories."""
        for allowed in self._allowed_dirs:
            if resolved == allowed or resolved.startswith(allowed + os.sep):
                return None

        return PathRejection(
            path=resolved, resolved=resolved,
            reason=f"Path is outside allowed directories: {self._allowed_dirs}",
            rule="outside_allowed",
        )


# ────────────────────────────────────────────────────────────────────
# Convenience helpers
# ────────────────────────────────────────────────────────────────────


def create_default_validator(
    allowed_dirs: Optional[List[str]] = None,
) -> SafetyValidator:
    """Create a SafetyValidator with sensible defaults.

    Allowed dirs: current working directory + /tmp + ~/.dragon.
    Override with allowed_dirs parameter or DRAGON_SAFE_ROOT env var.
    """
    return SafetyValidator(allowed_dirs=allowed_dirs)


def quick_check_read(path: Union[str, Path]) -> SafePath:
    """Quick read path validation with default settings."""
    return create_default_validator().validate_read(path)


def quick_check_write(path: Union[str, Path]) -> SafePath:
    """Quick write path validation with default settings."""
    return create_default_validator().validate_write(path)


def is_file_extension_safe(path: Union[str, Path]) -> bool:
    """Check if a file extension is in the safe-to-read list."""
    ext = os.path.splitext(str(path))[1].lower()
    # Allow no-extension files too
    if not ext:
        return True
    return ext in _DEFAULT_SAFE_EXTENSIONS


def sanitize_filename(name: str) -> str:
    """Sanitize a filename to prevent injection.

    Removes path separators, null bytes, and control characters.
    """
    # Remove path separators
    name = name.replace("/", "_").replace("\\", "_")
    # Remove null bytes
    name = name.replace("\x00", "")
    # Remove control characters (< 0x20 except tab/newline are rarely wanted)
    name = re.sub(r"[\x00-\x1f]", "", name)
    # Remove leading dots (hidden files) unless explicitly wanted
    # Collapse whitespace
    name = re.sub(r"\s+", "_", name.strip())
    # Max length
    if len(name) > 255:
        name = name[:255]
    return name or "unnamed"


__all__ = [
    "SafetyValidator",
    "SafePath",
    "PathRejection",
    "create_default_validator",
    "quick_check_read",
    "quick_check_write",
    "is_file_extension_safe",
    "sanitize_filename",
]
