"""
Dragon Gateway — Slack Platform Adapter
=======================================

Handles Slack bot webhooks using the Events API and chat.postMessage.

Slack docs: https://api.slack.com/

Configuration::

    adapter = SlackAdapter(
        bot_token="xoxb-...",       # or set SLACK_BOT_TOKEN env
        signing_secret="...",       # or set SLACK_SIGNING_SECRET env
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

import httpx

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("dragon.gateway.slack")

SLACK_API = "https://slack.com/api"


class SlackAdapter(PlatformAdapter):
    """Slack bot adapter using OAuth token and Events API.

    Handles url_verification challenge and event callbacks.
    """

    def __init__(
        self,
        bot_token: str = "",
        signing_secret: str = "",
    ) -> None:
        super().__init__(platform_name="slack", webhook_path="/slack/webhook")

        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
        self.signing_secret = signing_secret or os.getenv("SLACK_SIGNING_SECRET", "")

        logger.info("Slack adapter ready")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify Slack's X-Slack-Signature header."""
        signature = headers.get("x-slack-signature", "")
        timestamp = headers.get("x-slack-request-timestamp", "")

        if not self.signing_secret or not signature or not timestamp:
            return True  # Allow if not configured

        # Reject old timestamps (5 min replay window)
        if abs(time.time() - int(timestamp)) > 300:
            logger.warning("Slack timestamp too old")
            return False

        sig_basestring = f"v0:{timestamp}:{body.decode()}"
        computed = "v0=" + hmac.new(
            self.signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed, signature)

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse Slack event payload.

        Slack sends:
        - url_verification: {"type": "url_verification", "challenge": "..."}
        - event callback: {"type": "event_callback", "event": {...}}
        """
        # URL verification challenge
        if body.get("type") == "url_verification":
            return PlatformMessage(
                platform="slack",
                chat_id="__challenge__",
                user_id="__system__",
                content=body.get("challenge", ""),
            )

        # Event callback
        if body.get("type") != "event_callback":
            return None

        event = body.get("event", {})
        event_type = event.get("type", "")

        if event_type != "message" or event.get("subtype"):
            return None  # Skip bot messages, message_changed, etc.

        text = event.get("text", "")
        if not text:
            return None

        return PlatformMessage(
            platform="slack",
            chat_id=event.get("channel", ""),
            user_id=event.get("user", ""),
            content=text,
            message_id=event.get("ts", ""),
            thread_id=event.get("thread_ts", ""),
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via Slack chat.postMessage."""
        if not self.bot_token:
            logger.error("Slack bot token not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        # Build Slack Block Kit payload
        blocks = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": reply.content},
            }
        ]
        body = {"channel": chat_id, "blocks": blocks}

        if reply.thread_id:
            body["thread_ts"] = reply.thread_id

        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{SLACK_API}/chat.postMessage",
                    headers=headers,
                    json=body,
                )
                data = resp.json()
                if data.get("ok"):
                    logger.debug("Sent Slack message to %s", chat_id)
                    return True
                logger.error("Slack API error: %s", data.get("error"))
        except Exception as e:
            logger.exception("Failed to send Slack message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file to Slack. Returns the file URL or None."""
        import os as _os

        if not _os.path.exists(file_path) or not self.bot_token:
            return None

        url = f"{SLACK_API}/files.upload"
        headers = {"Authorization": f"Bearer {self.bot_token}"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (_os.path.basename(file_path), f)}
                    data = {"channels": ""}  # generic upload
                    resp = await client.post(url, headers=headers, files=files, data=data)
                result = resp.json()
                if result.get("ok") and result.get("file"):
                    return result["file"].get("permalink", "")
        except Exception as e:
            logger.exception("Slack upload failed: %s", e)

        return None
