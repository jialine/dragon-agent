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
import os
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

# ── Processing status reactions (Hermes-aligned) ─────────────────
_FEISHU_REACTION_IN_PROGRESS = "Typing"      # while processing
_FEISHU_REACTION_FAILURE = "CrossMark"       # on failure


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

        # Sent message tracking (for editing past messages)
        self._last_reply_id: str = ""
        self._last_alert_id: str = ""
        self._last_progress_id: str = ""
        self._last_progress_edit_time: float = 0.0  # cooldown for edit_message
        self._active_chats: set = set()  # track active chat_ids for restart notification
        self._sent_ids: List[str] = []  # keep up to 10 recent

        # Voice mode
        self.voice_enabled: Dict[str, bool] = {}  # per-user voice toggle
        self._voice_engine: Any = None  # shared VoiceEngine from processor

        # Processing status reactions (Hermes-aligned)
        self._reactions_enabled: bool = True
        self._pending_processing_reactions: dict = {}  # msg_id -> reaction_id

        logger.info(
            "Feishu adapter ready (domain=%s, mode=%s)",
            domain, self.connection_mode,
        )

    def set_voice_engine(self, engine: Any) -> None:
        """Wire the shared VoiceEngine from the processor."""
        self._voice_engine = engine

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

    async def notify_shutdown(self) -> None:
        """Send gateway restarting notification to all active chats."""
        msg = "⚠️ Dragon Gateway restarting — Your current task will be interrupted."
        for chat_id in list(self._active_chats):
            try:
                await self.send_message(PlatformReply(chat_id=chat_id, content=msg))
            except Exception as e:
                logger.warning("[Feishu] shutdown notify failed for %s: %s", chat_id, e)
        # Persist chats for startup notification
        import json, os
        path = os.path.expanduser("~/.hermes/.restart_notify.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(list(self._active_chats), f)
        logger.info("[Feishu] Shutdown notification sent to %d chats", len(self._active_chats))

    async def notify_startup(self) -> None:
        """Send gateway back online notification to previously active chats."""
        import json, os
        path = os.path.expanduser("~/.hermes/.restart_notify.json")
        chats = []
        if os.path.exists(path):
            try:
                with open(path) as f:
                    chats = json.load(f)
                os.remove(path)
            except Exception:
                pass
        if not chats:
            return
        msg = "✅ Dragon Gateway back online — Ready."
        for chat_id in chats:
            try:
                await self.send_message(PlatformReply(chat_id=chat_id, content=msg))
            except Exception as e:
                logger.warning("[Feishu] startup notify failed for %s: %s", chat_id, e)
        logger.info("[Feishu] Startup notification sent to %d chats", len(chats))

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
            log_level=lark.LogLevel.DEBUG,
        )

        # Monkey-patch _receive_message_loop for debugging (with auto-reconnect)
        import lark_oapi.ws.client as _wsc
        _orig_recv_loop = _wsc.Client._receive_message_loop
        async def _debug_recv_loop(self):
            import datetime as _dt
            with open("/tmp/feishu_raw_ws.log", "a") as _f:
                _f.write(f"[{_dt.datetime.now()}] recv_loop START\n")
            try:
                while True:
                    if self._conn is None:
                        with open("/tmp/feishu_raw_ws.log", "a") as _f:
                            _f.write(f"[{_dt.datetime.now()}] conn=None, exiting\n")
                        break
                    msg = await self._conn.recv()
                    with open("/tmp/feishu_raw_ws.log", "a") as _f:
                        _f.write(f"[{_dt.datetime.now()}] RECV {len(msg)}B\n")
                    import asyncio as _asyncio
                    _asyncio.get_event_loop().create_task(self._handle_message(msg))
            except Exception as e:
                with open("/tmp/feishu_raw_ws.log", "a") as _f:
                    _f.write(f"[{_dt.datetime.now()}] recv_loop EXIT, err: {e}\n")
                logger.error(self._fmt_log("receive message loop exit, err: {}", e))
                await self._disconnect()
                if self._auto_reconnect:
                    await self._reconnect()
                else:
                    raise e
        _wsc.Client._receive_message_loop = _debug_recv_loop

        # Start WS client in a dedicated thread with its own event loop
        # Mirrors Hermes's _run_official_feishu_ws_client pattern
        def _run_ws_in_thread():
            import lark_oapi.ws.client as ws_client_module
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws_client_module.loop = loop
            self._ws_thread_loop = loop
            retry_delay = 1
            max_delay = 120
            while self._running:
                try:
                    logger.info("[Feishu] WS client connecting...")
                    self._ws_client.start()
                except Exception as exc:
                    logger.error("[Feishu] WS stopped, reconnecting in %ds: %s", retry_delay, exc)
                if not self._running:
                    break
                import time as _time
                _time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            # Cleanup — only when _running is False (shutdown)
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
        import datetime as _dt
        with open("/tmp/feishu_event_debug.log", "a") as _f:
            _f.write(f"[{_dt.datetime.now()}] WebSocket client started, handler={event_handler}\n")
            _f.write(f"  handler type: {type(event_handler)}\n")
        logger.info("[Feishu] WebSocket client started in background thread")
        return True

    def _build_event_handler(self):
        """Build a Lark SDK event handler that dispatches to Dragon."""
        adapter = self  # capture for closure

        def _dispatch_event(event):
            """Called by Lark SDK on each inbound event."""
            import datetime as _dt
            with open("/tmp/feishu_dispatch.log", "a") as _f:
                _f.write(f"[{_dt.datetime.now()}] DISPATCH FIRED\n")
            logger.info("[Feishu] RAW EVENT: type=%s", getattr(event, 'type', 'N/A'))
            if not adapter._running:
                return
            try:
                event_type = getattr(event, 'type', '') or getattr(getattr(event, 'header', None), 'event_type', '')


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

    # ── Processing Status Reactions (Hermes-aligned) ──────────────

    async def _add_reaction(self, message_id: str, emoji_type: str) -> str:
        """Add a reaction emoji to a message. Returns reaction_id or empty."""
        if not message_id or not emoji_type:
            return ""
        token = await self._get_tenant_access_token()
        if not token:
            return ""
        try:
            url = f"{self.api_base}/im/v1/messages/{message_id}/reactions"
            body = {"reaction_type": {"emoji_type": emoji_type}}
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        rid = data.get("data", {}).get("reaction_id", "")
                        if rid:
                            logger.debug("[Feishu] Reaction %s added: %s", emoji_type, rid)
                        return rid
        except Exception:
            pass
        return ""

    async def _remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        """Remove a reaction. Returns True on success."""
        if not message_id or not reaction_id:
            return False
        token = await self._get_tenant_access_token()
        if not token:
            return False
        try:
            url = f"{self.api_base}/im/v1/messages/{message_id}/reactions/{reaction_id}"
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("code") == 0
        except Exception:
            pass
        return False

    async def on_processing_start(self, message_id: str) -> None:
        """Add Typing reaction when processing begins."""
        if not self._reactions_enabled or not message_id:
            return
        reaction_id = await self._add_reaction(message_id, _FEISHU_REACTION_IN_PROGRESS)
        if reaction_id:
            self._pending_processing_reactions[message_id] = reaction_id

    async def on_processing_complete(self, message_id: str, success: bool = True) -> None:
        """Remove Typing reaction, optionally add failure mark."""
        if not self._reactions_enabled or not message_id:
            return
        reaction_id = self._pending_processing_reactions.pop(message_id, "")
        if reaction_id:
            await self._remove_reaction(message_id, reaction_id)
        if not success:
            await self._add_reaction(message_id, _FEISHU_REACTION_FAILURE)

    async def _handle_ws_event(self, event: Any) -> None:
        """Process a WebSocket event in the main asyncio loop."""
        import datetime as _dh
        with open("/tmp/feishu_dispatch.log", "a") as _f:
            _f.write(f"[{_dh.datetime.now()}] HANDLE_WS ENTER\n")
        event_type = getattr(event, 'type', 'N/A')
        logger.info("[Feishu] Processing event: type=%s", event_type)

        message = await self._parse_ws_event(event)
        with open("/tmp/feishu_dispatch.log", "a") as _f:
            _f.write(f"[{_dh.datetime.now()}] PARSED: {'OK' if message else 'NONE'}\n")
        if message is None:
            logger.info("[Feishu] Event skipped: type=%s (not a message)", event_type)
            return

        logger.info(
            "[Feishu] Message: user=%s chat=%s text=%s",
            message.user_id, message.chat_id, message.content[:80],
        )
        # Track active chat for restart notification
        self._active_chats.add(message.chat_id)

        with open("/tmp/feishu_dispatch.log", "a") as _f:
            _f.write(f"[{_dh.datetime.now()}] HANDLER: {'SET' if self._message_handler else 'NONE'}\n")
            _f.write(f"[{_dh.datetime.now()}] handler type: {type(self._message_handler)}\n")
        if self._message_handler:
            try:
                # Check for voice commands
                voice_cmd_response = self._check_voice_command(message.content)
                if voice_cmd_response:
                    # Send command response directly
                    reply = PlatformReply(
                        content=voice_cmd_response,
                        chat_id=message.chat_id,
                        reply_to_message_id=message.message_id,
                    )
                    await self.send_message(reply)
                    # For /new or /task, create a fresh session
                    is_new = "/new" in message.content.lower() or "/reset" in message.content.lower() or "/clear" in message.content.lower()
                    is_task = message.content.lower().startswith("/task") or message.content.lower().startswith("/任务")
                    if is_new or is_task:
                        try:
                            from dragon.session import Session
                            import hashlib, time
                            new_sid = hashlib.sha256(
                                f"{message.platform}:{message.chat_id}:{time.time()}".encode()
                            ).hexdigest()[:12]
                            handler = self._message_handler
                            processor = getattr(handler, '__self__', None)
                            # Fallback: extract from closure (handler is plain function)
                            if processor is None and hasattr(handler, '__closure__') and handler.__closure__:
                                for cell in handler.__closure__:
                                    cc = cell.cell_contents
                                    if cc is not None and hasattr(cc, 'processor'):
                                        processor = cc.processor
                                        break
                            if processor and hasattr(processor, 'session_store') and processor.session_store:
                                if is_task:
                                    raw = message.content.strip()
                                    task_name = raw[5:].strip() if raw.lower().startswith("/task") else raw[3:].strip()
                                    sess = processor.session_store.create(new_sid)
                                    if task_name:
                                        processor.session_store.update_meta(new_sid, title=task_name)
                                else:
                                    processor.session_store.create(new_sid)
                                # Force new session for next message by storing hint
                                if hasattr(processor, '_next_session_ids'):
                                    processor._next_session_ids[message.chat_id] = new_sid
                        except Exception:
                            pass
                    return

                # ── STEER / QUEUE LOGIC ────────────────────────────
                # Check if processor is busy for this chat.
                # If so, queue the message as steer (injected mid-processing)
                # or as a regular queued message.
                from dragon.gateway.server import MessageProcessor
                handler = self._message_handler
                # Try to access the processor's steer/queue if it's a bound method
                processor = getattr(handler, '__self__', None)
                if processor and hasattr(processor, 'is_processing'):
                    chat_id = getattr(message, 'chat_id', '')
                    if processor.is_processing(chat_id):
                        # Busy — queue as steer to inject mid-processing
                        processor.queue_steer(chat_id, message.content)
                        logger.info(
                            "[Feishu] Busy, queued as steer: %s", message.content[:50]
                        )
                        await self._send_reaction(message.message_id or "", "OK")
                        return

                # Normal message handling — Hermes-style reactions
                msg_id = message.message_id or ""
                asyncio.create_task(self.on_processing_start(msg_id))

                # Register progress callback for periodic status updates
                handler = self._message_handler
                # Handler is a plain function (not bound method), so __self__ is None.
                # Extract the GatewayServer instance from its closure instead.
                processor = getattr(handler, '__self__', None)
                with open("/tmp/feishu_dispatch.log", "a") as _df:
                    _df.write(f"[{__import__('datetime').datetime.now()}] CLOSURE_DEBUG: __self__={processor}, has_closure={hasattr(handler, '__closure__')}\n")
                    if hasattr(handler, '__closure__') and handler.__closure__:
                        for i, cell in enumerate(handler.__closure__):
                            cc = cell.cell_contents
                            cc_type = type(cc).__name__
                            has_proc = hasattr(cc, 'processor') if cc is not None else False
                            _df.write(f"[{__import__('datetime').datetime.now()}] CLOSURE_DEBUG: cell[{i}] type={cc_type} has_processor={has_proc}\n")
                            if has_proc:
                                processor = cc.processor  # GatewayServer.processor = MessageProcessor
                                _df.write(f"[{__import__('datetime').datetime.now()}] CLOSURE_DEBUG: FOUND processor in cell[{i}]\n")
                                break
                    else:
                        _df.write(f"[{__import__('datetime').datetime.now()}] CLOSURE_DEBUG: NO closure\n")
                with open("/tmp/feishu_dispatch.log", "a") as _df:
                    _df.write(f"[{__import__('datetime').datetime.now()}] CHECK: processor={processor is not None} has_set_progress={hasattr(processor, 'set_progress_callback') if processor else 'N/A'} type={type(processor).__name__ if processor else 'None'}\n")
                if processor and hasattr(processor, 'set_progress_callback'):
                    async def _progress_cb(chat_id: str, text: str):
                        try:
                            _now = __import__('datetime').datetime.now()
                            with open("/tmp/feishu_dispatch.log", "a") as _f:
                                _f.write(f"[{_now}] PROGRESS_CB: firing text={text[:60]}\n")
                            result = await self.send_stream_progress(chat_id, text)
                            with open("/tmp/feishu_dispatch.log", "a") as _f:
                                _f.write(f"[{_now}] PROGRESS_CB: result={result!r}\n")
                        except Exception as e:
                            logger.warning("[Feishu] progress_cb error: %s", e)
                    processor.set_progress_callback(_progress_cb)
                    with open("/tmp/feishu_dispatch.log", "a") as _df:
                        _df.write(f"[{__import__('datetime').datetime.now()}] PROGRESS_CB: REGISTERED processor={type(processor).__name__}\n")

                # Register alert callback for CRITICAL/ALERT immediate push
                if processor and hasattr(processor, 'set_alert_callback'):
                    async def _alert_cb(chat_id: str, text: str):
                        try:
                            reply = PlatformReply(chat_id=chat_id, content=text)
                            ok = await self.send_message(reply)
                            if ok and self._last_reply_id:
                                self._last_alert_id = self._last_reply_id
                        except Exception as e:
                            logger.warning("[Feishu] alert_cb error: %s", e)
                    processor.set_alert_callback(_alert_cb)

                # Register edit callback for [EDIT] past-message updates
                if processor and hasattr(processor, 'set_edit_callback'):
                    async def _edit_cb(chat_id: str, target: str, new_text: str):
                        try:
                            # Resolve target to actual message_id
                            msg_id = ""
                            if target == "alert" and self._last_alert_id:
                                msg_id = self._last_alert_id
                            elif target == "progress" and self._last_progress_id:
                                msg_id = self._last_progress_id
                            elif target == "reply" and self._last_reply_id:
                                msg_id = self._last_reply_id
                            elif target == "last":
                                # Most recent of any type
                                msg_id = self._last_alert_id or self._last_progress_id or self._last_reply_id
                            if msg_id:
                                await self.edit_message(msg_id, new_text)
                        except Exception as e:
                            logger.warning("[Feishu] edit_cb error: %s", e)
                    processor.set_edit_callback(_edit_cb)

                success = True
                try:
                    # When voice is enabled, process with output_mode="voice"
                    # so the response is ready for audio delivery
                    open("/tmp/feishu_dispatch.log", "a").write(f"[{_dh.datetime.now()}] CALLING handler...\n")
                    user_vid = getattr(message, 'user_id', '')
                    if self.voice_enabled.get(user_vid, False) and self.voice_enabled.get('__global__', False):
                        handler = self._message_handler
                        # Extract GatewayServer from closure to access processor/system_prompt
                        gw_server = None
                        if hasattr(handler, "__closure__") and handler.__closure__:
                            for cell in handler.__closure__:
                                if hasattr(cell.cell_contents, "processor") and hasattr(cell.cell_contents, "system_prompt"):
                                    gw_server = cell.cell_contents
                                    break
                        if gw_server:
                            reply = await gw_server.processor.process(
                                message, gw_server.system_prompt, output_mode="voice"
                            )
                        else:
                            reply = await self._message_handler(message)
                    else:
                        reply = await self._message_handler(message)
                    with open("/tmp/feishu_dispatch.log", "a") as _f:
                        _f.write(f"[{_dh.datetime.now()}] REPLY rcvd: {reply.content[:50] if reply and reply.content else 'EMPTY'}\n")
                    await self.send_message(reply)
                except Exception:
                    success = False
                    raise
                finally:
                    asyncio.create_task(self.on_processing_complete(msg_id, success))
                
                # If voice is enabled, synthesize and send audio
                if self.voice_enabled.get(getattr(message, 'user_id', ''), False) and reply and reply.content:
                    await self._send_voice_reply(message.chat_id, reply.content, reply.reply_to_message_id)
                    
            except Exception as exc:
                logger.exception("[Feishu] Message handler error: %s", exc)
                # Send error notification to user
                try:
                    await self.send_message(PlatformReply(
            
                        chat_id=getattr(message, "chat_id", ""),
                        content=f"\u26a0\ufe0f 处理消息时出错: {str(exc)[:200]}",
                    ))
                except Exception:
                    pass
        else:
            logger.warning(
                "[Feishu] No message handler registered — "
                "call adapter.register_handler()! Message from %s dropped.",
                message.user_id,
            )

    # ── Voice Commands ────────────────────────────────────────────

    def _check_voice_command(self, text: str) -> Optional[str]:
        """Check if message is a command. Returns response text or None."""
        text_lower = text.strip().lower()
        # Session commands
        if text_lower in ("/new", "/reset", "/clear", "/新会话", "/重置"):
            return "🔄 会话已重置，下一轮对话将使用新上下文。"
        if text_lower.startswith("/task") or text_lower.startswith("/任务"):
            task_name = text.strip()[5:].strip() if text_lower.startswith("/task") else text.strip()[3:].strip()
            return f"✅ 新任务已创建：{task_name or '未命名'}"
        # Voice commands
        if text_lower in ("/voice on", "/voice off", "/语音 on", "/语音 off", "/语音 开", "/语音 关"):
            if "off" in text_lower or "关" in text_lower:
                self.voice_enabled[user_id] = False
                return "🔇 语音模式已关闭"
            else:
                self.voice_enabled[user_id] = True
                return "🔊 语音模式已开启，回复将附带语音"
        return None

    # ── Voice Synthesis ───────────────────────────────────────────

    async def _send_voice_reply(self, chat_id: str, text: str, reply_to_msg_id: str = ""):
        """Synthesize text to speech and send as audio message."""
        import tempfile
        import os as _os
        
        try:
            # Truncate long text for voice (max ~500 chars for reasonable audio length)
            voice_text = text[:4000] if len(text) > 500 else text
            
            # Use shared VoiceEngine from processor if available, else create one
            engine = self._voice_engine
            if engine is None:
                from dragon.voice_engine import VoiceEngine
                engine = VoiceEngine(voice="zh-CN-XiaoxiaoNeural")
            await engine.start()
            engine.consume(voice_text)
            await engine.flush()
            
            audio_item = await engine.next_audio()
            if audio_item is None:
                logger.warning("[Feishu] Voice synthesis produced no audio")
                await engine.stop()
                return
            
            _, audio_bytes = audio_item
            await engine.stop()
            
            if not audio_bytes or len(audio_bytes) < 100:
                return
            
            # Save to temp file
            tmp_path = _os.path.join(tempfile.gettempdir(), f"dragon_voice_{int(time.time())}.mp3")
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            
            # Send as audio message
            success = await self.send_audio_message(
                chat_id=chat_id,
                audio_path=tmp_path,
                reply_to_message_id=reply_to_msg_id,
                duration_ms=0,  # Feishu will calculate
            )
            
            # Cleanup
            try:
                _os.remove(tmp_path)
            except OSError:
                pass
            
            if not success:
                logger.warning("[Feishu] Failed to send voice reply")
                
        except Exception as e:
            logger.exception("[Feishu] Voice synthesis failed: %s", e)

    async def _parse_ws_event(self, event: Any) -> Optional[PlatformMessage]:
        """Parse a Lark SDK WebSocket event into PlatformMessage."""
        import datetime as _dp
        _log = lambda msg: open("/tmp/feishu_dispatch.log", "a").write(f"[{_dp.datetime.now()}] PARSE: {msg}\n")
        try:
            # Check event type
            _log(f"event type attr: {getattr(event, 'type', 'MISSING')}")
            _log(f"event schema: {getattr(event, 'schema', 'MISSING')}")
            _log(f"has event: {hasattr(event, 'event')}")
            event_type = getattr(event, 'type', '') or getattr(getattr(event, 'header', None), 'event_type', '')
            _log(f"resolved event_type: {event_type!r}")
            _log("type check PASSED, proceeding")
            _log(f"evt={getattr(event, 'event', 'NONE')}")

            if 'message' not in event_type and 'card' not in event_type and 'edited' not in event_type:
                _log(f'type check FAILED: {event_type}')
                return None
            # Detect edited messages and annotate
            is_edited = 'edit' in event_type.lower()

            evt = getattr(event, 'event', None)
            if evt is None:
                _log('evt is None')
                return None

            msg = getattr(evt, 'message', None)
            # Lark SDK v2: P2ImMessageReceiveV1Data uses 'event' nesting
            if msg is None and hasattr(evt, 'event'):
                inner = evt.event
                msg = getattr(inner, 'message', None)
                if msg is None:
                    _log(f'msg is None, evt class={type(evt).__name__}, has event={hasattr(evt, "event")}')
                    # Dump attributes for debugging
                    for attr in dir(evt):
                        if not attr.startswith('_'):
                            try:
                                val = getattr(evt, attr)
                                _log(f'  evt.{attr}={type(val).__name__}')
                            except:
                                pass
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
                # If text is empty, extract from rich text content blocks
                if not text and "content" in content_obj:
                    blocks = content_obj.get("content", [])
                    parts = []
                    for block in blocks:
                        if isinstance(block, list):
                            for item in block:
                                if isinstance(item, dict) and item.get("tag") == "text":
                                    parts.append(item.get("text", ""))
                    text = "".join(parts)
            except (json.JSONDecodeError, TypeError):
                text = str(content_raw)

            image_key = content_obj.get("image_key", "")
            if image_key:
                local_path = await self._download_image(image_key, message_id)
                if local_path:
                    text = (text + f"\n[图片已下载: {local_path}]") if text else f"[收到图片]\n[已下载: {local_path}]"
                else:
                    text = (text + "\n[图片下载失败]") if text else "[收到图片]"
            file_key = content_obj.get("file_key", "")
            if file_key:
                file_name = content_obj.get("file_name", file_key)
                local_path = await self._download_file(file_key, message_id)
                if local_path:
                    text = (text + f"\n[文件已下载: {local_path}]") if text else f"[收到文件: {file_name}]\n[已下载: {local_path}]"
                    # Track file for this chat
                    self._track_file(chat_id, local_path)
                else:
                    text = (text + f"\n[文件下载失败: {file_name}]") if text else f"[收到文件: {file_name}]"

            if not text:
                _log(f'text empty: content_raw={content_raw[:100]}')
                return None

            final_text = text.strip()
            if is_edited:
                final_text = "[用户编辑了消息]\n" + final_text

            # Derive stable session_id from chat_id (survives gateway restarts)
            _session_id = hashlib.sha256(f"feishu:{chat_id}".encode()).hexdigest()[:12]
            return PlatformMessage(
                platform="feishu",
                chat_id=chat_id,
                user_id=sender_id,
                content=final_text,
                message_id=message_id,
                raw={"event_type": event_type},
                timestamp=time.time(),
            )
        except Exception as exc:
            logger.exception("[Feishu] Failed to parse WS event")
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
            # Handle image/file in webhook mode (parity with WS mode)
            image_key = content_obj.get("image_key", "")
            file_key = content_obj.get("file_key", "")
            if image_key:
                local_path = await self._download_image(image_key, message_id or "")
                if local_path:
                    text = (text + f"\n[图片已下载: {local_path}]") if text else f"[收到图片]\n[已下载: {local_path}]"
            if file_key:
                local_path = await self._download_file(file_key, message_id or "")
                if local_path:
                    text = (text + f"\n[文件已下载: {local_path}]") if text else f"[收到文件]\n[已下载: {local_path}]"
                    self._track_file(chat_id, local_path)
        except (json.JSONDecodeError, TypeError):
            text = str(content_raw)

        if not text:
            return None

        return PlatformMessage(
            chat_id=chat_id,
            user_id=sender_id,
            content=text,
            message_id=message_id,
            thread_id=thread_id,
            raw=body,
            timestamp=float(header.get("create_time", int(time.time() * 1000))) / 1000,
        )

    # ── Send Message ──────────────────────────────────────────────

    _FEISHU_MSG_MAX_LEN = 3800  # Safe limit for Feishu markdown content

    async def _send_chunk(self, token: str, chat_id: str, text: str,
                          msg_type: str = "text",
                          reply_to_msg_id: str = "") -> str:
        """Send a single message chunk. Returns message_id or empty."""
        content_json = json.dumps({"text": text})
        if reply_to_msg_id:
            url = f"{self.api_base}/im/v1/messages/{reply_to_msg_id}/reply"
            body = {"content": content_json, "msg_type": msg_type}
        else:
            url = f"{self.api_base}/im/v1/messages?receive_id_type=chat_id"
            body = {
                "receive_id": chat_id,
                "content": content_json,
                "msg_type": msg_type,
            }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {}).get("message_id", "")
        return ""

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a text message reply via Feishu HTTP API.
        Automatically splits long messages to stay under Feishu's limit.
        """
        token = await self._get_tenant_access_token()
        if not token:
            logger.error("[Feishu] Failed to get tenant access token")
            return False

        chat_id = reply.chat_id
        if not chat_id:
            logger.error("[Feishu] No chat_id in reply")
            return False

        # ── MEDIA: path support ──
        content_text = reply.content or ""
        msg_type = "text"
        if "MEDIA:" in content_text:
            import re as _re
            m = _re.search(r"MEDIA:(/[\w\-_./]+)", content_text)
            if m:
                media_path = m.group(1)
                clean_text = _re.sub(r"MEDIA:\S+", "", content_text).strip()
                if os.path.exists(media_path):
                    file_key = await self.upload_media(media_path)
                    if file_key:
                        ext = os.path.splitext(media_path)[1].lower()
                        if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                            content_text = json.dumps({"image_key": file_key})
                            msg_type = "image"
                        else:
                            content_text = json.dumps({"file_key": file_key})
                            msg_type = "file"
                        logger.info("[Feishu] MEDIA sent: %s as %s", media_path, msg_type)
                    else:
                        logger.error("[Feishu] MEDIA upload failed: %s", media_path)
                        content_text = json.dumps({"text": "[MEDIA upload failed] " + clean_text})
                else:
                    logger.error("[Feishu] MEDIA not found: %s", media_path)
                    content_text = json.dumps({"text": "[File not found: " + media_path + "] " + clean_text})
            else:
                content_text = json.dumps({"text": content_text})
        else:
            content_text = json.dumps({"text": content_text})

        # ── Check if content needs splitting ──
        raw_text = reply.content or ""
        needs_split = (
            msg_type == "text" and len(raw_text) > self._FEISHU_MSG_MAX_LEN
        )

        if needs_split:
            logger.info(
                "[Feishu] send_message (split): chat=%s len=%d",
                chat_id, len(raw_text)
            )
            # Split on paragraph boundaries, then line boundaries, then word
            chunks = []
            remaining = raw_text
            while remaining:
                if len(remaining) <= self._FEISHU_MSG_MAX_LEN:
                    chunks.append(remaining)
                    break
                # Find a good split point
                split_at = self._FEISHU_MSG_MAX_LEN
                # Try to split at paragraph
                para_break = remaining.rfind("\n\n", 0, split_at)
                if para_break > self._FEISHU_MSG_MAX_LEN // 2:
                    split_at = para_break + 2
                else:
                    line_break = remaining.rfind("\n", 0, split_at)
                    if line_break > self._FEISHU_MSG_MAX_LEN // 2:
                        split_at = line_break + 1
                chunk = remaining[:split_at].strip()
                if chunk:
                    chunks.append(chunk)
                remaining = remaining[split_at:].strip()

            # Send all chunks
            first_id = ""
            for i, chunk in enumerate(chunks):
                chunk_text = json.dumps({"text": chunk})
                rid = "" if i == 0 else (first_id or reply.reply_to_message_id or "")
                msg_id = await self._send_chunk(
                    token, chat_id, chunk, msg_type="text", reply_to_msg_id=rid
                )
                if i == 0:
                    first_id = msg_id
                    if msg_id:
                        self._last_reply_id = msg_id
                        self._sent_ids.append(msg_id)
                        if len(self._sent_ids) > 10:
                            self._sent_ids.pop(0)
                import asyncio
                if i < len(chunks) - 1:
                    await asyncio.sleep(0.3)  # Rate limit between chunks
            logger.info("[Feishu] send_message split: %d chunks sent", len(chunks))
            return bool(first_id)

        # ── Single message (no split needed) ──
        logger.info(
            "[Feishu] send_message: chat=%s reply_to=%s type=%s len=%d",
            chat_id, reply.reply_to_message_id or "(none)", msg_type, len(reply.content or "")
        )

        if reply.reply_to_message_id:
            url = f"{self.api_base}/im/v1/messages/{reply.reply_to_message_id}/reply"
            body = {"content": content_text, "msg_type": msg_type}
        else:
            url = f"{self.api_base}/im/v1/messages?receive_id_type=chat_id"
            body = {
                "receive_id": chat_id,
                "content": content_text,
                "msg_type": msg_type,
            }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # ── Retry with exponential backoff ──
        import asyncio as _asyncio
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(url, headers=headers, json=body)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("code") == 0:
                            msg_id = data.get("data", {}).get("message_id", "")
                            if msg_id:
                                self._last_reply_id = msg_id
                                self._sent_ids.append(msg_id)
                                if len(self._sent_ids) > 10:
                                    self._sent_ids.pop(0)
                            logger.info("[Feishu] send_message OK: msg_id=%s", msg_id)
                            return True
                        logger.error("[Feishu] API error: code=%s msg=%s", data.get("code"), data.get("msg"))
                    elif resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", "5"))
                        logger.warning("[Feishu] Rate limited (429), waiting %ds...", retry_after)
                        await _asyncio.sleep(retry_after)
                        continue
                    else:
                        logger.error("[Feishu] HTTP %d: %s", resp.status_code, resp.text[:200])
            except Exception as e:
                logger.exception("[Feishu] send_message attempt %d/3 failed: %s", attempt + 1, e)
            if attempt < 2:
                await _asyncio.sleep(2 ** attempt)

        return False

    async def edit_message(self, message_id: str, new_content: str) -> bool:
        """Edit a previously sent message. Uses Feishu PUT API."""
        if not message_id or not new_content:
            return False
        token = await self._get_tenant_access_token()
        if not token:
            return False
        url = f"{self.api_base}/im/v1/messages/{message_id}"
        body = {
            "content": json.dumps({"text": new_content}),
            "msg_type": "text",
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.put(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        logger.info("[Feishu] Edited message %s", message_id)
                        return True
                    logger.error("[Feishu] Edit error: %s", data.get("msg"))
                else:
                    logger.error("[Feishu] Edit HTTP %d: %s", resp.status_code, resp.text[:300])
        except Exception as e:
            logger.exception("[Feishu] Edit failed: %s", e)
        return False

    # ── Stream Progress ──────────────────────────────────────────

    async def send_stream_progress(self, chat_id: str, text: str,
                                     is_final: bool = False) -> str:
        """Send incremental streaming progress — edits first message in-place.

        First call sends a new message and saves its ID.
        Subsequent calls edit that message to avoid chat spam.
        When is_final=True, marks complete and clears tracking.
        """
        prefix = "" if is_final else "\U0001f4dd "
        full_text = f"{prefix}{text}"
        if self._last_progress_id:
            # Edit existing progress message in-place (cooldown: min 15s between edits)
            import time
            now = time.time()
            if now - self._last_progress_edit_time < 15 and not is_final:
                return self._last_progress_id  # skip edit, within cooldown
            ok = await self.edit_message(self._last_progress_id, full_text)
            self._last_progress_edit_time = now
            if is_final:
                self._last_progress_id = ""
            elif not ok:
                # Edit failed (e.g. message deleted) — clear ID so next call sends new msg
                logger.warning("[Feishu] Progress edit failed, resetting progress_id")
                self._last_progress_id = ""
            return self._last_progress_id if ok else ""
        else:
            # First call: send new message
            reply = PlatformReply(
                chat_id=chat_id,
                content=full_text,
            )
            ok = await self.send_message(reply)
            if ok:
                self._last_progress_id = self._last_reply_id
                return self._last_reply_id
            return ""

    # ── Send Audio Message ────────────────────────────────────────

    async def send_audio_message(
        self, 
        chat_id: str, 
        audio_path: str, 
        reply_to_message_id: str = "",
        duration_ms: int = 0,
    ) -> bool:
        """Send an audio (voice) message via Feishu HTTP API.
        
        Returns True on success, False on failure.
        """
        token = await self._get_tenant_access_token()
        if not token:
            logger.error("[Feishu] Failed to get token for audio message")
            return False
        
        import os as _os
        if not _os.path.exists(audio_path):
            logger.error("[Feishu] Audio file not found: %s", audio_path)
            return False
        
        # Upload the audio file
        file_key = await self.upload_media(audio_path)
        if not file_key:
            logger.error("[Feishu] Failed to upload audio file")
            return False
        
        file_name = _os.path.basename(audio_path)
        
        # Send as audio message (Feishu supports audio msg_type)
        content = json.dumps({
            "file_key": file_key,
            "file_name": file_name,
            "duration": duration_ms,
        })
        
        if reply_to_message_id:
            url = f"{self.api_base}/im/v1/messages/{reply_to_message_id}/reply"
            body = {"content": content, "msg_type": "audio"}
        else:
            url = f"{self.api_base}/im/v1/messages?receive_id_type=chat_id"
            body = {
                "receive_id": chat_id,
                "content": content,
                "msg_type": "audio",
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
                        logger.info("[Feishu] Audio message sent: %s", audio_path)
                        return True
                    # Fall back to file message type
                    body["msg_type"] = "file"
                    resp2 = await client.post(url, headers=headers, json=body)
                    if resp2.status_code == 200:
                        data2 = resp2.json()
                        if data2.get("code") == 0:
                            logger.info("[Feishu] Audio sent as file: %s", audio_path)
                            return True
                    logger.error("[Feishu] Audio API error: %s", data.get("msg"))
                else:
                    logger.error("[Feishu] Audio HTTP %d", resp.status_code)
        except Exception as e:
            logger.exception("[Feishu] Failed to send audio: %s", e)
        
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

    
    async def _download_image(self, image_key: str, message_id: str = ""):
        """Download an image from Feishu by image_key, return local file path."""
        import os as _os
        token = await self._get_tenant_access_token()
        if not token:
            logger.error("[Feishu] No token for image download")
            return None
        url = f"{self.api_base}/im/v1/messages/{message_id}/resources/{image_key}?type=image"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if resp.status_code == 200:
                    ct = resp.headers.get("content-type", "")
                    ext = "jpg"
                    if "png" in ct: ext = "png"
                    elif "gif" in ct: ext = "gif"
                    elif "webp" in ct: ext = "webp"
                    save_dir = "dragon_data/uploads/images"
                    _os.makedirs(save_dir, exist_ok=True)
                    filepath = _os.path.join(save_dir, f"{image_key[:20]}.{ext}")
                    with open(filepath, "wb") as f:
                        f.write(resp.content)
                    logger.info("[Feishu] Downloaded image: %s (%d bytes)", filepath, len(resp.content))
                    return filepath
                else:
                    logger.error("[Feishu] Image download failed: HTTP %s", resp.status_code)
        except Exception:
            logger.exception("[Feishu] Image download error")
        return None

    async def _download_file(self, file_key: str, message_id: str = ""):
        """Download from Feishu by file_key, return local path."""
        import os as _os, re
        token = await self._get_tenant_access_token()
        if not token:
            logger.error("[Feishu] No token for file download")
            return None
        url = f"{self.api_base}/im/v1/messages/{message_id}/resources/{file_key}?type=file"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
                if resp.status_code == 200:
                    cd = resp.headers.get("content-disposition", "")
                    fname = file_key[:20]
                    m = re.search(r'filename[*]?="?([^";]+)', cd)
                    if m:
                        fname = m.group(1)
                    save_dir = "dragon_data/uploads/files"
                    _os.makedirs(save_dir, exist_ok=True)
                    filepath = _os.path.join(save_dir, fname)
                    with open(filepath, "wb") as fw:
                        fw.write(resp.content)
                    logger.info("[Feishu] Downloaded file: %s (%d bytes)", filepath, len(resp.content))
                    return filepath
                else:
                    logger.error("[Feishu] File download failed: HTTP %s", resp.status_code)
        except Exception:
            logger.exception("[Feishu] File download error")
        return None

    async def _upload_to_signoss(self, file_path: str):
        """Upload file to OSS via signOSS, return public URL."""
        import subprocess as _sp
        import json as _json
        import os as _os
        signoss_key = _os.getenv("SIGNOSS_API_KEY", "sk-your-signoss-key")
        from dragon._domain_loader import OSS_BASE_URL, OSS_FALLBACK_URL
        for base_url in [f"{OSS_BASE_URL}", OSS_FALLBACK_URL]:
            try:
                result = _sp.run(
                    ["curl", "-sk", "--max-time", "30", "-X", "POST", f"{base_url}/upload",
                     "-H", f"X-API-Key: {signoss_key}",
                     "-F", "category=feishu_images",
                     "-F", f"file=@{file_path}"],
                    capture_output=True, text=True, timeout=35
                )
                if result.returncode == 0 and "success" in result.stdout:
                    data = _json.loads(result.stdout)
                    files = data.get("files", [])
                    if files:
                        url = files[0].get("url", "")
                        logger.info("[Feishu] Uploaded to signOSS: %s", url)
                        return url
            except Exception as e:
                logger.debug("[Feishu] signOSS attempt %s failed: %s", base_url, e)
        return None

    def _track_file(self, chat_id: str, file_path: str):
        """Record downloaded file for a chat so Dragon remembers it across turns."""
        import json as _json, os as _os
        tracker = _os.path.join("dragon_data", "uploads", ".chat_files.json")
        _os.makedirs(_os.path.dirname(tracker), exist_ok=True)
        data = {}
        if _os.path.exists(tracker):
            try:
                with open(tracker, "r") as f:
                    data = _json.load(f)
            except Exception:
                pass
        files = data.get(chat_id, [])
        if file_path not in files:
            files.append(file_path)
        data[chat_id] = files
        with open(tracker, "w") as f:
            _json.dump(data, f, ensure_ascii=False)

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
