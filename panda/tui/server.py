#!/usr/bin/env python3
"""
Panda TUI JSON-RPC Backend Server
=================================

A lightweight JSON-line protocol server over stdin/stdout (inspired by MCP/LSP).
The Ink/React TUI sends JSON-encoded requests on stdin; the server responds on stdout.

Each message is a single JSON line:

    Request:   {"id": 1, "method": "chat.send", "params": {...}}
    Response:  {"id": 1, "result": {...}}
    Error:     {"id": 1, "error": {"code": -32601, "message": "..."}}

Streaming responses use chunk/done envelope types:

    Chunk:     {"id": 1, "type": "chunk", "content": "..."}
    Done:      {"id": 1, "type": "done"}

Usage:
    python -m panda.tui.server          # in $PATH / from panda CLI
    echo '{"id":1,"method":"ping"}' | python server.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "1.2.0"

# ── Logging to stderr only (stdout is the protocol channel) ──────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("panda.tui")

# ────────────────────────────────────────────────────────────────────
# Lazy imports — only load panda modules when actually needed
# ────────────────────────────────────────────────────────────────────

_session_store: Any = None
_tool_registry: Any = None
_skill_engine: Any = None
_config: Any = None
_provider_registry: Any = None


def _get_session_store():
    global _session_store
    if _session_store is None:
        from panda.session import SessionStore

        _session_store = SessionStore()
    return _session_store


def _get_tool_registry():
    global _tool_registry
    if _tool_registry is None:
        from panda.tool import ToolRegistry
        from panda.tool.builtins import register_builtins

        _tool_registry = ToolRegistry()
        register_builtins(_tool_registry)
    return _tool_registry


def _get_skill_engine():
    global _skill_engine
    if _skill_engine is None:
        from panda.skill import SkillEngine

        _skill_engine = SkillEngine()
    return _skill_engine


def _get_config():
    global _config
    if _config is None:
        from panda.config import PandaConfig

        _config = PandaConfig.load()
    return _config


def _get_provider_registry():
    global _provider_registry
    if _provider_registry is None:
        from panda.provider import auto_setup_providers

        _provider_registry = auto_setup_providers()
    return _provider_registry


# ────────────────────────────────────────────────────────────────────
# JSON-RPC error codes
# ────────────────────────────────────────────────────────────────────

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TIMEOUT_ERROR = -32000
PROVIDER_ERROR = -32001


def make_error(code: int, message: str, data: Any = None) -> Dict:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


def send_response(msg_id: Any, result: Any) -> None:
    """Write a success response to stdout."""
    _write_line({"id": msg_id, "result": result})


def send_error(msg_id: Any, code: int, message: str, data: Any = None) -> None:
    """Write an error response to stdout."""
    _write_line({"id": msg_id, "error": make_error(code, message, data)})


def send_chunk(msg_id: Any, content: str) -> None:
    """Write a streaming chunk to stdout."""
    _write_line({"id": msg_id, "type": "chunk", "content": content})


def send_done(msg_id: Any) -> None:
    """Signal end of streaming response."""
    _write_line({"id": msg_id, "type": "done"})


def _write_line(obj: Dict) -> None:
    """Write a JSON line to stdout, flushing immediately."""
    line = json.dumps(obj, ensure_ascii=False, default=str)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ────────────────────────────────────────────────────────────────────
# Request dispatcher
# ────────────────────────────────────────────────────────────────────

METHOD_TABLE: Dict[str, Any] = {}


def method(name: str):
    """Decorator to register a handler method."""

    def decorator(fn):
        METHOD_TABLE[name] = fn
        return fn

    return decorator


# ── Ping ────────────────────────────────────────────────────────────


@method("ping")
async def handle_ping(params: Dict) -> Dict:
    return {"status": "ok", "version": VERSION}


# ── Chat ────────────────────────────────────────────────────────────


@method("chat.send")
async def handle_chat_send(params: Dict) -> Dict:
    """Send a message to the AI and get a response.

    Params:
        message  (str)  — required
        session_id (str) — optional; creates new session if omitted
        stream   (bool) — optional; if True, use SSE-style chunking
        model    (str)  — optional model name
        provider (str)  — optional provider name
    """
    message = params.get("message", "")
    session_id = params.get("session_id")
    stream = params.get("stream", False)
    model = params.get("model", "gpt-4o")
    provider_name = params.get("provider", "openai")

    if not message:
        raise ValueError("'message' is required")

    store = _get_session_store()
    registry = _get_provider_registry()

    # Create or get session
    if session_id:
        sess = store.get(session_id)
        if sess is None:
            sess = store.create(title=message[:50], platform="tui", model=model)
            session_id = sess.id
    else:
        sess = store.create(title=message[:50], platform="tui", model=model)
        session_id = sess.id

    # Store user message
    store.add_message(session_id, "user", message)

    # Get recent history
    history_msgs = store.get_messages(session_id, limit=50)
    messages = [{"role": m.role, "content": m.content} for m in history_msgs]

    try:
        if stream:
            # For protocol streaming, we collect full content and send chunks
            _stream_id = _msg_id.get() if hasattr(_msg_id, "get") else None

            result = await registry.call(provider_name, model, messages=messages)
            content = result.content if hasattr(result, "content") else str(result)

            # Store assistant response
            store.add_message(session_id, "assistant", content)

            return {
                "session_id": session_id,
                "content": content,
                "model": model,
                "provider": provider_name,
                "usage": getattr(result, "usage", {}),
            }
        else:
            result = await registry.call(provider_name, model, messages=messages)
            content = result.content if hasattr(result, "content") else str(result)

            # Store assistant response
            store.add_message(session_id, "assistant", content)

            return {
                "session_id": session_id,
                "content": content,
                "model": model,
                "provider": provider_name,
                "usage": getattr(result, "usage", {}),
            }
    except Exception as e:
        logger.exception("chat.send failed")
        # Still add the error as an assistant message so the session has a record
        error_msg = f"[Error] {e}"
        store.add_message(session_id, "assistant", error_msg)
        raise


@method("chat.history")
async def handle_chat_history(params: Dict) -> Dict:
    """Get message history for a session.

    Params:
        session_id (str) — required
        limit      (int) — optional (default 100)
        offset     (int) — optional (default 0)
    """
    session_id = params.get("session_id", "")
    limit = params.get("limit", 100)
    offset = params.get("offset", 0)

    if not session_id:
        raise ValueError("'session_id' is required")

    store = _get_session_store()
    sess = store.get(session_id)
    if sess is None:
        raise ValueError(f"Session not found: {session_id}")

    messages = store.get_messages(session_id, limit=limit, offset=offset)
    return {
        "session_id": session_id,
        "title": sess.title,
        "platform": sess.platform,
        "model": sess.model,
        "message_count": sess.message_count,
        "messages": [m.to_dict() for m in messages],
    }


# ── Tools ───────────────────────────────────────────────────────────


@method("tools.list")
async def handle_tools_list(params: Dict) -> Dict:
    """List all registered tools.

    Params:
        category (str) — optional filter
    """
    category = params.get("category")
    registry = _get_tool_registry()
    tools = registry.list_tools(category=category)
    stats = registry.stats()
    return {"tools": tools, "stats": stats}


@method("tools.call")
async def handle_tools_call(params: Dict) -> Dict:
    """Call a registered tool by name.

    Params:
        name (str) — required
        args (dict) — optional
    """
    name = params.get("name", "")
    args = params.get("args", {})

    if not name:
        raise ValueError("'name' is required")

    registry = _get_tool_registry()
    result = await registry.call(name, args)
    return result.to_dict()


# ── Sessions ────────────────────────────────────────────────────────


@method("sessions.list")
async def handle_sessions_list(params: Dict) -> Dict:
    """List recent sessions.

    Params:
        limit    (int)  — optional (default 20)
        platform (str)  — optional filter
    """
    limit = params.get("limit", 20)
    platform = params.get("platform")
    store = _get_session_store()
    sessions = store.list_recent(limit=limit, platform=platform)
    return {"sessions": [s.to_dict() for s in sessions], "total": len(sessions)}


@method("sessions.get")
async def handle_sessions_get(params: Dict) -> Dict:
    """Get a single session with its messages.

    Params:
        id (str) — required
    """
    session_id = params.get("id", "")
    if not session_id:
        raise ValueError("'id' is required")

    store = _get_session_store()
    sess = store.get(session_id)
    if sess is None:
        raise ValueError(f"Session not found: {session_id}")

    messages = store.get_messages(session_id, limit=200)
    return {
        "session": sess.to_dict(),
        "messages": [m.to_dict() for m in messages],
    }


# ── Skills ──────────────────────────────────────────────────────────


@method("skills.list")
async def handle_skills_list(params: Dict) -> Dict:
    """List all registered skills."""
    engine = _get_skill_engine()
    skills = engine.list_skills()
    stats = engine.stats()
    return {"skills": skills, "stats": stats}


@method("skills.search")
async def handle_skills_search(params: Dict) -> Dict:
    """Search skills by query.

    Params:
        query (str) — required
        top_k (int) — optional (default 5)
    """
    query = params.get("query", "")
    top_k = params.get("top_k", 5)

    if not query:
        raise ValueError("'query' is required")

    engine = _get_skill_engine()
    matches = await engine.discover(query, top_k=top_k)
    return {
        "query": query,
        "matches": [
            {
                "name": m.skill_name,
                "similarity": round(m.similarity, 3),
                "description": m.skill.meta.description[:200],
                "version": m.skill.meta.version,
                "success_rate": round(m.skill.success_rate, 3),
            }
            for m in matches
        ],
    }


# ── Config ──────────────────────────────────────────────────────────


@method("config.get")
async def handle_config_get(params: Dict) -> Dict:
    """Get current Panda configuration."""
    cfg = _get_config()
    return {
        "router": {
            "model_path": cfg.router.model_path,
            "n_threads": cfg.router.n_threads,
            "n_ctx": cfg.router.n_ctx,
            "temperature": cfg.router.temperature,
            "max_tokens": cfg.router.max_tokens,
        },
        "server": {
            "host": cfg.server.host,
            "port": cfg.server.port,
            "log_level": cfg.server.log_level,
        },
        "memory": {
            "persist_dir": cfg.memory.persist_dir,
            "embedding_model": cfg.memory.embedding_model,
            "search_top_k": cfg.memory.search_top_k,
            "search_threshold": cfg.memory.search_threshold,
        },
        "guard": {
            "max_consecutive_repeats": cfg.guard.max_consecutive_repeats,
            "max_loop_rounds": cfg.guard.max_loop_rounds,
            "task_timeout_secs": cfg.guard.task_timeout_secs,
        },
    }


# ── Health ──────────────────────────────────────────────────────────


@method("health")
async def handle_health(params: Dict) -> Dict:
    """System health check."""
    checks = {}
    all_healthy = True

    # Session store
    try:
        store = _get_session_store()
        st = store.stats()
        checks["sessions"] = {"status": "ok", "db_path": st["db_path"]}
    except Exception as e:
        checks["sessions"] = {"status": "error", "error": str(e)}
        all_healthy = False

    # Tool registry
    try:
        registry = _get_tool_registry()
        tr_stats = registry.stats()
        checks["tools"] = {"status": "ok", "total_tools": tr_stats["total_tools"]}
    except Exception as e:
        checks["tools"] = {"status": "error", "error": str(e)}
        all_healthy = False

    # Skill engine
    try:
        engine = _get_skill_engine()
        sk_stats = engine.stats()
        checks["skills"] = {"status": "ok", "total_skills": sk_stats["total_skills"]}
    except Exception as e:
        checks["skills"] = {"status": "error", "error": str(e)}
        all_healthy = False

    # Config
    try:
        _get_config()
        checks["config"] = {"status": "ok"}
    except Exception as e:
        checks["config"] = {"status": "error", "error": str(e)}
        all_healthy = False

    return {
        "status": "healthy" if all_healthy else "degraded",
        "version": VERSION,
        "checks": checks,
    }


# ── Doctor ──────────────────────────────────────────────────────────


@method("doctor")
async def handle_doctor(params: Dict) -> Dict:
    """Run diagnostic checks (like panda doctor CLI)."""
    import importlib
    import platform as _platform

    results = []
    all_ok = True

    def _check(name: str, ok: bool, detail: str = "", severity: str = "error"):
        nonlocal all_ok
        icon = "ok" if ok else ("warn" if severity == "warning" else "error")
        if not ok and severity == "error":
            all_ok = False
        results.append({"name": name, "status": icon, "detail": detail})

    # Python version
    py_ver = _platform.python_version()
    _check("Python version", py_ver >= "3.10", f"Python {py_ver}")

    # Core dependencies
    deps = {
        "pydantic": "pydantic",
        "yaml": "pyyaml",
        "dotenv": "python-dotenv",
        "sqlite3": "sqlite3 (built-in)",
    }
    for mod, display in deps.items():
        try:
            importlib.import_module(mod)
            _check(f"Dependency: {display}", True, "installed")
        except ImportError:
            _check(f"Dependency: {display}", False, "not installed", "warning")

    # Config
    config_paths = ["config.yaml", os.path.expanduser("~/.panda/config.yaml")]
    found = None
    for cp in config_paths:
        if Path(cp).exists():
            found = cp
            break
    if found:
        try:
            from panda.config import PandaConfig

            PandaConfig.load(found)
            _check("Config file", True, found)
        except Exception as e:
            _check("Config file", False, str(e), "warning")
    else:
        _check("Config file", False, "config.yaml not found", "warning")

    # API Keys
    api_keys = ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "PANDA_GENERAL_API_KEY"]
    set_keys = [k for k in api_keys if os.getenv(k)]
    if set_keys:
        _check("API Keys", True, f"Configured: {', '.join(set_keys)}")
    else:
        _check("API Keys", False, "No API keys configured", "warning")

    _check("Platform", True, f"{_platform.system()} {_platform.release()}")

    return {
        "status": "ok" if all_ok else "issues_found",
        "results": results,
    }


# ────────────────────────────────────────────────────────────────────
# Protocol I/O loop
# ────────────────────────────────────────────────────────────────────


async def process_request(line: str) -> None:
    """Parse and dispatch a single JSON-line request."""
    # Parse
    try:
        request = json.loads(line)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON: %s", e)
        _write_line(
            {
                "id": None,
                "error": make_error(PARSE_ERROR, f"Parse error: {e}"),
            }
        )
        return

    if not isinstance(request, dict):
        _write_line(
            {
                "id": None,
                "error": make_error(INVALID_REQUEST, "Request must be a JSON object"),
            }
        )
        return

    msg_id = request.get("id")
    method_name = request.get("method", "")
    params = request.get("params", {})

    if not method_name:
        _write_line(
            {
                "id": msg_id,
                "error": make_error(INVALID_REQUEST, "Missing 'method' field"),
            }
        )
        return

    if not isinstance(params, dict):
        params = {}

    # Dispatch
    handler = METHOD_TABLE.get(method_name)
    if handler is None:
        send_error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method_name}")
        return

    try:
        # Call with a deadline (60s timeout)
        result = await asyncio.wait_for(handler(params), timeout=60.0)
        send_response(msg_id, result)
    except asyncio.TimeoutError:
        logger.error("Method '%s' timed out", method_name)
        send_error(msg_id, TIMEOUT_ERROR, f"Method '{method_name}' timed out after 60s")
    except ValueError as e:
        send_error(msg_id, INVALID_PARAMS, str(e))
    except Exception as e:
        logger.exception("Method '%s' failed", method_name)
        send_error(
            msg_id,
            INTERNAL_ERROR,
            f"{type(e).__name__}: {e}",
            data={"traceback": traceback.format_exc()[:1000]},
        )


async def _read_stdin() -> None:
    """Read JSON lines from stdin asynchronously."""
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)

    # On Windows/WSL, stdin may not support async well; fall back to sync reads
    try:
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    except (NotImplementedError, OSError):
        # Fallback: use run_in_executor for blocking reads
        logger.info("Using blocking stdin reader (platform limitation)")
        await _read_stdin_blocking()
        return

    while True:
        try:
            line_bytes = await reader.readline()
        except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
            break

        if not line_bytes:
            break

        line = line_bytes.decode("utf-8").strip()
        if line:
            await process_request(line)


async def _read_stdin_blocking() -> None:
    """Fallback blocking stdin reader."""
    loop = asyncio.get_event_loop()

    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            break

        line = line.strip()
        if line:
            await process_request(line)


async def run_server() -> None:
    """Start the TUI backend server, reading from stdin."""
    logger.info("Panda TUI Server v%s starting on stdin/stdout", VERSION)
    await _read_stdin()
    logger.info("Panda TUI Server shutting down")


def main() -> None:
    """Entry point for `panda tui` CLI command."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
