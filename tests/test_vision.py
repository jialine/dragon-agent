"""
Unit tests for Dragon Vision tools (vision_analyze, vision_info, ocr).
"""
import json
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────

def _make_test_image(path: Path, size=(100, 50), color="red", fmt="PNG"):
    """Create a test image file using Pillow."""
    from PIL import Image
    img = Image.new("RGB", size, color=color)
    img.save(path, format=fmt)
    return path


# ── SUPPORTED_FORMATS ──────────────────────────────────────────────────

def test_supported_formats():
    from dragon.tool.builtins.vision import SUPPORTED_FORMATS
    assert ".png" in SUPPORTED_FORMATS
    assert ".jpg" in SUPPORTED_FORMATS
    assert ".jpeg" in SUPPORTED_FORMATS
    assert ".gif" in SUPPORTED_FORMATS
    assert ".webp" in SUPPORTED_FORMATS
    assert ".bmp" in SUPPORTED_FORMATS


# ── tool_vision_info ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vision_info_local(tmp_path):
    from dragon.tool.builtins.vision import tool_vision_info
    img_path = tmp_path / "test.png"
    _make_test_image(img_path, size=(100, 50), color="red")

    result = json.loads(await tool_vision_info(str(img_path)))
    assert result["format"] == "PNG"
    assert result["width"] == 100
    assert result["height"] == 50
    assert result["size_bytes"] > 0


@pytest.mark.asyncio
async def test_vision_info_nonexistent():
    from dragon.tool.builtins.vision import tool_vision_info
    result = json.loads(await tool_vision_info("/nonexistent/image.jpg"))
    assert "error" in result


@pytest.mark.asyncio
async def test_vision_info_not_an_image(tmp_path):
    from dragon.tool.builtins.vision import tool_vision_info
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("hello world")

    result = json.loads(await tool_vision_info(str(txt_path)))
    # Should still return file info even if not an image
    assert "size_bytes" in result


# ── tool_vision_analyze ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vision_analyze_nonexistent():
    from dragon.tool.builtins.vision import tool_vision_analyze
    result = json.loads(await tool_vision_analyze("/nonexistent/image.jpg"))
    assert "error" in result


@pytest.mark.asyncio
async def test_vision_analyze_local_fallback(tmp_path):
    """Without vision API, should fall back to basic image info."""
    from dragon.tool.builtins.vision import tool_vision_analyze
    img_path = tmp_path / "test.png"
    _make_test_image(img_path, size=(50, 50), color="blue")

    result = json.loads(await tool_vision_analyze(str(img_path), "What color?"))
    # With no vision API, we get basic info + fallback note
    assert "format" in result
    assert result["format"] == "PNG"
    assert result["width"] == 50
    assert result["height"] == 50
    assert "fallback" in result


@pytest.mark.asyncio
async def test_vision_analyze_invalid_extension(tmp_path):
    from dragon.tool.builtins.vision import tool_vision_analyze
    txt_path = tmp_path / "notes.txt"
    txt_path.write_text("not an image")

    result = json.loads(await tool_vision_analyze(str(txt_path)))
    assert "error" in result


# ── tool_ocr ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ocr_no_tesseract():
    """When tesseract is not installed, should return an informative message."""
    from dragon.tool.builtins.vision import tool_ocr

    result = json.loads(await tool_ocr("/fake/path.png"))
    # Should either return error or install hint
    assert "error" in result or "install" in str(result).lower()


@pytest.mark.asyncio
async def test_ocr_nonexistent_file():
    from dragon.tool.builtins.vision import tool_ocr

    result = json.loads(await tool_ocr("/nonexistent/img.png"))
    assert "error" in result
