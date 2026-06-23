"""
Dragon Agent — Feishu Document Tools
=====================================

Tools for reading, listing, and creating Feishu/Lark documents
via the Feishu Open API (docx + drive).

Auth: Reuses FEISHU_APP_ID / FEISHU_APP_SECRET from environment.
Token caching with ~2h TTL.

Tools:
    - feishu_read_doc: Read a Feishu document as plain text
    - feishu_list_docs: List recent Feishu documents
    - feishu_create_doc: Create a new Feishu document
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger("dragon.tool.feishu_docs")

# ── Constants ────────────────────────────────────────────────────────

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

# Token cache (module-level, shared across all tool calls)
_token_cache: dict = {"token": "", "expires_at": 0.0}


# ── Auth Helpers ─────────────────────────────────────────────────────


async def _get_tenant_access_token() -> str:
    """Get or refresh the tenant access token (cached, ~2h TTL)."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")

    if not app_id or not app_secret:
        logger.error("[FeishuDocs] FEISHU_APP_ID or FEISHU_APP_SECRET not set")
        return ""

    url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
    body = {"app_id": app_id, "app_secret": app_secret}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    _token_cache["token"] = data["tenant_access_token"]
                    _token_cache["expires_at"] = (
                        time.time() + data.get("expire", 7200) - 200
                    )
                    return _token_cache["token"]
                logger.error(
                    "[FeishuDocs] Token API error: code=%s msg=%s",
                    data.get("code"), data.get("msg"),
                )
            else:
                logger.error(
                    "[FeishuDocs] Token HTTP %d: %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.exception("[FeishuDocs] Failed to get tenant access token: %s", exc)

    return ""


def _auth_headers(token: str) -> dict:
    """Build Authorization headers for Feishu API requests."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }


# ────────────────────────────────────────────────────────────────────
# Tool: Read a Feishu Document
# ────────────────────────────────────────────────────────────────────


async def tool_feishu_read_doc(doc_token: str) -> str:
    """Read a Feishu/Lark document as plain text.

    Uses the Feishu Docs API raw_content endpoint to retrieve
    the full document as plain text.

    Args:
        doc_token: The document token (ID) from the Feishu URL.
                   Example: from https://xxx.feishu.cn/docx/ABCD1234,
                   the token is 'ABCD1234'.

    Returns:
        JSON string: {title, content, doc_token} on success,
                     {error: ...} on failure.
    """
    if not doc_token or not doc_token.strip():
        return json.dumps({"error": "doc_token is required"})

    doc_token = doc_token.strip()

    token = await _get_tenant_access_token()
    if not token:
        return json.dumps({"error": "Failed to get Feishu access token"})

    # Step 1: Get document metadata (title)
    meta_url = f"{FEISHU_API_BASE}/docx/v1/documents/{doc_token}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Fetch metadata
            meta_resp = await client.get(meta_url, headers=_auth_headers(token))
            if meta_resp.status_code != 200:
                return json.dumps({
                    "error": f"Failed to get document metadata (HTTP {meta_resp.status_code})",
                    "doc_token": doc_token,
                })

            meta_data = meta_resp.json()
            if meta_data.get("code") != 0:
                return json.dumps({
                    "error": f"API error: {meta_data.get('msg', 'unknown')}",
                    "code": meta_data.get("code"),
                    "doc_token": doc_token,
                })

            doc_info = meta_data.get("data", {}).get("document", {})
            title = doc_info.get("title", "")

            # Step 2: Get raw content
            content_url = (
                f"{FEISHU_API_BASE}/docx/v1/documents/{doc_token}/raw_content"
            )
            content_resp = await client.get(
                content_url, headers=_auth_headers(token),
            )
            if content_resp.status_code != 200:
                return json.dumps({
                    "error": f"Failed to get document content (HTTP {content_resp.status_code})",
                    "title": title,
                    "doc_token": doc_token,
                })

            content_data = content_resp.json()
            if content_data.get("code") != 0:
                return json.dumps({
                    "error": f"Content API error: {content_data.get('msg', 'unknown')}",
                    "code": content_data.get("code"),
                    "title": title,
                    "doc_token": doc_token,
                })

            raw_content = content_data.get("data", {}).get("content", "")

            return json.dumps({
                "title": title,
                "content": raw_content,
                "doc_token": doc_token,
            })

    except httpx.TimeoutException:
        return json.dumps({
            "error": "Request timed out",
            "doc_token": doc_token,
        })
    except Exception as exc:
        logger.exception("[FeishuDocs] Failed to read document %s: %s", doc_token, exc)
        return json.dumps({
            "error": f"Failed to read document: {type(exc).__name__}: {str(exc)}",
            "doc_token": doc_token,
        })


# ────────────────────────────────────────────────────────────────────
# Tool: List Recent Feishu Documents
# ────────────────────────────────────────────────────────────────────


async def tool_feishu_list_docs(limit: int = 10) -> str:
    """List recent Feishu documents.

    Uses the Feishu Drive API to list docx files, sorted by
    most recently modified.

    Args:
        limit: Maximum number of documents to return (default: 10).

    Returns:
        JSON string: {files: [{token, name, url, created_time}], total: N}
                     on success, or {error: ...} on failure.
    """
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    token = await _get_tenant_access_token()
    if not token:
        return json.dumps({"error": "Failed to get Feishu access token"})

    url = (
        f"{FEISHU_API_BASE}/drive/v1/files"
        f"?type=docx&page_size={limit}&sort_type=EditedTime&direction=DESC"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_auth_headers(token))
            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Drive API HTTP {resp.status_code}",
                    "detail": resp.text[:300],
                })

            data = resp.json()
            if data.get("code") != 0:
                return json.dumps({
                    "error": f"Drive API error: {data.get('msg', 'unknown')}",
                    "code": data.get("code"),
                })

            files_raw = data.get("data", {}).get("files", [])
            has_more = data.get("data", {}).get("has_more", False)
            page_token = data.get("data", {}).get("page_token", "")

            files = []
            for f in files_raw:
                token_val = f.get("token", "")
                name = f.get("name", "")
                files.append({
                    "token": token_val,
                    "name": name,
                    "url": f"https://{os.getenv('FEISHU_DOMAIN', 'bytedance')}.feishu.cn/docx/{token_val}",
                    "created_time": f.get("created_time", ""),
                    "modified_time": f.get("modified_time", ""),
                })

            return json.dumps({
                "files": files,
                "total": len(files),
                "has_more": has_more,
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[FeishuDocs] Failed to list documents: %s", exc)
        return json.dumps({
            "error": f"Failed to list documents: {type(exc).__name__}: {str(exc)}",
        })


# ────────────────────────────────────────────────────────────────────
# Tool: Create a Feishu Document
# ────────────────────────────────────────────────────────────────────


async def tool_feishu_create_doc(title: str, content: str = "") -> str:
    """Create a new Feishu document.

    Creates a blank document (or with initial text content) via the
    Feishu Docx API and returns the document token and URL.

    Args:
        title: Title for the new document.
        content: Optional initial text content to add to the document body.

    Returns:
        JSON string: {doc_token, url, title, revision_id} on success,
                     or {error: ...} on failure.
    """
    if not title or not title.strip():
        return json.dumps({"error": "title is required"})

    title = title.strip()
    content = (content or "").strip()

    token = await _get_tenant_access_token()
    if not token:
        return json.dumps({"error": "Failed to get Feishu access token"})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Create the document
            create_url = f"{FEISHU_API_BASE}/docx/v1/documents"
            create_body = {"title": title}

            create_resp = await client.post(
                create_url, headers=_auth_headers(token), json=create_body,
            )

            if create_resp.status_code != 200:
                return json.dumps({
                    "error": f"Create document HTTP {create_resp.status_code}",
                    "detail": create_resp.text[:300],
                })

            create_data = create_resp.json()
            if create_data.get("code") != 0:
                return json.dumps({
                    "error": f"Create document API error: {create_data.get('msg', 'unknown')}",
                    "code": create_data.get("code"),
                })

            doc_data = create_data.get("data", {}).get("document", {})
            doc_token = doc_data.get("document_id", "")
            doc_url = doc_data.get("url", "")
            revision_id = doc_data.get("revision_id", 0)

            # Step 2: If content is provided, append it as a text block
            if content and doc_token:
                blocks_url = (
                    f"{FEISHU_API_BASE}/docx/v1/documents/{doc_token}"
                    f"/blocks/{doc_token}/children"
                )

                # Build a text block payload (block_type 2 = text)
                text_block = {
                    "block_type": 2,
                    "text": {
                        "elements": [
                            {"text_run": {"content": content}}
                        ],
                        "style": {},
                    },
                }

                blocks_body = {"children": [text_block]}

                blocks_resp = await client.post(
                    blocks_url, headers=_auth_headers(token), json=blocks_body,
                )

                if blocks_resp.status_code == 200:
                    blocks_data = blocks_resp.json()
                    if blocks_data.get("code") == 0:
                        # Update revision from block response
                        block_children = blocks_data.get("data", {}).get("children", [])
                        if block_children:
                            block_revision = blocks_data.get("data", {}).get(
                                "document_revision_id", revision_id,
                            )
                            revision_id = block_revision
                    else:
                        logger.warning(
                            "[FeishuDocs] Content append returned code %s: %s",
                            blocks_data.get("code"), blocks_data.get("msg"),
                        )
                else:
                    logger.warning(
                        "[FeishuDocs] Content append HTTP %d: %s",
                        blocks_resp.status_code, blocks_resp.text[:200],
                    )

            return json.dumps({
                "doc_token": doc_token,
                "url": doc_url,
                "title": title,
                "revision_id": revision_id,
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Request timed out"})
    except Exception as exc:
        logger.exception("[FeishuDocs] Failed to create document: %s", exc)
        return json.dumps({
            "error": f"Failed to create document: {type(exc).__name__}: {str(exc)}",
        })
