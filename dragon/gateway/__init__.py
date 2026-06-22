"""
Dragon Gateway — Multi-Platform Message Gateway
================================================

Abstract platform adapter + concrete implementations for 15 platforms.

Architecture::

    ┌──────────────────────────────────────────────────────────────────────┐
    │                       GatewayServer (FastAPI)                        │
    │  ┌───────────┐ ┌───────────────┐ ┌───────────┐ ┌──────────────────┐ │
    │  │  Feishu   │ │   Telegram    │ │  Discord  │ │     WeChat       │ │
    │  └─────┬─────┘ └──────┬────────┘ └─────┬─────┘ └────────┬─────────┘ │
    │  ┌─────┴─────┐ ┌──────┴────────┐ ┌─────┴─────┐ ┌────────┴─────────┐ │
    │  │   Slack   │ │   WhatsApp    │ │  Signal   │ │    DingTalk      │ │
    │  └─────┬─────┘ └──────┬────────┘ └─────┬─────┘ └────────┬─────────┘ │
    │  ┌─────┴─────┐ ┌──────┴────────┐ ┌─────┴─────┐ ┌────────┴─────────┐ │
    │  │   WeCom   │ │   Webhook     │ │    SMS    │ │     Email        │ │
    │  └─────┬─────┘ └──────┬────────┘ └─────┬─────┘ └────────┬─────────┘ │
    │  ┌─────┴─────┐ ┌──────┴────────┐ ┌─────┴─────┐                    │ │
    │  │  Matrix   │ │  Mattermost   │ │  QQ Bot   │                    │ │
    │  └─────┬─────┘ └──────┬────────┘ └─────┬─────┘                    │ │
    │        │              │               │                            │
    │  ┌─────▼──────────────▼───────────────▼────────────────────────┐   │
    │  │                      Message Router                           │   │
    │  │  → Session lookup/create                                      │   │
    │  │  → Provider call (via registry)                               │   │
    │  │  → Response formatting                                        │   │
    │  └──────────────────────────────────────────────────────────────┘   │
    └──────────────────────────────────────────────────────────────────────┘

Usage::

    from dragon.gateway import (
        GatewayServer, FeishuAdapter, TelegramAdapter,
        DiscordAdapter, WeChatAdapter, SlackAdapter,
        WhatsAppAdapter, SignalAdapter, DingTalkAdapter,
        WeComAdapter, GenericWebhookAdapter,
        SMSAdapter, EmailAdapter, MatrixAdapter,
        MattermostAdapter, QQBotAdapter,
    )

    server = GatewayServer(provider_registry=pr, session_store=ss)
    server.register_adapter(FeishuAdapter(app_id="...", app_secret="..."))
    server.register_adapter(SlackAdapter(bot_token="..."))
    server.register_adapter(SMSAdapter(account_sid="...", auth_token="..."))
    # ... register all desired adapters

    # Run as FastAPI app
    import uvicorn
    uvicorn.run(server.app, host="0.0.0.0", port=8000)
"""
from .base import PlatformAdapter, PlatformMessage, PlatformReply
from .server import GatewayServer

# Existing adapters
from .feishu import FeishuAdapter
from .telegram import TelegramAdapter
from .discord import DiscordAdapter
from .wechat import WeChatAdapter

# New adapters (batch 2)
from .slack import SlackAdapter
from .whatsapp import WhatsAppAdapter
from .signal import SignalAdapter
from .dingtalk import DingTalkAdapter
from .wecom import WeComAdapter
from .webhook import GenericWebhookAdapter

# New adapters (batch 3: SMS/Email/Matrix/Mattermost/QQ Bot)
from .sms import SMSAdapter
from .email import EmailAdapter
from .matrix import MatrixAdapter
from .mattermost import MattermostAdapter
from .qqbot import QQBotAdapter

__all__ = [
    "PlatformAdapter", "PlatformMessage", "PlatformReply",
    "GatewayServer",
    "FeishuAdapter", "TelegramAdapter", "DiscordAdapter", "WeChatAdapter",
    "SlackAdapter", "WhatsAppAdapter", "SignalAdapter",
    "DingTalkAdapter", "WeComAdapter", "GenericWebhookAdapter",
    "SMSAdapter", "EmailAdapter", "MatrixAdapter",
    "MattermostAdapter", "QQBotAdapter",
]
