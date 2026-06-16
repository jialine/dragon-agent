"""
Dragon Agent — Browser Control Tools
=====================================

Playwright-based browser automation with lazy initialization and
automatic cleanup after idle timeout.

Tools:
    - browser_open: Navigate to a URL
    - browser_screenshot: Capture page screenshot
    - browser_get_text: Extract visible text content
    - browser_click: Click an element by CSS selector
    - browser_type: Type text into an input element
    - browser_close: Close the browser instance

Dependencies:
    - playwright (soft): pip install playwright && playwright install chromium
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("dragon.tool.builtins.browser")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

IDLE_CLOSE_SECS: float = 300.0  # Auto-close after 5 minutes of idle
IDLE_CHECK_INTERVAL: float = 5.0  # How often to check for idle timeout
SCREENSHOT_MAX_WIDTH: int = 1920
SCREENSHOT_MAX_HEIGHT: int = 1440

# ────────────────────────────────────────────────────────────────────
# Global Browser State (lazy singleton)
# ────────────────────────────────────────────────────────────────────

_state_lock = threading.Lock()

_browser: Any = None        # playwright.async_api.Browser
_page: Any = None           # playwright.async_api.Page
_playwright: Any = None     # playwright.async_api.Playwright
_last_activity: float = 0.0
_idle_task: Optional[asyncio.Task] = None


def _has_playwright() -> bool:
    """Check if Playwright Python package is importable."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _get_playwright_version() -> str:
    """Get Playwright version string or empty."""
    try:
        import playwright
        return getattr(playwright, "__version__", "installed")
    except ImportError:
        return ""


def _touch_activity() -> None:
    """Update the last-activity timestamp."""
    global _last_activity
    _last_activity = time.monotonic()


async def _start_idle_monitor() -> None:
    """Background task that closes browser after idle timeout."""
    global _idle_task
    while True:
        await asyncio.sleep(IDLE_CHECK_INTERVAL)
        elapsed = time.monotonic() - _last_activity
        if elapsed >= IDLE_CLOSE_SECS:
            logger.info("Browser idle for %.0fs — auto-closing", elapsed)
            await _close_browser_internal()
            break


async def _ensure_browser() -> Dict[str, Any]:
    """Ensure browser is running; return error dict if not available."""
    global _browser, _page, _playwright

    if not _has_playwright():
        return {
            "error": (
                "Playwright is not installed. "
                "Install with: pip install playwright && playwright install chromium"
            ),
            "install_hint": "pip install playwright && playwright install chromium",
        }

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "error": "playwright.async_api could not be imported. Install: pip install playwright",
        }

    if _browser is not None:
        _touch_activity()
        return {"status": "already_open"}

    try:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        _page = await _browser.new_page()
        _page.set_default_timeout(15000)  # 15s default timeout
        _touch_activity()

        # Start idle monitor
        global _idle_task
        try:
            loop = asyncio.get_running_loop()
            _idle_task = loop.create_task(_start_idle_monitor())
        except RuntimeError:
            pass

        logger.info("Browser launched (headless chromium)")
        return {"status": "launched"}
    except Exception as e:
        logger.exception("Failed to launch browser")
        await _close_browser_internal()
        return {
            "error": f"Failed to launch browser: {type(e).__name__}: {str(e)}",
        }


async def _close_browser_internal() -> None:
    """Internal: close browser and cleanup."""
    global _browser, _page, _playwright, _idle_task

    if _idle_task is not None:
        _idle_task.cancel()
        _idle_task = None

    if _page is not None:
        try:
            await _page.close()
        except Exception:
            pass
        _page = None

    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None

    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None

    logger.info("Browser closed")


async def _reset_state() -> None:
    """Reset global state (for testing)."""
    global _browser, _page, _playwright, _last_activity, _idle_task

    if _idle_task is not None:
        _idle_task.cancel()
        _idle_task = None

    if _page is not None:
        try:
            await _page.close()
        except Exception:
            pass
    if _browser is not None:
        try:
            await _browser.close()
        except Exception:
            pass
    if _playwright is not None:
        try:
            await _playwright.stop()
        except Exception:
            pass

    _browser = None
    _page = None
    _playwright = None
    _last_activity = 0.0


def _get_state() -> Dict[str, Any]:
    """Get current browser state (for testing and debugging)."""
    return {
        "browser_open": _browser is not None,
        "page_open": _page is not None,
        "last_activity": _last_activity,
        "playwright_version": _get_playwright_version(),
    }


# ────────────────────────────────────────────────────────────────────
# Tool: browser_open
# ────────────────────────────────────────────────────────────────────


async def browser_open(url: str, wait_until: str = "load") -> str:
    """Open a URL in the browser. Launches browser if not running.

    Args:
        url: The URL to navigate to.
        wait_until: When to consider navigation done.
                    One of: load, domcontentloaded, networkidle.

    Returns:
        JSON with url, title, and status or error.
    """
    if not url or not url.strip():
        return json.dumps({"error": "URL cannot be empty"})

    url = url.strip()

    # Basic URL validation
    if not url.startswith(("http://", "https://", "data:", "about:", "file://")):
        # Auto-prepend https
        url = "https://" + url

    ensure_result = await _ensure_browser()
    if "error" in ensure_result:
        return json.dumps(ensure_result)

    try:
        # Validate wait_until
        valid_wait = ("load", "domcontentloaded", "networkidle")
        if wait_until not in valid_wait:
            wait_until = "load"

        await _page.goto(url, wait_until=wait_until, timeout=30000)
        _touch_activity()

        title = await _page.title()
        current_url = _page.url

        return json.dumps({
            "url": current_url,
            "title": title,
            "status": "opened",
        })
    except Exception as e:
        logger.warning("Failed to navigate to %s: %s", url, e)
        return json.dumps({"error": f"Failed to open URL: {type(e).__name__}: {str(e)}"})


# ────────────────────────────────────────────────────────────────────
# Tool: browser_screenshot
# ────────────────────────────────────────────────────────────────────


async def browser_screenshot(save_path: str) -> str:
    """Take a screenshot of the current page.

    Args:
        save_path: File path to save the screenshot (PNG).

    Returns:
        JSON with path, size_bytes, and viewport dimensions or error.
    """
    if not save_path or not save_path.strip():
        return json.dumps({"error": "save_path cannot be empty"})

    if _page is None:
        return json.dumps({"error": "No browser page open. Use browser_open first."})

    p = Path(save_path).expanduser().resolve()
    # Ensure .png extension
    if p.suffix.lower() != ".png":
        p = p.with_suffix(".png")
    p.parent.mkdir(parents=True, exist_ok=True)

    _touch_activity()

    try:
        await _page.screenshot(
            path=str(p),
            full_page=False,
            clip=None,
        )

        file_size = p.stat().st_size

        # Get viewport size
        viewport = _page.viewport_size
        width = viewport.get("width", 0) if viewport else 0
        height = viewport.get("height", 0) if viewport else 0

        return json.dumps({
            "path": str(p),
            "size_bytes": file_size,
            "viewport": {"width": width, "height": height},
        })
    except Exception as e:
        logger.exception("Screenshot failed")
        return json.dumps({"error": f"Screenshot failed: {type(e).__name__}: {str(e)}"})


# ────────────────────────────────────────────────────────────────────
# Tool: browser_get_text
# ────────────────────────────────────────────────────────────────────


async def browser_get_text(selector: str = "body") -> str:
    """Extract visible text from the page.

    Args:
        selector: CSS selector of element to extract text from.
                  Defaults to 'body' for entire page text.

    Returns:
        JSON with text content (truncated to ~15000 chars) and metadata.
    """
    if _page is None:
        return json.dumps({"error": "No browser page open. Use browser_open first."})

    _touch_activity()

    try:
        element = await _page.query_selector(selector)
        if element is None:
            return json.dumps({"error": f"No element found for selector: {selector}"})

        text = await element.inner_text()
        char_count = len(text)

        # Truncate to reasonable size
        max_chars = 15000
        truncated = char_count > max_chars
        if truncated:
            text = text[:max_chars]

        return json.dumps({
            "text": text,
            "characters": char_count,
            "truncated": truncated,
            "selector": selector,
            "url": _page.url,
        })
    except Exception as e:
        logger.warning("Failed to get text for selector '%s': %s", selector, e)
        return json.dumps({"error": f"Failed to get text: {type(e).__name__}: {str(e)}"})


# ────────────────────────────────────────────────────────────────────
# Tool: browser_click
# ────────────────────────────────────────────────────────────────────


async def browser_click(selector: str) -> str:
    """Click an element on the page by CSS selector.

    Args:
        selector: CSS selector of the element to click.

    Returns:
        JSON with status and new URL or error.
    """
    if not selector or not selector.strip():
        return json.dumps({"error": "selector cannot be empty"})

    if _page is None:
        return json.dumps({"error": "No browser page open. Use browser_open first."})

    _touch_activity()

    try:
        element = await _page.query_selector(selector)
        if element is None:
            return json.dumps({"error": f"No element found for selector: {selector}"})

        await element.click()

        # Small wait for any navigation
        await asyncio.sleep(0.5)
        new_url = _page.url

        return json.dumps({
            "status": "clicked",
            "selector": selector,
            "url": new_url,
        })
    except Exception as e:
        logger.warning("Click failed for selector '%s': %s", selector, e)
        return json.dumps({"error": f"Click failed: {type(e).__name__}: {str(e)}"})


# ────────────────────────────────────────────────────────────────────
# Tool: browser_type
# ────────────────────────────────────────────────────────────────────


async def browser_type(selector: str, text: str, clear_first: bool = True) -> str:
    """Type text into an input element.

    Args:
        selector: CSS selector of the input element.
        text: Text to type.
        clear_first: Clear existing text before typing (default: True).

    Returns:
        JSON with status or error.
    """
    if not selector or not selector.strip():
        return json.dumps({"error": "selector cannot be empty"})

    if _page is None:
        return json.dumps({"error": "No browser page open. Use browser_open first."})

    _touch_activity()

    try:
        element = await _page.query_selector(selector)
        if element is None:
            return json.dumps({"error": f"No element found for selector: {selector}"})

        if clear_first:
            await element.fill("")

        await element.type(text)

        return json.dumps({
            "status": "typed",
            "selector": selector,
            "text_length": len(text),
        })
    except Exception as e:
        logger.warning("Type failed for selector '%s': %s", selector, e)
        return json.dumps({"error": f"Type failed: {type(e).__name__}: {str(e)}"})


# ────────────────────────────────────────────────────────────────────
# Tool: browser_close
# ────────────────────────────────────────────────────────────────────


async def browser_close() -> str:
    """Close the browser and clean up resources.

    Returns:
        JSON with status.
    """
    if _browser is None and _page is None:
        return json.dumps({"status": "already_closed"})

    await _close_browser_internal()
    return json.dumps({"status": "closed"})
