"""
Dragon Agent — Plugin Loader
============================

Auto-discovery and loading of plugins from configurable paths.
Provides both synchronous and asynchronous loading with environment
validation and dependency checking.

Usage::

    from dragon.plugin.loader import PluginLoader

    loader = PluginLoader()
    loader.discover()
    loader.load_all()
    loader.enable_all()
"""

from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dragon.plugin import (
    PluginManager,
    PluginManifest,
    LoadedPlugin,
    PluginKind,
    PluginState,
    PluginContext,
)

logger = logging.getLogger("dragon.plugin.loader")


# ══════════════════════════════════════════════════════════════════════
# Plugin Loader
# ══════════════════════════════════════════════════════════════════════


class PluginLoader:
    """High-level plugin loader with config-driven discovery and validation.

    Builds on :class:`PluginManager` to add:
        - Environment variable validation
        - Dependency checking
        - Config-driven enabled/disabled lists
        - Pip-installed plugin support (entry points)
        - Status reporting

    Usage::

        loader = PluginLoader(
            plugin_paths=["/custom/plugins"],
            enabled=["my-plugin"],
        )
        loader.discover()
        loader.load_all()
        print(loader.status_report())
    """

    # Default search order
    _DEFAULT_PLUGIN_DIRS = [
        "bundled",   # <package>/plugins/
        "user",      # ~/.dragon/plugins/
        "project",   # ./.dragon/plugins/
    ]

    def __init__(
        self,
        plugin_paths: Optional[List[str]] = None,
        enabled: Optional[List[str]] = None,
        disabled: Optional[List[str]] = None,
        manager: Optional[PluginManager] = None,
    ):
        """Initialize the plugin loader.

        Args:
            plugin_paths: Additional paths to scan for plugins.
            enabled: Plugin names to enable (allow-list). If ``None``, all are enabled.
            disabled: Plugin names to disable (deny-list). Takes precedence over *enabled*.
            manager: Pre-configured :class:`PluginManager` instance.
        """
        self._manager = manager or PluginManager()
        self._enabled: Optional[Set[str]] = set(enabled) if enabled is not None else None
        self._disabled: Set[str] = set(disabled) if disabled else set()
        self._extra_paths: List[Path] = [Path(p) for p in (plugin_paths or [])]
        self._pip_plugins: List[PluginManifest] = []
        self._load_errors: Dict[str, str] = {}

    # ── Discovery ────────────────────────────────────────────────────────

    def discover(self) -> List[PluginManifest]:
        """Scan all configured plugin sources for manifests.

        Scans in this order:
            1. Directory plugins (bundled, user, project, extra)
            2. Pip-installed plugins (entry_points group ``dragon_agent.plugins``)

        Returns:
            List of all discovered :class:`PluginManifest` objects.
        """
        manifests: List[PluginManifest] = []

        # 1. Directory plugins
        dir_manifests = self._manager.discover_all(extra_paths=self._extra_paths)
        manifests.extend(dir_manifests)

        # 2. Pip-installed plugins
        pip_manifests = self._discover_pip_plugins()
        self._pip_plugins = pip_manifests
        manifests.extend(pip_manifests)

        logger.info(
            "PluginLoader discovered %d plugin(s) (%d dir, %d pip)",
            len(manifests),
            len(dir_manifests),
            len(pip_manifests),
        )
        return manifests

    def _discover_pip_plugins(self) -> List[PluginManifest]:
        """Discover plugins installed as pip packages.

        Pip packages can expose plugins via the ``dragon_agent.plugins``
        entry-point group in their ``pyproject.toml`` or ``setup.cfg``.

        Example ``pyproject.toml``::

            [project.entry-points."dragon_agent.plugins"]
            my_plugin = "my_plugin:register"
        """
        manifests: List[PluginManifest] = []
        entry_point_group = "dragon_agent.plugins"

        try:
            entry_points = importlib.metadata.entry_points(group=entry_point_group)
        except TypeError:
            # Python < 3.12: entry_points() doesn't support group parameter
            try:
                all_eps = importlib.metadata.entry_points()
                entry_points = [
                    ep for ep in all_eps if ep.group == entry_point_group
                ]
            except Exception:
                entry_points = []

        for ep in entry_points:
            name = ep.name
            try:
                # Load the entry point to get register function
                register_fn = ep.load()
                if not callable(register_fn):
                    logger.warning(
                        "Pip plugin '%s': entry point is not callable", name
                    )
                    continue

                # Build a minimal manifest
                manifest = PluginManifest(
                    name=name,
                    version="0.1.0",
                    kind=PluginKind.STANDALONE,
                    description=f"Pip-installed plugin: {ep.value}",
                    source="entrypoint",
                )
                manifests.append(manifest)

                # Register with the manager
                plugin = LoadedPlugin(manifest=manifest)
                self._manager._plugins[name] = plugin

                # Create context and call register
                ctx = PluginContext(manifest, self._manager)
                try:
                    register_fn(ctx)
                    plugin.state = PluginState.LOADED
                    logger.info("Pip plugin '%s' loaded via entry point", name)
                except Exception as exc:
                    plugin.state = PluginState.ERROR
                    plugin.error = str(exc)
                    logger.error("Pip plugin '%s' failed: %s", name, exc)

            except Exception as exc:
                logger.warning(
                    "Failed to load pip plugin entry point '%s': %s", name, exc
                )

        return manifests

    # ── Loading ──────────────────────────────────────────────────────────

    def load_all(self) -> Dict[str, LoadedPlugin]:
        """Load all discovered plugins that pass validation.

        Skips disabled plugins and those missing environment variables.

        Returns:
            Dict of plugin_name → :class:`LoadedPlugin` for successfully loaded plugins.
        """
        self._load_errors = {}
        results: Dict[str, LoadedPlugin] = {}

        for plugin in self._manager.list_plugins():
            name = plugin.manifest.name

            # Check disabled list (deny-list wins)
            if name in self._disabled:
                logger.info("Plugin '%s' is in disabled list — skipping", name)
                plugin.state = PluginState.DISABLED
                continue

            # Check enabled allow-list
            if self._enabled is not None and name not in self._enabled:
                logger.debug("Plugin '%s' not in enabled list — skipping", name)
                plugin.state = PluginState.DISABLED
                continue

            # Validate environment
            missing_env = self._check_required_env(plugin.manifest)
            if missing_env:
                self._load_errors[name] = (
                    f"Missing required environment variables: {', '.join(missing_env)}"
                )
                plugin.state = PluginState.ERROR
                plugin.error = self._load_errors[name]
                logger.warning(
                    "Plugin '%s' skipped: %s", name, plugin.error
                )
                continue

            # Load the plugin
            loaded = self._manager.load_plugin(name)
            if loaded and loaded.state not in (PluginState.ERROR,):
                results[name] = loaded
            else:
                if loaded and loaded.error:
                    self._load_errors[name] = loaded.error

        logger.info(
            "PluginLoader loaded %d/%d plugin(s)",
            len(results),
            self._manager.plugin_count,
        )
        return results

    def enable_all(self) -> int:
        """Enable all loaded plugins.

        Returns:
            Number of plugins successfully enabled.
        """
        count = 0
        for plugin in self._manager.list_plugins():
            if plugin.state == PluginState.LOADED:
                if self._manager.enable_plugin(plugin.manifest.name):
                    count += 1
        logger.info("PluginLoader enabled %d plugin(s)", count)
        return count

    async def load_all_async(self) -> Dict[str, LoadedPlugin]:
        """Async version of :meth:`load_all` — runs discovery + loading concurrently.

        Useful for startup where multiple plugins may have async ``register()``
        functions.

        Returns:
            Dict of plugin_name → :class:`LoadedPlugin`.
        """
        import asyncio

        self.discover()

        async def _load_one(name: str) -> Optional[Tuple[str, LoadedPlugin]]:
            loop = asyncio.get_running_loop()
            # Run the sync load in a thread to avoid blocking
            result = await loop.run_in_executor(
                None, self._manager.load_plugin, name
            )
            if result and result.state not in (PluginState.ERROR,):
                return (name, result)
            return None

        tasks = [
            _load_one(p.manifest.name)
            for p in self._manager.list_plugins()
            if p.manifest.name not in self._disabled
            and (self._enabled is None or p.manifest.name in self._enabled)
        ]

        completed = await asyncio.gather(*tasks)
        results = {
            name: plugin
            for name, plugin in (r for r in completed if r is not None)
        }
        return results

    # ── Validation ───────────────────────────────────────────────────────

    @staticmethod
    def _check_required_env(manifest: PluginManifest) -> List[str]:
        """Check that all required environment variables are set.

        Returns:
            List of missing variable names (empty if all present).
        """
        missing: List[str] = []
        for env_var in manifest.requires_env:
            if not os.getenv(env_var):
                missing.append(env_var)
        return missing

    # ── Status & Reporting ───────────────────────────────────────────────

    def status_report(self) -> str:
        """Generate a human-readable plugin status report.

        Returns:
            Multi-line string with plugin statuses.
        """
        lines = ["🐉 Dragon Agent Plugin Status", "=" * 50]

        plugins = self._manager.list_plugins()
        if not plugins:
            lines.append("\nNo plugins discovered.")
            return "\n".join(lines)

        # Group by state
        by_state: Dict[PluginState, List[LoadedPlugin]] = {
            PluginState.ENABLED: [],
            PluginState.LOADED: [],
            PluginState.DISCOVERED: [],
            PluginState.DISABLED: [],
            PluginState.ERROR: [],
        }
        for p in plugins:
            by_state[p.state].append(p)

        status_icons = {
            PluginState.ENABLED: "✅",
            PluginState.LOADED: "📦",
            PluginState.DISCOVERED: "🔍",
            PluginState.DISABLED: "⏸️",
            PluginState.ERROR: "❌",
        }

        for state, icon in status_icons.items():
            group = by_state[state]
            if not group:
                continue
            lines.append(f"\n{icon} {state.value.upper()} ({len(group)})")
            for p in sorted(group, key=lambda x: x.manifest.name):
                m = p.manifest
                extra = []
                if p.load_time_ms:
                    extra.append(f"{p.load_time_ms:.0f}ms")
                if m.kind != PluginKind.STANDALONE:
                    extra.append(m.kind.value)
                if m.version:
                    extra.append(f"v{m.version}")
                extra_str = f" ({', '.join(extra)})" if extra else ""
                lines.append(f"  • {m.name}{extra_str}")
                if p.error:
                    lines.append(f"    Error: {p.error}")
                if m.description:
                    lines.append(f"    {m.description[:120]}")

        lines.append(f"\n{'=' * 50}")
        lines.append(
            f"Total: {len(plugins)} | "
            f"Enabled: {len(by_state[PluginState.ENABLED])} | "
            f"Errors: {len(by_state[PluginState.ERROR])}"
        )
        return "\n".join(lines)

    def list_enabled_names(self) -> List[str]:
        """Get names of all enabled plugins."""
        return [p.manifest.name for p in self._manager.list_enabled_plugins()]

    # ── Accessors ────────────────────────────────────────────────────────

    @property
    def manager(self) -> PluginManager:
        """The underlying :class:`PluginManager` instance."""
        return self._manager

    def get_plugin(self, name: str) -> Optional[LoadedPlugin]:
        """Get a plugin by name."""
        return self._manager.get_plugin(name)

    def reload_plugin(self, name: str) -> Optional[LoadedPlugin]:
        """Hot-reload a specific plugin."""
        return self._manager.reload_plugin(name)
