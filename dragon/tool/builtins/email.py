"""
Dragon Agent — Email Tools
==========================

IMAP/SMTP-based email tools: send, search, and read emails.
Supports environment variable fallback for credentials.

Environment variables:
    DRAGON_SMTP_HOST, DRAGON_SMTP_PORT, DRAGON_SMTP_USER, DRAGON_SMTP_PASSWORD
    DRAGON_IMAP_HOST, DRAGON_IMAP_USER, DRAGON_IMAP_PASSWORD
"""
from __future__ import annotations

import email
import email.encoders
import email.message
import email.utils
import json
import logging
import os
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("dragon.tool.email")


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _get_smtp_config(
    smtp_host: Optional[str],
    smtp_port: Optional[int],
    smtp_user: Optional[str],
    smtp_password: Optional[str],
) -> tuple:
    """Resolve SMTP config from args or environment variables."""
    host = smtp_host or os.environ.get("DRAGON_SMTP_HOST")
    port = smtp_port if smtp_port is not None else int(os.environ.get("DRAGON_SMTP_PORT", "587"))
    user = smtp_user or os.environ.get("DRAGON_SMTP_USER")
    password = smtp_password or os.environ.get("DRAGON_SMTP_PASSWORD")
    return host, port, user, password


def _get_imap_config(
    imap_host: Optional[str],
    imap_user: Optional[str],
    imap_password: Optional[str],
) -> tuple:
    """Resolve IMAP config from args or environment variables."""
    host = imap_host or os.environ.get("DRAGON_IMAP_HOST")
    user = imap_user or os.environ.get("DRAGON_IMAP_USER")
    password = imap_password or os.environ.get("DRAGON_IMAP_PASSWORD")
    return host, user, password


def _extract_raw_bytes(fetch_data) -> bytes:
    """Extract raw email bytes from an imaplib fetch result.

    Handles multiple possible response formats from different servers
    or mock configurations.
    """
    # fetch_data is a list like [(b'1 (RFC822 {nnn}', raw), b')']
    # or [(b'1', (b'RFC822', raw))]
    if not fetch_data or not fetch_data[0]:
        return b""

    item = fetch_data[0]
    # item can be:
    #   1. bytes (e.g., literal string from uid command)
    #   2. tuple of (bytes, bytes) — standard imaplib fetch
    #   3. tuple of (bytes, tuple) — nested response

    if isinstance(item, bytes):
        return item

    if isinstance(item, tuple) and len(item) >= 2:
        part = item[1]
        if isinstance(part, bytes):
            return part
        if isinstance(part, tuple) and len(part) >= 2:
            # Nested: (b'1', (b'RFC822', raw))
            inner = part[1]
            if isinstance(inner, bytes):
                return inner

    # Fallback: try joining all bytes parts
    parts = []
    for x in fetch_data:
        if isinstance(x, bytes):
            parts.append(x)
        elif isinstance(x, tuple):
            for y in x:
                if isinstance(y, bytes):
                    parts.append(y)
    return b"".join(parts)


def _parse_email_headers(raw_bytes: bytes) -> dict:
    """Parse email headers from raw bytes."""
    if not raw_bytes:
        return {}
    msg = email.message_from_bytes(raw_bytes)
    return {
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
    }


def _parse_email_full(raw_bytes: bytes) -> dict:
    """Parse full email (headers + body) from raw bytes."""
    if not raw_bytes:
        return {}
    msg = email.message_from_bytes(raw_bytes)

    result = {
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
        "body": "",
    }

    # Extract body — prefer plain text, fall back to html
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                continue
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        result["body"] = payload.decode("utf-8", errors="replace")
                    except Exception:
                        result["body"] = payload.decode("latin-1", errors="replace")
                    break
        # If no text/plain found, try text/html
        if not result["body"]:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            result["body"] = payload.decode("utf-8", errors="replace")
                        except Exception:
                            result["body"] = payload.decode("latin-1", errors="replace")
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            try:
                result["body"] = payload.decode("utf-8", errors="replace")
            except Exception:
                result["body"] = payload.decode("latin-1", errors="replace")

    return result


# ────────────────────────────────────────────────────────────────────
# Tool: email_send
# ────────────────────────────────────────────────────────────────────


async def tool_email_send(
    to: str,
    subject: str,
    body: str,
    smtp_host: Optional[str] = None,
    smtp_port: Optional[int] = None,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
    cc: Optional[str] = None,
    attachments: Optional[List[str]] = None,
) -> str:
    """Send an email via SMTP.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.
        smtp_host: SMTP server hostname (defaults to DRAGON_SMTP_HOST env var).
        smtp_port: SMTP server port (default: 587, or DRAGON_SMTP_PORT env).
        smtp_user: SMTP login username (defaults to DRAGON_SMTP_USER env).
        smtp_password: SMTP login password (defaults to DRAGON_SMTP_PASSWORD env).
        cc: Optional CC recipient(s), comma-separated.
        attachments: Optional list of file paths to attach.

    Returns:
        JSON with success status, recipient, subject.
    """
    host, port, user, password = _get_smtp_config(
        smtp_host, smtp_port, smtp_user, smtp_password
    )

    if not host:
        return json.dumps({
            "error": "Missing SMTP host. Provide smtp_host argument or set DRAGON_SMTP_HOST."
        })
    if not user or not password:
        return json.dumps({
            "error": "Missing SMTP credentials. Provide smtp_user/smtp_password or set "
                     "DRAGON_SMTP_USER / DRAGON_SMTP_PASSWORD."
        })

    try:
        # Build message
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Attach files
        attached = []
        if attachments:
            for filepath in attachments:
                p = Path(filepath).expanduser().resolve()
                if not p.exists():
                    return json.dumps({"error": f"Attachment not found: {filepath}"})
                with open(p, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                email.encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename=\"{p.name}\"",
                )
                msg.attach(part)
                attached.append(p.name)

        # Determine all recipients
        all_recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
        if cc:
            all_recipients += [addr.strip() for addr in cc.split(",") if addr.strip()]

        # Send via SMTP
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)

        result = {
            "success": True,
            "to": to,
            "subject": subject,
        }
        if cc:
            result["cc"] = cc
        if attached:
            result["attachments"] = attached

        logger.info("Email sent to %s (subject: %s)", to, subject)
        return json.dumps(result)

    except smtplib.SMTPAuthenticationError as e:
        return json.dumps({"error": f"SMTP authentication failed: {e}"})
    except smtplib.SMTPException as e:
        return json.dumps({"error": f"SMTP error: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to send email: {type(e).__name__}: {e}"})


# ────────────────────────────────────────────────────────────────────
# Tool: email_search
# ────────────────────────────────────────────────────────────────────


async def tool_email_search(
    query: str = "ALL",
    folder: str = "INBOX",
    limit: int = 20,
    imap_host: Optional[str] = None,
    imap_user: Optional[str] = None,
    imap_password: Optional[str] = None,
) -> str:
    """Search emails in an IMAP folder and return headers.

    Args:
        query: IMAP search query (default: "ALL"). Can be "UNSEEN", "FROM ...",
               "SUBJECT ...", "SINCE dd-MMM-yyyy", etc.
        folder: IMAP folder to search (default: "INBOX").
        limit: Maximum number of results to return (default: 20).
        imap_host: IMAP server hostname (defaults to DRAGON_IMAP_HOST env).
        imap_user: IMAP login username (defaults to DRAGON_IMAP_USER env).
        imap_password: IMAP login password (defaults to DRAGON_IMAP_PASSWORD env).

    Returns:
        JSON with query, folder, count, and results list (uid, from, to, subject, date).
    """
    import imaplib

    host, user, password = _get_imap_config(imap_host, imap_user, imap_password)

    if not host:
        return json.dumps({
            "error": "Missing IMAP host. Provide imap_host argument or set DRAGON_IMAP_HOST."
        })
    if not user or not password:
        return json.dumps({
            "error": "Missing IMAP credentials. Provide imap_user/imap_password or set "
                     "DRAGON_IMAP_USER / DRAGON_IMAP_PASSWORD."
        })

    try:
        imap = imaplib.IMAP4_SSL(host)
        imap.login(user, password)
        imap.select(folder)

        status, data = imap.search(None, query)
        if status != "OK":
            return json.dumps({"error": f"IMAP search failed: {status}"})

        # Parse UID list
        uid_list = []
        for chunk in data:
            if chunk:
                uid_list.extend(chunk.split())

        # Limit results (most recent first — IMAP returns ascending)
        if len(uid_list) > limit:
            uid_list = uid_list[-limit:]

        results = []
        for uid in uid_list:
            try:
                status, msg_data = imap.fetch(uid, "(RFC822)")
                if status != "OK":
                    continue
                raw = _extract_raw_bytes(msg_data)
                headers = _parse_email_headers(raw)
                headers["uid"] = uid.decode() if isinstance(uid, bytes) else uid
                results.append(headers)
            except Exception:
                logger.debug("Failed to fetch email UID %s", uid, exc_info=True)
                continue

        imap.logout()

        return json.dumps({
            "query": query,
            "folder": folder,
            "count": len(results),
            "results": results,
        })

    except imaplib.IMAP4.error as e:
        return json.dumps({"error": f"IMAP error: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to search emails: {type(e).__name__}: {e}"})


# ────────────────────────────────────────────────────────────────────
# Tool: email_read
# ────────────────────────────────────────────────────────────────────


async def tool_email_read(
    uid: str,
    folder: str = "INBOX",
    imap_host: Optional[str] = None,
    imap_user: Optional[str] = None,
    imap_password: Optional[str] = None,
) -> str:
    """Read a specific email by UID from an IMAP folder.

    Args:
        uid: Email UID (as returned by email_search).
        folder: IMAP folder (default: "INBOX").
        imap_host: IMAP server hostname (defaults to DRAGON_IMAP_HOST env).
        imap_user: IMAP login username (defaults to DRAGON_IMAP_USER env).
        imap_password: IMAP login password (defaults to DRAGON_IMAP_PASSWORD env).

    Returns:
        JSON with uid, from, to, cc, subject, date, body.
    """
    import imaplib

    host, user, password = _get_imap_config(imap_host, imap_user, imap_password)

    if not host:
        return json.dumps({
            "error": "Missing IMAP host. Provide imap_host argument or set DRAGON_IMAP_HOST."
        })
    if not user or not password:
        return json.dumps({
            "error": "Missing IMAP credentials. Provide imap_user/imap_password or set "
                     "DRAGON_IMAP_USER / DRAGON_IMAP_PASSWORD."
        })

    try:
        imap = imaplib.IMAP4_SSL(host)
        imap.login(user, password)
        imap.select(folder)

        # Use UID SEARCH to verify the email exists
        status, data = imap.uid("SEARCH", "", f"(UID {uid})")
        if status != "OK":
            return json.dumps({"error": f"IMAP search failed: {status}"})

        # data from uid search is like [b'1'] or [b''] or [b'1 2']
        found = False
        for chunk in data:
            if chunk and chunk.strip():
                found = True
                break
        if not found:
            return json.dumps({"error": f"Email with UID {uid} not found in {folder}"})

        # Fetch the full email
        status, msg_data = imap.fetch(uid, "(RFC822)")
        if status != "OK":
            return json.dumps({"error": f"IMAP fetch failed: {status}"})

        raw = _extract_raw_bytes(msg_data)
        if not raw:
            return json.dumps({"error": f"Empty response for UID {uid}"})

        parsed = _parse_email_full(raw)
        parsed["uid"] = uid
        parsed["folder"] = folder

        imap.logout()

        return json.dumps(parsed)

    except imaplib.IMAP4.error as e:
        return json.dumps({"error": f"IMAP error: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to read email: {type(e).__name__}: {e}"})
