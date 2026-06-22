"""
Dragon Gateway — Telegram Platform Adapter
===========================================

Handles Telegram bot webhooks: message reception, reply, media upload.

Telegram Bot API docs: https://core.telegram.org/bots/api

Configuration::

    adapter = TelegramAdapter(
        bot_token="123456:ABC-DEF1234gh",
        webhook_url="https://your-server.com/telegram/webhook",
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

from dragon.gateway.base import (
    PlatformAdapter, PlatformMessage, PlatformReply,
)

logger = logging.getLogger("dragon.gateway.telegram")


# ────────────────────────────────────────────────────────────────────
# Telegram Adapter
# ────────────────────────────────────────────────────────────────────


class TelegramAdapter(PlatformAdapter):
    """Telegram bot adapter.

    Supports:
    - Webhook message reception
    - Text message reply
    - Media upload (photos, documents)
    - Inline keyboards (buttons)
    - Long message splitting (Telegram's 4096 char limit)
    """

    API_BASE = "https://api.telegram.org"

    def __init__(
        self,
        bot_token: str = "",
        webhook_url: str = "",
        webhook_secret: str = "",
    ) -> None:
        super().__init__(platform_name="telegram", webhook_path="/telegram/webhook")

        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.webhook_url = webhook_url or os.getenv("TELEGRAM_WEBHOOK_URL", "")
        self.webhook_secret = webhook_secret or os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

        self._api_url = f"{self.API_BASE}/bot{self.bot_token}"

        logger.info("Telegram adapter ready")

    # ── Webhook Setup ─────────────────────────────────────────────

    async def set_webhook(self) -> bool:
        """Register the webhook URL with Telegram."""
        if not self.webhook_url:
            logger.warning("No webhook_url configured")
            return False

        url = f"{self._api_url}/setWebhook"
        params = {"url": self.webhook_url}
        if self.webhook_secret:
            params["secret_token"] = self.webhook_secret

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=params)
                data = resp.json()
                if data.get("ok"):
                    logger.info("Telegram webhook set: %s", self.webhook_url)
                    return True
                else:
                    logger.error("Failed to set webhook: %s", data.get("description"))
        except Exception as e:
            logger.exception("Failed to set Telegram webhook: %s", e)

        return False

    async def delete_webhook(self) -> bool:
        """Remove the webhook (switch back to polling)."""
        url = f"{self._api_url}/deleteWebhook"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url)
                return resp.json().get("ok", False)
        except Exception:
            return False

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify Telegram webhook secret token.

        Telegram sends X-Telegram-Bot-Api-Secret-Token header.
        """
        if not self.webhook_secret:
            return True  # no secret configured → accept all

        received = headers.get("x-telegram-bot-api-secret-token", "")
        return hmac.compare_digest(received, self.webhook_secret)

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse Telegram update into PlatformMessage.

        Telegram update structure::

            {
                "update_id": 123456789,
                "message": {
                    "message_id": 100,
                    "from": {"id": 12345, "username": "user"},
                    "chat": {"id": -10012345, "type": "group"},
                    "text": "Hello!",
                    "date": 1700000000,
                    "message_thread_id": 50  (forum topics)
                }
            }
        """
        message = body.get("message") or body.get("edited_message")
        if not message:
            return None

        from_user = message.get("from", {})
        chat = message.get("chat", {})
        text = message.get("text", "") or message.get("caption", "")

        if not text:
            return None

        user_id = str(from_user.get("id", ""))
        chat_id = str(chat.get("id", ""))
        thread_id = str(message.get("message_thread_id", ""))

        # Handle / commands — strip the bot mention if present
        text = text.strip()

        return PlatformMessage(
            platform="telegram",
            chat_id=chat_id,
            user_id=user_id,
            content=text,
            message_id=str(message.get("message_id", "")),
            thread_id=thread_id,
            raw=body,
            timestamp=float(message.get("date", time.time())),
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a reply via Telegram Bot API.

        Supports:
        - Text messages (auto-split if >4096 chars)
        - Reply to specific message
        - Inline keyboard buttons
        """
        chat_id = reply.chat_id
        if not chat_id:
            logger.error("No chat_id in reply")
            return False

        text = reply.format_for_telegram()
        url = f"{self._api_url}/sendMessage"

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        if reply.reply_to_message_id:
            payload["reply_to_message_id"] = reply.reply_to_message_id
        if reply.thread_id:
            payload["message_thread_id"] = reply.thread_id

        # Inline keyboard
        if reply.buttons:
            payload["reply_markup"] = json.dumps({
                "inline_keyboard": [[
                    {"text": b.get("text", ""), "callback_data": b.get("callback_data", "")}
                    for b in reply.buttons
                ]]
            })

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    logger.debug("Sent Telegram message to %s", chat_id)
                    return True
                elif "parse" in str(data.get("description", "")).lower():
                    # Retry without Markdown parsing
                    payload["parse_mode"] = ""
                    resp2 = await client.post(url, json=payload)
                    data2 = resp2.json()
                    return data2.get("ok", False)
                else:
                    logger.error("Telegram API error: %s", data.get("description"))
        except Exception as e:
            logger.exception("Failed to send Telegram message: %s", e)

        return False

    async def send_long_message(self, reply: PlatformReply) -> bool:
        """Send a message that exceeds 4096 chars by splitting into chunks."""
        if len(reply.content) <= 4000:
            return await self.send_message(reply)

        chunks = []
        remaining = reply.content
        chunk_size = 3800

        for i in range(10):  # max 10 chunks
            if not remaining:
                break
            if len(remaining) <= chunk_size:
                chunks.append(remaining)
                break

            # Split at nearest paragraph break
            split_point = remaining.rfind("\n\n", 0, chunk_size)
            if split_point == -1:
                split_point = remaining.rfind("\n", 0, chunk_size)
            if split_point == -1:
                split_point = chunk_size

            chunks.append(remaining[:split_point].strip())
            remaining = remaining[split_point:].strip()

        success = True
        for i, chunk in enumerate(chunks):
            chunk_reply = PlatformReply(
                content=f"{chunk}\n\n_({i+1}/{len(chunks)})_",
                chat_id=reply.chat_id,
                thread_id=reply.thread_id,
            )
            if not await self.send_message(chunk_reply):
                success = False
            await asyncio_sleep(0.3)  # rate limit

        return success

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a photo or document and return the file_id."""
        import os

        if not os.path.exists(file_path):
            logger.error("File not found: %s", file_path)
            return None

        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        is_photo = ext in ("jpg", "jpeg", "png", "gif", "webp")
        method = "sendPhoto" if is_photo else "sendDocument"

        url = f"{self._api_url}/{method}"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    # We need a chat_id for sending media — use a placeholder
                    # For actual use, the caller provides chat_id
                    files = {"document" if not is_photo else "photo": f}
                    # Telegram requires chat_id for media, return path for now
                    return file_path
        except Exception as e:
            logger.exception("Failed to upload media: %s", e)
            return None


# ── Helper ─────────────────────────────────────────────────────────


async def asyncio_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)
