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
from dragon.workflow_store import WorkflowStore
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
        self.workflow_store: Optional[WorkflowStore] = None  # Task state tracking

        if compression_config:
            from dragon.compression import ContextCompressor
            self.compressor = ContextCompressor(
                config=compression_config,
            )
        else:
            self.compressor = None

    def _get_file_context(self, chat_id: str) -> str:
        """Read recently downloaded files for this chat, injected into every message."""
        import json as _json, os as _os
        tracker = _os.path.join("dragon_data", "uploads", ".chat_files.json")
        if not _os.path.exists(tracker):
            return ""
        try:
            with open(tracker, "r") as f:
                data = _json.load(f)
            files = data.get(chat_id, [])
            if files:
                return "\n📁 本对话中下载的文件：\n" + "\n".join(f"- {f}" for f in files) + "\n"
        except Exception:
            pass
        return ""

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
                    # Non-native FC: use "user" role for API compat
                    history.append({"role": "user", "content": "[Tool: " + tc["name"] + "]\n" + out[:1000]})
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
        with open("/tmp/feishu_dispatch.log", "a") as _f:
            _f.write(f"[{time.monotonic()}] PROCESS: ENTER user={getattr(message, 'user_id', '?')} chat={getattr(message, 'chat_id', '?')}\n")

        # Record request with industry/difficulty labels
        record_request(industry=industry, difficulty=difficulty)

        # 0. Pairing check --- gate unapproved users
        with open("/tmp/feishu_dispatch.log", "a") as _f:
            _f.write(f"[{time.monotonic()}] PROCESS: pairing_check pairing_store={'SET' if self.pairing_store else 'NONE'} user_id={message.user_id}\n")
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

        # 0.5 Auto-create workflow run for state tracking
        wf_id = ""
        if self.workflow_store:
            wf_name = message.content[:40].replace("\n", " ")
            wf_id = self.workflow_store.start_workflow(wf_name, {
                "chat_id": chat_id,
                "platform": getattr(message, 'platform', ''),
                "user_id": getattr(message, 'user_id', ''),
            })
            logger.debug("Workflow started: %s", wf_id)

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
        # Inject recently downloaded files as context
        file_context = self._get_file_context(chat_id)
        if file_context:
            message.content = file_context + "\n\n" + message.content

        history = []
        if system_prompt:
            history.append({"role": "system", "content": system_prompt})

        if self.session_store and session:
            past_msgs = self.session_store.get_messages(
                session.id, limit=50
            )
            history.extend([
                {"role": m.role, "content": m.content}
                for m in past_msgs[-50:]  # last 50 messages
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
        with open("/tmp/feishu_dispatch.log", "a") as _f:
            _f.write(f"[{time.monotonic()}] PROCESS: agent_loop_start history_len={len(history)}\n")
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
            print(f"[PROC_DEBUG] entering for loop, max_iter={self.max_tool_iterations}", flush=True)
            for iteration in range(self.max_tool_iterations):
                print(f"[PROC_DEBUG] iter={iteration}", flush=True)
                # Steer check
                steer_msg = self._pop_steer(chat_id)
                if steer_msg:
                    steer_injected += 1
                    history.append({"role": "user", "content": "[\u65b0\u6307\u4ee4] " + steer_msg})

                try:
                    print(f"[PROC_DEBUG] iter={iteration} provider_registry={'SET' if self.provider_registry else 'NONE'}", flush=True)
                    if self.provider_registry:
                        # Build tool schemas for the provider
                        tool_schemas = None
                        if self.tool_registry:
                            tool_schemas = self.tool_registry.get_openai_schemas()
                            # No tool limit — send all available tools

                        # DEBUG: log messages on error
                        try:
                            print(f"[PROC_DEBUG] calling provider with history_len={len(history)}", flush=True)
                            result = await self.provider_registry.call(
                                provider_name="openai",
                                messages=history,
                                max_tokens=2048,
                                tools=tool_schemas if tool_schemas else None,  # ENABLED: native FC
                            )
                        except Exception as call_err:
                            import json as _json
                            err_msg = str(call_err)
                            logger.error(f"Provider call error: {err_msg}")
                            # Dump last 3 messages for debugging
                            for i, m in enumerate(history[-3:]):
                                logger.error(f"  msg[{i}]: role={m.get('role')}, content_len={len(str(m.get('content','')))} type={type(m.get('content')).__name__}")
                            raise
                        response_text = result.content
                        
                        # DEBUG: log tool calls
                        if hasattr(result, 'tool_calls') and result.tool_calls:
                            tc_names = [tc.get('function',{}).get('name','?') for tc in result.tool_calls]
                            print(f"[PROC_DEBUG] iter={iteration} tool_calls={tc_names}", flush=True)
                            if self._progress_callback:
                                try:
                                    tool_str = ', '.join(tc_names[:3])
                                    await self._progress_callback(chat_id,
                                        f"iter {iteration+1}/{self.max_tool_iterations}: {tool_str}")
                                except Exception:
                                    pass
                        else:
                            print(f"[PROC_DEBUG] iter={iteration} text_response_len={len(response_text) if response_text else 0}", flush=True)

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

                # ── Parse tool calls (native FC → text fallback) ──
                tool_calls = []
                is_native_fc = False

                if hasattr(result, 'tool_calls') and result.tool_calls:
                    # Native function calling — parse from ProviderResult
                    is_native_fc = True
                    native_tcs = result.tool_calls[:5]  # Max 5 tool calls per turn
                    for tc in native_tcs:
                        func = tc.get("function", {})
                        args_raw = func.get("arguments", "{}")
                        try:
                            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append({
                            "name": func.get("name", ""),
                            "arguments": args,
                            "id": tc.get("id", ""),
                        })
                else:
                    # Legacy: text-based ```tool_call / <tool_call> parsing
                    tool_calls = self._parse_tool_calls(response_text)

                if not tool_calls:
                    # No tool calls — this is the final answer
                    reply_text = response_text
                    break

                # Execute tool calls
                if self.tool_registry:
                    if is_native_fc:
                        history.append({
                            "role": "assistant",
                            "content": response_text or None,
                            "tool_calls": result.tool_calls,
                        })
                    else:
                        # Clean tool call XML
                        clean_response = re.sub(
                            r'<(function_calls|tool_call)>(.*?)</\1>',
                            '', response_text, flags=re.DOTALL
                        ).strip()
                        if not clean_response:
                            clean_response = f"Calling tools: {', '.join(tc['name'] for tc in tool_calls)}"
                        history.append({"role": "assistant", "content": clean_response})

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

                        # Auto-detect tool errors and flag for LLM attention
                        output_lower = output.lower()
                        if '"error"' in output_lower or output_lower.startswith('{"error"'):
                            output = "⚠️ TOOL ERROR - DO NOT claim success: " + output
                        elif 'already exists' in output_lower:
                            output = "⚠️ ALREADY EXISTS - Tell user, do NOT recreate: " + output
                        elif 'not initialized' in output_lower:
                            output = "⚠️ BACKEND NOT READY - Tell user: " + output

                        # Record tool call metric
                        record_tool_call(tool_name=tc["name"])

                        # Log to workflow store
                        if self.workflow_store and wf_id:
                            try:
                                self.workflow_store.log_step(
                                    task_node_id=wf_id,
                                    step_name=f"tool_{tc['name']}",
                                    action="execute",
                                    output=output[:500],
                                )
                            except Exception:
                                pass

                        tool_outputs.append({
                            "tool": tc["name"],
                            "output": output[:500],
                        })
                        if is_native_fc and tc.get("id"):
                            history.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": output[:500],
                            })
                        else:
                            # Non-native mode: use "user" role for API compat
                            history.append({
                                "role": "user",
                                "content": f"[Tool result: {tc['name']}]\n{output[:500]}",
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

        # Update workflow store
        if self.workflow_store and wf_id:
            try:
                status = "done" if reply_text and "error" not in reply_text.lower()[:50] else "failed"
                self.workflow_store.update_workflow(
                    wf_id,
                    status=status,
                    summary=f"工具调用{tool_call_count}次, 回复{len(reply_text)}字",
                )
            except Exception:
                pass

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

        # Format 3: <function_calls> (DeepSeek native format)
        for match in re.finditer(
            r'<function_calls>(.*?)</function_calls>', content, re.DOTALL
        ):
            block = match.group(1)
            for invoke in re.finditer(
                r'<invoke name="([^"]+)">(.*?)</invoke>', block, re.DOTALL
            ):
                name = invoke.group(1)
                args_block = invoke.group(2)
                args = {}
                for param in re.finditer(
                    r'<parameter name="([^"]+)">(.*?)</parameter>', args_block, re.DOTALL
                ):
                    pname = param.group(1)
                    pval = param.group(2).strip()
                    # Try to parse as JSON, otherwise use as string
                    try:
                        pval = json.loads(pval)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    args[pname] = pval
                calls.append({"name": name, "arguments": args})

        # Format 4: Self-closing tags <tool_name key="val" />
        # Example: <search_skills query="lottery analysis" />
        for match in re.finditer(
            r'<(\w+)\s+([^>]*?)\s*/>', content
        ):
            name = match.group(1)
            # Skip known non-tool tags
            if name.lower() in ('br', 'hr', 'img', 'input', 'meta', 'link'):
                continue
            attrs_str = match.group(2)
            args = {}
            for attr_match in re.finditer(
                r'(\w+)="([^"]*)"', attrs_str
            ):
                key = attr_match.group(1)
                val = attr_match.group(2)
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    pass
                args[key] = val
            calls.append({"name": name, "arguments": args})

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

        # Initialize workflow store for state persistence
        try:
            self.workflow_store = WorkflowStore()
            self.processor.workflow_store = self.workflow_store
        except Exception:
            self.workflow_store = None

        # Tools are now injected via native function calling (OpenAI tools param).
        # No need for text-based tool list in system prompt — reduces token usage.
        pass

        # Register routes and lifecycle hooks
        self._register_routes()
        self._register_lifecycle()

        logger.info("GatewayServer ready")


    def _build_system_prompt(self) -> str:
        """Build system prompt aligned with Hermes reasoning standards."""        # ── Auto-inject available skills catalog ──────────────────────────
        skills_catalog = ""
        try:
            import json as _json
            import os as _os
            skills_dir = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
                "dragon_data", "skills"
            )
            if _os.path.isdir(skills_dir):
                skill_entries = []
                for fname in sorted(_os.listdir(skills_dir)):
                    if not fname.endswith(".json"):
                        continue
                    fpath = _os.path.join(skills_dir, fname)
                    try:
                        with open(fpath, "r") as _f:
                            data = _json.load(_f)
                        meta = data.get("meta", {})
                        name = meta.get("name", fname.replace(".json", ""))
                        desc = meta.get("description", "")
                        if name and desc:
                            skill_entries.append((name, desc))
                    except Exception:
                        pass

                if skill_entries:
                    lines = [
                        "",
                        "## Skills (mandatory)",
                        "",
                        "Before replying, scan the skills below. If a skill matches or "
                        "is even partially relevant to your task, you MUST load it "
                        "with skill_view(name) and follow its instructions.",
                        "",
                        "Err on the side of loading — skills contain specialized "
                        "knowledge that dramatically improves results.",
                        "",
                        "If a loaded skill has issues, fix it with "
                        "skill_manage(action='patch', name=..., content=...).",
                        "",
                        "After completing difficult tasks (>=5 tool calls), "
                        "offer to save the workflow as a new skill: "
                        "skill_manage(action='create', name=..., content=..., description=...).",
                        "",
                        "<available_skills>",
                    ]
                    for name, desc in skill_entries:
                        lines.append(f"  {name}: {desc}")
                    lines.append("</available_skills>")
                    skills_catalog = "\n".join(lines)
        except Exception:
            pass

        # Build memory sections
        memory_text = ""
        user_text = ""
        try:
            from dragon.tool.builtins.memory import load_memory_for_prompt
            mem = load_memory_for_prompt()
            if mem.get("memory"):
                memory_text = mem["memory"]
            if mem.get("user"):
                user_text = mem["user"]
        except Exception:
            pass

        prompts = [
            "你是 Dragon Agent，一个能够自我进化的 AI 助手。工具 API 已与 Hermes Agent 对齐。",
            "",
            "## 核心原则",
            "",
            "- **主动推理，不要等待指令。** 收到消息后立即判断步骤并开始执行。",
            "- **每次响应要么调用工具推进进度，要么给出最终结果。** 不输出空话。",
            "- **不确定时用 clarify 询问用户，不要猜测。**",
            "- **错误时自动重试，最多3次，全部失败后如实报告。**",
            "- ⚠️ **铁律：工具返回⚠️前缀=操作失败，必须如实告知用户，绝不声称成功。**",
            "",
            "## 持久记忆 (Memory)",
            "",
            "你有跨会话的持久记忆。用 memory 工具保存：用户偏好、环境细节、工具技巧、稳定惯例。",
            "优先记录能减少用户纠正的内容——最有价值的记忆是让用户不必再次纠正你。",
            "不要保存任务进度、会话结果、已完成的工作日志到 memory。",
            "将记忆写为陈述性事实，不要写为给自己的指令。",
        ]
        if user_text:
            prompts.extend([
                "",
                "USER PROFILE",
                user_text,
            ])
        if memory_text:
            prompts.extend([
                "",
                "MEMORY (your personal notes)",
                memory_text,
            ])
        prompts.extend([
            "",
            "## 技能系统 (Hermes 对齐)",
            "",
            "技能存储在 ~/.dragon/skills/，格式为 SKILL.md (YAML 前导 + Markdown)。",
            "",
            "工具：",
            "- skill_view(name) — 加载技能完整内容",
            "- skill_manage(action=create, name, content, description, tags) — 创建技能",
            "- skill_manage(action=patch, name, content) — 更新技能",
            "- skill_manage(action=delete, name) — 删除技能",
            "- search_skills(query) — 搜索已有技能",
            "",
            "何时创建技能：",
            "- 完成复杂任务(>=5次工具调用) → skill_manage(action=create)",
            "- 解决新问题/发现坑 → 记录为技能",
            "- 用户纠正做法 → 保存正确版本",
            "- 发现旧技能有误 → skill_manage(action=patch)",
            "- 不要等用户说保存——主动判断并执行",
            "",
            "平台：飞书(Lark)工作区，Markdown 格式，简洁中文优先。",
        ])
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
            import datetime as _dh
            with open("/tmp/feishu_dispatch.log", "a") as _f:
                _f.write(f"[{_dh.datetime.now()}] SERVER_HANDLER: ENTER user={getattr(message, 'user_id', '?')} chat={getattr(message, 'chat_id', '?')}\n")
            try:
                result = await self.processor.process(message, self.system_prompt)
                with open("/tmp/feishu_dispatch.log", "a") as _f:
                    _f.write(f"[{_dh.datetime.now()}] SERVER_HANDLER: DONE reply_len={len(result.content) if result and result.content else 0}\n")
                return result
            except Exception as e:
                with open("/tmp/feishu_dispatch.log", "a") as _f:
                    _f.write(f"[{_dh.datetime.now()}] SERVER_HANDLER: ERROR {e}\n")
                raise

        adapter.register_handler(_handler)

        # Wire shared VoiceEngine to adapter for voice mode
        if hasattr(adapter, 'set_voice_engine') and self.processor.voice_engine:
            adapter.set_voice_engine(self.processor.voice_engine)

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
