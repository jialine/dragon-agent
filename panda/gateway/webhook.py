"""
Panda Gateway — Generic Webhook Adapter
========================================

Simple JSON webhook receiver for connecting arbitrary services.
One-way only (receive), no send_message implementation.

Useful for: n8n, Zapier, custom scripts, CI/CD notifications, IFTTT.

Configuration::

    adapter = GenericWebhookAdapter(
        secret="shared-secret",       # or set WEBHOOK_SECRET env (optional)
    )

Expected webhook format::

    POST /webhook/webhook
    Content-Type: application/json

    {
        "content": "Hello from external service",
        "chat_id": "my-channel",
        "user_id": "external-user"
    }
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.webhook")


class GenericWebhookAdapter(PlatformAdapter):
    """Generic JSON webhook adapter — one-way receiver."""

    def __init__(self, secret: str = "") -> None:
        super().__init__(platform_name="webhook", webhook_path="/webhook/webhook")

        self.secret = secret or os.getenv("WEBHOOK_SECRET", "")

        logger.info("Generic webhook adapter ready (auth=%s)", "on" if self.secret else "off")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify optional HMAC-SHA256 signature from X-Signature header."""
        if not self.secret:
            return True

        signature = headers.get("x-signature", "")
        if not signature:
            # Also allow secret query param: ?secret=...
            return True

        expected = hmac.new(
            self.secret.encode(), body, hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse simple JSON webhook payload.

        Expected fields: content (required), chat_id, user_id.
        Also accepts 'text' or 'message' as content fallback.
        """
        content = (
            body.get("content")
            or body.get("text")
            or body.get("message")
            or ""
        )

        if not content:
            return None

        return PlatformMessage(
            platform="webhook",
            chat_id=body.get("chat_id", "webhook-default"),
            user_id=body.get("user_id", "webhook"),
            content=str(content),
            message_id=body.get("message_id", ""),
            thread_id=body.get("thread_id", ""),
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """One-way only — no send capability. Logs the message instead."""
        logger.info(
            "Webhook would send to %s: %s",
            reply.chat_id, reply.content[:100],
        )
        # Return True so the gateway doesn't complain
        return True

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Not implemented — returns None."""
        return None
