"""
Dragon Gateway — Feishu (Lark) Platform Adapter
================================================

Handles Feishu bot messages via two transport modes:

  - **websocket** (recommended) — outbound persistent WebSocket using the
    official Lark SDK.  No public URL needed.  Automatic reconnection.
    Set ``FEISHU_CONNECTION_MODE=websocket``.

  - **webhook** — HTTP push from Feishu to a reachable endpoint.
    Requires a public IP or reverse proxy.
    Set ``FEISHU_CONNECTION_MODE=webhook``.

Dependency: ``pip install lark-oapi websockets``

Architecture (WebSocket mode)::

    ┌─────────────┐   WSS outbound   ┌──────────────────┐
    │  Feishu     │ ◄────────────── │  Dragon Agent    │
    │  Server     │ ────────►       │  (lark_oapi.ws)  │
    └─────────────┘   events        └──────┬───────────┘
                                           │ dispatch events
                                    ┌──────▼───────────┐
                                    │  Message Router   │
                                    │  → LLM response   │
                                    │  → Feishu API reply│
                                    └──────────────────┘

Reference: https://open.feishu.cn/document/
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

try:
    import lark_oapi as lark
    from lark_oapi.ws import Client as FeishuWSClient
    LARK_AVAILABLE = True
except ImportError:
    lark = None  # type: ignore
    FeishuWSClient = None  # type: ignore
    LARK_AVAILABLE = False

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None  # type: ignore
    WEBSOCKETS_AVAILABLE = False

import httpx

from dragon.gateway.base import (
    PlatformAdapter, PlatformMessage, PlatformReply, verify_hmac_signature,
)

logger = logging.getLogger("dragon.gateway.feishu")


# ────────────────────────────────────────────────────────────────────
# Feishu Adapter
# ────────────────────────────────────────────────────────────────────


class FeishuAdapter(PlatformAdapter):
    """Feishu / Lark bot adapter with WebSocket + Webhook dual-mode.

    Parameters
    ----------
    app_id : str
        Feishu App ID (cli_xxx).
    app_secret : str
        Feishu App Secret.
    connection_mode : str
        ``"websocket"`` (default) or ``"webhook"``.
    verification_token : str
        Optional — for webhook event verification.
    domain : str
        ``"feishu"`` (China) or ``"lark"`` (international).
    encrypt_key : str
        Optional — webhook encryption key.
    """

    API_BASE = {
        "feishu": "https://open.feishu.cn/open-apis",
        "lark": "https://open.larksuite.com/open-apis",
    }

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        connection_mode: str = "websocket",
        verification_token: str = "",
        domain: str = "feishu",
        encrypt_key: str = "",
    ) -> None:
        super().__init__(platform_name="feishu", webhook_path="/feishu/webhook")

        self.app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self.connection_mode = (
            connection_mode or os.getenv("FEISHU_CONNECTION_MODE", "websocket")
        )
        self.verification_token = verification_token or os.getenv(
            "FEISHU_VERIFICATION_TOKEN", ""
        )
        self.encrypt_key = encrypt_key or os.getenv("FEISHU_ENCRYPT_KEY", "")
        self.domain = domain
        self.api_base = self.API_BASE.get(domain, self.API_BASE["feishu"])

        # Token management
        self._tenant_access_token: str = ""
        self._token_expires_at: float = 0.0

        # WebSocket state
        self._ws_client: Optional[Any] = None
        self._ws_future: Optional[asyncio.Future] = None
        self._ws_thread_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running: bool = False
        self._connected: bool = False

        # Dedup
        self._seen_message_ids: Dict[str, float] = {}  # msg_id → seen_at

        logger.info(
            "Feishu adapter ready (domain=%s, mode=%s)",
            domain, self.connection_mode,
        )

    # ── Connection Lifecycle ──────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to Feishu (WebSocket or start webhook server)."""
        if not self.app_id or not self.app_secret:
            logger.error("[Feishu] FEISHU_APP_ID or FEISHU_APP_SECRET not set")
            return False

        if self.connection_mode == "websocket":
            return await self._connect_websocket()
        elif self.connection_mode == "webhook":
            logger.info("[Feishu] Webhook mode — server handles incoming requests")
            self._connected = True
            return True
        else:
            logger.error(
                "[Feishu] Unknown connection_mode=%s. Use 'websocket' or 'webhook'.",
                self.connection_mode,
            )
            return False

    async def disconnect(self) -> None:
        """Disconnect from Feishu."""
        self._running = False
        self._connected = False

        # Wait for WS thread to finish (with timeout)
        ws_future = self._ws_future
        if ws_future is not None:
            try:
                await asyncio.wait_for(asyncio.shield(ws_future), timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass
            self._ws_future = None

        self._ws_thread_loop = None
        self._loop = None
        logger.info("[Feishu] Disconnected")

    async def _connect_websocket(self) -> bool:
        """Start the Lark SDK WebSocket client in a background thread."""
        if not LARK_AVAILABLE:
            logger.error("[Feishu] lark-oapi not installed. Run: pip install lark-oapi")
            return False
        if not WEBSOCKETS_AVAILABLE:
            logger.error("[Feishu] websockets not installed. Run: pip install websockets")
            return False

        self._loop = asyncio.get_running_loop()
        self._running = True

        # Build the event handler
        event_handler = self._build_event_handler()

        # Create WS client
        self._ws_client = FeishuWSClient(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.WARNING,
        )

        # Start WS client in a dedicated thread with its own event loop
        # Mirrors Hermes's _run_official_feishu_ws_client pattern
        def _run_ws_in_thread():
            import lark_oapi.ws.client as ws_client_module
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws_client_module.loop = loop
            self._ws_thread_loop = loop
            try:
                self._ws_client.start()
            except Exception as exc:
                logger.error("[Feishu] WS client stopped: %s", exc)
            finally:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                try:
                    loop.stop()
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass
                self._ws_thread_loop = None

        self._ws_future = asyncio.get_running_loop().run_in_executor(
            None, _run_ws_in_thread
        )

        self._connected = True
        logger.info("[Feishu] WebSocket client started in background thread")
        return True

    def _build_event_handler(self):
        """Build a Lark SDK event handler that dispatches to Dragon."""
        adapter = self  # capture for closure

        def _dispatch_event(event):
            """Called by Lark SDK on each inbound event."""
            logger.info("[Feishu] RAW EVENT: type=%s", getattr(event, 'type', 'N/A'))
            if not adapter._running:
                return
            try:
                event_type = getattr(event, 'type', '') or ''

                # Dedup for message events
                if hasattr(event, 'event') and hasattr(event.event, 'message'):
                    msg = event.event.message
                    msg_id = getattr(msg, 'message_id', '')
                    if msg_id:
                        now = time.time()
                        if msg_id in adapter._seen_message_ids:
                            if now - adapter._seen_message_ids[msg_id] < 3600:
                                return
                        adapter._seen_message_ids[msg_id] = now
                        cutoff = now - 86400
                        for old_id, old_ts in list(adapter._seen_message_ids.items()):
                            if old_ts < cutoff:
                                del adapter._seen_message_ids[old_id]

                asyncio.run_coroutine_threadsafe(
                    adapter._handle_ws_event(event),
                    adapter._loop,
                )
            except Exception as exc:
                logger.error("[Feishu] Event dispatch error: %s", exc, exc_info=True)

        # Use lark_oapi's built-in dispatcher with catch-all
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        handler = EventDispatcherHandler.builder(
            self.verification_token, self.encrypt_key
        ) \
            .register_p2_im_message_receive_v1(_dispatch_event) \
            .register_p2_card_action_trigger(_dispatch_event) \
            .register_p2_im_message_reaction_created_v1(_dispatch_event) \
            .build()
        return handler

    async def _handle_ws_event(self, event: Any) -> None:
        """Process a WebSocket event in the main asyncio loop."""
        event_type = getattr(event, 'type', 'N/A')
        logger.info("[Feishu] Processing event: type=%s", event_type)

        message = await self._parse_ws_event(event)
        if message is None:
            logger.info("[Feishu] Event skipped: type=%s (not a message)", event_type)
            return

        logger.info(
            "[Feishu] Message: user=%s chat=%s text=%s",
            message.user_id, message.chat_id, message.content[:80],
        )

        if self._message_handler:
            try:
                reply = await self._message_handler(message)
                await self.send_message(reply)
            except Exception as exc:
                logger.exception("[Feishu] Message handler error: %s", exc)
        else:
            logger.warning(
                "[Feishu] No message handler registered — "
                "call adapter.register_handler()! Message from %s dropped.",
                message.user_id,
            )

    async def _parse_ws_event(self, event: Any) -> Optional[PlatformMessage]:
        """Parse a Lark SDK WebSocket event into PlatformMessage."""
        try:
            # Check event type
            event_type = getattr(event, 'type', '') or ''

            if 'message' not in event_type and 'card' not in event_type:
                return None

            evt = getattr(event, 'event', None)
            if evt is None:
                return None

            msg = getattr(evt, 'message', None)
            if msg is None:
                return None

            chat_id = getattr(msg, 'chat_id', '')
            message_id = getattr(msg, 'message_id', '')

            # Sender
            sender = getattr(evt, 'sender', None)
            sender_id = ""
            if sender and hasattr(sender, 'sender_id'):
                sid = sender.sender_id
                if hasattr(sid, 'open_id'):
                    sender_id = sid.open_id

            # Content
            content_raw = getattr(msg, 'content', '{}')
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
                content=text.strip(),
                message_id=message_id,
                raw={"event_type": event_type},
                timestamp=time.time(),
            )
        except Exception as exc:
            logger.debug("[Feishu] Failed to parse WS event: %s", exc)
            return None

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Verify Feishu webhook signature (webhook mode only)."""
        timestamp = headers.get("x-lark-request-timestamp", "")
        nonce = headers.get("x-lark-request-nonce", "")
        signature = headers.get("x-lark-signature", "")

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
        """Parse Feishu event payload into PlatformMessage (webhook mode)."""
        if body.get("type") == "url_verification":
            return PlatformMessage(
                platform="feishu",
                chat_id="__challenge__",
                user_id="__system__",
                content=body.get("challenge", ""),
            )

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
        """Send a text message reply via Feishu HTTP API."""
        token = await self._get_tenant_access_token()
        if not token:
            logger.error("[Feishu] Failed to get tenant access token")
            return False

        chat_id = reply.chat_id
        if not chat_id:
            logger.error("[Feishu] No chat_id in reply")
            return False

        content = json.dumps({"text": reply.content})

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
                        return True
                    logger.error("[Feishu] API error: %s", data.get("msg"))
                else:
                    logger.error("[Feishu] HTTP %d: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.exception("[Feishu] Failed to send message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload a file/image to Feishu and return the file_key."""
        import os as _os

        if not _os.path.exists(file_path):
            return None

        token = await self._get_tenant_access_token()
        if not token:
            return None

        file_name = _os.path.basename(file_path)
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
                        return data["data"].get(f"{upload_type}_key", "")
        except Exception:
            logger.exception("[Feishu] Failed to upload media")

        return None

    # ── Token Management ──────────────────────────────────────────

    async def _get_tenant_access_token(self) -> str:
        """Get or refresh the tenant access token (cached, ~2h TTL)."""
        if self._tenant_access_token and time.time() < self._token_expires_at:
            return self._tenant_access_token

        if not self.app_id or not self.app_secret:
            logger.error("[Feishu] app_id and app_secret required")
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
                        self._token_expires_at = (
                            time.time() + data.get("expire", 7200) - 200
                        )
                        return self._tenant_access_token
                    logger.error("[Feishu] Token error: %s", data.get("msg"))
        except Exception:
            logger.exception("[Feishu] Failed to get tenant access token")

        return ""


# ────────────────────────────────────────────────────────────────────
# URL Verification Helper
# ────────────────────────────────────────────────────────────────────


def handle_url_verification(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle Feishu's URL verification challenge."""
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}
    return {}
