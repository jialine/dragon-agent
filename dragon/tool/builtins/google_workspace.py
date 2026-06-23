"""
Dragon Agent — Google Workspace Tools
=====================================

Gmail (SMTP/IMAP) and Google Drive/Calendar (API key) tools.
Uses simple env-var based auth — no complex OAuth flow.

Environment variables:
    GMAIL_USER           — Gmail address (e.g., user@gmail.com)
    GMAIL_APP_PASSWORD   — Gmail App Password (https://myaccount.google.com/apppasswords)
    GOOGLE_DRIVE_API_KEY — Google Drive API key (read-only access)
    GOOGLE_CALENDAR_API_KEY — Google Calendar API key
    GOOGLE_CALENDAR_ID   — Calendar ID (defaults to "primary" if not set)

Tools:
    - gmail_send: Send email via Gmail SMTP (SSL on port 465)
    - gmail_search: Search Gmail inbox via IMAP (SSL on port 993)
    - google_drive_search: Search Google Drive files by name
    - google_calendar_list: List upcoming Google Calendar events
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("dragon.tool.builtins.google_workspace")

# ── Constants ──────────────────────────────────────────────────────────

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465
GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993

DRIVE_API_URL = "https://www.googleapis.com/drive/v3/files"
CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3/calendars"


# ── Helpers ────────────────────────────────────────────────────────────


def _get_gmail_creds() -> tuple:
    """Return (user, password) from GMAIL_USER / GMAIL_APP_PASSWORD env vars."""
    user = os.environ.get("GMAIL_USER", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    return user, password


# ────────────────────────────────────────────────────────────────────────
# Tool: gmail_send
# ────────────────────────────────────────────────────────────────────────


async def tool_gmail_send(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail SMTP (SSL on port 465).

    Requires GMAIL_USER and GMAIL_APP_PASSWORD environment variables.
    Generate an App Password at: https://myaccount.google.com/apppasswords

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.

    Returns:
        JSON with to, subject, and sent status. Returns error field on failure.
    """
    if not to or not to.strip():
        return json.dumps({"error": "Recipient address cannot be empty"})
    if not subject or not subject.strip():
        return json.dumps({"error": "Subject cannot be empty"})

    user, password = _get_gmail_creds()
    if not user:
        return json.dumps({
            "error": "GMAIL_USER environment variable is not set. "
                     "Set your Gmail address (e.g., user@gmail.com)."
        })
    if not password:
        return json.dumps({
            "error": "GMAIL_APP_PASSWORD environment variable is not set. "
                     "Generate one at https://myaccount.google.com/apppasswords"
        })

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = user
        msg["To"] = to.strip()
        msg["Subject"] = subject.strip()

        # Use SMTP_SSL for direct SSL on port 465 (no STARTTLS needed)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, context=context) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)

        logger.info("Gmail sent to %s (subject: %s)", to, subject)
        return json.dumps({
            "to": to.strip(),
            "subject": subject.strip(),
            "sent": True,
        })

    except smtplib.SMTPAuthenticationError as e:
        return json.dumps({
            "error": f"Gmail authentication failed: {e}. "
                     "Make sure GMAIL_APP_PASSWORD is correct and 2FA is enabled."
        })
    except smtplib.SMTPException as e:
        return json.dumps({"error": f"SMTP error: {e}"})
    except Exception as e:
        logger.warning("gmail_send failed: %s", e)
        return json.dumps({"error": f"Failed to send email: {type(e).__name__}: {e}"})


# ────────────────────────────────────────────────────────────────────────
# Tool: gmail_search
# ────────────────────────────────────────────────────────────────────────


async def tool_gmail_search(query: str = "ALL", limit: int = 10) -> str:
    """Search Gmail inbox via IMAP (SSL on port 993).

    Requires GMAIL_USER and GMAIL_APP_PASSWORD environment variables.

    Args:
        query: IMAP search query (default: "ALL"). Examples:
               "UNSEEN", "FROM alice@example.com", "SUBJECT invoice",
               "SINCE 01-Jan-2024", "BEFORE 31-Dec-2024".
        limit: Maximum number of results to return (default: 10).

    Returns:
        JSON with emails list [{subject, from, date, id}].
        Returns error field on failure.
    """
    import imaplib

    user, password = _get_gmail_creds()
    if not user:
        return json.dumps({
            "error": "GMAIL_USER environment variable is not set."
        })
    if not password:
        return json.dumps({
            "error": "GMAIL_APP_PASSWORD environment variable is not set. "
                     "Generate one at https://myaccount.google.com/apppasswords"
        })

    try:
        imap = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        imap.login(user, password)
        imap.select("INBOX")

        status, data = imap.search(None, query)
        if status != "OK":
            return json.dumps({"error": f"IMAP search failed: {status}"})

        # Parse UID list
        uid_list = []
        for chunk in data:
            if chunk:
                uid_list.extend(chunk.split())

        if not uid_list:
            imap.logout()
            return json.dumps({
                "query": query,
                "emails": [],
                "count": 0,
            })

        # Limit results (most recent first — IMAP returns ascending IDs)
        if len(uid_list) > limit:
            uid_list = uid_list[-limit:]

        results: List[Dict[str, Any]] = []
        for uid in reversed(uid_list):  # Most recent first
            try:
                status_fetch, msg_data = imap.fetch(uid, "(RFC822)")
                if status_fetch != "OK":
                    continue
                # Parse headers from raw bytes
                raw = _extract_raw_bytes(msg_data)
                import email
                parsed = email.message_from_bytes(raw)
                uid_str = uid.decode() if isinstance(uid, bytes) else uid
                results.append({
                    "subject": parsed.get("Subject", ""),
                    "from": parsed.get("From", ""),
                    "date": parsed.get("Date", ""),
                    "id": uid_str,
                })
            except Exception:
                logger.debug("Failed to fetch email UID %s", uid, exc_info=True)
                continue

        imap.logout()

        return json.dumps({
            "query": query,
            "emails": results,
            "count": len(results),
        })

    except imaplib.IMAP4.error as e:
        return json.dumps({"error": f"IMAP error: {e}"})
    except Exception as e:
        logger.warning("gmail_search failed: %s", e)
        return json.dumps({"error": f"Failed to search emails: {type(e).__name__}: {e}"})


def _extract_raw_bytes(fetch_data) -> bytes:
    """Extract raw email bytes from an imaplib fetch result."""
    if not fetch_data or not fetch_data[0]:
        return b""

    item = fetch_data[0]
    if isinstance(item, bytes):
        return item
    if isinstance(item, tuple) and len(item) >= 2:
        part = item[1]
        if isinstance(part, bytes):
            return part
        if isinstance(part, tuple) and len(part) >= 2:
            inner = part[1]
            if isinstance(inner, bytes):
                return inner

    # Fallback: join all bytes parts
    parts = []
    for x in fetch_data:
        if isinstance(x, bytes):
            parts.append(x)
        elif isinstance(x, tuple):
            for y in x:
                if isinstance(y, bytes):
                    parts.append(y)
    return b"".join(parts)


# ────────────────────────────────────────────────────────────────────────
# Tool: google_drive_search
# ────────────────────────────────────────────────────────────────────────


async def tool_google_drive_search(query: str, limit: int = 10) -> str:
    """Search Google Drive files by name.

    Requires GOOGLE_DRIVE_API_KEY environment variable (read-only access).

    Args:
        query: Search query to match against file names.
        limit: Maximum number of results to return (default: 10).

    Returns:
        JSON with files list [{id, name, mimeType, webViewLink}].
        Returns error field on failure.
    """
    api_key = os.environ.get("GOOGLE_DRIVE_API_KEY", "")
    if not api_key:
        return json.dumps({
            "error": "GOOGLE_DRIVE_API_KEY environment variable is not set. "
                     "Get an API key from https://console.cloud.google.com/apis/credentials"
        })

    if not query or not query.strip():
        return json.dumps({"error": "Search query cannot be empty"})

    query = query.strip()
    q_param = f"name contains '{query}'"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(
                DRIVE_API_URL,
                params={
                    "q": q_param,
                    "pageSize": min(limit, 100),
                    "fields": "files(id,name,mimeType,webViewLink)",
                    "key": api_key,
                },
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Drive API returned HTTP {resp.status_code}: {resp.text[:500]}",
                    "query": query,
                })

            data = resp.json()
            files = data.get("files", [])

            results = [
                {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "mimeType": f.get("mimeType", ""),
                    "webViewLink": f.get("webViewLink", ""),
                }
                for f in files[:limit]
            ]

            return json.dumps({
                "query": query,
                "files": results,
                "count": len(results),
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Drive API request timed out", "query": query})
    except Exception as e:
        logger.warning("google_drive_search failed: %s", e)
        return json.dumps({
            "error": f"Drive search failed: {type(e).__name__}: {e}",
            "query": query,
        })


# ────────────────────────────────────────────────────────────────────────
# Tool: google_calendar_list
# ────────────────────────────────────────────────────────────────────────


async def tool_google_calendar_list(days: int = 7) -> str:
    """List upcoming Google Calendar events.

    Requires GOOGLE_CALENDAR_API_KEY environment variable.
    Optionally set GOOGLE_CALENDAR_ID (defaults to "primary").

    Args:
        days: Number of days ahead to look for events (default: 7).

    Returns:
        JSON with events list [{summary, start, end, location}].
        Returns error field on failure.
    """
    api_key = os.environ.get("GOOGLE_CALENDAR_API_KEY", "")
    if not api_key:
        return json.dumps({
            "error": "GOOGLE_CALENDAR_API_KEY environment variable is not set. "
                     "Get an API key from https://console.cloud.google.com/apis/credentials"
        })

    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    # Compute time bounds
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    # Rough end time (accepts the API's tolerance for slightly-imprecise end dates)
    time_max = datetime.fromtimestamp(now.timestamp() + days * 86400, tz=timezone.utc).isoformat()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            url = f"{CALENDAR_API_URL}/{calendar_id}/events"
            resp = await client.get(
                url,
                params={
                    "key": api_key,
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 50,
                },
            )

            if resp.status_code != 200:
                return json.dumps({
                    "error": f"Calendar API returned HTTP {resp.status_code}: {resp.text[:500]}",
                    "calendar_id": calendar_id,
                })

            data = resp.json()
            raw_events = data.get("items", [])

            events = []
            for e in raw_events:
                start_info = e.get("start", {})
                end_info = e.get("end", {})
                events.append({
                    "summary": e.get("summary", "(No title)"),
                    "start": start_info.get("dateTime", start_info.get("date", "")),
                    "end": end_info.get("dateTime", end_info.get("date", "")),
                    "location": e.get("location", ""),
                    "id": e.get("id", ""),
                })

            return json.dumps({
                "calendar_id": calendar_id,
                "days": days,
                "events": events,
                "count": len(events),
            })

    except httpx.TimeoutException:
        return json.dumps({"error": "Calendar API request timed out"})
    except Exception as e:
        logger.warning("google_calendar_list failed: %s", e)
        return json.dumps({
            "error": f"Calendar list failed: {type(e).__name__}: {e}",
        })
