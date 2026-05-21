"""
Comprehensive unit tests for panda.explorer module.

Tests cover:
  - ExploreStrategy enum
  - ExplorerConfig dataclass (creation, defaults, to_provider_profile)
  - ExplorationResult dataclass (creation, defaults, conversion)
  - ExplorerEnsemble (constructor, registration, unregistration, lookup)

All tests are pure synchronous — no async explore() calls.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from panda.explorer import (
    ExploreStrategy,
    ExplorerConfig,
    ExplorationResult,
    ExplorerEnsemble,
)
from panda.dispatch import ProviderProfile


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_dispatcher() -> MagicMock:
    """Return a PandaDispatcher mock with register/unregister methods."""
    dispatcher = MagicMock()
    dispatcher.register = MagicMock()
    dispatcher.unregister = MagicMock()
    return dispatcher


@pytest.fixture
def mock_guard() -> MagicMock:
    """Return an AntiLoopGuard mock."""
    return MagicMock()


@pytest.fixture
def mock_cost() -> MagicMock:
    """Return a CostOptimizer mock."""
    return MagicMock()


@pytest.fixture
def ensemble(mock_dispatcher, mock_guard, mock_cost) -> ExplorerEnsemble:
    """Create a fresh ExplorerEnsemble with mocked dependencies."""
    return ExplorerEnsemble(mock_dispatcher, mock_guard, mock_cost)


# ══════════════════════════════════════════════════════════════════════
# ExploreStrategy Enum Tests
# ══════════════════════════════════════════════════════════════════════

class TestExploreStrategy:
    """Tests for the ExploreStrategy enum."""

    def test_has_five_values(self):
        """ExploreStrategy should have exactly 5 members."""
        members = list(ExploreStrategy)
        assert len(members) == 5

    def test_all_values_are_strings(self):
        """Each ExploreStrategy value should be a string."""
        for strategy in ExploreStrategy:
            assert isinstance(strategy.value, str)
            assert len(strategy.value) > 0

    def test_known_values_present(self):
        """All expected strategy names exist."""
        assert ExploreStrategy.PARALLEL.value == "parallel"
        assert ExploreStrategy.SEQUENTIAL.value == "sequential"
        assert ExploreStrategy.DIVERSE.value == "diverse"
        assert ExploreStrategy.DEPTH_FIRST.value == "depth_first"
        assert ExploreStrategy.BREADTH_FIRST.value == "breadth_first"

    def test_can_construct_from_value(self):
        """ExploreStrategy(value) should work for all valid values."""
        assert ExploreStrategy("parallel") == ExploreStrategy.PARALLEL
        assert ExploreStrategy("sequential") == ExploreStrategy.SEQUENTIAL
        assert ExploreStrategy("diverse") == ExploreStrategy.DIVERSE
        assert ExploreStrategy("depth_first") == ExploreStrategy.DEPTH_FIRST
        assert ExploreStrategy("breadth_first") == ExploreStrategy.BREADTH_FIRST

    def test_invalid_value_raises(self):
        """Constructing from an invalid value should raise ValueError."""
        with pytest.raises(ValueError):
            ExploreStrategy("nonexistent")

    def test_strategy_ordering(self):
        """Enum members should be iterable in definition order."""
        names = [s.name for s in ExploreStrategy]
        assert names == ["PARALLEL", "SEQUENTIAL", "DIVERSE", "DEPTH_FIRST", "BREADTH_FIRST"]


# ══════════════════════════════════════════════════════════════════════
# ExplorerConfig Dataclass Tests
# ══════════════════════════════════════════════════════════════════════

class TestExplorerConfig:
    """Tests for the ExplorerConfig dataclass."""

    def test_creation_with_required_fields(self):
        """ExplorerConfig can be created with only required fields."""
        cfg = ExplorerConfig(
            name="test_explorer",
            model="test-model",
            system_prompt="You are a test assistant.",
        )
        assert cfg.name == "test_explorer"
        assert cfg.model == "test-model"
        assert cfg.system_prompt == "You are a test assistant."

    def test_default_values(self):
        """ExplorerConfig should have correct defaults for optional fields."""
        cfg = ExplorerConfig(
            name="default_test",
            model="default-model",
            system_prompt="System prompt.",
        )
        assert cfg.perspective == ""
        assert cfg.provider == "openai"
        assert cfg.api_key_env == "OPENAI_API_KEY"
        assert cfg.base_url is None
        assert cfg.max_tokens == 1024
        assert cfg.temperature == 0.7

    def test_custom_values(self):
        """ExplorerConfig should accept and store all custom values."""
        cfg = ExplorerConfig(
            name="custom_explorer",
            model="custom-model-v2",
            system_prompt="Custom system prompt.",
            perspective="custom_perspective",
            provider="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            base_url="https://api.custom.com/v1",
            max_tokens=2048,
            temperature=0.3,
        )
        assert cfg.name == "custom_explorer"
        assert cfg.model == "custom-model-v2"
        assert cfg.system_prompt == "Custom system prompt."
        assert cfg.perspective == "custom_perspective"
        assert cfg.provider == "anthropic"
        assert cfg.api_key_env == "ANTHROPIC_API_KEY"
        assert cfg.base_url == "https://api.custom.com/v1"
        assert cfg.max_tokens == 2048
        assert cfg.temperature == 0.3

    def test_to_provider_profile_basic(self):
        """to_provider_profile() should return a ProviderProfile with correct fields."""
        cfg = ExplorerConfig(
            name="金融视角",
            model="deepseek-chat",
            system_prompt="金融分析系统提示",
            perspective="金融分析",
            provider="deepseek",
            api_key_env="DEEPSEEK_API_KEY",
        )
        profile = cfg.to_provider_profile()
        assert isinstance(profile, ProviderProfile)
        assert profile.name == "explorer:金融视角"
        assert profile.provider == "deepseek"
        assert profile.model == "deepseek-chat"
        assert profile.api_key_env == "DEEPSEEK_API_KEY"
        assert profile.system_prompt == "金融分析系统提示"
        assert profile.timeout == 30.0

    def test_to_provider_profile_with_base_url(self):
        """to_provider_profile() should propagate base_url."""
        cfg = ExplorerConfig(
            name="custom",
            model="custom-model",
            system_prompt="Custom prompt.",
            base_url="https://custom.endpoint/v1",
        )
        profile = cfg.to_provider_profile()
        assert profile.base_url == "https://custom.endpoint/v1"

    def test_equality(self):
        """Two ExplorerConfigs with identical fields should be equal."""
        cfg1 = ExplorerConfig(name="a", model="m", system_prompt="s")
        cfg2 = ExplorerConfig(name="a", model="m", system_prompt="s")
        assert cfg1 == cfg2

    def test_inequality(self):
        """Two ExplorerConfigs with different fields should not be equal."""
        cfg1 = ExplorerConfig(name="a", model="m", system_prompt="s")
        cfg2 = ExplorerConfig(name="b", model="m", system_prompt="s")
        assert cfg1 != cfg2

    def test_is_dataclass(self):
        """ExplorerConfig should be a dataclass."""
        assert dataclasses.is_dataclass(ExplorerConfig)

    def test_repr_includes_fields(self):
        """repr() should include key field values."""
        cfg = ExplorerConfig(name="test", model="m1", system_prompt="prompt")
        r = repr(cfg)
        assert "test" in r
        assert "m1" in r


# ══════════════════════════════════════════════════════════════════════
# ExplorationResult Dataclass Tests
# ══════════════════════════════════════════════════════════════════════

class TestExplorationResult:
    """Tests for the ExplorationResult dataclass."""

    def test_creation_with_required_fields(self):
        """ExplorationResult can be created with only required fields."""
        result = ExplorationResult(
            explorer_name="金融视角",
            model_used="deepseek-chat",
        )
        assert result.explorer_name == "金融视角"
        assert result.model_used == "deepseek-chat"

    def test_default_values(self):
        """ExplorationResult should have correct defaults for optional fields."""
        result = ExplorationResult(explorer_name="test", model_used="m")
        assert result.findings == []
        assert result.approach == ""
        assert result.confidence == 0.5
        assert result.references == []
        assert result.caveats == ""
        assert result.cost_usd == 0.0
        assert result.raw_content == ""
        assert result.latency_ms == 0.0

    def test_full_creation(self):
        """ExplorationResult should accept all fields."""
        result = ExplorationResult(
            explorer_name="法律视角",
            model_used="deepseek-chat",
            findings=["关键发现1", "关键发现2", "关键发现3"],
            approach="法律分析",
            confidence=0.85,
            references=["《合同法》第52条", "最高人民法院判例"],
            caveats="本分析仅供参考，不构成法律意见",
            cost_usd=0.02,
            raw_content="完整的模型回复内容...",
            latency_ms=1234.5,
        )
        assert result.explorer_name == "法律视角"
        assert result.model_used == "deepseek-chat"
        assert len(result.findings) == 3
        assert result.approach == "法律分析"
        assert result.confidence == 0.85
        assert len(result.references) == 2
        assert "仅供参考" in result.caveats
        assert result.cost_usd == 0.02
        assert result.raw_content == "完整的模型回复内容..."
        assert result.latency_ms == 1234.5

    def test_asdict(self):
        """dataclasses.asdict() should produce correct dictionary."""
        result = ExplorationResult(
            explorer_name="test",
            model_used="model-x",
            findings=["f1"],
            confidence=0.75,
            cost_usd=0.01,
        )
        d = dataclasses.asdict(result)
        assert d["explorer_name"] == "test"
        assert d["model_used"] == "model-x"
        assert d["findings"] == ["f1"]
        assert d["confidence"] == 0.75
        assert d["cost_usd"] == 0.01
        # Defaults should also be present
        assert d["approach"] == ""
        assert d["caveats"] == ""
        assert d["latency_ms"] == 0.0

    def test_is_dataclass(self):
        """ExplorationResult should be a dataclass."""
        assert dataclasses.is_dataclass(ExplorationResult)

    def test_mutable_defaults_independent(self):
        """Each instance should get its own list defaults (not shared)."""
        r1 = ExplorationResult(explorer_name="r1", model_used="m1")
        r2 = ExplorationResult(explorer_name="r2", model_used="m2")
        r1.findings.append("f1")
        assert r2.findings == []  # r2 unaffected
        r2.references.append("ref1")
        assert r1.references == []  # r1 unaffected

    def test_confidence_clamped_in_creation(self):
        """Confidence should preserve the value as-is (no auto-clamping)."""
        # Note: The dataclass does not clamp; just stores the value.
        result = ExplorationResult(
            explorer_name="t", model_used="m", confidence=1.5
        )
        assert result.confidence == 1.5  # raw value stored


# ══════════════════════════════════════════════════════════════════════
# ExplorerEnsemble Tests
# ══════════════════════════════════════════════════════════════════════

class TestExplorerEnsembleConstructor:
    """Tests for ExplorerEnsemble.__init__ and basic state."""

    def test_constructor_stores_dependencies(self, mock_dispatcher, mock_guard, mock_cost):
        """The constructor should store dispatcher, guard, and cost."""
        ens = ExplorerEnsemble(mock_dispatcher, mock_guard, mock_cost)
        assert ens.dispatcher is mock_dispatcher
        assert ens.guard is mock_guard
        assert ens.cost is mock_cost

    def test_constructor_registers_builtin_explorers(self, ensemble):
        """After construction, all 5 built-in explorers should be registered."""
        explorers = ensemble.registered_explorers
        for industry in ["finance", "medical", "legal", "education", "general"]:
            assert industry in explorers, f"Missing built-in explorer: {industry}"

    def test_constructor_registers_builtins_with_dispatcher(
        self, mock_dispatcher, mock_guard, mock_cost
    ):
        """Constructor should call dispatcher.register for each built-in."""
        ExplorerEnsemble(mock_dispatcher, mock_guard, mock_cost)
        # 5 built-in explorers + 1 synthesizer = 6 register calls
        assert mock_dispatcher.register.call_count == 6

    def test_constructor_initial_session_id_empty(self, ensemble):
        """_session_id should start as empty string."""
        assert ensemble._session_id == ""

    def test_constructor_initial_strategy_none(self, ensemble):
        """_current_strategy should start as None."""
        assert ensemble._current_strategy is None

    def test_constructor_registered_explorers_count(self, ensemble):
        """registered_explorers should return exactly 5 keys after init."""
        assert len(ensemble.registered_explorers) == 5

    def test_class_constants(self, ensemble):
        """ExplorerEnsemble has correct class-level constants."""
        assert ensemble.EXPLORER_TIMEOUT == 30.0
        assert ensemble.SYNTHESIZER_MODEL == "deepseek-reasoner"
        assert ensemble.SYNTHESIZER_PROVIDER == "deepseek"
        assert ensemble.SYNTHESIZER_NAME == "synthesizer"


class TestExplorerEnsembleRegistration:
    """Tests for register_explorer / unregister_explorer."""

    def test_register_new_explorer(self, ensemble):
        """register_explorer should add a new explorer to the registry."""
        cfg = ExplorerConfig(
            name="custom_exp",
            model="custom-model",
            system_prompt="Custom prompt.",
        )
        initial_count = len(ensemble.registered_explorers)
        ensemble.register_explorer("custom", cfg)
        assert len(ensemble.registered_explorers) == initial_count + 1
        assert "custom" in ensemble.registered_explorers

    def test_register_explorer_calls_dispatcher(self, ensemble, mock_dispatcher):
        """register_explorer should register with dispatcher."""
        call_count_before = mock_dispatcher.register.call_count
        cfg = ExplorerConfig(
            name="new_exp",
            model="new-model",
            system_prompt="New prompt.",
        )
        ensemble.register_explorer("new_key", cfg)
        assert mock_dispatcher.register.call_count == call_count_before + 1

    def test_register_overwrites_existing(self, ensemble):
        """Registering the same key twice should overwrite the config."""
        cfg1 = ExplorerConfig(name="v1", model="m1", system_prompt="v1")
        cfg2 = ExplorerConfig(name="v2", model="m2", system_prompt="v2")
        ensemble.register_explorer("overwrite_test", cfg1)
        ensemble.register_explorer("overwrite_test", cfg2)
        retrieved = ensemble.get_explorer("overwrite_test")
        assert retrieved is not None
        assert retrieved.name == "v2"
        assert retrieved.model == "m2"

    def test_unregister_existing_explorer(self, ensemble):
        """unregister_explorer should remove an explorer from registry."""
        assert "finance" in ensemble.registered_explorers
        ensemble.unregister_explorer("finance")
        assert "finance" not in ensemble.registered_explorers

    def test_unregister_nonexistent_explorer(self, ensemble):
        """unregister_explorer on nonexistent key should not raise."""
        count_before = len(ensemble.registered_explorers)
        # Should not raise
        ensemble.unregister_explorer("nonexistent_key")
        assert len(ensemble.registered_explorers) == count_before

    def test_unregister_calls_dispatcher_unregister(self, ensemble, mock_dispatcher):
        """unregister_explorer should call dispatcher.unregister."""
        call_count_before = mock_dispatcher.unregister.call_count
        ensemble.unregister_explorer("finance")
        assert mock_dispatcher.unregister.call_count == call_count_before + 1


class TestExplorerEnsembleLookup:
    """Tests for get_explorer and registered_explorers."""

    def test_get_explorer_existing(self, ensemble):
        """get_explorer should return ExplorerConfig for a registered key."""
        cfg = ensemble.get_explorer("finance")
        assert cfg is not None
        assert isinstance(cfg, ExplorerConfig)
        assert cfg.name == "金融视角"
        assert cfg.perspective == "金融分析"

    def test_get_explorer_nonexistent(self, ensemble):
        """get_explorer should return None for an unregistered key."""
        cfg = ensemble.get_explorer("nonexistent")
        assert cfg is None

    def test_registered_explorers_returns_list(self, ensemble):
        """registered_explorers property should return a list."""
        result = ensemble.registered_explorers
        assert isinstance(result, list)

    def test_registered_explorers_contains_all_builtins(self, ensemble):
        """registered_explorers should contain all built-in keys."""
        keys = ensemble.registered_explorers
        assert "finance" in keys
        assert "medical" in keys
        assert "legal" in keys
        assert "education" in keys
        assert "general" in keys

    def test_get_explorer_after_register(self, ensemble):
        """get_explorer should work for newly registered explorers."""
        cfg = ExplorerConfig(
            name="dynamic_exp",
            model="dynamic-model",
            system_prompt="Dynamic.",
        )
        ensemble.register_explorer("dynamic", cfg)
        retrieved = ensemble.get_explorer("dynamic")
        assert retrieved is cfg

    def test_get_explorer_after_unregister(self, ensemble):
        """get_explorer should return None after unregistering."""
        ensemble.unregister_explorer("medical")
        assert ensemble.get_explorer("medical") is None

    def test_registered_explorers_after_unregister(self, ensemble):
        """registered_explorers should not include unregistered keys."""
        ensemble.unregister_explorer("legal")
        assert "legal" not in ensemble.registered_explorers

    def test_no_get_explorer_by_name_method(self, ensemble):
        """get_explorer_by_name is not a separate method; get_explorer accepts name."""
        # The API uses get_explorer(name) — confirm it works and
        # there's no separate get_explorer_by_name attribute.
        assert hasattr(ensemble, "get_explorer")
        # get_explorer IS the "by name" lookup
        cfg = ensemble.get_explorer("general")
        assert cfg is not None
        assert cfg.name == "通用视角"


class TestExplorerEnsembleReset:
    """Tests for ExplorerEnsemble.reset()."""

    def test_reset_calls_guard_reset(self, ensemble, mock_guard):
        """reset() should call guard.reset()."""
        ensemble.reset()
        mock_guard.reset.assert_called_once()

    def test_reset_clears_strategy(self, ensemble):
        """reset() should clear _current_strategy."""
        ensemble._current_strategy = "parallel"
        ensemble.reset()
        assert ensemble._current_strategy is None

    def test_reset_preserves_registry(self, ensemble):
        """reset() should NOT clear the explorer registry."""
        keys_before = ensemble.registered_explorers.copy()
        ensemble.reset()
        assert ensemble.registered_explorers == keys_before


# ══════════════════════════════════════════════════════════════════════
# Edge Cases & Integration-style Tests
# ══════════════════════════════════════════════════════════════════════

class TestExplorerEnsembleEdgeCases:
    """Edge case and interaction tests for ExplorerEnsemble."""

    def test_register_same_key_multiple_times_count(self, ensemble):
        """Registering the same key multiple times should not increase count."""
        cfg = ExplorerConfig(name="dup", model="m", system_prompt="s")
        ensemble.register_explorer("dup_key", cfg)
        count_after_first = len(ensemble.registered_explorers)
        ensemble.register_explorer("dup_key", cfg)
        assert len(ensemble.registered_explorers) == count_after_first

    def test_unregister_then_register_same_key(self, ensemble):
        """Unregistering then re-registering the same key should work."""
        ensemble.unregister_explorer("finance")
        assert "finance" not in ensemble.registered_explorers
        new_cfg = ExplorerConfig(
            name="re-registered-finance",
            model="new-model",
            system_prompt="Re-registered.",
        )
        ensemble.register_explorer("finance", new_cfg)
        assert "finance" in ensemble.registered_explorers
        assert ensemble.get_explorer("finance").name == "re-registered-finance"

    def test_explorer_config_many_explorers(self, ensemble):
        """Register many explorers and verify all are tracked."""
        for i in range(20):
            cfg = ExplorerConfig(
                name=f"explorer_{i}",
                model=f"model_{i}",
                system_prompt=f"prompt_{i}",
            )
            ensemble.register_explorer(f"key_{i}", cfg)
        # 5 built-ins + 20 custom = 25
        assert len(ensemble.registered_explorers) == 25

    def test_builtin_explorer_config_integrity(self, ensemble):
        """Each built-in explorer should have a non-empty system_prompt and perspective."""
        for key in ["finance", "medical", "legal", "education", "general"]:
            cfg = ensemble.get_explorer(key)
            assert cfg is not None
            assert len(cfg.system_prompt) > 50, f"System prompt for {key} too short"
            assert cfg.perspective != "", f"Perspective for {key} is empty"
            assert cfg.model != "", f"Model for {key} is empty"


# ══════════════════════════════════════════════════════════════════════
# ExplorerConfig.to_provider_profile edge cases
# ══════════════════════════════════════════════════════════════════════

class TestProviderProfileConversion:
    """Additional tests for ExplorerConfig → ProviderProfile conversion."""

    def test_provider_profile_name_prefix(self):
        """ProviderProfile name should always be prefixed with 'explorer:'."""
        cfg = ExplorerConfig(
            name="my_explorer",
            model="m",
            system_prompt="s",
        )
        profile = cfg.to_provider_profile()
        assert profile.name.startswith("explorer:")
        assert profile.name == "explorer:my_explorer"

    def test_provider_profile_timeout_fixed(self):
        """All explorer ProviderProfiles should have timeout=30.0."""
        cfg = ExplorerConfig(name="t", model="m", system_prompt="s")
        profile = cfg.to_provider_profile()
        assert profile.timeout == 30.0

    def test_provider_profile_default_base_url(self):
        """ProviderProfile base_url should be None by default."""
        cfg = ExplorerConfig(name="t", model="m", system_prompt="s")
        profile = cfg.to_provider_profile()
        assert profile.base_url is None
