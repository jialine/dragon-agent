
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
    file_paths: list[str] | None = None,
):
    """Send a message to a connected messaging platform. Hermes-aligned.

    Args:
        action: 'send' to send, 'list' to list available targets
        target: 'feishu' (default home chat), 'feishu:chat_id' for specific chat
        message: Message text to send
        file_path: Optional single file/image to attach
        file_paths: Optional list of file/image paths — batch send in one call
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
            # Normalize: merge file_path and file_paths
            all_files = []
            if file_paths:
                all_files.extend(file_paths)
            if file_path:
                all_files.append(file_path)
            # Deduplicate while preserving order
            seen = set()
            all_files = [f for f in all_files if not (f in seen or seen.add(f))]

            if not message and not all_files:
                return json.dumps({"error": "message or file_path(s) required"})

            token = await _get_token()
            if not token:
                return json.dumps({"error": "Feishu authentication failed"})

            chat_id = os.getenv("FEISHU_DEFAULT_CHAT_ID", "oc_683756dd47394fb46ef5693cd1187b4c")
            receive_id_type = "chat_id"
            if target and ":" in target:
                parts = target.split(":", 1)
                if len(parts) == 2:
                    chat_id = parts[1]
            if target and target.startswith("ou_"):
                chat_id = target
                receive_id_type = "open_id"

            async def _upload_and_send(fp: str, token: str) -> dict:
                """Upload a single file and send it as a message."""
                if not os.path.isfile(fp):
                    return {"error": f"File not found: {fp}"}
                ext = os.path.splitext(fp)[1].lower()
                is_image = ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
                upload_type = "image" if is_image else "file"

                _type_map = {
                    ".pdf": "pdf", ".doc": "doc", ".docx": "doc", ".xls": "xls",
                    ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt",
                    ".mp4": "mp4", ".mp3": "opus", ".opus": "opus",
                }
                file_type_str = _type_map.get(ext, "stream")
                file_name = os.path.basename(fp)

                upload_url = f"{FEISHU_API_BASE}/im/v1/{upload_type}s"
                headers = {"Authorization": f"Bearer {token}"}
                async with httpx.AsyncClient(timeout=60) as c:
                    with open(fp, "rb") as f:
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
                            return {"error": f"Upload failed: {data.get('msg')}", "file": fp}
                        file_key = data.get("data", {}).get(f"{upload_type}_key", "")

                    msg_content = {f"{upload_type}_key": file_key}
                    body2 = {
                        "receive_id": chat_id,
                        "msg_type": upload_type,
                        "content": json.dumps(msg_content),
                    }
                    r3 = await c.post(
                        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type={receive_id_type}",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json=body2,
                    )
                    data3 = r3.json()
                    if data3.get("code") == 0:
                        return {"status": "sent", "type": upload_type,
                                "message_id": data3.get("data", {}).get("message_id", ""),
                                "file": fp}
                    return {"error": data3.get("msg", "Unknown"), "file": fp}

            # Send text first if there is one
            results = []
            if message:
                body = {
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": message[:4096]}),
                }
                url = f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type={receive_id_type}"
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.post(url, headers=headers, json=body)
                    data = r.json()
                    if data.get("code") == 0:
                        results.append({"status": "sent", "type": "text",
                                        "message_id": data.get("data", {}).get("message_id", "")})
                    else:
                        results.append({"error": data.get("msg", "Unknown error"), "type": "text"})

            # Send each file
            for fp in all_files:
                results.append(await _upload_and_send(fp, token))

            if len(results) == 1:
                return json.dumps(results[0])
            return json.dumps({"status": "batch_done", "count": len(results), "results": results})

        else:
            return json.dumps({"error": f"Unknown action: {action}. Use 'send' or 'list'."})

    except Exception as e:
        return json.dumps({"error": str(e)})
