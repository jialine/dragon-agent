"""
Panda Gateway — WhatsApp (Meta Cloud API) Adapter
==================================================

Handles WhatsApp Business Platform messages via the Meta Cloud API.

Meta docs: https://developers.facebook.com/docs/whatsapp/cloud-api

Configuration::

    adapter = WhatsAppAdapter(
        phone_number_id="...",       # or set WHATSAPP_PHONE_ID env
        cloud_token="...",           # or set WHATSAPP_CLOUD_TOKEN env
        verify_token="...",          # or set WHATSAPP_VERIFY_TOKEN env
    )
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.whatsapp")

WHATSAPP_API = "https://graph.facebook.com/v18.0"


class WhatsAppAdapter(PlatformAdapter):
    """WhatsApp adapter via Meta Cloud API."""

    def __init__(
        self,
        phone_number_id: str = "",
        cloud_token: str = "",
        verify_token: str = "",
    ) -> None:
        super().__init__(platform_name="whatsapp", webhook_path="/whatsapp/webhook")

        self.phone_number_id = phone_number_id or os.getenv("WHATSAPP_PHONE_ID", "")
        self.cloud_token = cloud_token or os.getenv("WHATSAPP_CLOUD_TOKEN", "")
        self.verify_token = verify_token or os.getenv("WHATSAPP_VERIFY_TOKEN", "")

        logger.info("WhatsApp adapter ready (phone_id=%s...)", self.phone_number_id[:6] if self.phone_number_id else "none")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """WhatsApp verification is via hub.verify_token query param on GET."""
        return True  # Actual verification handled in parse_webhook

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse WhatsApp webhook payload.

        Verification (GET)::
            {"_method": "GET", "hub.mode": "subscribe", "hub.verify_token": "...",
             "hub.challenge": "..."}

        Message (POST)::
            {
                "object": "whatsapp_business_account",
                "entry": [{"changes": [{"value": {"messages": [{...}], ...}}]}]
            }
        """
        # Hub verification
        if body.get("_method") == "GET":
            mode = body.get("hub.mode", "")
            challenge = body.get("hub.challenge", "")
            token = body.get("hub.verify_token", "")
            if mode == "subscribe" and token == self.verify_token:
                return PlatformMessage(
                    platform="whatsapp",
                    chat_id="__challenge__",
                    user_id="__system__",
                    content=challenge,
                )
            return None

        # Message parsing
        entries = body.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    if msg.get("type") != "text":
                        continue

                    text = msg.get("text", {}).get("body", "")
                    if not text:
                        continue

                    return PlatformMessage(
                        platform="whatsapp",
                        chat_id=msg.get("from", ""),
                        user_id=msg.get("from", ""),
                        content=text,
                        message_id=msg.get("id", ""),
                        raw=body,
                    )

        return None

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a text message via WhatsApp Cloud API."""
        if not self.cloud_token or not self.phone_number_id:
            logger.error("WhatsApp token/phone_id not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        url = f"{WHATSAPP_API}/{self.phone_number_id}/messages"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": chat_id,
            "type": "text",
            "text": {"body": reply.content[:4096]},
        }

        headers = {
            "Authorization": f"Bearer {self.cloud_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=payload)
                data = resp.json()
                if resp.status_code in (200, 201) and "error" not in data:
                    logger.debug("Sent WhatsApp message to %s", chat_id)
                    return True
                logger.error("WhatsApp API error: %s", data)
        except Exception as e:
            logger.exception("Failed to send WhatsApp message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload media to WhatsApp. Returns media ID."""
        import os as _os

        if not _os.path.exists(file_path) or not self.cloud_token or not self.phone_number_id:
            return None

        url = f"{WHATSAPP_API}/{self.phone_number_id}/media"
        headers = {"Authorization": f"Bearer {self.cloud_token}"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (_os.path.basename(file_path), f)}
                    data = {"messaging_product": "whatsapp"}
                    resp = await client.post(url, headers=headers, files=files, data=data)
                result = resp.json()
                if "id" in result:
                    return result["id"]
        except Exception as e:
            logger.exception("WhatsApp upload failed: %s", e)

        return None
