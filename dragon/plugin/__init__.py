"""
Dragon Agent — Plugin System
============================

Lightweight plugin architecture enabling extensibility through
auto-discovered, manifest-driven plugin modules.

Architecture::

    PluginManager
        ├── Discover plugins from config paths
        │   ├── Bundled: <repo>/plugins/<name>/
        │   ├── User:    ~/.dragon/plugins/<name>/
        │   └── Project: ./.dragon/plugins/<name>/
        ├── Load PluginManifest from plugin.yaml
        ├── Execute register(ctx) in __init__.py
        ├── Manage lifecycle (enable, disable, reload)
        └── Fire hooks via HookSystem

Plugin Types Supported::

    model_provider   — LLM inference backend (OpenAI, Anthropic, DeepSeek, …)
    memory_backend   — Memory storage provider (ChromaDB, Milvus, …)
    tool             — Custom tool sets
    gateway_adapter  — Messaging platform adapter (new channel integration)

Lifecycle::

    1. Discovery  → scan plugin directories for plugin.yaml
    2. Validation → check manifest, platform compatibility
    3. Loading    → import module, call register(ctx)
    4. Enabling   → activate hooks/tools
    5. Reloading  → hot-reload on file change (optional file watcher)
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type

logger = logging.getLogger("dragon.plugin")


# ══════════════════════════════════════════════════════════════════════
# Plugin Kind
# ══════════════════════════════════════════════════════════════════════


class PluginKind(Enum):
    """Type of plugin — determines loading and lifecycle rules."""

    MODEL_PROVIDER = "model_provider"   # LLM backend provider
    MEMORY_BACKEND = "memory_backend"    # Memory storage backend
    TOOL = "tool"                        # Custom tool set
    GATEWAY_ADAPTER = "gateway_adapter"  # Messaging platform adapter
    STANDALONE = "standalone"            # General-purpose plugin


_VALID_PLUGIN_KINDS: Set[str] = {k.value for k in PluginKind}


# ══════════════════════════════════════════════════════════════════════
# Plugin Lifecycle State
# ══════════════════════════════════════════════════════════════════════


class PluginState(Enum):
    """Runtime state of a plugin."""

    DISCOVERED = "discovered"  # Found manifest, not yet loaded
    LOADED = "loaded"          # Module imported, register() called
    ENABLED = "enabled"        # Active — hooks firing, tools registered
    DISABLED = "disabled"      # Explicitly disabled
    ERROR = "error"            # Failed to load or register
    RELOADING = "reloading"    # Currently being hot-reloaded


# ══════════════════════════════════════════════════════════════════════
# Plugin Manifest
# ══════════════════════════════════════════════════════════════════════


@dataclass
class PluginManifest:
    """Parsed representation of a ``plugin.yaml`` manifest file.

    Attributes:
        name: Unique plugin identifier (e.g. ``"deepseek-provider"``).
        version: Semantic version string (e.g. ``"1.0.0"``).
        kind: Plugin kind from ``PluginKind``.
        description: Human-readable description.
        author: Plugin author name.
        provides_hooks: Hook names this plugin registers listeners for.
        provides_tools: Tool names this plugin provides.
        requires_env: Environment variables required for operation.
        dependencies: Python package dependencies (optional).
        path: Filesystem path to the plugin directory.
        source: Discovery source (``"bundled"``, ``"user"``, ``"project"``, ``"entrypoint"``).
    """

    name: str
    version: str = "0.1.0"
    kind: PluginKind = PluginKind.STANDALONE
    description: str = ""
    author: str = ""
    provides_hooks: List[str] = field(default_factory=list)
    provides_tools: List[str] = field(default_factory=list)
    requires_env: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    path: Optional[Path] = None
    source: str = "user"


# ══════════════════════════════════════════════════════════════════════
# Loaded Plugin
# ══════════════════════════════════════════════════════════════════════


@dataclass
class LoadedPlugin:
    """Runtime state for a single plugin.

    Attributes:
        manifest: The parsed plugin manifest.
        state: Current lifecycle state.
        module: Imported Python module (or ``None`` if not loaded).
        tools_registered: Tool names registered by this plugin.
        hooks_registered: Hook names registered by this plugin.
        error: Error message if state is ``ERROR``.
        load_time_ms: Time taken to load this plugin (ms).
        last_modified: Filesystem mtime for hot-reload detection.
    """

    manifest: PluginManifest
    state: PluginState = PluginState.DISCOVERED
    module: Optional[Any] = None
    tools_registered: List[str] = field(default_factory=list)
    hooks_registered: List[str] = field(default_factory=list)
    error: Optional[str] = None
    load_time_ms: float = 0.0
    last_modified: float = 0.0


# ══════════════════════════════════════════════════════════════════════
# Plugin Context (passed to register() functions)
# ══════════════════════════════════════════════════════════════════════


class PluginContext:
    """Facade given to plugins so they can register hooks, tools, and providers.

    Plugins receive a ``PluginContext`` in their ``register(ctx)`` function
    and use it to integrate with the Dragon Agent runtime.

    Example plugin ``__init__.py``::

        def register(ctx):
            ctx.register_hook("on_error", my_error_handler)
            ctx.register_tool("my_tool", my_tool_function)
    """

    def __init__(self, manifest: PluginManifest, manager: "PluginManager") -> None:
        self.manifest = manifest
        self._manager = manager

    def register_hook(self, hook_name: str, callback: Callable[..., Any]) -> None:
        """Register a callback for a named lifecycle hook.

        Args:
            hook_name: Hook name (e.g. ``"before_request"``, ``"after_response"``).
            callback: Callable that receives hook-specific kwargs.
        """
        self._manager.register_hook(self.manifest.name, hook_name, callback)

    def register_tool(self, tool_name: str, tool_fn: Callable[..., Any]) -> None:
        """Register a tool that will appear in the agent's tool registry.

        Args:
            tool_name: Unique tool identifier.
            tool_fn: The callable implementing the tool.
        """
        self._manager.register_tool(self.manifest.name, tool_name, tool_fn)

    def register_provider(self, provider_name: str, provider: Any) -> None:
        """Register an LLM provider backend.

        Args:
            provider_name: Provider identifier (e.g. ``"deepseek"``).
            provider: A :class:`dragon.provider.BaseProvider` instance.
        """
        self._manager.register_provider(self.manifest.name, provider_name, provider)

    def register_memory_backend(self, backend_name: str, backend: Any) -> None:
        """Register a memory storage backend.

        Args:
            backend_name: Backend identifier (e.g. ``"milvus"``).
            backend: Memory backend instance.
        """
        self._manager.register_memory_backend(
            self.manifest.name, backend_name, backend
        )

    def register_gateway_adapter(self, adapter_name: str, adapter: Any) -> None:
        """Register a gateway messaging platform adapter.

        Args:
            adapter_name: Adapter identifier (e.g. ``"line"``).
            adapter: Gateway adapter instance.
        """
        self._manager.register_gateway_adapter(
            self.manifest.name, adapter_name, adapter
        )

    def log(self, level: str, message: str, **extra) -> None:
        """Emit a log entry with plugin attribution.

        Args:
            level: Log level (``"debug"``, ``"info"``, ``"warning"``, ``"error"``).
            message: Log message.
        """
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn("[%s] %s", self.manifest.name, message)


# ══════════════════════════════════════════════════════════════════════
# Plugin Manager
# ══════════════════════════════════════════════════════════════════════


class PluginManager:
    """Central plugin registry and lifecycle manager.

    Discovers, loads, enables, and hot-reloads plugins from
    configured discovery paths.

    Usage::

        manager = PluginManager()
        manager.discover_all()
        manager.load_all()

        # Fire a hook
        await manager.invoke_hook("before_request", messages=[...])

        # Hot-reload changed plugins
        await manager.check_and_reload()
    """

    def __init__(self) -> None:
        # All discovered plugins (name → LoadedPlugin)
        self._plugins: Dict[str, LoadedPlugin] = {}

        # Hook registry (hook_name → list of (plugin_name, callback))
        self._hooks: Dict[str, List[tuple[str, Callable[..., Any]]]] = {}

        # Tool registry (tool_name → (plugin_name, tool_fn))
        self._tools: Dict[str, tuple[str, Callable[..., Any]]] = {}

        # Provider registry (provider_name → (plugin_name, provider))
        self._providers: Dict[str, tuple[str, Any]] = {}

        # Memory backends registered by plugins
        self._memory_backends: Dict[str, tuple[str, Any]] = {}

        # Gateway adapters registered by plugins
        self._gateway_adapters: Dict[str, tuple[str, Any]] = {}

        # Hot-reload support
        self._watcher_thread: Optional[threading.Thread] = None
        self._watcher_stop: threading.Event = threading.Event()

        self._lock = threading.RLock()
        logger.info("PluginManager initialized")

    # ── Discovery ────────────────────────────────────────────────────────

    def discover_all(self, extra_paths: Optional[List[Path]] = None) -> List[PluginManifest]:
        """Scan all configured plugin directories for manifests.

        Search order (later overrides earlier):
            1. Bundled plugins: ``<package>/plugins/``
            2. User plugins: ``~/.dragon/plugins/``
            3. Project plugins: ``./.dragon/plugins/``
            4. Extra paths: from *extra_paths* parameter

        Returns:
            List of discovered :class:`PluginManifest` objects.
        """
        discovered: List[PluginManifest] = []

        # Build search paths
        search_paths: List[Path] = []

        # 1. Bundled plugins
        bundled = self._get_bundled_plugins_dir()
        if bundled and bundled.is_dir():
            search_paths.append(bundled)

        # 2. User plugins
        user = Path.home() / ".dragon" / "plugins"
        if user.is_dir():
            search_paths.append(user)

        # 3. Project plugins
        project = Path.cwd() / ".dragon" / "plugins"
        if project.is_dir():
            search_paths.append(project)

        # 4. Extra paths
        if extra_paths:
            search_paths.extend(extra_paths)

        # Scan each path
        for base_path in search_paths:
            source = "bundled"
            if str(user) in str(base_path):
                source = "user"
            elif str(project) in str(base_path):
                source = "project"

            if not base_path.is_dir():
                continue

            for item in sorted(base_path.iterdir()):
                if not item.is_dir():
                    continue
                manifest_path = item / "plugin.yaml"
                if not manifest_path.is_file():
                    # Also try plugin.toml
                    manifest_path = item / "plugin.toml"
                if not manifest_path.is_file():
                    continue

                try:
                    manifest = self._parse_manifest(manifest_path, item, source)
                    discovered.append(manifest)
                    logger.debug(
                        "Discovered plugin '%s' (kind=%s, source=%s)",
                        manifest.name, manifest.kind.value, source,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to parse manifest from %s: %s",
                        manifest_path, exc,
                    )

        # Register manifests (user/project override bundled on name collision)
        with self._lock:
            for manifest in discovered:
                existing = self._plugins.get(manifest.name)
                if existing and existing.manifest.source in ("bundled",):
                    # Override bundled with user/project
                    logger.info(
                        "Plugin '%s' from '%s' overrides bundled version",
                        manifest.name, manifest.source,
                    )
                self._plugins[manifest.name] = LoadedPlugin(manifest=manifest)

        logger.info(
            "Discovered %d plugin(s) across %d search path(s)",
            len(discovered), len(search_paths),
        )
        return discovered

    # ── Loading ──────────────────────────────────────────────────────────

    def load_plugin(self, name: str) -> Optional[LoadedPlugin]:
        """Load a single plugin by name.

        Imports the plugin's ``__init__.py`` module and calls
        its ``register(ctx)`` function if defined.

        Args:
            name: Plugin name from the manifest.

        Returns:
            :class:`LoadedPlugin` on success, ``None`` if not found.
        """
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                logger.error("Plugin '%s' not found", name)
                return None
            if plugin.state == PluginState.ENABLED:
                logger.debug("Plugin '%s' already loaded", name)
                return plugin

        manifest = plugin.manifest
        if not manifest.path or not manifest.path.is_dir():
            plugin.state = PluginState.ERROR
            plugin.error = f"Plugin directory not found: {manifest.path}"
            logger.error("Plugin '%s': %s", name, plugin.error)
            return plugin

        init_file = manifest.path / "__init__.py"
        if not init_file.is_file():
            plugin.state = PluginState.ERROR
            plugin.error = f"No __init__.py in plugin directory: {manifest.path}"
            logger.error("Plugin '%s': %s", name, plugin.error)
            return plugin

        start = time.monotonic()

        try:
            # Import the plugin module
            module = self._import_plugin_module(name, init_file)

            # Call register(ctx) if defined
            ctx = PluginContext(manifest, self)
            register_fn = getattr(module, "register", None)
            if callable(register_fn):
                register_fn(ctx)
                logger.debug("Plugin '%s': register() executed", name)
            else:
                logger.debug(
                    "Plugin '%s': no register() function found (passive plugin)",
                    name,
                )

            # Track file mtime for hot-reload
            plugin.last_modified = init_file.stat().st_mtime

            with self._lock:
                plugin.module = module
                plugin.state = PluginState.LOADED
                plugin.load_time_ms = (time.monotonic() - start) * 1000
                plugin.error = None

            logger.info(
                "Plugin '%s' loaded in %.1f ms (kind=%s)",
                name, plugin.load_time_ms, manifest.kind.value,
            )
            return plugin

        except Exception as exc:
            with self._lock:
                plugin.state = PluginState.ERROR
                plugin.error = str(exc)
            logger.error(
                "Plugin '%s' failed to load: %s",
                name, exc, exc_info=True,
            )
            return plugin

    def load_all(self) -> Dict[str, LoadedPlugin]:
        """Load all discovered plugins.

        Skips plugins already in ENABLED or ERROR state.
        Disabled plugins are skipped.

        Returns:
            Dict mapping plugin name to :class:`LoadedPlugin`.
        """
        results: Dict[str, LoadedPlugin] = {}
        for name in list(self._plugins.keys()):
            plugin = self._plugins[name]
            if plugin.state in (PluginState.DISABLED, PluginState.ERROR):
                continue
            loaded = self.load_plugin(name)
            if loaded:
                results[name] = loaded
        return results

    def enable_plugin(self, name: str) -> bool:
        """Activate a loaded plugin.

        Enables all registered hooks and tools for the plugin.

        Args:
            name: Plugin name.

        Returns:
            ``True`` if successfully enabled.
        """
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                logger.error("Plugin '%s' not found", name)
                return False
            if plugin.state == PluginState.ENABLED:
                return True
            if plugin.state not in (PluginState.LOADED,):
                logger.error(
                    "Plugin '%s' cannot be enabled (state=%s)",
                    name, plugin.state.value,
                )
                return False
            plugin.state = PluginState.ENABLED

        logger.info("Plugin '%s' enabled", name)
        return True

    def disable_plugin(self, name: str) -> bool:
        """Deactivate a plugin.

        Hooks and tools remain registered but will not fire.

        Args:
            name: Plugin name.

        Returns:
            ``True`` if successfully disabled.
        """
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                return False
            plugin.state = PluginState.DISABLED

        logger.info("Plugin '%s' disabled", name)
        return True

    def unload_plugin(self, name: str) -> bool:
        """Completely unload a plugin and remove its registrations.

        Args:
            name: Plugin name.

        Returns:
            ``True`` if successfully unloaded.
        """
        with self._lock:
            plugin = self._plugins.pop(name, None)
            if plugin is None:
                return False

            # Remove hooks
            for hook_name in list(self._hooks.keys()):
                self._hooks[hook_name] = [
                    (pn, cb)
                    for pn, cb in self._hooks[hook_name]
                    if pn != name
                ]
                if not self._hooks[hook_name]:
                    del self._hooks[hook_name]

            # Remove tools
            for tool_name in list(self._tools.keys()):
                if self._tools[tool_name][0] == name:
                    del self._tools[tool_name]

            # Remove providers
            for prov_name in list(self._providers.keys()):
                if self._providers[prov_name][0] == name:
                    del self._providers[prov_name]

            # Remove memory backends
            for backend_name in list(self._memory_backends.keys()):
                if self._memory_backends[backend_name][0] == name:
                    del self._memory_backends[backend_name]

            # Remove gateway adapters
            for adapter_name in list(self._gateway_adapters.keys()):
                if self._gateway_adapters[adapter_name][0] == name:
                    del self._gateway_adapters[adapter_name]

        logger.info("Plugin '%s' unloaded", name)
        return True

    # ── Hot Reload ───────────────────────────────────────────────────────

    def reload_plugin(self, name: str) -> Optional[LoadedPlugin]:
        """Hot-reload a plugin by unloading and re-loading it.

        Args:
            name: Plugin name.

        Returns:
            :class:`LoadedPlugin` on success, ``None`` on failure.
        """
        with self._lock:
            plugin = self._plugins.get(name)
            if plugin is None:
                return None
            manifest = plugin.manifest
            was_enabled = plugin.state == PluginState.ENABLED

        logger.info("Hot-reloading plugin '%s'...", name)

        # Unload
        self.unload_plugin(name)

        # Re-register manifest
        with self._lock:
            # Invalidate any cached module
            if name in sys.modules:
                del sys.modules[name]
            self._plugins[name] = LoadedPlugin(manifest=manifest)

        # Reload
        result = self.load_plugin(name)
        if result and was_enabled:
            self.enable_plugin(name)

        return result

    def check_and_reload(self) -> List[str]:
        """Check all loaded plugins for filesystem changes and reload.

        Compares ``__init__.py`` modification times.

        Returns:
            List of plugin names that were reloaded.
        """
        reloaded: List[str] = []
        for name, plugin in list(self._plugins.items()):
            if plugin.state not in (PluginState.LOADED, PluginState.ENABLED):
                continue
            manifest = plugin.manifest
            if not manifest.path:
                continue
            init_file = manifest.path / "__init__.py"
            if not init_file.is_file():
                continue

            current_mtime = init_file.stat().st_mtime
            if current_mtime > plugin.last_modified:
                logger.info(
                    "Plugin '%s' changed on disk — triggering hot-reload",
                    name,
                )
                result = self.reload_plugin(name)
                if result and result.state not in (PluginState.ERROR,):
                    reloaded.append(name)

        if reloaded:
            logger.info("Hot-reloaded %d plugin(s): %s", len(reloaded), reloaded)
        return reloaded

    def start_file_watcher(self, interval_secs: float = 2.0) -> None:
        """Start a background thread that watches for plugin file changes.

        Args:
            interval_secs: Polling interval for filesystem checks.
        """
        if self._watcher_thread and self._watcher_thread.is_alive():
            logger.warning("File watcher already running")
            return

        self._watcher_stop.clear()

        def _watch_loop() -> None:
            logger.info(
                "Plugin file watcher started (interval=%.1fs)", interval_secs
            )
            while not self._watcher_stop.is_set():
                try:
                    self.check_and_reload()
                except Exception as exc:
                    logger.error("File watcher error: %s", exc)
                self._watcher_stop.wait(interval_secs)

        self._watcher_thread = threading.Thread(
            target=_watch_loop, daemon=True, name="dragon-plugin-watcher"
        )
        self._watcher_thread.start()

    def stop_file_watcher(self) -> None:
        """Stop the background file watcher thread."""
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_stop.set()
            self._watcher_thread.join(timeout=5.0)
            logger.info("Plugin file watcher stopped")

    # ── Hook Management ──────────────────────────────────────────────────

    def register_hook(
        self, plugin_name: str, hook_name: str, callback: Callable[..., Any]
    ) -> None:
        """Register a hook callback from a plugin.

        Called internally via ``PluginContext.register_hook()``.
        """
        with self._lock:
            self._hooks.setdefault(hook_name, []).append((plugin_name, callback))
            # Update plugin metadata
            plugin = self._plugins.get(plugin_name)
            if plugin and hook_name not in plugin.hooks_registered:
                plugin.hooks_registered.append(hook_name)
        logger.debug(
            "Plugin '%s' registered hook '%s'", plugin_name, hook_name
        )

    def invoke_hook(self, hook_name: str, **kwargs) -> List[Any]:
        """Synchronously invoke all callbacks registered for a hook.

        Args:
            hook_name: Hook to fire (e.g. ``"before_request"``).
            **kwargs: Keyword arguments passed to each callback.

        Returns:
            List of return values from all callbacks.
        """
        results: List[Any] = []
        listeners = self._hooks.get(hook_name, [])

        for plugin_name, callback in listeners:
            # Skip disabled plugins
            plugin = self._plugins.get(plugin_name)
            if plugin and plugin.state != PluginState.ENABLED:
                continue

            try:
                result = callback(**kwargs)
                results.append(result)
            except Exception as exc:
                logger.warning(
                    "Plugin '%s' hook '%s' raised: %s",
                    plugin_name, hook_name, exc,
                )

        return results

    async def invoke_hook_async(self, hook_name: str, **kwargs) -> List[Any]:
        """Asynchronously invoke all callbacks for a hook.

        Supports both sync and async callbacks.

        Args:
            hook_name: Hook to fire.
            **kwargs: Keyword arguments passed to each callback.

        Returns:
            List of return values from all callbacks.
        """
        import asyncio

        results: List[Any] = []
        listeners = self._hooks.get(hook_name, [])

        for plugin_name, callback in listeners:
            plugin = self._plugins.get(plugin_name)
            if plugin and plugin.state != PluginState.ENABLED:
                continue

            try:
                if asyncio.iscoroutinefunction(callback):
                    result = await callback(**kwargs)
                else:
                    result = callback(**kwargs)
                results.append(result)
            except Exception as exc:
                logger.warning(
                    "Plugin '%s' async hook '%s' raised: %s",
                    plugin_name, hook_name, exc,
                )

        return results

    # ── Tool Registration ────────────────────────────────────────────────

    def register_tool(
        self, plugin_name: str, tool_name: str, tool_fn: Callable[..., Any]
    ) -> None:
        """Register a tool from a plugin."""
        with self._lock:
            self._tools[tool_name] = (plugin_name, tool_fn)
            plugin = self._plugins.get(plugin_name)
            if plugin and tool_name not in plugin.tools_registered:
                plugin.tools_registered.append(tool_name)
        logger.debug(
            "Plugin '%s' registered tool '%s'", plugin_name, tool_name
        )

    def get_tool(self, tool_name: str) -> Optional[Callable[..., Any]]:
        """Get a tool by name (from any plugin)."""
        entry = self._tools.get(tool_name)
        if entry is None:
            return None
        plugin_name, tool_fn = entry
        plugin = self._plugins.get(plugin_name)
        if plugin and plugin.state != PluginState.ENABLED:
            return None
        return tool_fn

    def list_tools(self) -> Dict[str, str]:
        """List all registered tools with their plugin names."""
        return {
            name: entry[0]
            for name, entry in self._tools.items()
            if self._plugins.get(entry[0], LoadedPlugin(PluginManifest(name=entry[0]))).state == PluginState.ENABLED
        }

    # ── Provider Registration ───────────────────────────────────────────

    def register_provider(
        self, plugin_name: str, provider_name: str, provider: Any
    ) -> None:
        """Register an LLM provider from a plugin."""
        with self._lock:
            self._providers[provider_name] = (plugin_name, provider)
        logger.debug(
            "Plugin '%s' registered provider '%s'", plugin_name, provider_name
        )

    def get_provider(self, provider_name: str) -> Optional[Any]:
        """Get a registered provider."""
        entry = self._providers.get(provider_name)
        if entry is None:
            return None
        plugin_name, provider = entry
        plugin = self._plugins.get(plugin_name)
        if plugin and plugin.state != PluginState.ENABLED:
            return None
        return provider

    def list_providers(self) -> Dict[str, str]:
        """List all registered providers with source plugin names."""
        return {
            name: entry[0]
            for name, entry in self._providers.items()
            if self._plugins.get(entry[0], LoadedPlugin(PluginManifest(name=entry[0]))).state == PluginState.ENABLED
        }

    # ── Memory Backend Registration ──────────────────────────────────────

    def register_memory_backend(
        self, plugin_name: str, backend_name: str, backend: Any
    ) -> None:
        """Register a memory backend from a plugin."""
        with self._lock:
            self._memory_backends[backend_name] = (plugin_name, backend)
        logger.debug(
            "Plugin '%s' registered memory backend '%s'",
            plugin_name, backend_name,
        )

    def get_memory_backend(self, backend_name: str) -> Optional[Any]:
        """Get a registered memory backend."""
        entry = self._memory_backends.get(backend_name)
        if entry is None:
            return None
        plugin_name, backend = entry
        return backend

    # ── Gateway Adapter Registration ─────────────────────────────────────

    def register_gateway_adapter(
        self, plugin_name: str, adapter_name: str, adapter: Any
    ) -> None:
        """Register a gateway adapter from a plugin."""
        with self._lock:
            self._gateway_adapters[adapter_name] = (plugin_name, adapter)
        logger.debug(
            "Plugin '%s' registered gateway adapter '%s'",
            plugin_name, adapter_name,
        )

    def get_gateway_adapter(self, adapter_name: str) -> Optional[Any]:
        """Get a registered gateway adapter."""
        entry = self._gateway_adapters.get(adapter_name)
        if entry is None:
            return None
        plugin_name, adapter = entry
        return adapter

    # ── Query ────────────────────────────────────────────────────────────

    def get_plugin(self, name: str) -> Optional[LoadedPlugin]:
        """Get a plugin by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> List[LoadedPlugin]:
        """List all plugins."""
        return list(self._plugins.values())

    def list_enabled_plugins(self) -> List[LoadedPlugin]:
        """List only enabled plugins."""
        return [p for p in self._plugins.values() if p.state == PluginState.ENABLED]

    @property
    def plugin_count(self) -> int:
        """Total number of discovered plugins."""
        return len(self._plugins)

    @property
    def enabled_plugin_count(self) -> int:
        """Number of active plugins."""
        return sum(1 for p in self._plugins.values() if p.state == PluginState.ENABLED)

    # ── Internal Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _get_bundled_plugins_dir() -> Optional[Path]:
        """Locate the bundled plugins directory.

        Checks ``DRAGON_BUNDLED_PLUGINS`` env var, then falls back to
        the ``plugins/`` subdirectory relative to the package.
        """
        env_override = os.getenv("DRAGON_BUNDLED_PLUGINS")
        if env_override:
            return Path(env_override)

        # Fallback: find plugins/ relative to the dragon package
        try:
            import dragon

            pkg_dir = Path(dragon.__file__).parent
            bundled = pkg_dir.parent / "plugins"
            if bundled.is_dir():
                return bundled
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_manifest(
        manifest_path: Path, plugin_dir: Path, source: str
    ) -> PluginManifest:
        """Parse a plugin manifest (YAML or TOML)."""
        import yaml

        with open(manifest_path, "r") as f:
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict):
            raise ValueError(f"Manifest must be a dict, got {type(data).__name__}")

        name = data.get("name", plugin_dir.name)
        version = str(data.get("version", "0.1.0"))

        kind_str = data.get("kind", "standalone")
        if kind_str not in _VALID_PLUGIN_KINDS:
            raise ValueError(
                f"Invalid plugin kind '{kind_str}'. "
                f"Valid: {sorted(_VALID_PLUGIN_KINDS)}"
            )
        kind = PluginKind(kind_str)

        requires_env = data.get("requires_env", [])
        if isinstance(requires_env, list):
            requires_env = [str(e) for e in requires_env]
        else:
            requires_env = []

        dependencies = data.get("dependencies", [])
        if isinstance(dependencies, list):
            dependencies = [str(d) for d in dependencies]
        else:
            dependencies = []

        return PluginManifest(
            name=name,
            version=version,
            kind=kind,
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            provides_hooks=list(data.get("provides_hooks", [])),
            provides_tools=list(data.get("provides_tools", [])),
            requires_env=requires_env,
            dependencies=dependencies,
            path=plugin_dir,
            source=source,
        )

    @staticmethod
    def _import_plugin_module(name: str, init_file: Path) -> Any:
        """Import a plugin module from its ``__init__.py`` path.

        Uses ``importlib`` to load the module so it can be re-imported
        on hot-reload.
        """
        import sys

        # Ensure the module name is unique to avoid collisions
        module_name = f"_dragon_plugins.{name}"

        # Remove from sys.modules if already loaded (for reload)
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(module_name, init_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec for {init_file}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module


# ══════════════════════════════════════════════════════════════════════
# Module-level convenience
# ══════════════════════════════════════════════════════════════════════

# Ensure sys is importable in _import_plugin_module
import sys  # noqa: E402

__all__ = [
    "PluginManager",
    "PluginManifest",
    "PluginContext",
    "LoadedPlugin",
    "PluginKind",
    "PluginState",
]
