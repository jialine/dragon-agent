"""
Dragon Agent — Think Scrubber
=============================

Removes ``<think>...</think>`` (and related reasoning tags) from
reasoning model output. Handles both complete-string and streaming
delta scenarios with a stateful state machine.

Provides:

1. **``strip_think_blocks``** — regex-based scrubber for complete strings
   (non-streaming). Fast pure-Python implementation — no dependency on
   regex if the pattern set is simple.  Based on Hermes's ``_strip_think_blocks``
   but substantially cleaner for Dragon's provider layer.
2. **``StreamingThinkScrubber``** — stateful streaming scrubber that
   correctly handles tags split across delta boundaries.  Ported from
   Hermes's ``agent/think_scrubber.py`` with identical semantics.
3. **Configurable** — strip vs preserve-as-metadata, tag variants,
   block-boundary rules.

Usage::

    # Non-streaming
    cleaned = strip_think_blocks(response.content)

    # Streaming
    scrubber = StreamingThinkScrubber()
    for delta in stream:
        visible = scrubber.feed(delta)
        if visible:
            yield visible
    tail = scrubber.flush()
    if tail:
        yield tail

Tag variants handled (case-insensitive):
    ``<think>``, ``<thinking>``, ``<reasoning>``, ``<thought>``,
    ``<REASONING_SCRATCHPAD>``.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

__all__ = [
    "strip_think_blocks",
    "StreamingThinkScrubber",
    "ThinkScrubberConfig",
]

# ────────────────────────────────────────────────────────────────────
# Recognised tag variants
# ────────────────────────────────────────────────────────────────────

_OPEN_TAG_NAMES: Tuple[str, ...] = (
    "think",
    "thinking",
    "reasoning",
    "thought",
    "REASONING_SCRATCHPAD",
)

_OPEN_TAGS: Tuple[str, ...] = tuple(f"<{name}>" for name in _OPEN_TAG_NAMES)
_CLOSE_TAGS: Tuple[str, ...] = tuple(f"</{name}>" for name in _OPEN_TAG_NAMES)

# Precompute longest tag length (for partial-tag hold-back)
_MAX_TAG_LEN: int = max(len(tag) for tag in _OPEN_TAGS + _CLOSE_TAGS)


# ────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────

class ThinkScrubberConfig:
    """Configuration for think-block scrubbing.

    Attributes:
        strip: If True, remove think blocks entirely. If False, preserve
               reasoning content as metadata (for tools that need it).
        tag_names: Recognised tag names (case-insensitive).
        strip_orphan_close_tags: Remove dangling ``</think>`` tags that
               have no matching open tag.
        boundary_gating: Only treat an open tag as a block opener when it
               appears at a natural boundary (start of text, after newline,
               or after whitespace-only line). Prevents prose that mentions
               ``<think>`` from being incorrectly suppressed.
    """

    def __init__(
        self,
        *,
        strip: bool = True,
        tag_names: Tuple[str, ...] = _OPEN_TAG_NAMES,
        strip_orphan_close_tags: bool = True,
        boundary_gating: bool = True,
    ) -> None:
        self.strip = strip
        self.tag_names = tag_names
        self.strip_orphan_close_tags = strip_orphan_close_tags
        self.boundary_gating = boundary_gating

        # Materialise tags from names
        self.open_tags: Tuple[str, ...] = tuple(f"<{name}>" for name in tag_names)
        self.close_tags: Tuple[str, ...] = tuple(f"</{name}>" for name in tag_names)
        self.max_tag_len: int = max(
            len(tag) for tag in self.open_tags + self.close_tags
        ) if self.open_tags else 0


# ────────────────────────────────────────────────────────────────────
# Regex builder for strip_think_blocks (non-streaming)
# ────────────────────────────────────────────────────────────────────

def _build_strip_regex(
    open_tags: Tuple[str, ...] = _OPEN_TAGS,
    close_tags: Tuple[str, ...] = _CLOSE_TAGS,
) -> re.Pattern:
    """Build the regex pattern for non-streaming think-block stripping.

    Covers three cases:
        1. ``<tag>content</tag>``  — closed pair anywhere
        2. ``<tag>content``         — unterminated open at start (greedy to end)
        3. ``</tag>content``        — orphan close tag (only if no open exists)
    """
    esc = re.escape

    # Closed pair: <tag>.*?</tag> for each variant
    closed_alternatives = "|".join(
        f"{esc(open_tag)}.*?{esc(close_tag)}"
        for open_tag, close_tag in zip(open_tags, close_tags)
    )

    # Unterminated open at buffer start: <tag>.*?$ (non-greedy to end)
    unterminated_alternatives = "|".join(
        f"{esc(tag)}.*?(?:$|(?:{esc(other_open)}))"
        for tag in open_tags
        for other_open in open_tags
    )
    # Simpler: just match <tag>.* to end
    unterminated_pattern = "|".join(
        f"{esc(tag)}(?:(?!{esc(tag)}).)*$" for tag in open_tags
    )

    # Orphan close tags: </tag> with preceding whitespace
    orphan_alternatives = "|".join(
        rf"(?<!\S){esc(tag)}" for tag in close_tags
    )

    full_pattern = f"({closed_alternatives})|({unterminated_pattern})|({orphan_alternatives})"
    return re.compile(full_pattern, re.IGNORECASE | re.DOTALL)


_strip_regex: Optional[re.Pattern] = None


def _get_strip_regex() -> re.Pattern:
    global _strip_regex
    if _strip_regex is None:
        _strip_regex = _build_strip_regex()
    return _strip_regex


# ────────────────────────────────────────────────────────────────────
# Non-streaming scrubber
# ────────────────────────────────────────────────────────────────────

def strip_think_blocks(text: str, *, config: ThinkScrubberConfig | None = None) -> str:
    """Remove ``<think>...</think>`` blocks from a complete response string.

    Designed for non-streaming (complete response) use. For streaming
    deltas, use ``StreamingThinkScrubber`` instead.

    Case-insensitive. Handles:

        1. ``<think>content</think>``        — closed pair anywhere
        2. ``<think>content``                — unterminated open at start
        3. ``</think>other content``          — orphan close tag
        4. Whitespace surrounding tags is stripped to keep prose flowing.

    Args:
        text: Raw response text from a reasoning model.
        config: Optional configuration for tag variants and behavior.

    Returns:
        Text with think blocks removed (or preserved as metadata if
        config.strip=False).

    Examples:
        >>> strip_think_blocks("<think>reasoning</think>answer")
        'answer'
        >>> strip_think_blocks("<think>internal</think>")
        ''
        >>> strip_think_blocks("line1\\n<think>reasoning</think>\\nline2")
        'line1\\n\\nline2'
        >>> strip_think_blocks("normal text")  # no think tag
        'normal text'
    """
    if not text or not isinstance(text, str):
        return text

    cfg = config or ThinkScrubberConfig()
    if not cfg.strip:
        return text

    if "<" not in text:
        return text

    # Build regex for current config's tags
    regex = _build_strip_regex(cfg.open_tags, cfg.close_tags)

    result = regex.sub("", text)

    # Clean up: collapse multiple blank lines
    result = re.sub(r"\n{3,}", "\n\n", result)

    # Remove leading/trailing whitespace-only lines but preserve
    # the structure
    result = result.strip()

    return result


# ────────────────────────────────────────────────────────────────────
# Streaming scrubber (stateful)
# ────────────────────────────────────────────────────────────────────

class StreamingThinkScrubber:
    """Stateful scrubber for streaming reasoning/thinking blocks.

    Correctly handles tags split across delta boundaries. The regex-based
    ``strip_think_blocks`` works on complete strings but destroys state
    when applied per-delta — e.g. if ``<think>`` arrives in delta 1 and
    ``</think>`` in delta 3, the per-delta regex removes delta 1 entirely
    but delta 2 leaks as visible content.

    This state machine:

    - Tracks whether we are inside an open block (``_in_block``)
    - Holds back partial-tag suffixes at delta boundaries (``_buf``)
    - Emits non-reasoning content as soon as it's verified safe
    - Flushes any held-back non-tag text at end-of-stream

    Usage::

        scrubber = StreamingThinkScrubber()
        for delta in stream:
            visible = scrubber.feed(delta)
            if visible:
                emit(visible)
        tail = scrubber.flush()
        if tail:
            emit(tail)

    Call ``reset()`` at the top of each new turn.
    """

    def __init__(self, config: ThinkScrubberConfig | None = None) -> None:
        self.config = config or ThinkScrubberConfig()
        self._open_tags = self.config.open_tags
        self._close_tags = self.config.close_tags
        self._max_tag_len = self.config.max_tag_len

        self._in_block: bool = False
        self._buf: str = ""
        self._last_emitted_ended_newline: bool = True

    def reset(self) -> None:
        """Reset all state. Call at the top of every new turn."""
        self._in_block = False
        self._buf = ""
        self._last_emitted_ended_newline = True

    @property
    def in_block(self) -> bool:
        """True if currently inside a reasoning block."""
        return self._in_block

    def feed(self, text: str) -> str:
        """Feed one delta; return the scrubbed visible portion.

        May return an empty string when the entire delta is reasoning
        content or is being held back pending resolution of a partial
        tag at the boundary.
        """
        if not text:
            return ""
        buf = self._buf + text
        self._buf = ""
        out: List[str] = []

        while buf:
            if self._in_block:
                # Inside a block — hunt for the earliest close tag
                close_idx, close_len = self._find_first_tag(buf, self._close_tags)
                if close_idx == -1:
                    # No close tag yet — hold back a potential partial
                    # close-tag suffix; discard everything else
                    held = self._max_partial_suffix(buf, self._close_tags)
                    self._buf = buf[-held:] if held else ""
                    return "".join(out)
                # Found close: discard block content + tag, exit block
                buf = buf[close_idx + close_len:]
                self._in_block = False
            else:
                # Outside a block — look for tags
                # Priority 1: closed pair <tag>X</tag> anywhere
                pair = self._find_earliest_closed_pair(buf)

                # Priority 2: unterminated open tag at a block boundary
                open_idx, open_len = self._find_open_at_boundary(buf, out)

                # Pick whichever match comes earliest
                if pair is not None and (open_idx == -1 or pair[0] <= open_idx):
                    start_idx, end_idx = pair
                    preceding = buf[:start_idx]
                    if preceding:
                        preceding = self._strip_orphan_close_tags(preceding)
                        if preceding:
                            out.append(preceding)
                            self._last_emitted_ended_newline = preceding.endswith("\n")
                    buf = buf[end_idx:]
                    continue

                if open_idx != -1:
                    # Unterminated open at boundary — emit preceding, enter block
                    preceding = buf[:open_idx]
                    if preceding:
                        preceding = self._strip_orphan_close_tags(preceding)
                        if preceding:
                            out.append(preceding)
                            self._last_emitted_ended_newline = preceding.endswith("\n")
                    self._in_block = True
                    buf = buf[open_idx + open_len:]
                    continue

                # No resolvable tag structure — hold back partial suffix
                held = self._max_partial_suffix(buf, self._open_tags)
                held_close = self._max_partial_suffix(buf, self._close_tags)
                held = max(held, held_close)
                if held:
                    emit_text = buf[:-held]
                    self._buf = buf[-held:]
                else:
                    emit_text = buf
                    self._buf = ""
                if emit_text:
                    emit_text = self._strip_orphan_close_tags(emit_text)
                    if emit_text:
                        out.append(emit_text)
                        self._last_emitted_ended_newline = emit_text.endswith("\n")
                return "".join(out)

        return "".join(out)

    def flush(self) -> str:
        """End-of-stream flush.

        If still inside an unterminated block, held-back content is
        discarded (leaking partial reasoning is worse than truncated output).
        Otherwise, held-back partial-tag tail is emitted verbatim
        (it turned out not to be a real tag prefix).
        """
        if self._in_block:
            self._buf = ""
            self._in_block = False
            return ""
        tail = self._buf
        self._buf = ""
        if not tail:
            return ""
        tail = self._strip_orphan_close_tags(tail)
        if tail:
            self._last_emitted_ended_newline = tail.endswith("\n")
        return tail

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _find_first_tag(buf: str, tags: Tuple[str, ...]) -> Tuple[int, int]:
        """Return (earliest_index, tag_length) over *tags*, or (-1, 0).

        Case-insensitive match.
        """
        buf_lower = buf.lower()
        best_idx = -1
        best_len = 0
        for tag in tags:
            idx = buf_lower.find(tag.lower())
            if idx != -1 and (best_idx == -1 or idx < best_idx):
                best_idx = idx
                best_len = len(tag)
        return best_idx, best_len

    def _find_earliest_closed_pair(self, buf: str) -> Optional[Tuple[int, int]]:
        """Return (start_idx, end_idx) of the earliest closed pair, or None.

        A closed pair is ``<tag>...</tag>`` with non-greedy matching:
        the closest close tag after an open tag wins. Case-insensitive.
        """
        buf_lower = buf.lower()
        best: Optional[Tuple[int, int]] = None
        for open_tag, close_tag in zip(self._open_tags, self._close_tags):
            open_lower = open_tag.lower()
            close_lower = close_tag.lower()
            open_idx = buf_lower.find(open_lower)
            if open_idx == -1:
                continue
            close_idx = buf_lower.find(close_lower, open_idx + len(open_lower))
            if close_idx == -1:
                continue
            end_idx = close_idx + len(close_lower)
            if best is None or open_idx < best[0]:
                best = (open_idx, end_idx)
        return best

    def _find_open_at_boundary(
        self, buf: str, already_emitted: List[str],
    ) -> Tuple[int, int]:
        """Return the earliest block-boundary open-tag (idx, len), or (-1, 0)."""
        if not self.config.boundary_gating:
            return self._find_first_tag(buf, self._open_tags)

        buf_lower = buf.lower()
        best_idx = -1
        best_len = 0
        for tag in self._open_tags:
            tag_lower = tag.lower()
            search_start = 0
            while True:
                idx = buf_lower.find(tag_lower, search_start)
                if idx == -1:
                    break
                if self._is_block_boundary(buf, idx, already_emitted):
                    if best_idx == -1 or idx < best_idx:
                        best_idx = idx
                        best_len = len(tag)
                    break
                search_start = idx + 1
        return best_idx, best_len

    def _is_block_boundary(
        self, buf: str, idx: int, already_emitted: List[str],
    ) -> bool:
        """True iff position *idx* in *buf* is a block boundary.

        A block boundary is:
            - buf position 0 AND the most recent emission ended with
              a newline (or nothing has been emitted yet)
            - any position whose preceding text on the current line
              (since the last newline in buf) is whitespace-only, AND
              if there is no newline in the preceding buf portion, the
              most recent prior emission ended with a newline.
        """
        if idx == 0:
            if already_emitted:
                return already_emitted[-1].endswith("\n")
            return self._last_emitted_ended_newline
        preceding = buf[:idx]
        last_nl = preceding.rfind("\n")
        if last_nl == -1:
            # No newline in buf before the tag — boundary only if the
            # prior emission ended with a newline AND everything since
            # is whitespace.
            if already_emitted:
                prior_newline = already_emitted[-1].endswith("\n")
            else:
                prior_newline = self._last_emitted_ended_newline
            return prior_newline and preceding.strip() == ""
        # Newline present — text between it and the tag must be whitespace-only
        return preceding[last_nl + 1:].strip() == ""

    @staticmethod
    def _max_partial_suffix(buf: str, tags: Tuple[str, ...]) -> int:
        """Return the longest buf-suffix that is a prefix of any tag.

        Only prefixes strictly shorter than the tag itself count
        (full-length suffixes are the tag itself, handled as matches).
        Case-insensitive.
        """
        if not buf:
            return 0
        buf_lower = buf.lower()
        max_check = min(len(buf_lower), _MAX_TAG_LEN - 1)
        for i in range(max_check, 0, -1):
            suffix = buf_lower[-i:]
            for tag in tags:
                tag_lower = tag.lower()
                if len(tag_lower) > i and tag_lower.startswith(suffix):
                    return i
        return 0

    @staticmethod
    def _strip_orphan_close_tags(text: str) -> str:
        """Remove any close tags from *text* (orphan-close handling).

        An orphan close tag has no matching open tag; it's always noise.
        Strips the tag and any trailing whitespace so surrounding prose
        flows naturally.
        """
        if "</" not in text:
            return text
        text_lower = text.lower()
        out: List[str] = []
        i = 0
        while i < len(text):
            matched = False
            if text_lower[i:i + 2] == "</":
                for tag in _CLOSE_TAGS:
                    tag_lower = tag.lower()
                    tag_len = len(tag_lower)
                    if i + tag_len <= len(text) and text_lower[i:i + tag_len] == tag_lower:
                        # Skip the tag and trailing whitespace
                        j = i + tag_len
                        while j < len(text) and text[j] in " \t\n\r":
                            j += 1
                        i = j
                        matched = True
                        break
            if not matched:
                out.append(text[i])
                i += 1
        return "".join(out)


# ────────────────────────────────────────────────────────────────────
# Convenience: apply to a ProviderResult
# ────────────────────────────────────────────────────────────────────

def scrub_provider_result(content: str, *, metadata: Optional[Dict] = None) -> Tuple[str, Optional[Dict]]:
    """Scrub think blocks from a provider response, optionally preserving
    the reasoning content as metadata.

    Args:
        content: Raw response content from the provider.
        metadata: Optional dict to store reasoning content in (preserve mode).

    Returns:
        ``(cleaned_content, updated_metadata)``.
    """
    if "<think" not in content.lower() and "<reasoning" not in content.lower():
        return content, metadata

    # Extract reasoning blocks for metadata
    if metadata is not None:
        reasoning_parts = []
        # Collect content from all think blocks
        pattern = _build_strip_regex()
        for match in pattern.finditer(content):
            matched_text = match.group(0)
            if "<" in matched_text:
                reasoning_parts.append(matched_text)
        if reasoning_parts:
            existing = metadata.get("reasoning", "")
            metadata["reasoning"] = (
                existing + "\n\n" + "\n".join(reasoning_parts)
                if existing
                else "\n".join(reasoning_parts)
            )

    return strip_think_blocks(content), metadata
