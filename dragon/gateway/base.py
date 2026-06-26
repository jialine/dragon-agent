"""
Dragon Gateway — Abstract Platform Adapter
==========================================

Base class for all messaging platform adapters.

Each adapter:
- Receives messages from the platform (via webhook or polling)
- Converts them to a standardized PlatformMessage
- Formats and sends responses back via PlatformReply

To add a new platform, subclass PlatformAdapter and implement:
    - handle_webhook() or handle_poll()
    - send_message()
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.gateway")


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class PlatformMessage:
    """Standardized message from any platform."""
    platform: str                    # "feishu", "telegram", "discord", etc.
    chat_id: str                    # unique chat/room identifier
    user_id: str                    # sender identifier
    content: str                    # message text
    message_id: str = ""            # platform-specific message ID
    thread_id: str = ""             # thread/topic ID if applicable
    raw: Dict[str, Any] = field(default_factory=dict)  # original platform payload
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.message_id:
            self.message_id = hashlib.md5(
                f"{self.chat_id}:{self.user_id}:{self.timestamp}".encode()
            ).hexdigest()[:12]

    @property
    def session_id(self) -> str:
        """Derive a stable session ID from chat_id."""
        return hashlib.sha256(
            f"{self.platform}:{self.chat_id}".encode()
        ).hexdigest()[:12]


@dataclass
class PlatformReply:
    """Standardized reply to send back to any platform."""
    content: str
    chat_id: str = ""
    thread_id: str = ""
    reply_to_message_id: str = ""
    media_paths: List[str] = field(default_factory=list)  # local file paths for attachments
    buttons: List[Dict[str, Any]] = field(default_factory=list)
    audio_chunks: List[Tuple[str, bytes]] = field(default_factory=list)  # (sentence, mp3_bytes) for voice mode
    output_mode: str = "text"  # "text" or "voice"

    def format_for_telegram(self) -> str:
        """Truncate long messages for Telegram's 4096 char limit."""
        text = self.content
        if len(text) > 4000:
            text = text[:3900] + "\n\n... [消息过长已截断]"
        return text


# ────────────────────────────────────────────────────────────────────
# Abstract Platform Adapter
# ────────────────────────────────────────────────────────────────────


class PlatformAdapter(ABC):
    """Abstract base for messaging platform adapters.

    Subclasses implement the platform-specific logic for:
    - Receiving webhooks
    - Sending messages
    - Verifying signatures
    - Uploading media

    Parameters
    ----------
    platform_name : str
        Identifier: "feishu", "telegram", "discord", etc.
    webhook_path : str
        URL path for incoming webhooks, e.g. "/feishu/webhook".
    """

    def __init__(
        self,
        platform_name: str,
        webhook_path: str = "",
    ) -> None:
        self.platform_name = platform_name
        self.webhook_path = webhook_path or f"/{platform_name}/webhook"
        self._message_handler: Optional[Callable] = None
        logger.info("Platform adapter '%s' initialized", platform_name)

    # ── Message Handler Registration ──────────────────────────────

    def register_handler(self, handler: Callable) -> None:
        """Register an async callback: handler(PlatformMessage) -> PlatformReply."""
        self._message_handler = handler

    async def handle_message(self, message: PlatformMessage) -> PlatformReply:
        """Route a message to the registered handler."""
        if self._message_handler is None:
            return PlatformReply(content="[系统未就绪，请稍后再试]")

        try:
            return await self._message_handler(message)
        except Exception as e:
            logger.exception("Message handler error for %s", self.platform_name)
            return PlatformReply(content=f"处理消息时出错: {e}")

    # ── Abstract Methods ──────────────────────────────────────────

    @abstractmethod
    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify the webhook request is authentic."""
        ...

    @abstractmethod
    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse a webhook payload into a PlatformMessage."""
        ...

    @abstractmethod
    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a reply back to the platform."""
        ...

    @abstractmethod
    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a media file and return a platform-specific reference."""
        ...


# ────────────────────────────────────────────────────────────────────
# Signature Verification Helpers
# ────────────────────────────────────────────────────────────────────


def verify_hmac_signature(
    secret: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify HMAC-SHA256 signature (used by Feishu and others)."""
    if not secret or not timestamp or not nonce or not signature:
        return False

    payload = f"{timestamp}{nonce}".encode() + body
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_telegram_signature(token: str, data: Dict[str, Any]) -> bool:
    """Verify Telegram's HMAC-SHA256 data_check_string signature."""
    from urllib.parse import unquote

    if "hash" not in data:
        return False

    received_hash = data.pop("hash")
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(data.items())
    )

    secret_key = hashlib.sha256(token.encode()).digest()
    computed_hash = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()

    data["hash"] = received_hash  # restore
    return hmac.compare_digest(computed_hash, received_hash)
