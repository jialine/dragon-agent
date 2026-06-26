"""
Dragon Agent — Voice Engine (Streaming TTS)
============================================

Provides real-time streaming text-to-speech by hooking into LLM output chunks.
Uses Microsoft Edge TTS (edge-tts) for free, high-quality neural synthesis.

VoiceEngine buffers incoming text chunks, detects sentence boundaries,
and synthesizes complete sentences to audio asynchronously.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncGenerator, AsyncIterable, List, Tuple, Optional, Union

logger = logging.getLogger("dragon.voice_engine")

# Default Chinese neural voice via Microsoft Edge TTS
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# Sentence-ending punctuation for boundary detection
# Requires at least one character before the boundary (no empty sentences)
_SENTENCE_BOUNDARY_RE = re.compile(r'^(.+?[。！？.!?\n])\s*(.*)$', re.DOTALL)

# Maximum buffer length before forcing a split at a soft boundary
_MAX_BUFFER_LENGTH = 300
# Soft boundaries for forced splitting (commas, semicolons, etc.)
_SOFT_BOUNDARY_RE = re.compile(r'^(.{50,}?[,，;；:：])\s*(.*)$', re.DOTALL)


class VoiceEngine:
    """流式语音合成引擎，用于语音模式。

    从 LLM 流式输出中逐块接收文本，自动检测句子边界，
    并在后台异步合成语音片段。

    支持两种使用模式：
    1. consume() + next_audio() — 后台队列模式，适合长时间流式输入。
    2. stream() — 异步生成器模式，一次性传入文本，逐句合成并 yield。

    Attributes:
        voice: edge-tts 语音标识符。
        speed: 语速倍率（暂未启用，保留以备后用）。
    """

    def __init__(self, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> None:
        self.voice = voice
        self.speed = speed
        self.buffer = ""
        self.sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.audio_queue: asyncio.Queue[Tuple[str, bytes] | None] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Streaming API (async generator) ──────────────────────────────

    async def stream(
        self,
        text: Union[str, AsyncIterable[str]],
    ) -> AsyncGenerator[Tuple[str, bytes], None]:
        """句子级流式语音合成 — 异步生成器。

        将文本按 。！？\\n 等边界拆分为句子，逐句合成 MP3 音频，
        每完成一句就 yield 一个 (句子文本, MP3 bytes) 元组。

        支持两种输入模式：
        - str: 完整文本，内部分句后逐句合成。
        - AsyncIterable[str]: 流式文本块，会先缓冲直到检测到句子边界。

        对于无标点的超长文本，会在 300 字符附近强制断句。

        Args:
            text: 待合成的文本（字符串）或文本块异步迭代器。

        Yields:
            (sentence_text, mp3_bytes) 元组。
        """
        buffer = ""

        if isinstance(text, str):
            # String mode: split into sentences and synthesize
            async for item in self._stream_from_string(text):
                yield item
            return

        # AsyncIterable mode: buffer chunks until boundaries
        async for chunk in text:
            if not chunk:
                continue
            buffer += chunk

            # Extract complete sentences
            while True:
                sentence, buffer = self._extract_sentence(buffer)
                if sentence is None:
                    break
                audio = await self._synthesize(sentence)
                if audio:
                    yield (sentence, audio)

        # Flush remaining buffer
        remaining = buffer.strip()
        if remaining:
            audio = await self._synthesize(remaining)
            if audio:
                yield (remaining, audio)

    async def _stream_from_string(
        self, text: str,
    ) -> AsyncGenerator[Tuple[str, bytes], None]:
        """从完整字符串中分句合成。

        使用 sentence-level streaming：拆分 → 逐句合成 → yield。
        """
        remaining = text
        while remaining:
            sentence, remaining = self._extract_sentence(remaining)
            if sentence is None:
                # No boundary found, synthesize the rest
                rest = remaining.strip()
                if rest:
                    audio = await self._synthesize(rest)
                    if audio:
                        yield (rest, audio)
                return
            audio = await self._synthesize(sentence)
            if audio:
                yield (sentence, audio)

    def _extract_sentence(self, buffer: str) -> Tuple[Optional[str], str]:
        """从缓冲区提取一个完整句子。

        优先级：
        1. 匹配句子结束标点（。！？.!?\\n）
        2. 若缓冲区超过 _MAX_BUFFER_LENGTH，尝试在软边界处断句
        3. 都不匹配则返回 (None, buffer)，等待更多输入

        Args:
            buffer: 当前文本缓冲区。

        Returns:
            (sentence, remaining) — sentence 为完整句子或 None。
        """
        # Try sentence-ending punctuation first
        match = _SENTENCE_BOUNDARY_RE.match(buffer)
        if match:
            sentence = match.group(1).strip()
            remaining = match.group(2)
            if sentence:
                return (sentence, remaining)
            # Empty sentence after boundary — skip the boundary char
            return (None, remaining)

        # If buffer is too long without any boundary, force a split
        if len(buffer) > _MAX_BUFFER_LENGTH:
            soft_match = _SOFT_BOUNDARY_RE.match(buffer)
            if soft_match:
                sentence = soft_match.group(1).strip()
                remaining = soft_match.group(2)
                if sentence:
                    logger.debug(
                        "Forced sentence split at soft boundary "
                        "(buffer length=%d)", len(buffer),
                    )
                    return (sentence, remaining)
            # Last resort: split at _MAX_BUFFER_LENGTH
            logger.debug(
                "Forced sentence split at hard boundary "
                "(buffer length=%d, no soft boundary found)", len(buffer),
            )
            sentence = buffer[:_MAX_BUFFER_LENGTH].strip()
            remaining = buffer[_MAX_BUFFER_LENGTH:]
            return (sentence, remaining)

        return (None, buffer)

    # ── Public API ──────────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台合成任务。"""
        self._running = True
        self._task = asyncio.create_task(self._synthesis_loop())
        logger.debug("VoiceEngine synthesis loop started (voice=%s)", self.voice)

    def consume(self, text_chunk: str) -> None:
        """投喂来自 LLM 流式输出的文本块。

        文本会在内部缓冲，检测到完整句子后自动入队合成。

        Args:
            text_chunk: 增量文本片段。
        """
        self.buffer += text_chunk
        sentence = self._detect_boundary(self.buffer)
        if sentence:
            self.sentence_queue.put_nowait(sentence)
            # _detect_boundary 已将 buffer 更新为剩余文本，无需再次清空

    async def flush(self) -> None:
        """刷新缓冲区中剩余的文本并等待全部合成完成。

        应在 LLM 输出结束后调用，确保最后一段文本也被合成。
        """
        if self.buffer.strip():
            await self.sentence_queue.put(self.buffer.strip())
            self.buffer = ""
        # 发送结束信号
        await self.sentence_queue.put(None)
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug("VoiceEngine flushed")

    async def stop(self) -> None:
        """停止合成引擎。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.debug("VoiceEngine stopped")

    async def next_audio(self) -> Tuple[str, bytes] | None:
        """获取下一个已合成的音频片段。

        Returns:
            (text, audio_bytes) 元组，text 为对应的句子文本，
            audio_bytes 为 MP3 音频数据。
            若合成已结束则返回 None。
        """
        return await self.audio_queue.get()

    # ── Sentence Boundary Detection ─────────────────────────────────

    def _detect_boundary(self, buffer: str) -> str | None:
        """检测句子边界。

        匹配中文句号/感叹号/问号、英文标点及换行符作为句子边界。
        检测到完整句子后，更新内部 buffer 为剩余文本。

        现在委托给 _extract_sentence()，支持超长文本强制断句。

        Args:
            buffer: 当前缓冲区内容（实际上不使用此参数，直接操作 self.buffer）。

        Returns:
            完整句子文本；若无完整句子则返回 None。
        """
        sentence, remaining = self._extract_sentence(self.buffer)
        if sentence is not None:
            self.buffer = remaining
            return sentence
        return None

    # ── Background Synthesis Loop ───────────────────────────────────

    async def _synthesis_loop(self) -> None:
        """后台循环：从队列中取出句子并合成音频。"""
        while self._running:
            try:
                sentence = await asyncio.wait_for(
                    self.sentence_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if sentence is None:
                break

            audio = await self._synthesize(sentence)
            if audio:
                await self.audio_queue.put((sentence, audio))

        # 发送结束信号
        await self.audio_queue.put(None)
        logger.debug("VoiceEngine synthesis loop ended")

    # ── TTS Backend ─────────────────────────────────────────────────

    async def _synthesize(self, text: str) -> bytes | None:
        """使用 edge-tts 流式 API 将文本合成为 MP3 音频。

        Args:
            text: 待合成的文本。

        Returns:
            MP3 音频字节数据；合成失败时返回 None。
        """
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text=text, voice=self.voice)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            if chunks:
                return b"".join(chunks)
        except ImportError:
            logger.warning("edge-tts not installed — VoiceEngine will produce no audio")
        except Exception:
            logger.exception("TTS synthesis failed for text: %s", text[:80])
        return None
