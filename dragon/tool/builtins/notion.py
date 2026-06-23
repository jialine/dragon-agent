"""
Dragon Agent — Notion API Integration
=====================================

Tools for searching, reading, and creating Notion pages
via the Notion REST API.

Auth: Requires NOTION_API_KEY in environment.
API Version: 2022-06-28

Tools:
    - notion_search: Search Notion pages by query
    - notion_read_page: Read a Notion page as text
    - notion_create_page: Create a new Notion page
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("dragon.tool.notion")

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _notion_headers() -> dict:
    """Build headers for Notion API requests."""
    api_key = os.getenv("NOTION_API_KEY", "")
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _check_auth() -> Optional[str]:
    """Verify NOTION_API_KEY is set. Returns error JSON string or None."""
    if not os.getenv("NOTION_API_KEY"):
        return json.dumps({"error": "NOTION_API_KEY environment variable is not set"})
    return None


# ────────────────────────────────────────────────────────────────────
# Tool: Search Notion Pages
# ────────────────────────────────────────────────────────────────────


async def tool_notion_search(query: str, limit: int = 5) -> str:
    """Search Notion pages by title or content.

    Uses the Notion search endpoint to find pages matching the query.

    Args:
        query: Search query string.
        limit: Maximum results to return (default: 5).

    Returns:
        JSON with results list containing id, title, url, and type.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    if not query or not query.strip():
        return json.dumps({"error": "query is required"})

    query = query.strip()
    limit = max(1, min(limit, 100))

    url = f"{NOTION_API_BASE}/search"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers=_notion_headers(),
                json={"query": query, "page_size": limit},
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Notion API HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                })

            data = resp.json()
            results_raw = data.get("results", [])

            results = []
            for item in results_raw:
                obj_type = item.get("object", "unknown")
                item_id = item.get("id", "")

                # Extract title based on object type
                title = ""
                if obj_type == "page":
                    props = item.get("properties", {})
                    # Notion pages often have a "title" or "Name" property
                    for prop_name, prop_val in props.items():
                        if prop_val.get("type") == "title":
                            title_parts = prop_val.get("title", [])
                            title = "".join(
                                t.get("plain_text", "") for t in title_parts
                            )
                            break
                elif obj_type == "database":
                    db_title = item.get("title", [])
                    title = "".join(
                        t.get("plain_text", "") for t in db_title
                    )

                results.append({
                    "id": item_id,
                    "title": title or "(untitled)",
                    "url": item.get("url", f"https://notion.so/{item_id}"),
                    "type": obj_type,
                })

            return json.dumps({
                "query": query,
                "results": results,
                "total": len(results),
                "has_more": data.get("has_more", False),
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[Notion] Search failed: %s", exc)
        return json.dumps({
            "error": f"Search failed: {type(exc).__name__}: {str(exc)}",
        })


# ────────────────────────────────────────────────────────────────────
# Tool: Read a Notion Page
# ────────────────────────────────────────────────────────────────────


async def tool_notion_read_page(page_id: str) -> str:
    """Read a Notion page as plain text.

    Fetches block children of the page and extracts text from
    paragraph, heading, bulleted_list_item, numbered_list_item,
    to_do, and quote blocks.

    Args:
        page_id: The Notion page ID (UUID with or without hyphens).

    Returns:
        JSON with page_id, title, and content (plain text).
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    if not page_id or not page_id.strip():
        return json.dumps({"error": "page_id is required"})

    page_id = page_id.strip().replace("-", "")

    # Normalize to UUID format for API
    if len(page_id) == 32:
        page_id = f"{page_id[:8]}-{page_id[8:12]}-{page_id[12:16]}-{page_id[16:20]}-{page_id[20:]}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Get page metadata (title)
            page_url = f"{NOTION_API_BASE}/pages/{page_id}"
            page_resp = await client.get(page_url, headers=_notion_headers())

            title = ""
            if page_resp.status_code == 200:
                page_data = page_resp.json()
                props = page_data.get("properties", {})
                for prop_name, prop_val in props.items():
                    if prop_val.get("type") == "title":
                        title_parts = prop_val.get("title", [])
                        title = "".join(
                            t.get("plain_text", "") for t in title_parts
                        )
                        break
            else:
                logger.warning(
                    "[Notion] Page metadata HTTP %d: %s",
                    page_resp.status_code,
                    page_resp.text[:200],
                )

            # Step 2: Get block children
            blocks_url = f"{NOTION_API_BASE}/blocks/{page_id}/children"
            blocks_resp = await client.get(blocks_url, headers=_notion_headers())

            if blocks_resp.status_code != 200:
                return json.dumps({
                    "error": f"Failed to get page blocks (HTTP {blocks_resp.status_code})",
                    "page_id": page_id,
                })

            blocks_data = blocks_resp.json()
            blocks = blocks_data.get("results", [])

            # Extract text from each block
            lines = []
            for block in blocks:
                block_type = block.get("type", "")
                block_content = block.get(block_type, {})

                if block_type == "paragraph":
                    text_items = block_content.get("rich_text", [])
                    text = "".join(
                        t.get("plain_text", "") for t in text_items
                    )
                    if text.strip():
                        lines.append(text)
                    else:
                        lines.append("")  # blank line for paragraph breaks

                elif block_type in ("heading_1", "heading_2", "heading_3"):
                    text_items = block_content.get("rich_text", [])
                    text = "".join(
                        t.get("plain_text", "") for t in text_items
                    )
                    if text.strip():
                        prefix = "#" if block_type == "heading_1" else "##" if block_type == "heading_2" else "###"
                        lines.append(f"{prefix} {text}")

                elif block_type in ("bulleted_list_item", "numbered_list_item"):
                    text_items = block_content.get("rich_text", [])
                    text = "".join(
                        t.get("plain_text", "") for t in text_items
                    )
                    prefix = "-" if block_type == "bulleted_list_item" else "1."
                    lines.append(f"{prefix} {text}")

                elif block_type == "to_do":
                    text_items = block_content.get("rich_text", [])
                    text = "".join(
                        t.get("plain_text", "") for t in text_items
                    )
                    checked = block_content.get("checked", False)
                    marker = "[x]" if checked else "[ ]"
                    lines.append(f"- {marker} {text}")

                elif block_type == "quote":
                    text_items = block_content.get("rich_text", [])
                    text = "".join(
                        t.get("plain_text", "") for t in text_items
                    )
                    lines.append(f"> {text}")

                elif block_type == "code":
                    text_items = block_content.get("rich_text", [])
                    text = "".join(
                        t.get("plain_text", "") for t in text_items
                    )
                    language = block_content.get("language", "")
                    lines.append(f"```{language}")
                    lines.append(text)
                    lines.append("```")

                elif block_type == "divider":
                    lines.append("---")

            content = "\n".join(lines)

            return json.dumps({
                "page_id": page_id,
                "title": title or "(untitled)",
                "content": content,
                "block_count": len(blocks),
                "has_more": blocks_data.get("has_more", False),
            })

    except httpx.TimeoutException:
        return json.dumps({
            "error": "Request timed out",
            "page_id": page_id,
        })
    except Exception as exc:
        logger.exception("[Notion] Failed to read page %s: %s", page_id, exc)
        return json.dumps({
            "error": f"Failed to read page: {type(exc).__name__}: {str(exc)}",
            "page_id": page_id,
        })


# ────────────────────────────────────────────────────────────────────
# Tool: Create a Notion Page
# ────────────────────────────────────────────────────────────────────


async def tool_notion_create_page(
    title: str,
    content: str = "",
    parent_id: str = "",
) -> str:
    """Create a new Notion page.

    Creates a page with a title and optional text content. If parent_id
    is provided, the page is created as a child of that page or database.

    Args:
        title: Title for the new page.
        content: Optional text content for the page body.
        parent_id: Optional parent page/database ID. If empty, creates
                   a standalone page in the integration's workspace.

    Returns:
        JSON with page_id, url, and title on success.
    """
    auth_err = _check_auth()
    if auth_err:
        return auth_err

    if not title or not title.strip():
        return json.dumps({"error": "title is required"})

    title = title.strip()
    content = (content or "").strip()

    # Build page properties
    parent = {}
    if parent_id and parent_id.strip():
        parent_id = parent_id.strip().replace("-", "")
        if len(parent_id) == 32:
            parent_id = f"{parent_id[:8]}-{parent_id[8:12]}-{parent_id[12:16]}-{parent_id[16:20]}-{parent_id[20:]}"
        parent["page_id"] = parent_id
    else:
        parent["type"] = "workspace"

    page_body = {
        "parent": parent,
        "properties": {
            "title": {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": title},
                    }
                ]
            }
        },
    }

    # If content is provided, add it as children blocks
    if content:
        children = []
        for paragraph in content.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph:
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {"content": paragraph},
                            }
                        ]
                    },
                })
        if children:
            page_body["children"] = children

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{NOTION_API_BASE}/pages",
                headers=_notion_headers(),
                json=page_body,
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Notion API HTTP {resp.status_code}",
                    "detail": resp.text[:500],
                })

            data = resp.json()
            page_id = data.get("id", "")
            page_url = data.get("url", f"https://notion.so/{page_id}")

            return json.dumps({
                "page_id": page_id,
                "url": page_url,
                "title": title,
                "has_content": bool(content),
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[Notion] Failed to create page: %s", exc)
        return json.dumps({
            "error": f"Failed to create page: {type(exc).__name__}: {str(exc)}",
        })
