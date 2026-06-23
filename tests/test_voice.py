"""
Tests for Dragon Agent VoiceEngine (Streaming TTS).

Tests cover: boundary detection, consume flow, empty input,
graceful degradation without edge-tts, and English boundary handling.
"""

import asyncio

import pytest

from dragon.voice_engine import VoiceEngine, DEFAULT_VOICE


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


# ── Module-level constant tests ─────────────────────────────────────


def test_default_voice_is_chinese():
    """Default voice should be a Chinese neural voice."""
    assert "zh-CN" in DEFAULT_VOICE
    assert "Neural" in DEFAULT_VOICE


def test_voice_engine_instantiation():
    """VoiceEngine should instantiate with default values."""
    engine = VoiceEngine()
    assert engine.voice == DEFAULT_VOICE
    assert engine.speed == 1.0
    assert engine.buffer == ""
    assert engine._running is False
    assert engine._task is None


def test_voice_engine_custom_voice():
    """VoiceEngine should accept custom voice parameter."""
    engine = VoiceEngine(voice="zh-CN-YunxiNeural")
    assert engine.voice == "zh-CN-YunxiNeural"


# ── Boundary detection tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_boundary_detection_chinese():
    """Detect Chinese sentence boundary (。)."""
    engine = VoiceEngine()
    engine.buffer = "你好世界。后面的文字"
    result = engine._detect_boundary(engine.buffer)
    assert result == "你好世界。"
    assert engine.buffer == "后面的文字"


@pytest.mark.asyncio
async def test_boundary_detection_chinese_exclamation():
    """Detect Chinese sentence boundary (！)."""
    engine = VoiceEngine()
    engine.buffer = "太棒了！继续加油"
    result = engine._detect_boundary(engine.buffer)
    assert result == "太棒了！"
    assert engine.buffer == "继续加油"


@pytest.mark.asyncio
async def test_boundary_detection_chinese_question():
    """Detect Chinese sentence boundary (？)."""
    engine = VoiceEngine()
    engine.buffer = "你好吗？我很好"
    result = engine._detect_boundary(engine.buffer)
    assert result == "你好吗？"
    assert engine.buffer == "我很好"


@pytest.mark.asyncio
async def test_no_boundary():
    """Text without sentence-ending punctuation should return None."""
    engine = VoiceEngine()
    engine.buffer = "没有标点符号的文字"
    result = engine._detect_boundary(engine.buffer)
    assert result is None
    # Buffer should remain unchanged
    assert engine.buffer == "没有标点符号的文字"


@pytest.mark.asyncio
async def test_english_boundary():
    """Detect English sentence boundary (.)."""
    engine = VoiceEngine()
    engine.buffer = "Hello world. More text"
    result = engine._detect_boundary(engine.buffer)
    assert result == "Hello world."
    assert engine.buffer == "More text"


@pytest.mark.asyncio
async def test_newline_boundary():
    """Detect newline as sentence boundary."""
    engine = VoiceEngine()
    engine.buffer = "第一行\n第二行内容"
    result = engine._detect_boundary(engine.buffer)
    assert result == "第一行"
    assert engine.buffer == "第二行内容"


@pytest.mark.asyncio
async def test_multiple_sentences_in_buffer():
    """Only the first sentence should be extracted."""
    engine = VoiceEngine()
    engine.buffer = "第一句。第二句。第三句。"
    result = engine._detect_boundary(engine.buffer)
    assert result == "第一句。"
    assert engine.buffer == "第二句。第三句。"


@pytest.mark.asyncio
async def test_boundary_no_remaining():
    """When sentence is at end, buffer should be empty."""
    engine = VoiceEngine()
    engine.buffer = "只有一句话。"
    result = engine._detect_boundary(engine.buffer)
    assert result == "只有一句话。"
    assert engine.buffer == ""


# ── Consume flow tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consume_no_boundary():
    """Text without boundary should stay in buffer, no queue items."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("没有标点")
    assert engine.buffer == "没有标点"
    assert engine.sentence_queue.empty()
    await engine.stop()


@pytest.mark.asyncio
async def test_consume_boundary_immediate():
    """Text chunk with boundary should immediately enqueue."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("完整句子。")
    assert engine.buffer == ""
    assert not engine.sentence_queue.empty()
    sentence = engine.sentence_queue.get_nowait()
    assert sentence == "完整句子。"
    await engine.stop()


@pytest.mark.asyncio
async def test_consume_across_chunks():
    """Boundary spanning multiple chunks should be detected."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("你好")
    assert engine.buffer == "你好"
    assert engine.sentence_queue.empty()

    engine.consume("世界。")
    assert engine.buffer == ""
    assert not engine.sentence_queue.empty()
    sentence = engine.sentence_queue.get_nowait()
    assert sentence == "你好世界。"
    await engine.stop()


@pytest.mark.asyncio
async def test_consume_trailing_text_preserved():
    """Text after a boundary should stay in buffer for next sentence."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("第一句。残留文本")
    assert engine.buffer == "残留文本"
    assert not engine.sentence_queue.empty()
    sentence = engine.sentence_queue.get_nowait()
    assert sentence == "第一句。"

    engine.consume("变成第二句。")
    assert engine.buffer == ""
    sentence = engine.sentence_queue.get_nowait()
    assert sentence == "残留文本变成第二句。"
    await engine.stop()


# ── Full pipeline tests (requires edge-tts) ─────────────────────────


@requires_edge_tts
@pytest.mark.asyncio
async def test_consume_flow():
    """Full flow: feed chunks, flush, collect audio."""
    engine = VoiceEngine()
    await engine.start()

    # Feed text in chunks
    engine.consume("你好")
    engine.consume("世界。")
    engine.consume("这是第二句话。")

    await engine.flush()

    # Collect audio
    items = []
    while True:
        item = await engine.next_audio()
        if item is None:
            break
        text, audio = item
        items.append((text, audio))

    assert len(items) >= 2  # At least 2 sentences
    for text, audio in items:
        assert isinstance(text, str)
        assert isinstance(audio, bytes)
        assert len(audio) > 0

    await engine.stop()


@requires_edge_tts
@pytest.mark.asyncio
async def test_full_pipeline_single_sentence():
    """Single sentence should produce valid MP3 audio."""
    engine = VoiceEngine()
    await engine.start()

    engine.consume("你好，这是一个语音合成测试。")
    await engine.flush()

    item = await engine.next_audio()
    assert item is not None
    text, audio = item
    assert text == "你好，这是一个语音合成测试。"
    assert len(audio) > 0

    # Should signal end after the one item
    end_signal = await engine.next_audio()
    assert end_signal is None

    await engine.stop()


@requires_edge_tts
@pytest.mark.asyncio
async def test_multiple_sentences_ordered():
    """Audio items should maintain sentence order."""
    engine = VoiceEngine()
    await engine.start()

    sentences = ["你好。", "这是第二句。", "最后一句。"]
    for s in sentences:
        engine.consume(s)

    await engine.flush()

    items = []
    while True:
        item = await engine.next_audio()
        if item is None:
            break
        items.append(item)

    assert len(items) == 3
    for i, (text, audio) in enumerate(items):
        assert text == sentences[i]
        assert len(audio) > 0

    await engine.stop()


# ── Edge case tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_input():
    """No audio should be produced for empty input."""
    engine = VoiceEngine()
    await engine.start()
    await engine.flush()
    item = await engine.next_audio()
    assert item is None  # No audio for empty input
    await engine.stop()


@pytest.mark.asyncio
async def test_whitespace_only_input():
    """Whitespace-only input should not produce audio."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("   \n\t  ")
    await engine.flush()
    item = await engine.next_audio()
    assert item is None
    await engine.stop()


@pytest.mark.asyncio
async def test_edge_tts_not_installed():
    """VoiceEngine should work gracefully without edge-tts."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("测试。")
    await engine.flush()
    # Should not crash, may or may not produce audio
    while True:
        item = await engine.next_audio()
        if item is None:
            break
    await engine.stop()


@pytest.mark.asyncio
async def test_stop_during_operation():
    """Stop should cancel background task cleanly."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("测试内容。")
    await engine.stop()
    # Should not raise, engine should be stopped
    assert engine._running is False


@pytest.mark.asyncio
async def test_flush_with_no_start():
    """Flush without start should still work (self._task is None)."""
    engine = VoiceEngine()
    engine.consume("测试。")
    # Should not crash even though synthesis loop wasn't started
    await engine.flush()


@pytest.mark.asyncio
async def test_double_flush():
    """Calling flush twice should not cause errors."""
    engine = VoiceEngine()
    await engine.start()
    engine.consume("测试。")
    await engine.flush()
    # Second flush should be safe
    await engine.flush()
    await engine.stop()


@pytest.mark.asyncio
async def test_double_stop():
    """Calling stop twice should not cause errors."""
    engine = VoiceEngine()
    await engine.start()
    await engine.stop()
    await engine.stop()  # Should be safe


@pytest.mark.asyncio
async def test_consume_after_stop():
    """Consuming after stop should not crash."""
    engine = VoiceEngine()
    await engine.start()
    await engine.stop()
    engine.consume("测试。")
    # Should not raise — sentence goes into queue but loop stopped


@pytest.mark.asyncio
async def test_long_text_handling():
    """Long Chinese text should not crash the engine."""
    engine = VoiceEngine()
    await engine.start()
    long_text = "测试内容。" * 100
    engine.consume(long_text)
    await engine.flush()
    # Just verify no crash — queue items may or may not be consumed
    await engine.stop()
