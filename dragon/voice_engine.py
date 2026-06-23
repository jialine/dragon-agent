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
from typing import Tuple, Optional

logger = logging.getLogger("dragon.voice_engine")

# Default Chinese neural voice via Microsoft Edge TTS
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class VoiceEngine:
    """流式语音合成引擎，用于语音模式。

    从 LLM 流式输出中逐块接收文本，自动检测句子边界，
    并在后台异步合成语音片段。

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

        Args:
            buffer: 当前缓冲区内容（实际上不使用此参数，直接操作 self.buffer）。

        Returns:
            完整句子文本；若无完整句子则返回 None。
        """
        # 匹配以句子结束标点结尾的文本
        match = re.match(r'^(.*?[。！？.!?\n])\s*(.*)$', self.buffer, re.DOTALL)
        if match:
            sentence = match.group(1).strip()
            remaining = match.group(2)
            if sentence:
                # 将剩余文本保留在 buffer 中
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
