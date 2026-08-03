"""Tests for dragon/think_scrubber.py"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
from dragon.think_scrubber import strip_think_blocks, StreamingThinkScrubber, scrub_provider_result

class TestStripThinkBlocks:
    def test_simple(self):
        r = strip_think_blocks("Before <thinking>secret</thinking> After")
        assert "secret" not in r
        assert "Before" in r and "After" in r

    def test_no_block(self):
        assert strip_think_blocks("Normal text") == "Normal text"

    def test_multiple(self):
        r = strip_think_blocks("A <thinking>x</thinking> B <thinking>y</thinking> C")
        assert "x" not in r and "y" not in r
        assert "A" in r and "C" in r

    def test_empty(self):
        assert strip_think_blocks("") == ""

class TestStreamingThinkScrubber:
    def test_initial(self):
        s = StreamingThinkScrubber(config=None)
        assert s.in_block is False

    def test_normal_text(self):
        s = StreamingThinkScrubber(config=None)
        assert s.feed("Hello") == "Hello"

    def test_enter_block(self):
        s = StreamingThinkScrubber(config=None)
        r = s.feed("<thinking>secret")
        assert s.in_block is True

    def test_exit_block(self):
        s = StreamingThinkScrubber(config=None)
        s.feed("<thinking>hidden")
        r = s.feed("</thinking> visible")
        assert "hidden" not in r
        assert "visible" in r

    def test_reset(self):
        s = StreamingThinkScrubber(config=None)
        s.feed("<thinking>")
        s.reset()
        assert s.in_block is False

class TestScrubProviderResult:
    def test_no_block(self):
        c, _ = scrub_provider_result("Hello")
        assert c == "Hello"

    def test_with_block(self):
        c, _ = scrub_provider_result("Out <thinking>in</thinking> out")
        assert "in" not in c
        assert "Out" in c
