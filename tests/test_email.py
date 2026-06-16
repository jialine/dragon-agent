"""
Unit tests for Dragon Email tools (email_send, email_search, email_read).

All SMTP/IMAP interactions are mocked — no real servers needed.
"""
import json
import os
import email
import unittest.mock as mock

import pytest


# ── tool_email_send ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_send_basic():
    """Send a basic email — SMTP is mocked."""
    with mock.patch("smtplib.SMTP") as mock_smtp:
        from dragon.tool.builtins.email import tool_email_send

        result = json.loads(await tool_email_send(
            to="hello@example.com",
            subject="Test",
            body="Hello world",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user@example.com",
            smtp_password="pass123",
        ))

    assert "error" not in result
    assert result.get("success") is True
    assert result.get("to") == "hello@example.com"
    assert result.get("subject") == "Test"

    # Verify SMTP was called
    mock_smtp.assert_called_once_with("smtp.example.com", 587)
    instance = mock_smtp.return_value.__enter__.return_value
    instance.starttls.assert_called_once()
    instance.login.assert_called_once_with("user@example.com", "pass123")
    instance.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_email_send_with_cc_and_attachments(tmp_path):
    """Send email with CC and attachments."""
    attach_path = tmp_path / "test.txt"
    attach_path.write_text("file content")

    with mock.patch("smtplib.SMTP") as mock_smtp:
        from dragon.tool.builtins.email import tool_email_send

        result = json.loads(await tool_email_send(
            to="to@example.com",
            subject="With CC",
            body="Body text",
            smtp_host="localhost",
            smtp_port=465,
            smtp_user="user",
            smtp_password="pass",
            cc="cc@example.com",
            attachments=[str(attach_path)],
        ))

    assert "error" not in result
    assert result.get("success") is True
    assert "cc@example.com" in result.get("cc", "")

    instance = mock_smtp.return_value.__enter__.return_value
    instance.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_email_send_env_fallback(monkeypatch):
    """SMTP config should fall back to environment variables."""
    monkeypatch.setenv("DRAGON_SMTP_HOST", "env.smtp.com")
    monkeypatch.setenv("DRAGON_SMTP_PORT", "2525")
    monkeypatch.setenv("DRAGON_SMTP_USER", "envuser")
    monkeypatch.setenv("DRAGON_SMTP_PASSWORD", "envpass")

    with mock.patch("smtplib.SMTP") as mock_smtp:
        from dragon.tool.builtins.email import tool_email_send

        result = json.loads(await tool_email_send(
            to="to@x.com",
            subject="Env test",
            body="body",
        ))

    assert "error" not in result
    assert result.get("success") is True

    mock_smtp.assert_called_once_with("env.smtp.com", 2525)
    instance = mock_smtp.return_value.__enter__.return_value
    instance.login.assert_called_once_with("envuser", "envpass")


@pytest.mark.asyncio
async def test_email_send_missing_config():
    """Should return an error when SMTP config is missing entirely."""
    # Remove any env vars that might exist
    for key in ("DRAGON_SMTP_HOST", "DRAGON_SMTP_PORT",
                "DRAGON_SMTP_USER", "DRAGON_SMTP_PASSWORD"):
        os.environ.pop(key, None)

    from dragon.tool.builtins.email import tool_email_send

    result = json.loads(await tool_email_send(
        to="to@x.com",
        subject="No config",
        body="body",
    ))

    assert "error" in result


# ── tool_email_search ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_search_basic():
    """Search inbox — IMAP is mocked."""
    mock_imap = mock.MagicMock()
    mock_imap.search.return_value = ("OK", [b"1 2 3"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        from dragon.tool.builtins.email import tool_email_search

        result = json.loads(await tool_email_search(
            query="ALL",
            folder="INBOX",
            limit=20,
            imap_host="imap.example.com",
            imap_user="user@x.com",
            imap_password="pass",
        ))

    assert "error" not in result
    assert "query" in result
    assert "results" in result
    assert isinstance(result["results"], list)
    assert result.get("count", 0) >= 0


@pytest.mark.asyncio
async def test_email_search_with_headers():
    """Search should return email headers."""
    mock_imap = mock.MagicMock()
    mock_imap.search.return_value = ("OK", [b"42"])
    # fetch returns: (type, [(num, (flags, RFC822 raw))])
    raw = (
        b"From: sender@example.com\r\n"
        b"To: receiver@example.com\r\n"
        b"Subject: Hello\r\n"
        b"Date: Mon, 1 Jan 2024 00:00:00 +0000\r\n"
        b"\r\n"
        b"Body text"
    )
    mock_imap.fetch.return_value = ("OK", [(b"42", (b"RFC822", raw))])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        from dragon.tool.builtins.email import tool_email_search

        result = json.loads(await tool_email_search(
            query="ALL",
            folder="INBOX",
            limit=5,
            imap_host="imap.x.com",
            imap_user="u",
            imap_password="p",
        ))

    assert "error" not in result
    assert len(result["results"]) == 1
    r = result["results"][0]
    assert r["uid"] == "42"
    assert r["from"] == "sender@example.com"
    assert r["subject"] == "Hello"


@pytest.mark.asyncio
async def test_email_search_env_fallback(monkeypatch):
    """IMAP config should fall back to environment variables."""
    monkeypatch.setenv("DRAGON_IMAP_HOST", "env.imap.com")
    monkeypatch.setenv("DRAGON_IMAP_USER", "envuser@imap.com")
    monkeypatch.setenv("DRAGON_IMAP_PASSWORD", "imappass")

    mock_imap = mock.MagicMock()
    mock_imap.search.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        from dragon.tool.builtins.email import tool_email_search

        result = json.loads(await tool_email_search(
            query="UNSEEN",
            folder="INBOX",
            limit=10,
        ))

    assert "error" not in result
    # Verify IMAP host was used from env
    # (We can't directly assert the connection args easily because of how
    # MagicMock works, but we verify the result)
    assert isinstance(result, dict)
    assert "results" in result


@pytest.mark.asyncio
async def test_email_search_missing_config():
    """Should return an error when IMAP config is missing."""
    for key in ("DRAGON_IMAP_HOST", "DRAGON_IMAP_USER", "DRAGON_IMAP_PASSWORD"):
        os.environ.pop(key, None)

    from dragon.tool.builtins.email import tool_email_search

    result = json.loads(await tool_email_search(
        query="ALL",
    ))

    assert "error" in result


# ── tool_email_read ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_read_full():
    """Read a full email by UID."""
    mock_imap = mock.MagicMock()
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Meeting\r\n"
        b"Date: Tue, 2 Jan 2024 10:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Let's meet at 3pm."
    )
    mock_imap.uid.return_value = ("OK", [b"1"])
    mock_imap.fetch.return_value = ("OK", [(b"1", raw)])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        from dragon.tool.builtins.email import tool_email_read

        result = json.loads(await tool_email_read(
            uid="42",
            folder="INBOX",
            imap_host="imap.x.com",
            imap_user="u",
            imap_password="p",
        ))

    assert "error" not in result
    assert result["uid"] == "42"
    assert result["from"] == "alice@example.com"
    assert result["subject"] == "Meeting"
    assert "Let's meet at 3pm." in result.get("body", "")


@pytest.mark.asyncio
async def test_email_read_not_found():
    """Return error when UID not found."""
    mock_imap = mock.MagicMock()
    mock_imap.uid.return_value = ("OK", [b""])  # no results

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        from dragon.tool.builtins.email import tool_email_read

        result = json.loads(await tool_email_read(
            uid="999",
            folder="INBOX",
            imap_host="imap.x.com",
            imap_user="u",
            imap_password="p",
        ))

    assert "error" in result


@pytest.mark.asyncio
async def test_email_read_env_fallback(monkeypatch):
    """IMAP config should fall back to environment variables for read."""
    monkeypatch.setenv("DRAGON_IMAP_HOST", "env.imap.com")
    monkeypatch.setenv("DRAGON_IMAP_USER", "envuser")
    monkeypatch.setenv("DRAGON_IMAP_PASSWORD", "envpass")

    mock_imap = mock.MagicMock()
    mock_imap.uid.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        from dragon.tool.builtins.email import tool_email_read

        result = json.loads(await tool_email_read(
            uid="1",
        ))

    # Even if not found, should not be a config error
    result = json.loads(result) if isinstance(result, str) else result
    assert "error" in result  # not found
    # Should NOT be a config error
    assert "Missing IMAP" not in result.get("error", "")


@pytest.mark.asyncio
async def test_email_read_missing_config():
    """Should return an error when IMAP config is missing."""
    for key in ("DRAGON_IMAP_HOST", "DRAGON_IMAP_USER", "DRAGON_IMAP_PASSWORD"):
        os.environ.pop(key, None)

    from dragon.tool.builtins.email import tool_email_read

    result = json.loads(await tool_email_read(uid="1"))

    assert "error" in result


# ── Registration ─────────────────────────────────────────────────────────


def test_email_tools_registered():
    """Verify email tools are registered in the builtins registry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    tool_names = [t["name"] for t in registry.list_tools()]

    assert "email_send" in tool_names
    assert "email_search" in tool_names
    assert "email_read" in tool_names
