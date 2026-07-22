"""Tests for dragon/usage_pricing.py"""
import sys; sys.path.insert(0, '/home/jialine/dragon-agent')
from dragon.usage_pricing import (
    get_pricing, get_cost, list_models, list_providers,
    format_cost, has_known_pricing, CostResult, convert_currency,
)

class TestGetPricing:
    def test_known(self):
        p = get_pricing("openai", "gpt-4o")
        assert p is not None

    def test_unknown(self):
        p = get_pricing("xxx", "yyy-999")
        assert p is None

class TestGetCost:
    def test_computes(self):
        r = get_cost("openai", "gpt-4o", prompt_tokens=1000, completion_tokens=500)
        assert isinstance(r, CostResult)
        assert r.prompt_cost > 0

class TestListModels:
    def test_returns_list(self):
        models = list_models()
        assert isinstance(models, list)
        assert len(models) > 10

class TestListProviders:
    def test_returns_list(self):
        providers = list_providers()
        assert isinstance(providers, list)
        assert "openai" in providers

class TestFormatCost:
    def test_nonempty(self):
        assert len(format_cost(0.01)) > 0

class TestHasKnownPricing:
    def test_known(self):
        assert has_known_pricing("openai", "gpt-4o") is True
    def test_unknown(self):
        assert has_known_pricing("xxx", "yyy") is False

class TestConvertCurrency:
    def test_cny(self):
        assert convert_currency(1.0, "CNY") > 6.0
    def test_usd(self):
        assert convert_currency(1.0, "USD") == 1.0
