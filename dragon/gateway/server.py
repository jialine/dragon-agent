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
import os
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse

from dragon.gateway.base import PlatformAdapter, PlatformMessage, PlatformReply
from dragon.workflow_store import WorkflowStore
from dragon.workflow.dispatcher import WorkflowDispatcher
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
        self._next_session_ids: Dict[str, str] = {}  # chat_id → forced next session_id

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
                        provider_name="openai", messages=history, max_tokens=8192)
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

        # 0.5 Intent-driven workflow dispatch
        wf_id = ""
        wf_context_text = ""
        if self.workflow_dispatcher and self.workflow_store:
            try:
                wf_def, wf_ctx, wf_source = await self.workflow_dispatcher.dispatch(
                    message.content
                )
                wf_name = message.content[:40].replace("\n", " ")
                if wf_def and wf_source != "fallback":
                    # Workflow matched or auto-created — inject context
                    wf_name = wf_def.name
                    wf_context_text = (
                        f"[工作流: {wf_def.name}]\n"
                        f"[来源: {wf_source}]\n"
                        f"[步骤: {len(wf_def.steps)}步]\n"
                    )
                    logger.info(
                        "Workflow dispatched: %s (source=%s, steps=%d)",
                        wf_def.name, wf_source, len(wf_def.steps),
                    )
                # Always start tracking
                wf_id = self.workflow_store.start_workflow(wf_name, {
                    "chat_id": chat_id,
                    "platform": getattr(message, 'platform', ''),
                    "user_id": getattr(message, 'user_id', ''),
                    "workflow_source": wf_source,
                    "workflow_context": wf_ctx,
                })
                logger.debug("Workflow tracked: %s", wf_id)
            except Exception as _wfe:
                logger.warning("Workflow dispatch failed: %s", _wfe)
                # Fallback: still track without workflow matching
                if self.workflow_store:
                    wf_name = message.content[:40].replace("\n", " ")
                    wf_id = self.workflow_store.start_workflow(wf_name, {
                        "chat_id": chat_id,
                        "platform": getattr(message, 'platform', ''),
                        "user_id": getattr(message, 'user_id', ''),
                    })
        elif self.workflow_store:
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
            # Check for forced new session from /new /reset command
            forced_sid = self._next_session_ids.get(chat_id) if chat_id else None
            lookup_id = forced_sid or message.session_id
            session = self.session_store.get(lookup_id)
            if session is None:
                session = self.session_store.create(
                    title=message.content[:50],
                    platform=message.platform,
                )
                record_session_created()
            # Persist session_id → chat_id mapping for all future messages
            self._next_session_ids[chat_id] = session.id

        # 2. Build message history
        # Inject recently downloaded files as context
        file_context = self._get_file_context(chat_id)
        if file_context:
            message.content = file_context + "\n\n" + message.content

        # ── Refresh memory every turn (was only loaded once at init) ──
        try:
            from dragon.tool.builtins.memory import load_memory_for_prompt
            mem = load_memory_for_prompt()
            mem_part = mem.get("memory", "") if mem else ""
            user_part = mem.get("user", "") if mem else ""
            fresh_sp = self._rebuild_system_prompt_with_memory(mem_part, user_part)
            if fresh_sp:
                system_prompt = fresh_sp
        except Exception:
            pass

        history = []
        if system_prompt:
            history.append({"role": "system", "content": system_prompt})

        if self.session_store and session:
            past_msgs = self.session_store.get_messages(
                session.id, limit=200
            )
            # Hermes-style: keep last 20 msgs, no tool truncation
            # (context window manages overflow; truncated tool results cause bad reasoning)
            MAX_HIST = 20
            total = len(past_msgs)
            if total > MAX_HIST:
                dropped = total - MAX_HIST
                past_msgs = past_msgs[-MAX_HIST:]
                if system_prompt:
                    system_prompt = system_prompt.rstrip() + (
                        "\n\n[已截断: 省略 " + str(dropped) + " 条更早消息, 保留最近 " + str(MAX_HIST) + " 条]\n"
                    )
            for m in past_msgs:
                c = m.content or ""
                r = m.role
                msg = {"role": r, "content": c}
                # Extract tool_call_id from content prefix [call_XX_...] for tool messages
                if r == "tool":
                    tc_match = re.match(r'^\[(call_\w+)\]\s*', c)
                    if tc_match:
                        msg["tool_call_id"] = tc_match.group(1)
                # Restore tool_calls for assistant messages
                if r == "assistant" and m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
                history.append(msg)

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
        _current_tool_name = "thinking..."  # for progress display
        _loop_start = time.monotonic()

        # Progress reporter (background, every 3 min)
        progress_task = None
        progress_stop = asyncio.Event()

        async def _progress_reporter():
            last_report = 0
            while not progress_stop.is_set():
                await asyncio.sleep(180)
                if progress_stop.is_set():
                    break
                elapsed = time.monotonic() - _loop_start
                if elapsed - last_report >= 180 and self._progress_callback:
                    last_report = elapsed
                    mins = int(elapsed // 60)
                    try:
                        await self._progress_callback(chat_id,
                            f"\u23f3 {mins}min elapsed \u2014 iter {tool_call_count + 1}/{self.max_tool_iterations}, "
                            f"running: {_current_tool_name}")
                    except Exception as e:
                        logger.warning("[Processor] 3min progress error: %s", e)

        if self._progress_callback:
            progress_task = asyncio.create_task(_progress_reporter())

        try:
            print(f"[PROC_DEBUG] entering for loop, max_iter={self.max_tool_iterations}", flush=True)
            thinking_only_count = 0  # guard against reasoning-model infinite loop
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
                            all_schemas = self.tool_registry.get_openai_schemas()
                            # Limit to 25 tools to prevent payload overflow (400 errors)
                            # Prioritize: file, terminal, web, memory, skills, core
                            _priority_cats = {"file", "terminal", "web", "memory", "skills", "interaction", "delegation", "automation", "development"}
                            _priority = []
                            _others = []
                            for s in all_schemas:
                                name = s.get("function", {}).get("name", "")
                                cat = ""
                                for t in self.tool_registry.list_tools():
                                    if t.get("name") == name:
                                        cat = t.get("category", "")
                                        break
                                if cat in _priority_cats or name in ("read_file", "write_file", "search_files", "terminal", "web_search", "memory", "session_search", "skill_view", "skill_manage", "clarify", "todo", "cronjob", "delegate_task", "execute_code", "patch", "vision_analyze", "send_message"):
                                    _priority.append(s)
                                else:
                                    _others.append(s)
                            tool_schemas = (_priority + _others)[:25]

                        # ── Trim history to prevent unbounded growth from accumulated tool calls ──
                        MAX_PROVIDER_HIST = 50
                        # Hermes-aligned orphan cleanup: drop invalid tool messages.
                        # Runs twice — before AND after trim — because trim can
                        # chop off an assistant message and leave its tool results
                        # orphaned.
                        def _hermes_cleanup(msgs):
                            known_tool_ids = set()
                            out = []
                            for m in msgs:
                                if not isinstance(m, dict):
                                    out.append(m)
                                    continue
                                role = m.get("role")
                                if role == "assistant":
                                    known_tool_ids = set()
                                    for tc in (m.get("tool_calls") or []):
                                        if isinstance(tc, dict) and tc.get("id"):
                                            known_tool_ids.add(tc["id"])
                                    out.append(m)
                                elif role == "tool":
                                    tc_id = m.get("tool_call_id")
                                    if tc_id and tc_id in known_tool_ids:
                                        out.append(m)
                                else:
                                    if role == "user":
                                        known_tool_ids = set()
                                    out.append(m)
                            return out
                        history = _hermes_cleanup(history)
                        if len(history) > MAX_PROVIDER_HIST:
                            trimmed = [history[0]] if history[0].get("role") == "system" else []
                            trimmed += history[-(MAX_PROVIDER_HIST - len(trimmed)):]
                            print(f"[PROC_DEBUG] trimmed history: {len(history)} → {len(trimmed)}", flush=True)
                            history = trimmed
                            # Re-run cleanup after trim — trim may have orphaned tool messages
                            history = _hermes_cleanup(history)

                        # ── Retry loop: up to 3 attempts with exponential backoff ──

                        max_retries = 3
                        last_err = None
                        result = None
                        for attempt in range(max_retries):
                            try:
                                print(f"[PROC_DEBUG] calling provider attempt {attempt+1}/{max_retries} history_len={len(history)}", flush=True)
                                result = await self.provider_registry.call(
                                    provider_name="openai",
                                    messages=history,
                                    max_tokens=8192, temperature=0.7,
                                    tools=tool_schemas if tool_schemas else None,  # ENABLED: native FC
                                )
                                break  # Success
                            except Exception as call_err:
                                last_err = call_err
                                err_msg = str(call_err)
                                logger.warning(f"Provider call attempt {attempt+1}/{max_retries} failed: {err_msg[:200]}")
                                if attempt < max_retries - 1:
                                    await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
                                    continue
                                # Last attempt failed — dump debug info
                                logger.error(f"Provider call FAILED after {max_retries} attempts: {err_msg}")
                                for i, m in enumerate(history[-3:]):
                                    logger.error(f"  msg[{i}]: role={m.get('role')}, content_len={len(str(m.get('content','')))} type={type(m.get('content')).__name__}")
                        if result is None:
                            raise last_err
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
                                except Exception as e:
                                    logger.warning("[Processor] iter progress error: %s", e)
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
                                except Exception as e:
                                    logger.warning("[Processor] alert_cb error: %s", e)

                        # ── EDIT past messages ────────────────────
                        if self._edit_callback:
                            edits = self._extract_edits(response_text)
                            for target, new_text in edits:
                                try:
                                    await self._edit_callback(chat_id, target, new_text)
                                except Exception as e:
                                    logger.warning("[Processor] edit_cb error: %s", e)

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
                    logger.error(f"Agent loop iter failed: {str(e)[:200]}")
                    error_text = f"[SYSTEM ERROR: {str(e)[:300]}] Please continue working."
                    history.append({"role": "user", "content": error_text})
                    continue

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
                            "id": tc.get("id") or f"call_{func.get('name', 'unknown')}",
                        })
                else:
                    # Legacy: text-based ```tool_call / <tool_call> parsing
                    tool_calls = self._parse_tool_calls(response_text)

                if not tool_calls:
                    # No tool calls — but check if this is thinking-only
                    msg = result.raw["choices"][0]["message"] if hasattr(result, "raw") else {}
                    has_reasoning = bool(msg.get("reasoning_content", ""))
                    has_content = bool(response_text and response_text.strip())
                    if has_reasoning and not has_content:
                        thinking_only_count += 1
                        if thinking_only_count < 3:
                            # Thinking-only response — give model another turn to produce output
                            logger.debug("Thinking-only response detected, continuing loop (%d/3)", thinking_only_count)
                            continue
                        else:
                            # Force output after 3 consecutive thinking-only responses
                            logger.warning("Thinking-only loop detected after 3 turns, forcing output")
                            history.append({"role": "user", "content": "Stop thinking and give your final answer NOW. Output only the answer, no reasoning."})
                            continue
                    reply_text = response_text
                    break

                # Execute tool calls
                if self.tool_registry:
                    if is_native_fc:
                        history.append({
                            "role": "assistant",
                            "content": response_text or "",
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
                    _current_tool_name = ", ".join(tc["name"] for tc in tool_calls[:2])
                    for tc in tool_calls:
                        tool_call_count += 1
                        try:
                            import os as _os, datetime as _dt
                            _tool_name = tc["name"]
                            _tool_args = tc.get("arguments", {})
                            with open("/tmp/dragon_tool_debug.log", "a") as _tf:
                                _tf.write(f"[{_dt.datetime.now()}] CALL {_tool_name} args={json.dumps(_tool_args, ensure_ascii=False)[:300]} cwd={_os.getcwd()}\n")
                            tool_result = await self.tool_registry.call(
                                _tool_name, _tool_args
                            )
                            output = str(tool_result.output) if tool_result.success else tool_result.error
                            with open("/tmp/dragon_tool_debug.log", "a") as _tf:
                                _tf.write(f"[{_dt.datetime.now()}] RESULT {_tool_name} success={tool_result.success} output_len={len(output)} output_preview={output[:200]}\n")
                        except Exception as e:
                            output = f"Tool error: {e}"
                            with open("/tmp/dragon_tool_debug.log", "a") as _tf:
                                _tf.write(f"[{_dt.datetime.now()}] ERROR {tc['name']}: {e}\n")

                        # Auto-detect tool errors and flag for LLM attention
                        output_lower = output.lower()
                        error_patterns = [
                            '"error"', '{"error"', 'already exists', 'not initialized',
                            'permission denied', 'connection refused', 'not found',
                            'timeout', 'failed', 'traceback', 'syntaxerror',
                            'keyerror', 'attributeerror', 'filenotfounderror',
                            'access denied', 'unauthorized', 'forbidden',
                            'internal server error', 'bad gateway', 'service unavailable',
                        ]
                        is_error = any(p in output_lower for p in error_patterns)
                        if output_lower.startswith('{"error"') or '"error"' in output_lower:
                            output = "⚠️ TOOL ERROR - DO NOT claim success: " + output
                        elif is_error:
                            output = "⚠️ TOOL ISSUE - Verify before claiming success: " + output

                        # Record tool call metric
                        record_tool_call(tool_name=tc["name"])

                        # Log to workflow store
                        if self.workflow_store and wf_id:
                            try:
                                self.workflow_store.log_step(
                                    task_node_id=wf_id,
                                    step_name=f"tool_{tc['name']}",
                                    action="execute",
                                    output=output[:4000],
                                )
                            except Exception:
                                pass

                        tool_outputs.append({
                            "tool": tc["name"],
                            "output": output[:4000],
                        })
                        history.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id") or f"call_{tc.get('name', 'unknown')}",
                            "content": output[:4000],
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

        # 6. Save to session (including tool results from this turn)
        if self.session_store and session:
            self.session_store.add_message(session.id, "user", message.content)
            self.session_store.add_message(session.id, "assistant", reply_text)
            # Also save tool interaction history for context continuity
            if tool_call_count > 0 and history:
                for msg in history:
                    if msg.get("role") == "tool":
                        tool_name = msg.get("tool_call_id", "unknown")[:30]
                        tool_out = msg.get("content", "")[:2000]
                        self.session_store.add_message(
                            session.id, "tool",
                            f"[{tool_name}] {tool_out}"
                        )

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


    def _rebuild_system_prompt_with_memory(self, memory_text="", user_text=""):
        """Rebuild full system prompt: base + tools + skills + memory every turn."""
        import os, json, yaml
        base_prompt = ""
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yaml")
            if os.path.exists(cfg_path):
                with open(cfg_path) as cf:
                    cfg = yaml.safe_load(cf) or {}
                base_prompt = cfg.get("gateway", {}).get("system_prompt", "")
                if base_prompt:
                    base_prompt = base_prompt.strip()
        except Exception:
            pass

        # Tool catalog (compact)
        tool_lines = ["", "## Available Tools", ""]
        if self.tool_registry:
            tools = self.tool_registry.list_tools()
            cats = {}
            for t in tools:
                cat = t.get("category", "general")
                cats.setdefault(cat, []).append(t.get("name", "?"))
            for cat in sorted(cats):
                names = ", ".join(cats[cat][:15])
                tool_lines.append("[%s] %s" % (cat, names))
            tool_lines.append("")
            tool_lines.append("Core: read_file/write_file/search_files/terminal/web_search/memory/session_search")
            tool_lines.append("Use MEDIA:/path in responses to send images/files.")
        tool_catalog = "\n".join(tool_lines) if len(tool_lines) > 3 else ""

        # Skills catalog
        skills_catalog = ""
        try:
            skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dragon_data", "skills")
            if os.path.isdir(skills_dir):
                skill_entries = []
                for fname in sorted(os.listdir(skills_dir)):
                    if not fname.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(skills_dir, fname)) as _f:
                            data = json.load(_f)
                        meta = data.get("meta", {})
                        name = meta.get("name", "")
                        desc = meta.get("description", "")
                        if name and desc:
                            skill_entries.append((name, desc))
                    except Exception:
                        pass
                if skill_entries:
                    lines = ["", "## Skills", "", "<available_skills>"]
                    for name, desc in skill_entries[:50]:
                        lines.append("  %s: %s" % (name, desc))
                    lines.append("</available_skills>")
                    skills_catalog = "\n".join(lines)
        except Exception:
            pass

        parts = []
        if base_prompt:
            parts.append(base_prompt)
        if tool_catalog:
            parts.append(tool_catalog)
        if skills_catalog:
            parts.append(skills_catalog)
        if user_text:
            parts.extend(["", "USER PROFILE" + chr(10) + user_text])
        if memory_text:
            parts.extend(["", "MEMORY" + chr(10) + memory_text])
        return "\n".join(parts)


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

        # Initialize workflow dispatcher for intent-driven routing
        self.workflow_dispatcher = None
        self.processor.workflow_dispatcher = None
        try:
            if provider_registry:
                async def _dispatch_llm(history):
                    provider_name = provider_registry.available_providers()[0]
                    provider = provider_registry.get(provider_name)
                    response = await provider.chat(
                        messages=history, max_tokens=300, temperature=0.1,
                    )
                    return response.get("content", "")

                import os as _os
                wf_dir = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
                    "workflows"
                )
                self.workflow_dispatcher = WorkflowDispatcher(
                    workflows_dir=wf_dir, provider_fn=_dispatch_llm,
                )
                self.processor.workflow_dispatcher = self.workflow_dispatcher
        except Exception as _exc:
            import logging
            _log = logging.getLogger("dragon.gateway")
            _log.warning("Workflow dispatcher init failed: %s", _exc)

        # Tools are now injected via native function calling (OpenAI tools param).
        # No need for text-based tool list in system prompt — reduces token usage.
        pass

        # Enable context compression (Hermes-aligned)
        if provider_registry:
            try:
                from dragon.compression import CompressionConfig

                async def _compress_llm(history):
                    provider_name = provider_registry.available_providers()[0]
                    provider = provider_registry.get(provider_name)
                    response = await provider.chat(
                        messages=history, max_tokens=800, temperature=0.3,
                    )
                    return response.get("content", "")

                compression_config = CompressionConfig(
                    min_msg_count=12,
                    min_char_count=6000,
                    keep_last=6,
                    provider_fn=_compress_llm,
                )
                self.processor.compressor = __import__("dragon.compression", fromlist=["ContextCompressor"]).ContextCompressor(
                    config=compression_config,
                )
            except Exception as _exc:
                import logging
                _log = logging.getLogger("dragon.gateway")
                _log.warning("Compression init failed: %s", _exc)

        # Register routes and lifecycle hooks
        self._register_routes()
        self._register_lifecycle()

        logger.info("GatewayServer ready")


    def _build_system_prompt(self) -> str:
        """Build system prompt aligned with Hermes reasoning standards."""
        # ── Step 1: Read base system prompt from config.yaml ────────────
        base_prompt = ""
        try:
            import yaml
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "config.yaml"
            )
            if os.path.exists(config_path):
                with open(config_path) as cf:
                    cfg = yaml.safe_load(cf) or {}
                base_prompt = cfg.get("gateway", {}).get("system_prompt", "")
                if base_prompt:
                    base_prompt = base_prompt.strip()
        except Exception:
            pass

        # ── Auto-inject available skills catalog ──────────────────────────
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

        prompts = []
        if base_prompt:
            prompts.append(base_prompt)
        else:
            # Fallback if config.yaml has no system_prompt
            prompts.extend([
                "你是 Dragon Agent，一个直接、主动的 AI 助手。你拥有持久记忆、丰富的工具生态和多平台网关。",
                "",
                "## 核心原则",
                "",
                "1. **直接动手，不要等确认** — 用户说\"做X\"就直接做，不要问\"要不要开始\"、\"需要确认吗\"。问就是浪费时间。",
                "2. **先推理后执行** — 接到任务先想清楚要怎么做，再调用工具，不要盲目试错。",
                "3. **错了就改** — 被纠正不要解释不要道歉，直接改对。",
                "4. **简洁可执行** — 回复简短有料，不要废话铺垫。",
                "",
                "## 工具纪律",
                "",
                "### Skills（强制）",
                "回复前扫描下方的可用技能列表。如果任何技能与当前任务相关，你必须用 skill_view(name) 加载它并按其指令执行。",
                "",
                "宁可多加载 — 技能包含专用知识和已验证的工作流，远胜凭通用能力临场发挥。即使你认为自己能处理，也要先加载技能 — 它可能包含该领域特有的陷阱、约定或用户偏好。",
                "",
                "如果加载的技能有过时的、不完整的或错误的步骤，立刻用 skill_manage(action='patch') 修复，不要等用户提。不维护的技能是负债。",
                "",
                "完成复杂任务（5+ 次工具调用）、修了一个难缠的 bug、或发现非平凡的工作流后，主动问用户要不要存成新技能。",
                "",
                "### 记忆管理",
                "",
                "你有跨会话的持久记忆。在以下情况立即保存：",
                "- 用户纠正了你或说\"记住这个\"",
                "- 用户分享了偏好、习惯、个人信息（姓名、角色、时区、编码风格）",
                "- 你发现了环境相关事实（OS、已安装工具、项目结构）",
                "- 你学到了约定、API 陷阱、或该用户设置特有的工作流",
                "",
                "优先级：用户偏好和纠正 > 环境事实 > 程序性知识。最有价值的记忆是防止用户重复自己的话。",
                "",
                "不要保存：任务进度、会话结果、已完成工作的日志、临时 TODO 状态。用 session_search 从历史记录中召回这些。",
                "",
                "写记忆用陈述性事实，不要用祈使句。\"用户偏好简洁回复\" 是对的写法，\"总是简洁回复\" 是错的 — 祈使句在未来会话中会被当成指令重新执行。",
                "",
                "### 会话搜索",
                "用户提到\"上次我们做过\"、\"还记得吗\"、\"之前那个方案\"时，用 session_search 主动搜索历史，不要让他们重复。跨会话引用场景同理。",
                "",
                "## 平台规范",
                "",
                "当前平台：**飞书（Lark）**",
                "",
                "- 飞书渲染 Markdown — 粗体、斜体、代码块、链接都支持",
                "- 发送媒体文件：回复中使用 `MEDIA:/absolute/path/to/file`",
                "  - 图片 (.jpg, .png, .webp) 直接内嵌显示",
                "  - 音频作为语音消息发送",
                "  - 其他文件作为附件",
                "- 中文回复，除非用户指定其他语言",
                "",
                "## 当前会话上下文",
                "",
                "会话状态（连接平台、默认频道、调度目标）在每个会话开始处注入。注意 Home Channels 里的默认投递目标。",
                "",
                "## 与 Hermes 的关系",
                "",
                "你是 Dragon Agent（192.168.0.32 / 192.168.0.100 上的独立实例），与 Hermes 共享相同的架构哲学和提示词标准，但各自独立运行。遇到 Dragon 自身的问题（配置、部署、Bug）时参考 dragon-agent 技能文档解决。",
                "",
            ])
        if user_text:
            prompts.extend(["", "USER PROFILE", user_text])
        if memory_text:
            prompts.extend(["", "MEMORY (your personal notes)", memory_text])
        return "\n".join(prompts)

    def _rebuild_system_prompt_with_memory(self, memory_text: str, user_text: str) -> str:
        """Rebuild full system prompt: base + tools + skills + memory every turn."""
        base_prompt = ""
        try:
            import yaml
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "config.yaml"
            )
            if os.path.exists(config_path):
                with open(config_path) as cf:
                    cfg = yaml.safe_load(cf) or {}
                base_prompt = cfg.get("gateway", {}).get("system_prompt", "")
                if base_prompt:
                    base_prompt = base_prompt.strip()
        except Exception:
            pass

        # ── Dynamic tool list (compact — names only to save tokens) ──
        tool_lines = ["", "## Available Tools", ""]
        if self.tool_registry:
            tools = self.tool_registry.list_tools()
            # Group by category, show names only
            cats = {}
            for t in tools:
                cat = t.get("category", "general")
                cats.setdefault(cat, []).append(t.get("name", "?"))
            for cat in sorted(cats):
                names = ", ".join(cats[cat][:15])
                tool_lines.append(f"[{cat}] {names}")
            tool_lines.append("")
            tool_lines.append("Use tools by name. Read files with read_file/write_file/search_files.")
            tool_lines.append("Critical: memory tool saves facts across sessions. Use it proactively.")
            tool_lines.append("You can send images/files with MEDIA:/path in your response.")
        tool_catalog = "\n".join(tool_lines) if len(tool_lines) > 3 else ""

        # ── Skills catalog ──
        skills_catalog = ""
        try:
            import json
            skills_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "dragon_data", "skills"
            )
            if os.path.isdir(skills_dir):
                skill_entries = []
                for fname in sorted(os.listdir(skills_dir)):
                    if not fname.endswith(".json"):
                        continue
                    fpath = os.path.join(skills_dir, fname)
                    try:
                        with open(fpath, "r") as _f:
                            data = json.load(_f)
                        meta = data.get("meta", {})
                        name = meta.get("name", fname.replace(".json", ""))
                        desc = meta.get("description", "")
                        if name and desc:
                            skill_entries.append((name, desc))
                    except Exception:
                        pass
                if skill_entries:
                    lines = [
                        "", "## Skills (mandatory)", "",
                        "Before replying, scan the skills below. If a skill matches ",
                        "load it with skill_view(name) and follow its instructions.", "",
                        "<available_skills>",
                    ]
                    for name, desc in skill_entries[:50]:
                        lines.append(f"  {name}: {desc}")
                    lines.append("</available_skills>")
                    skills_catalog = "\n".join(lines)
        except Exception:
            pass

        prompts = []
        if base_prompt:
            prompts.append(base_prompt)
        if tool_catalog:
            prompts.append(tool_catalog)
        if skills_catalog:
            prompts.append(skills_catalog)
        prompts.append("")
        if user_text:
            prompts.extend(["══════════════════════════════════════════════", "USER PROFILE", user_text, ""])
        if memory_text:
            prompts.extend(["══════════════════════════════════════════════", "MEMORY (your personal notes)", memory_text, ""])
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
