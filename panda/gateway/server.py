"""
Panda Gateway Server — FastAPI webhook router for all platforms.

Ties together:
- Platform adapters (Feishu, Telegram)
- Session management
- Provider registry
- Message processing pipeline

Usage::

    from panda.gateway import GatewayServer
    from panda.gateway.feishu import FeishuAdapter
    from panda.gateway.telegram import TelegramAdapter

    server = GatewayServer(provider_registry=pr, session_store=ss)
    server.register_adapter(FeishuAdapter(app_id="...", app_secret="..."))
    server.register_adapter(TelegramAdapter(bot_token="..."))

    import uvicorn
    uvicorn.run(server.app, host="0.0.0.0", port=8000)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse

from panda.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply

logger = logging.getLogger("panda.gateway.server")


# ────────────────────────────────────────────────────────────────────
# Message Processor
# ────────────────────────────────────────────────────────────────────


class MessageProcessor:
    """Core processing pipeline: message → AI response → reply.

    Handles:
    - Session lookup/creation
    - Context management (compression)
    - Provider call
    - Response formatting
    """

    def __init__(
        self,
        provider_registry: Any = None,
        session_store: Any = None,
        tool_registry: Any = None,
        skill_engine: Any = None,
        compression_config: Any = None,
        max_tool_iterations: int = 5,
    ) -> None:
        self.provider_registry = provider_registry
        self.session_store = session_store
        self.tool_registry = tool_registry
        self.skill_engine = skill_engine
        self.max_tool_iterations = max_tool_iterations

        if compression_config:
            from panda.compression import ContextCompressor
            self.compressor = ContextCompressor(
                config=compression_config,
            )
        else:
            self.compressor = None

    async def process(
        self,
        message: PlatformMessage,
        system_prompt: str = "",
    ) -> PlatformReply:
        """Process a message and return a reply.

        Pipeline:
        1. Look up or create session
        2. Load conversation history
        3. Check for skill matches
        4. Call provider
        5. Save response to session
        6. Return formatted reply
        """
        start = time.monotonic()

        # 1. Session lookup
        session = None
        if self.session_store:
            session = self.session_store.get(message.session_id)
            if session is None:
                session = self.session_store.create(
                    title=message.content[:50],
                    platform=message.platform,
                )

        # 2. Build message history
        history = []
        if system_prompt:
            history.append({"role": "system", "content": system_prompt})

        if self.session_store and session:
            past_msgs = self.session_store.get_messages(
                session.id, limit=50
            )
            history.extend([
                {"role": m.role, "content": m.content}
                for m in past_msgs[-20:]  # last 20 messages
            ])

        history.append({"role": "user", "content": message.content})

        # 3. Compress if needed
        if self.compressor and self.compressor.needs_compression(history):
            history = self.compressor.compress(history)

        # 4. Skill matching
        skill_context = ""
        if self.skill_engine:
            try:
                matches = await self.skill_engine.discover(message.content, top_k=1)
                if matches:
                    skill_context = (
                        f"\n\n[相关技能: {matches[0].skill_name} "
                        f"(成功率: {matches[0].skill.success_rate:.0%})]\n"
                        f"{matches[0].skill.content[:500]}"
                    )
                    if history[0]["role"] == "system":
                        history[0]["content"] += skill_context
            except Exception as e:
                logger.debug("Skill matching skipped: %s", e)

        # 5. Agent loop: call provider → parse tool calls → execute → repeat
        reply_text = ""
        tool_call_count = 0

        for iteration in range(self.max_tool_iterations):
            try:
                if self.provider_registry:
                    # Build tool schemas for the provider
                    tool_schemas = None
                    if self.tool_registry:
                        tool_schemas = self.tool_registry.get_openai_schemas()

                    result = await self.provider_registry.call(
                        provider_name="openai",
                        messages=history,
                        max_tokens=2048,
                    )
                    response_text = result.content
                else:
                    response_text = (
                        "[Panda Agent 未配置 Provider]\n\n"
                        "请设置环境变量: OPENAI_API_KEY 或 DEEPSEEK_API_KEY"
                    )
                    break
            except Exception as e:
                logger.exception("Provider call failed")
                reply_text = f"抱歉，处理您的消息时出错: {e}"
                break

            # Parse tool calls from the response
            tool_calls = self._parse_tool_calls(response_text)

            if not tool_calls:
                # No tool calls — this is the final answer
                reply_text = response_text
                break

            # Execute tool calls
            if self.tool_registry:
                history.append({"role": "assistant", "content": response_text})

                tool_outputs = []
                for tc in tool_calls:
                    tool_call_count += 1
                    try:
                        tool_result = await self.tool_registry.call(
                            tc["name"], tc.get("arguments", {})
                        )
                        output = str(tool_result.output) if tool_result.success else tool_result.error
                    except Exception as e:
                        output = f"Tool error: {e}"

                    tool_outputs.append({
                        "tool": tc["name"],
                        "output": output[:2000],
                    })
                    history.append({
                        "role": "tool",
                        "content": output[:2000],
                        "name": tc["name"],
                    })

                # Check if we should continue
                if iteration == self.max_tool_iterations - 1:
                    # Last iteration — ask model to summarize
                    history.append({
                        "role": "user",
                        "content": "Please provide your final answer based on the tool results above.",
                    })
            else:
                # No tool registry — treat as final response
                reply_text = response_text
                break

        # If loop ended without final answer, use accumulated context
        if not reply_text and history:
            last_msg = history[-1]["content"]
            reply_text = f"[已完成 {tool_call_count} 次工具调用]\n\n{last_msg[:2000]}"

        # 6. Save to session
        if self.session_store and session:
            self.session_store.add_message(session.id, "user", message.content)
            self.session_store.add_message(session.id, "assistant", reply_text)

        return PlatformReply(
            content=reply_text,
            chat_id=message.chat_id,
            thread_id=message.thread_id,
            reply_to_message_id=message.message_id,
        )

    def _parse_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Parse tool call requests from model output.

        Supports:
        - OpenAI native tool_calls (parsed from text if provider doesn't support)
        - ```tool_call\n{...}\n``` blocks
        - <tool_call>{...}</tool_call> XML tags
        """
        calls = []

        # Format 1: ```tool_call ... ```
        for match in re.finditer(r'```tool_call\s*\n(.*?)\n```', content, re.DOTALL):
            try:
                calls.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass

        # Format 2: <tool_call>...</tool_call>
        for match in re.finditer(r'<tool_call>(.*?)</tool_call>', content, re.DOTALL):
            try:
                calls.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                pass

        return calls


# ────────────────────────────────────────────────────────────────────
# Gateway Server (FastAPI)
# ────────────────────────────────────────────────────────────────────


class GatewayServer:
    """FastAPI-based gateway server for all messaging platforms.

    Parameters
    ----------
    provider_registry : ProviderRegistry
        Provider registry for LLM calls.
    session_store : SessionStore
        Session store for conversation persistence.
    tool_registry : ToolRegistry
        Tool registry for tool-enabled responses.
    skill_engine : SkillEngine
        Skill engine for semantic skill matching.
    system_prompt : str
        Default system prompt for all platforms.
    """

    def __init__(
        self,
        provider_registry: Any = None,
        session_store: Any = None,
        tool_registry: Any = None,
        skill_engine: Any = None,
        system_prompt: str = "",
    ) -> None:
        self.app = FastAPI(title="Panda Gateway", version="1.0.0")
        self.adapters: Dict[str, PlatformAdapter] = {}
        self.processor = MessageProcessor(
            provider_registry=provider_registry,
            session_store=session_store,
            tool_registry=tool_registry,
            skill_engine=skill_engine,
        )
        self.system_prompt = system_prompt or (
            "你是 Panda Agent，一个能够自我进化的 AI 助手。\n"
            "你的技能会随着使用不断改进。回答简洁、准确、有帮助。"
        )

        # Register routes
        self._register_routes()

        logger.info("GatewayServer ready")

    def register_adapter(self, adapter: PlatformAdapter) -> None:
        """Register a platform adapter."""
        self.adapters[adapter.platform_name] = adapter
        logger.info("Registered platform: %s", adapter.platform_name)

    def _register_routes(self) -> None:
        """Register FastAPI routes for all adapters and health check."""

        @self.app.get("/health")
        async def health():
            return {
                "status": "healthy",
                "platforms": list(self.adapters.keys()),
                "timestamp": time.time(),
            }

        @self.app.get("/")
        async def index():
            return {
                "service": "Panda Gateway",
                "version": "1.0.0",
                "platforms": list(self.adapters.keys()),
                "endpoints": {
                    "health": "/health",
                    "feishu": "/feishu/webhook",
                    "telegram": "/telegram/webhook",
                },
            }

        # Generic webhook handler for all platforms
        @self.app.post("/feishu/webhook")
        async def feishu_webhook(request: Request):
            return await self._handle_webhook("feishu", request)

        @self.app.post("/telegram/webhook")
        async def telegram_webhook(request: Request):
            return await self._handle_webhook("telegram", request)

        @self.app.post("/discord/webhook")
        async def discord_webhook(request: Request):
            return await self._handle_webhook("discord", request)

        @self.app.post("/wechat/webhook")
        async def wechat_webhook(request: Request):
            return await self._handle_webhook("wechat", request)

        # Generic catch-all webhook
        @self.app.post("/{platform}/webhook")
        async def generic_webhook(platform: str, request: Request):
            if platform in ("feishu", "telegram", "discord", "wechat"):
                return await self._handle_webhook(platform, request)
            raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")

    async def _handle_webhook(self, platform: str, request: Request) -> JSONResponse:
        """Handle an incoming webhook for any platform."""
        adapter = self.adapters.get(platform)
        if adapter is None:
            raise HTTPException(status_code=404, detail=f"Platform '{platform}' not configured")

        # Read body
        body = await request.body()
        headers = dict(request.headers)

        # Verify signature
        if not await adapter.verify_webhook(headers, body):
            logger.warning("Webhook verification failed for %s", platform)
            raise HTTPException(status_code=403, detail="Signature verification failed")

        # Parse body
        try:
            body_dict = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        # Handle Feishu URL verification challenge
        if platform == "feishu" and body_dict.get("type") == "url_verification":
            challenge = body_dict.get("challenge", "")
            return JSONResponse({"challenge": challenge})

        # Parse message
        message = await adapter.parse_webhook(body_dict)
        if message is None:
            return JSONResponse({"status": "ignored"})

        # Process message (fire-and-forget for Telegram, sync for others)
        if platform == "telegram":
            # Telegram expects a quick 200 OK, then sends the reply separately
            asyncio.create_task(self._process_and_reply(adapter, message))
            return JSONResponse({"status": "ok"})
        else:
            reply = await self.processor.process(message, self.system_prompt)
            await adapter.send_message(reply)
            return JSONResponse({"status": "ok"})

    async def _process_and_reply(
        self, adapter: PlatformAdapter, message: PlatformMessage
    ) -> None:
        """Process a message and send the reply (for fire-and-forget platforms)."""
        try:
            reply = await self.processor.process(message, self.system_prompt)
            await adapter.send_message(reply)
        except Exception as e:
            logger.exception("Async reply failed: %s", e)


# ────────────────────────────────────────────────────────────────────
# Quick Start Helper
# ────────────────────────────────────────────────────────────────────


def create_feishu_gateway(
    app_id: str = "",
    app_secret: str = "",
    provider_registry: Any = None,
    session_store: Any = None,
) -> GatewayServer:
    """Quick-start a Feishu-only gateway."""
    from panda.gateway.feishu import FeishuAdapter

    server = GatewayServer(
        provider_registry=provider_registry,
        session_store=session_store,
    )
    server.register_adapter(FeishuAdapter(
        app_id=app_id, app_secret=app_secret,
    ))
    return server


def create_telegram_gateway(
    bot_token: str = "",
    provider_registry: Any = None,
    session_store: Any = None,
) -> GatewayServer:
    """Quick-start a Telegram-only gateway."""
    from panda.gateway.telegram import TelegramAdapter

    server = GatewayServer(
        provider_registry=provider_registry,
        session_store=session_store,
    )
    server.register_adapter(TelegramAdapter(bot_token=bot_token))
    return server
