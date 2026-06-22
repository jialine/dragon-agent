"""
Dragon Gateway — Mattermost Platform Adapter
===========================================

Handles Mattermost messages via the REST API v4.
Supports slash commands and outgoing webhooks.

Mattermost docs: https://api.mattermost.com/

Configuration::

    adapter = MattermostAdapter(
        server_url="https://mattermost.example.com",  # or set MATTERMOST_URL env
        bot_token="...",                                # or set MATTERMOST_TOKEN env
    )
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("dragon.gateway.mattermost")


class MattermostAdapter(PlatformAdapter):
    """Mattermost adapter using REST API v4.

    Handles slash commands (/command text) and outgoing webhook payloads.
    Verifies incoming requests via Authorization Bearer token.
    """

    def __init__(
        self,
        server_url: str = "",
        bot_token: str = "",
    ) -> None:
        super().__init__(platform_name="mattermost", webhook_path="/mattermost/webhook")

        self.server_url = (server_url or os.getenv("MATTERMOST_URL", "")).rstrip("/")
        self.bot_token = bot_token or os.getenv("MATTERMOST_TOKEN", "")

        logger.info(
            "Mattermost adapter ready (server=%s)",
            self.server_url or "none",
        )

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify the Authorization header contains the bot token."""
        if not self.bot_token:
            return True  # Allow if not configured

        auth = headers.get("authorization", headers.get("Authorization", ""))
        # Check for Bearer token
        if auth.startswith("Bearer "):
            return auth[7:] == self.bot_token
        # Also allow token as a query param or direct match
        return auth == self.bot_token

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse Mattermost slash command or outgoing webhook payload.

        Slash command format::

            {
                "token": "...",
                "team_id": "...",
                "channel_id": "...",
                "user_id": "...",
                "command": "/dragon",
                "text": "hello world",
                "response_url": "..."
            }

        Outgoing webhook format::

            {
                "token": "...",
                "team_id": "...",
                "channel_id": "...",
                "user_id": "...",
                "text": "hello world",
                "trigger_word": "!dragon"
            }
        """
        # Slash command
        command = body.get("command", "")
        text = body.get("text", "")

        # Outgoing webhook (text includes trigger word)
        trigger = body.get("trigger_word", "")
        if trigger and text.startswith(trigger):
            text = text[len(trigger):].strip()

        # Fallback: plain webhook with content/text field
        if not text:
            text = body.get("content", body.get("message", ""))

        if not text:
            return None

        return PlatformMessage(
            platform="mattermost",
            chat_id=body.get("channel_id", body.get("channel_id", "")),
            user_id=body.get("user_id", body.get("user_name", "")),
            content=text,
            message_id=body.get("post_id", ""),
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via Mattermost /api/v4/posts."""
        if not self.bot_token or not self.server_url:
            logger.error("Mattermost credentials not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        url = f"{self.server_url}/api/v4/posts"

        payload = {
            "channel_id": chat_id,
            "message": reply.content,
        }
        if reply.reply_to_message_id:
            payload["root_id"] = reply.reply_to_message_id

        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    logger.debug("Sent Mattermost message to %s", chat_id)
                    return True
                logger.error("Mattermost API error: %s %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Failed to send Mattermost message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file to Mattermost. Returns the file ID."""
        import os as _os

        if not _os.path.exists(file_path) or not self.bot_token or not self.server_url:
            return None

        url = f"{self.server_url}/api/v4/files"
        headers = {"Authorization": f"Bearer {self.bot_token}"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    files = {
                        "files": (_os.path.basename(file_path), f, "application/octet-stream"),
                    }
                    data = {"channel_id": ""}
                    resp = await client.post(url, headers=headers, files=files, data=data)
                if resp.status_code in (200, 201):
                    result = resp.json()
                    file_infos = result.get("file_infos", [])
                    if file_infos:
                        return file_infos[0].get("id", "")
                logger.error("Mattermost upload error: %s", resp.text)
        except Exception as e:
            logger.exception("Mattermost upload failed: %s", e)

        return None
