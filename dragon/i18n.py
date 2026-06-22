"""
Lightweight internationalization (i18n) for Dragon Agent static messages.

Scope: high-impact user-facing static strings — CLI messages, error messages,
prompts, gateway replies. Agent-generated output, log lines, and tool outputs
stay in English.

Supports locale auto-detection from environment, with Chinese (zh-CN) as the
default locale. Missing keys fall back to English; if English is also missing,
the key path itself is returned so a broken catalog never crashes the agent.

Usage::

    from dragon.i18n import t, set_locale
    print(t("cli.welcome"))                        # in current locale
    print(t("errors.timeout", timeout=30))          # with format args
    set_locale("zh-CN")
    print(t("cli.goodbye"))
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("dragon.i18n")

# ────────────────────────────────────────────────────────────────────
# Language Configuration
# ────────────────────────────────────────────────────────────────────

SUPPORTED_LOCALES: tuple[str, ...] = (
    "zh-CN",  # Simplified Chinese (default)
    "en",  # English
)

DEFAULT_LOCALE = "zh-CN"

_LOCALE_ALIASES: dict[str, str] = {
    "zh": "zh-CN",
    "zh_cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-sg": "zh-CN",
    "chinese": "zh-CN",
    "mandarin": "zh-CN",
    "cn": "zh-CN",
    "english": "en",
    "en-us": "en",
    "en-gb": "en",
    "en_us": "en",
}

# ────────────────────────────────────────────────────────────────────
# Translation Store
# ────────────────────────────────────────────────────────────────────

# Built-in translations. Keys are locale codes, values are flat
# key -> translated-string dicts. YAML-based locale files can overlay
# this at runtime.

_BUILTIN_STRINGS: Dict[str, Dict[str, str]] = {
    "zh-CN": {
        # CLI messages
        "cli.welcome": "欢迎使用 Dragon Agent！",
        "cli.goodbye": "再见！",
        "cli.prompt": "> ",
        "cli.help": "输入你的问题，或使用 /help 查看命令。",
        "cli.model_info": "模型: {model} | 提供方: {provider}",
        "cli.streaming": "流式输出中...",
        "cli.interactive_mode": "交互模式",
        "cli.non_interactive": "单次查询模式",

        # Errors
        "errors.provider_not_found": "未找到提供方: {provider}",
        "errors.model_not_found": "未找到模型: {model}",
        "errors.api_key_missing": "缺少 API 密钥: {env_var}",
        "errors.timeout": "请求超时 ({timeout}秒)",
        "errors.rate_limited": "请求频率过高，请稍后重试。",
        "errors.unknown": "发生未知错误: {error}",
        "errors.config_invalid": "配置无效: {error}",
        "errors.file_not_found": "文件未找到: {path}",
        "errors.permission_denied": "权限不足: {path}",
        "errors.path_traversal": "路径遍历攻击被阻止: {path}",

        # Session
        "session.created": "会话已创建: {id}",
        "session.deleted": "会话已删除: {id}",
        "session.not_found": "会话未找到: {id}",
        "session.list_header": "最近的会话:",
        "session.empty": "暂无会话。",
        "session.title_set": "标题已设置: {title}",

        # Provider
        "provider.connected": "已连接: {provider} ({model})",
        "provider.failed": "连接失败: {provider}",
        "provider.available": "可用提供方: {providers}",
        "provider.no_available": "没有可用的提供方。",

        # Tool
        "tool.executing": "执行工具: {tool}",
        "tool.success": "工具执行成功: {tool}",
        "tool.failed": "工具执行失败: {tool} - {error}",
        "tool.guardrails_blocked": "Guardrails 阻止了工具: {tool}",

        # Gateway
        "gateway.started": "网关已启动: {host}:{port}",
        "gateway.stopped": "网关已停止。",
        "gateway.platform_enabled": "平台已启用: {platform}",

        # Config
        "config.loaded": "配置已加载: {path}",
        "config.saved": "配置已保存: {path}",
        "config.valid": "配置有效。",
        "config.invalid": "配置无效 — 请运行 'dragon config check' 查看详情。",

        # Cron
        "cron.scheduled": "定时任务已安排: {job}",
        "cron.removed": "定时任务已移除: {job}",
        "cron.execution_failed": "定时任务执行失败: {job} - {error}",

        # Skills
        "skill.loaded": "技能已加载: {name}",
        "skill.failed": "技能加载失败: {name}",
        "skill.evolved": "技能已进化: {name}",

        # Memory
        "memory.stored": "记忆已存储。",
        "memory.recalled": "回忆了 {count} 条记忆。",
        "memory.empty": "暂无记忆。",

        # Insights
        "insights.header": "使用洞察报告",
        "insights.empty": "暂无数据。",
        "insights.period": "周期: {days} 天",

        # Title Generator
        "title.generated": "自动标题: {title}",
        "title.failed": "标题生成失败。",
    },
    "en": {
        # CLI messages
        "cli.welcome": "Welcome to Dragon Agent!",
        "cli.goodbye": "Goodbye!",
        "cli.prompt": "> ",
        "cli.help": "Type your question, or use /help for commands.",
        "cli.model_info": "Model: {model} | Provider: {provider}",
        "cli.streaming": "Streaming...",
        "cli.interactive_mode": "Interactive Mode",
        "cli.non_interactive": "Single Query Mode",

        # Errors
        "errors.provider_not_found": "Provider not found: {provider}",
        "errors.model_not_found": "Model not found: {model}",
        "errors.api_key_missing": "API key missing: {env_var}",
        "errors.timeout": "Request timed out ({timeout}s)",
        "errors.rate_limited": "Rate limited. Please try again later.",
        "errors.unknown": "Unknown error: {error}",
        "errors.config_invalid": "Invalid config: {error}",
        "errors.file_not_found": "File not found: {path}",
        "errors.permission_denied": "Permission denied: {path}",
        "errors.path_traversal": "Path traversal blocked: {path}",

        # Session
        "session.created": "Session created: {id}",
        "session.deleted": "Session deleted: {id}",
        "session.not_found": "Session not found: {id}",
        "session.list_header": "Recent sessions:",
        "session.empty": "No sessions.",
        "session.title_set": "Title set: {title}",

        # Provider
        "provider.connected": "Connected: {provider} ({model})",
        "provider.failed": "Connection failed: {provider}",
        "provider.available": "Available providers: {providers}",
        "provider.no_available": "No available providers.",

        # Tool
        "tool.executing": "Executing tool: {tool}",
        "tool.success": "Tool succeeded: {tool}",
        "tool.failed": "Tool failed: {tool} - {error}",
        "tool.guardrails_blocked": "Guardrails blocked tool: {tool}",

        # Gateway
        "gateway.started": "Gateway started on {host}:{port}",
        "gateway.stopped": "Gateway stopped.",
        "gateway.platform_enabled": "Platform enabled: {platform}",

        # Config
        "config.loaded": "Config loaded: {path}",
        "config.saved": "Config saved: {path}",
        "config.valid": "Config is valid.",
        "config.invalid": "Config invalid — run 'dragon config check' for details.",

        # Cron
        "cron.scheduled": "Cron job scheduled: {job}",
        "cron.removed": "Cron job removed: {job}",
        "cron.execution_failed": "Cron job failed: {job} - {error}",

        # Skills
        "skill.loaded": "Skill loaded: {name}",
        "skill.failed": "Skill failed to load: {name}",
        "skill.evolved": "Skill evolved: {name}",

        # Memory
        "memory.stored": "Memory stored.",
        "memory.recalled": "Recalled {count} memories.",
        "memory.empty": "No memories yet.",

        # Insights
        "insights.header": "Usage Insights Report",
        "insights.empty": "No data available.",
        "insights.period": "Period: {days} days",

        # Title Generator
        "title.generated": "Auto-generated title: {title}",
        "title.failed": "Title generation failed.",
    },
}

# ────────────────────────────────────────────────────────────────────
# Runtime State
# ────────────────────────────────────────────────────────────────────

_current_locale: str = ""
_locale_lock = threading.Lock()
_catalog_cache: dict[str, dict[str, str]] = {}
_catalog_lock = threading.Lock()


def _normalize_locale(raw: str) -> str:
    """Normalize a locale string to a supported code."""
    if not raw or not isinstance(raw, str):
        return DEFAULT_LOCALE
    key = raw.strip().lower().replace("_", "-")
    if key in SUPPORTED_LOCALES:
        return key
    if key in _LOCALE_ALIASES:
        return _LOCALE_ALIASES[key]
    # Try base language
    base = key.split("-")[0]
    if base == "zh":
        return "zh-CN"
    if base == "en":
        return "en"
    return DEFAULT_LOCALE


def _detect_locale() -> str:
    """Auto-detect locale from environment variables."""
    # Check DRAGON_LOCALE first
    env_locale = os.getenv("DRAGON_LOCALE", "")
    if env_locale:
        return _normalize_locale(env_locale)

    # Check LANG (Unix convention)
    lang = os.getenv("LANG", "")
    if lang:
        # Extract language from LANG=zh_CN.UTF-8 or en_US.UTF-8
        parts = lang.split(".")[0].split("_")
        if len(parts) >= 1:
            locale_str = parts[0]
            if len(parts) >= 2:
                locale_str = f"{parts[0]}-{parts[1]}"
            normalized = _normalize_locale(locale_str)
            if normalized != DEFAULT_LOCALE or locale_str == "zh-CN":
                return normalized

    # Check LC_ALL, LC_MESSAGES
    for var in ("LC_ALL", "LC_MESSAGES"):
        val = os.getenv(var, "")
        if val and val != "C" and val != "POSIX":
            normalized = _normalize_locale(val.split(".")[0])
            if normalized != DEFAULT_LOCALE or "zh" in val.lower():
                return normalized

    return DEFAULT_LOCALE


def get_locale() -> str:
    """Get the currently active locale."""
    global _current_locale
    if _current_locale:
        return _current_locale
    # Lazy init
    with _locale_lock:
        if not _current_locale:
            _current_locale = _detect_locale()
    return _current_locale


def set_locale(locale: str) -> None:
    """Set the active locale at runtime."""
    global _current_locale
    with _locale_lock:
        _current_locale = _normalize_locale(locale)
    logger.debug("Locale set to %s", _current_locale)


def reset_locale() -> None:
    """Reset locale to auto-detection."""
    global _current_locale
    with _locale_lock:
        _current_locale = ""


# ────────────────────────────────────────────────────────────────────
# Catalog loading (for YAML-based locale files)
# ────────────────────────────────────────────────────────────────────


def _load_yaml_catalog(locale: str) -> Dict[str, str]:
    """Load locale strings from a YAML file if available."""
    with _catalog_lock:
        cached = _catalog_cache.get(locale)
        if cached is not None:
            return cached

    # Try to load from dragon/locales/<locale>.yaml
    try:
        locales_dir = Path(__file__).resolve().parent / "locales"
        yaml_path = locales_dir / f"{locale}.yaml"
        if yaml_path.is_file():
            import yaml
            with yaml_path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            flat = _flatten_yaml(raw)
            with _catalog_lock:
                _catalog_cache[locale] = flat
            return flat
    except Exception as exc:
        logger.debug("Could not load locale file for %s: %s", locale, exc)

    with _catalog_lock:
        _catalog_cache[locale] = {}
    return {}


def _flatten_yaml(node: Any, prefix: str = "", out: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Flatten nested YAML dict into dotted keys."""
    if out is None:
        out = {}
    if isinstance(node, dict):
        for key, value in node.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            _flatten_yaml(value, child_key, out)
    elif isinstance(node, str):
        out[prefix] = node
    return out


# ────────────────────────────────────────────────────────────────────
# Translation Function
# ────────────────────────────────────────────────────────────────────


def t(key: str, locale: Optional[str] = None, **format_kwargs: Any) -> str:
    """Translate a dotted key to the active locale.

    Args:
        key: Dotted path (e.g., 'cli.welcome', 'errors.timeout').
        locale: Override locale. Defaults to auto-detected locale.
        **format_kwargs: str.format() substitution arguments.

    Returns:
        Translated string, or English fallback, or the key itself if missing.
    """
    target = _normalize_locale(locale) if locale else get_locale()

    # Try YAML catalog first, then built-in
    yaml_catalog = _load_yaml_catalog(target)
    if key in yaml_catalog:
        value = yaml_catalog[key]
    elif key in _BUILTIN_STRINGS.get(target, {}):
        value = _BUILTIN_STRINGS[target][key]
    elif target != "en":
        # Fall back to English
        en_yaml = _load_yaml_catalog("en")
        if key in en_yaml:
            value = en_yaml[key]
        elif key in _BUILTIN_STRINGS.get("en", {}):
            value = _BUILTIN_STRINGS["en"][key]
        else:
            logger.debug("i18n miss: key=%r locale=%r", key, target)
            value = key
    else:
        logger.debug("i18n miss: key=%r locale=%r", key, target)
        value = key

    if format_kwargs:
        try:
            return value.format(**format_kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning(
                "i18n format failed for key=%r locale=%r: %s", key, target, exc
            )
            return value
    return value


def add_translations(locale: str, translations: Dict[str, str]) -> None:
    """Add or override translations for a locale at runtime.

    Args:
        locale: Locale code (e.g., 'zh-CN', 'en').
        translations: Dict of key -> translated string.
    """
    norm = _normalize_locale(locale)
    if norm not in _BUILTIN_STRINGS:
        _BUILTIN_STRINGS[norm] = {}
    _BUILTIN_STRINGS[norm].update(translations)
    # Invalidate YAML cache if it overlaps
    with _catalog_lock:
        _catalog_cache.pop(norm, None)


def get_translations(locale: Optional[str] = None) -> Dict[str, str]:
    """Get all translations for a locale.

    Args:
        locale: Locale code. Defaults to current locale.

    Returns:
        Dict of key -> translated string.
    """
    target = _normalize_locale(locale) if locale else get_locale()
    result: Dict[str, str] = {}

    # YAML overrides first
    yaml_catalog = _load_yaml_catalog(target)
    if yaml_catalog:
        result.update(yaml_catalog)

    # Built-in as base
    if target in _BUILTIN_STRINGS:
        for k, v in _BUILTIN_STRINGS[target].items():
            if k not in result:
                result[k] = v
    return result


__all__ = [
    "SUPPORTED_LOCALES",
    "DEFAULT_LOCALE",
    "t",
    "get_locale",
    "set_locale",
    "reset_locale",
    "add_translations",
    "get_translations",
]
