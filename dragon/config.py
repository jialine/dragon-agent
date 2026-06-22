"""
Dragon Agent config system.

Inspired by Hermes Agent's config chain:
  1. Hardcoded defaults
  2. config.yaml (YAML file)
  3. .env (environment variables)
  4. System environment variables (highest priority)
"""

from pydantic import BaseModel, Field
from typing import Dict, Optional
import os
import yaml
from pathlib import Path
from dotenv import load_dotenv


class RouterConfig(BaseModel):
    model_path: str = "models/qwen3-0.6b-q4_k_m.gguf"
    n_threads: int = 4
    n_ctx: int = 512
    temperature: float = 0.1
    max_tokens: int = 128
    fallback_on_failure: bool = True


class GlobalApiConfig(BaseModel):
    """Single API endpoint shared by all industries."""
    base_url: str = "https://api.sangyuye.com/v1"
    api_key_env: str = "DRAGON_API_KEY"
    model: str = ""
    timeout_secs: int = 60
    max_retries: int = 2


class IndustryConfig(BaseModel):
    """Per-industry config — only system_prompt; API comes from GlobalApiConfig."""
    system_prompt: str = "You are a helpful assistant."


class DispatchConfig(BaseModel):
    global_api: GlobalApiConfig = Field(default_factory=GlobalApiConfig)
    industries: Dict[str, IndustryConfig] = Field(default_factory=dict)


class MemoryConfig(BaseModel):
    persist_dir: str = "dragon_data/vectordb"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    search_top_k: int = 5
    search_threshold: float = 0.5
    recency_weight: float = 0.1


class BackupConfig(BaseModel):
    endpoint: str = ""
    access_key_env: str = "DRAGON_BACKUP_ACCESS_KEY"
    secret_key_env: str = "DRAGON_BACKUP_SECRET_KEY"
    bucket: str = "dragon-backups"
    prefix: str = "dragon/backups/"
    interval_hours: int = 6
    keep_last: int = 7


class GuardConfig(BaseModel):
    max_consecutive_repeats: int = 3
    max_loop_rounds: int = 2
    max_ineffective_retries: int = 3
    window_size: int = 50
    task_timeout_secs: int = 300


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


class PlatformAuthConfig(BaseModel):
    """Per-platform credentials (loaded from config.yaml, NOT env vars for security)."""
    enabled: bool = False

    # Common
    connection_mode: str = "websocket"  # "websocket" or "webhook"

    # Feishu / Lark
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    domain: str = "feishu"  # "feishu" or "lark"

    # WeChat Official Account
    token: str = ""
    encoding_aes_key: str = ""

    # Telegram / Discord
    bot_token: str = ""


class GatewayConfig(BaseModel):
    """Multi-platform message gateway configuration.

    Each platform key (feishu, wechat, telegram, etc.) maps to PlatformAuthConfig.
    Only platforms with enabled=true are started.
    """
    enabled: bool = False
    standalone: bool = True       # True = separate port, False = mount on main server
    port: int = 8781              # standalone port
    host: str = "0.0.0.0"
    system_prompt: str = ""       # override default system prompt for gateway

    # Platform credentials keyed by name
    platforms: Dict[str, PlatformAuthConfig] = Field(default_factory=dict)


class DragonConfig(BaseModel):
    router: RouterConfig = Field(default_factory=RouterConfig)
    dispatch: DispatchConfig = Field(default_factory=DispatchConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "DragonConfig":
        """Load config from YAML file with env var overrides."""
        load_dotenv()

        # Start with defaults
        config_data = {}

        # Layer 1: config.yaml
        if os.path.exists(config_path):
            with open(config_path) as f:
                file_data = yaml.safe_load(f)
                if file_data:
                    config_data.update(file_data)

        # Layer 2: .env / system env overrides
        _apply_env_overrides(config_data)

        return cls(**config_data)


# Known DragonConfig section names (must match DragonConfig model fields)
_DRAGON_SECTIONS = frozenset({"router", "dispatch", "memory", "backup", "guard", "server", "gateway"})


def _apply_env_overrides(data: dict, prefix: str = "DRAGON_"):
    """Apply DRAGON_* environment variables as overrides."""
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        # DRAGON_BACKUP_INTERVAL_HOURS → backup.interval_hours
        # DRAGON_SERVER_PORT → server.port
        suffix = key[len(prefix):].lower().replace("__", ".")
        parts = suffix.split("_")

        # Find the section name by trying progressively longer prefixes until
        # we find a known section name, then treat the remainder as the field name.
        section = None
        field = None
        for i in range(1, len(parts)):
            candidate = "_".join(parts[:i])
            if candidate in _DRAGON_SECTIONS:
                section = candidate
                field = "_".join(parts[i:])
                break

        if section is None:
            # Fallback: no known section found, treat first part as section
            # and the rest as field (original behavior for simple cases)
            section = parts[0]
            field = "_".join(parts[1:]) if len(parts) > 1 else None

        if field is None:
            # DRAGON_SOMETHING without further qualifiers → data[something] = value
            data[section] = _coerce_env_value(value)
        else:
            d = data.setdefault(section, {})
            if isinstance(d, dict):
                d[field] = _coerce_env_value(value)


def _coerce_env_value(value: str):
    """Coerce env var string to appropriate type."""
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
