"""
Dragon Agent — Data Analysis Tools
==================================

Built-in tools for code execution, data exploration, and data visualization.

Tools:
    - code_exec: Execute Python code in a sandbox
    - data_explore: Explore CSV/JSON/Excel data files
    - data_plot: Execute matplotlib code and save as PNG

Safety: code_exec and data_plot sandbox blocks dangerous modules
(os.system, subprocess, etc.) and captures stdout/stderr separately.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dragon.tool.builtins.analysis")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

# Modules and functions that are BLOCKED in the sandbox
DANGEROUS_MODULES: List[str] = [
    "subprocess",
    "os",
    "shutil",
    "signal",
    "socket",
    "multiprocessing",
    "threading",
    "ctypes",
    "importlib",
    "code",
    "compileall",
    "pty",
    "pdb",
    "codeop",
    "dis",
]

DANGEROUS_FUNCTIONS: List[str] = [
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "sys.exit",
    "sys.breakpointhook",
    "shutil.rmtree",
    "__import__",
    "eval",
    "exec",
    "compile",
    "open",
]

# ────────────────────────────────────────────────────────────────────
# Sandbox Helpers
# ────────────────────────────────────────────────────────────────────
SAFE_IMPORTS: Dict[str, Any] = {
    "json": json,
    "math": None,  # import math
    "random": None,
    "statistics": None,
    "datetime": None,
    "collections": None,
    "itertools": None,
    "functools": None,
    "re": None,
    "string": None,
    "textwrap": None,
    "csv": None,
    "pprint": None,
    "typing": None,
    "dataclasses": None,
    "enum": None,
    "hashlib": None,
    "base64": None,
    "uuid": None,
    "logging": None,
    "copy": None,
    "decimal": None,
    "fractions": None,
    "heapq": None,
    "bisect": None,
    "io": None,
    "pathlib": None,
    "sys": None,      # sys import allowed, dangerous functions blocked by _check_code_safety
    "os.path": None,  # only os.path is safe
    "numpy": "np",
    "np": None,
    "pandas": "pd",
    "pd": None,
    "matplotlib": "mpl",
    "mpl": None,
    "matplotlib.pyplot": "plt",
    "plt": None,
    "scipy": None,
    "scipy.stats": None,
    "sklearn": None,
    "seaborn": None,
    "PIL": None,
    "PIL.Image": None,
}


# ────────────────────────────────────────────────────────────────────
# Sandbox Helpers
# ────────────────────────────────────────────────────────────────────

def _check_code_safety(code: str) -> Optional[str]:
    """Scan code for dangerous imports and function calls.

    Returns error message if dangerous patterns found, None if safe.
    """
    code_lower = code.lower()

    # Check for dangerous imports
    for mod in DANGEROUS_MODULES:
        # Match "import subprocess" or "from subprocess import ..."
        if f"import {mod}" in code_lower or f"from {mod} import" in code_lower or f"from {mod}." in code_lower:
            return f"Dangerous import blocked: '{mod}'. Use safe alternatives."

    # Check for dangerous function calls
    for func in DANGEROUS_FUNCTIONS:
        if func in code_lower:
            return f"Dangerous function blocked: '{func}'. Not allowed in sandbox."

    # Check for eval/exec/compile calls
    # More targeted check
    import re
    dangerous_call_patterns = [
        r"\bexec\s*\(",
        r"\beval\s*\(",
        r"\bcompile\s*\(",
        r"\b__import__\s*\(",
        r"\bopen\s*\(",
    ]
    for pattern in dangerous_call_patterns:
        if re.search(pattern, code):
            return f"Dangerous call blocked: {pattern}. Not allowed in sandbox."

    return None


def _get_safe_globals() -> Dict[str, Any]:
    """Build safe globals with __builtins__ set to the real builtins module.

    The sandbox import hook and code safety check provide the actual
    security layer — blocking dangerous imports and function calls.
    """
    import builtins
    return {"__builtins__": builtins}


class _SandboxImportHook:
    """Import hook that only allows safe modules."""

    def __init__(self):
        import builtins
        self._builtins = builtins
        self._original_import = builtins.__import__

    def __enter__(self):
        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            # Top-level module name
            top_level = name.split(".")[0]

            # Block dangerous modules
            if top_level in DANGEROUS_MODULES:
                raise ImportError(f"Import of '{name}' is blocked in sandbox")

            # Block subprocess explicitly even as sub-import
            if "subprocess" in name.split("."):
                raise ImportError(f"Import of '{name}' is blocked in sandbox")

            # Allow safe modules
            safe_mods = set(SAFE_IMPORTS.keys())
            safe_tops = set()
            for m in safe_mods:
                safe_tops.add(m.split(".")[0])

            if top_level in safe_tops or top_level in ("numpy", "pandas", "matplotlib",
                                                         "scipy", "sklearn", "seaborn",
                                                         "PIL", "np", "pd", "mpl", "plt"):
                return self._original_import(name, globals, locals, fromlist, level)
            else:
                raise ImportError(f"Import of '{name}' is blocked in sandbox")

        self._builtins.__import__ = safe_import
        return self

    def __exit__(self, *args):
        self._builtins.__import__ = self._original_import


def _execute_sandboxed(code: str, timeout: int) -> Dict[str, Any]:
    """Execute Python code in a sandbox and return stdout/stderr/error."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    start_time = time.monotonic()

    try:
        # Check code safety first
        safety_error = _check_code_safety(code)
        if safety_error:
            return {
                "stdout": "",
                "stderr": "",
                "error": safety_error,
                "execution_time": round((time.monotonic() - start_time) * 1000, 1),
            }

        old_stdout = sys.stdout
        old_stderr = sys.stderr

        try:
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf

            # Build safe globals BEFORE entering sandbox import hook
            safe_globals = _get_safe_globals()
            safe_locals: Dict[str, Any] = {}

            with _SandboxImportHook():
                exec(code, safe_globals, safe_locals)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        execution_time = round((time.monotonic() - start_time) * 1000, 1)

        return {
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue(),
            "execution_time": execution_time,
            "return_value": repr(safe_locals.get("__result__")) if "__result__" in safe_locals else None,
        }

    except Exception as e:
        execution_time = round((time.monotonic() - start_time) * 1000, 1)
        return {
            "stdout": stdout_buf.getvalue(),
            "stderr": stderr_buf.getvalue() + "\n" + traceback.format_exc(),
            "error": f"{type(e).__name__}: {e}",
            "execution_time": execution_time,
        }


# ────────────────────────────────────────────────────────────────────
# Tool: code_exec
# ────────────────────────────────────────────────────────────────────

async def tool_code_exec(code: str, timeout: int = 30) -> str:
    """Execute Python code in a sandbox with safety restrictions.

    Dangerous modules (os.system, subprocess, etc.) are blocked.
    stdout and stderr are captured separately.

    Args:
        code: Python code to execute.
        timeout: Maximum execution time in seconds (default 30).

    Returns:
        JSON string with stdout, stderr, error (if any), and execution_time.
    """
    if not code or not code.strip():
        return json.dumps({
            "stdout": "",
            "stderr": "",
            "error": "Empty code — nothing to execute.",
            "execution_time": 0,
        })

    try:
        # Use asyncio to enforce timeout
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _execute_sandboxed, code, timeout),
            timeout=timeout,
        )
        return json.dumps(result)

    except asyncio.TimeoutError:
        return json.dumps({
            "stdout": "",
            "stderr": "",
            "error": f"Code execution timed out after {timeout}s",
            "execution_time": timeout * 1000,
        })
    except Exception as e:
        return json.dumps({
            "stdout": "",
            "stderr": "",
            "error": f"Unexpected error: {type(e).__name__}: {e}",
            "execution_time": 0,
        })


# ────────────────────────────────────────────────────────────────────
# Tool: data_explore
# ────────────────────────────────────────────────────────────────────

async def tool_data_explore(file_path: str) -> str:
    """Explore a data file (CSV, JSON, Excel) and return structure + statistics.

    Reads the file and returns:
    - Number of rows and columns
    - Column names
    - First 5 rows (preview)
    - Basic statistics (mean, std, min, max for numeric columns)

    Args:
        file_path: Path to CSV, JSON, or Excel (.xlsx) file.

    Returns:
        JSON string with rows, columns, column_names, preview, and statistics.
    """
    p = Path(file_path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"File not found: {file_path}"})

    suffix = p.suffix.lower()

    try:
        if suffix == ".csv":
            return _explore_csv(p)
        elif suffix == ".json":
            return _explore_json(p)
        elif suffix in (".xlsx", ".xls"):
            return _explore_excel(p)
        else:
            return json.dumps({
                "error": f"Unsupported file format: '{suffix}'. Supported: .csv, .json, .xlsx, .xls"
            })
    except Exception as e:
        return json.dumps({
            "error": f"Failed to explore file: {type(e).__name__}: {e}",
        })


def _explore_csv(path: Path) -> str:
    """Explore a CSV file."""
    # Try pandas first, fall back to csv module
    try:
        import pandas as pd
        df = pd.read_csv(str(path))
        return _build_explore_result(df)
    except ImportError:
        pass

    # Fallback: stdlib csv
    import csv
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows_list = list(reader)

    if not rows_list:
        return json.dumps({"error": "CSV file is empty"})

    header = rows_list[0]
    data_rows = rows_list[1:]

    # Basic numeric stats for columns that look numeric
    stats = {}
    for col_idx, col_name in enumerate(header):
        values = []
        for row in data_rows:
            if col_idx < len(row):
                try:
                    values.append(float(row[col_idx]))
                except (ValueError, TypeError):
                    pass
        if values:
            stats[col_name] = {
                "count": len(values),
                "mean": round(sum(values) / len(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
            }

    return json.dumps({
        "rows": len(data_rows),
        "columns": len(header),
        "column_names": header,
        "preview": data_rows[:5],
        "statistics": stats,
        "format": "csv",
    })


def _explore_json(path: Path) -> str:
    """Explore a JSON file."""
    import json as _json
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = _json.load(f)

    # Handle JSON array of objects
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        rows = data
        columns = list(data[0].keys())
        return _build_dict_list_result(rows, columns, "json")

    # Handle JSON object
    if isinstance(data, dict):
        return json.dumps({
            "rows": 1,
            "columns": len(data),
            "column_names": sorted(data.keys()),
            "preview": [{k: str(v)[:100] for k, v in data.items()}],
            "statistics": {},
            "format": "json",
        })

    return json.dumps({"error": "JSON structure not supported — expected array of objects or object"})


def _explore_excel(path: Path) -> str:
    """Explore an Excel file (requires pandas or openpyxl)."""
    try:
        import pandas as pd
        df = pd.read_excel(str(path))
        return _build_explore_result(df)
    except ImportError:
        return json.dumps({
            "error": "Excel support requires pandas and openpyxl. Install with: pip install pandas openpyxl"
        })


def _build_explore_result(df) -> str:
    """Build explore result from a pandas DataFrame."""
    try:
        import pandas as pd
        import numpy as np

        # Preview: first 5 rows as list of dicts
        preview_df = df.head(5)
        preview = preview_df.to_dict(orient="records")
        # Convert non-serializable types
        for row in preview:
            for k, v in row.items():
                if isinstance(v, (np.integer,)):
                    row[k] = int(v)
                elif isinstance(v, (np.floating,)):
                    row[k] = float(v)
                elif isinstance(v, (np.bool_,)):
                    row[k] = bool(v)
                elif pd.isna(v):
                    row[k] = None

        # Statistics: describe numeric columns
        stats = {}
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        if numeric_cols:
            desc = df[numeric_cols].describe()
            for col in numeric_cols:
                col_stats = desc[col].to_dict()
                stats[col] = {
                    k: (None if pd.isna(v) else round(float(v), 4))
                    for k, v in col_stats.items()
                }

        return json.dumps({
            "rows": len(df),
            "columns": len(df.columns),
            "column_names": df.columns.tolist(),
            "preview": preview,
            "statistics": stats,
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        })
    except Exception as e:
        return json.dumps({"error": f"Pandas error: {type(e).__name__}: {e}"})


def _build_dict_list_result(rows: list, columns: list, fmt: str) -> str:
    """Build explore result from a list of dicts (JSON/fallback)."""
    # Preview
    preview = rows[:5]

    # Compute basic numeric stats
    stats = {}
    for col in columns:
        values = []
        for row in rows:
            val = row.get(col)
            try:
                if val is not None:
                    values.append(float(val))
            except (ValueError, TypeError):
                pass
        if values:
            stats[col] = {
                "count": len(values),
                "mean": round(sum(values) / len(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
            }

    return json.dumps({
        "rows": len(rows),
        "columns": len(columns),
        "column_names": columns,
        "preview": preview,
        "statistics": stats,
        "format": fmt,
    })


# ────────────────────────────────────────────────────────────────────
# Tool: data_plot
# ────────────────────────────────────────────────────────────────────

async def tool_data_plot(code: str, output_path: Optional[str] = None) -> str:
    """Execute matplotlib code and save the resulting plot as a PNG file.

    The code should use matplotlib to create a plot. The plot is automatically
    saved to the specified output_path. If no output_path is given, a temporary
    path is generated.

    Safety: Same sandbox restrictions as code_exec apply (no subprocess, os.system, etc.)

    Args:
        code: Matplotlib Python code to execute. Should include plot commands
              but NOT plt.savefig() — the tool handles saving automatically.
        output_path: Path to save the PNG (optional, auto-generated if not provided).

    Returns:
        JSON string with path to the saved PNG, or error details.
    """

    if not code or not code.strip():
        return json.dumps({"error": "Empty code — nothing to execute."})

    # Determine output path
    if output_path:
        out_p = Path(output_path).expanduser().resolve()
    else:
        # Auto-generate under ~/.dragon/plots/
        home = Path.home()
        plots_dir = home / ".dragon" / "plots"
        plots_dir.mkdir(parents=True, exist_ok=True)
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_p = plots_dir / f"plot_{timestamp}.png"

    out_p.parent.mkdir(parents=True, exist_ok=True)

    # Wrap the code: set Agg backend, add savefig, use safe globals
    wrapped_code = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        + code +
        f"\n__plot_path__ = {repr(str(out_p))}\n"
        "plt.savefig(__plot_path__, dpi=100, bbox_inches='tight')\n"
        "plt.close('all')\n"
    )

    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _execute_sandboxed, wrapped_code, 30),
            timeout=60,
        )

        data = result

        if "error" in data:
            return json.dumps(data)

        # Check if file was created
        if out_p.exists() and out_p.stat().st_size > 0:
            return json.dumps({
                "path": str(out_p),
                "size_bytes": out_p.stat().st_size,
                "stdout": data.get("stdout", ""),
                "stderr": data.get("stderr", ""),
                "execution_time": data.get("execution_time", 0),
            })
        else:
            return json.dumps({
                "error": "Plot was generated but the output file is empty or missing. Check your matplotlib code.",
                "stdout": data.get("stdout", ""),
                "stderr": data.get("stderr", ""),
            })

    except asyncio.TimeoutError:
        return json.dumps({
            "error": "Plot generation timed out after 60s",
        })
    except Exception as e:
        return json.dumps({
            "error": f"Unexpected error: {type(e).__name__}: {e}",
        })
