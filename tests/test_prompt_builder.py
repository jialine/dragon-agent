"""Tests for dragon/prompt_builder.py"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
import pytest
from dragon.prompt_builder import (
    MiniTemplate, PromptBuilder, BuiltPrompt, CachePolicy, CacheEntry,
)

class TestMiniTemplate:
    def test_simple_variable(self):
        t = MiniTemplate("Hello {{name}}!")
        assert t.render(name="World") == "Hello World!"

    def test_multiple_variables(self):
        t = MiniTemplate("{{greeting}}, {{name}}!")
        result = t.render(greeting="Hi", name="Test")
        assert "Hi, Test!" == result

    def test_no_variables(self):
        t = MiniTemplate("Plain text")
        assert t.render() == "Plain text"

    def test_variables_set(self):
        t = MiniTemplate("{{a}} {{b}} {{a}}")
        assert t.variables == {"a", "b"}

    def test_missing_variable(self):
        t = MiniTemplate("{{exists}} {{missing}}")
        result = t.render(exists="yes")
        assert "yes" in result

class TestPromptBuilder:
    def test_build_basic(self):
        pb = PromptBuilder(
            identity="You are a test assistant.",
            tool_guidance="Use tools wisely.",
            help_guidance="Ask for help when needed.",
            industry_preambles=None,
            cache_policy=CachePolicy.DISABLED,
            cache_ttl_minutes=5,
            max_cache_entries=100,
        )
        result = pb.build(industry="technology", context="Testing context", skills=None, extra_sections=None, platform="cli", metadata=None)
        assert isinstance(result, BuiltPrompt)
        assert len(result.system_prompt) > 0

    def test_build_with_skills(self):
        pb = PromptBuilder(identity="ID", tool_guidance="", help_guidance="", industry_preambles=None, cache_policy=CachePolicy.DISABLED, cache_ttl_minutes=5, max_cache_entries=100)
        result = pb.build(industry="general", context="ctx", skills=["python", "testing"], platform="cli")
        assert isinstance(result, BuiltPrompt)

    def test_cache_stats(self):
        pb = PromptBuilder(identity="ID", tool_guidance="TG", help_guidance="HG", industry_preambles=None, cache_policy=CachePolicy.PERSISTENT, cache_ttl_minutes=5, max_cache_entries=100)
        stats = pb.get_cache_stats()
        assert isinstance(stats, dict)

    def test_invalidate_cache(self):
        pb = PromptBuilder(identity="ID", tool_guidance="TG", help_guidance="HG", industry_preambles=None, cache_policy=CachePolicy.DISABLED, cache_ttl_minutes=5, max_cache_entries=100)
        count = pb.invalidate_cache()
        assert count >= 0

class TestCacheEntry:
    def test_not_expired_initially(self):
        import time
        e = CacheEntry(key="test", system_prompt="hello", created_at=time.time())
        assert e.expired is False
