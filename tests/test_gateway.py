"""
Unit tests for Dragon Gateway — adapters, message processing.
"""
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from dragon.gateway.base import (
    PlatformMessage, PlatformReply, PlatformAdapter,
    verify_hmac_signature, verify_telegram_signature,
)


class TestPlatformMessage:
    def test_creation(self):
        msg = PlatformMessage(
            platform="feishu",
            chat_id="oc_123",
            user_id="ou_456",
            content="Hello",
        )
        assert msg.platform == "feishu"
        assert msg.chat_id == "oc_123"
        assert msg.content == "Hello"
        assert msg.session_id != ""

    def test_session_id_is_deterministic(self):
        msg1 = PlatformMessage(platform="feishu", chat_id="oc_123", user_id="a", content="x")
        msg2 = PlatformMessage(platform="feishu", chat_id="oc_123", user_id="a", content="y")
        assert msg1.session_id == msg2.session_id

    def test_session_id_differs_by_platform(self):
        msg1 = PlatformMessage(platform="feishu", chat_id="same", user_id="a", content="x")
        msg2 = PlatformMessage(platform="telegram", chat_id="same", user_id="a", content="x")
        assert msg1.session_id != msg2.session_id


class TestPlatformReply:
    def test_creation(self):
        reply = PlatformReply(content="Hi!", chat_id="oc_123")
        assert reply.content == "Hi!"
        assert reply.chat_id == "oc_123"

    def test_telegram_truncation(self):
        reply = PlatformReply(content="x" * 5000)
        formatted = reply.format_for_telegram()
        assert len(formatted) <= 4100
        assert "..." in formatted

    def test_short_message_not_truncated(self):
        reply = PlatformReply(content="Hello world")
        assert reply.format_for_telegram() == "Hello world"


class TestSignatureVerification:
    def test_hmac_verification_valid(self):
        import hmac, hashlib
        secret = "test-secret"
        timestamp = "1234567890"
        nonce = "abc123"
        body = b'{"test": true}'
        payload = f"{timestamp}{nonce}".encode() + body
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert verify_hmac_signature(secret, timestamp, nonce, body, sig) is True

    def test_hmac_verification_invalid(self):
        assert verify_hmac_signature("secret", "t", "n", b"{}", "bad-sig") is False

    def test_hmac_verification_missing_params(self):
        assert verify_hmac_signature("", "", "", b"", "") is False


class TestFeishuAdapter:
    def test_parse_challenge(self):
        from dragon.gateway.feishu import FeishuAdapter

        adapter = FeishuAdapter(app_id="test", app_secret="test")
        body = {"type": "url_verification", "challenge": "test-challenge-123"}

        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.chat_id == "__challenge__"
        assert msg.content == "test-challenge-123"

    def test_parse_text_message(self):
        from dragon.gateway.feishu import FeishuAdapter

        adapter = FeishuAdapter(app_id="test", app_secret="test")
        body = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_123",
                "event_type": "im.message.receive_v1",
                "create_time": "1700000000000",
            },
            "event": {
                "sender": {"sender_id": {"open_id": "ou_test123"}},
                "message": {
                    "message_id": "om_test456",
                    "chat_id": "oc_test789",
                    "message_type": "text",
                    "content": json.dumps({"text": "你好世界"}),
                },
            },
        }

        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "feishu"
        assert msg.user_id == "ou_test123"
        assert msg.chat_id == "oc_test789"
        assert msg.content == "你好世界"


class TestTelegramAdapter:
    def test_parse_text_message(self):
        from dragon.gateway.telegram import TelegramAdapter

        adapter = TelegramAdapter(bot_token="test:token")
        body = {
            "update_id": 123456789,
            "message": {
                "message_id": 100,
                "from": {"id": 123456, "first_name": "Test"},
                "chat": {"id": 789012, "type": "private"},
                "text": "Hello bot",
                "date": 1700000000,
            },
        }

        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "telegram"
        assert msg.user_id == "123456"
        assert msg.chat_id == "789012"
        assert msg.content == "Hello bot"

    def test_parse_message_with_thread(self):
        from dragon.gateway.telegram import TelegramAdapter

        adapter = TelegramAdapter(bot_token="test:token")
        body = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "from": {"id": 1},
                "chat": {"id": 1},
                "text": "Test",
                "date": 1700000000,
                "message_thread_id": 42,
            },
        }

        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.thread_id == "42"


class TestGatewayServer:
    def test_server_creation(self):
        from dragon.gateway.server import GatewayServer
        server = GatewayServer()
        assert server.app is not None
        assert len(server.adapters) == 0

    def test_register_adapter(self):
        from dragon.gateway.server import GatewayServer
        from dragon.gateway.base import PlatformAdapter

        class FakeAdapter(PlatformAdapter):
            async def verify_webhook(self, headers, body): return True
            async def parse_webhook(self, body): return None
            async def send_message(self, reply): return True
            async def upload_media(self, path): return None

        server = GatewayServer()
        adapter = FakeAdapter(platform_name="fake", webhook_path="/fake/webhook")
        server.register_adapter(adapter)
        assert "fake" in server.adapters


class TestMessageProcessor:
    def test_processor_without_provider(self):
        from dragon.gateway.server import MessageProcessor

        proc = MessageProcessor()
        msg = PlatformMessage(
            platform="test", chat_id="c1", user_id="u1", content="Hello",
        )

        reply = asyncio.new_event_loop().run_until_complete(
            proc.process(msg)
        )
        # Without provider, the agent loop breaks and returns the raw message
        assert reply is not None
        assert len(reply.content) > 0


class TestHandleURLVerification:
    def test_challenge_response(self):
        from dragon.gateway.feishu import handle_url_verification
        body = {"type": "url_verification", "challenge": "abc123"}
        result = handle_url_verification(body)
        assert result == {"challenge": "abc123"}

    def test_non_challenge_ignored(self):
        from dragon.gateway.feishu import handle_url_verification
        assert handle_url_verification({}) == {}


# ══════════════════════════════════════════════════════════════════════
# SlackAdapter
# ══════════════════════════════════════════════════════════════════════

class TestSlackAdapter:
    def test_constructor_reads_bot_token_from_env(self):
        from dragon.gateway.slack import SlackAdapter
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test-token"}):
            adapter = SlackAdapter()
            assert adapter.bot_token == "xoxb-test-token"

    def test_constructor_explicit_token_overrides_env(self):
        from dragon.gateway.slack import SlackAdapter
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "env-token"}):
            adapter = SlackAdapter(bot_token="explicit-token")
            assert adapter.bot_token == "explicit-token"

    def test_webhook_path(self):
        from dragon.gateway.slack import SlackAdapter
        adapter = SlackAdapter()
        assert adapter.webhook_path == "/slack/webhook"

    def test_verify_webhook_empty_headers_returns_true(self):
        from dragon.gateway.slack import SlackAdapter
        adapter = SlackAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_url_verification_challenge(self):
        from dragon.gateway.slack import SlackAdapter
        adapter = SlackAdapter()
        body = {"type": "url_verification", "challenge": "challenge-token-xyz"}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "slack"
        assert msg.chat_id == "__challenge__"
        assert msg.content == "challenge-token-xyz"

    def test_parse_webhook_text_message_event(self):
        from dragon.gateway.slack import SlackAdapter
        adapter = SlackAdapter()
        body = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C12345",
                "user": "U67890",
                "text": "Hello from Slack",
                "ts": "1700000000.000100",
                "thread_ts": "1699999999.000000",
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "slack"
        assert msg.chat_id == "C12345"
        assert msg.user_id == "U67890"
        assert msg.content == "Hello from Slack"
        assert msg.thread_id == "1699999999.000000"
        assert msg.message_id == "1700000000.000100"

    def test_parse_webhook_skips_bot_subtype(self):
        from dragon.gateway.slack import SlackAdapter
        adapter = SlackAdapter()
        body = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel": "C12345",
                "user": "U67890",
                "text": "bot msg",
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_send_message_calls_api(self):
        from dragon.gateway.slack import SlackAdapter
        from dragon.gateway.base import PlatformReply

        adapter = SlackAdapter(bot_token="xoxb-fake")
        reply = PlatformReply(content="Hi from test", chat_id="C12345")

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = mock_post
            result = asyncio.new_event_loop().run_until_complete(
                adapter.send_message(reply)
            )
            assert result is True
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["json"]["channel"] == "C12345"

    def test_send_message_no_token_returns_false(self):
        from dragon.gateway.slack import SlackAdapter
        from dragon.gateway.base import PlatformReply

        adapter = SlackAdapter(bot_token="")
        reply = PlatformReply(content="Hi", chat_id="C12345")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════
# WhatsAppAdapter
# ══════════════════════════════════════════════════════════════════════

class TestWhatsAppAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.whatsapp import WhatsAppAdapter
        with patch.dict("os.environ", {
            "WHATSAPP_CLOUD_TOKEN": "cloud-token-abc",
            "WHATSAPP_PHONE_ID": "123456789",
        }):
            adapter = WhatsAppAdapter()
            assert adapter.cloud_token == "cloud-token-abc"
            assert adapter.phone_number_id == "123456789"

    def test_verify_webhook_always_returns_true(self):
        from dragon.gateway.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_hub_verification(self):
        from dragon.gateway.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter(verify_token="my-verify-token")
        body = {
            "_method": "GET",
            "hub.mode": "subscribe",
            "hub.verify_token": "my-verify-token",
            "hub.challenge": "challenge-abc-123",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "whatsapp"
        assert msg.chat_id == "__challenge__"
        assert msg.content == "challenge-abc-123"

    def test_parse_webhook_hub_verification_wrong_token(self):
        from dragon.gateway.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter(verify_token="correct-token")
        body = {
            "_method": "GET",
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-token",
            "hub.challenge": "challenge",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_parse_webhook_message_entry(self):
        from dragon.gateway.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter()
        body = {
            "object": "whatsapp_business_account",
            "entry": [{
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "messages": [{
                            "from": "5511999999999",
                            "id": "wamid.abc123",
                            "type": "text",
                            "text": {"body": "Hello from WhatsApp"},
                        }],
                    },
                }],
            }],
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "whatsapp"
        assert msg.chat_id == "5511999999999"
        assert msg.user_id == "5511999999999"
        assert msg.content == "Hello from WhatsApp"
        assert msg.message_id == "wamid.abc123"

    def test_parse_webhook_non_text_message_skipped(self):
        from dragon.gateway.whatsapp import WhatsAppAdapter
        adapter = WhatsAppAdapter()
        body = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "5511999999999",
                            "id": "wamid.xyz",
                            "type": "image",
                            "image": {"id": "img-123"},
                        }],
                    },
                }],
            }],
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None


# ══════════════════════════════════════════════════════════════════════
# SignalAdapter
# ══════════════════════════════════════════════════════════════════════

class TestSignalAdapter:
    def test_constructor_reads_env_with_default(self):
        from dragon.gateway.signal_adapter import SignalAdapter
        adapter = SignalAdapter()
        assert adapter.rest_url == "http://localhost:8080"

    def test_constructor_custom_rest_url(self):
        from dragon.gateway.signal_adapter import SignalAdapter
        with patch.dict("os.environ", {"SIGNAL_REST_URL": "http://signal-api:9000"}):
            adapter = SignalAdapter()
            assert adapter.rest_url == "http://signal-api:9000"

    def test_verify_webhook_always_returns_true(self):
        from dragon.gateway.signal_adapter import SignalAdapter
        adapter = SignalAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_signal_cli_format(self):
        from dragon.gateway.signal_adapter import SignalAdapter
        adapter = SignalAdapter()
        body = {
            "envelope": {
                "source": "+12345678901",
                "sourceNumber": "+12345678901",
                "dataMessage": {
                    "message": "Hello from Signal",
                    "timestamp": 1700000000000,
                },
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "signal"
        assert msg.chat_id == "+12345678901"
        assert msg.user_id == "+12345678901"
        assert msg.content == "Hello from Signal"
        assert msg.message_id == "1700000000000"

    def test_parse_webhook_skips_sync_message(self):
        from dragon.gateway.signal_adapter import SignalAdapter
        adapter = SignalAdapter()
        body = {
            "envelope": {
                "source": "+12345678901",
                "syncMessage": {"type": "read"},
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_webhook_path(self):
        from dragon.gateway.signal_adapter import SignalAdapter
        adapter = SignalAdapter()
        assert adapter.webhook_path == "/signal/webhook"


# ══════════════════════════════════════════════════════════════════════
# DingTalkAdapter
# ══════════════════════════════════════════════════════════════════════

class TestDingTalkAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.dingtalk import DingTalkAdapter
        with patch.dict("os.environ", {
            "DINGTALK_APP_KEY": "dingabc123",
            "DINGTALK_APP_SECRET": "secret-xyz",
        }):
            adapter = DingTalkAdapter()
            assert adapter.app_key == "dingabc123"
            assert adapter.app_secret == "secret-xyz"

    def test_parse_webhook_robot_text_message(self):
        from dragon.gateway.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter()
        body = {
            "msgtype": "text",
            "text": {"content": "你好 DingTalk"},
            "senderId": "$:user123",
            "senderNick": "TestUser",
            "sessionWebhook": "https://oapi.dingtalk.com/robot/send?access_token=...",
            "conversationId": "cid789",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "dingtalk"
        assert msg.chat_id == "cid789"
        assert msg.user_id == "$:user123"
        assert msg.content == "你好 DingTalk"

    def test_parse_webhook_check_url_challenge(self):
        from dragon.gateway.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter()
        body = {"msgtype": "check_url"}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "dingtalk"
        assert msg.chat_id == "__challenge__"
        assert msg.content == "__check_url__"

    def test_verify_webhook_with_hmac_signature(self):
        import base64, hmac, hashlib
        from dragon.gateway.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(app_secret="test-secret")
        timestamp = "1700000000000"
        string_to_sign = f"{timestamp}\ntest-secret"
        computed = hmac.new(
            "test-secret".encode(), string_to_sign.encode(), hashlib.sha256
        ).digest()
        expected_sig = base64.b64encode(computed).decode()
        headers = {
            "x-ak-dingtalk-signature": expected_sig,
            "x-ak-dingtalk-timestamp": timestamp,
        }
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook(headers, b"{}")
        )
        assert result is True

    def test_verify_webhook_no_secret_allows_all(self):
        from dragon.gateway.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(app_secret="")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True


# ══════════════════════════════════════════════════════════════════════
# WeComAdapter
# ══════════════════════════════════════════════════════════════════════

class TestWeComAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.wecom import WeComAdapter
        with patch.dict("os.environ", {
            "WECOM_CORP_ID": "ww123456",
            "WECOM_CORP_SECRET": "corp-secret-xyz",
        }):
            adapter = WeComAdapter()
            assert adapter.corp_id == "ww123456"
            assert adapter.corp_secret == "corp-secret-xyz"

    def test_verify_webhook_returns_true(self):
        from dragon.gateway.wecom import WeComAdapter
        adapter = WeComAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_echostr_verification(self):
        import hashlib
        from dragon.gateway.wecom import WeComAdapter
        adapter = WeComAdapter(token="test-token")
        # Build a valid sha1 signature for the test token
        ts = "1700000000"
        nonce = "abc123"
        tmp = sorted(["test-token", ts, nonce])
        sig = hashlib.sha1("".join(tmp).encode()).hexdigest()
        body = {
            "_method": "GET",
            "msg_signature": sig,
            "timestamp": ts,
            "nonce": nonce,
            "echostr": "echo-test-value",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "wecom"
        assert msg.chat_id == "__challenge__"
        # Content should be the echostr (no encryption key, so _decrypt_echostr returns raw)
        assert msg.content == "echo-test-value"

    def test_parse_webhook_xml_text_message(self):
        from dragon.gateway.wecom import WeComAdapter
        adapter = WeComAdapter()
        xml_body = (
            "<xml>"
            "<ToUserName><![CDATA[ww123]]></ToUserName>"
            "<FromUserName><![CDATA[user_zhangsan]]></FromUserName>"
            "<CreateTime>1700000000</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[你好企业微信]]></Content>"
            "<MsgId>123456</MsgId>"
            "</xml>"
        )
        body = {"_xml_body": xml_body}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "wecom"
        assert msg.chat_id == "user_zhangsan"
        assert msg.user_id == "user_zhangsan"
        assert msg.content == "你好企业微信"
        assert msg.message_id == "123456"

    def test_parse_webhook_non_text_xml_skipped(self):
        from dragon.gateway.wecom import WeComAdapter
        adapter = WeComAdapter()
        xml_body = (
            "<xml>"
            "<ToUserName><![CDATA[ww123]]></ToUserName>"
            "<FromUserName><![CDATA[user_zhangsan]]></FromUserName>"
            "<MsgType><![CDATA[image]]></MsgType>"
            "<MediaId><![CDATA[media-123]]></MediaId>"
            "</xml>"
        )
        body = {"_xml_body": xml_body}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_webhook_path(self):
        from dragon.gateway.wecom import WeComAdapter
        adapter = WeComAdapter()
        assert adapter.webhook_path == "/wecom/webhook"


# ══════════════════════════════════════════════════════════════════════
# GenericWebhookAdapter
# ══════════════════════════════════════════════════════════════════════

class TestGenericWebhookAdapter:
    def test_constructor_with_secret_from_env(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        with patch.dict("os.environ", {"WEBHOOK_SECRET": "shared-secret-123"}):
            adapter = GenericWebhookAdapter()
            assert adapter.secret == "shared-secret-123"

    def test_constructor_without_secret(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        with patch.dict("os.environ", {}, clear=True):
            adapter = GenericWebhookAdapter()
            assert adapter.secret == ""

    def test_verify_webhook_no_secret_returns_true(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_verify_webhook_matching_signature(self):
        import hmac, hashlib
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter(secret="my-secret")
        body = b'{"test": true}'
        expected = hmac.new(
            "my-secret".encode(), body, hashlib.sha256
        ).hexdigest()
        headers = {"x-signature": expected}
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook(headers, body)
        )
        assert result is True

    def test_verify_webhook_non_matching_signature(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter(secret="my-secret")
        body = b'{"test": true}'
        headers = {"x-signature": "bad-signature-value"}
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook(headers, body)
        )
        assert result is False

    def test_verify_webhook_no_signature_header_returns_true(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter(secret="my-secret")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_simple_json(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter()
        body = {
            "content": "Hello from webhook",
            "chat_id": "channel-1",
            "user_id": "external-user",
            "message_id": "msg-001",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "webhook"
        assert msg.chat_id == "channel-1"
        assert msg.user_id == "external-user"
        assert msg.content == "Hello from webhook"
        assert msg.message_id == "msg-001"

    def test_parse_webhook_text_fallback(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter()
        body = {
            "text": "Fallback text content",
            "chat_id": "channel-2",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.content == "Fallback text content"
        assert msg.chat_id == "channel-2"

    def test_parse_webhook_message_fallback(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter()
        body = {
            "message": "Message field fallback",
            "chat_id": "channel-3",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.content == "Message field fallback"

    def test_parse_webhook_no_content_returns_none(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter()
        body = {"chat_id": "channel-x", "user_id": "user-x"}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_send_message_returns_true(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        from dragon.gateway.base import PlatformReply
        adapter = GenericWebhookAdapter()
        reply = PlatformReply(content="test", chat_id="ch1")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is True

    def test_webhook_path(self):
        from dragon.gateway.webhook import GenericWebhookAdapter
        adapter = GenericWebhookAdapter()
        assert adapter.webhook_path == "/webhook/webhook"


# ══════════════════════════════════════════════════════════════════════
# SMSAdapter
# ══════════════════════════════════════════════════════════════════════

class TestSMSAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.sms import SMSAdapter
        with patch.dict("os.environ", {
            "TWILIO_ACCOUNT_SID": "AC123test",
            "TWILIO_AUTH_TOKEN": "auth-token-xyz",
            "TWILIO_PHONE_NUMBER": "+15551234567",
        }):
            adapter = SMSAdapter()
            assert adapter.account_sid == "AC123test"
            assert adapter.auth_token == "auth-token-xyz"
            assert adapter.phone_number == "+15551234567"

    def test_constructor_explicit_values_override_env(self):
        from dragon.gateway.sms import SMSAdapter
        with patch.dict("os.environ", {
            "TWILIO_ACCOUNT_SID": "env-sid",
            "TWILIO_AUTH_TOKEN": "env-token",
            "TWILIO_PHONE_NUMBER": "+1111",
        }):
            adapter = SMSAdapter(
                account_sid="explicit-sid",
                auth_token="explicit-token",
                phone_number="+2222",
            )
            assert adapter.account_sid == "explicit-sid"
            assert adapter.auth_token == "explicit-token"
            assert adapter.phone_number == "+2222"

    def test_webhook_path(self):
        from dragon.gateway.sms import SMSAdapter
        adapter = SMSAdapter()
        assert adapter.webhook_path == "/sms/webhook"

    def test_verify_webhook_returns_true(self):
        from dragon.gateway.sms import SMSAdapter
        adapter = SMSAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_form_encoded_body(self):
        from dragon.gateway.sms import SMSAdapter
        adapter = SMSAdapter()
        body = {
            "Body": "Hello from SMS",
            "From": "+15551234567",
            "MessageSid": "SMabc123",
            "To": "+15559876543",
            "SmsStatus": "received",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "sms"
        assert msg.chat_id == "+15551234567"
        assert msg.user_id == "+15551234567"
        assert msg.content == "Hello from SMS"
        assert msg.message_id == "SMabc123"

    def test_parse_webhook_raw_bytes_fallback(self):
        from dragon.gateway.sms import SMSAdapter
        adapter = SMSAdapter()
        body = {
            "_raw_body": b"Body=Hello+raw&From=%2B15559999999&MessageSid=SMraw999",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "sms"
        assert msg.chat_id == "+15559999999"
        assert msg.content == "Hello raw"
        assert msg.message_id == "SMraw999"

    def test_parse_webhook_empty_body_returns_none(self):
        from dragon.gateway.sms import SMSAdapter
        adapter = SMSAdapter()
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook({})
        )
        assert msg is None

    def test_send_message_calls_twilio_api(self):
        from dragon.gateway.sms import SMSAdapter
        from dragon.gateway.base import PlatformReply

        adapter = SMSAdapter(
            account_sid="ACtest", auth_token="test-token", phone_number="+1555"
        )
        reply = PlatformReply(content="Hi from SMS", chat_id="+15551234567")

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = mock_post
            result = asyncio.new_event_loop().run_until_complete(
                adapter.send_message(reply)
            )
            assert result is True
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["auth"] == ("ACtest", "test-token")
            assert call_kwargs.kwargs["data"]["To"] == "+15551234567"
            assert call_kwargs.kwargs["data"]["Body"] == "Hi from SMS"

    def test_send_message_no_credentials_returns_false(self):
        from dragon.gateway.sms import SMSAdapter
        from dragon.gateway.base import PlatformReply

        adapter = SMSAdapter(account_sid="", auth_token="")
        reply = PlatformReply(content="Hi", chat_id="+15551234567")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════
# EmailAdapter
# ══════════════════════════════════════════════════════════════════════

class TestEmailAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.email_adapter import EmailAdapter
        with patch.dict("os.environ", {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "bot@example.com",
            "SMTP_PASS": "s3cr3t",
        }):
            adapter = EmailAdapter()
            assert adapter.smtp_host == "smtp.example.com"
            assert adapter.smtp_port == 465
            assert adapter.smtp_user == "bot@example.com"
            assert adapter.smtp_pass == "s3cr3t"

    def test_constructor_defaults(self):
        from dragon.gateway.email_adapter import EmailAdapter
        with patch.dict("os.environ", {}, clear=True):
            adapter = EmailAdapter()
            assert adapter.smtp_host == "smtp.gmail.com"
            assert adapter.smtp_port == 587
            assert adapter.smtp_user == ""
            assert adapter.smtp_pass == ""

    def test_webhook_path(self):
        from dragon.gateway.email_adapter import EmailAdapter
        adapter = EmailAdapter()
        assert adapter.webhook_path == "/email/webhook"

    def test_verify_webhook_returns_true(self):
        from dragon.gateway.email_adapter import EmailAdapter
        adapter = EmailAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_email_dict(self):
        from dragon.gateway.email_adapter import EmailAdapter
        adapter = EmailAdapter()
        body = {
            "from": "sender@example.com",
            "to": "bot@example.com",
            "subject": "Test email",
            "body": "This is the email body",
            "message_id": "<msg-001@example.com>",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "email"
        assert msg.chat_id == "sender@example.com"
        assert msg.user_id == "sender@example.com"
        assert msg.content == "This is the email body"
        assert msg.message_id == "<msg-001@example.com>"

    def test_parse_webhook_empty_body_returns_none(self):
        from dragon.gateway.email_adapter import EmailAdapter
        adapter = EmailAdapter()
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook({"from": "a@b.com"})
        )
        assert msg is None

    def test_send_message_constructs_mime_and_uses_smtp(self):
        from dragon.gateway.email_adapter import EmailAdapter
        from dragon.gateway.base import PlatformReply

        adapter = EmailAdapter(
            smtp_host="smtp.test.com", smtp_port=587,
            smtp_user="bot@test.com", smtp_pass="pass123",
        )
        reply = PlatformReply(content="Hello via email", chat_id="user@test.com")

        mock_server = MagicMock()
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value = mock_server
            result = asyncio.new_event_loop().run_until_complete(
                adapter.send_message(reply)
            )
            assert result is True
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("bot@test.com", "pass123")
            mock_server.sendmail.assert_called_once()
            call_args = mock_server.sendmail.call_args
            assert call_args[0][0] == "bot@test.com"
            assert call_args[0][1] == "user@test.com"
            assert "Hello via email" in call_args[0][2]

    def test_send_message_no_credentials_returns_false(self):
        from dragon.gateway.email_adapter import EmailAdapter
        from dragon.gateway.base import PlatformReply

        adapter = EmailAdapter(smtp_user="", smtp_pass="")
        reply = PlatformReply(content="Hi", chat_id="user@test.com")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════
# MatrixAdapter
# ══════════════════════════════════════════════════════════════════════

class TestMatrixAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.matrix import MatrixAdapter
        with patch.dict("os.environ", {
            "MATRIX_HOMESERVER": "https://matrix.example.com",
            "MATRIX_ACCESS_TOKEN": "syt_test123",
        }):
            adapter = MatrixAdapter()
            assert adapter.homeserver == "https://matrix.example.com"
            assert adapter.access_token == "syt_test123"

    def test_constructor_defaults(self):
        from dragon.gateway.matrix import MatrixAdapter
        with patch.dict("os.environ", {}, clear=True):
            adapter = MatrixAdapter()
            assert adapter.homeserver == "https://matrix.org"
            assert adapter.access_token == ""

    def test_webhook_path(self):
        from dragon.gateway.matrix import MatrixAdapter
        adapter = MatrixAdapter()
        assert adapter.webhook_path == "/matrix/webhook"

    def test_verify_webhook_returns_true(self):
        from dragon.gateway.matrix import MatrixAdapter
        adapter = MatrixAdapter()
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_m_room_message_event(self):
        from dragon.gateway.matrix import MatrixAdapter
        adapter = MatrixAdapter()
        body = {
            "room_id": "!abc123:matrix.org",
            "event_id": "$event456",
            "sender": "@user:matrix.org",
            "content": {
                "msgtype": "m.text",
                "body": "Hello from Matrix",
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "matrix"
        assert msg.chat_id == "!abc123:matrix.org"
        assert msg.user_id == "@user:matrix.org"
        assert msg.content == "Hello from Matrix"
        assert msg.message_id == "$event456"

    def test_parse_webhook_non_text_msgtype_skipped(self):
        from dragon.gateway.matrix import MatrixAdapter
        adapter = MatrixAdapter()
        body = {
            "room_id": "!abc:matrix.org",
            "event_id": "$img789",
            "sender": "@user:matrix.org",
            "content": {
                "msgtype": "m.image",
                "body": "image.png",
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_parse_webhook_empty_content_skipped(self):
        from dragon.gateway.matrix import MatrixAdapter
        adapter = MatrixAdapter()
        body = {
            "room_id": "!abc:matrix.org",
            "sender": "@user:matrix.org",
            "content": {"msgtype": "m.text", "body": ""},
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_send_message_calls_matrix_api(self):
        from dragon.gateway.matrix import MatrixAdapter
        from dragon.gateway.base import PlatformReply

        adapter = MatrixAdapter(
            homeserver="https://matrix.test.org", access_token="syt_fake"
        )
        reply = PlatformReply(content="Hello Matrix", chat_id="!room:matrix.org")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_put = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.put = mock_put
            result = asyncio.new_event_loop().run_until_complete(
                adapter.send_message(reply)
            )
            assert result is True
            mock_put.assert_called_once()
            call_kwargs = mock_put.call_args
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer syt_fake"
            assert call_kwargs.kwargs["json"]["msgtype"] == "m.text"
            assert call_kwargs.kwargs["json"]["body"] == "Hello Matrix"

    def test_send_message_no_credentials_returns_false(self):
        from dragon.gateway.matrix import MatrixAdapter
        from dragon.gateway.base import PlatformReply

        adapter = MatrixAdapter(access_token="")
        reply = PlatformReply(content="Hi", chat_id="!room:matrix.org")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════
# MattermostAdapter
# ══════════════════════════════════════════════════════════════════════

class TestMattermostAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.mattermost import MattermostAdapter
        with patch.dict("os.environ", {
            "MATTERMOST_URL": "https://mm.example.com",
            "MATTERMOST_TOKEN": "bot-token-abc",
        }):
            adapter = MattermostAdapter()
            assert adapter.server_url == "https://mm.example.com"
            assert adapter.bot_token == "bot-token-abc"

    def test_constructor_explicit_values_override_env(self):
        from dragon.gateway.mattermost import MattermostAdapter
        with patch.dict("os.environ", {
            "MATTERMOST_URL": "env-url",
            "MATTERMOST_TOKEN": "env-token",
        }):
            adapter = MattermostAdapter(
                server_url="https://explicit.example.com",
                bot_token="explicit-token",
            )
            assert adapter.server_url == "https://explicit.example.com"
            assert adapter.bot_token == "explicit-token"

    def test_webhook_path(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter()
        assert adapter.webhook_path == "/mattermost/webhook"

    def test_verify_webhook_valid_bearer_token(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter(bot_token="my-secret-token")
        headers = {"Authorization": "Bearer my-secret-token"}
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook(headers, b"{}")
        )
        assert result is True

    def test_verify_webhook_wrong_token(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter(bot_token="my-secret-token")
        headers = {"Authorization": "Bearer wrong-token"}
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook(headers, b"{}")
        )
        assert result is False

    def test_verify_webhook_no_token_configured_allows_all(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter(bot_token="")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_parse_webhook_slash_command(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter()
        body = {
            "token": "verification-token",
            "team_id": "team123",
            "channel_id": "channel456",
            "user_id": "user789",
            "command": "/dragon",
            "text": "hello world",
            "response_url": "https://mm.example.com/hooks/abc",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "mattermost"
        assert msg.chat_id == "channel456"
        assert msg.user_id == "user789"
        assert msg.content == "hello world"

    def test_parse_webhook_outgoing_webhook(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter()
        body = {
            "token": "hook-token",
            "team_id": "team111",
            "channel_id": "channel222",
            "user_id": "user333",
            "text": "!dragon how are you",
            "trigger_word": "!dragon",
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "mattermost"
        assert msg.chat_id == "channel222"
        assert msg.user_id == "user333"
        # trigger word stripped
        assert msg.content == "how are you"

    def test_parse_webhook_no_text_returns_none(self):
        from dragon.gateway.mattermost import MattermostAdapter
        adapter = MattermostAdapter()
        body = {"channel_id": "c1", "user_id": "u1"}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_send_message_calls_mattermost_api(self):
        from dragon.gateway.mattermost import MattermostAdapter
        from dragon.gateway.base import PlatformReply

        adapter = MattermostAdapter(
            server_url="https://mm.test.com", bot_token="bot-token-123"
        )
        reply = PlatformReply(content="Hello Mattermost", chat_id="channel789")

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = mock_post
            result = asyncio.new_event_loop().run_until_complete(
                adapter.send_message(reply)
            )
            assert result is True
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["json"]["channel_id"] == "channel789"
            assert call_kwargs.kwargs["json"]["message"] == "Hello Mattermost"
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer bot-token-123"

    def test_send_message_no_credentials_returns_false(self):
        from dragon.gateway.mattermost import MattermostAdapter
        from dragon.gateway.base import PlatformReply

        adapter = MattermostAdapter(server_url="", bot_token="")
        reply = PlatformReply(content="Hi", chat_id="channel1")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is False


# ══════════════════════════════════════════════════════════════════════
# QQBotAdapter
# ══════════════════════════════════════════════════════════════════════

class TestQQBotAdapter:
    def test_constructor_reads_env_vars(self):
        from dragon.gateway.qqbot import QQBotAdapter
        with patch.dict("os.environ", {
            "QQ_BOT_APP_ID": "102012345",
            "QQ_BOT_TOKEN": "qq-token-abc",
        }):
            adapter = QQBotAdapter()
            assert adapter.app_id == "102012345"
            assert adapter.token == "qq-token-abc"

    def test_constructor_explicit_values_override_env(self):
        from dragon.gateway.qqbot import QQBotAdapter
        with patch.dict("os.environ", {
            "QQ_BOT_APP_ID": "env-app-id",
            "QQ_BOT_TOKEN": "env-token",
        }):
            adapter = QQBotAdapter(app_id="explicit-id", token="explicit-token")
            assert adapter.app_id == "explicit-id"
            assert adapter.token == "explicit-token"

    def test_webhook_path(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter()
        assert adapter.webhook_path == "/qqbot/webhook"

    def test_verify_webhook_no_token_returns_true(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter(token="")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook({}, b"{}")
        )
        assert result is True

    def test_verify_webhook_no_signature_headers_returns_true(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter(token="some-token")
        headers = {}
        result = asyncio.new_event_loop().run_until_complete(
            adapter.verify_webhook(headers, b"{}")
        )
        assert result is True

    def test_parse_webhook_message_create_event(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter()
        body = {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "msg_abc123",
                "author": {"id": "user_qq_456"},
                "content": "Hello QQ Bot",
                "channel_id": "chan_789",
                "guild_id": "guild_001",
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "qqbot"
        assert msg.chat_id == "chan_789"
        assert msg.user_id == "user_qq_456"
        assert msg.content == "Hello QQ Bot"
        assert msg.message_id == "msg_abc123"

    def test_parse_webhook_group_message_uses_group_openid(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter()
        body = {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "d": {
                "id": "msg_group_1",
                "author": {"id": "group_user"},
                "content": "Group chat message",
                "group_openid": "group_open_123",
            },
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.chat_id == "group_open_123"
        assert msg.content == "Group chat message"

    def test_parse_webhook_verification_event(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter()
        body = {
            "op": 1,
            "t": "VERIFICATION",
            "d": {"plain_token": "verify-abc", "event_ts": "1234567890"},
        }
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is not None
        assert msg.platform == "qqbot"
        assert msg.chat_id == "__challenge__"

    def test_parse_webhook_non_message_create_skipped(self):
        from dragon.gateway.qqbot import QQBotAdapter
        adapter = QQBotAdapter()
        body = {"op": 0, "t": "GUILD_CREATE", "d": {}}
        msg = asyncio.new_event_loop().run_until_complete(
            adapter.parse_webhook(body)
        )
        assert msg is None

    def test_send_message_calls_qq_bot_api(self):
        from dragon.gateway.qqbot import QQBotAdapter
        from dragon.gateway.base import PlatformReply

        adapter = QQBotAdapter(app_id="1020test", token="qq-token-test")
        reply = PlatformReply(content="Hello from QQ", chat_id="g12345")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = mock_post
            result = asyncio.new_event_loop().run_until_complete(
                adapter.send_message(reply)
            )
            assert result is True
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["json"]["content"] == "Hello from QQ"
            assert call_kwargs.kwargs["json"]["msg_type"] == 0
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bot 1020test.qq-token-test"

    def test_send_message_no_credentials_returns_false(self):
        from dragon.gateway.qqbot import QQBotAdapter
        from dragon.gateway.base import PlatformReply

        adapter = QQBotAdapter(app_id="", token="")
        reply = PlatformReply(content="Hi", chat_id="g12345")
        result = asyncio.new_event_loop().run_until_complete(
            adapter.send_message(reply)
        )
        assert result is False
