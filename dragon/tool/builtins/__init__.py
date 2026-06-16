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
from dragon.tool.builtins.kanban import (
    tool_kanban_create_board,
    tool_kanban_add_task,
    tool_kanban_list,
    tool_kanban_move,
    tool_kanban_delete_task,
    tool_kanban_list_boards,
)
from dragon.tool.builtins.browser import (
    browser_open, browser_screenshot, browser_get_text,
    browser_click, browser_type, browser_close,
)


logger = logging.getLogger("dragon.tool.builtins")


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
# Web Tools: web_search, web_fetch, web_download
# ────────────────────────────────────────────────────────────────────


async def tool_web_search(query: str, max_results: int = 10) -> str:
    """Search the web via DuckDuckGo HTML (no API key required).

    Args:
        query: Search query string.
        max_results: Maximum number of results to return (default: 10).

    Returns:
        JSON with query, results list (title, url, snippet), and total count.
    """
    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty"})

    query = query.strip()
    results: List[Dict[str, str]] = []

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        ) as client:

            # Try DuckDuckGo HTML search (POST)
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )

            if resp.status_code == 200:
                results = _parse_duckduckgo_html(resp.text, max_results)

            # If DuckDuckGo HTML didn't return results, fall back to regex
            # parsing of the lite version
            if not results:
                resp2 = await client.get(
                    "https://lite.duckduckgo.com/lite/",
                    params={"q": query},
                )
                if resp2.status_code == 200:
                    results = _parse_duckduckgo_lite(resp2.text, max_results)

    except Exception as e:
        logger.warning("Web search failed for query '%s': %s", query, e)
        return json.dumps({
            "query": query,
            "results": [],
            "total": 0,
            "error": str(e),
        })

    return json.dumps({
        "query": query,
        "results": results,
        "total": len(results),
    })


def _parse_duckduckgo_html(html: str, max_results: int) -> List[Dict[str, str]]:
    """Extract results from DuckDuckGo HTML response using regex."""
    results: List[Dict[str, str]] = []

    # Pattern for result links: <a class="result__a" href="URL">Title</a>
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    # Pattern for snippets: <a class="result__snippet">Snippet</a>
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (url, title) in enumerate(links):
        if len(results) >= max_results:
            break
        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

        if title_clean and url.startswith("http"):
            results.append({
                "title": title_clean,
                "url": url,
                "snippet": snippet,
            })

    return results


def _parse_duckduckgo_lite(html: str, max_results: int) -> List[Dict[str, str]]:
    """Extract results from DuckDuckGo Lite HTML (fallback)."""
    results: List[Dict[str, str]] = []

    # Lite uses table rows with links and descriptions
    # Pattern: <a href="URL">Title</a> ... <td class="result-snippet">Snippet</td>
    row_pattern = re.compile(
        r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
        re.DOTALL,
    )

    links = row_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    seen_urls: set = set()
    for i, (url, title) in enumerate(links):
        if len(results) >= max_results:
            break
        # Skip non-result links
        if "duckduckgo.com" in url or url in seen_urls:
            continue
        seen_urls.add(url)

        title_clean = re.sub(r"<[^>]+>", "", title).strip()
        snippet = ""
        for j in range(i, min(i + 2, len(snippets))):
            s = re.sub(r"<[^>]+>", "", snippets[j]).strip()
            if s and s != title_clean:
                snippet = s
                break

        if title_clean and url.startswith("http"):
            results.append({
                "title": title_clean,
                "url": url,
                "snippet": snippet,
            })

    return results


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

    # ── Web Search / Fetch / Download ─────────────────────────────
    registry.register(
        name="web_search",
        description="Search the web via DuckDuckGo HTML (no API key required). Returns title, url, and snippet.",
        tags=["web", "search", "duckduckgo"],
        category="web",
        timeout_secs=30,
    )(tool_web_search)

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

    logger.info("Registered %d built-in tools", 36)
