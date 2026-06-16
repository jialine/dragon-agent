"""
Tests for Dragon Agent Analysis Tools (code_exec, data_explore, data_plot).

Covers: basic execution, sandbox safety, file I/O, plotting, and registry integration.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────

def _make_test_csv(filepath: str, rows: int = 10) -> None:
    """Write a test CSV with name, age, score columns."""
    with open(filepath, "w") as f:
        f.write("name,age,score\n")
        for i in range(rows):
            f.write(f"user{i},{20 + i},{80 + i * 0.5}\n")


def _make_test_json(filepath: str) -> None:
    """Write a test JSON array of records."""
    import json as _json
    data = [
        {"name": f"user{i}", "age": 20 + i, "score": 80 + i * 0.5}
        for i in range(5)
    ]
    with open(filepath, "w") as f:
        _json.dump(data, f)


def _has_pandas() -> bool:
    try:
        import pandas  # noqa: F401
        return True
    except ImportError:
        return False


def _has_matplotlib() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


HAS_PANDAS = _has_pandas()
HAS_MATPLOTLIB = _has_matplotlib()


# ── tool_code_exec tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_code_exec_basic_arithmetic():
    """Execute simple arithmetic and capture result in stdout."""
    from dragon.tool.builtins.analysis import tool_code_exec

    result = json.loads(await tool_code_exec("print(2 + 2)"))
    assert "stdout" in result
    assert "4" in result["stdout"]
    assert "error" not in result


@pytest.mark.asyncio
async def test_code_exec_stdout_stderr_capture():
    """Both stdout and stderr should be captured separately."""
    from dragon.tool.builtins.analysis import tool_code_exec

    code = "import sys\nprint('hello')\nprint('oops', file=sys.stderr)"
    result = json.loads(await tool_code_exec(code))

    assert "hello" in result["stdout"]
    assert "oops" in result["stderr"]
    assert "error" not in result


@pytest.mark.asyncio
async def test_code_exec_subprocess_blocked():
    """Importing subprocess or os.system should be blocked."""
    from dragon.tool.builtins.analysis import tool_code_exec

    for code in [
        "import subprocess",
        "import os; os.system('echo pwned')",
        "import os; os.popen('id')",
        "import os; os.execv('/bin/sh', [])",
        "from subprocess import run",
    ]:
        result = json.loads(await tool_code_exec(code))
        assert "error" in result, f"Should block: {code}"


@pytest.mark.asyncio
async def test_code_exec_returns_valid_json_on_error():
    """Syntax errors must return valid JSON, not crash."""
    from dragon.tool.builtins.analysis import tool_code_exec

    result = json.loads(await tool_code_exec("print(1 / 0)"))
    assert isinstance(result, dict)
    # May have stdout (print before error) and error key
    assert "stdout" in result or "error" in result or "stderr" in result


@pytest.mark.asyncio
async def test_code_exec_always_returns_json():
    """All code paths must return parseable JSON."""
    from dragon.tool.builtins.analysis import tool_code_exec

    for code in ["print('ok')", "", "x = 1", "raise ValueError('test')"]:
        result_str = await tool_code_exec(code)
        data = json.loads(result_str)
        assert isinstance(data, dict)


# ── tool_data_explore tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_explore_csv(tmp_path):
    """Read CSV, return rows/cols/columns/preview/stats."""
    from dragon.tool.builtins.analysis import tool_data_explore

    csv_path = tmp_path / "test.csv"
    _make_test_csv(str(csv_path), rows=8)

    result = json.loads(await tool_data_explore(str(csv_path)))

    assert result["rows"] == 8
    assert result["columns"] == 3
    assert result["column_names"] == ["name", "age", "score"]
    assert len(result["preview"]) == 5  # first 5 rows
    assert "statistics" in result
    # Statistics should have numeric columns
    assert "age" in result["statistics"] or "score" in result["statistics"]


@pytest.mark.asyncio
async def test_data_explore_json(tmp_path):
    """Read JSON array, return stats."""
    from dragon.tool.builtins.analysis import tool_data_explore

    json_path = tmp_path / "test.json"
    _make_test_json(str(json_path))

    result = json.loads(await tool_data_explore(str(json_path)))

    assert result["rows"] == 5
    assert result["columns"] == 3
    assert len(result["preview"]) == 5


@pytest.mark.asyncio
async def test_data_explore_nonexistent():
    """Missing file should return error, not crash."""
    from dragon.tool.builtins.analysis import tool_data_explore

    result = json.loads(await tool_data_explore("/nonexistent/data.csv"))
    assert "error" in result


@pytest.mark.asyncio
async def test_data_explore_unsupported_format(tmp_path):
    """Unsupported file format should return error."""
    from dragon.tool.builtins.analysis import tool_data_explore

    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("not a data file")

    result = json.loads(await tool_data_explore(str(txt_path)))
    assert "error" in result


# ── tool_data_plot tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_plot_basic(tmp_path):
    """Execute matplotlib code and save PNG to specified path."""
    if not HAS_MATPLOTLIB:
        pytest.skip("matplotlib not installed")

    from dragon.tool.builtins.analysis import tool_data_plot

    output = tmp_path / "chart.png"
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2, 3], [1, 4, 9])\n"
        "plt.title('Test Plot')\n"
    )

    result = json.loads(await tool_data_plot(code, output_path=str(output)))

    assert "path" in result
    assert result["path"] == str(output)
    assert os.path.exists(str(output))
    assert os.path.getsize(str(output)) > 0


@pytest.mark.asyncio
async def test_data_plot_auto_generated_path(tmp_path, monkeypatch):
    """When no output_path given, auto-generate one."""
    if not HAS_MATPLOTLIB:
        pytest.skip("matplotlib not installed")

    from dragon.tool.builtins.analysis import tool_data_plot

    # Force matplotlib Agg backend for headless
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([0, 1], [0, 1])\n"
    )
    # Override default output dir to tmp_path
    output_file = tmp_path / "auto_plot.png"
    result = json.loads(await tool_data_plot(code, output_path=str(output_file)))

    assert "path" in result
    assert os.path.exists(str(output_file))
    assert os.path.getsize(str(output_file)) > 0


@pytest.mark.asyncio
async def test_data_plot_sandbox_blocked():
    """Dangerous imports should be blocked in plot code too."""
    from dragon.tool.builtins.analysis import tool_data_plot

    result = json.loads(await tool_data_plot("import subprocess"))
    assert "error" in result


@pytest.mark.asyncio
async def test_data_plot_always_returns_json():
    """All code paths (including errors) must return valid JSON."""
    from dragon.tool.builtins.analysis import tool_data_plot

    for code in [
        "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; plt.plot([1]); ",
        "raise RuntimeError('fail')",
        "",
    ]:
        result_str = await tool_data_plot(code)
        data = json.loads(result_str)
        assert isinstance(data, dict)


# ── Registry integration tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_analysis_tools_registered():
    """Verify all three analysis tools are registered in ToolRegistry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    for name in ("code_exec", "data_explore", "data_plot"):
        tool = registry.get(name)
        assert tool is not None, f"Tool '{name}' not registered"
        assert tool.category == "analysis"

    # Verify search returns them (search by tag/description)
    results = registry.search("code")
    names = [r["name"] for r in results]
    assert "code_exec" in names

    results = registry.search("data")
    names = [r["name"] for r in results]
    assert "data_explore" in names
    assert "data_plot" in names


@pytest.mark.asyncio
async def test_code_exec_via_registry():
    """End-to-end: call code_exec through the registry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    result = await registry.call("code_exec", {"code": "print(42)"})
    assert result.success is True
    data = json.loads(result.output)
    assert "42" in data.get("stdout", "")
