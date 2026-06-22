"""
Dragon Gateway — Signal Messenger Adapter
=========================================

Uses signal-cli REST API to send/receive Signal messages.

Requires signal-cli-rest-api running locally (default: http://localhost:8080).
GitHub: https://github.com/bbernhard/signal-cli-rest-api

Configuration::

    adapter = SignalAdapter(
        rest_url="http://localhost:8080",  # or set SIGNAL_REST_URL env
        sender_number="+1234567890",       # or set SIGNAL_SENDER_NUMBER env
    )
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("dragon.gateway.signal")


class SignalAdapter(PlatformAdapter):
    """Signal adapter via signal-cli REST API."""

    def __init__(
        self,
        rest_url: str = "",
        sender_number: str = "",
    ) -> None:
        super().__init__(platform_name="signal", webhook_path="/signal/webhook")

        self.rest_url = (rest_url or os.getenv("SIGNAL_REST_URL", "http://localhost:8080")).rstrip("/")
        self.sender_number = sender_number or os.getenv("SIGNAL_SENDER_NUMBER", "")

        logger.info("Signal adapter ready (url=%s)", self.rest_url)

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """No webhook verification — internal service."""
        return True

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse incoming message from signal-cli receive webhook.

        Expected format (from signal-cli REST API webhook callback)::

            {
                "envelope": {
                    "source": "+1234567890",
                    "sourceNumber": "+1234567890",
                    "dataMessage": {
                        "message": "hello",
                        "timestamp": 1234567890123
                    }
                }
            }
        """
        envelope = body.get("envelope", body)  # Some wrappers use 'envelope'

        # sync / receipt / typing messages — skip
        if "dataMessage" not in envelope and "syncMessage" in envelope:
            return None

        data_msg = envelope.get("dataMessage", {})
        text = data_msg.get("message", "")
        if not text:
            return None

        source = envelope.get("source", "") or envelope.get("sourceNumber", "")

        return PlatformMessage(
            platform="signal",
            chat_id=source,  # Signal uses phone number as chat ID
            user_id=source,
            content=text,
            message_id=str(data_msg.get("timestamp", "")),
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via signal-cli REST API POST /v2/send."""
        chat_id = reply.chat_id
        if not chat_id:
            return False

        url = f"{self.rest_url}/v2/send"

        payload: Dict[str, Any] = {
            "number": chat_id,
            "message": reply.content,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.debug("Sent Signal message to %s", chat_id)
                    return True
                logger.error("Signal API error %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Failed to send Signal message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Not implemented — returns None."""
        return None
