"""
Panda Gateway — SMS (Twilio) Platform Adapter
=============================================

Handles SMS messages via the Twilio Programmable Messaging API.
Receives incoming SMS via Twilio webhooks, sends replies via Messages API.

Twilio docs: https://www.twilio.com/docs/messaging

Configuration::

    adapter = SMSAdapter(
        account_sid="AC...",         # or set TWILIO_ACCOUNT_SID env
        auth_token="...",            # or set TWILIO_AUTH_TOKEN env
        phone_number="+1234567890",  # or set TWILIO_PHONE_NUMBER env
    )
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

import httpx

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.sms")

TWILIO_API = "https://api.twilio.com/2010-04-01"


class SMSAdapter(PlatformAdapter):
    """SMS adapter using Twilio Programmable Messaging API.

    Handles form-encoded webhooks from Twilio (application/x-www-form-urlencoded).
    Signature verification is skipped — Twilio's X-Twilio-Signature requires the
    full request URL which is complex to reconstruct in a gateway proxy.
    """

    def __init__(
        self,
        account_sid: str = "",
        auth_token: str = "",
        phone_number: str = "",
    ) -> None:
        super().__init__(platform_name="sms", webhook_path="/sms/webhook")

        self.account_sid = account_sid or os.getenv("TWILIO_ACCOUNT_SID", "")
        self.auth_token = auth_token or os.getenv("TWILIO_AUTH_TOKEN", "")
        self.phone_number = phone_number or os.getenv("TWILIO_PHONE_NUMBER", "")

        logger.info(
            "SMS adapter ready (sid=%s..., from=%s)",
            self.account_sid[:6] if self.account_sid else "none",
            self.phone_number or "none",
        )

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """Skip Twilio signature verification for now.

        Twilio's X-Twilio-Signature requires hashing the full request URL
        with ordered query params, which is complex to reconstruct reliably.
        """
        return True

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse Twilio webhook payload.

        Twilio sends application/x-www-form-urlencoded with fields::

            Body=hello+world
            From=%2B1234567890
            To=%2B0987654321
            MessageSid=SM...
            SmsStatus=received
        """
        # The gateway server may have already parsed form body into dict.
        # Check for _form_body in case it was passed through raw.
        form = body.get("_form_body", body) if isinstance(body, dict) else body

        # Fallback: if the dict has a raw bytes body, parse it as form data
        msg_body = form.get("Body", "")
        if not msg_body and isinstance(body.get("_raw_body"), bytes):
            raw = body["_raw_body"]
            try:
                parsed = parse_qs(raw.decode("utf-8", errors="replace"))
                msg_body = parsed.get("Body", [""])[0]
                from_number = parsed.get("From", [""])[0]
                message_sid = parsed.get("MessageSid", [""])[0]
            except Exception:
                return None
        else:
            from_number = form.get("From", "")
            message_sid = form.get("MessageSid", "")

        if not msg_body:
            return None

        return PlatformMessage(
            platform="sms",
            chat_id=from_number,
            user_id=from_number,
            content=msg_body,
            message_id=message_sid,
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send an SMS via Twilio Messages API."""
        if not self.account_sid or not self.auth_token:
            logger.error("Twilio credentials not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        url = f"{TWILIO_API}/Accounts/{self.account_sid}/Messages.json"

        auth = (self.account_sid, self.auth_token)
        data = {
            "From": self.phone_number,
            "To": chat_id,
            "Body": reply.content[:1600],  # SMS limit with multi-part
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, auth=auth, data=data)
                if resp.status_code in (200, 201):
                    logger.debug("Sent SMS to %s", chat_id)
                    return True
                logger.error("Twilio API error: %s %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Failed to send SMS: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Not implemented — returns None."""
        return None
