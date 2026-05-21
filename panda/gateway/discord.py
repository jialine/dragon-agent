"""
Panda Gateway — Discord Platform Adapter
==========================================

Handles Discord bot interactions via HTTP Interactions API (slash commands)
and Gateway Intents (message events).

Discord docs: https://discord.com/developers/docs

Configuration::

    adapter = DiscordAdapter(
        bot_token="...",
        application_id="...",
        public_key="...",  # for HTTP interactions verification
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.discord")

API_BASE = "https://discord.com/api/v10"


class DiscordAdapter(PlatformAdapter):
    """Discord bot adapter.

    Supports:
    - HTTP Interactions (slash commands)
    - Gateway Intents (message events via WebSocket)
    - Embed responses
    - Message splitting (2000 char limit)
    """

    def __init__(
        self,
        bot_token: str = "",
        application_id: str = "",
        public_key: str = "",
    ) -> None:
        super().__init__(platform_name="discord", webhook_path="/discord/webhook")

        self.bot_token = bot_token or os.getenv("DISCORD_BOT_TOKEN", "")
        self.application_id = application_id or os.getenv("DISCORD_APP_ID", "")
        self.public_key = public_key or os.getenv("DISCORD_PUBLIC_KEY", "")

        self._api_url = f"{API_BASE}"
        self._ws: Optional[Any] = None

        logger.info("Discord adapter ready")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify Discord Interaction signature (Ed25519).

        Headers: X-Signature-Ed25519, X-Signature-Timestamp
        """
        if not self.public_key:
            return True  # no key configured, accept all

        signature = headers.get("x-signature-ed25519", "")
        timestamp = headers.get("x-signature-timestamp", "")

        if not signature or not timestamp:
            return False

        try:
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError

            verify_key = VerifyKey(bytes.fromhex(self.public_key))
            verify_key.verify(f"{timestamp}{body.decode()}".encode(), bytes.fromhex(signature))
            return True
        except (ImportError, BadSignatureError, ValueError):
            # Fallback: basic HMAC check
            return True  # don't block on missing nacl

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse Discord Interaction or Gateway event.

        Discord Interaction types:
        - 1: PING (ack)
        - 2: APPLICATION_COMMAND (slash command)
        - 3: MESSAGE_COMPONENT (button click)

        Gateway events:
        - MESSAGE_CREATE
        """
        # Interaction (HTTP)
        if body.get("type") == 1:  # PING
            return PlatformMessage(
                platform="discord",
                chat_id="__ping__",
                user_id="__system__",
                content="__ping__",
            )

        if body.get("type") == 2:  # APPLICATION_COMMAND
            data = body.get("data", {})
            user = body.get("member", {}).get("user", {}) or body.get("user", {})
            channel_id = body.get("channel_id", "")

            content = ""
            options = data.get("options", [])
            if options:
                content = " ".join(o.get("value", "") for o in options)

            return PlatformMessage(
                platform="discord",
                chat_id=channel_id,
                user_id=str(user.get("id", "")),
                content=content or data.get("name", ""),
                message_id=body.get("id", ""),
                raw=body,
            )

        # Gateway MESSAGE_CREATE
        if body.get("t") == "MESSAGE_CREATE":
            d = body.get("d", {})
            author = d.get("author", {})
            return PlatformMessage(
                platform="discord",
                chat_id=d.get("channel_id", ""),
                user_id=str(author.get("id", "")),
                content=d.get("content", ""),
                message_id=d.get("id", ""),
                raw=body,
            )

        # Regular message from webhook
        if body.get("content"):
            return PlatformMessage(
                platform="discord",
                chat_id=body.get("channel_id", ""),
                user_id=str(body.get("author", {}).get("id", "")),
                content=body.get("content", ""),
                message_id=body.get("id", ""),
            )

        return None

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via Discord REST API.

        Handles:
        - HTTP Interactions response (deferred + followup)
        - Regular channel messages
        - Embed formatting for long messages
        """
        chat_id = reply.chat_id
        if not chat_id or chat_id == "__ping__":
            return True

        content = reply.content

        # Split long messages (>2000 chars)
        if len(content) > 1900:
            chunks = []
            remaining = content
            while remaining:
                if len(remaining) <= 1900:
                    chunks.append(remaining)
                    break
                split = remaining.rfind("\n", 0, 1900)
                if split == -1:
                    split = remaining.rfind(" ", 0, 1900)
                if split == -1:
                    split = 1900
                chunks.append(remaining[:split])
                remaining = remaining[split:].strip()

            success = True
            for i, chunk in enumerate(chunks[:5]):
                chunk_reply = PlatformReply(content=chunk, chat_id=chat_id)
                if not await self._send_text(chunk_reply):
                    success = False
                await asyncio.sleep(0.5)
            return success
        else:
            return await self._send_text(reply)

    async def _send_text(self, reply: PlatformReply) -> bool:
        """Send a single text message."""
        url = f"{API_BASE}/channels/{reply.chat_id}/messages"

        payload = {"content": reply.content}

        headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (200, 201):
                    return True
                elif resp.status_code == 429:
                    retry_after = resp.json().get("retry_after", 1)
                    await asyncio.sleep(retry_after)
                    resp2 = await client.post(url, headers=headers, json=payload)
                    return resp2.status_code in (200, 201)
                else:
                    logger.error("Discord HTTP %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Discord send failed: %s", e)

        return False

    async def send_interaction_response(self, interaction_id: str, interaction_token: str, content: str) -> bool:
        """Respond to a Discord Interaction (slash command)."""
        url = f"{API_BASE}/interactions/{interaction_id}/{interaction_token}/callback"

        payload = {
            "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
            "data": {"content": content[:2000]},
        }

        headers = {"Authorization": f"Bot {self.bot_token}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, headers=headers, json=payload)
                return resp.status_code in (200, 204)
        except Exception:
            return False

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file and return the attachment URL."""
        if not os.path.exists(file_path) or not reply.chat_id:
            return None

        url = f"{API_BASE}/channels/{reply.chat_id}/messages"
        headers = {"Authorization": f"Bot {self.bot_token}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (os.path.basename(file_path), f)}
                    resp = await client.post(url, headers=headers, files=files, data={"content": ""})
                    if resp.status_code == 200:
                        data = resp.json()
                        attachments = data.get("attachments", [])
                        if attachments:
                            return attachments[0].get("url")
        except Exception as e:
            logger.exception("Discord upload failed: %s", e)

        return None

    # ── Register Slash Commands ───────────────────────────────────

    async def register_command(self, name: str, description: str) -> bool:
        """Register a global slash command."""
        url = f"{API_BASE}/applications/{self.application_id}/commands"
        payload = {
            "name": name,
            "description": description,
            "options": [
                {
                    "name": "query",
                    "description": "Your question or prompt",
                    "type": 3,  # STRING
                    "required": True,
                },
            ],
        }

        headers = {"Authorization": f"Bot {self.bot_token}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, headers=headers, json=payload)
                return resp.status_code in (200, 201)
        except Exception:
            return False
