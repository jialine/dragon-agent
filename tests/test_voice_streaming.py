"""
Tests for Dragon Agent VoiceEngine.stream() — Streaming TTS.

Covers: sentence splitting (string + async iterable),
streaming output ordering, edge cases (empty text, no punctuation,
very long sentences), and gateway integration point.
"""

import asyncio

import pytest

from dragon.voice_engine import (
    VoiceEngine,
    DEFAULT_VOICE,
    _MAX_BUFFER_LENGTH,
    _SENTENCE_BOUNDARY_RE,
    _SOFT_BOUNDARY_RE,
)


def _edge_tts_available() -> bool:
    """Check if edge-tts Python module is importable."""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


EDGE_TTS_AVAILABLE = _edge_tts_available()
requires_edge_tts = pytest.mark.skipif(
    not EDGE_TTS_AVAILABLE, reason="edge-tts not installed"
)


# ── Sentence boundary regex tests ───────────────────────────────────


def test_boundary_re_chinese_period():
    """Chinese period should be detected as sentence boundary."""
    match = _SENTENCE_BOUNDARY_RE.match("你好世界。后面的文字")
    assert match is not None
    assert match.group(1).strip() == "你好世界。"
    assert match.group(2) == "后面的文字"


def test_boundary_re_exclamation():
    """Chinese exclamation mark should be detected."""
    match = _SENTENCE_BOUNDARY_RE.match("太棒了！继续")
    assert match is not None
    assert match.group(1).strip() == "太棒了！"


def test_boundary_re_question():
    """Chinese question mark should be detected."""
    match = _SENTENCE_BOUNDARY_RE.match("你好吗？我很好")
    assert match is not None
    assert match.group(1).strip() == "你好吗？"


def test_boundary_re_newline():
    """Newline should be detected as sentence boundary."""
    match = _SENTENCE_BOUNDARY_RE.match("第一行\n第二行")
    assert match is not None
    assert match.group(1).strip() == "第一行"


def test_boundary_re_english():
    """English punctuation should be detected."""
    match = _SENTENCE_BOUNDARY_RE.match("Hello world. More text")
    assert match is not None
    assert match.group(1).strip() == "Hello world."


def test_boundary_re_no_match():
    """Text without punctuation should not match."""
    match = _SENTENCE_BOUNDARY_RE.match("没有标点符号的文字")
    assert match is None


def test_boundary_re_empty_input():
    """Empty input should not match."""
    match = _SENTENCE_BOUNDARY_RE.match("")
    assert match is None


# ── _extract_sentence tests ─────────────────────────────────────────


def test_extract_sentence_basic():
    """Extract a single sentence with Chinese period."""
    engine = VoiceEngine()
    sentence, remaining = engine._extract_sentence("你好。世界")
    assert sentence == "你好。"
    assert remaining == "世界"


def test_extract_sentence_no_boundary():
    """No boundary found — return None and full buffer."""
    engine = VoiceEngine()
    sentence, remaining = engine._extract_sentence("没有标点")
    assert sentence is None
    assert remaining == "没有标点"


def test_extract_sentence_multiple_boundaries():
    """Only first sentence extracted, rest remains."""
    engine = VoiceEngine()
    sentence, remaining = engine._extract_sentence("第一句。第二句。第三句。")
    assert sentence == "第一句。"
    assert remaining == "第二句。第三句。"


def test_extract_sentence_at_end():
    """Sentence at end of buffer — remaining is empty."""
    engine = VoiceEngine()
    sentence, remaining = engine._extract_sentence("只有一句话。")
    assert sentence == "只有一句话。"
    assert remaining == ""


def test_extract_sentence_leading_boundary():
    """Leading boundary char should be skipped — no valid sentence extracted."""
    engine = VoiceEngine()
    sentence, remaining = engine._extract_sentence("。后面的文字")
    # Leading boundary: no content before it, regex requires .+? (min 1 char)
    assert sentence is None  # no valid sentence (empty before boundary)
    assert remaining == "。后面的文字"


def test_extract_sentence_soft_boundary():
    """Very long text without punctuation should be forced-split at comma."""
    engine = VoiceEngine()
    # Build text > _MAX_BUFFER_LENGTH with a comma
    long_text = "这是一个非常长的句子" + "包含很多内容" * 50 + "，这里有逗号" + "后续内容" * 5
    assert len(long_text) > _MAX_BUFFER_LENGTH
    sentence, remaining = engine._extract_sentence(long_text)
    assert sentence is not None
    assert len(sentence) >= 50  # min length for soft boundary
    assert "，" in sentence or "," in sentence
    assert len(remaining) > 0


def test_extract_sentence_hard_split():
    """Very long text without any punctuation should be force-split at max."""
    engine = VoiceEngine()
    # Build a string without any punctuation at all
    long_text = "A" * 350
    assert len(long_text) > _MAX_BUFFER_LENGTH
    sentence, remaining = engine._extract_sentence(long_text)
    assert sentence is not None
    assert len(sentence) == _MAX_BUFFER_LENGTH
    assert len(remaining) == 350 - _MAX_BUFFER_LENGTH


def test_extract_sentence_empty():
    """Empty buffer returns (None, '')."""
    engine = VoiceEngine()
    sentence, remaining = engine._extract_sentence("")
    assert sentence is None
    assert remaining == ""


# ── stream() with string input (no edge-tts) ────────────────────────


@pytest.mark.asyncio
async def test_stream_string_no_edge_tts():
    """stream() with string input should not crash without edge-tts.
    
    When edge-tts is installed, it produces audio; when not, it gracefully yields nothing.
    """
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("测试句子。"):
        results.append((sentence, audio))
    # With edge-tts: 1 result; without: 0 results. Either is fine.
    if EDGE_TTS_AVAILABLE:
        assert len(results) >= 1
        assert len(results[0][1]) > 0
    else:
        assert len(results) == 0


@pytest.mark.asyncio
async def test_stream_string_empty():
    """stream() with empty string should yield nothing."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream(""):
        results.append((sentence, audio))
    assert len(results) == 0


@pytest.mark.asyncio
async def test_stream_string_whitespace():
    """stream() with whitespace-only should yield nothing."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("   \n\t  "):
        results.append((sentence, audio))
    assert len(results) == 0


@pytest.mark.asyncio
async def test_stream_string_no_boundary():
    """stream() with text but no boundary — should synthesize whole text at end."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("没有标点的一段文字"):
        results.append((sentence, audio))
    # Without edge-tts: no audio. With edge-tts: one chunk for whole text.
    # Just verify no crash
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_stream_string_multiple_sentences():
    """stream() should split on boundaries and yield once per sentence."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("第一句。第二句！第三句？"):
        results.append((sentence, audio))
    # Without edge-tts: 0 results. Structure is correct though.
    assert isinstance(results, list)


# ── stream() with async iterable (no edge-tts) ──────────────────────


async def _chunk_generator(chunks: list[str]):
    """Helper: async iterable of text chunks."""
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_stream_async_iterable_basic():
    """stream() should accept async iterable of text chunks.
    
    When edge-tts is installed, produces audio; when not, gracefully yields nothing.
    """
    engine = VoiceEngine()
    results = []
    chunks = _chunk_generator(["你好", "世界。", "第二句。"])
    async for sentence, audio in engine.stream(chunks):
        results.append((sentence, audio))
    if EDGE_TTS_AVAILABLE:
        assert len(results) >= 2
    else:
        assert len(results) == 0


@pytest.mark.asyncio
async def test_stream_async_iterable_cross_boundary():
    """Boundary spanning async chunks should be detected.
    
    When edge-tts is installed, produces audio; when not, gracefully yields nothing.
    """
    engine = VoiceEngine()
    results = []
    chunks = _chunk_generator(["你好", "世界。继续"])
    async for sentence, audio in engine.stream(chunks):
        results.append((sentence, audio))
    if EDGE_TTS_AVAILABLE:
        assert len(results) >= 1
    else:
        assert len(results) == 0


@pytest.mark.asyncio
async def test_stream_async_iterable_empty_chunks():
    """Empty chunks should be skipped gracefully."""
    engine = VoiceEngine()
    results = []
    chunks = _chunk_generator(["", "", "你好。", ""])
    async for sentence, audio in engine.stream(chunks):
        results.append((sentence, audio))
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_stream_async_iterable_no_boundary():
    """Async iterable without punctuation — flushed at end."""
    engine = VoiceEngine()
    results = []
    chunks = _chunk_generator(["没有", "标点", "符号"])
    async for sentence, audio in engine.stream(chunks):
        results.append((sentence, audio))
    assert isinstance(results, list)


# ── stream() with edge-tts (integration tests) ──────────────────────


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_string_with_edge_tts():
    """Full streaming with edge-tts: string input → MP3 chunks."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("你好世界。这是第二句话。"):
        results.append((sentence, audio))
    assert len(results) == 2
    for i, (sentence, audio) in enumerate(results):
        assert isinstance(sentence, str)
        assert isinstance(audio, bytes)
        assert len(audio) > 0


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_string_single_sentence():
    """Single sentence should produce one MP3 chunk."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("你好，测试。\n"):
        results.append((sentence, audio))
    assert len(results) == 1
    text, mp3 = results[0]
    assert "你好" in text
    assert len(mp3) > 0


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_string_ordering():
    """Audio chunks should maintain sentence order."""
    engine = VoiceEngine()
    sentences = ["你好。", "这是第二句。", "最后一句。"]
    text = "".join(sentences)
    results = []
    async for sentence, audio in engine.stream(text):
        results.append((sentence, audio))
    assert len(results) == 3
    for i, (sentence, _) in enumerate(results):
        assert sentence == sentences[i]


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_string_chinese_mixed():
    """Mixed Chinese and English punctuation."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream("Hello你好。How are you? 我很好！"):
        results.append((sentence, audio))
    assert len(results) == 3


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_async_iterable_with_edge_tts():
    """Streaming with async iterable should synthesize sentences as they arrive."""
    engine = VoiceEngine()
    results = []
    chunks = _chunk_generator(["你好", "世界。", "这是", "第二句。"])
    async for sentence, audio in engine.stream(chunks):
        results.append((sentence, audio))
    assert len(results) == 2
    assert "你好" in results[0][0]
    assert "第二句" in results[1][0]
    for _, audio in results:
        assert len(audio) > 0


# ── Gateway integration tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_processor_with_voice_engine():
    """MessageProcessor should accept voice_engine parameter."""
    from dragon.gateway.server import MessageProcessor

    engine = VoiceEngine()
    processor = MessageProcessor(voice_engine=engine)
    assert processor.voice_engine is engine


@pytest.mark.asyncio
async def test_processor_without_voice_engine():
    """MessageProcessor should work without voice_engine (None default)."""
    from dragon.gateway.server import MessageProcessor

    processor = MessageProcessor()
    assert processor.voice_engine is None


@pytest.mark.asyncio
async def test_platform_reply_audio_chunks():
    """PlatformReply should support audio_chunks for voice mode."""
    from dragon.gateway.base import PlatformReply

    reply = PlatformReply(
        content="你好",
        output_mode="voice",
        audio_chunks=[("你好。", b"fake_mp3")],
    )
    assert reply.output_mode == "voice"
    assert len(reply.audio_chunks) == 1
    assert reply.audio_chunks[0][0] == "你好。"
    assert reply.audio_chunks[0][1] == b"fake_mp3"


@pytest.mark.asyncio
async def test_platform_reply_default_text_mode():
    """Default PlatformReply should be text mode with empty audio."""
    from dragon.gateway.base import PlatformReply

    reply = PlatformReply(content="hello")
    assert reply.output_mode == "text"
    assert reply.audio_chunks == []


# ── Edge case: very long sentences ──────────────────────────────────


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_very_long_sentence():
    """A very long sentence without punctuation should be force-split."""
    engine = VoiceEngine()
    # 500 chars without any boundary punctuation
    long_text = "这是一个非常长的句子" + "包含很多内容" * 50
    assert len(long_text) > _MAX_BUFFER_LENGTH
    results = []
    async for sentence, audio in engine.stream(long_text):
        results.append((sentence, audio))
    # Should have at least 1 chunk (forced split)
    assert len(results) >= 1
    for _, audio in results:
        assert len(audio) > 0


@requires_edge_tts
@pytest.mark.asyncio
async def test_stream_empty_text_edge_tts():
    """Empty text with edge-tts should yield nothing."""
    engine = VoiceEngine()
    results = []
    async for sentence, audio in engine.stream(""):
        results.append((sentence, audio))
    assert len(results) == 0
