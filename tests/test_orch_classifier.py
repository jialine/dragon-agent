"""Tests for dragon/orchestrator/classifier.py"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
from dragon.orchestrator.classifier import classify, Classification, Tier

class TestClassify:
    def test_returns_classification(self):
        result = classify("What is Python?")
        assert isinstance(result, Classification)

    def test_has_tier(self):
        result = classify("Write a complete web application with auth")
        assert isinstance(result.tier, Tier)

    def test_simple_question(self):
        result = classify("Hello")
        assert isinstance(result.tier, Tier)

    def test_empty_input(self):
        result = classify("")
        assert isinstance(result, Classification)

class TestTier:
    def test_comparison(self):
        assert Tier.SIMPLE.value < Tier.COMPLEX.value
