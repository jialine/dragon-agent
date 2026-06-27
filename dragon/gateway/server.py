"""
Dragon Gateway Server — FastAPI webhook router for all platforms.

Ties together:
- Platform adapters (Feishu, Telegram)
- Session management
- Provider registry
- Message processing pipeline

Usage::

    from dragon.gateway import GatewayServer
    from dragon.gateway.feishu import FeishuAdapter
    from dragon.gateway.telegram import TelegramAdapter

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

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply
from dragon.monitoring import (
    record_request,
    record_latency,
    record_token_consumption,
    record_tool_call,
    record_session_created,
    record_error,
)

logger = logging.getLogger("dragon.gateway.server")


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
    - Voice mode (optional text-to-speech)
    """

    def __init__(
        self,
        provider_registry: Any = None,
        session_store: Any = None,
        tool_registry: Any = None,
        skill_engine: Any = None,
        compression_config: Any = None,
        pairing_store: Any = None,
        voice_engine: Any = None,
        max_tool_iterations: int = 90,
    ) -> None:
        self.provider_registry = provider_registry
        self.session_store = session_store
        self.tool_registry = tool_registry
        self.skill_engine = skill_engine
        self.pairing_store = pairing_store
        self.voice_engine = voice_engine
        self.max_tool_iterations = max_tool_iterations

        # Multi-turn features
        self._steer_queues: Dict[str, List[str]] = {}
        self._message_queues: Dict[str, List[PlatformMessage]] = {}
        self._processing: Dict[str, bool] = {}
        self._progress_callback: Optional[Callable] = None
        self._alert_callback: Optional[Callable] = None  # CRITICAL push
        self._edit_callback: Optional[Callable] = None   # Edit past messages

        if compression_config:
            from dragon.compression import ContextCompressor
            self.compressor = ContextCompressor(
                config=compression_config,
            )
        else:
            self.compressor = None

    # ── Steer / Queue / Progress helpers ──────────────────────

    def set_progress_callback(self, callback: Optional[Callable]) -> None:
        self._progress_callback = callback

    def set_alert_callback(self, callback: Optional[Callable]) -> None:
        """Set callback for immediate CRITICAL/ALERT push during processing."""
        self._alert_callback = callback

    def _extract_critical(self, content: str) -> list:
        """Extract [CRITICAL]/[ALERT]/!!! markers from LLM response.
        Returns list of (level, text) tuples. level: 'critical'|'alert'|'important'.
        """
        import re
        items = []

        # Format 1: [CRITICAL] ... [/CRITICAL]
        for m in re.finditer(r'\[CRITICAL\]\s*\n?(.*?)\n?\[/CRITICAL\]', content, re.DOTALL | re.IGNORECASE):
            items.append(('critical', m.group(1).strip()[:500]))

        # Format 2: [ALERT] ... [/ALERT]
        for m in re.finditer(r'\[ALERT\]\s*\n?(.*?)\n?\[/ALERT\]', content, re.DOTALL | re.IGNORECASE):
            items.append(('alert', m.group(1).strip()[:500]))

        # Format 3: !!! ... !!! (important findings)
        for m in re.finditer(r'!!!\s*(.+?)\s*!!!', content):
            items.append(('important', m.group(1).strip()[:500]))

        # Format 4: [IMPORTANT] ... [/IMPORTANT]
        for m in re.finditer(r'\[IMPORTANT\]\s*\n?(.*?)\n?\[/IMPORTANT\]', content, re.DOTALL | re.IGNORECASE):
            items.append(('important', m.group(1).strip()[:500]))

        return items

    def set_edit_callback(self, callback: Optional[Callable]) -> None:
        """Set callback for editing past messages during processing."""
        self._edit_callback = callback

    def _extract_edits(self, content: str) -> list:
        """Extract [EDIT:target]new text[/EDIT] markers from LLM response.
        Returns list of (target, new_text) tuples.
        target: 'last'|'reply'|'alert'|'progress'.
        """
        import re
        edits = []
        for m in re.finditer(
            r'\[EDIT(?::\s*(\w+))?\]\s*\n?(.*?)\n?\[/EDIT\]',
            content, re.DOTALL | re.IGNORECASE
        ):
            target = (m.group(1) or 'last').strip().lower()
            text = m.group(2).strip()[:1500]
            edits.append((target, text))
        return edits

    def queue_steer(self, chat_id: str, content: str) -> None:
        if chat_id not in self._steer_queues:
            self._steer_queues[chat_id] = []
        self._steer_queues[chat_id].append(content)

    def _pop_steer(self, chat_id: str) -> Optional[str]:
        q = self._steer_queues.get(chat_id, [])
        return q.pop(0) if q else None

    def queue_message(self, message: PlatformMessage) -> None:
        chat_id = getattr(message, 'chat_id', '')
        if chat_id not in self._message_queues:
            self._message_queues[chat_id] = []
        self._message_queues[chat_id].append(message)

    def is_processing(self, chat_id: str) -> bool:
        return self._processing.get(chat_id, False)

    async def _process_single(
        self, message: PlatformMessage, system_prompt: str = "",
        session: Any = None, max_iterations: int = 5,
    ) -> Optional[str]:
        history = []
        if system_prompt:
            history.append({"role": "system", "content": system_prompt})
        if session and self.session_store:
            past = self.session_store.get_messages(session.id, limit=10)
            history.extend([{"role": m.role, "content": m.content} for m in past[-5:]])
        history.append({"role": "user", "content": message.content})
        for _ in range(max_iterations):
            try:
                if self.provider_registry:
                    result = await self.provider_registry.call(
                        provider_name="openai", messages=history, max_tokens=1024)
                    resp = result.content
                else:
                    return None
            except Exception:
                return None
            tcs = self._parse_tool_calls(resp)
            if not tcs:
                return resp
            if self.tool_registry:
                history.append({"role": "assistant", "content": resp})
                for tc in tcs:
                    try:
                        tr = await self.tool_registry.call(tc["name"], tc.get("arguments", {}))
                        out = str(tr.output) if tr.success else tr.error
                    except Exception as e:
                        out = f"Tool error: {e}"
                    history.append({"role": "tool", "content": out[:1000], "name": tc["name"]})
        return None


    async def process(
        self,
        message: PlatformMessage,
        system_prompt: str = "",
        output_mode: str = "text",
        industry: str = "unknown",
        difficulty: str = "unknown",
    ) -> PlatformReply:
        """Process a message and return a reply.

        Pipeline:
        1. Look up or create session
        2. Load conversation history
        3. Check for skill matches
        4. Call provider
        5. Save response to session
        6. Return formatted reply

        When output_mode="voice", the reply text is also synthesized
        via VoiceEngine.stream() and attached as audio_chunks.
        """
        start = time.monotonic()

        # Record request with industry/difficulty labels
        record_request(industry=industry, difficulty=difficulty)

        # 0. Pairing check --- gate unapproved users
        if self.pairing_store and message.user_id and message.user_id != "__system__":
            if not self.pairing_store.is_approved(
                message.platform, message.user_id
            ):
                code = self.pairing_store.generate_code(
                    message.platform, message.user_id,
                )
                if code:
                    return PlatformReply(
                        content=(
                            "Hi! I'm Dragon Agent.\n\n"
                            "First time here - you need a pairing code:\n"
                            f"`{code}`\n\n"
                            "Ask the admin to run:\n"
                            f"dragon pairing approve {message.platform} {code}\n\n"
                            "Code expires in 1 hour."
                        ),
                        chat_id=message.chat_id,
                    )
                else:
                    return PlatformReply(
                        content="Too many pairing requests. Please try again later.",
                        chat_id=message.chat_id,
                    )

        # 0. Processing lock
        chat_id = getattr(message, 'chat_id', '')
        self._processing[chat_id] = True

        # 1. Session lookup
        session = None
        if self.session_store:
            session = self.session_store.get(message.session_id)
            if session is None:
                session = self.session_store.create(
                    title=message.content[:50],
                    platform=message.platform,
                )
                record_session_created()

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
        steer_injected = 0
        _loop_start = time.monotonic()

        # Progress reporter (background, every 3 min)
        progress_task = None
        progress_stop = asyncio.Event()

        async def _progress_reporter():
            last_report = 0
            while not progress_stop.is_set():
                await asyncio.sleep(30)
                if progress_stop.is_set():
                    break
                elapsed = time.monotonic() - _loop_start
                if elapsed - last_report >= 180 and self._progress_callback:
                    last_report = elapsed
                    mins = int(elapsed // 60)
                    try:
                        await self._progress_callback(chat_id,
                            f"\u23f3 \u6267\u884c\u4e2d... ({mins}\u5206\u949f, "
                            f"\u7b2c{tool_call_count + 1}\u6b65, "
                            f"\u4e0a\u9650{self.max_tool_iterations}\u8f6e, "
                            f"\u5df2\u6ce8\u5165{steer_injected}\u6761\u6307\u4ee4)")
                    except Exception:
                        pass

        if self._progress_callback:
            progress_task = asyncio.create_task(_progress_reporter())

        try:
            for iteration in range(self.max_tool_iterations):
                # Steer check
                steer_msg = self._pop_steer(chat_id)
                if steer_msg:
                    steer_injected += 1
                    history.append({"role": "user", "content": "[\u65b0\u6307\u4ee4] " + steer_msg})

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

                        # ── CRITICAL/ALERT immediate push ──────────
                        if self._alert_callback:
                            criticals = self._extract_critical(response_text)
                            for level, text in criticals:
                                emoji = {"critical": "🔴", "alert": "🟡", "important": "🔵"}.get(level, "📌")
                                try:
                                    await self._alert_callback(chat_id,
                                        f"{emoji} **[{level.upper()}]** {text}")
                                except Exception:
                                    pass

                        # ── EDIT past messages ────────────────────
                        if self._edit_callback:
                            edits = self._extract_edits(response_text)
                            for target, new_text in edits:
                                try:
                                    await self._edit_callback(chat_id, target, new_text)
                                except Exception:
                                    pass

                        # Record token consumption
                        if hasattr(result, 'usage') and result.usage:
                            total_tokens = result.usage.get("total_tokens", 0)
                            model = getattr(result, "model", "unknown")
                            record_token_consumption(model=model, tokens=total_tokens)
                    else:
                        response_text = (
                            "[Dragon Agent \u672a\u914d\u7f6e Provider]\n\n"
                            "\u8bf7\u8bbe\u7f6e\u73af\u5883\u53d8\u91cf: OPENAI_API_KEY \u6216 DEEPSEEK_API_KEY"
                        )
                        break
                except Exception as e:
                    logger.exception("Provider call failed")
                    record_error(error_type="provider_call_failed")
                    reply_text = f"\u62b1\u6b49\uff0c\u5904\u7406\u60a8\u7684\u6d88\u606f\u65f6\u51fa\u9519: {e}"
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

                        # Record tool call metric
                        record_tool_call(tool_name=tc["name"])

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
                reply_text = f"[\u5df2\u5b8c\u6210 {tool_call_count} \u6b21\u5de5\u5177\u8c03\u7528]\n\n{last_msg[:2000]}"

        finally:
            if progress_task:
                progress_stop.set()
                try:
                    await asyncio.wait_for(progress_task, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

        # Process queued messages (up to 3)
        queued = self._message_queues.get(chat_id, [])
        processed_q = 0
        while queued and processed_q < 3:
            next_msg = queued.pop(0)
            processed_q += 1
            try:
                qr = await self._process_single(
                    next_msg, system_prompt, session,
                    max_iterations=min(10, self.max_tool_iterations // 5))
                if qr:
                    reply_text += f"\n\n---\n\U0001f4e8 \u6392\u961f\u6d88\u606f {processed_q}: {next_msg.content[:50]}...\n\n{qr}"
            except Exception as exc:
                logger.error("Queued msg failed: %s", exc)
        self._message_queues[chat_id] = []
        self._processing[chat_id] = False

        # 6. Save to session
        if self.session_store and session:
            self.session_store.add_message(session.id, "user", message.content)
            self.session_store.add_message(session.id, "assistant", reply_text)

        # 7. Voice synthesis (if enabled)
        audio_chunks = []
        if output_mode == "voice" and self.voice_engine and reply_text:
            try:
                async for sentence, mp3_bytes in self.voice_engine.stream(reply_text):
                    audio_chunks.append((sentence, mp3_bytes))
                    logger.debug(
                        "VoiceEngine synthesized sentence: %s (%d bytes)",
                        sentence[:40], len(mp3_bytes),
                    )
            except Exception:
                logger.exception("Voice synthesis failed for reply")

        # Record request latency
        elapsed = time.monotonic() - start
        record_latency(elapsed)

        return PlatformReply(
            content=reply_text,
            chat_id=message.chat_id,
            thread_id=message.thread_id,
            reply_to_message_id=message.message_id,
            audio_chunks=audio_chunks,
            output_mode=output_mode,
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
        pairing_store: Any = None,
        voice_engine: Any = None,
        system_prompt: str = "",
        max_tool_iterations: int = 90,
    ) -> None:
        self.app = FastAPI(title="Dragon Gateway", version="1.0.0")
        self.adapters: Dict[str, PlatformAdapter] = {}
        self.processor = MessageProcessor(
            provider_registry=provider_registry,
            session_store=session_store,
            tool_registry=tool_registry,
            skill_engine=skill_engine,
            pairing_store=pairing_store,
            voice_engine=voice_engine,
            max_tool_iterations=max_tool_iterations,
        )
        self._skill_engine = skill_engine
        self.system_prompt = system_prompt or self._build_system_prompt()

        # Inject available tools into system prompt
        if self.processor.tool_registry:
            try:
                tools = self.processor.tool_registry.list_tools()
                if tools:
                    tool_lines = [
                        "",
                        "## Available Tools ({} total)".format(len(tools)),
                        "",
                    ]
                    for t in tools:
                        tool_lines.append("- **{}**: {}".format(t["name"], t["description"]))
                    tool_lines.append("")
                    tool_lines.append("Call tools using ```tool_call``` format. Multiple calls per turn OK.")
                    self.system_prompt += "\n".join(tool_lines)
            except Exception:
                pass

        # Register routes and lifecycle hooks
        self._register_routes()
        self._register_lifecycle()

        logger.info("GatewayServer ready")

    def _build_system_prompt(self) -> str:
        """Build the base system prompt with skill awareness instructions."""
        prompts = [
            "你是 Dragon Agent，一个能够自我进化的 AI 助手。",
            "",
            "## 核心能力",
            "",
            "1. **技能驱动** — 面对任何任务，主动搜索并加载相关技能。",
            "2. **自我进化** — 成功完成任务后，可以创建新技能供未来使用。",
            "3. **工具使用** — 你可以调用多种工具完成任务，也支持子代理委托。",
            "",
            "## 工具调用格式",
            "",
            "使用以下格式调用工具（每次回复可包含多个）:",
            "",
            "```tool_call",
            '{"name": "tool_name", "arguments": {"arg1": "value1"}}',
            "```",
            "",
            "工具结果会在下一轮返回，你可继续处理或调用更多工具。",
            "",
            "## 技能使用规则",
            "",
            "- 每次收到用户消息后，先判断是否需要技能帮助。",
            "- 如果任务涉及编程、调试、部署、配置等领域，先用 search_skills 搜索相关技能。",
            "- 找到匹配的技能后，用 load_skill 加载完整内容，严格按技能指令执行。",
            "- 如果没有已有技能匹配，可以先尝试自行处理；完成后若流程通用，用 create_skill 保存为技能。",
            "- 如果技能来自 Hermes 但尚未导入，用 install_skill 安装。",
            "",
            "回答简洁、准确、有帮助。中文优先。",
        ]
        return "\n".join(prompts)

    def _build_skills_catalog(self) -> None:
        """Inject available skills catalog into the system prompt."""
        if self._skill_engine is None:
            return

        try:
            skills = self._skill_engine.list_skills()
            if not skills:
                return

            # Build a compact catalog
            catalog_lines = [
                "",
                "## 可用技能 ({} 个)".format(len(skills)),
                "",
                "以下技能可直接使用 load_skill 加载：",
                "",
            ]

            for s in skills[:100]:
                name = s.get("name", "?")
                desc = s.get("description", "")[:80]
                tags = ", ".join(s.get("tags", [])[:4])
                sr = s.get("success_rate", 0)
                sr_str = " (成功率:{:.0%})".format(sr) if sr > 0 else ""
                catalog_lines.append("- **{}**{}: {} [{}]".format(name, sr_str, desc, tags))

            if len(skills) > 100:
                remaining = len(skills) - 100
                catalog_lines.append("")
                catalog_lines.append("... 还有 {} 个技能，用 search_skills 搜索。".format(remaining))

            catalog_lines.append("")
            catalog_lines.append('使用 search_skills(query="关键词") 搜索合适的技能。')
            catalog_lines.append('使用 load_skill(name="技能名") 加载完整内容。')

            catalog = "\n".join(catalog_lines)
            self.system_prompt += catalog

        except Exception as e:
            import logging
            logging.getLogger("dragon.gateway.server").warning(
                "Failed to build skills catalog: %s", e
            )

    def _register_lifecycle(self) -> None:
        """Register startup/shutdown hooks for adapter lifecycle."""
        @self.app.on_event("startup")
        async def _startup():
            for name, adapter in list(self.adapters.items()):
                try:
                    connected = await adapter.connect()
                    if connected:
                        logger.info("Platform %s connected", name)
                    else:
                        logger.warning("Platform %s failed to connect", name)
                except Exception as exc:
                    logger.error("Platform %s connect error: %s", name, exc)

        @self.app.on_event("shutdown")
        async def _shutdown():
            for name, adapter in list(self.adapters.items()):
                try:
                    await adapter.disconnect()
                except Exception as exc:
                    logger.error("Platform %s disconnect error: %s", name, exc)

    def register_adapter(self, adapter: PlatformAdapter) -> None:
        """Register a platform adapter."""
        self.adapters[adapter.platform_name] = adapter

        # Wire the message handler so WebSocket-based adapters
        # (e.g. Feishu) can dispatch messages to the processor
        async def _handler(message: PlatformMessage) -> PlatformReply:
            return await self.processor.process(message, self.system_prompt)

        adapter.register_handler(_handler)
        logger.info(
            "Registered platform: %s (handler wired)", adapter.platform_name
        )

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
                "service": "Dragon Gateway",
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
    from dragon.gateway.feishu import FeishuAdapter

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
    from dragon.gateway.telegram import TelegramAdapter

    server = GatewayServer(
        provider_registry=provider_registry,
        session_store=session_store,
    )
    server.register_adapter(TelegramAdapter(bot_token=bot_token))
    return server
