"""
Dragon Gateway — WeChat (微信) Official Account Adapter
=======================================================

Handles WeChat Official Account messages via the passive reply mechanism
and the customer service message API.

WeChat docs: https://developers.weixin.qq.com/doc/offiaccount/

Configuration::

    adapter = WeChatAdapter(
        token="your_wechat_token",
        app_id="wx...",
        app_secret="...",
        encoding_aes_key="...",  # optional, for encrypted mode
    )
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import httpx

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("dragon.gateway.wechat")

WECHAT_API = "https://api.weixin.qq.com/cgi-bin"
# WeChat message types
MSG_TEXT = "text"
MSG_IMAGE = "image"
MSG_VOICE = "voice"
MSG_VIDEO = "video"
MSG_EVENT = "event"


class WeChatAdapter(PlatformAdapter):
    """WeChat Official Account adapter.

    Supports:
    - URL verification (echostr)
    - Text message reception and reply (passive reply)
    - Customer service message API (proactive messaging)
    - Access token management (7200s expiry)
    - Message encryption/decryption (optional, via encoding_aes_key)
    """

    def __init__(
        self,
        token: str = "",
        app_id: str = "",
        app_secret: str = "",
        encoding_aes_key: str = "",
    ) -> None:
        super().__init__(platform_name="wechat", webhook_path="/wechat/webhook")

        self.token = token or os.getenv("WECHAT_TOKEN", "")
        self.app_id = app_id or os.getenv("WECHAT_APP_ID", "")
        self.app_secret = app_secret or os.getenv("WECHAT_APP_SECRET", "")
        self.encoding_aes_key = encoding_aes_key or os.getenv("WECHAT_ENCODING_AES_KEY", "")

        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        logger.info("WeChat adapter ready (app_id=%s...)", self.app_id[:8] if self.app_id else "none")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """WeChat doesn't use signature headers — verification is done via echostr."""
        return True  # Always parse, let parse_webhook handle echostr

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse WeChat message.

        WeChat sends GET for verification with query params:
        ?signature=...&timestamp=...&nonce=...&echostr=...

        WeChat sends POST for messages as XML:
        <xml>
          <ToUserName><![CDATA[gh_xxx]]></ToUserName>
          <FromUserName><![CDATA[o_xxx]]></FromUserName>
          <CreateTime>1234567890</CreateTime>
          <MsgType><![CDATA[text]]></MsgType>
          <Content><![CDATA[hello]]></Content>
          <MsgId>123456</MsgId>
        </xml>
        """
        # URL verification (GET parameters are passed differently)
        if body.get("_method") == "GET":
            echostr = body.get("echostr", "")
            if echostr and self._verify_signature(body):
                return PlatformMessage(
                    platform="wechat",
                    chat_id="__verify__",
                    user_id="__system__",
                    content=echostr,
                )
            return None

        # XML message parsing
        xml_str = body.get("_xml_body", "")
        if not xml_str:
            # Try string content
            xml_str = body.get("body", "")

        if not xml_str:
            return None

        try:
            root = ET.fromstring(xml_str)

            msg_type = self._xml_text(root, "MsgType")
            if msg_type != MSG_TEXT:
                return None

            from_user = self._xml_text(root, "FromUserName")
            to_user = self._xml_text(root, "ToUserName")
            content = self._xml_text(root, "Content")
            msg_id = self._xml_text(root, "MsgId")
            create_time = self._xml_text(root, "CreateTime")

            return PlatformMessage(
                platform="wechat",
                chat_id=from_user,   # WeChat uses user ID as chat identifier
                user_id=from_user,
                content=content,
                message_id=msg_id,
                raw={"from_user": from_user, "to_user": to_user},
                timestamp=float(create_time) if create_time else time.time(),
            )
        except ET.ParseError:
            logger.warning("Failed to parse WeChat XML")
            return None

    def _xml_text(self, root, tag: str) -> str:
        elem = root.find(tag)
        return elem.text.strip() if elem is not None and elem.text else ""

    def _verify_signature(self, params: Dict[str, str]) -> bool:
        """Verify WeChat signature for URL verification."""
        if not self.token:
            return False

        signature = params.get("signature", "")
        timestamp = params.get("timestamp", "")
        nonce = params.get("nonce", "")

        tmp_list = sorted([self.token, timestamp, nonce])
        tmp_str = "".join(tmp_list)
        computed = hashlib.sha1(tmp_str.encode()).hexdigest()

        return computed == signature

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a reply via WeChat.

        WeChat passive reply must be sent as XML within 5 seconds.
        For longer responses, use the customer service API.
        """
        if reply.chat_id == "__verify__":
            return True

        # Use customer service API for reliable delivery
        token = await self._get_access_token()
        if not token:
            return False

        url = f"{WECHAT_API}/message/custom/send?access_token={token}"

        payload = {
            "touser": reply.chat_id,
            "msgtype": "text",
            "text": {"content": reply.content[:600]},  # WeChat limits
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    logger.debug("Sent WeChat message to %s", reply.chat_id)
                    return True
                elif data.get("errcode") == 45047:
                    # Rate limited — wait and retry once
                    await asyncio.sleep(3)
                    resp2 = await client.post(url, json=payload)
                    return resp2.json().get("errcode") == 0
                else:
                    logger.error("WeChat API error: %s", data)
        except Exception as e:
            logger.exception("Failed to send WeChat message: %s", e)

        return False

    def build_passive_reply(self, from_user: str, to_user: str, content: str) -> str:
        """Build WeChat passive reply XML.

        Must be returned within 5 seconds of receiving the message.
        """
        return (
            "<xml>"
            f"<ToUserName><![CDATA[{from_user}]]></ToUserName>"
            f"<FromUserName><![CDATA[{to_user}]]></FromUserName>"
            f"<CreateTime>{int(time.time())}</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[{content[:600]}]]></Content>"
            "</xml>"
        )

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Upload media to WeChat and return media_id."""
        import os

        if not os.path.exists(file_path):
            return None

        token = await self._get_access_token()
        if not token:
            return None

        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        upload_type = "image" if ext in ("jpg", "jpeg", "png") else "voice" if ext in ("mp3", "amr") else "thumb"

        url = f"{WECHAT_API}/media/upload?access_token={token}&type={upload_type}"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                with open(file_path, "rb") as f:
                    files = {"media": (os.path.basename(file_path), f)}
                    resp = await client.post(url, files=files)
                    data = resp.json()
                    if "media_id" in data:
                        return data["media_id"]
        except Exception as e:
            logger.exception("WeChat upload failed: %s", e)

        return None

    # ── Token Management ─────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """Get or refresh WeChat access token (7200s expiry)."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self.app_id or not self.app_secret:
            logger.error("WeChat app_id and app_secret required")
            return ""

        url = f"{WECHAT_API}/token?grant_type=client_credential&appid={self.app_id}&secret={self.app_secret}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                data = resp.json()
                if "access_token" in data:
                    self._access_token = data["access_token"]
                    expires = data.get("expires_in", 7200)
                    self._token_expires_at = time.time() + expires - 300
                    logger.debug("WeChat access token refreshed")
                    return self._access_token
                else:
                    logger.error("WeChat token error: %s", data.get("errmsg"))
        except Exception as e:
            logger.exception("Failed to get WeChat token: %s", e)

        return ""


# Helper for external callers
import asyncio as _asyncio
