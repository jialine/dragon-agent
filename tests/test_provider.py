"""
Unit tests for panda.provider module — data classes, BaseProvider,
ProviderRegistry, and concrete provider constructors.

All tests are synchronous. No HTTP/API calls — mock as needed.
"""
import asyncio
import os
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from panda.provider import (
    ProviderResult,
    StreamChunk,
    ProviderConfig,
    BaseProvider,
    OpenAIProvider,
    DeepSeekProvider,
    XAIProvider,
    GoogleProvider,
    AnthropicProvider,
    LocalProvider,
    OllamaProvider,
    TogetherProvider,
    GroqProvider,
    MistralProvider,
    MoonshotProvider,
    OpenRouterProvider,
    AzureOpenAIProvider,
    CohereProvider,
    ReplicateProvider,
    PerplexityProvider,
    FireworksProvider,
    CloudflareProvider,
    VertexAIProvider,
    BedrockProvider,
    ProviderRegistry,
    auto_setup_providers,
)


# ══════════════════════════════════════════════════════════════════════
# ProviderResult dataclass
# ══════════════════════════════════════════════════════════════════════

class TestProviderResultCreation:
    """Tests for ProviderResult dataclass creation and defaults."""

    def test_create_with_all_fields(self):
        result = ProviderResult(
            content="Hello, world!",
            model="gpt-4o",
            provider="openai",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="stop",
            latency_ms=1234.5,
            raw={"choices": [{"message": {"content": "Hello, world!"}}]},
        )
        assert result.content == "Hello, world!"
        assert result.model == "gpt-4o"
        assert result.provider == "openai"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        assert result.finish_reason == "stop"
        assert result.latency_ms == 1234.5
        assert result.raw == {"choices": [{"message": {"content": "Hello, world!"}}]}

    def test_create_with_minimal_fields(self):
        result = ProviderResult(content="ok", model="test", provider="test")
        assert result.content == "ok"
        assert result.model == "test"
        assert result.provider == "test"
        assert result.usage == {}
        assert result.finish_reason == "stop"
        assert result.latency_ms == 0.0
        assert result.raw is None

    def test_default_finish_reason(self):
        result = ProviderResult(content="x", model="m", provider="p")
        assert result.finish_reason == "stop"

    def test_default_latency_ms(self):
        result = ProviderResult(content="x", model="m", provider="p")
        assert result.latency_ms == 0.0

    def test_default_raw_is_none(self):
        result = ProviderResult(content="x", model="m", provider="p")
        assert result.raw is None


class TestProviderResultTotalTokens:
    """Tests for the total_tokens property."""

    def test_total_tokens_present(self):
        result = ProviderResult(
            content="x", model="m", provider="p",
            usage={"total_tokens": 42},
        )
        assert result.total_tokens == 42

    def test_total_tokens_zero(self):
        result = ProviderResult(
            content="x", model="m", provider="p",
            usage={"total_tokens": 0},
        )
        assert result.total_tokens == 0

    def test_total_tokens_missing_key_returns_zero(self):
        result = ProviderResult(
            content="x", model="m", provider="p",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )
        assert result.total_tokens == 0

    def test_total_tokens_empty_usage(self):
        result = ProviderResult(content="x", model="m", provider="p")
        assert result.total_tokens == 0


class TestProviderResultImmutability:
    """ProviderResult is a dataclass; fields can be reassigned."""

    def test_fields_are_mutable(self):
        result = ProviderResult(content="old", model="m", provider="p")
        result.content = "new"
        assert result.content == "new"

    def test_usage_dict_is_shared_ref(self):
        usage = {"total_tokens": 10}
        result = ProviderResult(content="x", model="m", provider="p", usage=usage)
        usage["total_tokens"] = 99
        assert result.total_tokens == 99


# ══════════════════════════════════════════════════════════════════════
# StreamChunk dataclass
# ══════════════════════════════════════════════════════════════════════

class TestStreamChunkCreation:
    """Tests for StreamChunk dataclass creation and defaults."""

    def test_create_with_content(self):
        chunk = StreamChunk(content="Hello")
        assert chunk.content == "Hello"
        assert chunk.finish_reason is None
        assert chunk.usage is None

    def test_create_with_all_fields(self):
        chunk = StreamChunk(
            content="world",
            finish_reason="stop",
            usage={"total_tokens": 50},
        )
        assert chunk.content == "world"
        assert chunk.finish_reason == "stop"
        assert chunk.usage == {"total_tokens": 50}

    def test_default_content_empty_string(self):
        chunk = StreamChunk()
        assert chunk.content == ""

    def test_default_finish_reason_none(self):
        chunk = StreamChunk()
        assert chunk.finish_reason is None

    def test_default_usage_none(self):
        chunk = StreamChunk()
        assert chunk.usage is None

    def test_create_with_none_values_explicitly(self):
        chunk = StreamChunk(content="", finish_reason=None, usage=None)
        assert chunk.content == ""
        assert chunk.finish_reason is None
        assert chunk.usage is None


# ══════════════════════════════════════════════════════════════════════
# ProviderConfig dataclass
# ══════════════════════════════════════════════════════════════════════

class TestProviderConfigCreation:
    """Tests for ProviderConfig dataclass creation and defaults."""

    def test_create_with_all_fields(self):
        config = ProviderConfig(
            provider="openai",
            api_key="sk-test123",
            api_key_env="OPENAI_API_KEY",
            base_url="https://custom.api.com/v1",
            default_model="gpt-4o",
            timeout_secs=60.0,
            max_retries=3,
        )
        assert config.provider == "openai"
        assert config.api_key == "sk-test123"
        assert config.api_key_env == "OPENAI_API_KEY"
        assert config.base_url == "https://custom.api.com/v1"
        assert config.default_model == "gpt-4o"
        assert config.timeout_secs == 60.0
        assert config.max_retries == 3

    def test_create_with_minimal_fields(self):
        config = ProviderConfig(provider="deepseek")
        assert config.provider == "deepseek"
        assert config.api_key == ""
        assert config.api_key_env == ""
        assert config.base_url is None
        assert config.default_model == ""
        assert config.timeout_secs == 120.0
        assert config.max_retries == 2

    def test_default_timeout_secs(self):
        config = ProviderConfig(provider="test")
        assert config.timeout_secs == 120.0

    def test_default_max_retries(self):
        config = ProviderConfig(provider="test")
        assert config.max_retries == 2

    def test_default_api_key_empty(self):
        config = ProviderConfig(provider="test")
        assert config.api_key == ""

    def test_default_base_url_none(self):
        config = ProviderConfig(provider="test")
        assert config.base_url is None


# ══════════════════════════════════════════════════════════════════════
# BaseProvider
# ══════════════════════════════════════════════════════════════════════

class TestBaseProviderConstructor:
    """Tests for BaseProvider constructor — uses OpenAIProvider (concrete)."""

    def test_stores_config(self):
        config = ProviderConfig(provider="openai", api_key="sk-abc")
        provider = OpenAIProvider(config)
        assert provider.config is config
        assert provider.config.api_key == "sk-abc"

    def test_api_key_from_config_direct(self):
        config = ProviderConfig(provider="openai", api_key="sk-direct")
        provider = OpenAIProvider(config)
        assert provider._api_key == "sk-direct"

    def test_api_key_from_env_var(self):
        config = ProviderConfig(provider="openai", api_key_env="TEST_PROVIDER_KEY")
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "sk-from-env"}):
            provider = OpenAIProvider(config)
            assert provider._api_key == "sk-from-env"

    def test_api_key_direct_takes_precedence_over_env(self):
        config = ProviderConfig(
            provider="openai", api_key="sk-direct", api_key_env="TEST_PROVIDER_KEY",
        )
        with patch.dict(os.environ, {"TEST_PROVIDER_KEY": "sk-from-env"}):
            provider = OpenAIProvider(config)
            assert provider._api_key == "sk-direct"

    def test_api_key_env_var_not_set_returns_empty(self):
        config = ProviderConfig(provider="openai", api_key_env="NONEXISTENT_VAR_XYZ")
        with patch.dict(os.environ, {}, clear=True):
            provider = OpenAIProvider(config)
            assert provider._api_key == ""


class TestBaseProviderAvailable:
    """Tests for the available property."""

    def test_available_true_with_api_key(self):
        config = ProviderConfig(provider="openai", api_key="sk-abc")
        provider = OpenAIProvider(config)
        assert provider.available is True

    def test_available_false_without_api_key(self):
        config = ProviderConfig(provider="openai", api_key_env="MISSING_KEY_12345")
        with patch.dict(os.environ, {}, clear=True):
            provider = OpenAIProvider(config)
            assert provider.available is False

    def test_available_true_with_env_var_key(self):
        config = ProviderConfig(provider="openai", api_key_env="TEST_AVAILABLE_KEY")
        with patch.dict(os.environ, {"TEST_AVAILABLE_KEY": "sk-env"}):
            provider = OpenAIProvider(config)
            assert provider.available is True


class TestBaseProviderBuildHeaders:
    """Tests for _build_headers method."""

    def test_build_headers_with_api_key(self):
        config = ProviderConfig(provider="openai", api_key="sk-test")
        provider = OpenAIProvider(config)
        headers = provider._build_headers()
        assert headers == {"Authorization": "Bearer sk-test"}

    def test_build_headers_with_empty_key(self):
        config = ProviderConfig(provider="openai", api_key="")
        provider = OpenAIProvider(config)
        headers = provider._build_headers()
        assert headers == {"Authorization": "Bearer "}

    def test_build_headers_with_env_key(self):
        config = ProviderConfig(provider="openai", api_key_env="TEST_HEADER_KEY")
        with patch.dict(os.environ, {"TEST_HEADER_KEY": "sk-env-header"}):
            provider = OpenAIProvider(config)
            headers = provider._build_headers()
            assert headers == {"Authorization": "Bearer sk-env-header"}


# ══════════════════════════════════════════════════════════════════════
# Concrete Provider Constructors
# ══════════════════════════════════════════════════════════════════════

class TestDeepSeekProvider:
    """Tests for DeepSeekProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="deepseek", api_key="sk-test")
        provider = DeepSeekProvider(config)
        assert config.base_url == "https://api.deepseek.com/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="deepseek", api_key="sk-test",
            base_url="https://custom.deepseek.com/v1",
        )
        provider = DeepSeekProvider(config)
        assert config.base_url == "https://custom.deepseek.com/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="deepseek", api_key="sk-test")
        provider = DeepSeekProvider(config)
        assert config.default_model == "deepseek-chat"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="deepseek", api_key="sk-test",
            default_model="deepseek-reasoner",
        )
        provider = DeepSeekProvider(config)
        assert config.default_model == "deepseek-reasoner"

    def test_available_property(self):
        config = ProviderConfig(provider="deepseek", api_key="sk-test")
        provider = DeepSeekProvider(config)
        assert provider.available is True


class TestXAIProvider:
    """Tests for XAIProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="xai", api_key="sk-test")
        provider = XAIProvider(config)
        assert config.base_url == "https://api.x.ai/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="xai", api_key="sk-test",
            base_url="https://custom.x.ai/v1",
        )
        provider = XAIProvider(config)
        assert config.base_url == "https://custom.x.ai/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="xai", api_key="sk-test")
        provider = XAIProvider(config)
        assert config.default_model == "grok-3-beta"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="xai", api_key="sk-test",
            default_model="grok-3",
        )
        provider = XAIProvider(config)
        assert config.default_model == "grok-3"

    def test_available_property(self):
        config = ProviderConfig(provider="xai", api_key="sk-test")
        provider = XAIProvider(config)
        assert provider.available is True


class TestLocalProvider:
    """Tests for LocalProvider constructor."""

    def test_no_key_needed_for_local(self):
        """LocalProvider doesn't need an API key to be 'available'."""
        config = ProviderConfig(provider="local", base_url="/path/to/model.gguf")
        provider = LocalProvider(config)
        # Local provider is always "available" even with no api_key
        # because it doesn't need an external API
        assert provider._model_path == "/path/to/model.gguf"

    def test_default_model_path_empty(self):
        config = ProviderConfig(provider="local")
        provider = LocalProvider(config)
        assert provider._model_path == ""


# ══════════════════════════════════════════════════════════════════════
# ProviderRegistry
# ══════════════════════════════════════════════════════════════════════

class TestProviderRegistryConstructor:
    """Tests for ProviderRegistry constructor defaults."""

    def test_empty_on_creation(self):
        registry = ProviderRegistry()
        assert registry._providers == {}
        assert registry._default_provider is None
        assert registry._fallback_chain == []

    def test_providers_is_dict(self):
        registry = ProviderRegistry()
        assert isinstance(registry._providers, dict)


class TestProviderRegistryRegister:
    """Tests for register() method."""

    def test_register_adds_provider(self):
        registry = ProviderRegistry()
        config = ProviderConfig(provider="openai", api_key="sk-test")
        provider = OpenAIProvider(config)
        registry.register("openai", provider)
        assert "openai" in registry._providers
        assert registry._providers["openai"] is provider

    def test_first_registered_becomes_default_automatically(self):
        registry = ProviderRegistry()
        config = ProviderConfig(provider="openai", api_key="sk-test")
        provider = OpenAIProvider(config)
        registry.register("openai", provider)
        assert registry._default_provider == "openai"

    def test_register_multiple_first_remains_default(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        p2 = DeepSeekProvider(ProviderConfig(provider="deepseek", api_key="sk-2"))
        registry.register("openai", p1)
        registry.register("deepseek", p2)
        assert registry._default_provider == "openai"

    def test_register_with_default_true_overrides(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        p2 = DeepSeekProvider(ProviderConfig(provider="deepseek", api_key="sk-2"))
        registry.register("openai", p1)
        registry.register("deepseek", p2, default=True)
        assert registry._default_provider == "deepseek"

    def test_register_multiple_providers(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        p2 = DeepSeekProvider(ProviderConfig(provider="deepseek", api_key="sk-2"))
        p3 = XAIProvider(ProviderConfig(provider="xai", api_key="sk-3"))
        registry.register("openai", p1)
        registry.register("deepseek", p2)
        registry.register("xai", p3)
        assert len(registry._providers) == 3
        assert set(registry._providers.keys()) == {"openai", "deepseek", "xai"}


class TestProviderRegistryUnregister:
    """Tests for unregister() method."""

    def test_unregister_removes_provider(self):
        registry = ProviderRegistry()
        config = ProviderConfig(provider="openai", api_key="sk-test")
        provider = OpenAIProvider(config)
        registry.register("openai", provider)
        registry.unregister("openai")
        assert "openai" not in registry._providers

    def test_unregister_nonexistent_no_error(self):
        registry = ProviderRegistry()
        # Should not raise
        registry.unregister("nonexistent")

    def test_unregister_default_falls_back_to_next(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        p2 = DeepSeekProvider(ProviderConfig(provider="deepseek", api_key="sk-2"))
        registry.register("openai", p1)
        registry.register("deepseek", p2)
        assert registry._default_provider == "openai"
        registry.unregister("openai")
        assert registry._default_provider == "deepseek"

    def test_unregister_last_clears_default(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        registry.register("openai", p1)
        registry.unregister("openai")
        assert registry._default_provider is None


class TestProviderRegistryFallbackChain:
    """Tests for set_fallback_chain()."""

    def test_set_fallback_chain(self):
        registry = ProviderRegistry()
        chain = ["deepseek", "openai", "anthropic"]
        registry.set_fallback_chain(chain)
        assert registry._fallback_chain == chain

    def test_set_empty_fallback_chain(self):
        registry = ProviderRegistry()
        registry.set_fallback_chain([])
        assert registry._fallback_chain == []

    def test_overwrite_existing_chain(self):
        registry = ProviderRegistry()
        registry.set_fallback_chain(["a", "b"])
        registry.set_fallback_chain(["c"])
        assert registry._fallback_chain == ["c"]


class TestProviderRegistryAvailableProviders:
    """Tests for available_providers()."""

    def test_lists_available_providers(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        p2 = DeepSeekProvider(ProviderConfig(provider="deepseek", api_key="sk-2"))
        registry.register("openai", p1)
        registry.register("deepseek", p2)
        available = registry.available_providers()
        assert sorted(available) == ["deepseek", "openai"]

    def test_excludes_unavailable_providers(self):
        registry = ProviderRegistry()
        p_ok = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        # Provider with no key — will be unavailable unless env var is set
        p_bad = OpenAIProvider(ProviderConfig(
            provider="bad", api_key_env="MISSING_TEST_KEY_999",
        ))
        with patch.dict(os.environ, {}, clear=True):
            registry.register("openai", p_ok)
            registry.register("bad", p_bad)
            available = registry.available_providers()
            assert available == ["openai"]

    def test_empty_when_no_providers(self):
        registry = ProviderRegistry()
        assert registry.available_providers() == []

    def test_empty_when_all_unavailable(self):
        registry = ProviderRegistry()
        with patch.dict(os.environ, {}, clear=True):
            p = OpenAIProvider(ProviderConfig(
                provider="test", api_key_env="MISSING_KEY",
            ))
            registry.register("test", p)
            assert registry.available_providers() == []


class TestProviderRegistryCall:
    """Tests for call() method — sync tests with mocking."""

    def _make_registry_with_provider(self, name="openai", api_key="sk-test"):
        """Helper: create a registry with one available provider."""
        registry = ProviderRegistry()
        config = ProviderConfig(provider=name, api_key=api_key, default_model="test-model")
        provider = OpenAIProvider(config)
        registry.register(name, provider)
        return registry, provider

    def test_call_unknown_provider_raises_valueerror(self):
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="Unknown provider"):
            asyncio.run(registry.call("nonexistent"))

    def test_call_unknown_provider_raises_sync_check(self):
        """ValueError is raised synchronously, before any await."""
        registry = ProviderRegistry()
        # Even calling without asyncio, the registry._providers.get is sync
        with pytest.raises(ValueError, match="Unknown provider"):
            # Use coroutine.send to trigger up to first await
            coro = registry.call("nonexistent")
            try:
                coro.send(None)
            except StopIteration:
                pass

    def test_call_available_provider_uses_default_model(self):
        registry, provider = self._make_registry_with_provider()
        # Mock provider.complete to return a result
        expected = ProviderResult(content="response", model="test-model", provider="openai")
        provider.complete = AsyncMock(return_value=expected)

        result = asyncio.run(registry.call("openai"))
        assert result is expected

    def test_call_passes_explicit_model(self):
        registry, provider = self._make_registry_with_provider()
        expected = ProviderResult(content="ok", model="gpt-4o", provider="openai")
        provider.complete = AsyncMock(return_value=expected)

        result = asyncio.run(registry.call("openai", model="gpt-4o"))
        provider.complete.assert_awaited_once_with(
            model="gpt-4o", messages=[], temperature=0.7, max_tokens=2048,
        )

    def test_call_passes_messages_and_params(self):
        registry, provider = self._make_registry_with_provider()
        expected = ProviderResult(content="hi", model="m", provider="p")
        provider.complete = AsyncMock(return_value=expected)

        messages = [{"role": "user", "content": "hello"}]
        result = asyncio.run(registry.call(
            "openai", model="gpt-4o", messages=messages,
            temperature=0.2, max_tokens=512,
        ))
        provider.complete.assert_awaited_once_with(
            model="gpt-4o", messages=messages, temperature=0.2, max_tokens=512,
        )

    def test_call_unavailable_with_fallback(self):
        registry = ProviderRegistry()
        # Register an unavailable provider (no API key)
        p_unavailable = OpenAIProvider(ProviderConfig(
            provider="openai", api_key_env="MISSING_KEY",
        ))
        # Register a fallback that is available
        p_fallback = DeepSeekProvider(ProviderConfig(
            provider="deepseek", api_key="sk-fb",
            default_model="deepseek-chat",
        ))
        expected = ProviderResult(content="fallback response", model="deepseek-chat", provider="deepseek")
        p_fallback.complete = AsyncMock(return_value=expected)

        with patch.dict(os.environ, {}, clear=True):
            registry.register("openai", p_unavailable)
            registry.register("deepseek", p_fallback)
            registry.set_fallback_chain(["deepseek"])

            result = asyncio.run(registry.call("openai", model="gpt-4o"))
            assert result is expected
            p_fallback.complete.assert_awaited_once_with(
                model="gpt-4o", messages=[], temperature=0.7, max_tokens=2048,
            )

    def test_call_unavailable_no_valid_fallback_raises(self):
        registry = ProviderRegistry()
        p_unavailable = OpenAIProvider(ProviderConfig(
            provider="openai", api_key_env="MISSING_KEY",
        ))
        p_also_unavailable = DeepSeekProvider(ProviderConfig(
            provider="deepseek", api_key_env="ALSO_MISSING",
        ))

        with patch.dict(os.environ, {}, clear=True):
            registry.register("openai", p_unavailable)
            registry.register("deepseek", p_also_unavailable)
            registry.set_fallback_chain(["deepseek"])

            with pytest.raises(RuntimeError, match="unavailable"):
                asyncio.run(registry.call("openai"))


class TestProviderRegistryCallStream:
    """Tests for call_stream() method."""

    def test_call_stream_unknown_provider_raises_valueerror(self):
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="Unknown provider"):
            # call_stream is an async generator — the ValueError is raised
            # synchronously before the first yield, so asyncio.run catches it.
            async def collect():
                async for _ in registry.call_stream("nonexistent"):
                    pass
            asyncio.run(collect())


# ══════════════════════════════════════════════════════════════════════
# auto_setup_providers
# ══════════════════════════════════════════════════════════════════════

class TestAutoSetupProviders:
    """Tests for auto_setup_providers() factory function."""

    def test_returns_registry(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = auto_setup_providers()
            assert isinstance(registry, ProviderRegistry)

    def test_no_env_vars_ollama_is_always_present(self):
        """Ollama is always registered (local, default OLLAMA_HOST fallback)."""
        with patch.dict(os.environ, {}, clear=True):
            registry = auto_setup_providers()
            assert "ollama" in registry._providers

    def test_openai_from_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "openai" in registry.available_providers()

    def test_deepseek_from_env(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "deepseek" in registry.available_providers()

    def test_anthropic_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "anthropic" in registry.available_providers()

    def test_google_from_env(self):
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "google" in registry.available_providers()

    def test_xai_from_env(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "xai" in registry.available_providers()

    def test_multiple_providers_from_env(self):
        env = {
            "OPENAI_API_KEY": "sk-oai",
            "DEEPSEEK_API_KEY": "sk-ds",
            "ANTHROPIC_API_KEY": "sk-ant",
        }
        with patch.dict(os.environ, env, clear=True):
            registry = auto_setup_providers()
            available = registry.available_providers()
            # ollama is always registered (local, default host fallback)
            assert sorted(available) == ["anthropic", "deepseek", "ollama", "openai"]

    def test_fallback_chain_set_to_available(self):
        env = {"OPENAI_API_KEY": "sk-oai", "DEEPSEEK_API_KEY": "sk-ds"}
        with patch.dict(os.environ, env, clear=True):
            registry = auto_setup_providers()
            # ollama always present, so chain has 3 providers
            assert len(registry._fallback_chain) == 3
            assert "openai" in registry._fallback_chain
            assert "deepseek" in registry._fallback_chain
            assert "ollama" in registry._fallback_chain
            assert set(registry._fallback_chain) == {"openai", "deepseek", "ollama"}


# ══════════════════════════════════════════════════════════════════════
# Together Provider
# ══════════════════════════════════════════════════════════════════════

class TestTogetherProvider:
    """Tests for TogetherProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="together", api_key="sk-test")
        provider = TogetherProvider(config)
        assert config.base_url == "https://api.together.xyz/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="together", api_key="sk-test",
            base_url="https://custom.together.xyz/v1",
        )
        provider = TogetherProvider(config)
        assert config.base_url == "https://custom.together.xyz/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="together", api_key="sk-test")
        provider = TogetherProvider(config)
        assert config.default_model == "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="together", api_key="sk-test",
            default_model="mistralai/Mixtral-8x7B-Instruct-v0.1",
        )
        provider = TogetherProvider(config)
        assert config.default_model == "mistralai/Mixtral-8x7B-Instruct-v0.1"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="together", api_key="sk-test")
        provider = TogetherProvider(config)
        assert provider.available is True

    def test_available_false_without_key(self):
        config = ProviderConfig(provider="together", api_key_env="MISSING_TOGETHER_KEY")
        with patch.dict(os.environ, {}, clear=True):
            provider = TogetherProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# Groq Provider
# ══════════════════════════════════════════════════════════════════════

class TestGroqProvider:
    """Tests for GroqProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="groq", api_key="sk-test")
        provider = GroqProvider(config)
        assert config.base_url == "https://api.groq.com/openai/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="groq", api_key="sk-test",
            base_url="https://custom.groq.com/v1",
        )
        provider = GroqProvider(config)
        assert config.base_url == "https://custom.groq.com/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="groq", api_key="sk-test")
        provider = GroqProvider(config)
        assert config.default_model == "llama-4-maverick-17b-128e-instruct"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="groq", api_key="sk-test",
            default_model="mixtral-8x7b-32768",
        )
        provider = GroqProvider(config)
        assert config.default_model == "mixtral-8x7b-32768"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="groq", api_key="sk-test")
        provider = GroqProvider(config)
        assert provider.available is True


# ══════════════════════════════════════════════════════════════════════
# Mistral Provider
# ══════════════════════════════════════════════════════════════════════

class TestMistralProvider:
    """Tests for MistralProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="mistral", api_key="sk-test")
        provider = MistralProvider(config)
        assert config.base_url == "https://api.mistral.ai/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="mistral", api_key="sk-test",
            base_url="https://custom.mistral.ai/v1",
        )
        provider = MistralProvider(config)
        assert config.base_url == "https://custom.mistral.ai/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="mistral", api_key="sk-test")
        provider = MistralProvider(config)
        assert config.default_model == "mistral-large-latest"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="mistral", api_key="sk-test",
            default_model="codestral-latest",
        )
        provider = MistralProvider(config)
        assert config.default_model == "codestral-latest"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="mistral", api_key="sk-test")
        provider = MistralProvider(config)
        assert provider.available is True


# ══════════════════════════════════════════════════════════════════════
# Ollama Provider
# ══════════════════════════════════════════════════════════════════════

class TestOllamaProvider:
    """Tests for OllamaProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="ollama")
        provider = OllamaProvider(config)
        assert config.base_url == "http://localhost:11434/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="ollama",
            base_url="http://192.168.1.50:11434/v1",
        )
        provider = OllamaProvider(config)
        assert config.base_url == "http://192.168.1.50:11434/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="ollama")
        provider = OllamaProvider(config)
        assert config.default_model == "llama3"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="ollama",
            default_model="mistral",
        )
        provider = OllamaProvider(config)
        assert config.default_model == "mistral"

    def test_always_available_no_api_key_needed(self):
        """Ollama is always available regardless of API key."""
        config = ProviderConfig(provider="ollama")
        with patch.dict(os.environ, {}, clear=True):
            provider = OllamaProvider(config)
            assert provider.available is True

    def test_available_even_with_empty_config(self):
        """Ollama is available even with no API key and no env vars."""
        config = ProviderConfig(
            provider="ollama", api_key="", api_key_env="NONEXISTENT_VAR",
        )
        with patch.dict(os.environ, {}, clear=True):
            provider = OllamaProvider(config)
            assert provider.available is True


# ══════════════════════════════════════════════════════════════════════
# Moonshot Provider (月之暗面 / Kimi)
# ══════════════════════════════════════════════════════════════════════

class TestMoonshotProvider:
    """Tests for MoonshotProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="moonshot")
        provider = MoonshotProvider(config)
        assert config.base_url == "https://api.moonshot.cn/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(provider="moonshot", base_url="https://custom.moonshot.cn/v1")
        provider = MoonshotProvider(config)
        assert config.base_url == "https://custom.moonshot.cn/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="moonshot")
        provider = MoonshotProvider(config)
        assert config.default_model == "moonshot-v1-8k"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(provider="moonshot", default_model="moonshot-v1-32k")
        provider = MoonshotProvider(config)
        assert config.default_model == "moonshot-v1-32k"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="moonshot", api_key="sk-test")
        provider = MoonshotProvider(config)
        assert provider.available is True

    def test_not_available_without_api_key(self):
        config = ProviderConfig(provider="moonshot", api_key="", api_key_env="NONEXISTENT")
        with patch.dict(os.environ, {}, clear=True):
            provider = MoonshotProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# OpenRouter Provider
# ══════════════════════════════════════════════════════════════════════

class TestOpenRouterProvider:
    """Tests for OpenRouterProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="openrouter", api_key="sk-test")
        provider = OpenRouterProvider(config)
        assert config.base_url == "https://openrouter.ai/api/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="openrouter", api_key="sk-test",
            base_url="https://custom.openrouter.ai/api/v1",
        )
        provider = OpenRouterProvider(config)
        assert config.base_url == "https://custom.openrouter.ai/api/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="openrouter", api_key="sk-test")
        provider = OpenRouterProvider(config)
        assert config.default_model == "openai/gpt-4o"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="openrouter", api_key="sk-test",
            default_model="anthropic/claude-sonnet-4",
        )
        provider = OpenRouterProvider(config)
        assert config.default_model == "anthropic/claude-sonnet-4"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="openrouter", api_key="sk-test")
        provider = OpenRouterProvider(config)
        assert provider.available is True


# ══════════════════════════════════════════════════════════════════════
# Azure OpenAI Provider
# ══════════════════════════════════════════════════════════════════════

class TestAzureOpenAIProvider:
    """Tests for AzureOpenAIProvider constructor (inherits BaseProvider directly)."""

    def test_available_requires_both_endpoint_and_key(self):
        """Azure requires both API key and endpoint to be available."""
        config = ProviderConfig(
            provider="azure", api_key="az-key",
            base_url="https://my-resource.openai.azure.com",
        )
        provider = AzureOpenAIProvider(config)
        assert provider.available is True

    def test_available_false_without_endpoint(self):
        """Azure is not available when endpoint is missing."""
        config = ProviderConfig(provider="azure", api_key="az-key")
        with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": ""}, clear=True):
            provider = AzureOpenAIProvider(config)
            assert provider.available is False

    def test_available_false_without_key(self):
        """Azure is not available when API key is missing."""
        config = ProviderConfig(
            provider="azure", base_url="https://my-resource.openai.azure.com",
        )
        with patch.dict(os.environ, {}, clear=True):
            provider = AzureOpenAIProvider(config)
            assert provider.available is False

    def test_build_url_constructs_correct_url(self):
        config = ProviderConfig(
            provider="azure", api_key="az-key",
            base_url="https://my-resource.openai.azure.com",
        )
        provider = AzureOpenAIProvider(config)
        url = provider._build_url("gpt-4o")
        assert url == (
            "https://my-resource.openai.azure.com"
            "/openai/deployments/gpt-4o"
            "/chat/completions?api-version=2024-10-21"
        )

    def test_build_url_strips_trailing_slash_from_endpoint(self):
        config = ProviderConfig(
            provider="azure", api_key="az-key",
            base_url="https://my-resource.openai.azure.com/",
        )
        provider = AzureOpenAIProvider(config)
        url = provider._build_url("gpt-4o")
        assert url.startswith("https://my-resource.openai.azure.com/openai/")
        assert "//openai" not in url

    def test_build_headers_uses_api_key_format(self):
        """Azure uses 'api-key' header, not 'Authorization: Bearer'."""
        config = ProviderConfig(
            provider="azure", api_key="az-test-key",
            base_url="https://my-resource.openai.azure.com",
        )
        provider = AzureOpenAIProvider(config)
        headers = provider._build_headers()
        assert headers == {
            "api-key": "az-test-key",
            "Content-Type": "application/json",
        }

    def test_custom_deployment_model_name(self):
        """Azure deployment name is the model name passed at call time."""
        config = ProviderConfig(
            provider="azure", api_key="az-key",
            base_url="https://my-resource.openai.azure.com",
            default_model="my-custom-gpt4-deployment",
        )
        provider = AzureOpenAIProvider(config)
        assert config.default_model == "my-custom-gpt4-deployment"

    def test_endpoint_from_env_var(self):
        """Endpoint can be read from AZURE_OPENAI_ENDPOINT env var."""
        config = ProviderConfig(
            provider="azure", api_key="az-key",
        )
        with patch.dict(os.environ, {
            "AZURE_OPENAI_ENDPOINT": "https://env-resource.openai.azure.com",
        }):
            provider = AzureOpenAIProvider(config)
            assert provider._endpoint == "https://env-resource.openai.azure.com"
            assert provider.available is True

    def test_custom_api_version(self):
        config = ProviderConfig(
            provider="azure", api_key="az-key",
            base_url="https://my-resource.openai.azure.com",
            api_version="2025-01-01-preview",
        )
        provider = AzureOpenAIProvider(config)
        assert provider._api_version == "2025-01-01-preview"
        url = provider._build_url("gpt-4o")
        assert "api-version=2025-01-01-preview" in url


# ══════════════════════════════════════════════════════════════════════
# Cohere Provider
# ══════════════════════════════════════════════════════════════════════

class TestCohereProvider:
    """Tests for CohereProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="cohere", api_key="sk-test")
        provider = CohereProvider(config)
        assert config.base_url == "https://api.cohere.com/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="cohere", api_key="sk-test",
            base_url="https://custom.cohere.com/v1",
        )
        provider = CohereProvider(config)
        assert config.base_url == "https://custom.cohere.com/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="cohere", api_key="sk-test")
        provider = CohereProvider(config)
        assert config.default_model == "command-r-plus"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="cohere", api_key="sk-test",
            default_model="command-r",
        )
        provider = CohereProvider(config)
        assert config.default_model == "command-r"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="cohere", api_key="sk-test")
        provider = CohereProvider(config)
        assert provider.available is True

    def test_available_false_without_key(self):
        config = ProviderConfig(provider="cohere", api_key_env="MISSING_COHERE_KEY")
        with patch.dict(os.environ, {}, clear=True):
            provider = CohereProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# Replicate Provider
# ══════════════════════════════════════════════════════════════════════

class TestReplicateProvider:
    """Tests for ReplicateProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="replicate", api_key="sk-test")
        provider = ReplicateProvider(config)
        assert config.base_url == "https://api.replicate.com/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="replicate", api_key="sk-test",
            base_url="https://custom.replicate.com/v1",
        )
        provider = ReplicateProvider(config)
        assert config.base_url == "https://custom.replicate.com/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="replicate", api_key="sk-test")
        provider = ReplicateProvider(config)
        assert config.default_model == "meta/meta-llama-4-maverick"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="replicate", api_key="sk-test",
            default_model="meta/llama-3-70b",
        )
        provider = ReplicateProvider(config)
        assert config.default_model == "meta/llama-3-70b"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="replicate", api_key="sk-test")
        provider = ReplicateProvider(config)
        assert provider.available is True

    def test_available_false_without_key(self):
        config = ProviderConfig(provider="replicate", api_key_env="MISSING_REPLICATE_KEY")
        with patch.dict(os.environ, {}, clear=True):
            provider = ReplicateProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# Perplexity Provider
# ══════════════════════════════════════════════════════════════════════

class TestPerplexityProvider:
    """Tests for PerplexityProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="perplexity", api_key="sk-test")
        provider = PerplexityProvider(config)
        assert config.base_url == "https://api.perplexity.ai"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="perplexity", api_key="sk-test",
            base_url="https://custom.perplexity.ai",
        )
        provider = PerplexityProvider(config)
        assert config.base_url == "https://custom.perplexity.ai"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="perplexity", api_key="sk-test")
        provider = PerplexityProvider(config)
        assert config.default_model == "sonar-pro"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="perplexity", api_key="sk-test",
            default_model="sonar",
        )
        provider = PerplexityProvider(config)
        assert config.default_model == "sonar"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="perplexity", api_key="sk-test")
        provider = PerplexityProvider(config)
        assert provider.available is True

    def test_available_false_without_key(self):
        config = ProviderConfig(provider="perplexity", api_key_env="MISSING_PERPLEXITY_KEY")
        with patch.dict(os.environ, {}, clear=True):
            provider = PerplexityProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# Fireworks Provider
# ══════════════════════════════════════════════════════════════════════

class TestFireworksProvider:
    """Tests for FireworksProvider constructor (inherits OpenAIProvider)."""

    def test_sets_default_base_url(self):
        config = ProviderConfig(provider="fireworks", api_key="sk-test")
        provider = FireworksProvider(config)
        assert config.base_url == "https://api.fireworks.ai/inference/v1"

    def test_does_not_override_explicit_base_url(self):
        config = ProviderConfig(
            provider="fireworks", api_key="sk-test",
            base_url="https://custom.fireworks.ai/v1",
        )
        provider = FireworksProvider(config)
        assert config.base_url == "https://custom.fireworks.ai/v1"

    def test_sets_default_model(self):
        config = ProviderConfig(provider="fireworks", api_key="sk-test")
        provider = FireworksProvider(config)
        assert config.default_model == "accounts/fireworks/models/llama-v4-maverick"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="fireworks", api_key="sk-test",
            default_model="accounts/fireworks/models/llama-v3-70b",
        )
        provider = FireworksProvider(config)
        assert config.default_model == "accounts/fireworks/models/llama-v3-70b"

    def test_available_with_api_key(self):
        config = ProviderConfig(provider="fireworks", api_key="sk-test")
        provider = FireworksProvider(config)
        assert provider.available is True

    def test_available_false_without_key(self):
        config = ProviderConfig(provider="fireworks", api_key_env="MISSING_FIREWORKS_KEY")
        with patch.dict(os.environ, {}, clear=True):
            provider = FireworksProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# Cloudflare Workers AI Provider
# ══════════════════════════════════════════════════════════════════════

class TestCloudflareProvider:
    """Tests for CloudflareProvider constructor (inherits BaseProvider directly)."""

    def test_sets_default_model(self):
        config = ProviderConfig(provider="cloudflare", api_key="cf-key")
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "abc123"}, clear=True):
            provider = CloudflareProvider(config)
            assert config.default_model == "@cf/meta/llama-4-maverick"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="cloudflare", api_key="cf-key",
            default_model="@cf/meta/llama-3-8b",
        )
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "abc123"}, clear=True):
            provider = CloudflareProvider(config)
            assert config.default_model == "@cf/meta/llama-3-8b"

    def test_available_with_key_and_account_id(self):
        config = ProviderConfig(provider="cloudflare", api_key="cf-key")
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "abc123"}, clear=True):
            provider = CloudflareProvider(config)
            assert provider.available is True

    def test_available_false_without_account_id(self):
        config = ProviderConfig(provider="cloudflare", api_key="cf-key")
        with patch.dict(os.environ, {}, clear=True):
            provider = CloudflareProvider(config)
            assert provider.available is False

    def test_available_false_without_key(self):
        config = ProviderConfig(provider="cloudflare", api_key_env="NONEXISTENT")
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "abc123"}, clear=True):
            provider = CloudflareProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# Vertex AI Provider
# ══════════════════════════════════════════════════════════════════════

class TestVertexAIProvider:
    """Tests for VertexAIProvider constructor (inherits BaseProvider directly)."""

    def test_sets_default_model(self):
        config = ProviderConfig(provider="vertex")
        with patch.dict(os.environ, {"VERTEX_PROJECT_ID": "my-project"}, clear=True):
            provider = VertexAIProvider(config)
            assert config.default_model == "gemini-2.5-flash"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(provider="vertex", default_model="gemini-2.0-flash")
        with patch.dict(os.environ, {"VERTEX_PROJECT_ID": "my-project"}, clear=True):
            provider = VertexAIProvider(config)
            assert config.default_model == "gemini-2.0-flash"

    def test_available_with_project_id(self):
        config = ProviderConfig(provider="vertex")
        with patch.dict(os.environ, {"VERTEX_PROJECT_ID": "my-project"}, clear=True):
            provider = VertexAIProvider(config)
            assert provider.available is True

    def test_available_false_without_project_id(self):
        config = ProviderConfig(provider="vertex")
        with patch.dict(os.environ, {}, clear=True):
            provider = VertexAIProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# AWS Bedrock Provider
# ══════════════════════════════════════════════════════════════════════

class TestBedrockProvider:
    """Tests for BedrockProvider constructor (inherits BaseProvider directly)."""

    def test_sets_default_model(self):
        config = ProviderConfig(provider="bedrock")
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIA-test"}, clear=True):
            provider = BedrockProvider(config)
            assert config.default_model == "us.anthropic.claude-sonnet-4-20250514-v1:0"

    def test_does_not_override_explicit_model(self):
        config = ProviderConfig(
            provider="bedrock", default_model="us.meta.llama4-maverick",
        )
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "AKIA-test"}, clear=True):
            provider = BedrockProvider(config)
            assert config.default_model == "us.meta.llama4-maverick"

    def test_available_with_aws_credentials(self):
        config = ProviderConfig(provider="bedrock")
        with patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "AKIA-test",
            "AWS_REGION": "us-east-1",
        }, clear=True):
            provider = BedrockProvider(config)
            assert provider.available is True

    def test_available_false_without_credentials(self):
        config = ProviderConfig(provider="bedrock")
        with patch.dict(os.environ, {}, clear=True):
            provider = BedrockProvider(config)
            assert provider.available is False


# ══════════════════════════════════════════════════════════════════════
# auto_setup_providers — New Provider Env Var Detection
# ══════════════════════════════════════════════════════════════════════

class TestAutoSetupNewProviders:
    """Tests for auto_setup_providers() env var detection for new providers."""

    def test_cohere_from_env(self):
        with patch.dict(os.environ, {"COHERE_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "cohere" in registry.available_providers()

    def test_replicate_from_env(self):
        with patch.dict(os.environ, {"REPLICATE_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "replicate" in registry.available_providers()

    def test_perplexity_from_env(self):
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "perplexity" in registry.available_providers()

    def test_fireworks_from_env(self):
        with patch.dict(os.environ, {"FIREWORKS_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "fireworks" in registry.available_providers()

    def test_cloudflare_from_env(self):
        with patch.dict(os.environ, {
            "CLOUDFLARE_API_KEY": "cf-key",
            "CLOUDFLARE_ACCOUNT_ID": "abc123",
        }, clear=True):
            registry = auto_setup_providers()
            assert "cloudflare" in registry.available_providers()

    def test_cloudflare_not_registered_without_account_id(self):
        """Cloudflare should NOT be registered when account ID is missing."""
        with patch.dict(os.environ, {
            "CLOUDFLARE_API_KEY": "cf-key",
        }, clear=True):
            registry = auto_setup_providers()
            assert "cloudflare" not in registry._providers

    def test_vertex_from_env(self):
        with patch.dict(os.environ, {"VERTEX_PROJECT_ID": "my-project"}, clear=True):
            registry = auto_setup_providers()
            assert "vertex" in registry.available_providers()

    def test_bedrock_from_env(self):
        with patch.dict(os.environ, {
            "AWS_ACCESS_KEY_ID": "AKIA-test",
            "AWS_REGION": "us-east-1",
        }, clear=True):
            registry = auto_setup_providers()
            assert "bedrock" in registry.available_providers()

    def test_together_from_env(self):
        with patch.dict(os.environ, {"TOGETHER_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "together" in registry.available_providers()

    def test_groq_from_env(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "groq" in registry.available_providers()

    def test_mistral_from_env(self):
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "mistral" in registry.available_providers()

    def test_openrouter_from_env(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "openrouter" in registry.available_providers()

    def test_moonshot_from_env(self):
        with patch.dict(os.environ, {"MOONSHOT_API_KEY": "sk-test"}, clear=True):
            registry = auto_setup_providers()
            assert "moonshot" in registry.available_providers()

    def test_azure_from_env(self):
        with patch.dict(os.environ, {
            "AZURE_OPENAI_API_KEY": "az-key",
            "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
        }, clear=True):
            registry = auto_setup_providers()
            assert "azure" in registry.available_providers()

    def test_azure_not_registered_without_endpoint(self):
        """Azure should NOT be registered when endpoint is missing."""
        with patch.dict(os.environ, {
            "AZURE_OPENAI_API_KEY": "az-key",
        }, clear=True):
            registry = auto_setup_providers()
            assert "azure" not in registry._providers

    def test_azure_not_registered_without_key(self):
        """Azure should NOT be registered when key is missing."""
        with patch.dict(os.environ, {
            "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
        }, clear=True):
            registry = auto_setup_providers()
            assert "azure" not in registry._providers

    def test_together_not_registered_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = auto_setup_providers()
            assert "together" not in registry._providers

    def test_groq_not_registered_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = auto_setup_providers()
            assert "groq" not in registry._providers

    def test_mistral_not_registered_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = auto_setup_providers()
            assert "mistral" not in registry._providers

    def test_openrouter_not_registered_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            registry = auto_setup_providers()
            assert "openrouter" not in registry._providers

    def test_all_new_providers_together(self):
        env = {
            "TOGETHER_API_KEY": "sk-t",
            "GROQ_API_KEY": "sk-g",
            "MISTRAL_API_KEY": "sk-m",
            "OPENROUTER_API_KEY": "sk-o",
            "AZURE_OPENAI_API_KEY": "az-k",
            "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
        }
        with patch.dict(os.environ, env, clear=True):
            registry = auto_setup_providers()
            available = registry.available_providers()
            assert "together" in available
            assert "groq" in available
            assert "mistral" in available
            assert "openrouter" in available
            assert "azure" in available
            # ollama is always registered
            assert "ollama" in available


# ══════════════════════════════════════════════════════════════════════
# Edge cases and integration scenarios
# ══════════════════════════════════════════════════════════════════════

class TestProviderRegistryEdgeCases:
    """Edge cases for ProviderRegistry."""

    def test_register_overwrite_existing_name(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-old"))
        p2 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-new"))
        registry.register("test", p1)
        registry.register("test", p2)
        assert registry._providers["test"] is p2

    def test_register_overwrite_does_not_change_default_unless_first(self):
        registry = ProviderRegistry()
        p1 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-1"))
        p2 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-2"))
        registry.register("a", p1)
        registry.register("b", p2)
        assert registry._default_provider == "a"
        # Overwrite "b" with a new provider — default should stay "a"
        p3 = OpenAIProvider(ProviderConfig(provider="openai", api_key="sk-3"))
        registry.register("b", p3)
        assert registry._default_provider == "a"


class TestProviderResultStringRepresentation:
    """Tests for ProviderResult __repr__ (from dataclass)."""

    def test_repr_includes_content(self):
        result = ProviderResult(
            content="Hello", model="gpt-4o", provider="openai",
        )
        repr_str = repr(result)
        assert "Hello" in repr_str
        assert "gpt-4o" in repr_str
        assert "openai" in repr_str

    def test_repr_includes_defaults(self):
        result = ProviderResult(content="x", model="m", provider="p")
        repr_str = repr(result)
        assert "latency_ms=0.0" in repr_str or "latency_ms=0" in repr_str
