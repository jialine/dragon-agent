
"""
Feishu Drive comment tools — Hermes-aligned.
"""
import json, time, logging, httpx, os

logger = logging.getLogger("dragon.tool.feishu_comments")

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

_token = {"token": "", "expires": 0}

async def _get_token():
    if _token["token"] and time.time() < _token["expires"] - 60:
        return _token["token"]
    try:
        url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET})
            data = r.json()
            if data.get("code") == 0:
                _token["token"] = data["tenant_access_token"]
                _token["expires"] = time.time() + data.get("expire", 7200)
                return _token["token"]
    except Exception as e:
        logger.error(f"Token error: {e}")
    return ""


async def tool_feishu_drive_add_comment(file_token: str, content: str, file_type: str = "docx"):
    """Add a whole-document comment on a Feishu document. Hermes-aligned."""
    token = await _get_token()
    if not token:
        return json.dumps({"error": "Feishu auth failed"})
    try:
        url = f"{FEISHU_API_BASE}/drive/v1/files/{file_token}/comments"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"comment_type": "whole", "content": json.dumps({"elements": [{"type": "text_run", "text_run": {"content": content[:5000]}}]})}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, headers=headers, json=body)
            data = r.json()
            if data.get("code") == 0:
                return json.dumps({"status": "ok", "comment_id": data.get("data", {}).get("comment_id", "")})
            return json.dumps({"error": data.get("msg", "Unknown")})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def tool_feishu_drive_list_comments(file_token: str, file_type: str = "docx", page_size: int = 50, is_whole: bool = False):
    """List comments on a Feishu document. Hermes-aligned."""
    token = await _get_token()
    if not token:
        return json.dumps({"error": "Feishu auth failed"})
    try:
        url = f"{FEISHU_API_BASE}/drive/v1/files/{file_token}/comments"
        params = {"page_size": min(page_size, 100)}
        if is_whole:
            params["is_whole"] = "true"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers=headers, params=params)
            data = r.json()
            if data.get("code") == 0:
                comments = []
                for item in data.get("data", {}).get("items", []):
                    comments.append({
                        "comment_id": item.get("comment_id", ""),
                        "content": str(item.get("content", ""))[:200],
                        "created_at": item.get("create_time", ""),
                    })
                return json.dumps({"comments": comments, "total": len(comments)})
            return json.dumps({"error": data.get("msg", "Unknown")})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def tool_feishu_drive_reply_comment(file_token: str, comment_id: str, content: str, file_type: str = "docx"):
    """Reply to a comment on a Feishu document. Hermes-aligned."""
    token = await _get_token()
    if not token:
        return json.dumps({"error": "Feishu auth failed"})
    try:
        url = f"{FEISHU_API_BASE}/drive/v1/files/{file_token}/comments/{comment_id}/replies"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"content": json.dumps({"elements": [{"type": "text_run", "text_run": {"content": content[:5000]}}]})}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, headers=headers, json=body)
            data = r.json()
            if data.get("code") == 0:
                return json.dumps({"status": "ok", "reply_id": data.get("data", {}).get("reply_id", "")})
            return json.dumps({"error": data.get("msg", "Unknown")})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def tool_feishu_drive_list_comment_replies(file_token: str, comment_id: str, file_type: str = "docx", page_size: int = 50):
    """List replies in a comment thread. Hermes-aligned."""
    token = await _get_token()
    if not token:
        return json.dumps({"error": "Feishu auth failed"})
    try:
        url = f"{FEISHU_API_BASE}/drive/v1/files/{file_token}/comments/{comment_id}/replies"
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers=headers, params={"page_size": min(page_size, 100)})
            data = r.json()
            if data.get("code") == 0:
                replies = []
                for item in data.get("data", {}).get("items", []):
                    replies.append({
                        "reply_id": item.get("reply_id", ""),
                        "content": str(item.get("content", ""))[:200],
                        "created_at": item.get("create_time", ""),
                    })
                return json.dumps({"replies": replies, "total": len(replies)})
            return json.dumps({"error": data.get("msg", "Unknown")})
    except Exception as e:
        return json.dumps({"error": str(e)})
