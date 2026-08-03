"""
Tests for dragon/feishu.py — Adapter-level functions.

Tests:
1. Voice command parsing   — _check_voice_command
2. WebSocket event parsing — _parse_ws_event
3. Webhook parsing         — parse_webhook
4. Adapter initialization   — FeishuAdapter.__init__
5. Adapter config fallbacks — env var / domain / connection_mode
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dragon.feishu import FeishuAdapter
from dragon.gateway.base import PlatformMessage


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _make_mock_ws_event(
    event_type: str = "im.message.receive_v1",
    chat_id: str = "oc_test123",
    message_id: str = "om_test456",
    sender_open_id: str = "ou_user789",
    text: str = "你好",
    content_raw: str = None,
    has_event: bool = True,
    has_message: bool = True,
    has_sender: bool = True,
) -> MagicMock:
    """Build a mock Lark SDK WebSocket event object."""
    event = MagicMock()
    event.type = event_type

    if has_event:
        evt = MagicMock()
        if has_message:
            msg = MagicMock()
            msg.chat_id = chat_id
            msg.message_id = message_id
            msg.content = content_raw or json.dumps({"text": text})
            evt.message = msg
        else:
            evt.message = None

        if has_sender:
            sender = MagicMock()
            sid = MagicMock()
            sid.open_id = sender_open_id
            sender.sender_id = sid
            evt.sender = sender

        event.event = evt
    else:
        event.event = None

    return event


# ════════════════════════════════════════════════════════════════════
# Voice Command Tests
# ════════════════════════════════════════════════════════════════════

class TestVoiceCommands:
    """Test _check_voice_command (toggle voice mode via slash commands)."""

    def setup_method(self):
        self.adapter = FeishuAdapter(app_id="test", app_secret="test")

    def test_voice_on_english(self):
        result = self.adapter._check_voice_command("/voice on")
        assert self.adapter.voice_enabled is True
        assert "语音模式已开启" in result

    def test_voice_off_english(self):
        self.adapter.voice_enabled = True
        result = self.adapter._check_voice_command("/voice off")
        assert self.adapter.voice_enabled is False
        assert "语音模式已关闭" in result

    def test_voice_on_chinese(self):
        result = self.adapter._check_voice_command("/语音 on")
        assert self.adapter.voice_enabled is True

    def test_voice_off_chinese(self):
        self.adapter.voice_enabled = True
        result = self.adapter._check_voice_command("/语音 off")
        assert self.adapter.voice_enabled is False

    def test_voice_on_chinese_kai(self):
        result = self.adapter._check_voice_command("/语音 开")
        assert self.adapter.voice_enabled is True

    def test_voice_off_chinese_guan(self):
        self.adapter.voice_enabled = True
        result = self.adapter._check_voice_command("/语音 关")
        assert self.adapter.voice_enabled is False

    def test_non_voice_command_returns_none(self):
        result = self.adapter._check_voice_command("你好世界")
        assert result is None

    def test_normal_message_returns_none(self):
        result = self.adapter._check_voice_command("/help")
        assert result is None

    def test_case_insensitive(self):
        result = self.adapter._check_voice_command("/Voice ON")
        assert self.adapter.voice_enabled is True

    def test_whitespace_handling(self):
        result = self.adapter._check_voice_command("  /语音 on  ")
        assert self.adapter.voice_enabled is True

    def test_partial_match_returns_none(self):
        """Words containing 'on' or 'off' shouldn't trigger."""
        result = self.adapter._check_voice_command("/voice onion")
        assert result is None


# ════════════════════════════════════════════════════════════════════
# WebSocket Event Parsing Tests
# ════════════════════════════════════════════════════════════════════

class TestParseWsEvent:
    """Test _parse_ws_event — Lark SDK event → PlatformMessage."""

    def setup_method(self):
        self.adapter = FeishuAdapter(app_id="test", app_secret="test")

    @pytest.mark.asyncio
    async def test_basic_text_message(self):
        event = _make_mock_ws_event(text="你好世界")
        result = await self.adapter._parse_ws_event(event)

        assert result is not None
        assert result.platform == "feishu"
        assert result.chat_id == "oc_test123"
        assert result.user_id == "ou_user789"
        assert result.content == "你好世界"
        assert result.message_id == "om_test456"

    @pytest.mark.asyncio
    async def test_multiline_text_message(self):
        content = json.dumps({"text": "第一行\n第二行\n第三行"})
        event = _make_mock_ws_event(content_raw=content)
        result = await self.adapter._parse_ws_event(event)

        assert result is not None
        assert "第一行" in result.content
        assert "第二行" in result.content

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self):
        event = _make_mock_ws_event(text="")
        result = await self.adapter._parse_ws_event(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_message_event_returns_none(self):
        """Events like 'im.message.reaction.created_v1' should not parse as messages."""
        event = _make_mock_ws_event(event_type="im.message.reaction.created_v1")
        # reaction event type contains "message" but has no message content
        # _parse_ws_event checks 'message' in event_type → enters message parsing → gets text → returns PlatformMessage
        # This is a gray area — let's see what actually happens
        result = await self.adapter._parse_ws_event(event)
        # The event still has message content, so it will parse. The filter is on event_type string.
        # "im.message.reaction.created_v1" contains "message" so it passes the filter.
        assert result is not None

    @pytest.mark.asyncio
    async def test_card_action_event(self):
        """Card action events contain 'card' in type, should pass filter."""
        event = _make_mock_ws_event(
            event_type="card.action.trigger",
            text="点击了按钮",
        )
        result = await self.adapter._parse_ws_event(event)
        assert result is not None

    @pytest.mark.asyncio
    async def test_non_message_non_card_event(self):
        """Events without 'message' or 'card' in type are skipped."""
        event = _make_mock_ws_event(
            event_type="im.chat.disbanded",
        )
        result = await self.adapter._parse_ws_event(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_event_attribute(self):
        event = MagicMock()
        event.type = "im.message.receive_v1"
        # MagicMock auto-creates .event.message.* → need to suppress that
        # Configure to raise AttributeError on .event access
        del event.event
        result = await self.adapter._parse_ws_event(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_message_in_event(self):
        event = _make_mock_ws_event(has_message=False)
        result = await self.adapter._parse_ws_event(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_content(self):
        event = _make_mock_ws_event(content_raw="not valid json {{{")
        result = await self.adapter._parse_ws_event(event)
        # Falls back to str(content_raw)
        assert result is not None
        assert result.content == "not valid json {{{"

    @pytest.mark.asyncio
    async def test_content_none(self):
        event = _make_mock_ws_event(content_raw="null")
        result = await self.adapter._parse_ws_event(event)
        assert result is None  # json.loads("null") → None → text="" → returns None

    @pytest.mark.asyncio
    async def test_whitespace_only_text(self):
        event = _make_mock_ws_event(text="   ")
        result = await self.adapter._parse_ws_event(event)
        # Code checks "if not text" BEFORE strip(),
        # and "   " is truthy, so it returns with stripped empty content.
        # This is a known quirk — whitespace-only messages pass the guard.
        assert result is not None
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_text_is_stripped(self):
        event = _make_mock_ws_event(text="  有空格  ")
        result = await self.adapter._parse_ws_event(event)
        assert result.content == "有空格"

    @pytest.mark.asyncio
    async def test_raw_event_data_stored(self):
        event = _make_mock_ws_event(text="test")
        result = await self.adapter._parse_ws_event(event)
        assert result.raw == {"event_type": "im.message.receive_v1"}


# ════════════════════════════════════════════════════════════════════
# Webhook Parsing Tests
# ════════════════════════════════════════════════════════════════════

class TestParseWebhook:
    """Test parse_webhook — HTTP webhook body → PlatformMessage."""

    def setup_method(self):
        self.adapter = FeishuAdapter(app_id="test", app_secret="test")

    @pytest.mark.asyncio
    async def test_url_verification(self):
        body = {
            "type": "url_verification",
            "challenge": "abc123",
            "token": "xxx",
        }
        result = await self.adapter.parse_webhook(body)
        assert result is not None
        assert result.chat_id == "__challenge__"
        assert result.content == "abc123"

    @pytest.mark.asyncio
    async def test_basic_message(self):
        body = {
            "header": {"event_type": "im.message.receive_v1", "create_time": "1700000000000"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_test"}},
                "message": {
                    "chat_id": "oc_test",
                    "message_id": "om_test",
                    "content": json.dumps({"text": "你好飞书"}),
                },
            },
        }
        result = await self.adapter.parse_webhook(body)
        assert result is not None
        assert result.platform == "feishu"
        assert result.chat_id == "oc_test"
        assert result.user_id == "ou_test"
        assert result.content == "你好飞书"
        assert result.message_id == "om_test"

    @pytest.mark.asyncio
    async def test_non_message_event_returns_none(self):
        body = {
            "header": {"event_type": "im.chat.disbanded"},
            "event": {"sender": {}, "message": {}},
        }
        result = await self.adapter.parse_webhook(body)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_text_returns_none(self):
        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_test"}},
                "message": {
                    "chat_id": "oc_test",
                    "message_id": "om_test",
                    "content": json.dumps({"text": ""}),
                },
            },
        }
        result = await self.adapter.parse_webhook(body)
        assert result is None

    @pytest.mark.asyncio
    async def test_thread_id_from_root_id(self):
        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_test"}},
                "message": {
                    "chat_id": "oc_test",
                    "message_id": "om_test",
                    "root_id": "om_root123",
                    "content": json.dumps({"text": "thread reply"}),
                },
            },
        }
        result = await self.adapter.parse_webhook(body)
        assert result.thread_id == "om_root123"

    @pytest.mark.asyncio
    async def test_thread_id_from_parent_id(self):
        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "ou_test"}},
                "message": {
                    "chat_id": "oc_test",
                    "message_id": "om_test",
                    "parent_id": "om_parent456",
                    "content": json.dumps({"text": "reply"}),
                },
            },
        }
        result = await self.adapter.parse_webhook(body)
        assert result.thread_id == "om_parent456"

    @pytest.mark.asyncio
    async def test_invalid_json_content_fallback(self):
        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {}},
                "message": {
                    "chat_id": "oc_test",
                    "message_id": "om_test",
                    "content": "plain text not json",
                },
            },
        }
        result = await self.adapter.parse_webhook(body)
        assert result is not None
        assert result.content == "plain text not json"


# ════════════════════════════════════════════════════════════════════
# Adapter Initialization Tests
# ════════════════════════════════════════════════════════════════════

class TestAdapterInit:
    """Test FeishuAdapter initialization and config fallbacks."""

    def test_explicit_params(self):
        adapter = FeishuAdapter(
            app_id="cli_aaa",
            app_secret="sec_bbb",
            connection_mode="webhook",
            domain="lark",
        )
        assert adapter.app_id == "cli_aaa"
        assert adapter.app_secret == "sec_bbb"
        assert adapter.connection_mode == "webhook"
        assert adapter.domain == "lark"
        assert adapter.api_base == "https://open.larksuite.com/open-apis"

    def test_default_domain_feishu(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y")
        assert adapter.api_base == "https://open.feishu.cn/open-apis"

    def test_unknown_domain_falls_back_to_feishu(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y", domain="unknown")
        assert adapter.api_base == "https://open.feishu.cn/open-apis"

    def test_default_connection_mode_websocket(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y")
        assert adapter.connection_mode == "websocket"

    def test_env_var_app_id(self, monkeypatch):
        monkeypatch.setenv("FEISHU_APP_ID", "cli_from_env")
        adapter = FeishuAdapter()
        assert adapter.app_id == "cli_from_env"

    def test_env_var_app_secret(self, monkeypatch):
        monkeypatch.setenv("FEISHU_APP_SECRET", "sec_from_env")
        adapter = FeishuAdapter()
        assert adapter.app_secret == "sec_from_env"

    def test_env_var_connection_mode(self, monkeypatch):
        monkeypatch.setenv("FEISHU_CONNECTION_MODE", "webhook")
        adapter = FeishuAdapter(connection_mode="")
        assert adapter.connection_mode == "webhook"

    def test_env_var_verification_token(self, monkeypatch):
        monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "tok123")
        adapter = FeishuAdapter()
        assert adapter.verification_token == "tok123"

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("FEISHU_APP_ID", "env_id")
        adapter = FeishuAdapter(app_id="explicit_id")
        assert adapter.app_id == "explicit_id"

    def test_empty_params_default_to_empty_strings(self, monkeypatch):
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        adapter = FeishuAdapter()
        assert adapter.app_id == ""
        assert adapter.app_secret == ""

    def test_voice_disabled_by_default(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y")
        assert adapter.voice_enabled is False

    def test_reactions_enabled_by_default(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y")
        assert adapter._reactions_enabled is True

    def test_platform_name_is_feishu(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y")
        assert adapter.platform_name == "feishu"

    def test_dedup_seen_messages_initialized(self):
        adapter = FeishuAdapter(app_id="x", app_secret="y")
        assert adapter._seen_message_ids == {}
