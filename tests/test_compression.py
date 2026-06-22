"""
Unit tests for dragon.compression — token estimation, CompressionConfig, ContextCompressor.
"""
import pytest
from dragon.compression import (
    estimate_tokens,
    estimate_message_tokens,
    CompressionConfig,
    ContextCompressor,
)


# ═══════════════════════════════════════════════════════════════
# estimate_tokens
# ═══════════════════════════════════════════════════════════════

class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert estimate_tokens("") == 0

    def test_none_returns_zero(self):
        assert estimate_tokens(None) == 0

    def test_english_text(self):
        # "Hello world" = 11 chars, ~11/4 = 2
        result = estimate_tokens("Hello world")
        assert result == 2

    def test_cjk_text(self):
        # 6 CJK chars → 6 / 1.5 = 4
        result = estimate_tokens("你好世界你好")
        assert result == 4

    def test_mixed_cjk_english(self):
        # "你好world" — 2 CJK + 5 other = 2/1.5 + 5/4 = 1 + 1 = 2
        result = estimate_tokens("你好world")
        assert result == 2

    def test_long_english_text(self):
        # 100 chars → 100 / 4 = 25
        text = "a" * 100
        result = estimate_tokens(text)
        assert result == 25

    def test_long_cjk_text(self):
        # 300 CJK chars → 300 / 1.5 = 200
        text = "中" * 300
        result = estimate_tokens(text)
        assert result == 200

    def test_whitespace_only(self):
        result = estimate_tokens("   ")
        assert result == 0

    def test_single_cjk_char(self):
        result = estimate_tokens("中")
        assert result == 0

    def test_single_ascii_char(self):
        result = estimate_tokens("a")
        assert result == 0


# ═══════════════════════════════════════════════════════════════
# estimate_message_tokens
# ═══════════════════════════════════════════════════════════════

class TestEstimateMessageTokens:
    def test_empty_list_returns_zero(self):
        assert estimate_message_tokens([]) == 0

    def test_single_message(self):
        # "Hello" = 5 chars → 5/4 = 1, + 4 overhead = 5
        msgs = [{"role": "user", "content": "Hello"}]
        assert estimate_message_tokens(msgs) == 5

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"},
        ]
        # "You are helpful." = 16 chars → 16/4 = 4 + 4 = 8
        # "hi" = 2 chars → 2/4 = 0 + 4 = 4
        # "hello there" = 11 chars → 11/4 = 2 + 4 = 6
        # total = 8 + 4 + 6 = 18
        assert estimate_message_tokens(msgs) == 18

    def test_message_with_empty_content(self):
        msgs = [{"role": "system", "content": ""}]
        assert estimate_message_tokens(msgs) == 4

    def test_message_missing_content_field(self):
        msgs = [{"role": "user"}]
        # .get("content", "") returns "" → 0 + 4 = 4
        assert estimate_message_tokens(msgs) == 4

    def test_cjk_messages(self):
        # "你好世界" = 4 CJK chars → 4/1.5 = 2 + 4 = 6
        msgs = [{"role": "user", "content": "你好世界"}]
        assert estimate_message_tokens(msgs) == 6


# ═══════════════════════════════════════════════════════════════
# CompressionConfig
# ═══════════════════════════════════════════════════════════════

class TestCompressionConfig:
    def test_default_values(self):
        cfg = CompressionConfig()
        assert cfg.enabled is True
        assert cfg.threshold_ratio == 0.75
        assert cfg.target_ratio == 0.50
        assert cfg.keep_recent == 10
        assert cfg.keep_system is True
        assert cfg.summary_model == ""
        assert cfg.max_summary_tokens == 500

    def test_custom_values(self):
        cfg = CompressionConfig(
            enabled=False,
            threshold_ratio=0.60,
            target_ratio=0.40,
            keep_recent=5,
            keep_system=False,
            summary_model="gpt-4",
            max_summary_tokens=200,
        )
        assert cfg.enabled is False
        assert cfg.threshold_ratio == 0.60
        assert cfg.target_ratio == 0.40
        assert cfg.keep_recent == 5
        assert cfg.keep_system is False
        assert cfg.summary_model == "gpt-4"
        assert cfg.max_summary_tokens == 200


# ═══════════════════════════════════════════════════════════════
# ContextCompressor — needs_compression & usage_ratio
# ═══════════════════════════════════════════════════════════════

class TestNeedsCompression:
    def test_under_threshold_returns_false(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=10000)
        msgs = [{"role": "user", "content": "hi"}]
        assert compressor.needs_compression(msgs) is False

    def test_over_threshold_returns_true(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=100)
        # threshold = 75; "x" * 400 = 100 content tokens + 4 overhead = 104 > 75
        msgs = [{"role": "user", "content": "x" * 400}]
        assert compressor.needs_compression(msgs) is True

    def test_at_exact_threshold_returns_false(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=100)
        # threshold = 75; "x" * 284 = 71 content tokens + 4 overhead = 75 (not over)
        msgs = [{"role": "user", "content": "x" * 284}]
        assert compressor.needs_compression(msgs) is False

    def test_disabled_returns_false(self):
        cfg = CompressionConfig(enabled=False)
        compressor = ContextCompressor(config=cfg, context_limit=100)
        msgs = [{"role": "user", "content": "x" * 400}]
        assert compressor.needs_compression(msgs) is False

    def test_empty_messages_returns_false(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=10000)
        assert compressor.needs_compression([]) is False


class TestUsageRatio:
    def test_usage_ratio_correct(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        # "x" * 100 = 25 content tokens + 4 overhead = 29; 29/1000 = 0.029
        msgs = [{"role": "user", "content": "x" * 100}]
        assert compressor.usage_ratio(msgs) == pytest.approx(0.029)

    def test_usage_ratio_empty(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        assert compressor.usage_ratio([]) == 0.0

    def test_usage_ratio_with_system_message(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        # "sys" = 3/4 = 0 content + 4 overhead, "hi" = 2/4 = 0 + 4 overhead = 8
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        assert compressor.usage_ratio(msgs) == pytest.approx(0.008)


# ═══════════════════════════════════════════════════════════════
# ContextCompressor — compress()
# ═══════════════════════════════════════════════════════════════

class TestCompress:
    def test_empty_messages_returns_empty(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=10000)
        result = compressor.compress([])
        assert result == []

    def test_under_threshold_returns_unchanged(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=10000)
        msgs = [{"role": "user", "content": "hi"}]
        result = compressor.compress(msgs)
        assert result == msgs

    def test_keeps_system_message(self):
        compressor = ContextCompressor(
            config=CompressionConfig(keep_system=True, keep_recent=2),
            context_limit=200,
        )
        # threshold=150, target=100
        # system: "You are an assistant" = 20/4 = 5 + 4 = 9
        # 8 big user messages with ~200 tokens each → total >> 150 → trigger
        msgs = [{"role": "system", "content": "You are an assistant"}] + [
            {"role": "user", "content": f"msg{i} " + "x" * 800} for i in range(8)
        ]
        result = compressor.compress(msgs)
        # system should be the first message
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are an assistant"

    def test_keeps_recent_messages(self):
        compressor = ContextCompressor(
            config=CompressionConfig(keep_recent=3, keep_system=False),
            context_limit=300,
        )
        msgs = [
            {"role": "user", "content": "msg0 " + "x" * 800},
            {"role": "assistant", "content": "msg1 " + "x" * 800},
            {"role": "user", "content": "msg2 " + "x" * 800},
            {"role": "assistant", "content": "recent3"},
            {"role": "user", "content": "recent4"},
            {"role": "assistant", "content": "recent5"},
        ]
        result = compressor.compress(msgs)
        # Last 3 messages should be preserved intact
        assert result[-3]["content"] == "recent3"
        assert result[-2]["content"] == "recent4"
        assert result[-1]["content"] == "recent5"

    def test_inserts_compression_summary(self):
        compressor = ContextCompressor(
            config=CompressionConfig(keep_recent=2, keep_system=False),
            context_limit=200,
        )
        # Messages sized so middle messages are partially truncated but not entirely wiped
        msgs = [
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "x" * 200},
            {"role": "user", "content": "x" * 200},
            {"role": "assistant", "content": "r3"},
            {"role": "user", "content": "r4"},
        ]
        result = compressor.compress(msgs)
        # Should have a compression summary message
        summary_found = any("上下文已压缩" in m["content"] for m in result)
        assert summary_found

    def test_all_messages_recent_nothing_truncated(self):
        compressor = ContextCompressor(
            config=CompressionConfig(keep_recent=10, keep_system=False),
            context_limit=10000,
        )
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = compressor.compress(msgs)
        assert len(result) == 2
        assert result == msgs

    def test_no_system_message_does_not_try_to_keep(self):
        compressor = ContextCompressor(
            config=CompressionConfig(keep_system=True, keep_recent=2),
            context_limit=300,
        )
        # No system message — first message is "user"
        msgs = [
            {"role": "user", "content": "x" * 800},
            {"role": "assistant", "content": "x" * 800},
            {"role": "user", "content": "x" * 800},
            {"role": "assistant", "content": "r1"},
            {"role": "user", "content": "r2"},
        ]
        result = compressor.compress(msgs)
        # The first kept message should NOT be a system message from the original
        # (since there was no system message to begin with)
        assert result[-2]["content"] == "r1"
        assert result[-1]["content"] == "r2"

    def test_compression_only_triggers_when_over_target(self):
        """Messages over threshold but under target should not be compressed."""
        compressor = ContextCompressor(
            config=CompressionConfig(threshold_ratio=0.50, target_ratio=0.75),
            context_limit=1000,
        )
        # 604 tokens: threshold=500, but target=750 so current < target → no compress
        msgs = [{"role": "user", "content": "x" * 2400}]  # 600 + 4 = 604
        result = compressor.compress(msgs)
        assert result == msgs


# ═══════════════════════════════════════════════════════════════
# ContextCompressor — _truncate_messages
# ═══════════════════════════════════════════════════════════════

class TestTruncateMessages:
    def test_budget_zero_returns_empty(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        msgs = [{"role": "user", "content": "hello"}]
        result = compressor._truncate_messages(msgs, 0)
        assert result == []

    def test_budget_negative_returns_empty(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        msgs = [{"role": "user", "content": "hello"}]
        result = compressor._truncate_messages(msgs, -10)
        assert result == []

    def test_empty_messages_returns_empty(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        result = compressor._truncate_messages([], 1000)
        assert result == []

    def test_budget_huge_returns_all(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        result = compressor._truncate_messages(msgs, 10000)
        assert len(result) == 2
        assert result == msgs

    def test_budget_limited_truncates_from_front(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        # "x" * 40 = 10 content tokens + 4 overhead = 14 per message
        msgs = [
            {"role": "user", "content": f"msg{i} " + "x" * 40} for i in range(10)
        ]
        # Budget = 50 tokens, each message ~14 → fits 3 messages
        result = compressor._truncate_messages(msgs, 50)
        assert len(result) <= 3
        assert result[-1]["content"] == msgs[-1]["content"]

    def test_budget_smaller_than_one_message_returns_empty(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        msgs = [{"role": "user", "content": "hello world"}]
        # "hello world" = 11/4 = 2 content + 4 overhead = 6 > budget 3
        result = compressor._truncate_messages(msgs, 3)
        assert result == []


# ═══════════════════════════════════════════════════════════════
# ContextCompressor — compression_count
# ═══════════════════════════════════════════════════════════════

class TestCompressionCount:
    def test_starts_at_zero(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=1000)
        assert compressor.compression_count == 0

    def test_increments_after_compress(self):
        compressor = ContextCompressor(
            config=CompressionConfig(keep_recent=1, keep_system=False),
            context_limit=300,
        )
        msgs = [
            {"role": "user", "content": "x" * 800},
            {"role": "assistant", "content": "x" * 800},
            {"role": "user", "content": "recent"},
        ]
        assert compressor.compression_count == 0
        compressor.compress(msgs)
        assert compressor.compression_count == 1
        compressor.compress(msgs)
        assert compressor.compression_count == 2

    def test_no_increment_when_no_compression_needed(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=10000)
        msgs = [{"role": "user", "content": "hi"}]
        compressor.compress(msgs)
        # Under target → returns early, no increment
        assert compressor.compression_count == 0


# ═══════════════════════════════════════════════════════════════
# ContextCompressor — init
# ═══════════════════════════════════════════════════════════════

class TestContextCompressorInit:
    def test_default_config_when_none(self):
        compressor = ContextCompressor()
        assert isinstance(compressor.config, CompressionConfig)
        assert compressor.config.enabled is True
        assert compressor.context_limit == 128000

    def test_custom_context_limit(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, context_limit=4096)
        assert compressor.context_limit == 4096

    def test_summary_fn_can_be_none(self):
        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, summary_fn=None)
        assert compressor._summary_fn is None

    def test_summary_fn_is_stored(self):
        async def dummy_fn(messages, max_tokens):
            return "summary"

        cfg = CompressionConfig()
        compressor = ContextCompressor(config=cfg, summary_fn=dummy_fn)
        assert compressor._summary_fn is dummy_fn
