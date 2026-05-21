"""
Unit tests for panda.dispatch — data classes, enums, exceptions,
CircuitBreaker constructor, and PandaDispatcher constructor/registration.

Tests are focused on constructors and pure (non-async) methods.
No async dispatch calls are exercised.
"""
import os

import pytest

from panda.dispatch import (
    # Enums
    CircuitBreakerState,
    # Data classes
    ProviderProfile,
    Usage,
    DispatchResult,
    StreamChunk,
    # Circuit breaker
    CircuitBreaker,
    # Exceptions
    DispatchError,
    CircuitBreakerOpenError,
    ProviderNotFoundError,
    AllProvidersFailedError,
    # Dispatcher
    PandaDispatcher,
)


# ════════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_profile():
    """A minimal ProviderProfile for testing."""
    return ProviderProfile(
        name="legal-gpt4",
        provider="openai",
        model="gpt-4-turbo",
        api_key_env="OPENAI_API_KEY",
    )


@pytest.fixture
def sample_messages():
    """A minimal messages list."""
    return [{"role": "user", "content": "What is contract law?"}]


# ════════════════════════════════════════════════════════════════════════
# CircuitBreakerState
# ════════════════════════════════════════════════════════════════════════


class TestCircuitBreakerState:
    """Tests for the CircuitBreakerState enum."""

    def test_all_three_values_exist(self):
        """All three circuit states should be defined."""
        assert CircuitBreakerState.CLOSED is not None
        assert CircuitBreakerState.OPEN is not None
        assert CircuitBreakerState.HALF_OPEN is not None

    def test_values_are_strings(self):
        """Each enum member should map to the correct string value."""
        assert CircuitBreakerState.CLOSED.value == "closed"
        assert CircuitBreakerState.OPEN.value == "open"
        assert CircuitBreakerState.HALF_OPEN.value == "half_open"

    def test_enum_length_is_three(self):
        """There should be exactly 3 states."""
        assert len(CircuitBreakerState) == 3

    def test_enum_membership(self):
        """Members should be instances of CircuitBreakerState."""
        assert isinstance(CircuitBreakerState.CLOSED, CircuitBreakerState)
        assert isinstance(CircuitBreakerState.OPEN, CircuitBreakerState)
        assert isinstance(CircuitBreakerState.HALF_OPEN, CircuitBreakerState)


# ════════════════════════════════════════════════════════════════════════
# ProviderProfile
# ════════════════════════════════════════════════════════════════════════


class TestProviderProfile:
    """Tests for the ProviderProfile dataclass."""

    def test_creation_with_required_fields(self, sample_profile):
        """ProviderProfile should be created with required fields only."""
        assert sample_profile.name == "legal-gpt4"
        assert sample_profile.provider == "openai"
        assert sample_profile.model == "gpt-4-turbo"
        assert sample_profile.api_key_env == "OPENAI_API_KEY"

    def test_defaults(self):
        """Verify default values for optional fields."""
        p = ProviderProfile(
            name="test",
            provider="openai",
            model="gpt-4",
            api_key_env="API_KEY",
        )
        assert p.base_url is None
        assert p.system_prompt == ""
        assert p.timeout == 60.0
        assert p.max_retries == 2

    def test_base_url_none_by_default(self):
        """base_url should be None when not provided."""
        p = ProviderProfile(
            name="deepseek-v3",
            provider="deepseek",
            model="deepseek-chat",
            api_key_env="DEEPSEEK_KEY",
        )
        assert p.base_url is None

    def test_creation_with_all_fields(self):
        """ProviderProfile should accept all fields."""
        p = ProviderProfile(
            name="custom",
            provider="anthropic",
            model="claude-4",
            api_key_env="ANTHROPIC_KEY",
            base_url="https://api.example.com/v1",
            system_prompt="You are a helpful assistant.",
            timeout=120.0,
            max_retries=5,
        )
        assert p.name == "custom"
        assert p.provider == "anthropic"
        assert p.model == "claude-4"
        assert p.api_key_env == "ANTHROPIC_KEY"
        assert p.base_url == "https://api.example.com/v1"
        assert p.system_prompt == "You are a helpful assistant."
        assert p.timeout == 120.0
        assert p.max_retries == 5

    def test_is_dataclass_instance(self, sample_profile):
        """ProviderProfile should be a dataclass instance."""
        from dataclasses import is_dataclass
        assert is_dataclass(sample_profile)

    def test_equality(self):
        """Two profiles with the same fields should be equal."""
        a = ProviderProfile(name="x", provider="o", model="g", api_key_env="K")
        b = ProviderProfile(name="x", provider="o", model="g", api_key_env="K")
        assert a == b

    def test_inequality(self):
        """Profiles with different fields should not be equal."""
        a = ProviderProfile(name="x", provider="o", model="g", api_key_env="K")
        b = ProviderProfile(name="y", provider="o", model="g", api_key_env="K")
        assert a != b


# ════════════════════════════════════════════════════════════════════════
# Usage
# ════════════════════════════════════════════════════════════════════════


class TestUsage:
    """Tests for the Usage dataclass."""

    def test_defaults(self):
        """All Usage fields should default to 0."""
        u = Usage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_creation_with_values(self):
        """Usage should store provided token counts."""
        u = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert u.prompt_tokens == 100
        assert u.completion_tokens == 50
        assert u.total_tokens == 150

    def test_from_openai_with_valid_object(self):
        """from_openai should extract token counts from an OpenAI usage object."""

        class MockUsage:
            prompt_tokens = 200
            completion_tokens = 80
            total_tokens = 280

        u = Usage.from_openai(MockUsage())
        assert u.prompt_tokens == 200
        assert u.completion_tokens == 80
        assert u.total_tokens == 280

    def test_from_openai_with_none(self):
        """from_openai with None should return a zeroed Usage."""
        u = Usage.from_openai(None)
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_from_openai_with_partial_usage(self):
        """from_openai should handle objects with only some attributes."""

        class PartialUsage:
            prompt_tokens = 50

        u = Usage.from_openai(PartialUsage())
        assert u.prompt_tokens == 50
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_from_openai_with_empty_object(self):
        """from_openai with an object that has no token attrs should return zeros."""

        class Empty:
            pass

        u = Usage.from_openai(Empty())
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0


# ════════════════════════════════════════════════════════════════════════
# DispatchResult
# ════════════════════════════════════════════════════════════════════════


class TestDispatchResult:
    """Tests for the DispatchResult dataclass."""

    def test_creation_minimal(self):
        """DispatchResult should create with only required fields."""
        r = DispatchResult(
            industry="legal",
            provider="legal-gpt4",
            model="gpt-4-turbo",
            content="Contract law is...",
        )
        assert r.industry == "legal"
        assert r.provider == "legal-gpt4"
        assert r.model == "gpt-4-turbo"
        assert r.content == "Contract law is..."

    def test_defaults(self):
        """Verify default values on DispatchResult."""
        r = DispatchResult(
            industry="medical",
            provider="med-claude",
            model="claude-4",
            content="Diagnosis...",
        )
        assert isinstance(r.usage, Usage)
        assert r.usage.total_tokens == 0
        assert r.latency_ms == 0.0
        assert r.knowledge_used is False
        assert r.fallback_used is False
        assert r.streamed is False

    def test_creation_with_usage(self):
        """DispatchResult should accept a Usage object."""
        u = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        r = DispatchResult(
            industry="legal",
            provider="p",
            model="m",
            content="c",
            usage=u,
        )
        assert r.usage is u
        assert r.usage.total_tokens == 15

    def test_creation_with_all_fields(self):
        """DispatchResult should accept all fields including bool flags."""
        u = Usage(total_tokens=42)
        r = DispatchResult(
            industry="finance",
            provider="fin-gpt",
            model="gpt-4",
            content="Analysis...",
            usage=u,
            latency_ms=1234.5,
            knowledge_used=True,
            fallback_used=True,
            streamed=False,
        )
        assert r.industry == "finance"
        assert r.latency_ms == 1234.5
        assert r.knowledge_used is True
        assert r.fallback_used is True
        assert r.streamed is False


# ════════════════════════════════════════════════════════════════════════
# StreamChunk
# ════════════════════════════════════════════════════════════════════════


class TestStreamChunk:
    """Tests for the StreamChunk dataclass."""

    def test_creation_minimal(self):
        """StreamChunk should require only content."""
        c = StreamChunk(content="Hello")
        assert c.content == "Hello"

    def test_defaults(self):
        """Optional fields should default to None."""
        c = StreamChunk(content="token")
        assert c.finish_reason is None
        assert c.usage is None

    def test_creation_with_finish_reason(self):
        """StreamChunk should accept a finish_reason."""
        c = StreamChunk(content="done", finish_reason="stop")
        assert c.finish_reason == "stop"

    def test_creation_with_usage(self):
        """StreamChunk should accept a Usage object."""
        u = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        c = StreamChunk(content="", finish_reason="stop", usage=u)
        assert c.usage is u
        assert c.usage.total_tokens == 30

    def test_creation_with_both_optional_fields(self):
        """StreamChunk should accept both finish_reason and usage."""
        u = Usage(total_tokens=100)
        c = StreamChunk(content="final", finish_reason="length", usage=u)
        assert c.content == "final"
        assert c.finish_reason == "length"
        assert c.usage.total_tokens == 100

    def test_empty_content_allowed(self):
        """StreamChunk should allow empty content (e.g., usage-only chunks)."""
        c = StreamChunk(content="")
        assert c.content == ""


# ════════════════════════════════════════════════════════════════════════
# DispatchError Hierarchy
# ════════════════════════════════════════════════════════════════════════


class TestDispatchErrorHierarchy:
    """Tests for the dispatch exception hierarchy."""

    def test_dispatch_error_is_base(self):
        """DispatchError should be an Exception subclass."""
        assert issubclass(DispatchError, Exception)

    def test_circuit_breaker_open_error_is_dispatch_error(self):
        """CircuitBreakerOpenError should be a DispatchError."""
        assert issubclass(CircuitBreakerOpenError, DispatchError)

    def test_provider_not_found_error_is_dispatch_error(self):
        """ProviderNotFoundError should be a DispatchError."""
        assert issubclass(ProviderNotFoundError, DispatchError)

    def test_all_providers_failed_error_is_dispatch_error(self):
        """AllProvidersFailedError should be a DispatchError."""
        assert issubclass(AllProvidersFailedError, DispatchError)

    def test_circuit_breaker_open_error_attributes(self):
        """CircuitBreakerOpenError should store provider_name and retry_after."""
        exc = CircuitBreakerOpenError("test-provider", 42.5)
        assert exc.provider_name == "test-provider"
        assert exc.retry_after == 42.5

    def test_circuit_breaker_open_error_message(self):
        """CircuitBreakerOpenError message should include provider and retry info."""
        exc = CircuitBreakerOpenError("my-provider", 30.0)
        msg = str(exc)
        assert "Circuit breaker OPEN" in msg
        assert "my-provider" in msg
        assert "30.0s" in msg

    def test_provider_not_found_error_empty(self):
        """ProviderNotFoundError should be instantiable without a message."""
        exc = ProviderNotFoundError()
        assert isinstance(exc, DispatchError)
        assert isinstance(exc, ProviderNotFoundError)

    def test_provider_not_found_error_with_message(self):
        """ProviderNotFoundError should carry a custom message."""
        exc = ProviderNotFoundError("No provider for 'legal'")
        assert "No provider for 'legal'" in str(exc)

    def test_all_providers_failed_error_with_message(self):
        """AllProvidersFailedError should carry a custom message."""
        exc = AllProvidersFailedError("Both providers failed")
        assert "Both providers failed" in str(exc)

    def test_all_providers_failed_error_with_cause(self):
        """AllProvidersFailedError should support chaining from another exception."""
        cause = ValueError("original error")
        exc = AllProvidersFailedError("fail") 
        exc.__cause__ = cause  # simulate `raise X from cause`
        assert exc.__cause__ is cause

    def test_dispatch_error_can_be_raised_and_caught(self):
        """DispatchError should be raisable and catchable."""
        with pytest.raises(DispatchError):
            raise DispatchError("test error")

    def test_circuit_breaker_open_error_is_caught_by_dispatch_error(self):
        """CircuitBreakerOpenError should be catchable as DispatchError."""
        try:
            raise CircuitBreakerOpenError("p", 10.0)
        except DispatchError:
            pass  # expected
        else:
            pytest.fail("CircuitBreakerOpenError was not caught by DispatchError")


# ════════════════════════════════════════════════════════════════════════
# CircuitBreaker (constructor only)
# ════════════════════════════════════════════════════════════════════════


class TestCircuitBreaker:
    """Tests for the CircuitBreaker class — constructor and internal state only."""

    def test_constructor_defaults(self):
        """Default constructor should set standard thresholds."""
        cb = CircuitBreaker()
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 60.0

    def test_constructor_custom_thresholds(self):
        """Constructor should accept custom failure_threshold and recovery_timeout."""
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 30.0

    def test_constructor_initializes_circuits_empty(self):
        """_circuits dict should start empty."""
        cb = CircuitBreaker()
        assert cb._circuits == {}

    def test_constructor_creates_lock(self):
        """_lock should be an asyncio.Lock."""
        import asyncio
        cb = CircuitBreaker()
        assert isinstance(cb._lock, asyncio.Lock)

    def test_constructor_with_zero_threshold(self):
        """Zero failure_threshold should be accepted."""
        cb = CircuitBreaker(failure_threshold=0)
        assert cb.failure_threshold == 0

    def test_constructor_with_zero_recovery(self):
        """Zero recovery_timeout should be accepted."""
        cb = CircuitBreaker(recovery_timeout=0.0)
        assert cb.recovery_timeout == 0.0


# ════════════════════════════════════════════════════════════════════════
# PandaDispatcher (constructor + registration, NO async dispatch)
# ════════════════════════════════════════════════════════════════════════


class TestPandaDispatcherConstructor:
    """Tests for PandaDispatcher constructor and configuration."""

    def test_constructor_defaults(self):
        """Default constructor should set expected fields."""
        d = PandaDispatcher()
        assert d._registry == {}
        assert d._clients == {}
        assert d._fallback_profile is None
        assert d._fallback_client is None
        assert d._default_timeout == 60.0
        assert isinstance(d._circuit_breaker, CircuitBreaker)
        assert d._circuit_breaker.failure_threshold == 3
        assert d._circuit_breaker.recovery_timeout == 60.0

    def test_constructor_custom_circuit_params(self):
        """Custom circuit breaker params should flow through."""
        d = PandaDispatcher(
            circuit_failure_threshold=5,
            circuit_recovery_timeout=30.0,
        )
        assert d._circuit_breaker.failure_threshold == 5
        assert d._circuit_breaker.recovery_timeout == 30.0

    def test_constructor_custom_default_timeout(self):
        """Custom default_timeout should be stored."""
        d = PandaDispatcher(default_timeout=120.0)
        assert d._default_timeout == 120.0

    def test_constructor_creates_lock(self):
        """PandaDispatcher should create an asyncio.Lock."""
        import asyncio
        d = PandaDispatcher()
        assert isinstance(d._lock, asyncio.Lock)


class TestPandaDispatcherRegistry:
    """Tests for PandaDispatcher register, unregister, and related methods."""

    def test_register_provider(self, sample_profile):
        """Registering a provider should store it in _registry."""
        d = PandaDispatcher()
        d.register("legal", sample_profile)
        assert "legal" in d._registry
        assert d._registry["legal"] is sample_profile

    def test_register_provider_clears_client_cache(self, sample_profile):
        """Registering should pop any cached client for that profile name."""
        d = PandaDispatcher()
        # Simulate a cached client
        d._clients["legal-gpt4"] = object()
        d.register("legal", sample_profile)
        assert "legal-gpt4" not in d._clients

    def test_register_overwrites_existing(self):
        """Registering the same industry twice should replace the profile."""
        d = PandaDispatcher()
        p1 = ProviderProfile(name="a", provider="o", model="m1", api_key_env="K")
        p2 = ProviderProfile(name="b", provider="o", model="m2", api_key_env="K")
        d.register("legal", p1)
        d.register("legal", p2)
        assert d._registry["legal"] is p2

    def test_unregister_provider(self, sample_profile):
        """Unregistering should remove a registered provider."""
        d = PandaDispatcher()
        d.register("legal", sample_profile)
        d.unregister("legal")
        assert "legal" not in d._registry

    def test_unregister_clears_client_cache(self, sample_profile):
        """Unregistering should also pop the cached client."""
        d = PandaDispatcher()
        d.register("legal", sample_profile)
        d._clients["legal-gpt4"] = object()
        d.unregister("legal")
        assert "legal-gpt4" not in d._clients

    def test_unregister_nonexistent_no_error(self):
        """Unregistering a non-existent industry should not raise."""
        d = PandaDispatcher()
        d.unregister("nonexistent")  # should not raise

    def test_set_fallback(self, sample_profile):
        """set_fallback should store the profile and reset the fallback client."""
        d = PandaDispatcher()
        d._fallback_client = object()  # simulate cached client
        d.set_fallback(sample_profile)
        assert d._fallback_profile is sample_profile
        assert d._fallback_client is None

    def test_registered_industries_empty(self):
        """registered_industries should return empty list initially."""
        d = PandaDispatcher()
        assert d.registered_industries == []

    def test_registered_industries_with_providers(self, sample_profile):
        """registered_industries should return all registered keys."""
        d = PandaDispatcher()
        p2 = ProviderProfile(name="med", provider="o", model="m", api_key_env="K")
        d.register("legal", sample_profile)
        d.register("medical", p2)
        assert set(d.registered_industries) == {"legal", "medical"}

    def test_get_profile_returns_profile(self, sample_profile):
        """get_profile should return the registered profile for an industry."""
        d = PandaDispatcher()
        d.register("legal", sample_profile)
        assert d.get_profile("legal") is sample_profile

    def test_get_profile_returns_none_for_unknown(self):
        """get_profile should return None for unknown industries."""
        d = PandaDispatcher()
        assert d.get_profile("nonexistent") is None

    def test_get_profile_returns_none_after_unregister(self, sample_profile):
        """get_profile should return None after unregistering."""
        d = PandaDispatcher()
        d.register("legal", sample_profile)
        d.unregister("legal")
        assert d.get_profile("legal") is None


class TestPandaDispatcherGetApiKey:
    """Tests for the _get_api_key static method."""

    def test_raises_dispatch_error_when_env_not_set(self, monkeypatch, sample_profile):
        """_get_api_key should raise DispatchError when env var is not set."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(DispatchError) as exc_info:
            PandaDispatcher._get_api_key(sample_profile)
        assert "OPENAI_API_KEY" in str(exc_info.value)
        assert "legal-gpt4" in str(exc_info.value)

    def test_raises_dispatch_error_for_unset_custom_env(self, monkeypatch):
        """_get_api_key should raise for any unset env var."""
        profile = ProviderProfile(
            name="custom",
            provider="o",
            model="m",
            api_key_env="MY_CUSTOM_KEY",
        )
        monkeypatch.delenv("MY_CUSTOM_KEY", raising=False)
        with pytest.raises(DispatchError) as exc_info:
            PandaDispatcher._get_api_key(profile)
        assert "MY_CUSTOM_KEY" in str(exc_info.value)

    def test_returns_value_when_env_set(self, monkeypatch, sample_profile):
        """_get_api_key should return the env var value when set."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-12345")
        key = PandaDispatcher._get_api_key(sample_profile)
        assert key == "sk-test-12345"

    def test_returns_empty_string_when_env_is_empty(self, monkeypatch, sample_profile):
        """_get_api_key with empty string env var should raise DispatchError."""
        monkeypatch.setenv("OPENAI_API_KEY", "")
        with pytest.raises(DispatchError):
            PandaDispatcher._get_api_key(sample_profile)


class TestPandaDispatcherBuildMessages:
    """Tests for the _build_messages static method."""

    def test_no_system_prompt(self, sample_messages):
        """With empty system_prompt and no knowledge, no system message added."""
        result = PandaDispatcher._build_messages(
            messages=sample_messages,
            system_prompt="",
            knowledge=None,
        )
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "What is contract law?"

    def test_with_system_prompt(self, sample_messages):
        """System prompt should be added as a system message."""
        result = PandaDispatcher._build_messages(
            messages=sample_messages,
            system_prompt="You are a legal expert.",
            knowledge=None,
        )
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a legal expert."
        assert result[1]["role"] == "user"

    def test_with_system_and_knowledge(self, sample_messages):
        """Knowledge should be appended to the system prompt."""
        result = PandaDispatcher._build_messages(
            messages=sample_messages,
            system_prompt="You are a legal expert.",
            knowledge="Contract law is governed by common law principles.",
        )
        assert len(result) == 2
        assert result[0]["role"] == "system"
        content = result[0]["content"]
        assert "You are a legal expert." in content
        assert "## Reference Knowledge" in content
        assert "Contract law is governed by common law principles." in content

    def test_with_knowledge_no_system_prompt(self, sample_messages):
        """Knowledge should still be added even without a system prompt."""
        result = PandaDispatcher._build_messages(
            messages=sample_messages,
            system_prompt="",
            knowledge="Important context.",
        )
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "\n\n## Reference Knowledge\nImportant context."

    def test_empty_messages(self):
        """Should work with empty messages list."""
        result = PandaDispatcher._build_messages(
            messages=[],
            system_prompt="You are helpful.",
            knowledge=None,
        )
        assert len(result) == 1
        assert result[0]["role"] == "system"

    def test_empty_messages_and_empty_system_and_no_knowledge(self):
        """With everything empty, result should be an empty list."""
        result = PandaDispatcher._build_messages(
            messages=[],
            system_prompt="",
            knowledge=None,
        )
        assert result == []

    def test_does_not_mutate_input_messages(self, sample_messages):
        """_build_messages should not modify the input messages list."""
        original = [dict(sample_messages[0])]
        PandaDispatcher._build_messages(
            messages=original,
            system_prompt="You are helpful.",
            knowledge="Some context.",
        )
        # Original messages should be unchanged
        assert original == sample_messages

    def test_multiple_user_messages(self):
        """Multiple messages should all be preserved."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        result = PandaDispatcher._build_messages(
            messages=messages,
            system_prompt="System",
            knowledge=None,
        )
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"
        assert result[3]["role"] == "user"


class TestPandaDispatcherRetryConstants:
    """Tests for PandaDispatcher retry-related class constants."""

    def test_retry_max_attempts(self):
        """RETRY_MAX_ATTEMPTS should have the expected value."""
        assert PandaDispatcher.RETRY_MAX_ATTEMPTS == 3

    def test_retry_min_wait(self):
        """RETRY_MIN_WAIT should be 1.0."""
        assert PandaDispatcher.RETRY_MIN_WAIT == 1.0

    def test_retry_max_wait(self):
        """RETRY_MAX_WAIT should be 10.0."""
        assert PandaDispatcher.RETRY_MAX_WAIT == 10.0


class TestPandaDispatcherModuleExports:
    """Verify that all expected symbols are exported from panda.dispatch."""

    def test_main_classes_are_importable(self):
        """All core classes should be importable from panda.dispatch."""
        from panda.dispatch import (
            PandaDispatcher,
            ProviderProfile,
            DispatchResult,
            StreamChunk,
            Usage,
            CircuitBreakerState,
            CircuitBreaker,
            DispatchError,
            CircuitBreakerOpenError,
            ProviderNotFoundError,
            AllProvidersFailedError,
        )
        # If we reach here, all imports succeeded
        assert True
