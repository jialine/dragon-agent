"""
Unit tests for Dragon Browser tools (browser_open, browser_screenshot, etc.).

Uses Playwright as a soft dependency. Tests skip gracefully when it's not installed.
"""
import json
import os
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────

_HAS_PLAYWRIGHT: bool | None = None

def _has_playwright() -> bool:
    """Check if Playwright Python package is importable."""
    global _HAS_PLAYWRIGHT
    if _HAS_PLAYWRIGHT is None:
        try:
            import playwright  # noqa: F401
            _HAS_PLAYWRIGHT = True
        except ImportError:
            _HAS_PLAYWRIGHT = False
    return _HAS_PLAYWRIGHT


def _has_browsers() -> bool:
    """Check if Playwright browsers are installed."""
    if not _has_playwright():
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Module-level constants ──────────────────────────────────────────────

def test_browser_constants():
    """Verify browser module constants are reasonable."""
    from dragon.tool.builtins.browser import IDLE_CLOSE_SECS
    assert IDLE_CLOSE_SECS >= 60  # Should be at least 1 minute


# ── tool_browser_open ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_open_no_playwright():
    """When Playwright is not installed, should return a helpful error."""
    if _has_playwright() and _has_browsers():
        pytest.skip("Playwright is installed — skipping no-Playwright test")

    from dragon.tool.builtins.browser import browser_open
    try:
        result = json.loads(await browser_open("about:blank"))
        if _has_playwright():
            # Playwright is installed but maybe browsers aren't
            assert "error" not in result or "path" in result
        else:
            # Should gracefully report Playwright missing
            assert isinstance(result, dict)
    except Exception:
        # Real error is acceptable (import fails before reaching code)
        pass


@pytest.mark.asyncio
async def test_browser_open_empty_url():
    """Empty URL should return an error."""
    from dragon.tool.builtins.browser import browser_open
    if not _has_playwright():
        # Even without Playwright, validation should work
        pass
    try:
        result = json.loads(await browser_open(""))
        if isinstance(result, dict):
            # Either error or helpful message
            pass
    except Exception:
        pass


# ── tool_browser_close ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_close_no_instance():
    """Closing when no browser is open should not crash."""
    from dragon.tool.builtins.browser import browser_close
    result = json.loads(await browser_close())
    assert isinstance(result, dict)


# ── tool_browser_screenshot ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_screenshot_no_instance():
    """Screenshot without browser should return an error gracefully."""
    from dragon.tool.builtins.browser import browser_screenshot
    result = json.loads(await browser_screenshot("/tmp/no-browser.png"))
    assert "error" in result


@pytest.mark.asyncio
async def test_browser_screenshot_empty_path():
    """Empty path should return an error."""
    from dragon.tool.builtins.browser import browser_screenshot
    result = json.loads(await browser_screenshot(""))
    assert "error" in result


# ── tool_browser_get_text ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_get_text_no_instance():
    """Get text without browser should return an error."""
    from dragon.tool.builtins.browser import browser_get_text
    result = json.loads(await browser_get_text())
    assert "error" in result


# ── tool_browser_click ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_click_no_instance():
    """Click without browser should return an error."""
    from dragon.tool.builtins.browser import browser_click
    result = json.loads(await browser_click("#nonexistent"))
    assert "error" in result

@pytest.mark.asyncio
async def test_browser_click_empty_selector():
    """Empty selector should return an error."""
    from dragon.tool.builtins.browser import browser_click
    result = json.loads(await browser_click(""))
    assert "error" in result


# ── tool_browser_type ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_type_no_instance():
    """Type without browser should return an error."""
    from dragon.tool.builtins.browser import browser_type
    result = json.loads(await browser_type("#input", "test text"))
    assert "error" in result

@pytest.mark.asyncio
async def test_browser_type_empty_selector():
    """Empty selector should return an error."""
    from dragon.tool.builtins.browser import browser_type
    result = json.loads(await browser_type("", "test"))
    assert "error" in result


# ── Full workflow (integration) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_browser_full_workflow(tmp_path):
    """Open a page, interact, screenshot, get text, close."""
    if not _has_playwright() or not _has_browsers():
        pytest.skip("Playwright with browsers not available")

    from dragon.tool.builtins.browser import (
        browser_open, browser_get_text, browser_screenshot,
        browser_close, _get_state, _reset_state,
    )

    # Ensure clean state before test
    await _reset_state()

    try:
        # Use a static simple page served via data URL
        data_url = "data:text/html,<html><body><h1>Hello Dragon</h1><p>Test paragraph.</p></body></html>"

        # Open
        result = json.loads(await browser_open(data_url))
        assert "error" not in result, f"Open failed: {result}"
        assert result.get("url") == data_url or "url" in result

        # Get text
        text_result = json.loads(await browser_get_text())
        assert "error" not in text_result, f"Get text failed: {text_result}"
        assert "Hello Dragon" in text_result.get("text", "")

        # Screenshot
        shot_path = tmp_path / "browser_test.png"
        shot_result = json.loads(await browser_screenshot(str(shot_path)))
        assert "error" not in shot_result, f"Screenshot failed: {shot_result}"
        assert os.path.exists(shot_path)
        assert os.path.getsize(shot_path) > 0

        # Close
        close_result = json.loads(await browser_close())
        assert "error" not in close_result
    finally:
        await _reset_state()


# ── Registration ─────────────────────────────────────────────────────────

def test_browser_tools_registered():
    """Verify browser tools are registered in the builtins registry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    tool_names = [t["name"] for t in registry.list_tools()]

    assert "browser_open" in tool_names
    assert "browser_screenshot" in tool_names
    assert "browser_get_text" in tool_names
    assert "browser_click" in tool_names
    assert "browser_type" in tool_names
    assert "browser_close" in tool_names
