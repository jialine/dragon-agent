"""
Panda Gateway — DingTalk (钉钉) Platform Adapter
=================================================

Handles DingTalk bot webhooks for enterprise messaging.

DingTalk docs: https://open.dingtalk.com/document/

Configuration::

    adapter = DingTalkAdapter(
        app_key="ding...",             # or set DINGTALK_APP_KEY env
        app_secret="...",              # or set DINGTALK_APP_SECRET env
    )
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

import httpx

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.dingtalk")

DINGTALK_API = "https://oapi.dingtalk.com"


class DingTalkAdapter(PlatformAdapter):
    """DingTalk adapter with HMAC signature verification and access token management."""

    def __init__(
        self,
        app_key: str = "",
        app_secret: str = "",
    ) -> None:
        super().__init__(platform_name="dingtalk", webhook_path="/dingtalk/webhook")

        self.app_key = app_key or os.getenv("DINGTALK_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("DINGTALK_APP_SECRET", "")

        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        logger.info("DingTalk adapter ready (app_key=%s...)", self.app_key[:8] if self.app_key else "none")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify DingTalk HMAC signature from headers.

        DingTalk sends X-AK-DingTalk-Signature and X-AK-DingTalk-Timestamp.
        """
        signature = headers.get("x-ak-dingtalk-signature", "")
        timestamp = headers.get("x-ak-dingtalk-timestamp", "")

        if not self.app_secret or not signature or not timestamp:
            return True  # Allow if not configured

        # Compute HMAC-SHA256 and base64 encode
        string_to_sign = f"{timestamp}\n{self.app_secret}"
        computed = hmac.new(
            self.app_secret.encode(), string_to_sign.encode(), hashlib.sha256
        ).digest()
        import base64
        expected = base64.b64encode(computed).decode()

        return hmac.compare_digest(expected, signature)

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse DingTalk message callback.

        DingTalk sends registration check first, then message events::

            {
                "msgtype": "text",
                "text": {"content": "hello"},
                "senderNick": "user",
                "senderId": "$:...",
                "sessionWebhook": "...",
                "conversationId": "cid..."
            }
        """
        # Bot registration check (initial verification)
        if body.get("msgtype") == "check_url":
            return PlatformMessage(
                platform="dingtalk",
                chat_id="__challenge__",
                user_id="__system__",
                content="__check_url__",
            )

        msgtype = body.get("msgtype", "")
        if msgtype != "text":
            return None

        text_content = body.get("text", {}).get("content", "")
        if not text_content:
            return None

        return PlatformMessage(
            platform="dingtalk",
            chat_id=body.get("conversationId", body.get("sessionWebhook", "")),
            user_id=body.get("senderId", ""),
            content=text_content,
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a text message via DingTalk robot message API."""
        token = await self._get_access_token()
        if not token:
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        # Use session webhook for group bot or direct API
        url = f"{DINGTALK_API}/robot/send?access_token={token}"

        payload = {
            "msgtype": "text",
            "text": {"content": reply.content},
        }

        # If we have an @user list
        if chat_id:
            payload["at"] = {"atUserIds": [chat_id]}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    logger.debug("Sent DingTalk message")
                    return True
                logger.error("DingTalk API error: %s", data)
        except Exception as e:
            logger.exception("Failed to send DingTalk message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Not implemented — returns None."""
        return None

    # ── Token Management ──────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """Get or refresh DingTalk access token."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self.app_key or not self.app_secret:
            logger.error("DingTalk app_key and app_secret required")
            return ""

        url = f"{DINGTALK_API}/gettoken?appkey={self.app_key}&appsecret={self.app_secret}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                data = resp.json()
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expires_at = time.time() + data.get("expires_in", 7200) - 300
                    logger.debug("DingTalk access token refreshed")
                    return self._access_token
                logger.error("DingTalk token error: %s", data)
        except Exception as e:
            logger.exception("Failed to get DingTalk token: %s", e)

        return ""
