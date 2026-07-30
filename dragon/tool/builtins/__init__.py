"""
Dragon Agent — Built-in Tools
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
    - web_search: Search the web via DuckDuckGo (no API key)
    - web_fetch: Fetch a page and extract title + text
    - web_download: Download a file from URL to local path
    - tts: Text-to-speech via Microsoft Edge TTS
    - tts_voices: List available TTS voices
    - vision_analyze: AI-powered image analysis with metadata fallback
    - vision_info: Image metadata (format, dimensions, EXIF)
    - ocr: Optical Character Recognition via pytesseract
    - browser_open: Open a URL in headless browser (Playwright)
    - browser_screenshot: Capture page screenshot
    - browser_get_text: Extract visible text from page
    - browser_click: Click element by CSS selector
    - browser_type: Type text into input element
    - browser_close: Close the browser
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

from dragon.tool.builtins.tts import tool_tts, tool_tts_voices
from dragon.tool.builtins.vision import tool_vision_analyze, tool_vision_info, tool_ocr
from dragon.tool.builtins.analysis import tool_code_exec, tool_data_explore, tool_data_plot
from dragon.tool.builtins.execute_code import tool_execute_code
from dragon.tool.builtins.wan_video import tool_wan_video
from dragon.tool.builtins.documents import (
    tool_pptx_read,
    tool_pptx_create,
    tool_pdf_read,
    tool_pdf_extract,
    tool_docx_read,
)
from dragon.tool.builtins.email import (
    tool_email_send,
    tool_email_search,
    tool_email_read,
)
from dragon.tool.builtins.analysis import (
    tool_code_exec,
    tool_data_explore,
    tool_data_plot,
)
from dragon.tool.registry import ToolRegistry
from dragon.tool.builtins.workflows import set_workflow_store
from dragon.tool.builtins.cronjob import tool_cronjob
from dragon.tool.builtins.send_message import tool_send_message
from dragon.tool.builtins.process import tool_process
from dragon.tool.builtins.feishu_comments import (
    tool_feishu_drive_add_comment,
    tool_feishu_drive_list_comments,
    tool_feishu_drive_reply_comment,
    tool_feishu_drive_list_comment_replies,
)
from dragon.tool.builtins.kanban import (
    tool_kanban_create_board,
    tool_kanban_add_task,
    tool_kanban_list,
    tool_kanban_move,
    tool_kanban_delete_task,
    tool_kanban_list_boards,
)
from dragon.tool.builtins.image_gen import tool_image_generate, tool_image_models
from dragon.tool.builtins.browser import (
    browser_open, browser_screenshot, browser_get_text,
    browser_click, browser_type, browser_close,
)
from dragon.tool.builtins.maps import (
    tool_geocode, tool_reverse_geocode, tool_get_route, tool_search_poi,
)
from dragon.tool.builtins.obsidian import (
    tool_obsidian_read, tool_obsidian_search, tool_obsidian_create,
)
from dragon.tool.builtins.feishu_docs import (
    tool_feishu_read_doc, tool_feishu_list_docs, tool_feishu_create_doc,
)
from dragon.tool.builtins.youtube import tool_youtube_transcript, tool_youtube_summarize
from dragon.tool.builtins.spotify import tool_spotify_search, tool_spotify_now_playing, tool_spotify_play, tool_spotify_pause, tool_spotify_skip, tool_spotify_previous, tool_spotify_queue, tool_spotify_devices, tool_spotify_volume, tool_spotify_playlists
from dragon.tool.builtins.gif_search import tool_gif_search, tool_gif_trending
from dragon.tool.builtins.notion import tool_notion_search, tool_notion_read_page, tool_notion_create_page
from dragon.tool.builtins.linear import tool_linear_list_issues, tool_linear_create_issue
from dragon.tool.builtins.airtable import tool_airtable_list_records, tool_airtable_create_record
from dragon.tool.builtins import skills as _skills_module
from dragon.tool.builtins.google_workspace import (
    tool_gmail_send,
    tool_gmail_search,
    tool_google_drive_search,
    tool_google_calendar_list,
)
from dragon.tool.builtins.patch import tool_patch
from dragon.tool.builtins.clarify import tool_clarify
from dragon.tool.builtins.todo import tool_todo
from dragon.tool.builtins.memory import tool_memory, load_memory_for_prompt
from dragon.tool.builtins.session_search import tool_session_search
from dragon.web_providers import WebSearchRouter

logger = logging.getLogger("dragon.tool.builtins")

# ── Web Search Router (shared instance) ─────────────────────────────
_web_search_router = WebSearchRouter()


# ────────────────────────────────────────────────────────────────────
# Built-in tool implementations
# ────────────────────────────────────────────────────────────────────


async def tool_search(
    pattern: str,
    path: str = ".",
    target: str = "content",
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

    if target == "files":
        results = []
        count = 0
        for fp in search_dir.rglob(pattern if "*" in pattern else f"*{pattern}*"):
            if count >= max_results:
                break
            results.append(str(fp.relative_to(search_dir)))
            count += 1
        return json.dumps({"matches": results, "count": count})

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
    path: str,
    offset: int = 1,
    limit: int = 500,
) -> str:
    """Read a file's contents.

    Args:
        path: Path to the file.
        offset: First line to read (1-indexed).
        limit: Maximum number of lines to read.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return json.dumps({"error": f"File not found: {path}"})
    if p.is_dir():
        return json.dumps({"error": f"Path is a directory: {path}"})

    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").split("\n")
    except Exception as e:
        return json.dumps({"error": str(e)})

    start = max(0, offset - 1)
    end = min(len(lines), start + limit)
    selected = lines[start:end]

    return json.dumps({
        "file": str(p),
        "total_lines": len(lines),
        "offset": offset,
        "limit": limit,
        "end_line": end,
        "content": "\n".join(selected),
    })


async def tool_file_write(
    path: str,
    content: str,
    append: bool = False,
) -> str:
    """Write content to a file. OVERWRITES the entire file unless append=True.

    Args:
        path: Path to the file.
        content: Content to write.
        append: If True, append instead of overwrite.
    """
    p = Path(path).expanduser().resolve()
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
    timeout: int = 180,
    workdir: str = None,
    background: bool = False,
    pty: bool = False,
) -> str:
    """Execute shell commands on a Linux environment. Hermes-aligned."""
    # Map params
    timeout_secs = timeout
    if workdir is None:
        workdir = "."

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
# Web Tools: web_search, web_fetch, web_download
# ────────────────────────────────────────────────────────────────────


async def tool_web_search(query: str, max_results: int = 10, provider: str = "") -> str:
    """Search the web via multiple providers (Brave, SearXNG, DuckDuckGo).

    Supports Brave Search (set BRAVE_API_KEY), SearXNG (set SEARXNG_URL),
    or DuckDuckGo HTML scraping (always available, no key needed).

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default: 10).
        provider: Optional provider name to force a specific backend
            ('brave', 'searxng', 'duckduckgo'). Leave empty for auto-fallback.

    Returns:
        JSON with query, provider used, results list (title, url, snippet),
        and total count.
    """
    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty"})

    query = query.strip()

    # Validate explicit provider choice
    if provider:
        available = _web_search_router.list_providers()
        available_names = [p["name"] for p in available]
        if provider not in available_names:
            return json.dumps({
                "error": f"Provider '{provider}' is not available",
                "available": available,
            })

    try:
        used_provider, results = await _web_search_router.search(
            query, max_results=max_results, provider=provider or None
        )

        return json.dumps({
            "query": query,
            "provider": used_provider,
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ],
            "total": len(results),
        })
    except Exception as e:
        logger.warning("Web search failed for query '%s': %s", query, e)
        return json.dumps({
            "query": query,
            "provider": "error",
            "results": [],
            "total": 0,
            "error": str(e),
        })


async def tool_web_providers() -> str:
    """List available web search providers and their status.

    Returns:
        JSON with a list of provider metadata dicts (name, available).
    """
    available = _web_search_router.list_providers()
    return json.dumps({"providers": available})


async def tool_web_fetch(url: str) -> str:
    """Fetch a web page and return its title + first 5000 characters of text.

    Args:
        url: The URL to fetch.

    Returns:
        JSON with url, title, content (truncated), and status_code.
    """
    if not url or not url.strip():
        return json.dumps({"error": "URL cannot be empty"})

    url = url.strip()

    # Basic validation
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"Invalid URL: must start with http:// or https://"})

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            },
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            status_code = response.status_code

            # Extract title
            title = ""
            content = response.text[:10000]  # Read up to 10k chars
            title_match = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
            if title_match:
                title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

            # Strip HTML tags for text content
            text_content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
            text_content = re.sub(r"<script[^>]*>.*?</script>", "", text_content, flags=re.DOTALL | re.IGNORECASE)
            text_content = re.sub(r"<[^>]+>", " ", text_content)
            text_content = re.sub(r"\s+", " ", text_content).strip()

            # Limit to ~5000 chars
            max_chars = 5000
            if len(text_content) > max_chars:
                text_content = text_content[:max_chars]

            result = {
                "url": str(response.url),
                "status_code": status_code,
                "title": title,
                "content": text_content,
                "content_length": len(text_content),
            }

            if status_code >= 400:
                result["warning"] = f"HTTP {status_code}"

            return json.dumps(result)

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch URL: {type(e).__name__}: {str(e)}"})


async def tool_web_download(url: str, save_path: str) -> str:
    """Download a file from a URL to a local path.

    Args:
        url: The URL to download from.
        save_path: Local file path to save the downloaded content.

    Returns:
        JSON with path, size_bytes, and content_type.
    """
    if not url or not url.strip():
        return json.dumps({"error": "URL cannot be empty"})

    url = url.strip()

    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"Invalid URL: must start with http:// or https://"})

    if not save_path or not save_path.strip():
        return json.dumps({"error": "save_path cannot be empty"})

    save_path = save_path.strip()
    p = Path(save_path).expanduser().resolve()

    # Create parent directories
    p.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            p.write_bytes(response.content)

            content_type = response.headers.get("content-type", "unknown")

            return json.dumps({
                "path": str(p),
                "size_bytes": len(response.content),
                "content_type": content_type,
            })

    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}: {str(e)}"})
    except httpx.TimeoutException:
        return json.dumps({"error": "Download timed out"})
    except Exception as e:
        return json.dumps({"error": f"Download failed: {type(e).__name__}: {str(e)}"})


# ────────────────────────────────────────────────────────────────────
# Register all built-in tools
# ────────────────────────────────────────────────────────────────────


def _register_feishu_docs(registry):
    """Register Feishu document tools (read, list, create)."""
    registry.register(
        name="feishu_read_doc",
        description="Read a Feishu/Lark document as plain text. Provide the doc_token from the document URL.",
        tags=["feishu", "document", "read"],
        category="productivity",
        timeout_secs=30,
    )(tool_feishu_read_doc)

    registry.register(
        name="feishu_list_docs",
        description="List recent Feishu documents with names, tokens, and URLs.",
        tags=["feishu", "document", "list"],
        category="productivity",
        timeout_secs=30,
    )(tool_feishu_list_docs)

    registry.register(
        name="feishu_create_doc",
        description="Create a new Feishu document with optional initial text content.",
        tags=["feishu", "document", "create"],
        category="productivity",
        timeout_secs=30,
    )(tool_feishu_create_doc)


def _register_image_gen(registry):
    registry.register(
        name="image_generate",
        description="Generate an image from a text prompt",
        tags=["image", "generate", "ai"],
        category="media",
        timeout_secs=300,
    )(tool_image_generate)

    registry.register(
        name="image_models",
        description="List available image generation models",
        tags=["image", "models", "list"],
        category="media",
        timeout_secs=10,
    )(tool_image_models)


def _register_maps(registry):
    registry.register(
        name="geocode",
        description="Convert an address or place name to latitude/longitude coordinates using OpenStreetMap Nominatim.",
        tags=["maps", "geocode", "coordinates", "location"],
        category="maps",
        timeout_secs=15,
    )(tool_geocode)

    registry.register(
        name="reverse_geocode",
        description="Convert latitude/longitude coordinates to a human-readable address.",
        tags=["maps", "geocode", "reverse", "location"],
        category="maps",
        timeout_secs=15,
    )(tool_reverse_geocode)

    registry.register(
        name="get_route",
        description="Get a route with turn-by-turn directions between two points using OSRM. Supports car, bike, and foot modes.",
        tags=["maps", "route", "directions", "navigation"],
        category="maps",
        timeout_secs=30,
    )(tool_get_route)

    registry.register(
        name="search_poi",
        description="Search for points of interest (restaurants, hospitals, hotels, etc.) near a location using OpenStreetMap.",
        tags=["maps", "poi", "search", "places"],
        category="maps",
        timeout_secs=15,
    )(tool_search_poi)


def _register_youtube(registry):
    registry.register(
        name="youtube_transcript",
        description="Get transcript/subtitles from a YouTube video. Returns timed segments with text, start time, and duration.",
        tags=["youtube", "transcript", "subtitles", "video"],
        category="media",
        timeout_secs=30,
    )(tool_youtube_transcript)

    registry.register(
        name="youtube_summarize",
        description="Get a YouTube video transcript as a plain-text summary with timestamps, ready for AI summarization.",
        tags=["youtube", "summarize", "transcript", "video"],
        category="media",
        timeout_secs=30,
    )(tool_youtube_summarize)


def _register_obsidian(registry):
    registry.register(
        name="obsidian_read",
        description="Read an Obsidian note from a local vault (Markdown file). Returns title, content, word count, and metadata.",
        tags=["obsidian", "notes", "read", "markdown"],
        category="productivity",
        timeout_secs=15,
    )(tool_obsidian_read)

    registry.register(
        name="obsidian_search",
        description="Search Obsidian notes by keyword or regex. Results are ranked by match count with snippets.",
        tags=["obsidian", "search", "notes", "markdown"],
        category="productivity",
        timeout_secs=30,
    )(tool_obsidian_search)

    registry.register(
        name="obsidian_create",
        description="Create a new Obsidian note with YAML frontmatter (date, tags). Supports vault subfolders.",
        tags=["obsidian", "create", "notes", "markdown"],
        category="productivity",
        timeout_secs=10,
    )(tool_obsidian_create)


def _register_google(registry):
    """Register Google Workspace tools (Gmail + Drive + Calendar)."""
    registry.register(
        name="gmail_send",
        description="Send email via Gmail SMTP. Requires GMAIL_USER and GMAIL_APP_PASSWORD env vars.",
        tags=["google", "gmail", "email", "send"],
        category="email",
        timeout_secs=30,
    )(tool_gmail_send)

    registry.register(
        name="gmail_search",
        description="Search Gmail inbox via IMAP. Returns subject, from, date for matching emails.",
        tags=["google", "gmail", "email", "search"],
        category="email",
        timeout_secs=30,
    )(tool_gmail_search)

    registry.register(
        name="google_drive_search",
        description="Search Google Drive files by name. Requires GOOGLE_DRIVE_API_KEY env var.",
        tags=["google", "drive", "search", "files"],
        category="productivity",
        timeout_secs=15,
    )(tool_google_drive_search)

    registry.register(
        name="google_calendar_list",
        description="List upcoming Google Calendar events. Requires GOOGLE_CALENDAR_API_KEY env var.",
        tags=["google", "calendar", "events", "schedule"],
        category="productivity",
        timeout_secs=15,
    )(tool_google_calendar_list)


def _register_spotify(registry):
    """Register Spotify music tools (search, playback, devices, playlists)."""
    registry.register(
        name="spotify_search",
        description="Search Spotify for tracks, albums, artists, or playlists. Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.",
        tags=["spotify", "music", "search", "audio"],
        category="media",
        timeout_secs=15,
    )(tool_spotify_search)

    registry.register(
        name="spotify_now_playing",
        description="Get the currently playing track on Spotify. Requires SPOTIFY_REFRESH_TOKEN for user authorization.",
        tags=["spotify", "music", "playback", "now-playing"],
        category="media",
        timeout_secs=15,
    )(tool_spotify_now_playing)

    registry.register(
        name="spotify_play",
        description="Start or resume Spotify playback. Optionally specify a track URI, context URI, or device ID.",
        tags=["spotify", "music", "playback"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_play)

    registry.register(
        name="spotify_pause",
        description="Pause Spotify playback.",
        tags=["spotify", "music", "playback"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_pause)

    registry.register(
        name="spotify_skip",
        description="Skip to the next track on Spotify.",
        tags=["spotify", "music", "playback"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_skip)

    registry.register(
        name="spotify_previous",
        description="Go back to the previous track on Spotify.",
        tags=["spotify", "music", "playback"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_previous)

    registry.register(
        name="spotify_queue",
        description="Add a track to the Spotify playback queue.",
        tags=["spotify", "music", "queue"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_queue)

    registry.register(
        name="spotify_devices",
        description="List available Spotify devices (name, type, volume, active status).",
        tags=["spotify", "music", "devices"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_devices)

    registry.register(
        name="spotify_volume",
        description="Set Spotify playback volume (0-100).",
        tags=["spotify", "music", "playback", "volume"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_volume)

    registry.register(
        name="spotify_playlists",
        description="List the current user's Spotify playlists.",
        tags=["spotify", "music", "playlists"],
        category="media",
        timeout_secs=10,
    )(tool_spotify_playlists)


def _register_gif(registry):
    """Register GIF search tools (Tenor API)."""
    registry.register(
        name="gif_search",
        description="Search for GIFs via the Tenor API. Requires TENOR_API_KEY.",
        tags=["gif", "tenor", "search", "media"],
        category="media",
        timeout_secs=15,
    )(tool_gif_search)

    registry.register(
        name="gif_trending",
        description="Get trending GIFs from Tenor. Requires TENOR_API_KEY.",
        tags=["gif", "tenor", "trending", "media"],
        category="media",
        timeout_secs=15,
    )(tool_gif_trending)


def _register_notion(registry):
    """Register Notion tools (search, read, create pages)."""
    registry.register(
        name="notion_search",
        description="Search Notion pages by title or content. Requires NOTION_API_KEY.",
        tags=["notion", "search", "pages", "productivity"],
        category="productivity",
        timeout_secs=30,
    )(tool_notion_search)

    registry.register(
        name="notion_read_page",
        description="Read a Notion page as plain text by page ID. Requires NOTION_API_KEY.",
        tags=["notion", "read", "pages", "productivity"],
        category="productivity",
        timeout_secs=30,
    )(tool_notion_read_page)

    registry.register(
        name="notion_create_page",
        description="Create a new Notion page with title and optional content. Requires NOTION_API_KEY.",
        tags=["notion", "create", "pages", "productivity"],
        category="productivity",
        timeout_secs=30,
    )(tool_notion_create_page)


def _register_linear(registry):
    """Register Linear tools (list, create issues)."""
    registry.register(
        name="linear_list_issues",
        description="List Linear issues with optional team filter. Requires LINEAR_API_KEY.",
        tags=["linear", "issues", "list", "project-management"],
        category="productivity",
        timeout_secs=30,
    )(tool_linear_list_issues)

    registry.register(
        name="linear_create_issue",
        description="Create a new Linear issue with title, description, and optional team. Requires LINEAR_API_KEY.",
        tags=["linear", "issues", "create", "project-management"],
        category="productivity",
        timeout_secs=30,
    )(tool_linear_create_issue)


def _register_airtable(registry):
    """Register Airtable tools (list, create records)."""
    registry.register(
        name="airtable_list_records",
        description="List records from an Airtable table by base ID and table name. Requires AIRTABLE_API_KEY.",
        tags=["airtable", "records", "list", "database"],
        category="productivity",
        timeout_secs=30,
    )(tool_airtable_list_records)

    registry.register(
        name="airtable_create_record",
        description="Create a new record in an Airtable table with JSON fields. Requires AIRTABLE_API_KEY.",
        tags=["airtable", "records", "create", "database"],
        category="productivity",
        timeout_secs=30,
    )(tool_airtable_create_record)


def _register_skills(registry):
    """Register skill management tools (search, load, install, create)."""
    registry.register(
        name="search_skills",
        description="Search available skills by name, description, or tags. Use this to find relevant skills for any task.",
        tags=["skill", "search", "discovery"],
        category="skills",
        timeout_secs=10,
    )(_skills_module.tool_search_skills)

    registry.register(
        name="load_skill",
        description="Load a skill's full content into the conversation. Use when you need detailed instructions for a task.",
        tags=["skill", "load", "knowledge"],
        category="skills",
        timeout_secs=10,
    )(_skills_module.tool_load_skill)

    registry.register(
        name="install_skill",
        description="Install a skill from an external source (Hermes, OpenClaw) by name or search query.",
        tags=["skill", "install", "import"],
        category="skills",
        timeout_secs=30,
    )(_skills_module.tool_install_skill)

    registry.register(
        name="skill_manage",
        description="Manage skills: create, patch, delete. Hermes-aligned. After completing a complex task, always save as a skill with action='create'.",
        tags=["skill", "create", "patch", "delete", "evolution"],
        category="skills",
        timeout_secs=15,
    )(_skills_module.tool_skill_manage)

    registry.register(
        name="skill_view",
        description="Load a skill's full content. Use BEFORE executing a task to get detailed instructions.",
        tags=["skill", "view", "load"],
        category="skills",
        timeout_secs=10,
    )(_skills_module.tool_skill_view)




async def tool_terminal(
    command: str,
    timeout: int = 180,
    workdir: str = None,
    background: bool = False,
    pty: bool = False,
) -> str:
    """Execute shell commands. Hermes-aligned: terminal(command, timeout, workdir, background, pty)."""
    return await tool_execute(
        command=command,
        timeout=timeout,
        workdir=workdir or ".",
        background=background,
        pty=pty,
    )

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
        description="Read a text file with line numbers and pagination. Use offset and limit for large files.",
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
        name="terminal",
        description="Execute shell commands on a Linux environment. Hermes-aligned.",
        tags=["terminal", "shell", "command"],
        category="terminal",
        timeout_secs=300,
    )(tool_terminal)

    registry.register(
        name="http_get",
        description="Make an HTTP GET request to a URL.",
        tags=["web", "http", "api"],
        category="web",
        timeout_secs=30,
    )(tool_http_get)

    # ── Web Search / Fetch / Download ─────────────────────────────
    registry.register(
        name="web_search",
        description="Search the web via multiple providers (Brave, SearXNG, DuckDuckGo). Returns title, url, snippet, and provider used.",
        tags=["web", "search", "brave", "searxng", "duckduckgo"],
        category="web",
        timeout_secs=30,
    )(tool_web_search)

    registry.register(
        name="web_providers",
        description="List available web search providers (Brave, SearXNG, DuckDuckGo) and their status.",
        tags=["web", "search", "providers", "status"],
        category="web",
        timeout_secs=10,
    )(tool_web_providers)

    registry.register(
        name="web_fetch",
        description="Fetch a web page and return its title and text content (first 5000 chars).",
        tags=["web", "fetch", "http"],
        category="web",
        timeout_secs=30,
    )(tool_web_fetch)

    registry.register(
        name="web_download",
        description="Download a file from a URL to a local path. Returns path, size_bytes, and content_type.",
        tags=["web", "download", "file"],
        category="web",
        timeout_secs=60,
    )(tool_web_download)

    # ── TTS (Text-to-Speech) ────────────────────────────────────
    registry.register(
        name="tts",
        description="Convert text to speech using Microsoft Edge TTS. Supports Chinese neural voices.",
        tags=["audio", "tts", "speech", "voice"],
        category="media",
        timeout_secs=180,
    )(tool_tts)

    registry.register(
        name="tts_voices",
        description="List available TTS voice identifiers for use with the tts tool.",
        tags=["audio", "tts", "voice"],
        category="media",
        timeout_secs=10,
    )(tool_tts_voices)

    # ── Vision / Image Recognition ────────────────────────────────
    registry.register(
        name="vision_analyze",
        description="Analyze images using AI vision or basic metadata. Supports local files and URLs.",
        tags=["vision", "image", "analyze", "multimodal"],
        category="media",
        timeout_secs=120,
    )(tool_vision_analyze)

    registry.register(
        name="vision_info",
        description="Get image metadata: format, dimensions, file size, EXIF data.",
        tags=["vision", "image", "metadata", "info"],
        category="media",
        timeout_secs=10,
    )(tool_vision_info)

    registry.register(
        name="ocr",
        description="Extract text from images using OCR (pytesseract). Supports Chinese and English.",
        tags=["vision", "ocr", "text", "image"],
        category="media",
        timeout_secs=60,
    )(tool_ocr)

    # ── Data Analysis ────────────────────────────────────────────
    registry.register(
        name="code_exec",
        description="Execute Python code in a sandbox with safety restrictions. Blocks dangerous modules.",
        tags=["code", "execute", "python", "sandbox"],
        category="analysis",
        timeout_secs=60,
    )(tool_code_exec)

    registry.register(
        name="execute_code",
        description="Execute Python code with FULL system access in a subprocess. Can run shell commands, install packages, create files, start servers. Use for development tasks. Returns JSON: success, output, exit_code, duration_ms.",
        schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source code to execute with full system access"},
                "timeout": {"type": "integer", "description": "Max seconds (default 60, max 300)"},
            },
            "required": ["code"],
        },
        tags=["code", "execution", "python", "subprocess", "development", "system"],
        category="development",
        timeout_secs=300,
    )(tool_execute_code)

    registry.register(
        name="wan_video",
        description="Generate video from text using Wan2.7 AI. Text-to-video and image-to-video. Async with polling. Returns video file path.",
        schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Video scene description"},
                "model": {"type": "string", "description": "wan2.7-t2v or wan2.7-r2v"},
                "size": {"type": "string", "description": "1280x720 or 720x1280"},
                "duration": {"type": "integer", "description": "Seconds (min 5)"},
            },
            "required": ["prompt"],
        },
        tags=["video", "generation", "wan", "ai-video", "creative"],
        category="creative",
        timeout_secs=600,
    )(tool_wan_video)

    registry.register(
        name="data_explore",
        description="Explore a data file (CSV, JSON, Excel) — rows, columns, preview, statistics.",
        tags=["data", "explore", "csv", "json", "excel", "statistics"],
        category="analysis",
        timeout_secs=30,
    )(tool_data_explore)

    registry.register(
        name="data_plot",
        description="Execute matplotlib code and save the plot as a PNG file.",
        tags=["data", "plot", "matplotlib", "visualization", "chart"],
        category="analysis",
        timeout_secs=120,
    )(tool_data_plot)

    # ── Browser (Playwright) ──────────────────────────────────────
    registry.register(
        name="browser_open",
        description="Open a URL in the headless browser. Launches browser automatically if not running.",
        tags=["browser", "web", "playwright"],
        category="browser",
        timeout_secs=60,
    )(browser_open)

    registry.register(
        name="browser_screenshot",
        description="Take a screenshot of the current browser page (PNG).",
        tags=["browser", "screenshot", "image"],
        category="browser",
        timeout_secs=30,
    )(browser_screenshot)

    registry.register(
        name="browser_get_text",
        description="Extract visible text from the current browser page.",
        tags=["browser", "text", "extract"],
        category="browser",
        timeout_secs=30,
    )(browser_get_text)

    registry.register(
        name="browser_click",
        description="Click an element on the page by CSS selector.",
        tags=["browser", "click", "interact"],
        category="browser",
        timeout_secs=30,
    )(browser_click)

    registry.register(
        name="browser_type",
        description="Type text into an input element on the page by CSS selector.",
        tags=["browser", "type", "input", "interact"],
        category="browser",
        timeout_secs=30,
    )(browser_type)

    registry.register(
        name="browser_close",
        description="Close the browser and clean up resources.",
        tags=["browser", "close", "cleanup"],
        category="browser",
        timeout_secs=15,
    )(browser_close)

    # ── Documents (PPT/PDF/DOCX) ──────────────────────────────────
    registry.register(
        name="pptx_read",
        description="Read a PPTX file and return the title and text content of each slide.",
        tags=["document", "pptx", "powerpoint", "read"],
        category="document",
        timeout_secs=30,
    )(tool_pptx_read)

    registry.register(
        name="pptx_create",
        description="Create a PPTX file from slide definitions. slides=[{\"title\":\"...\", \"content\":[\"...\"]}]",
        tags=["document", "pptx", "powerpoint", "create"],
        category="document",
        timeout_secs=30,
    )(tool_pptx_create)

    registry.register(
        name="pdf_read",
        description="Read text from a PDF file with optional page range (pymupdf/fitz).",
        tags=["document", "pdf", "read"],
        category="document",
        timeout_secs=30,
    )(tool_pdf_read)

    registry.register(
        name="pdf_extract",
        description="Extract pages from a PDF as PNG images (pymupdf/fitz).",
        tags=["document", "pdf", "extract", "image"],
        category="document",
        timeout_secs=60,
    )(tool_pdf_extract)

    registry.register(
        name="docx_read",
        description="Read text content and tables from a DOCX file.",
        tags=["document", "docx", "word", "read"],
        category="document",
        timeout_secs=30,
    )(tool_docx_read)

    # ── Kanban Project Management ──────────────────────────────────
    registry.register(
        name="kanban_create_board",
        description="Create a new Kanban board for project management. Boards are stored as JSON files.",
        tags=["kanban", "board", "project", "create"],
        category="productivity",
        timeout_secs=10,
    )(tool_kanban_create_board)

    registry.register(
        name="kanban_add_task",
        description="Add a task to a Kanban board with title, description, status, and priority.",
        tags=["kanban", "task", "add"],
        category="productivity",
        timeout_secs=10,
    )(tool_kanban_add_task)

    registry.register(
        name="kanban_list",
        description="List tasks on a Kanban board, optionally filtered by status column.",
        tags=["kanban", "task", "list"],
        category="productivity",
        timeout_secs=10,
    )(tool_kanban_list)

    registry.register(
        name="kanban_move",
        description="Move a Kanban task to a new status column (todo, in_progress, review, done).",
        tags=["kanban", "task", "move", "status"],
        category="productivity",
        timeout_secs=10,
    )(tool_kanban_move)

    registry.register(
        name="kanban_delete_task",
        description="Delete a task from a Kanban board by task ID.",
        tags=["kanban", "task", "delete"],
        category="productivity",
        timeout_secs=10,
    )(tool_kanban_delete_task)

    registry.register(
        name="kanban_list_boards",
        description="List all Kanban boards with task counts and creation dates.",
        tags=["kanban", "board", "list"],
        category="productivity",
        timeout_secs=10,
    )(tool_kanban_list_boards)

    # --- Workflow management tools ---
    import dragon.tool.builtins.workflows as _wf_module
    registry.register(
        name="create_workflow",
        description="Create a new workflow for multi-step task orchestration. Use for complex multi-step tasks that need tracking across sessions.",
        tags=["workflow", "create", "orchestration"],
        category="workflows",
        timeout_secs=10,
    )(_wf_module.tool_create_workflow)
    registry.register(
        name="list_workflows",
        description="List active or recent workflows. Filter by status.",
        tags=["workflow", "list"],
        category="workflows",
        timeout_secs=10,
    )(_wf_module.tool_list_workflows)
    registry.register(
        name="update_workflow",
        description="Update workflow status (done/failed) and summary.",
        tags=["workflow", "update"],
        category="workflows",
        timeout_secs=10,
    )(_wf_module.tool_update_workflow)

    # ── Email (SMTP/IMAP) ───────────────────────────────────────────
    registry.register(
        name="email_send",
        description="Send an email via SMTP. Supports CC, attachments, and env-var credentials.",
        tags=["email", "smtp", "send"],
        category="email",
        timeout_secs=30,
    )(tool_email_send)

    registry.register(
        name="email_search",
        description="Search emails in an IMAP folder. Returns headers for matching messages.",
        tags=["email", "imap", "search"],
        category="email",
        timeout_secs=30,
    )(tool_email_search)

    registry.register(
        name="email_read",
        description="Read a specific email by UID from an IMAP folder. Returns full content.",
        tags=["email", "imap", "read"],
        category="email",
        timeout_secs=30,
    )(tool_email_read)

    # ── Image Generation ───────────────────────────────────────────
    _register_image_gen(registry)

    # ── Maps / Geolocation ─────────────────────────────────────────
    _register_maps(registry)

    # ── Feishu Documents ────────────────────────────────────────────
    _register_feishu_docs(registry)

    # ── YouTube ────────────────────────────────────────────────────
    _register_youtube(registry)

    # ── Obsidian ──────────────────────────────────────────────────
    _register_obsidian(registry)

    # ── Google Workspace (Gmail + Drive + Calendar) ───────────────
    _register_google(registry)

    # ── Spotify ──────────────────────────────────────────────────
    _register_spotify(registry)

    # ── GIF Search (Tenor) ───────────────────────────────────────
    _register_gif(registry)

    # ── Notion ───────────────────────────────────────────────────
    _register_notion(registry)

    # ── Linear ──────────────────────────────────────────────────
    _register_linear(registry)

    # ── Airtable ────────────────────────────────────────────────
    _register_airtable(registry)

    # ── Skills (search, load, install, create) ────────────────────
    _register_skills(registry)


    # ── Subagent Delegation (delegate_task, delegate_many) ─────────
    try:
        from dragon.subagent import SubagentOrchestrator

        async def _tool_delegate_task(
            goal: str,
            context: str = "",
            timeout_secs: float = 120.0,
        ) -> str:
            """Spawn a subagent to work on a task independently."""
            import json as _json, os, yaml
            from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig

            _pr = ProviderRegistry()
            for p in ['config.yaml', os.path.expanduser('~/.dragon/config.yaml')]:
                if os.path.exists(p):
                    with open(p) as f:
                        cfg = yaml.safe_load(f) or {}
                    api = cfg.get('dispatch', {}).get('global_api', {})
                    _pr.register('openai', OpenAIProvider(ProviderConfig(
                        provider='openai',
                        api_key=os.getenv(api.get('api_key_env', ''), 'not-needed'),
                        base_url=api.get('base_url'),
                        default_model=api.get('model', 'gpt-4o'),
                    )))
                    break

            orch = SubagentOrchestrator(
                provider_registry=_pr, tool_registry=registry,
                max_concurrent=1,
            )
            result = await orch.delegate(
                goal=goal, context=context,
                timeout_secs=min(timeout_secs, 300),
            )
            return _json.dumps({
                "status": result.status.value,
                "summary": result.summary[:2000],
                "findings": result.findings,
                "tokens_used": result.tokens_used,
                "confidence": result.confidence,
                "tool_calls": result.tool_calls,
                "latency_ms": result.latency_ms,
            }, ensure_ascii=False)

        async def _tool_delegate_many(
            tasks: str,
            timeout_secs: float = 180.0,
        ) -> str:
            """Spawn multiple subagents in parallel (max 3)."""
            import json as _json, os, yaml
            from dragon.provider import ProviderRegistry, OpenAIProvider, ProviderConfig

            task_list = _json.loads(tasks) if isinstance(tasks, str) else tasks
            task_list = task_list[:3]

            _pr = ProviderRegistry()
            for p in ['config.yaml', os.path.expanduser('~/.dragon/config.yaml')]:
                if os.path.exists(p):
                    with open(p) as f:
                        cfg = yaml.safe_load(f) or {}
                    api = cfg.get('dispatch', {}).get('global_api', {})
                    _pr.register('openai', OpenAIProvider(ProviderConfig(
                        provider='openai',
                        api_key=os.getenv(api.get('api_key_env', ''), 'not-needed'),
                        base_url=api.get('base_url'),
                        default_model=api.get('model', 'gpt-4o'),
                    )))
                    break

            orch = SubagentOrchestrator(
                provider_registry=_pr, tool_registry=registry,
                max_concurrent=min(len(task_list), 3),
            )
            results = await orch.delegate_many(task_list)
            return _json.dumps([{
                "goal": r.goal[:100],
                "status": r.status.value,
                "summary": r.summary[:1000],
                "confidence": r.confidence,
            } for r in results], ensure_ascii=False)

        registry.register(
            name="delegate_task",
            description="Spawn a subagent to work on a task independently with isolated context. "
                        "Use for research, code analysis, data processing — any focused task. "
                        "Returns summary, findings, confidence score, and token usage.",
            tags=["delegation", "subagent", "parallel"],
            category="delegation",
            timeout_secs=300,
            max_retries=1,
        )(_tool_delegate_task)

        registry.register(
            name="delegate_many",
            description="Spawn up to 3 subagents in parallel for independent tasks. "
                        "Pass tasks as JSON array of {goal, context}. Each runs in isolation. "
                        "Use when you have multiple independent subtasks.",
            tags=["delegation", "subagent", "parallel", "batch"],
            category="delegation",
            timeout_secs=600,
            max_retries=1,
        )(_tool_delegate_many)

        logger.info("Subagent delegation tools registered (delegate_task, delegate_many)")
    except ImportError:
        logger.debug("Subagent tools skipped: subagent module not available")
    except Exception as _e:
        logger.warning("Failed to register subagent tools: %s", _e)

    # ── Hermes-aligned Tools: patch, clarify, todo, session_search ──
    registry.register(
        name="patch",
        description="Targeted find-and-replace edits in files. Returns unified diff format. Hermes-aligned: patch(path, old_string, new_string, replace_all=False).",
        tags=["file", "edit", "patch", "diff"],
        category="file",
        timeout_secs=30,
    )(tool_patch)

    registry.register(
        name="clarify",
        description="Ask the user a clarifying question with optional choices. Hermes-aligned: clarify(question, choices=None). Returns user response or confirmation.",
        tags=["interaction", "clarify", "question"],
        category="interaction",
        timeout_secs=300,
    )(tool_clarify)

    registry.register(
        name="todo",
        description="Manage a session-level todo list. View, set, or merge items. Hermes-aligned: todo(todos=None, merge=False). Items: {id, content, status}.",
        tags=["todo", "task", "list", "productivity"],
        category="productivity",
        timeout_secs=15,
    )(tool_todo)

    registry.register(
        name="memory",
        description="Save durable information to persistent memory that survives across sessions.",
        tags=["memory", "persistence"],
        category="memory",
        timeout_secs=10,
    )(tool_memory)

    registry.register(
        name="session_search",

        description="Search or list past conversation sessions with FTS5 full-text search. Hermes-aligned: session_search(query=None, limit=3). Returns session summaries.",
        tags=["session", "search", "history", "memory"],
        category="memory",
        timeout_secs=15,
    )(tool_session_search)

    # ── Hermes-aligned tool aliases ──────────────────────────────────
    # Register with Hermes names so LLM trained on Hermes conventions works
    if "file_read" in registry._tools:
        # read_file → file_read
        registry._tools["read_file"] = registry._tools["file_read"]
        registry._tools["read_file"].name = "read_file"
        registry._tools["read_file"].description = (
            "Read a text file with line numbers and pagination. "
            "Use offset and limit for large files. Hermes-aligned."
        )
    if "file_write" in registry._tools:
        # write_file → file_write
        registry._tools["write_file"] = registry._tools["file_write"]
        registry._tools["write_file"].name = "write_file"
        registry._tools["write_file"].description = (
            "Write content to a file, creating parent directories. "
            "Always overwrites the entire file. Hermes-aligned."
        )
    if "search" in registry._tools:
        # search_files → search
        registry._tools["search_files"] = registry._tools["search"]
        registry._tools["search_files"].name = "search_files"
        registry._tools["search_files"].description = (
            "Search file contents or find files by name. "
            "Use pattern with target='content' or target='files'. Hermes-aligned."
        )
    if "vision_analyze" in registry._tools:
        pass  # placeholder
    if "tts" in registry._tools:
        registry._tools["text_to_speech"] = registry._tools["tts"]
        registry._tools["text_to_speech"].name = "text_to_speech"
        registry._tools["text_to_speech"].description = "Convert text to speech audio. Hermes-aligned: text_to_speech(text, output_path)."
    if "feishu_read_doc" in registry._tools:
        registry._tools["feishu_doc_read"] = registry._tools["feishu_read_doc"]
        registry._tools["feishu_doc_read"].name = "feishu_doc_read"
        registry._tools["feishu_doc_read"].description = "Read the full content of a Feishu/Lark document. Hermes-aligned: feishu_doc_read(doc_token)."

    # ── Send Message ────────────────────────────────────────────
    registry.register(
        name="send_message",
        description="Send a message to a connected messaging platform, or list available targets. Hermes-aligned: send_message(action='send'|'list', target, message, file_path).",
        tags=["messaging", "feishu", "send"],
        category="interaction",
        timeout_secs=30,
    )(tool_send_message)

    # ── Process Management ───────────────────────────────────────
    registry.register(
        name="process",
        description="Manage background processes. Hermes-aligned: process(action, session_id, command). Actions: list, start, poll, log, wait, kill, write, submit, close.",
        tags=["process", "background", "terminal"],
        category="terminal",
        timeout_secs=300,
    )(tool_process)

    # ── Feishu Drive Comments ────────────────────────────────────
    registry.register(
        name="feishu_drive_add_comment",
        description="Add a whole-document comment on a Feishu document. Hermes-aligned: feishu_drive_add_comment(file_token, content).",
        tags=["feishu", "document", "comment"],
        category="productivity",
        timeout_secs=15,
    )(tool_feishu_drive_add_comment)

    registry.register(
        name="feishu_drive_list_comments",
        description="List comments on a Feishu document. Hermes-aligned: feishu_drive_list_comments(file_token, is_whole=False).",
        tags=["feishu", "document", "comment"],
        category="productivity",
        timeout_secs=15,
    )(tool_feishu_drive_list_comments)

    registry.register(
        name="feishu_drive_reply_comment",
        description="Reply to a comment thread on a Feishu document. Hermes-aligned: feishu_drive_reply_comment(file_token, comment_id, content).",
        tags=["feishu", "document", "comment"],
        category="productivity",
        timeout_secs=15,
    )(tool_feishu_drive_reply_comment)

    registry.register(
        name="feishu_drive_list_comment_replies",
        description="List all replies in a comment thread on a Feishu document. Hermes-aligned.",
        tags=["feishu", "document", "comment"],
        category="productivity",
        timeout_secs=15,
    )(tool_feishu_drive_list_comment_replies)

    # ── Cronjob ─────────────────────────────────────────────────
    registry.register(
        name="cronjob",
        description="Manage scheduled cron jobs. Hermes-aligned: cronjob(action, name, schedule, prompt, job_id). Actions: create, list, pause, resume, remove, run, stats.",
        tags=["cron", "schedule", "automation"],
        category="automation",
        timeout_secs=15,
    )(tool_cronjob)

    logger.info("Registered %d built-in tools (with Hermes aliases)", len(registry._tools))
