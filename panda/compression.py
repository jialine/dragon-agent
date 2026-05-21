"""
Panda Context Compression — Prevent token overflow in long conversations.

Strategy: When the conversation exceeds a threshold, compress older messages
by summarizing them or truncating them, keeping the most recent messages intact.

Inspired by Hermes Agent's trajectory compressor but simpler.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("panda.compression")


# ────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────

@dataclass
class CompressionConfig:
    enabled: bool = True
    threshold_ratio: float = 0.75       # trigger when usage > 75% of limit
    target_ratio: float = 0.50           # compress down to 50% of limit
    keep_recent: int = 10                # always keep last N messages intact
    keep_system: bool = True             # always keep system message
    summary_model: str = ""              # model for generating summaries ("" = use same)
    max_summary_tokens: int = 500        # max tokens per summary chunk


# ────────────────────────────────────────────────────────────────────
# Token estimation (fast, no API call)
# ────────────────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Fast token count estimation. ~4 chars per token for English, ~2 for CJK."""
    if not text:
        return 0

    cjk_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', text))
    other_chars = len(text) - cjk_chars

    # CJK: ~1.5 chars/token, Other: ~4 chars/token
    return int(cjk_chars / 1.5 + other_chars / 4)


def estimate_message_tokens(messages: List[Dict[str, str]]) -> int:
    """Estimate total tokens in a list of messages."""
    total = 0
    for m in messages:
        total += estimate_tokens(m.get("content", ""))
        total += 4  # role overhead
    return total


# ────────────────────────────────────────────────────────────────────
# Compressor
# ────────────────────────────────────────────────────────────────────


class ContextCompressor:
    """Compress conversation context to stay within token limits.

    Usage::

        compressor = ContextCompressor(
            config=CompressionConfig(threshold_ratio=0.75, target_ratio=0.50),
            context_limit=128000,
        )

        messages = [...]  # long conversation
        if compressor.needs_compression(messages):
            messages = compressor.compress(messages)
    """

    def __init__(
        self,
        config: CompressionConfig = None,
        context_limit: int = 128000,
        summary_fn: Optional[Callable] = None,
    ) -> None:
        self.config = config or CompressionConfig()
        self.context_limit = context_limit
        self._summary_fn = summary_fn  # async function for LLM summarization
        self._compression_count = 0
        logger.info(
            "ContextCompressor ready — limit=%d, threshold=%.0f%%, target=%.0f%%",
            context_limit,
            self.config.threshold_ratio * 100,
            self.config.target_ratio * 100,
        )

    def needs_compression(self, messages: List[Dict[str, str]]) -> bool:
        """Check if the conversation needs compression."""
        if not self.config.enabled:
            return False
        estimated = estimate_message_tokens(messages)
        return estimated > int(self.context_limit * self.config.threshold_ratio)

    def usage_ratio(self, messages: List[Dict[str, str]]) -> float:
        """Current token usage as a ratio of context limit."""
        return estimate_message_tokens(messages) / self.context_limit

    def compress(
        self,
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """Compress messages using truncation (fast path).

        Strategy:
        1. Keep system message (if config.keep_system)
        2. Keep the last N messages intact (config.keep_recent)
        3. Truncate middle messages evenly to hit target ratio
        4. Insert a summary message at the compression boundary

        For LLM-based summarization, use compress_async().
        """
        if not messages:
            return messages

        target_tokens = int(self.context_limit * self.config.target_ratio)
        current_tokens = estimate_message_tokens(messages)

        if current_tokens <= target_tokens:
            return messages  # already fits

        result = []

        # 1. Keep system message
        sys_idx = 0
        if self.config.keep_system and messages[0].get("role") == "system":
            result.append(messages[0])
            sys_idx = 1

        # 2. Keep recent messages
        keep_recent = min(self.config.keep_recent, len(messages) - sys_idx)
        recent = messages[-keep_recent:]
        middle = messages[sys_idx:-keep_recent] if keep_recent > 0 else messages[sys_idx:]

        # 3. Calculate how many middle messages to keep
        recent_tokens = estimate_message_tokens(recent)
        sys_tokens = estimate_tokens(messages[0].get("content", "")) + 4 if sys_idx > 0 else 0
        budget_for_middle = target_tokens - recent_tokens - sys_tokens

        compressed_middle = self._truncate_messages(middle, budget_for_middle)

        # 4. Add compression summary
        if compressed_middle:
            removed_count = len(middle) - len(compressed_middle)
            if removed_count > 0:
                summary = (
                    f"[上下文已压缩: 省略了 {removed_count} 条中间消息。"
                    f"当前会话共 {len(messages)} 条消息。]"
                )
                result.append({"role": "system", "content": summary})

        result.extend(compressed_middle)
        result.extend(recent)

        self._compression_count += 1
        logger.info(
            "Compressed: %d→%d messages (%d→%d est. tokens)",
            len(messages), len(result), current_tokens, estimate_message_tokens(result),
        )

        return result

    async def compress_async(
        self,
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """Compress messages using LLM summarization (quality path).

        Requires summary_fn to be set (an async function that takes messages
        and returns a summary string).
        """
        if not messages or not self._summary_fn:
            return self.compress(messages)

        target_tokens = int(self.context_limit * self.config.target_ratio)
        current_tokens = estimate_message_tokens(messages)
        if current_tokens <= target_tokens:
            return messages

        result = []

        # Keep system
        sys_idx = 0
        if self.config.keep_system and messages[0].get("role") == "system":
            result.append(messages[0])
            sys_idx = 1

        # Keep recent
        keep_recent = min(self.config.keep_recent, len(messages) - sys_idx)
        recent = messages[-keep_recent:]
        middle = messages[sys_idx:-keep_recent]

        # Summarize middle messages via LLM
        if middle and self._summary_fn:
            try:
                summary_text = await self._summary_fn(
                    middle,
                    max_tokens=self.config.max_summary_tokens,
                )
                result.append({"role": "system", "content": f"[对话摘要] {summary_text}"})
                self._compression_count += 1
                logger.info("Async compressed: summarized %d middle messages", len(middle))
            except Exception as e:
                logger.warning("LLM summarization failed, falling back to truncation: %s", e)
                # Fallback to truncation
                recent_tokens = estimate_message_tokens(recent)
                sys_tokens = estimate_tokens(messages[0].get("content", "")) + 4 if sys_idx > 0 else 0
                budget = target_tokens - recent_tokens - sys_tokens
                compressed = self._truncate_messages(middle, budget)
                result.extend(compressed)
        else:
            result.extend(middle)

        result.extend(recent)
        return result

    def _truncate_messages(
        self, messages: List[Dict[str, str]], token_budget: int
    ) -> List[Dict[str, str]]:
        """Truncate messages evenly to fit within token budget."""
        if not messages or token_budget <= 0:
            return []

        # Simple strategy: keep as many messages from the END as fit
        result = []
        used = 0
        for m in reversed(messages):
            mt = estimate_tokens(m.get("content", "")) + 4
            if used + mt <= token_budget:
                result.append(m)
                used += mt
            else:
                break

        result.reverse()
        return result

    @property
    def compression_count(self) -> int:
        return self._compression_count
