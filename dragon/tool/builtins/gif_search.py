"""
Dragon Agent — GIF Search Tools (Tenor)
========================================

Search for GIFs and discover trending GIFs via the Tenor API.
Free API key available at https://tenor.com/gifapi.

Tools:
    - gif_search: Search for GIFs by keyword
    - gif_trending: Get trending GIFs

API:
    - Tenor v2: https://tenor.googleapis.com/v2

Environment Variables:
    - TENOR_API_KEY (required)

Dependencies:
    - httpx (already in Dragon Agent deps)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("dragon.tool.builtins.gif_search")

# ── Constants ────────────────────────────────────────────────────────

TENOR_SEARCH_URL = "https://tenor.googleapis.com/v2/search"
TENOR_FEATURED_URL = "https://tenor.googleapis.com/v2/featured"

DEFAULT_LIMIT = 5
MAX_LIMIT = 50


# ── Helpers ──────────────────────────────────────────────────────────


def _get_api_key() -> Optional[str]:
    """Get the Tenor API key from environment."""
    return os.environ.get("TENOR_API_KEY", "").strip() or None


def _normalize_gif(gif: dict) -> dict:
    """Extract the essential fields from a Tenor API GIF object."""
    media_formats = gif.get("media_formats", {})

    # Prefer tinygif for preview, gif for full URL
    preview_url = ""
    full_url = ""

    for fmt_name in ("tinygif", "nanogif", "gif"):
        fmt = media_formats.get(fmt_name, {})
        if fmt.get("url"):
            if not preview_url:
                preview_url = fmt["url"]
            if fmt_name == "gif":
                full_url = fmt["url"]
    if not full_url:
        full_url = preview_url

    return {
        "id": gif.get("id", ""),
        "title": gif.get("title", "") or gif.get("content_description", ""),
        "description": gif.get("content_description", ""),
        "url": full_url,
        "preview_url": preview_url,
        "media_formats": {
            k: {"url": v.get("url", ""), "size": f"{v.get('dims', [0, 0])[0]}x{v.get('dims', [0, 0])[1]}",
                "duration_secs": round(v.get("duration", 0), 1)}
            for k, v in media_formats.items()
            if v.get("url")
        },
    }


# ── Tool Implementations ─────────────────────────────────────────────


async def tool_gif_search(query: str, limit: int = 5) -> str:
    """Search for GIFs via the Tenor API.

    Requires the TENOR_API_KEY environment variable.
    Get a free API key at https://tenor.com/gifapi.

    Args:
        query: Search query string (e.g., "cat dancing", "happy birthday").
        limit: Maximum number of results (1-50). Default: 5.

    Returns:
        JSON with query, results list, and total count. Each result
        includes id, title, description, url (full GIF), preview_url
        (tiny thumbnail), and media_formats. Returns error field if
        the API key is missing or the API fails.
    """
    api_key = _get_api_key()
    if not api_key:
        return json.dumps({
            "error": (
                "Tenor API key not configured. "
                "Set the TENOR_API_KEY environment variable. "
                "Get a free key at https://tenor.com/gifapi"
            ),
        })

    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty"})

    query = query.strip()
    limit = max(1, min(MAX_LIMIT, limit))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(
                TENOR_SEARCH_URL,
                params={
                    "q": query,
                    "key": api_key,
                    "limit": limit,
                    "media_filter": "gif,tinygif,nanogif",
                },
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Tenor API error (HTTP {resp.status_code})",
                    "detail": resp.text[:500],
                })

            data = resp.json()

    except httpx.TimeoutException:
        return json.dumps({"error": "Tenor API request timed out"})
    except Exception as e:
        logger.warning("Tenor GIF search failed: %s", e)
        return json.dumps({"error": f"Tenor API request failed: {type(e).__name__}: {str(e)}"})

    results = [_normalize_gif(gif) for gif in data.get("results", [])]

    return json.dumps({
        "query": query,
        "results": results,
        "total": len(results),
        "next": data.get("next", ""),
    })


async def tool_gif_trending(limit: int = 5) -> str:
    """Get trending GIFs from Tenor.

    Requires the TENOR_API_KEY environment variable.
    Get a free API key at https://tenor.com/gifapi.

    Args:
        limit: Maximum number of results (1-50). Default: 5.

    Returns:
        JSON with results list and total count. Each result includes
        id, title, description, url (full GIF), preview_url (tiny
        thumbnail), and media_formats. Returns error field if the API
        key is missing or the API fails.
    """
    api_key = _get_api_key()
    if not api_key:
        return json.dumps({
            "error": (
                "Tenor API key not configured. "
                "Set the TENOR_API_KEY environment variable. "
                "Get a free key at https://tenor.com/gifapi"
            ),
        })

    limit = max(1, min(MAX_LIMIT, limit))

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(
                TENOR_FEATURED_URL,
                params={
                    "key": api_key,
                    "limit": limit,
                    "media_filter": "gif,tinygif,nanogif",
                },
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Tenor API error (HTTP {resp.status_code})",
                    "detail": resp.text[:500],
                })

            data = resp.json()

    except httpx.TimeoutException:
        return json.dumps({"error": "Tenor API request timed out"})
    except Exception as e:
        logger.warning("Tenor trending GIFs failed: %s", e)
        return json.dumps({"error": f"Tenor API request failed: {type(e).__name__}: {str(e)}"})

    results = [_normalize_gif(gif) for gif in data.get("results", [])]

    return json.dumps({
        "results": results,
        "total": len(results),
        "next": data.get("next", ""),
    })
