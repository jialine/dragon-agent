"""
Unit tests for DragonConfig — defaults, YAML loading, env var overrides.
"""
import os
import tempfile
from pathlib import Path

import pytest
import yaml
from dragon.config import (
    DragonConfig, RouterConfig, MemoryConfig, GuardConfig, ServerConfig,
    BackupConfig, DispatchConfig, IndustryConfig,
)


class TestDragonConfigDefaults:
    def test_default_router_model_path(self):
        cfg = DragonConfig()
        assert cfg.router.model_path == "models/qwen2-1.5b-q4_k_m.gguf"

    def test_default_memory_embedding_model(self):
        cfg = DragonConfig()
        assert cfg.memory.embedding_model == "BAAI/bge-small-zh-v1.5"

    def test_default_memory_search_top_k(self):
        cfg = DragonConfig()
        assert cfg.memory.search_top_k == 5

    def test_default_memory_search_threshold(self):
        cfg = DragonConfig()
        assert cfg.memory.search_threshold == 0.5

    def test_default_guard_window_size(self):
        cfg = DragonConfig()
        assert cfg.guard.window_size == 50

    def test_default_server_port(self):
        cfg = DragonConfig()
        assert cfg.server.port == 8000


class TestDragonConfigFromYAML:
    def test_load_from_yaml_file(self):
        yaml_content = {
            "router": {"n_threads": 2},
            "server": {"port": 9000},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_content, f)
            tmp_path = f.name

        try:
            cfg = DragonConfig.load(tmp_path)
            assert cfg.router.n_threads == 2
            assert cfg.server.port == 9000
            assert cfg.router.model_path == "models/qwen2-1.5b-q4_k_m.gguf"
        finally:
            os.unlink(tmp_path)

    def test_load_missing_file_uses_defaults(self):
        cfg = DragonConfig.load("/nonexistent/dragon_config.yaml")
        assert cfg.router.model_path == "models/qwen2-1.5b-q4_k_m.gguf"


class TestDragonConfigEnvOverrides:
    """Test env var overrides for simple (non-underscore) field names."""

    def setup_method(self):
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("DRAGON_"):
                self._saved[k] = os.environ.pop(k)

    def teardown_method(self):
        # Remove any DRAGON_ env vars that were set during the test
        for k in list(os.environ):
            if k.startswith("DRAGON_") and k not in self._saved:
                del os.environ[k]
        for k, v in self._saved.items():
            os.environ[k] = v

    def test_env_override_server_port(self):
        os.environ["DRAGON_SERVER_PORT"] = "9999"
        cfg = DragonConfig.load()
        assert cfg.server.port == 9999


class TestRouterConfig:
    def test_defaults(self):
        rc = RouterConfig()
        assert rc.model_path == "models/qwen2-1.5b-q4_k_m.gguf"
        assert rc.n_threads == 4
        assert rc.n_ctx == 512
        assert rc.temperature == 0.1


class TestMemoryConfig:
    def test_defaults(self):
        mc = MemoryConfig()
        assert mc.persist_dir == "dragon_data/vectordb"
        assert mc.embedding_model == "BAAI/bge-small-zh-v1.5"
        assert mc.search_top_k == 5


class TestGuardConfigDefaults:
    def test_defaults(self):
        gc = GuardConfig()
        assert gc.max_consecutive_repeats == 3
        assert gc.max_loop_rounds == 2
        assert gc.max_ineffective_retries == 3
        assert gc.window_size == 50
        assert gc.task_timeout_secs == 300


class TestBackupConfigDefaults:
    """Test all BackupConfig default values."""

    def test_defaults(self):
        bc = BackupConfig()
        assert bc.endpoint == ""
        assert bc.access_key_env == "DRAGON_BACKUP_ACCESS_KEY"
        assert bc.secret_key_env == "DRAGON_BACKUP_SECRET_KEY"
        assert bc.bucket == "dragon-backups"
        assert bc.prefix == "dragon/backups/"
        assert bc.interval_hours == 6
        assert bc.keep_last == 7

    def test_custom_values(self):
        bc = BackupConfig(
            endpoint="https://s3.example.com",
            access_key_env="MY_ACCESS_KEY",
            secret_key_env="MY_SECRET_KEY",
            bucket="my-bucket",
            prefix="my/prefix/",
            interval_hours=12,
            keep_last=10,
        )
        assert bc.endpoint == "https://s3.example.com"
        assert bc.bucket == "my-bucket"
        assert bc.interval_hours == 12
        assert bc.keep_last == 10


class TestServerConfigDefaults:
    """Test all ServerConfig default values and overrides."""

    def test_defaults(self):
        sc = ServerConfig()
        assert sc.host == "0.0.0.0"
        assert sc.port == 8000
        assert sc.log_level == "info"

    def test_custom_values(self):
        sc = ServerConfig(host="127.0.0.1", port=3000, log_level="debug")
        assert sc.host == "127.0.0.1"
        assert sc.port == 3000
        assert sc.log_level == "debug"


class TestIndustryConfigDefaults:
    """Test IndustryConfig — per-industry system prompt config."""

    def test_defaults(self):
        pc = IndustryConfig()
        assert pc.system_prompt == "You are a helpful assistant."

    def test_custom_values(self):
        pc = IndustryConfig(
            system_prompt="You are a financial expert.",
        )
        assert pc.system_prompt == "You are a financial expert."


class TestDispatchConfigDefaults:
    """Test DispatchConfig defaults and custom industries."""

    def test_default_industries_empty(self):
        dc = DispatchConfig()
        assert dc.industries == {}

    def test_custom_industries(self):
        dc = DispatchConfig(industries={
            "finance": IndustryConfig(system_prompt="Financial expert"),
            "health": IndustryConfig(system_prompt="Medical expert"),
        })
        assert len(dc.industries) == 2
        assert dc.industries["finance"].system_prompt == "Financial expert"
        assert dc.industries["health"].system_prompt == "Medical expert"


class TestRouterConfigCustomValues:
    """Test RouterConfig with all custom values."""

    def test_all_custom_fields(self):
        rc = RouterConfig(
            model_path="models/custom.gguf",
            n_threads=8,
            n_ctx=2048,
            temperature=0.7,
            max_tokens=256,
            fallback_on_failure=False,
        )
        assert rc.model_path == "models/custom.gguf"
        assert rc.n_threads == 8
        assert rc.n_ctx == 2048
        assert rc.temperature == 0.7
        assert rc.max_tokens == 256
        assert rc.fallback_on_failure is False


class TestMemoryConfigCustomValues:
    """Test MemoryConfig with all custom values."""

    def test_all_custom_fields(self):
        mc = MemoryConfig(
            persist_dir="/custom/path",
            embedding_model="custom/model",
            search_top_k=10,
            search_threshold=0.8,
            recency_weight=0.5,
        )
        assert mc.persist_dir == "/custom/path"
        assert mc.embedding_model == "custom/model"
        assert mc.search_top_k == 10
        assert mc.search_threshold == 0.8
        assert mc.recency_weight == 0.5


class TestDragonConfigAllDefaults:
    """Test that all sections have correct defaults from DragonConfig."""

    def test_all_sections_present(self):
        cfg = DragonConfig()
        assert isinstance(cfg.router, RouterConfig)
        assert isinstance(cfg.dispatch, DispatchConfig)
        assert isinstance(cfg.memory, MemoryConfig)
        assert isinstance(cfg.backup, BackupConfig)
        assert isinstance(cfg.guard, GuardConfig)
        assert isinstance(cfg.server, ServerConfig)

    def test_all_section_defaults(self):
        cfg = DragonConfig()
        # router
        assert cfg.router.model_path == "models/qwen2-1.5b-q4_k_m.gguf"
        assert cfg.router.n_threads == 4
        # memory
        assert cfg.memory.search_top_k == 5
        assert cfg.memory.recency_weight == 0.1
        # guard
        assert cfg.guard.max_consecutive_repeats == 3
        assert cfg.guard.task_timeout_secs == 300
        # server
        assert cfg.server.host == "0.0.0.0"
        assert cfg.server.port == 8000
        # backup
        assert cfg.backup.bucket == "dragon-backups"
        assert cfg.backup.interval_hours == 6
        # dispatch
        assert cfg.dispatch.industries == {}


class TestDragonConfigMultipleEnvOverrides:
    """Test multiple simultaneous env var overrides."""

    def setup_method(self):
        self._saved = {}
        for k in list(os.environ):
            if k.startswith("DRAGON_"):
                self._saved[k] = os.environ.pop(k)

    def teardown_method(self):
        # Remove any DRAGON_ env vars that were set during the test
        for k in list(os.environ):
            if k.startswith("DRAGON_") and k not in self._saved:
                del os.environ[k]
        for k, v in self._saved.items():
            os.environ[k] = v

    def test_multiple_env_overrides(self):
        os.environ["DRAGON_SERVER_PORT"] = "9999"
        os.environ["DRAGON_ROUTER_N_THREADS"] = "8"
        os.environ["DRAGON_MEMORY_SEARCH_TOP_K"] = "10"
        cfg = DragonConfig.load()
        assert cfg.server.port == 9999
        assert cfg.router.n_threads == 8
        assert cfg.memory.search_top_k == 10

    def test_env_override_boolean_true(self):
        os.environ["DRAGON_ROUTER_FALLBACK_ON_FAILURE"] = "true"
        cfg = DragonConfig.load()
        assert cfg.router.fallback_on_failure is True

    def test_env_override_boolean_false(self):
        os.environ["DRAGON_ROUTER_FALLBACK_ON_FAILURE"] = "false"
        cfg = DragonConfig.load()
        assert cfg.router.fallback_on_failure is False

    def test_env_override_boolean_yes(self):
        os.environ["DRAGON_ROUTER_FALLBACK_ON_FAILURE"] = "yes"
        cfg = DragonConfig.load()
        assert cfg.router.fallback_on_failure is True

    def test_env_override_float(self):
        os.environ["DRAGON_ROUTER_TEMPERATURE"] = "0.5"
        cfg = DragonConfig.load()
        assert cfg.router.temperature == 0.5

    def test_env_override_guard_fields(self):
        os.environ["DRAGON_GUARD_MAX_LOOP_ROUNDS"] = "5"
        os.environ["DRAGON_GUARD_WINDOW_SIZE"] = "100"
        cfg = DragonConfig.load()
        assert cfg.guard.max_loop_rounds == 5
        assert cfg.guard.window_size == 100

    def test_env_override_backup_fields(self):
        os.environ["DRAGON_BACKUP_INTERVAL_HOURS"] = "12"
        os.environ["DRAGON_BACKUP_KEEP_LAST"] = "14"
        cfg = DragonConfig.load()
        assert cfg.backup.interval_hours == 12
        assert cfg.backup.keep_last == 14

    def test_env_override_memory_threshold(self):
        os.environ["DRAGON_MEMORY_SEARCH_THRESHOLD"] = "0.75"
        os.environ["DRAGON_MEMORY_RECENCY_WEIGHT"] = "0.25"
        cfg = DragonConfig.load()
        assert cfg.memory.search_threshold == 0.75
        assert cfg.memory.recency_weight == 0.25


class TestDragonConfigSerialization:
    """Test DragonConfig serialization roundtrip."""

    def test_to_dict_and_from_dict(self):
        cfg = DragonConfig()
        cfg.router.n_threads = 2
        cfg.server.port = 9000

        data = cfg.model_dump()
        restored = DragonConfig(**data)
        assert restored.router.n_threads == 2
        assert restored.server.port == 9000

    def test_from_dict_preserves_all_sections(self):
        data = DragonConfig().model_dump()
        restored = DragonConfig(**data)
        assert restored.router.model_path == "models/qwen2-1.5b-q4_k_m.gguf"
        assert restored.memory.embedding_model == "BAAI/bge-small-zh-v1.5"
        assert restored.guard.window_size == 50
        assert restored.backup.bucket == "dragon-backups"


class TestDragonConfigValidation:
    """Test config validation edge cases."""

    def test_invalid_model_path_type(self):
        """RouterConfig model_path must be a string."""
        with pytest.raises(Exception):
            RouterConfig(model_path=123)

    def test_invalid_temperature_type(self):
        """RouterConfig temperature must be a float."""
        with pytest.raises(Exception):
            RouterConfig(temperature="hot")


class TestDragonConfigYAMLRoundtrip:
    """Test full config save/load roundtrip via YAML."""

    def test_yaml_roundtrip(self):
        cfg = DragonConfig()
        cfg.router.n_threads = 6
        cfg.server.port = 8080
        cfg.memory.search_top_k = 8

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg.model_dump(), f)
            tmp_path = f.name

        try:
            restored = DragonConfig.load(tmp_path)
            assert restored.router.n_threads == 6
            assert restored.server.port == 8080
            assert restored.memory.search_top_k == 8
        finally:
            os.unlink(tmp_path)

    def test_yaml_roundtrip_all_sections(self):
        """Roundtrip with all sections having custom values."""
        cfg = DragonConfig(
            router=RouterConfig(n_threads=2, temperature=0.5),
            server=ServerConfig(port=9999, host="127.0.0.1"),
            memory=MemoryConfig(search_top_k=3, recency_weight=0.2),
            backup=BackupConfig(bucket="custom-bucket", keep_last=5),
            guard=GuardConfig(window_size=100, max_loop_rounds=1),
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg.model_dump(), f)
            tmp_path = f.name

        try:
            restored = DragonConfig.load(tmp_path)
            assert restored.router.n_threads == 2
            assert restored.router.temperature == 0.5
            assert restored.server.port == 9999
            assert restored.server.host == "127.0.0.1"
            assert restored.memory.search_top_k == 3
            assert restored.memory.recency_weight == 0.2
            assert restored.backup.bucket == "custom-bucket"
            assert restored.backup.keep_last == 5
            assert restored.guard.window_size == 100
            assert restored.guard.max_loop_rounds == 1
        finally:
            os.unlink(tmp_path)


class TestDragonConfigMerge:
    """Test config merging: base defaults + partial overrides."""

    def test_merge_partial_overrides(self):
        """Load from empty file, then apply env overrides for router only."""
        base = DragonConfig()
        base.router.n_threads = 10
        # Other sections should stay at defaults
        assert base.memory.search_top_k == 5
        assert base.server.port == 8000

    def test_yml_overrides_merge_with_defaults(self):
        yaml_content = {"router": {"n_threads": 3}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_content, f)
            tmp_path = f.name

        try:
            cfg = DragonConfig.load(tmp_path)
            assert cfg.router.n_threads == 3
            # Unspecified fields keep defaults
            assert cfg.router.model_path == "models/qwen2-1.5b-q4_k_m.gguf"
            assert cfg.memory.embedding_model == "BAAI/bge-small-zh-v1.5"
            assert cfg.server.port == 8000
        finally:
            os.unlink(tmp_path)
