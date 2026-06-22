"""
Token estimation for the Dragon Compressor.

Supports tiktoken (preferred) with heuristic fallback for environments
where tiktoken is not installed.

Token counting handles:
  - Plain text
  - OpenAI-format message lists
  - CJK-aware heuristic fallback
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("dragon.compressor.estimator")


# ────────────────────────────────────────────────────────────────────
# Optional tiktoken support
# ────────────────────────────────────────────────────────────────────

_tiktoken_available = False
_cl100k_encoder = None
_o200k_encoder = None


def _try_import_tiktoken() -> None:
    """Lazily import tiktoken and cache encoders."""
    global _tiktoken_available, _cl100k_encoder, _o200k_encoder
    if _tiktoken_available:
        return
    try:
        import tiktoken as _tk
        _cl100k_encoder = _tk.get_encoding("cl100k_base")
        try:
            _o200k_encoder = _tk.get_encoding("o200k_base")
        except Exception:
            _o200k_encoder = None
        _tiktoken_available = True
        logger.debug("tiktoken loaded (cl100k_base%s)", " + o200k_base" if _o200k_encoder else "")
    except ImportError:
        logger.debug("tiktoken not installed; using heuristic token estimation")
    except Exception as exc:
        logger.warning("tiktoken loading failed: %s; using heuristic estimation", exc)


@dataclass
class EstimateResult:
    """Result of a token count estimation."""

    tokens: int
    chars: int
    method: str  # "tiktoken" | "heuristic"
    model_family: str = "default"


class TokenEstimator:
    """Fast token counting using tiktoken with heuristic fallback.

    Parameters
    ----------
    model_family : str
        Target model family for token estimation (e.g., "gpt-4o", "deepseek-chat").
        Affects which tiktoken encoder is used. Default uses cl100k_base.
    """

    # Per-token overhead constants matching OpenAI's counting rules
    _MESSAGE_OVERHEAD = 4  # <im_start>role\n<im_end>\n
    _TOOL_CALL_BASE = 8
    _REPLY_OVERHEAD = 3  # <|start|>assistant<|message|>

    def __init__(self, model_family: str = "default") -> None:
        _try_import_tiktoken()
        self.model_family = model_family
        logger.debug("TokenEstimator ready: tiktoken=%s, model_family=%s",
                     _tiktoken_available, model_family)

    # ── Core counting API ───────────────────────────────────────────

    def estimate_text(self, text: str) -> EstimateResult:
        """Estimate token count for a plain string.

        Args:
            text: The text to count tokens for.

        Returns:
            EstimateResult with token count and method used.
        """
        if not text:
            return EstimateResult(tokens=0, chars=0, method="tiktoken")

        if _tiktoken_available:
            encoder = self._resolve_encoder()
            tokens = len(encoder.encode(text))
            return EstimateResult(tokens=tokens, chars=len(text), method="tiktoken",
                                  model_family=self.model_family)
        else:
            return self._heuristic_estimate(text)

    def estimate_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        include_tool_calls: bool = True,
    ) -> int:
        """Estimate total tokens for a list of messages.

        This mirrors OpenAI's token counting rules (3 tokens per message
        for priming, plus content tokens, plus per-name overhead).

        Args:
            messages: List of message dicts with 'role' and 'content'.
            include_tool_calls: If True, count tool calls and tool results.

        Returns:
            Total estimated token count.
        """
        if not messages:
            return 0

        if _tiktoken_available:
            return self._tiktoken_count_messages(messages, include_tool_calls)
        else:
            return self._heuristic_count_messages(messages)

    def estimate_single_message(self, message: Dict[str, Any]) -> int:
        """Estimate tokens for a single message dict.

        Args:
            message: Dict with 'role' and 'content'.

        Returns:
            Estimated token count for this message.
        """
        return self.estimate_messages([message])

    # ── Internal: tiktoken path ─────────────────────────────────────

    def _resolve_encoder(self):
        """Pick the best encoder for the model family."""
        if self.model_family.startswith(("gpt-4", "gpt-3.5", "o1", "o3", "o4")):
            return _o200k_encoder or _cl100k_encoder
        return _cl100k_encoder

    def _tiktoken_count_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        include_tool_calls: bool = True,
    ) -> int:
        encoder = self._resolve_encoder()
        total = 0

        for msg in messages:
            total += self._MESSAGE_OVERHEAD
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Count role
            if role:
                total += len(encoder.encode(str(role)))

            # Count content (string or multimodal parts)
            total += self._tiktoken_count_content(encoder, content)

            # Count name if present
            name = msg.get("name")
            if name:
                total += len(encoder.encode(str(name)))
                total -= 1  # role is already in overhead

            # Count tool calls
            if include_tool_calls:
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        total += len(encoder.encode(str(fn.get("name", ""))))
                        total += len(encoder.encode(str(fn.get("arguments", ""))))
                        total += self._TOOL_CALL_BASE

            # Count tool_call_id
            tc_id = msg.get("tool_call_id")
            if tc_id:
                total += len(encoder.encode(str(tc_id)))

        total += self._REPLY_OVERHEAD
        return total

    def _tiktoken_count_content(self, encoder, content: Any) -> int:
        """Count tokens in message content (string or multimodal parts list)."""
        if content is None:
            return 0
        if isinstance(content, str):
            return len(encoder.encode(content))
        if isinstance(content, list):
            total = 0
            for part in content:
                if isinstance(part, str):
                    total += len(encoder.encode(part))
                elif isinstance(part, dict):
                    for key in ("text", "image_url", "input_image"):
                        val = part.get(key)
                        if isinstance(val, str):
                            total += len(encoder.encode(val))
                        elif isinstance(val, dict):
                            total += len(encoder.encode(val.get("url", "") or ""))
                else:
                    total += len(encoder.encode(str(part)))
            return total
        return len(encoder.encode(str(content)))

    # ── Internal: heuristic fallback ────────────────────────────────

    def _heuristic_estimate(self, text: str) -> EstimateResult:
        """Fast heuristic token count: ~4 chars/token for English, ~1.5 for CJK."""
        cjk_chars = len(
            re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f\uff00-\uffef]', text)
        )
        other_chars = max(0, len(text) - cjk_chars)
        tokens = int(cjk_chars / 1.5 + other_chars / 4)
        return EstimateResult(tokens=max(tokens, 1) if text else 0,
                              chars=len(text), method="heuristic")

    def _heuristic_count_messages(self, messages: Sequence[Dict[str, Any]]) -> int:
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += self._heuristic_estimate(content).tokens
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        total += self._heuristic_estimate(part).tokens
                    elif isinstance(part, dict):
                        for val in part.values():
                            if isinstance(val, str):
                                total += self._heuristic_estimate(val).tokens
            elif content is not None:
                total += self._heuristic_estimate(str(content)).tokens
            role = m.get("role", "")
            if role:
                total += self._heuristic_estimate(role).tokens
            total += 4  # message overhead
        return total


# ────────────────────────────────────────────────────────────────────
# Module-level convenience
# ────────────────────────────────────────────────────────────────────

_default_estimator = None


def get_estimator(model_family: str = "default") -> TokenEstimator:
    """Get or create a cached TokenEstimator instance."""
    global _default_estimator
    if _default_estimator is None or _default_estimator.model_family != model_family:
        _default_estimator = TokenEstimator(model_family=model_family)
    return _default_estimator


def estimate_tokens(text: str) -> int:
    """Convenience: estimate tokens in a string (using default estimator)."""
    return get_estimator().estimate_text(text).tokens


def estimate_message_tokens(messages: Sequence[Dict[str, Any]]) -> int:
    """Convenience: estimate tokens in a message list (using default estimator)."""
    return get_estimator().estimate_messages(messages)
