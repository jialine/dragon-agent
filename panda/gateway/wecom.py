"""
Panda Gateway — WeCom (企业微信) Platform Adapter
=================================================

Handles WeCom (WeChat Work) bot messages via the corporate API.

WeCom docs: https://developer.work.weixin.qq.com/document/

Configuration::

    adapter = WeComAdapter(
        corp_id="ww...",               # or set WECOM_CORP_ID env
        corp_secret="...",             # or set WECOM_CORP_SECRET env
        token="...",                   # or set WECOM_TOKEN env
        encoding_aes_key="...",        # or set WECOM_ENCODING_AES_KEY env
    )
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

import httpx

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.wecom")

WECOM_API = "https://qyapi.weixin.qq.com/cgi-bin"


class WeComAdapter(PlatformAdapter):
    """WeCom (企业微信) adapter with echostr verification and message handling."""

    MSG_TEXT = "text"

    def __init__(
        self,
        corp_id: str = "",
        corp_secret: str = "",
        token: str = "",
        encoding_aes_key: str = "",
    ) -> None:
        super().__init__(platform_name="wecom", webhook_path="/wecom/webhook")

        self.corp_id = corp_id or os.getenv("WECOM_CORP_ID", "")
        self.corp_secret = corp_secret or os.getenv("WECOM_CORP_SECRET", "")
        self.token = token or os.getenv("WECOM_TOKEN", "")
        self.encoding_aes_key = encoding_aes_key or os.getenv("WECOM_ENCODING_AES_KEY", "")

        self._access_token: str = ""
        self._token_expires_at: float = 0.0

        logger.info("WeCom adapter ready (corp_id=%s...)", self.corp_id[:8] if self.corp_id else "none")

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """WeCom verification is via echostr query parameter on GET."""
        return True

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse WeCom webhook callback.

        Verification (GET)::
            {"_method": "GET", "msg_signature": "...", "timestamp": "...",
             "nonce": "...", "echostr": "..."}

        Message (POST) — decrypted XML::
            <xml>
              <ToUserName><![CDATA[ww...]]></ToUserName>
              <FromUserName><![CDATA[userid]]></FromUserName>
              <MsgType><![CDATA[text]]></MsgType>
              <Content><![CDATA[hello]]></Content>
              <MsgId>123</MsgId>
            </xml>
        """
        # URL verification (GET)
        if body.get("_method") == "GET":
            echostr = body.get("echostr", "")
            if echostr and self._verify_url(body):
                decrypted = self._decrypt_echostr(echostr)
                return PlatformMessage(
                    platform="wecom",
                    chat_id="__challenge__",
                    user_id="__system__",
                    content=decrypted or echostr,
                )
            return None

        # POST: parse XML body
        xml_raw = body.get("_xml_body", "") or body.get("body", "")
        if not xml_raw:
            return None

        try:
            root = ET.fromstring(xml_raw)
        except ET.ParseError:
            logger.warning("Failed to parse WeCom XML")
            return None

        # Decrypt if needed
        encrypt_elem = root.find("Encrypt")
        if encrypt_elem is not None and encrypt_elem.text and self.encoding_aes_key:
            decrypted = self._decrypt_msg(encrypt_elem.text, self.encoding_aes_key)
            if decrypted:
                try:
                    root = ET.fromstring(decrypted)
                except ET.ParseError:
                    return None

        msg_type = self._xml_text(root, "MsgType")
        if msg_type != self.MSG_TEXT:
            return None

        from_user = self._xml_text(root, "FromUserName")
        content = self._xml_text(root, "Content")
        msg_id = self._xml_text(root, "MsgId")

        if not content:
            return None

        return PlatformMessage(
            platform="wecom",
            chat_id=from_user,
            user_id=from_user,
            content=content,
            message_id=msg_id,
            raw=body,
        )

    @staticmethod
    def _xml_text(root, tag: str) -> str:
        elem = root.find(tag)
        return elem.text.strip() if elem is not None and elem.text else ""

    def _verify_url(self, params: Dict[str, str]) -> bool:
        """Verify WeCom URL signature."""
        if not self.token:
            return False
        sig = params.get("msg_signature", "")
        ts = params.get("timestamp", "")
        nonce = params.get("nonce", "")
        tmp = sorted([self.token, ts, nonce])
        return hashlib.sha1("".join(tmp).encode()).hexdigest() == sig

    def _decrypt_msg(self, encrypted: str, aes_key: str) -> Optional[str]:
        """Decrypt WeCom message using AES-256-CBC."""
        try:
            from Crypto.Cipher import AES
        except ImportError:
            logger.warning("pycryptodome not installed, cannot decrypt WeCom messages")
            return None

        key = base64.b64decode(aes_key + "=")
        raw = base64.b64decode(encrypted)
        cipher = AES.new(key, AES.MODE_CBC, key[:16])
        plain = cipher.decrypt(raw)
        # Remove PKCS#7 padding
        pad = plain[-1]
        plain = plain[:-pad]
        # Extract: random(16) + msg_len(4) + msg + corp_id
        content = plain[20:]
        return content.decode("utf-8", errors="replace")

    def _decrypt_echostr(self, echostr: str) -> str:
        """Decrypt echostr for URL verification."""
        try:
            from Crypto.Cipher import AES
            key = base64.b64decode(self.encoding_aes_key + "=")
            raw = base64.b64decode(echostr)
            cipher = AES.new(key, AES.MODE_CBC, key[:16])
            plain = cipher.decrypt(raw)
            pad = plain[-1]
            plain = plain[:-pad]
            return plain[16:].decode("utf-8", errors="replace")
        except Exception:
            return echostr

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send a message via WeCom message API."""
        token = await self._get_access_token()
        if not token:
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        url = f"{WECOM_API}/message/send?access_token={token}"

        payload = {
            "touser": chat_id,
            "msgtype": "text",
            "agentid": int(os.getenv("WECOM_AGENT_ID", "0")),
            "text": {"content": reply.content[:2048]},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    logger.debug("Sent WeCom message to %s", chat_id)
                    return True
                logger.error("WeCom API error: %s", data)
        except Exception as e:
            logger.exception("Failed to send WeCom message: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Not implemented — returns None."""
        return None

    # ── Token Management ──────────────────────────────────────────

    async def _get_access_token(self) -> str:
        """Get or refresh WeCom access token."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self.corp_id or not self.corp_secret:
            logger.error("WeCom corp_id and corp_secret required")
            return ""

        url = f"{WECOM_API}/gettoken?corpid={self.corp_id}&corpsecret={self.corp_secret}"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                data = resp.json()
                if data.get("errcode") == 0:
                    self._access_token = data["access_token"]
                    self._token_expires_at = time.time() + data.get("expires_in", 7200) - 300
                    logger.debug("WeCom access token refreshed")
                    return self._access_token
                logger.error("WeCom token error: %s", data)
        except Exception as e:
            logger.exception("Failed to get WeCom token: %s", e)

        return ""
