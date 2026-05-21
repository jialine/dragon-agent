"""
Panda Agent — Built-in Tools
=============================

Reference implementations that demonstrate the tool registration system.
These are the equivalent of Hermes's core tools (terminal, file, web, etc.),
but with circuit breaking, retry, and pipeline composability built-in.

Tools:
    - search: Keyword/pattern search across files
    - file_read: Read file contents
    - file_write: Write/overwrite files
    - execute: Run shell commands
    - http_get: HTTP GET request
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from panda.tool.registry import ToolRegistry


logger = logging.getLogger("panda.tool.builtins")


# ────────────────────────────────────────────────────────────────────
# Built-in tool implementations
# ────────────────────────────────────────────────────────────────────


async def tool_search(
    pattern: str,
    path: str = ".",
    file_glob: str = "*",
    max_results: int = 50,
) -> str:
    """Search file contents with regex pattern matching.

    Args:
        pattern: Regex pattern to search for.
        path: Directory to search in.
        file_glob: File pattern filter (e.g., '*.py').
        max_results: Maximum results to return.
    """
    import glob as glob_mod

    search_dir = Path(path).expanduser().resolve()
    if not search_dir.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    results = []
    count = 0
    pattern_re = re.compile(pattern)

    for filepath in search_dir.rglob(file_glob):
        if filepath.is_dir():
            continue
        if count >= max_results:
            break
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(content.split("\n"), 1):
                if pattern_re.search(line):
                    results.append({
                        "file": str(filepath.relative_to(search_dir)),
                        "line": lineno,
                        "content": line.strip()[:200],
                    })
                    count += 1
                    if count >= max_results:
                        break
        except Exception:
            continue

    return json.dumps({
        "pattern": pattern,
        "matches": len(results),
        "results": results,
    })


async def tool_file_read(
    filepath: str,
    start_line: int = 1,
    end_line: int = 100,
) -> str:
    """Read a file's contents.

    Args:
        filepath: Path to the file.
        start_line: First line to read (1-indexed).
        end_line: Last line to read (inclusive).
    """
    p = Path(filepath).expanduser().resolve()
    if not p.exists():
        return json.dumps({"error": f"File not found: {filepath}"})
    if p.is_dir():
        return json.dumps({"error": f"Path is a directory: {filepath}"})

    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").split("\n")
    except Exception as e:
        return json.dumps({"error": str(e)})

    start = max(0, start_line - 1)
    end = min(len(lines), end_line)
    selected = lines[start:end]

    return json.dumps({
        "file": str(p),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": end,
        "content": "\n".join(selected),
    })


async def tool_file_write(
    filepath: str,
    content: str,
    append: bool = False,
) -> str:
    """Write content to a file.

    Args:
        filepath: Path to write to.
        content: Content to write.
        append: If True, append instead of overwrite.
    """
    p = Path(filepath).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)

    try:
        mode = "a" if append else "w"
        p.write_text(content, encoding="utf-8")
        return json.dumps({
            "file": str(p),
            "bytes_written": len(content.encode("utf-8")),
            "mode": mode,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


async def tool_execute(
    command: str,
    workdir: str = ".",
    timeout_secs: int = 60,
) -> str:
    """Execute a shell command.

    Args:
        command: Shell command to execute.
        workdir: Working directory.
        timeout_secs: Maximum execution time.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
        )
        return json.dumps({
            "stdout": result.stdout[-5000:],
            "stderr": result.stderr[-2000:],
            "exit_code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Command timed out after {timeout_secs}s"})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def tool_http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout_secs: int = 30,
) -> str:
    """Make an HTTP GET request.

    Args:
        url: URL to fetch.
        headers: Optional HTTP headers.
        timeout_secs: Request timeout.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.get(url, headers=headers)
            return json.dumps({
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "content": response.text[:5000],
            })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ────────────────────────────────────────────────────────────────────
# Register all built-in tools
# ────────────────────────────────────────────────────────────────────


def register_builtins(registry: ToolRegistry) -> None:
    """Register all built-in tools on the given registry."""
    registry.register(
        name="search",
        description="Search file contents with regex pattern matching. Use this instead of grep.",
        tags=["file", "search", "grep"],
        category="file",
        timeout_secs=30,
    )(tool_search)

    registry.register(
        name="file_read",
        description="Read the contents of a file with line numbers and pagination.",
        tags=["file", "read"],
        category="file",
        timeout_secs=10,
    )(tool_file_read)

    registry.register(
        name="file_write",
        description="Write content to a file, creating parent directories as needed.",
        tags=["file", "write"],
        category="file",
        timeout_secs=10,
    )(tool_file_write)

    registry.register(
        name="execute",
        description="Execute a shell command and capture stdout/stderr.",
        tags=["terminal", "shell", "command"],
        category="terminal",
        timeout_secs=120,
    )(tool_execute)

    registry.register(
        name="http_get",
        description="Make an HTTP GET request to a URL.",
        tags=["web", "http", "api"],
        category="web",
        timeout_secs=30,
    )(tool_http_get)

    logger.info("Registered %d built-in tools", 5)
