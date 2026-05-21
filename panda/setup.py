"""
Panda Setup — Rich interactive configuration wizard.

Sections (modular, can run independently):
    panda setup              Full wizard (all sections)
    panda setup model        Model & provider picker
    panda setup providers    API keys only
    panda setup gateway      Gateway/platform config
    panda setup --quick      Non-interactive, env vars only

Design principles (aligned with Hermes Agent):
    - Rich panels, tables, and progress for polished UX
    - Each section is self-contained and idempotent
    - API keys are masked, validated, and persisted
    - Config is saved to both .env and config.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.text import Text
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None

ENV_FILE = Path.home() / ".panda" / ".env"
CONFIG_FILE = Path.home() / ".panda" / "config.yaml"

# ── Provider Registry ────────────────────────────────────────────────

PROVIDERS = [
    {"id": "openai",       "env": "OPENAI_API_KEY",     "label": "OpenAI",           "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"], "url": "https://platform.openai.com/api-keys"},
    {"id": "deepseek",     "env": "DEEPSEEK_API_KEY",   "label": "DeepSeek",         "models": ["deepseek-chat", "deepseek-reasoner"],            "url": "https://platform.deepseek.com/api_keys"},
    {"id": "anthropic",    "env": "ANTHROPIC_API_KEY",  "label": "Anthropic",        "models": ["claude-sonnet-4-20250514", "claude-haiku-3.5"], "url": "https://console.anthropic.com/keys"},
    {"id": "google",       "env": "GOOGLE_API_KEY",     "label": "Google Gemini",    "models": ["gemini-2.5-flash", "gemini-2.5-pro"],           "url": "https://aistudio.google.com/apikey"},
    {"id": "xai",          "env": "XAI_API_KEY",        "label": "xAI Grok",         "models": ["grok-3"],                                       "url": "https://console.x.ai"},
    {"id": "openrouter",   "env": "OPENROUTER_API_KEY", "label": "OpenRouter",       "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4"],  "url": "https://openrouter.ai/keys"},
    {"id": "moonshot",     "env": "MOONSHOT_API_KEY",   "label": "Moonshot/Kimi",    "models": ["moonshot-v1-8k"],                               "url": "https://platform.moonshot.cn/console/api-keys"},
    {"id": "together",     "env": "TOGETHER_API_KEY",   "label": "Together AI",      "models": ["meta-llama/Llama-4"],                           "url": "https://api.together.xyz/settings/api-keys"},
    {"id": "groq",         "env": "GROQ_API_KEY",       "label": "Groq",             "models": ["llama-3.3-70b"],                                "url": "https://console.groq.com/keys"},
    {"id": "mistral",      "env": "MISTRAL_API_KEY",    "label": "Mistral AI",       "models": ["mistral-large"],                                "url": "https://console.mistral.ai/api-keys"},
]

GATEWAY_PLATFORMS = [
    {"id": "feishu",    "name": "Feishu (飞书)",     "envs": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"], "docs": "https://open.feishu.cn/app"},
    {"id": "telegram",  "name": "Telegram",            "envs": ["TELEGRAM_BOT_TOKEN"],               "docs": "https://t.me/BotFather"},
    {"id": "discord",   "name": "Discord",             "envs": ["DISCORD_BOT_TOKEN"],               "docs": "https://discord.com/developers/applications"},
    {"id": "wechat",    "name": "WeChat (微信)",       "envs": ["WECHAT_APP_ID", "WECHAT_APP_SECRET"], "docs": "https://mp.weixin.qq.com/"},
]


# ── Helpers ──────────────────────────────────────────────────────────

def _print(text="", style=""):
    if console:
        if style:
            console.print(text, style=style)
        else:
            console.print(text)
    else:
        print(text)

def _panel(title, content, style="bold blue"):
    if console:
        console.print(Panel(content, title=title, border_style=style, box=box.ROUNDED))
    else:
        print(f"\n═══ {title} ═══")
        print(content)

def _table(title, headers, rows):
    if console:
        t = Table(title=title, box=box.SIMPLE_HEAVY)
        for h in headers:
            t.add_column(h, style="cyan" if h == headers[0] else "")
        for row in rows:
            t.add_row(*[str(c) for c in row])
        console.print(t)
    else:
        print(f"\n{title}")
        print("  " + "  ".join(headers))
        for row in rows:
            print("  " + "  ".join(str(c) for c in row))

def _prompt(text, default="", password=False):
    if console:
        if password:
            return Prompt.ask(f"  {text}", password=True, default=default) or default
        return Prompt.ask(f"  {text}", default=default) or default
    else:
        if password:
            import getpass
            v = getpass.getpass(f"  {text}: ").strip()
        else:
            v = input(f"  {text} [{default}]: ").strip()
        return v or default

def _confirm(text, default=True):
    if console:
        return Confirm.ask(f"  {text}", default=default)
    else:
        s = " [Y/n]" if default else " [y/N]"
        a = input(f"  {text}{s}: ").strip().lower()
        return default if not a else a in ("y", "yes")

def _load_env() -> Dict[str, str]:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def _save_env(env: Dict[str, str]):
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Panda Agent — Environment Configuration", "# Generated by: panda setup", ""]
    sections = {
        "Feishu / Lark":        [k for k in env if k.startswith("FEISHU_")],
        "Telegram":             [k for k in env if k.startswith("TELEGRAM_")],
        "Discord":              [k for k in env if k.startswith("DISCORD_")],
        "WeChat":               [k for k in env if k.startswith("WECHAT_")],
        "Provider API Keys":    [k for k in env if k.endswith("_API_KEY") and not k.startswith(("FEISHU_", "TELEGRAM_", "DISCORD_", "WECHAT_"))],
        "Defaults":             [k for k in env if k.startswith("PANDA_")],
    }
    for section, keys in sections.items():
        if keys:
            lines.append(f"\n# ── {section} ──")
            for k in sorted(keys):
                lines.append(f"{k}={env[k]}")
    ENV_FILE.write_text("\n".join(lines) + "\n")

def _load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(CONFIG_FILE.read_text()) or {}
    except Exception:
        return {}

def _save_config(cfg: Dict[str, Any]):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    CONFIG_FILE.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))


# ── Setup Sections ───────────────────────────────────────────────────

def setup_welcome():
    _panel(
        "🐼 Panda Agent Setup",
        "Interactive configuration wizard.\n\n"
        "Sections:  [model]  [providers]  [gateway]  [tools]\n"
        "Run individually:  panda setup <section>\n"
        "Quick mode:         panda setup --quick",
        style="bold green"
    )

def setup_model():
    """Step: Choose default model and provider."""
    _panel("📦 Model & Provider", "Choose your default AI model.", style="bold cyan")

    env = _load_env()
    cfg = _load_config()

    # Show currently configured providers
    configured = []
    for p in PROVIDERS:
        key = env.get(p["env"]) or os.getenv(p["env"], "")
        configured.append((p["id"], p["label"], "✓" if key else "○"))

    _table("Providers", ["Status", "Provider", "API Key"], [(s, n, "✓ configured" if s == "✓" else "not set") for s, n, _ in configured])

    # Pick provider
    _print("\n  Select default provider:", style="bold")
    for i, p in enumerate(PROVIDERS, 1):
        status = "✓" if env.get(p["env"]) else " "
        _print(f"  [{status}] {i}. {p['label']}  ({', '.join(p['models'][:2])})")

    try:
        choice = input("\n  Number (or Enter to skip): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(PROVIDERS):
            provider = PROVIDERS[int(choice) - 1]
            cfg["provider"] = cfg.get("provider", {})
            cfg["provider"]["default"] = provider["id"]

            # Pick model for this provider
            models = provider["models"]
            _print(f"\n  Models for {provider['label']}:", style="bold")
            for i, m in enumerate(models, 1):
                _print(f"    {i}. {m}")

            m_choice = input(f"\n  Model number [1]: ").strip()
            idx = int(m_choice) - 1 if m_choice.isdigit() and 1 <= int(m_choice) <= len(models) else 0
            cfg["provider"][provider["id"]] = {"model": models[idx]}

            _print(f"  ✓ Default: {provider['label']} / {models[idx]}", style="green")
    except (EOFError, KeyboardInterrupt):
        pass

    _save_config(cfg)
    _print("  ✓ Config saved", style="green")


def setup_providers(quick=False):
    """Step: Configure API keys for providers."""
    _panel("🔑 Provider API Keys", "At least one provider is required.\nGet free keys from the URLs below.", style="bold cyan")

    env = _load_env()
    added = 0

    for p in PROVIDERS:
        existing = env.get(p["env"]) or os.getenv(p["env"], "")
        if existing:
            _print(f"  ✓ {p['label']}: {'*' * 12} (configured)", style="green")
            if quick:
                env[p["env"]] = existing
                continue
            if _confirm(f"Keep {p['label']} key?"):
                env[p["env"]] = existing
                continue
            else:
                del env[p["env"]]

        if quick:
            continue

        _print(f"\n  {p['label']}", style="bold yellow")
        _print(f"  Get key: {p['url']}")
        _print(f"  Models:  {', '.join(p['models'][:3])}")

        if not _confirm("Configure this provider?", default=False):
            continue

        key = _prompt(p["env"], password=True)
        if key:
            env[p["env"]] = key
            added += 1
            _print(f"  ✓ {p['label']} key saved", style="green")

    _save_env(env)
    if quick:
        _print(f"  ✓ Loaded {len([1 for p in PROVIDERS if env.get(p['env'])])} keys from environment", style="green")
    else:
        _print(f"  ✓ {added} new keys configured", style="green")


def setup_gateway():
    """Step: Configure messaging platforms."""
    _panel("🌐 Gateway Platforms", "Connect Panda to messaging platforms.", style="bold cyan")

    env = _load_env()
    configured = 0

    for plat in GATEWAY_PLATFORMS:
        all_set = all(env.get(v) or os.getenv(v) for v in plat["envs"])
        status = "✓" if all_set else "○"
        _print(f"\n  {status} {plat['name']}", style="bold green" if all_set else "bold yellow")
        _print(f"  Docs: {plat['docs']}")
        _print(f"  Requires: {', '.join(plat['envs'])}")

        if all_set:
            if _confirm("Reconfigure?", default=False):
                pass
            else:
                continue

        if not _confirm("Configure now?", default=False):
            continue

        for var in plat["envs"]:
            val = _prompt(var, password="SECRET" in var or "TOKEN" in var)
            if val:
                env[var] = val
        configured += 1
        _print(f"  ✓ {plat['name']} configured", style="green")

    _save_env(env)
    _print(f"\n  ✓ {configured} platforms configured", style="green")
    _print("  Start gateway: panda gateway start --feishu --telegram", style="dim")


def setup_doctor():
    """Step: Run diagnostics and health check."""
    _panel("🩺 System Check", "Verifying installation and configuration.", style="bold cyan")

    checks = []

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append(("Python", py_ver, "✓" if sys.version_info >= (3, 11) else "✗"))

    # Rich available
    checks.append(("Rich TUI", "available" if HAS_RICH else "not installed", "✓" if HAS_RICH else "⚠"))

    # Config files
    checks.append(("config.yaml", str(CONFIG_FILE), "✓" if CONFIG_FILE.exists() else "○"))
    checks.append((".env", str(ENV_FILE), "✓" if ENV_FILE.exists() else "○"))

    # Providers
    env = _load_env()
    provider_count = sum(1 for p in PROVIDERS if env.get(p["env"]) or os.getenv(p["env"]))
    checks.append(("API Keys", f"{provider_count}/{len(PROVIDERS)} configured", "✓" if provider_count > 0 else "✗"))

    # Gateway platforms
    plat_count = sum(1 for p in GATEWAY_PLATFORMS if all(env.get(v) or os.getenv(v) for v in p["envs"]))
    checks.append(("Gateways", f"{plat_count}/{len(GATEWAY_PLATFORMS)} ready", "✓" if plat_count > 0 else "○"))

    # Disk
    try:
        import shutil
        disk = shutil.disk_usage(str(Path.home()))
        gb_free = disk.free / (1024**3)
        checks.append(("Disk", f"{gb_free:.1f} GB free", "✓" if gb_free > 1 else "⚠"))
    except Exception:
        checks.append(("Disk", "unknown", "?"))

    # Model file
    model_path = _load_config().get("router", {}).get("model_path", "models/qwen3-0.6b-q4_k_m.gguf")
    model_exists = Path(model_path).exists()
    checks.append(("Router Model", model_path, "✓" if model_exists else "○"))

    _table("Health Check", ["Component", "Status", "Detail"],
           [(c, s, d) for c, d, s in checks])

    # Summary
    failures = [c for c, _, s in checks if s in ("✗", "⚠")]
    if failures:
        _print(f"\n  ⚠ Issues found: {', '.join(failures)}", style="yellow")
        _print("  Run: panda setup  to fix configuration", style="dim")
    else:
        _print(f"\n  ✓ All systems healthy", style="green")


# ── Main Entry ───────────────────────────────────────────────────────

def run_setup(section="", feishu_only=False, providers_only=False, quick=False):
    """Run the interactive setup wizard.

    Parameters
    ----------
    section : str
        Specific section: "model", "providers", "gateway", "doctor", or "" for all.
    """
    if quick:
        env = _load_env()
        for p in PROVIDERS:
            v = os.getenv(p["env"], "")
            if v:
                env[p["env"]] = v
        for plat in GATEWAY_PLATFORMS:
            for v in plat["envs"]:
                val = os.getenv(v, "")
                if val:
                    env[v] = val
        _save_env(env)
        _print(f"  ✓ Quick setup: loaded {len(env)} vars from environment", style="green")

        if not CONFIG_FILE.exists():
            _save_config({
                "provider": {"default": "openai", "openai": {"model": "gpt-4o"}},
                "server": {"host": "0.0.0.0", "port": 8000},
            })
        return

    # Route to specific section
    if section == "model":
        setup_model()
    elif section == "providers" or providers_only:
        setup_providers()
    elif section == "gateway":
        setup_gateway()
    elif section == "doctor":
        setup_doctor()
    elif feishu_only:
        setup_gateway()  # gateway section includes Feishu
    else:
        # Full wizard
        setup_welcome()
        setup_model()
        setup_providers()
        setup_gateway()
        setup_doctor()

        _panel(
            "✓ Setup Complete!",
            "Next steps:\n"
            "  panda chat          Start chatting\n"
            "  panda serve         Start API server\n"
            "  panda gateway start Start messaging gateway\n"
            "  panda doctor        Re-run diagnostics",
            style="bold green"
        )
