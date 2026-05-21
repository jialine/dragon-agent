"""
Panda Gateway — Feishu (Lark) Platform Adapter
================================================

Handles Feishu bot webhooks: message reception, reply, media upload.

Feishu API docs: https://open.feishu.cn/document/

Configuration::

    adapter = FeishuAdapter(
        app_id="cli_xxxxx",
        app_secret="xxxxxxxx",
        verification_token="xxxxxxxx",  # optional, for event verification
        domain="feishu",  # or "lark" for international
    )
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from panda.gateway.base import (
    PlatformAdapter, PlatformMessage, PlatformReply, verify_hmac_signature,
)

logger = logging.getLogger("panda.gateway.feishu")


# ────────────────────────────────────────────────────────────────────
# Feishu Adapter
# ────────────────────────────────────────────────────────────────────


class FeishuAdapter(PlatformAdapter):
    """Feishu / Lark bot adapter.

    Supports:
    - Webhook event verification (URL verification challenge)
    - Message reception (text messages in DMs and groups)
    - Reply via Feishu message API
    - Media upload (images, files)
    - Tenant access token auto-refresh
    """

    API_BASE = {
        "feishu": "https://open.feishu.cn/open-apis",
        "lark": "https://open.larksuite.com/open-apis",
    }

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        verification_token: str = "",
        domain: str = "feishu",
        encrypt_key: str = "",
    ) -> None:
        super().__init__(platform_name="feishu", webhook_path="/feishu/webhook")

        self.app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self.verification_token = verification_token or os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        self.encrypt_key = encrypt_key or os.getenv("FEISHU_ENCRYPT_KEY", "")
        self.domain = domain
        self.api_base = self.API_BASE.get(domain, self.API_BASE["feishu"])

        # Token management
        self._tenant_access_token: str = ""
        self._token_expires_at: float = 0.0

        logger.info("Feishu adapter ready (domain=%s)", domain)

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify Feishu webhook signature.

        Feishu sends headers: X-Lark-Request-Timestamp, X-Lark-Request-Nonce,
        X-Lark-Signature for verification.
        """
        timestamp = headers.get("x-lark-request-timestamp", "")
        nonce = headers.get("x-lark-request-nonce", "")
        signature = headers.get("x-lark-signature", "")

        # If no signature headers, accept (may be URL verification)
        if not timestamp and not signature:
            return True

        return verify_hmac_signature(
            secret=self.app_secret,
            timestamp=timestamp,
            nonce=nonce,
            body=body,
            signature=signature,
        )

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse Feishu event payload into PlatformMessage.

        Feishu event structure::

            {
                "schema": "2.0",
                "header": {"event_type": "im.message.receive_v1", ...},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_xxx"}},
                    "message": {
                        "message_id": "om_xxx",
                        "chat_id": "oc_xxx",
                        "content": "{\\"text\\":\\"hello\\"}",
                        ...
                    }
                }
            }
        """
        # Check for URL verification challenge
        if body.get("type") == "url_verification":
            return PlatformMessage(
                platform="feishu",
                chat_id="__challenge__",
                user_id="__system__",
                content=body.get("challenge", ""),
            )

        # Parse event
        header = body.get("header", {})
        event_type = header.get("event_type", "")

        if "message" not in event_type:
            return None

        event = body.get("event", {})
        sender = event.get("sender", {})
        message = event.get("message", {})

        sender_id = sender.get("sender_id", {}).get("open_id", "")
        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")
        thread_id = message.get("root_id", "") or message.get("parent_id", "")

        # Parse message content (Feishu sends content as JSON string)
        content_raw = message.get("content", "{}")
        try:
            content_obj = json.loads(content_raw)
            text = content_obj.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = str(content_raw)

        if not text:
            return None

        return PlatformMessage(
            platform="feishu",
            chat_id=chat_id,
            user_id=sender_id,
            content=text,
            message_id=message_id,
            thread_id=thread_id,
            raw=body,
            timestamp=float(header.get("create_time", int(time.time() * 1000))) / 1000,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a text message reply via Feishu API.

        Uses the "reply" endpoint if reply_to_message_id is set,
        otherwise sends a new message to the chat.
        """
        token = await self._get_tenant_access_token()
        if not token:
            logger.error("Failed to get tenant access token")
            return False

        chat_id = reply.chat_id
        if not chat_id:
            logger.error("No chat_id in reply")
            return False

        # Build message content
        content = json.dumps({"text": reply.content})

        # Choose endpoint
        if reply.reply_to_message_id:
            url = f"{self.api_base}/im/v1/messages/{reply.reply_to_message_id}/reply"
            body = {"content": content, "msg_type": "text"}
        else:
            url = f"{self.api_base}/im/v1/messages"
            body = {
                "receive_id": chat_id,
                "content": content,
                "msg_type": "text",
            }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        logger.debug("Sent Feishu message to %s", chat_id)
                        return True
                    else:
                        logger.error("Feishu API error: %s", data.get("msg"))
                else:
                    logger.error("Feishu HTTP %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Failed to send Feishu message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file/image to Feishu and return the image_key/file_key."""
        import os

        if not os.path.exists(file_path):
            logger.error("File not found: %s", file_path)
            return None

        token = await self._get_tenant_access_token()
        if not token:
            return None

        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

        is_image = ext in ("jpg", "jpeg", "png", "gif", "webp", "bmp")
        upload_type = "image" if is_image else "file"

        url = f"{self.api_base}/im/v1/{upload_type}s"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    files = {
                        f"{upload_type}_type": (None, "message"),
                        f"{upload_type}": (file_name, f),
                    }
                    headers = {"Authorization": f"Bearer {token}"}
                    resp = await client.post(url, headers=headers, files=files)

                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        key = data["data"].get(f"{upload_type}_key", "")
                        logger.info("Uploaded %s: %s", upload_type, key)
                        return key
        except Exception as e:
            logger.exception("Failed to upload media: %s", e)

        return None

    # ── Token Management ──────────────────────────────────────────

    async def _get_tenant_access_token(self) -> str:
        """Get or refresh the tenant access token.

        Tokens expire after ~2 hours. This caches and auto-refreshes.
        """
        if self._tenant_access_token and time.time() < self._token_expires_at:
            return self._tenant_access_token

        if not self.app_id or not self.app_secret:
            logger.error("Feishu app_id and app_secret required")
            return ""

        url = f"{self.api_base}/auth/v3/tenant_access_token/internal"
        body = {"app_id": self.app_id, "app_secret": self.app_secret}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        self._tenant_access_token = data["tenant_access_token"]
                        # Feishu tokens typically expire in 7200s, cache for 7000s
                        self._token_expires_at = time.time() + data.get("expire", 7200) - 200
                        logger.debug("Tenant access token refreshed")
                        return self._tenant_access_token
                    else:
                        logger.error("Token fetch error: %s", data.get("msg"))
        except Exception as e:
            logger.exception("Failed to get tenant access token: %s", e)

        return ""


# ────────────────────────────────────────────────────────────────────
# URL Verification Helper (for Feishu event subscription setup)
# ────────────────────────────────────────────────────────────────────


def handle_url_verification(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle Feishu's URL verification challenge during event subscription setup.

    Returns the response body to send back.
    """
    if body.get("type") == "url_verification":
        token = body.get("token", "")
        challenge = body.get("challenge", "")
        return {"challenge": challenge}

    return {}
