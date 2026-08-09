"""
Tests for Dragon session_search and clarify tools (bug fixes).

Covers: session_search without platform param, clarify with list choices.
"""

import json
import pytest


# ── session_search ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_search_recent_no_query():
    """session_search with no query should list recent sessions without error."""
    from dragon.tool.builtins.session_search import tool_session_search

    result_json = await tool_session_search(query="", limit=3)
    result = json.loads(result_json)

    assert "mode" in result
    assert result["mode"] == "recent"
    assert "sessions" in result
    assert isinstance(result["sessions"], list)


@pytest.mark.asyncio
async def test_session_search_with_query():
    """session_search with query should search without error."""
    from dragon.tool.builtins.session_search import tool_session_search

    # Even with no results, should not crash
    result_json = await tool_session_search(query="nonexistent_xyz", limit=5)
    result = json.loads(result_json)

    assert result["mode"] == "search"
    assert isinstance(result["sessions"], list)


@pytest.mark.asyncio
async def test_session_search_no_platform_error():
    """Regression: session_search must not raise NameError for 'platform'."""
    from dragon.tool.builtins.session_search import tool_session_search

    # This was crashing with "name 'platform' is not defined"
    result = await tool_session_search(query="", limit=1)
    data = json.loads(result)
    assert data["mode"] == "recent"
    assert "sessions" in data


# ── clarify ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clarify_basic_question():
    """clarify with a simple question should work."""
    from dragon.tool.builtins.clarify import tool_clarify

    result_json = await tool_clarify(question="What do you prefer?")
    result = json.loads(result_json)

    assert result["action"] == "clarify"
    assert result["question"] == "What do you prefer?"
    assert result["status"] == "question_sent_to_user"


@pytest.mark.asyncio
async def test_clarify_with_string_choices():
    """clarify with choices as a JSON string."""
    from dragon.tool.builtins.clarify import tool_clarify

    result_json = await tool_clarify(
        question="Pick one",
        choices='["A", "B", "C"]',
    )
    result = json.loads(result_json)
    assert result["choices"] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_clarify_with_list_choices():
    """Regression: clarify must accept choices as a Python list (what model passes)."""
    from dragon.tool.builtins.clarify import tool_clarify

    # The model passes choices as a list, not a JSON string
    result_json = await tool_clarify(
        question="Pick one",
        choices=["Option 1", "Option 2", "Option 3"],
    )
    result = json.loads(result_json)

    assert result["action"] == "clarify"
    assert result["choices"] == ["Option 1", "Option 2", "Option 3"]


@pytest.mark.asyncio
async def test_clarify_with_comma_string_choices():
    """clarify with comma-separated string choices."""
    from dragon.tool.builtins.clarify import tool_clarify

    result_json = await tool_clarify(
        question="Pick",
        choices="A, B, C",
    )
    result = json.loads(result_json)
    assert "A" in result["choices"]


@pytest.mark.asyncio
async def test_clarify_empty_question():
    """clarify with empty question should return error."""
    from dragon.tool.builtins.clarify import tool_clarify

    result_json = await tool_clarify(question="")
    result = json.loads(result_json)
    assert "error" in result


@pytest.mark.asyncio
async def test_clarify_no_choices():
    """clarify without choices should work."""
    from dragon.tool.builtins.clarify import tool_clarify

    result_json = await tool_clarify(question="Yes or no?")
    result = json.loads(result_json)

    assert result["action"] == "clarify"
    assert "choices" not in result
