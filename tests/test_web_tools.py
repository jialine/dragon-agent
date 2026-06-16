"""
Unit tests for Dragon Web tools (web_search, web_fetch, web_download).
"""
import json
import os
from pathlib import Path

import httpx
import pytest


# ── Helpers ────────────────────────────────────────────────────────────

def _has_network() -> bool:
    """Check if we can reach duckduckgo.com (skip integration tests if offline)."""
    try:
        import socket
        socket.create_connection(("html.duckduckgo.com", 443), timeout=3)
        return True
    except OSError:
        return False


# ── tool_web_search ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_search_empty_query():
    """Empty query should return an error."""
    from dragon.tool.builtins import tool_web_search
    result = json.loads(await tool_web_search(""))
    assert "error" in result


@pytest.mark.asyncio
async def test_web_search_returns_results():
    """Search should return structured results."""
    if not _has_network():
        pytest.skip("No network — skip web search integration test")

    from dragon.tool.builtins import tool_web_search
    result = json.loads(await tool_web_search("Python programming", max_results=3))
    # Should not be an error
    assert "error" not in result
    # Should have results
    assert "results" in result
    assert "query" in result
    assert result["query"] == "Python programming"
    assert isinstance(result["results"], list)
    # Each result should have title and url at minimum
    for r in result["results"]:
        assert "title" in r
        assert "url" in r


@pytest.mark.asyncio
async def test_web_search_respects_max_results():
    """max_results parameter should be honored."""
    if not _has_network():
        pytest.skip("No network — skip web search integration test")

    from dragon.tool.builtins import tool_web_search
    result = json.loads(await tool_web_search("test", max_results=2))
    assert "error" not in result
    assert len(result["results"]) <= 2


@pytest.mark.asyncio
async def test_web_search_parse_fallback():
    """Even without network, regex fallback should not crash — should return structured error."""
    from dragon.tool.builtins import tool_web_search
    result = json.loads(await tool_web_search("some query", max_results=1))
    # Should return JSON (either results or graceful error)
    assert isinstance(result, dict)
    if "error" in result:
        assert "results" not in result or result.get("results", []) == []


# ── tool_web_fetch ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_fetch_invalid_url():
    """Invalid URL should return an error."""
    from dragon.tool.builtins import tool_web_fetch
    result = json.loads(await tool_web_fetch("not-a-valid-url"))
    assert "error" in result


@pytest.mark.asyncio
async def test_web_fetch_empty_url():
    """Empty URL should return an error."""
    from dragon.tool.builtins import tool_web_fetch
    result = json.loads(await tool_web_fetch(""))
    assert "error" in result


@pytest.mark.asyncio
async def test_web_fetch_real_page():
    """Fetch a real page and verify structured response."""
    if not _has_network():
        pytest.skip("No network — skip web fetch integration test")

    from dragon.tool.builtins import tool_web_fetch
    # Use a simple, fast page
    result = json.loads(await tool_web_fetch("https://httpbin.org/get"))
    assert "error" not in result
    assert "status_code" in result
    assert result["status_code"] == 200
    assert "url" in result
    assert "title" in result or "content" in result


@pytest.mark.asyncio
async def test_web_fetch_truncates_content():
    """Content should be truncated to a reasonable length."""
    if not _has_network():
        pytest.skip("No network — skip web fetch integration test")

    from dragon.tool.builtins import tool_web_fetch
    result = json.loads(await tool_web_fetch("https://httpbin.org/bytes/10000"))
    assert "error" not in result
    if "content" in result:
        # Content should be limited to ~10000 chars
        assert len(result["content"]) <= 12000


# ── tool_web_download ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_download_invalid_url():
    """Invalid URL should return an error."""
    from dragon.tool.builtins import tool_web_download
    result = json.loads(await tool_web_download("not-a-url", "/tmp/test.txt"))
    assert "error" in result


@pytest.mark.asyncio
async def test_web_download_empty_url():
    """Empty URL should return an error."""
    from dragon.tool.builtins import tool_web_download
    result = json.loads(await tool_web_download("", "/tmp/test.txt"))
    assert "error" in result


@pytest.mark.asyncio
async def test_web_download_real_file(tmp_path):
    """Download a real file and verify it exists."""
    if not _has_network():
        pytest.skip("No network — skip web download integration test")

    from dragon.tool.builtins import tool_web_download

    save_path = tmp_path / "downloaded.json"
    result = json.loads(await tool_web_download(
        "https://httpbin.org/json",
        str(save_path),
    ))

    assert "error" not in result, f"Unexpected error: {result}"
    assert "path" in result
    assert "size_bytes" in result
    assert result["size_bytes"] > 0
    assert os.path.exists(save_path)


@pytest.mark.asyncio
async def test_web_download_creates_parent_dirs(tmp_path):
    """Should create parent directories automatically."""
    if not _has_network():
        pytest.skip("No network — skip web download integration test")

    from dragon.tool.builtins import tool_web_download

    save_path = tmp_path / "deep" / "nested" / "data.json"
    result = json.loads(await tool_web_download(
        "https://httpbin.org/json",
        str(save_path),
    ))

    assert "error" not in result, f"Unexpected error: {result}"
    assert os.path.exists(save_path)


# ── Registration ───────────────────────────────────────────────────────

def test_web_tools_registered():
    """Verify web tools are registered in the builtins registry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    tool_names = [t["name"] for t in registry.list_tools()]

    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "web_download" in tool_names
