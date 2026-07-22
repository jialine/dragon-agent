"""
Dragon Agent — Code Execution Sandbox
======================================

Secure Python code execution in a sandboxed subprocess.
Modeled after Hermes Agent's execute_code tool.

Tools:
    - execute_code: Run Python code in an isolated subprocess

Dependencies:
    - Python 3.10+ (built-in subprocess, tempfile)
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("dragon.tool.builtins.execute_code")

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 60  # seconds
MAX_TIMEOUT = 300     # max allowed timeout
MAX_OUTPUT_BYTES = 50 * 1024  # 50KB stdout cap

# ── Tool Implementation ─────────────────────────────────────────────


async def tool_execute_code(
    code: str,
    timeout: int = DEFAULT_TIMEOUT,
    workdir: str = "",
) -> str:
    """Execute Python code in an isolated subprocess and return the output.

    The code runs in a temporary directory with a fresh Python process.
    stdout and stderr are captured. If the code exceeds timeout, the
    process is killed and partial output is returned.

    Args:
        code: Python source code to execute.
        timeout: Maximum execution time in seconds (default 60, max 300).
        workdir: Optional working directory. Defaults to temp dir.

    Returns:
        JSON string with keys: success, output, exit_code, duration_ms, error.
    """
    import json

    if not code or not code.strip():
        return json.dumps({
            "success": False,
            "output": "",
            "exit_code": -1,
            "duration_ms": 0,
            "error": "Empty code block",
        }, ensure_ascii=False)

    # Enforce timeout cap
    timeout = min(max(timeout, 1), MAX_TIMEOUT)

    # Use provided workdir or create temp
    tmpdir = None
    cwd = workdir if workdir and os.path.isdir(workdir) else None
    if not cwd:
        tmpdir = tempfile.TemporaryDirectory(prefix="dragon_exec_")
        cwd = tmpdir.name

    start = time.monotonic()

    try:
        proc = subprocess.run(
            ["python3", "-c", code],
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            text=True,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        duration_ms = (time.monotonic() - start) * 1000

        # Combine stdout + stderr, cap output
        output = proc.stdout
        if proc.stderr:
            if output:
                output += "\n## STDERR ##\n"
            output += proc.stderr

        if len(output.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES:
            truncate_marker = "\n... [OUTPUT TRUNCATED — exceeded 50KB limit] ...\n"
            # Truncate from the end since most useful output is at the end
            output = truncate_marker + output[-MAX_OUTPUT_BYTES // 2:]

        return json.dumps({
            "success": proc.returncode == 0,
            "output": output,
            "exit_code": proc.returncode,
            "duration_ms": round(duration_ms, 1),
            "error": None if proc.returncode == 0 else f"Exit code {proc.returncode}",
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        duration_ms = (time.monotonic() - start) * 1000
        return json.dumps({
            "success": False,
            "output": f"[Process killed after {timeout}s timeout]",
            "exit_code": -1,
            "duration_ms": round(duration_ms, 1),
            "error": f"Timeout after {timeout}s",
        }, ensure_ascii=False)

    except FileNotFoundError:
        duration_ms = (time.monotonic() - start) * 1000
        return json.dumps({
            "success": False,
            "output": "",
            "exit_code": -1,
            "duration_ms": round(duration_ms, 1),
            "error": "python3 not found on PATH",
        }, ensure_ascii=False)

    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return json.dumps({
            "success": False,
            "output": "",
            "exit_code": -1,
            "duration_ms": round(duration_ms, 1),
            "error": f"Execution error: {exc}",
        }, ensure_ascii=False)

    finally:
        if tmpdir:
            try:
                tmpdir.cleanup()
            except Exception:
                pass
