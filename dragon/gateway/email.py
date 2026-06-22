"""
Dragon Gateway — Email (SMTP/IMAP) Platform Adapter
==================================================

Handles email messages via SMTP for sending and IMAP IDLE for polling.
This is a polling adapter — no webhook endpoint; use parse_webhook as
the IMAP poll result parser.

SMTP/IMAP docs: https://docs.python.org/3/library/email.html

Configuration::

    adapter = EmailAdapter(
        smtp_host="smtp.gmail.com",   # or set SMTP_HOST env
        smtp_port=587,                 # or set SMTP_PORT env
        smtp_user="user@gmail.com",    # or set SMTP_USER env
        smtp_pass="app-password",      # or set SMTP_PASS env
    )
"""
from __future__ import annotations

import logging
import os
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import httpx

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("dragon.gateway.email")


class EmailAdapter(PlatformAdapter):
    """Email adapter using SMTP for sending and IMAP for receiving (poll-based).

    This adapter does not expose a traditional webhook. Instead, it is
    intended to be used with a polling loop via IMAP IDLE.
    """

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 0,
        smtp_user: str = "",
        smtp_pass: str = "",
    ) -> None:
        super().__init__(platform_name="email", webhook_path="/email/webhook")

        self.smtp_host = smtp_host or os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = smtp_port or int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = smtp_user or os.getenv("SMTP_USER", "")
        self.smtp_pass = smtp_pass or os.getenv("SMTP_PASS", "")

        logger.info(
            "Email adapter ready (host=%s:%s, user=%s)",
            self.smtp_host, self.smtp_port,
            self.smtp_user or "none",
        )

    # ── Webhook Verification ──────────────────────────────────────

    async def verify_webhook(self, headers: Dict, body: bytes) -> bool:
        """No webhook — polling adapter. Always returns True."""
        return True

    # ── Webhook Parsing ───────────────────────────────────────────

    async def parse_webhook(self, body: Dict[str, Any]) -> Optional[PlatformMessage]:
        """Parse email message dict from IMAP polling result.

        The polling layer should pass a dict with fields::

            {
                "from": "sender@example.com",
                "to": "bot@example.com",
                "subject": "...",
                "body": "plain text content",
                "message_id": "<...>",
            }
        """
        from_addr = body.get("from", body.get("From", ""))
        body_text = body.get("body", body.get("text", body.get("content", "")))
        message_id = body.get("message_id", body.get("Message-ID", ""))

        if not body_text:
            return None

        return PlatformMessage(
            platform="email",
            chat_id=from_addr,
            user_id=from_addr,
            content=body_text,
            message_id=message_id,
            raw=body,
        )

    # ── Send Message ──────────────────────────────────────────────

    async def send_message(self, reply: PlatformReply) -> bool:
        """Send an email via SMTP."""
        import smtplib
        import ssl

        if not self.smtp_user or not self.smtp_pass:
            logger.error("SMTP credentials not configured")
            return False

        chat_id = reply.chat_id
        if not chat_id or chat_id == "__challenge__":
            return True

        # Build email message
        msg = MIMEMultipart()
        msg["From"] = self.smtp_user
        msg["To"] = chat_id
        msg["Subject"] = reply.content.split("\n")[0][:78] if reply.content else "Re:"
        msg.attach(MIMEText(reply.content, "plain", "utf-8"))

        # Attach media files if present
        for media_path in reply.media_paths:
            try:
                with open(media_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename={os.path.basename(media_path)}",
                    )
                    msg.attach(part)
            except Exception as e:
                logger.warning("Failed to attach %s: %s", media_path, e)

        try:
            context = ssl.create_default_context()
            if self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls(context=context)

            server.login(self.smtp_user, self.smtp_pass)
            server.sendmail(self.smtp_user, chat_id, msg.as_string())
            server.quit()
            logger.debug("Sent email to %s", chat_id)
            return True
        except Exception as e:
            logger.exception("Failed to send email: %s", e)

        return False

    # ── Media Upload ──────────────────────────────────────────────

    async def upload_media(self, file_path: str) -> Optional[str]:
        """Attach file to email on send. Returns file path as reference."""
        import os as _os

        if not _os.path.exists(file_path):
            return None
        return file_path  # Will be attached during send_message
