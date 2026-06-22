"""
Dragon Gateway — Matrix Platform Adapter
========================================

Handles Matrix chat messages via the Client-Server API.
Uses polling to receive messages, API calls for sending.

Matrix docs: https://spec.matrix.org/latest/client-server-api/

Configuration::

    adapter = MatrixAdapter(
        homeserver="https://matrix.org",  # or set MATRIX_HOMESERVER env
        access_token="syt_...",           # or set MATRIX_ACCESS_TOKEN env
    )
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("dragon.gateway.matrix")

MATRIX_API = "/_matrix/client/v3"


class MatrixAdapter(PlatformAdapter):
    """Matrix adapter using the Client-Server API v3.

    Receives messages via polling /sync, sends via /send/m.room.message.
    Internal polling means verify_webhook always passes.
    """

    def __init__(
        self,
        homeserver: str = "",
        access_token: str = "",
    ) -> None:
        super().__init__(platform_name="matrix", webhook_path="/matrix/webhook")

        self.homeserver = (homeserver or os.getenv("MATRIX_HOMESERVER", "https://matrix.org")).rstrip("/")
        self.access_token = access_token or os.getenv("MATRIX_ACCESS_TOKEN", "")

        self._next_batch: str = ""

        logger.info(
            "Matrix adapter ready (homeserver=%s)",
            self.homeserver,
        )

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Internal polling adapter — always passes verification."""
        return True

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse a Matrix event dict from /sync polling.

        Expected format from the polling layer::

            {
                "room_id": "!abc:matrix.org",
                "event_id": "$event...",
                "sender": "@user:matrix.org",
                "content": {
                    "msgtype": "m.text",
                    "body": "hello world"
                }
            }
        """
        room_id = body.get("room_id", "")
        event_id = body.get("event_id", "")
        sender = body.get("sender", "")
        content = body.get("content", {})

        msgtype = content.get("msgtype", "")
        text = content.get("body", "")

        if msgtype != "m.text" or not text:
            return None

        return PlatformMessage(
            platform="matrix",
            chat_id=room_id,
            user_id=sender,
            content=text,
            message_id=event_id,
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via Matrix /send API."""
        if not self.access_token or not self.homeserver:
            logger.error("Matrix credentials not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        # Generate a transaction ID for idempotency
        import uuid
        txn_id = str(uuid.uuid4())

        url = f"{self.homeserver}{MATRIX_API}/rooms/{chat_id}/send/m.room.message/{txn_id}"

        payload = {
            "msgtype": "m.text",
            "body": reply.content,
        }
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.put(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    logger.debug("Sent Matrix message to %s", chat_id)
                    return True
                logger.error("Matrix API error: %s %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Failed to send Matrix message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file to the Matrix content repository. Returns MXC URI."""
        import os as _os

        if not _os.path.exists(file_path) or not self.access_token:
            return None

        url = f"{self.homeserver}/_matrix/media/v3/upload"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (_os.path.basename(file_path), f, "application/octet-stream")}
                    resp = await client.post(url, headers=headers, files=files)
                if resp.status_code == 200:
                    result = resp.json()
                    mxc = result.get("content_uri", "")
                    logger.debug("Uploaded media to Matrix: %s", mxc)
                    return mxc
                logger.error("Matrix upload error: %s", resp.text)
        except Exception as e:
            logger.exception("Matrix upload failed: %s", e)

        return None
