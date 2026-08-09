
"""
send_message tool — Hermes-aligned cross-platform messaging.
"""
import json, os, time, asyncio, logging
import httpx

logger = logging.getLogger("dragon.tool.send_message")

FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

_token_cache = {"token": "", "expires": 0}

async def _get_token():
    if _token_cache["token"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["token"]
    try:
        url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
        body = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=body)
            data = r.json()
            if data.get("code") == 0:
                _token_cache["token"] = data["tenant_access_token"]
                _token_cache["expires"] = time.time() + data.get("expire", 7200)
                return _token_cache["token"]
    except Exception as e:
        logger.error(f"Token error: {e}")
    return ""


async def tool_send_message(
    action: str = "send",
    target: str = "",
    message: str = "",
    file_path: str = "",
):
    """Send a message to a connected messaging platform. Hermes-aligned.

    Args:
        action: 'send' to send, 'list' to list available targets
        target: 'feishu' (default home chat), 'feishu:chat_id' for specific chat
        message: Message text to send
        file_path: Optional path to a file/image to send as attachment
    """
    try:
        if action == "list":
            # Return available platforms
            token = await _get_token()
            if not token:
                return json.dumps({"error": "Cannot get Feishu token", "platforms": []})

            # List recent chats
            url = f"{FEISHU_API_BASE}/im/v1/chats"
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(url, headers=headers, params={"page_size": 20})
                data = r.json()
                chats = []
                if data.get("code") == 0:
                    for item in data.get("data", {}).get("items", []):
                        chats.append({
                            "chat_id": item.get("chat_id", ""),
                            "name": item.get("name", "Unknown"),
                            "type": item.get("chat_type", "group"),
                        })

            return json.dumps({
                "platforms": {
                    "feishu": {
                        "type": "feishu",
                        "chats": chats,
                        "home_chat": "auto-detected",
                    }
                }
            }, ensure_ascii=False)

        elif action == "send":
            if not message and not file_path:
                return json.dumps({"error": "message or file_path required"})

            token = await _get_token()
            if not token:
                return json.dumps({"error": "Feishu authentication failed"})

            chat_id = "oc_683756dd47394fb46ef5693cd1187b4c"  # Default home chat
            receive_id_type = "chat_id"
            if target and ":" in target:
                parts = target.split(":", 1)
                if len(parts) == 2:
                    chat_id = parts[1]
            # If target looks like an open_id (starts with ou_), use open_id receiver type
            if target and target.startswith("ou_"):
                chat_id = target
                receive_id_type = "open_id"

            # Send text
            if message and not file_path:
                body = {
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": message[:4096]}),
                    "receive_id_type": receive_id_type,
                }
                url = f"{FEISHU_API_BASE}/im/v1/messages"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.post(url, headers=headers, json=body)
                    data = r.json()
                    if data.get("code") == 0:
                        return json.dumps({"status": "sent", "message_id": data.get("data", {}).get("message_id", "")})
                    return json.dumps({"error": data.get("msg", "Unknown error"), "code": data.get("code")})

            # Send file/image
            if file_path and os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                is_image = ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
                upload_type = "image" if is_image else "file"

                # Detect file_type for Feishu API
                _type_map = {
                    ".pdf": "pdf", ".doc": "doc", ".docx": "doc", ".xls": "xls",
                    ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt",
                    ".mp4": "mp4", ".mp3": "opus", ".opus": "opus",
                }
                file_type_str = _type_map.get(ext, "stream")
                file_name = os.path.basename(file_path)

                # Upload
                upload_url = f"{FEISHU_API_BASE}/im/v1/{upload_type}s"
                headers = {"Authorization": f"Bearer {token}"}
                async with httpx.AsyncClient(timeout=30) as c:
                    with open(file_path, "rb") as f:
                        if is_image:
                            files_data = {
                                "image_type": (None, "message"),
                                "image": (file_name, f),
                            }
                        else:
                            files_data = {
                                "file_type": (None, file_type_str),
                                "file_name": (None, file_name),
                                "file": (file_name, f),
                            }
                        r = await c.post(upload_url, headers=headers, files=files_data)
                        data = r.json()
                        if data.get("code") != 0:
                            return json.dumps({"error": f"Upload failed: {data.get('msg')}"})
                        file_key = data.get("data", {}).get(f"{upload_type}_key", "")

                    # Send
                    msg_content = {f"{upload_type}_key": file_key}
                    body2 = {
                        "receive_id": chat_id,
                        "msg_type": upload_type,
                        "content": json.dumps(msg_content),
                        "receive_id_type": receive_id_type,
                    }
                    r3 = await c.post(f"{FEISHU_API_BASE}/im/v1/messages",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                     json=body2)
                    data3 = r3.json()
                    if data3.get("code") == 0:
                        return json.dumps({"status": "sent", "type": upload_type, "message_id": data3.get("data", {}).get("message_id", "")})
                    return json.dumps({"error": data3.get("msg", "Unknown")})

            return json.dumps({"error": "No message content to send"})

        else:
            return json.dumps({"error": f"Unknown action: {action}. Use 'send' or 'list'."})

    except Exception as e:
        return json.dumps({"error": str(e)})
