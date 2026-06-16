"""
Tests for Dragon Agent TTS (Text-to-Speech) tools.

Tests cover: tool_tts, tool_tts_voices, edge cases, and registry integration.
"""
import asyncio
import json
import os
import tempfile
import sys
from pathlib import Path

import pytest


# ── Helpers ──────────────────────────────────────────────────────────

def _sync_call(registry, tool_name, args, timeout=None):
    """Synchronous wrapper for registry.call()."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            registry.call(tool_name, args, timeout_secs=timeout)
        )
    finally:
        loop.close()


def _edge_tts_available():
    """Check if edge-tts is installed and functional."""
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


def test_voices_constant():
    """VOICES list should contain Chinese neural voices."""
    from dragon.tool.builtins.tts import VOICES, DEFAULT_VOICE

    assert len(VOICES) >= 4
    assert DEFAULT_VOICE in VOICES
    for voice in VOICES:
        assert "Neural" in voice


def test_max_text_length_constant():
    """MAX_TEXT_LENGTH should be a reasonable positive integer."""
    from dragon.tool.builtins.tts import MAX_TEXT_LENGTH

    assert isinstance(MAX_TEXT_LENGTH, int)
    assert 1000 <= MAX_TEXT_LENGTH <= 10000


# ── tool_tts_voices tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_voices_returns_valid_json():
    """Voices tool should return JSON with voices list."""
    from dragon.tool.builtins.tts import tool_tts_voices

    result = json.loads(await tool_tts_voices())
    assert "voices" in result
    assert isinstance(result["voices"], list)
    assert len(result["voices"]) >= 4
    assert "default_voice" in result
    assert "total" in result
    assert result["total"] == len(result["voices"])


@pytest.mark.asyncio
async def test_tts_voices_always_returns_json():
    """tts_voices must always return valid JSON (no deps needed)."""
    from dragon.tool.builtins.tts import tool_tts_voices

    result_str = await tool_tts_voices()
    data = json.loads(result_str)
    assert isinstance(data, dict)
    assert "error" not in data


# ── tool_tts tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_empty_text_error():
    """Empty text should return an error."""
    from dragon.tool.builtins.tts import tool_tts

    result = json.loads(await tool_tts(""))
    assert "error" in result


@pytest.mark.asyncio
async def test_tts_whitespace_text_error():
    """Whitespace-only text should return an error."""
    from dragon.tool.builtins.tts import tool_tts

    result = json.loads(await tool_tts("   \n\t  "))
    assert "error" in result


@pytest.mark.asyncio
async def test_tts_always_returns_valid_json():
    """All code paths must return parseable JSON."""
    from dragon.tool.builtins.tts import tool_tts

    for text in ["hello", "", "测" * 6000]:
        result_str = await tool_tts(text, output_path="/tmp/dragon_test_always_json.mp3")
        data = json.loads(result_str)
        assert isinstance(data, dict)


@requires_edge_tts
@pytest.mark.asyncio
async def test_tts_saves_file():
    """Convert text to speech and verify MP3 file is created."""
    from dragon.tool.builtins.tts import tool_tts

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_output.mp3")
        result = json.loads(await tool_tts(
            text="你好，这是一个测试。",
            output_path=output_path,
        ))

        assert "path" in result
        assert result["path"] == output_path
        assert "duration_seconds" in result
        assert "voice" in result
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0


@requires_edge_tts
@pytest.mark.asyncio
async def test_tts_with_custom_voice():
    """Specify a custom Chinese voice for TTS."""
    from dragon.tool.builtins.tts import tool_tts

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_voice.mp3")
        result = json.loads(await tool_tts(
            text="你好世界",
            voice="zh-CN-YunxiNeural",
            output_path=output_path,
        ))

        assert result["voice"] == "zh-CN-YunxiNeural"
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0


@requires_edge_tts
@pytest.mark.asyncio
async def test_tts_creates_parent_directory():
    """TTS should create parent directories for the output path."""
    from dragon.tool.builtins.tts import tool_tts

    with tempfile.TemporaryDirectory() as tmpdir:
        nested_path = os.path.join(tmpdir, "deep", "nested", "dir", "output.mp3")
        result = json.loads(await tool_tts(
            text="Short test.",
            output_path=nested_path,
        ))

        assert os.path.exists(nested_path)
        assert os.path.getsize(nested_path) > 0


@requires_edge_tts
@pytest.mark.asyncio
async def test_tts_auto_generated_output_path():
    """When no output_path is given, auto-generate under ~/.dragon/tts/."""
    from dragon.tool.builtins.tts import tool_tts

    result = json.loads(await tool_tts(text="Auto-generated path test."))
    assert "path" in result
    assert ".dragon" in result["path"] or "tts" in result["path"]

    # Cleanup
    output_file = Path(result["path"])
    if output_file.exists():
        output_file.unlink()


@requires_edge_tts
@pytest.mark.asyncio
async def test_tts_long_text_truncation():
    """Text over MAX_TEXT_LENGTH should be automatically truncated."""
    from dragon.tool.builtins.tts import tool_tts, MAX_TEXT_LENGTH

    long_text = "测试 " * 3000
    assert len(long_text) > MAX_TEXT_LENGTH

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_long.mp3")
        result = json.loads(await tool_tts(
            text=long_text,
            output_path=output_path,
        ))

        assert "error" not in result
        assert result["text_length"] <= MAX_TEXT_LENGTH
        assert os.path.exists(output_path)


# ── Registry integration tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_registered():
    """Verify tts tool is properly registered in ToolRegistry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    tool = registry.get("tts")
    assert tool is not None
    assert tool.name == "tts"
    assert tool.category == "media"
    assert "tts" in tool.tags
    assert "audio" in tool.tags


@pytest.mark.asyncio
async def test_tts_voices_registered():
    """Verify tts_voices tool is properly registered."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    tool = registry.get("tts_voices")
    assert tool is not None
    assert tool.name == "tts_voices"
    assert tool.category == "media"
    assert "tts" in tool.tags


@pytest.mark.asyncio
async def test_tts_tools_in_search():
    """TTS tools should appear in registry keyword search."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    results = registry.search("tts")
    names = [r["name"] for r in results]
    assert "tts" in names
    assert "tts_voices" in names

    # Also search by category
    results = registry.search("audio")
    names = [r["name"] for r in results]
    assert "tts" in names


@requires_edge_tts
@pytest.mark.asyncio
async def test_tts_via_registry():
    """End-to-end: call tts through the registry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "registry_test.mp3")
        result = await registry.call("tts", {
            "text": "Registry integration test via Dragon Agent.",
            "output_path": output_path,
        })

        assert result.success is True
        assert os.path.exists(output_path)
        data = json.loads(result.output)
        assert data["path"] == output_path


@pytest.mark.asyncio
async def test_tts_voices_via_registry():
    """End-to-end: call tts_voices through the registry."""
    from dragon.tool.registry import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    result = await registry.call("tts_voices", {})
    assert result.success is True
    data = json.loads(result.output)
    assert "voices" in data


# ── Error handling / edge cases ─────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_unknown_voice():
    """Unknown voice should still produce valid JSON (edge-tts may error)."""
    from dragon.tool.builtins.tts import tool_tts

    result = json.loads(await tool_tts(
        "test",
        voice="nonexistent-voice-xyz",
        output_path="/tmp/dragon_test_unknown_voice.mp3",
    ))
    assert isinstance(result, dict)
    assert "path" in result or "error" in result


@pytest.mark.asyncio
async def test_tts_special_characters():
    """Text with special characters should not crash."""
    from dragon.tool.builtins.tts import tool_tts

    result = json.loads(await tool_tts(
        '你好 "世界"! 测试\'s & special <chars>\\n换行',
        output_path="/tmp/dragon_test_special.mp3",
    ))
    assert isinstance(result, dict)
    assert "path" in result or "error" in result


@pytest.mark.asyncio
async def test_tts_error_when_edge_tts_missing(monkeypatch):
    """Simulate edge-tts not installed — verify graceful error JSON."""
    from dragon.tool.builtins.tts import tool_tts
    import dragon.tool.builtins.tts as tts_mod

    # Mock _check_edge_tts to return False
    monkeypatch.setattr(tts_mod, "_check_edge_tts", lambda: False)
    monkeypatch.setattr(tts_mod, "_check_edge_tts_module", lambda: False)

    result = json.loads(await tool_tts(text="test"))
    assert "error" in result
    assert "edge-tts" in result["error"].lower()
