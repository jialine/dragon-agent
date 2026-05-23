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
    {"id": "openai",    "env": "OPENAI_API_KEY",    "label": "OpenAI",          "models": [
        {"name": "gpt-4o",           "desc": "Best overall, 128K context, multimodal",          "ctx": "128K", "tier": "premium"},
        {"name": "gpt-4o-mini",      "desc": "Fast and affordable, 128K context",                "ctx": "128K", "tier": "budget"},
        {"name": "gpt-4.1",          "desc": "Latest, improved coding and reasoning",            "ctx": "1M",   "tier": "premium"},
        {"name": "o4-mini",          "desc": "Fast reasoning model, cost-effective",              "ctx": "200K", "tier": "budget"},
    ], "url": "https://platform.openai.com/api-keys"},
    {"id": "deepseek",  "env": "DEEPSEEK_API_KEY",  "label": "DeepSeek",        "models": [
        {"name": "deepseek-chat",     "desc": "General purpose, best value",                    "ctx": "128K", "tier": "budget"},
        {"name": "deepseek-reasoner", "desc": "Advanced reasoning (R1), math and code",          "ctx": "128K", "tier": "premium"},
    ], "url": "https://platform.deepseek.com/api_keys"},
    {"id": "anthropic", "env": "ANTHROPIC_API_KEY", "label": "Anthropic",       "models": [
        {"name": "claude-sonnet-4-20250514", "desc": "Best coding agent, 200K context",          "ctx": "200K", "tier": "premium"},
        {"name": "claude-haiku-3.5",         "desc": "Fast, affordable, good for simple tasks",   "ctx": "200K", "tier": "budget"},
        {"name": "claude-opus-4-20250514",   "desc": "Most capable, complex analysis",           "ctx": "200K", "tier": "premium"},
    ], "url": "https://console.anthropic.com/keys"},
    {"id": "google",    "env": "GOOGLE_API_KEY",    "label": "Google Gemini",   "models": [
        {"name": "gemini-2.5-flash", "desc": "Fast, 1M context, great for large docs",          "ctx": "1M",   "tier": "budget"},
        {"name": "gemini-2.5-pro",   "desc": "Most capable, 1M context, deep reasoning",         "ctx": "1M",   "tier": "premium"},
    ], "url": "https://aistudio.google.com/apikey"},
    {"id": "xai",       "env": "XAI_API_KEY",       "label": "xAI Grok",        "models": [
        {"name": "grok-3", "desc": "DeepSearch reasoning, real-time knowledge",                "ctx": "128K", "tier": "premium"},
    ], "url": "https://console.x.ai"},
    {"id": "openrouter","env": "OPENROUTER_API_KEY","label": "OpenRouter",      "models": [
        {"name": "openai/gpt-4o",            "desc": "OpenAI via OpenRouter",                    "ctx": "128K", "tier": "premium"},
        {"name": "anthropic/claude-sonnet-4","desc": "Claude Sonnet via OpenRouter",              "ctx": "200K", "tier": "premium"},
        {"name": "google/gemini-2.5-flash",  "desc": "Gemini Flash via OpenRouter",              "ctx": "1M",   "tier": "budget"},
    ], "url": "https://openrouter.ai/keys"},
    {"id": "moonshot",  "env": "MOONSHOT_API_KEY",  "label": "Moonshot/Kimi",   "models": [
        {"name": "moonshot-v1-8k",  "desc": "Chinese-optimized, 8K context",                    "ctx": "8K",   "tier": "budget"},
        {"name": "kimi-k2",         "desc": "Latest Kimi, strong Chinese reasoning",             "ctx": "128K", "tier": "premium"},
    ], "url": "https://platform.moonshot.cn/console/api-keys"},
    {"id": "together",  "env": "TOGETHER_API_KEY",  "label": "Together AI",     "models": [
        {"name": "meta-llama/Llama-4-Maverick", "desc": "Open-source, strong general purpose",   "ctx": "128K", "tier": "budget"},
    ], "url": "https://api.together.xyz/settings/api-keys"},
    {"id": "groq",      "env": "GROQ_API_KEY",      "label": "Groq",            "models": [
        {"name": "llama-3.3-70b", "desc": "Ultra-fast inference, great latency",                 "ctx": "128K", "tier": "budget"},
    ], "url": "https://console.groq.com/keys"},
    {"id": "mistral",   "env": "MISTRAL_API_KEY",   "label": "Mistral AI",      "models": [
        {"name": "mistral-large", "desc": "Strong multilingual, 128K context",                  "ctx": "128K", "tier": "premium"},
    ], "url": "https://console.mistral.ai/api-keys"},
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
        model_names = [m["name"] for m in p["models"][:2]]
        _print(f"  [{status}] {i}. {p['label']}  ({', '.join(model_names)})")

    try:
        choice = input("\n  Number (or Enter to skip): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(PROVIDERS):
            provider = PROVIDERS[int(choice) - 1]
            cfg["provider"] = cfg.get("provider", {})
            cfg["provider"]["default"] = provider["id"]

            # Pick model for this provider
            models = provider["models"]
            _print(f"\n  Models for {provider['label']}:", style="bold")
            _table(
                f"Models for {provider['label']}",
                ["#", "Model", "Context", "Tier", "Description"],
                [
                    (str(i), m["name"], m["ctx"], m["tier"].upper(), m["desc"])
                    for i, m in enumerate(models, 1)
                ]
            )

            m_choice = input(f"\n  Model number [1]: ").strip()
            idx = int(m_choice) - 1 if m_choice.isdigit() and 1 <= int(m_choice) <= len(models) else 0
            model = models[idx]
            cfg["provider"][provider["id"]] = {"model": model["name"]}

            _print(f"  ✓ Default: {provider['label']} / {model['name']} ({model['ctx']})", style="green")
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


# ── Model Picker (panda model) ──────────────────────────────────────

def pick_model(name: Optional[str] = None):
    """Interactive model picker — like Hermes 'hermes model'.

    Shows all configured providers with available models, lets you
    switch the default provider and model interactively.
    """
    _panel(
        "🤖 Model Picker",
        "Choose your default AI model and provider.",
        style="bold magenta"
    )

    env = _load_env()
    cfg = _load_config()

    # Show current
    default_provider = cfg.get("provider", {}).get("default", "openai")
    current_model = cfg.get("provider", {}).get(default_provider, {}).get("model", "")

    _print(f"\n  Current: {default_provider} / {current_model or 'not set'}", style="bold green")

    # Show all configured providers with status
    rows = []
    configured_providers = []
    for p in PROVIDERS:
        has_key = bool(env.get(p["env"]) or os.getenv(p["env"]))
        status = "✓" if has_key else "○"
        active = " ◀ active" if p["id"] == default_provider else ""
        rows.append((status, p["id"], p["label"] + active))

    _table("Configured Providers", ["Key", "Provider", "Status"], rows)

    # If name specified, switch directly
    if name:
        if "/" in name:
            prov_id, model_name = name.split("/", 1)
        else:
            prov_id = default_provider
            model_name = name

        provider = next((p for p in PROVIDERS if p["id"] == prov_id), None)
        if provider:
            has_key = bool(env.get(provider["env"]) or os.getenv(provider["env"]))
            if not has_key:
                _print(f"\n  ⚠ {provider['label']} has no API key configured", style="yellow")
                if _confirm("Configure API key now?"):
                    key = _prompt(provider["env"], password=True)
                    if key:
                        env[provider["env"]] = key
                        _save_env(env)
                        _print(f"  ✓ Key saved", style="green")

            # Find model info for display
            model_info = next((m for m in provider["models"] if m["name"] == model_name), None)
            cfg["provider"] = cfg.get("provider", {})
            cfg["provider"]["default"] = prov_id
            cfg["provider"][prov_id] = cfg["provider"].get(prov_id, {})
            cfg["provider"][prov_id]["model"] = model_name
            _save_config(cfg)

            ctx = model_info["ctx"] if model_info else "?"
            desc = model_info["desc"] if model_info else ""
            _panel(
                "✓ Model Updated",
                f"Default: {prov_id} / {model_name}  ({ctx})\n"
                f"{desc}\n\n"
                f"Start chatting: panda chat",
                style="bold green"
            )
            return
        else:
            _print(f"  ✗ Unknown provider: {prov_id}", style="red")
            return

    # Interactive: pick provider
    _print("\n  ── Configured Providers ──", style="bold cyan")
    for i, p in enumerate(PROVIDERS, 1):
        has_key = bool(env.get(p["env"]) or os.getenv(p["env"]))
        if has_key:
            mark = "✓"
            active = " ◀ active" if p["id"] == default_provider else ""
            model_count = len(p["models"])
            _print(f"  {mark} {i:>2}. {p['label']}{active}  ({model_count} models)", style="green")

    _print("\n  ── Available Providers ──", style="bold yellow")
    for i, p in enumerate(PROVIDERS, 1):
        has_key = bool(env.get(p["env"]) or os.getenv(p["env"]))
        if not has_key:
            _print(f"  ○ {i:>2}. {p['label']}  — {p['url']}", style="dim")

    try:
        choice = input("\n  Number (Enter to keep current): ").strip()
        if not choice:
            _print("  No change", style="dim")
            return

        idx = int(choice) - 1
        if 0 <= idx < len(PROVIDERS):
            provider = PROVIDERS[idx]

            # Check key
            has_key = bool(env.get(provider["env"]) or os.getenv(provider["env"]))
            if not has_key:
                _print(f"\n  ⚠ {provider['label']} needs an API key", style="yellow")
                _print(f"  Get one at: {provider['url']}")
                if _confirm("Enter API key now?"):
                    key = _prompt(provider["env"], password=True)
                    if key:
                        env[provider["env"]] = key
                        _save_env(env)
                        _print(f"  ✓ Key saved", style="green")
                    else:
                        _print("  Cancelled", style="dim")
                        return

            # Pick model — show rich table
            models = provider["models"]
            _print(f"\n  {provider['label']} — available models:", style="bold")

            _table(
                f"Models for {provider['label']}",
                ["#", "Model", "Context", "Tier", "Description"],
                [
                    (str(i), m["name"], m["ctx"], m["tier"].upper(), m["desc"])
                    for i, m in enumerate(models, 1)
                ]
            )

            m_choice = input(f"  Model number [1]: ").strip()
            m_idx = int(m_choice) - 1 if m_choice.isdigit() and 1 <= int(m_choice) <= len(models) else 0
            model = models[m_idx]

            # Save
            cfg["provider"] = cfg.get("provider", {})
            cfg["provider"]["default"] = provider["id"]
            cfg["provider"][provider["id"]] = cfg["provider"].get(provider["id"], {})
            cfg["provider"][provider["id"]]["model"] = model["name"]
            _save_config(cfg)

            _panel(
                f"✓ Model Updated — {model['name']}",
                f"Provider: {provider['label']}\n"
                f"Model:    {model['name']}\n"
                f"Context:  {model['ctx']}\n"
                f"Description: {model['desc']}\n\n"
                f"Start: panda chat -p {provider['id']} -m {model['name']}",
                style="bold green"
            )
    except (ValueError, EOFError, KeyboardInterrupt):
        _print("  Cancelled", style="dim")
