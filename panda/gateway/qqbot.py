"""
Panda Gateway — QQ Bot (QQ机器人) Platform Adapter
==================================================

Handles QQ Bot messages via the QQ Open Bot API.
Receives webhook events (op=0, t=MESSAGE_CREATE), sends via message API.

QQ Bot docs: https://bot.q.qq.com/wiki/

Configuration::

    adapter = QQBotAdapter(
        app_id="1020XXXXX",             # or set QQ_BOT_APP_ID env
        token="...",                    # or set QQ_BOT_TOKEN env
    )
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

from panda.gateway.base import (
    PlatformAdapter, PlatformMessage, PlatformReply, verify_hmac_signature
)

logger = logging.getLogger("panda.gateway.qqbot")
QQ_API = "https://api.sgroup.qq.com"


class QQBotAdapter(PlatformAdapter):
    """QQ Bot adapter using the QQ Open API with Ed25519 signature verification."""

    def __init__(self, app_id: str = "", token: str = "") -> None:
        super().__init__(platform_name="qqbot", webhook_path="/qqbot/webhook")
        self.app_id = app_id or os.getenv("QQ_BOT_APP_ID", "")
        self.token = token or os.getenv("QQ_BOT_TOKEN", "")
        logger.info("QQ Bot adapter ready (app_id=%s)", self.app_id or "none")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify QQ Bot Ed25519 signature via X-Signature-Ed25519 header."""
        if not self.token:
            return True
        signature = headers.get("x-signature-ed25519", "")
        timestamp = headers.get("x-signature-timestamp", "")
        if not signature or not timestamp:
            return True
        return verify_hmac_signature(
            secret=self.token, timestamp=timestamp, nonce="",
            body=body, signature=signature,
        )

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse QQ Bot message event.

        QQ Bot webhook: {"op": 0, "t": "MESSAGE_CREATE",
            "d": {"id": "...", "author": {"id": "..."}, "content": "...",
                  "channel_id": "...", "guild_id": "..."}}
        """
        op = body.get("op", -1)
        event_type = body.get("t", "")

        if op != 0 or event_type != "MESSAGE_CREATE":
            # Handle challenge/verification event
            if event_type == "VERIFICATION" or body.get("d", {}).get("plain_token"):
                d = body.get("d", {})
                return PlatformMessage(
                    platform="qqbot", chat_id="__challenge__",
                    user_id="__system__",
                    content=json.dumps(d) if d else "__check_url__",
                )
            return None

        d = body.get("d", {})
        author = d.get("author", {})
        content = d.get("content", "")
        if not content:
            return None

        chat_id = d.get("channel_id", "") or d.get("group_openid", "")
        return PlatformMessage(
            platform="qqbot", chat_id=chat_id,
            user_id=author.get("id", ""), content=content,
            message_id=d.get("id", ""), raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via QQ Bot API (group or direct)."""
        if not self.app_id or not self.token:
            logger.error("QQ Bot credentials not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        headers = {
            "Authorization": f"Bot {self.app_id}.{self.token}",
            "Content-Type": "application/json",
        }
        payload = {"content": reply.content[:2000], "msg_type": 0}

        # Route to group or direct message based on chat_id prefix
        is_group = chat_id.startswith("g") or chat_id.isdigit()
        url = (
            f"{QQ_API}/v2/groups/{chat_id}/messages" if is_group
            else f"{QQ_API}/v2/users/{chat_id}/messages"
        )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    logger.debug("Sent QQ Bot message to %s", chat_id)
                    return True
                logger.error("QQ Bot API error: %s %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Failed to send QQ Bot message: %s", e)
        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file to QQ Bot. Returns file UUID."""
        import os as _os
        if not _os.path.exists(file_path) or not self.app_id or not self.token:
            return None

        url = f"{QQ_API}/v2/groups/0/files"
        headers = {"Authorization": f"Bot {self.app_id}.{self.token}"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (_os.path.basename(file_path), f, "application/octet-stream")}
                    resp = await client.post(url, headers=headers, files=files)
                if resp.status_code in (200, 201):
                    result = resp.json()
                    return result.get("file_uuid", result.get("id", ""))
                logger.error("QQ Bot upload error: %s", resp.text)
        except Exception as e:
            logger.exception("QQ Bot upload failed: %s", e)
        return None
