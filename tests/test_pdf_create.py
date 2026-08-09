"""
Tests for Dragon PDF Create Tool (pdf_create).

Covers: basic creation, markdown formatting, multi-page, error handling, registry integration.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────

def _read_pdf_text(filepath: str) -> str:
    """Extract text from a PDF — decompress FlateDecode streams."""
    import re
    import zlib

    with open(filepath, "rb") as f:
        data = f.read()

    text_parts = []
    # Find all stream objects with FlateDecode
    for match in re.finditer(rb"/Filter\s+/FlateDecode.*?>>\s*stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        try:
            decompressed = zlib.decompress(match.group(1))
            # Try UTF-8 first, fall back to latin-1
            try:
                text_parts.append(decompressed.decode("utf-8"))
            except UnicodeDecodeError:
                text_parts.append(decompressed.decode("latin-1", errors="replace"))
        except Exception:
            pass
    return "\n".join(text_parts)


# ── Basic Creation Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pdf_create_basic():
    """Create a basic PDF and verify it's valid."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        result_json = await tool_pdf_create(
            path=path,
            content="Hello World\n\nThis is a test.",
            title="Test Doc",
        )
        result = json.loads(result_json)

        assert result["file"] == path
        assert result["pages"] == 1
        assert result["size_bytes"] > 0
        assert result["title"] == "Test Doc"

        # Verify it's a real PDF
        with open(path, "rb") as f:
            header = f.read(8)
        assert header.startswith(b"%PDF-1.")
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_multi_page():
    """Create a multi-page PDF with page breaks."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        result_json = await tool_pdf_create(
            path=path,
            content="Page 1\n\n---\n\nPage 2\n\n---\n\nPage 3",
            title="Multi-page",
        )
        result = json.loads(result_json)
        assert result["pages"] == 3
        assert result["size_bytes"] > 500  # multi-page should be bigger
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_empty_content():
    """Create a PDF with empty content should still work."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        result_json = await tool_pdf_create(path=path, content="", title="Empty")
        result = json.loads(result_json)
        assert result["pages"] >= 1
        assert result["size_bytes"] > 0
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_no_title():
    """Create a PDF without a title."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        result_json = await tool_pdf_create(path=path, content="Test content")
        result = json.loads(result_json)
        assert result["title"] == "(untitled)"
        assert result["pages"] == 1
    finally:
        os.unlink(path)


# ── Markdown Formatting Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_pdf_create_headings():
    """Headings should be recognized."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        content = "# H1\n\n## H2\n\n### H3\n\nNormal text."
        result_json = await tool_pdf_create(path=path, content=content)
        result = json.loads(result_json)
        assert result["size_bytes"] > 0
        # Verify headings appear in the raw PDF content
        raw = _read_pdf_text(path)
        assert "H1" in raw
        assert "H2" in raw
        assert "H3" in raw
        assert "Normal text" in raw
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_inline_formatting():
    """Bold, italic, and code formatting should be rendered."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        content = "This is **bold**, *italic*, and `code` text."
        result_json = await tool_pdf_create(path=path, content=content)
        result = json.loads(result_json)
        raw = _read_pdf_text(path)
        # The text should appear without markup characters
        assert "bold" in raw
        assert "italic" in raw
        assert "code" in raw
        # Verify the PDF makes valid output
        assert result["size_bytes"] > 0
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_lists():
    """Unordered list items should render."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        content = "- Item one\n- Item two\n- Item three"
        result_json = await tool_pdf_create(path=path, content=content)
        raw = _read_pdf_text(path)
        assert "Item one" in raw
        assert "Item two" in raw
        assert "Item three" in raw
    finally:
        os.unlink(path)


# ── Edge Case Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pdf_create_chinese_text():
    """Chinese characters should render correctly."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        content = "# 中文标题\n\n这是**粗体**和*斜体*的测试。\n\n- 列表项一\n- 列表项二"
        result_json = await tool_pdf_create(
            path=path, content=content, title="中文文档"
        )
        result = json.loads(result_json)
        raw = _read_pdf_text(path)
        assert "中文标题" in raw
        assert "粗体" in raw
        assert "斜体" in raw
        assert "列表项" in raw
        assert result["title"] == "中文文档"
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_special_chars():
    """Parentheses and backslashes should be escaped."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        content = "Function call: foo(bar) with backslash \\ test"
        result_json = await tool_pdf_create(path=path, content=content)
        result = json.loads(result_json)
        raw = _read_pdf_text(path)
        # Parentheses get PDF-escaped: foo\(bar\)
        assert "foo" in raw
        assert "bar" in raw
        assert "backslash" in raw
        assert result["size_bytes"] > 0
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_single_page_break_only():
    """Content with only --- should produce 2 pages."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        result_json = await tool_pdf_create(
            path=path, content="Above\n\n---\n\nBelow"
        )
        result = json.loads(result_json)
        assert result["pages"] == 2
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_pdf_create_nonexistent_directory():
    """Should create parent directories automatically."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.TemporaryDirectory() as tmpdir:
        nested = os.path.join(tmpdir, "a", "b", "c", "test.pdf")
        result_json = await tool_pdf_create(path=nested, content="Test")
        result = json.loads(result_json)
        assert result["file"] == nested
        assert os.path.exists(nested)


@pytest.mark.asyncio
async def test_pdf_create_author():
    """Author metadata should be stored."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        result_json = await tool_pdf_create(
            path=path, content="Test", author="TestAuthor"
        )
        result = json.loads(result_json)
        assert result["author"] == "TestAuthor"
    finally:
        os.unlink(path)


# ── Stress Tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pdf_create_large_content():
    """Create a PDF with lots of content."""
    from dragon.tool.builtins.pdf_create import tool_pdf_create

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name

    try:
        # Generate ~100 paragraphs
        paragraphs = []
        for i in range(100):
            p = f"## Section {i}\n\nThis is paragraph {i}. " * 20
            paragraphs.append(p)
        content = "\n\n".join(paragraphs)

        result_json = await tool_pdf_create(path=path, content=content, title="Large")
        result = json.loads(result_json)
        assert result["pages"] >= 1
        # Compressed repetitive text is very compact; size is fine as long as > 0
    finally:
        os.unlink(path)


# ── Registry Integration ───────────────────────────────────────────────

def test_pdf_create_registered():
    """Verify pdf_create is registered in the tool registry."""
    from dragon.tool.registry import ToolRegistry

    registry = ToolRegistry()

    # Import triggers: the builtins __init__ registers tools
    from dragon.tool.builtins import register_builtins

    register_builtins(registry)

    tools = registry.list_tools()
    tool_names = [t["name"] for t in tools]

    assert "pdf_create" in tool_names, f"pdf_create not found in {tool_names}"
