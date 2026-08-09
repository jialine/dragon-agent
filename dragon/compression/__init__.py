"""
Dragon Context Compressor — Hermes-aligned conversation compression.

Automatically summarizes older messages when conversation history exceeds
a threshold, keeping context manageable for the LLM while preserving
important information.

Thresholds:
- MIN_MSG_COUNT = 12  — minimum messages before compression triggers
- MIN_CHAR_COUNT = 6000 — minimum characters before compression triggers
- KEEP_LAST = 6  — always keep the most recent K messages uncompressed
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dragon.compression")


# ════════════════════════════════════════════════════════════════════
# Compressor Config
# ════════════════════════════════════════════════════════════════════


class CompressionConfig:
    """Hermes-aligned compression thresholds and behavior."""

    def __init__(
        self,
        min_msg_count: int = 12,
        min_char_count: int = 6000,
        keep_last: int = 6,
        provider_fn=None,
    ):
        self.min_msg_count = min_msg_count
        self.min_char_count = min_char_count
        self.keep_last = keep_last
        self.provider_fn = provider_fn  # async fn for summary generation


# ════════════════════════════════════════════════════════════════════
# ContextCompressor
# ════════════════════════════════════════════════════════════════════


class ContextCompressor:
    """Hermes-aligned conversation compressor.

    When conversation history exceeds thresholds, older messages are
    summarized into a compact form and prepended as a system message.
    """

    def __init__(self, config: CompressionConfig):
        self._config = config
        self._compression_count = 0

    def needs_compression(self, history: List[Dict[str, Any]]) -> bool:
        """Check if history should be compressed."""
        msg_count = len(history)
        total_chars = sum(len(str(m.get("content", ""))) for m in history)

        if msg_count < self._config.min_msg_count:
            return False
        if total_chars < self._config.min_char_count:
            return False

        return True

    async def compress(
        self,
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Compress conversation history.

        Strategy:
        1. Keep the system prompt (if first message is system)
        2. Summarize messages from index 1 to -(keep_last)
        3. Keep the most recent keep_last messages intact
        4. Prepend summary as a system message
        """
        keep = self._config.keep_last
        total = len(history)

        if total <= keep + 2:
            return history  # Nothing to compress

        # ── Partition history ──
        system_msg = None
        start = 0
        if history[0].get("role") == "system":
            system_msg = history[0]
            start = 1

        # Messages to summarize: [start .. total-keep)
        to_summarize = history[start:total - keep]
        to_keep = history[total - keep:]

        if not to_summarize:
            return history

        # ── Build a compact text to summarize ──
        conversation_text = self._build_conversation_text(to_summarize)

        # ── Generate summary ──
        summary = await self._summarize(conversation_text)
        if not summary:
            logger.warning("Compression summary empty, returning uncompressed history")
            return history

        self._compression_count += 1
        logger.info(
            "Compression #%d: %d msgs → summary (%d chars), keeping last %d",
            self._compression_count, len(to_summarize), len(summary), keep,
        )

        # ── Rebuild history ──
        compressed = []
        if system_msg:
            # Inject summary into system message
            sys_content = system_msg.get("content", "")
            compressed.append({
                "role": "system",
                "content": f"{sys_content}\n\n## 对话摘要 (第 {self._compression_count} 次压缩)\n\n{summary}",
            })
        else:
            compressed.append({
                "role": "system",
                "content": f"## 对话摘要 (第 {self._compression_count} 次压缩)\n\n{summary}",
            })
        compressed.extend(to_keep)

        return compressed

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _build_conversation_text(messages: List[Dict[str, Any]]) -> str:
        """Build a compact text representation of messages for summarization."""
        lines = []
        for m in messages:
            role = m.get("role", "unknown")
            content = str(m.get("content", ""))
            # Truncate very long messages
            if len(content) > 2000:
                content = content[:2000] + "..."
            prefix = {"user": "👤", "assistant": "🤖", "tool": "🔧", "system": "⚙️"}.get(role, "•")
            lines.append(f"{prefix} [{role}]: {content}")
        return "\n\n".join(lines)

    async def _summarize(self, conversation_text: str) -> str:
        """Generate a summary of the conversation using the provider."""
        if self._config.provider_fn:
            try:
                prompt = (
                    "你是一个对话压缩器。请用中文将以下对话历史压缩为一段简洁的摘要，"
                    "保留关键信息：用户的需求、你的回答要点、重要的工具调用结果、"
                    "任何需要记住的事实和决定。\n\n"
                    "不要超过 800 字。直接输出摘要，不要加前言。\n\n"
                    f"{conversation_text}"
                )
                summary = await self._config.provider_fn(
                    [{"role": "user", "content": prompt}],
                )
                if summary and len(summary.strip()) > 20:
                    return summary.strip()
            except Exception as exc:
                logger.error("Summary generation failed: %s", exc)
        return ""
