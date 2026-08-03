"""
Dragon Agent — Config Hot Reload.

Provides config file watching and hot-reload functionality:
- watch_config(): Monitor ~/.dragon/config.yaml for changes
- reload_config(callback): Trigger callback when config changes
- reload_env(): Reload .env file into os.environ

Uses watchdog (inotify on Linux) if available; falls back to polling.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional, Protocol
from pathlib import Path

logger = logging.getLogger("dragon.config_reload")


class _ConfigWatcher(Protocol):
    """Protocol for config watcher objects."""
    def stop(self) -> None: ...


# ── Default paths ────────────────────────────────────────────────────

def _default_config_path() -> Path:
    """Return the default config.yaml path: ~/.dragon/config.yaml."""
    return Path.home() / ".dragon" / "config.yaml"

def _default_env_path() -> Path:
    """Return the default .env path: ~/.dragon/.env."""
    return Path.home() / ".dragon" / ".env"


# ── Watchdog-based watcher ────────────────────────────────────────────

def _has_watchdog() -> bool:
    """Check whether watchdog is installed."""
    try:
        import watchdog  # noqa: F401
        return True
    except ImportError:
        return False


class _PollingWatcher:
    """Fallback file watcher using periodic mtime polling."""

    def __init__(self, path: Path, callback: Callable[[], None], interval: float = 2.0):
        self._path = path
        self._callback = callback
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_mtime: Optional[float] = None

    def start(self) -> None:
        """Start the polling watcher in a background daemon thread."""
        if self._thread is not None:
            return

        # Capture initial mtime
        if self._path.exists():
            self._last_mtime = self._path.stat().st_mtime

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Config polling watcher started (interval=%.1fs, path=%s)",
                     self._interval, self._path)

    def stop(self) -> None:
        """Stop the polling watcher."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Config polling watcher stopped")

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._interval)
            if self._stop_event.is_set():
                break
            try:
                self._check()
            except Exception:
                logger.debug("Poll check failed", exc_info=True)

    def _check(self) -> None:
        if not self._path.exists():
            return
        mtime = self._path.stat().st_mtime
        if self._last_mtime is not None and mtime != self._last_mtime:
            logger.info("Config file changed (detected via polling): %s", self._path)
            self._last_mtime = mtime
            try:
                self._callback()
            except Exception:
                logger.exception("Config reload callback failed")
        self._last_mtime = mtime


class _WatchdogWatcher:
    """File watcher using the watchdog library (inotify on Linux)."""

    def __init__(self, path: Path, callback: Callable[[], None]):
        self._path = path
        self._callback = callback
        self._observer = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the watchdog observer in a background thread."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            raise RuntimeError("watchdog is not installed")

        # Ensure parent directory exists
        watch_dir = self._path.parent
        if not watch_dir.exists():
            watch_dir.mkdir(parents=True, exist_ok=True)

        class _Handler(FileSystemEventHandler):
            def on_modified(self2, event):
                # Only fire for the exact config file (not other files in the dir)
                if os.path.abspath(event.src_path) == os.path.abspath(str(self._path)):
                    logger.info("Config file changed (detected via watchdog): %s", self._path)
                    try:
                        self._callback()
                    except Exception:
                        logger.exception("Config reload callback failed")

        self._observer = Observer()
        self._observer.schedule(_Handler(), str(watch_dir), recursive=False)
        self._observer.start()
        logger.info("Config watchdog started (path=%s)", self._path)

    def stop(self) -> None:
        """Stop the watchdog observer."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
        logger.info("Config watchdog stopped")


# ── Public API ────────────────────────────────────────────────────────

def reload_env(env_path: Optional[Path] = None) -> None:
    """Reload .env file into os.environ.

    Uses python-dotenv's load_dotenv with override=True so new values
    replace existing ones.

    Args:
        env_path: Path to .env file. Defaults to ~/.dragon/.env.
    """
    path = env_path or _default_env_path()

    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv not installed; cannot reload .env")
        return

    if not path.exists():
        logger.debug(".env file not found: %s", path)
        return

    load_dotenv(path, override=True)
    logger.info("Environment reloaded from %s", path)


def reload_config(
    callback: Optional[Callable[[], None]] = None,
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> Callable[[], None]:
    """Reload config.yaml and optionally .env, then invoke callback.

    This is the default reload handler. It:
    1. Logs the reload event
    2. Calls reload_env() to refresh environment variables
    3. Calls the provided callback (e.g., to re-instantiate DragonConfig)

    Args:
        callback: Optional callback after reload (e.g., update live config object).
        config_path: Path to config.yaml. Defaults to ~/.dragon/config.yaml.
        env_path: Path to .env file. Defaults to ~/.dragon/.env.

    Returns:
        A no-argument callable suitable for passing to watch_config().
    """
    cp = config_path or _default_config_path()
    ep = env_path or _default_env_path()

    def _reload() -> None:
        logger.info("🔄 Reloading Dragon configuration from %s", cp)
        reload_env(ep)

        if callback is not None:
            try:
                callback()
            except Exception:
                logger.exception("Config reload callback failed")

    return _reload


def watch_config(
    callback: Callable[[], None],
    config_path: Optional[Path] = None,
    use_watchdog: Optional[bool] = None,
    poll_interval: float = 2.0,
) -> _ConfigWatcher:
    """Start watching config.yaml for changes.

    When the config file is modified, ``callback()`` is invoked.

    Uses watchdog (inotify) if available; falls back to polling
    every ``poll_interval`` seconds.

    Args:
        callback: Called (no args) when config.yaml is modified.
        config_path: Path to watch. Defaults to ~/.dragon/config.yaml.
        use_watchdog: If True, require watchdog; if False, force polling.
                      If None (default), use watchdog when available.
        poll_interval: Seconds between polls when using polling fallback.

    Returns:
        A watcher object with ``.stop()`` method. The caller should keep
        a reference to it (e.g., in a global) to stop the watcher on shutdown.

    Example:
        watcher = watch_config(lambda: print("Config changed!"))
        # ... later ...
        watcher.stop()
    """
    path = config_path or _default_config_path()

    # Determine backend
    if use_watchdog is None:
        use_watchdog = _has_watchdog()

    if use_watchdog and _has_watchdog():
        watcher = _WatchdogWatcher(path, callback)
    else:
        if use_watchdog:
            logger.warning("watchdog not installed; falling back to polling")
        watcher = _PollingWatcher(path, callback, interval=poll_interval)

    watcher.start()
    return watcher


# ── Global singleton watcher (for the /reload command in interactive chat) ──

_global_watcher: Optional[_ConfigWatcher] = None


def start_global_watcher(
    callback: Callable[[], None],
    config_path: Optional[Path] = None,
) -> None:
    """Start a global config watcher (singleton).

    Safe to call multiple times — restarts the watcher if already running.

    Args:
        callback: Called when config changes.
        config_path: Path to watch.
    """
    global _global_watcher
    stop_global_watcher()
    _global_watcher = watch_config(callback, config_path=config_path)
    logger.info("Global config watcher started")


def stop_global_watcher() -> None:
    """Stop the global config watcher if running."""
    global _global_watcher
    if _global_watcher is not None:
        _global_watcher.stop()
        _global_watcher = None
        logger.info("Global config watcher stopped")


def trigger_reload(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> dict:
    """Manually trigger a config reload. Returns a status dict.

    This is the function backing the /reload slash command.

    Returns:
        dict with keys: 'config' (bool), 'env' (bool), 'config_path', 'env_path'
    """
    cp = config_path or _default_config_path()
    ep = env_path or _default_env_path()

    status = {
        "config": cp.exists(),
        "env": ep.exists(),
        "config_path": str(cp),
        "env_path": str(ep),
    }

    # Reload env
    if ep.exists():
        reload_env(ep)

    return status
