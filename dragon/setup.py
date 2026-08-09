"""
Dragon Setup — Rich interactive configuration wizard.

Sections (modular, can run independently):
    dragon setup              Full wizard (all sections)
    dragon setup model        Model & provider picker
    dragon setup providers    API keys only
    dragon setup gateway      Gateway/platform config
    dragon setup --quick      Non-interactive, env vars only

Design principles (aligned with Hermes Agent):
    - Rich panels, tables, and progress for polished UX
    - Each section is self-contained and idempotent
    - API keys are masked, validated, and persisted
    - Config is saved to both .env and config.yaml
"""

from __future__ import annotations

import os
import sys
import json
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

ENV_FILE = Path.home() / ".dragon" / ".env"
CONFIG_FILE = Path.home() / ".dragon" / "config.yaml"

# ── Dispatch Config (唯一入口: andlapi.cn / sangyuye.com) ──────────

# Dragon 所有 LLM 调用统一走 dispatch 网关，不直连任何第三方 API
DISPATCH_CONFIG = {
    "id": "dispatch",
    "env": "DEEPSEEK_API_KEY",  # 兼容旧环境变量，实际用 DRAGON_API_KEY
    "label": "andlapi.cn 调度网关",
    "description": "DeepSeek V4 Pro · GPT-4o · Claude · Gemini — 一个 Key 全搞定",
    "register_url": "https://api.andlapi.cn",
    "alt_url": "https://api.sangyuye.com",
}

PROVIDERS = [  # 保留变量名以兼容旧代码，但只包含 dispatch
    {
        "id": "dispatch",
        "env": "DEEPSEEK_API_KEY",
        "label": "andlapi.cn / sangyuye.com",
        "models": [
            {"name": "deepseek-v4-pro", "desc": "DeepSeek V4 Pro, best all-around", "ctx": "128K", "tier": "premium"},
            {"name": "deepseek-chat", "desc": "DeepSeek V3, best value", "ctx": "128K", "tier": "budget"},
            {"name": "gpt-4o", "desc": "OpenAI GPT-4o, multimodal", "ctx": "128K", "tier": "premium"},
            {"name": "gpt-4.1", "desc": "GPT-4.1, coding + reasoning", "ctx": "1M", "tier": "premium"},
            {"name": "gemini-2.5-pro", "desc": "Gemini 2.5 Pro, deep reasoning", "ctx": "1M", "tier": "premium"},
            {"name": "gemini-2.5-flash", "desc": "Gemini 2.5 Flash, fast + large docs", "ctx": "1M", "tier": "budget"},
            {"name": "claude-sonnet-4", "desc": "Claude Sonnet 4, best coding", "ctx": "200K", "tier": "premium"},
        ],
        "url": "https://api.andlapi.cn",
    }
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

def _check_agilemind():
    """AgileMind removed — all calls now go through dispatch (andlapi.cn)."""
    return None


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
    lines = ["# Dragon Agent — Environment Configuration", "# Generated by: dragon setup", ""]
    sections = {
        "Feishu / Lark":        [k for k in env if k.startswith("FEISHU_")],
        "Telegram":             [k for k in env if k.startswith("TELEGRAM_")],
        "Discord":              [k for k in env if k.startswith("DISCORD_")],
        "WeChat":               [k for k in env if k.startswith("WECHAT_")],
        "Provider API Keys":    [k for k in env if k.endswith("_API_KEY") and not k.startswith(("FEISHU_", "TELEGRAM_", "DISCORD_", "WECHAT_"))],
        "Defaults":             [k for k in env if k.startswith("DRAGON_")],
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
        "🐉 Dragon Agent Setup",
        "Interactive configuration wizard.\n\n"
        "Sections:  [model]  [providers]  [gateway]  [tools]\n"
        "Run individually:  dragon setup <section>\n"
        "Quick mode:         dragon setup --quick",
        style="bold green"
    )

def _fetch_models(api_key: str, base_url: str) -> List[Dict[str, str]]:
    """Fetch available models from the dispatch API (/v1/models)."""
    import urllib.request
    import urllib.error
    import ssl

    results = []
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            models = data.get("data", [])
            for m in models:
                mid = m.get("id", "")
                owned = m.get("owned_by", "")
                if mid and not mid.startswith(("dall-e", "tts", "whisper", "moderation", "embedding")):
                    results.append({
                        "name": mid,
                        "desc": owned or "AI model",
                        "ctx": "—",
                        "tier": "premium",
                    })
    except Exception as e:
        _print(f"  ⚠ 无法从 API 获取模型列表: {e}", style="yellow")
    
    return results


def setup_model():
    """Step: Choose default model from dispatch (andlapi.cn)."""
    _panel("📦 Model & Provider", "所有模型通过 andlapi.cn 调度网关接入", style="bold cyan")

    env = _load_env()
    cfg = _load_config()

    # Check if API key is set
    key = env.get("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        _print(f"  ○ 未配置 API Key，请先运行: dragon setup providers", style="yellow")
        return
    
    _print(f"  ✓ API Key: {'*' * 12} (已配置)", style="green")
    
    base_url = cfg.get("dispatch", {}).get("global_api", {}).get("base_url", "https://api.andlapi.cn/v1")
    _print(f"\n  调度网关: {base_url}", style="dim")
    
    # Fetch real model list from API
    _print(f"\n  正在拉取可用模型...", style="bold")
    live_models = _fetch_models(key, base_url)
    
    if live_models:
        models = live_models
        _print(f"  ✓ 从 API 获取到 {len(models)} 个模型", style="green")
    else:
        models = PROVIDERS[0]["models"]
        _print(f"  ⚠ 使用本地内置模型列表 ({len(models)} 个)", style="yellow")
    
    # Pick default model
    _print(f"\n  可用模型:", style="bold")
    for i, m in enumerate(models, 1):
        _print(f"  {i}. {m['name']} — {m['desc']} ({m['ctx']})")
    
    current = cfg.get("dispatch", {}).get("global_api", {}).get("model", "")
    if current:
        _print(f"\n  当前默认: {current}", style="dim")
    
    try:
        choice = input(f"\n  选择默认模型 [1]: ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(models) else 0
        model = models[idx]
        cfg["dispatch"] = cfg.get("dispatch", {})
        cfg["dispatch"]["global_api"] = cfg["dispatch"].get("global_api", {})
        cfg["dispatch"]["global_api"]["model"] = model["name"]
        _save_config(cfg)
        _print(f"  ✓ 默认: {model['name']} ({model['ctx']})", style="green")
    except (EOFError, KeyboardInterrupt):
        pass

    _print("  ✓ Config saved", style="green")


def setup_providers(quick=False):
    """Step: Configure dispatch API key."""
    _panel("🔑 API Key", "Dragon 统一走 andlapi.cn 调度网关，一个 Key 用所有模型\n注册: https://api.andlapi.cn", style="bold cyan")

    env = _load_env()
    key = env.get("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")

    if key:
        _print(f"  ✓ API Key: {'*' * 12} (已配置)", style="green")
        if quick:
            return
        if _confirm("更换 API Key?"):
            key = ""
        else:
            return

    _print("\n  获取 Key: https://api.andlapi.cn (注册送 ¥10)", style="bold")
    _print("  备用:     https://api.sangyuye.com", style="dim")
    
    key = _prompt("DEEPSEEK_API_KEY", password=True)
    if key:
        env["DEEPSEEK_API_KEY"] = key
        _save_env(env)
        # Update config.yaml dispatch
        cfg = _load_config()
        cfg["dispatch"] = cfg.get("dispatch", {})
        cfg["dispatch"]["global_api"] = cfg["dispatch"].get("global_api", {})
        cfg["dispatch"]["global_api"]["api_key"] = key
        cfg["dispatch"]["global_api"]["base_url"] = "https://api.andlapi.cn/v1"
        if not cfg["dispatch"]["global_api"].get("model"):
            cfg["dispatch"]["global_api"]["model"] = "deepseek-v4-pro"
        _save_config(cfg)
        _print(f"  ✓ API Key 已保存", style="green")


def setup_gateway():
    """Step: Configure messaging platforms."""
    _panel("🌐 Gateway Platforms", "Connect Dragon to messaging platforms.", style="bold cyan")

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
    _print("  Start gateway: dragon gateway start --feishu --telegram", style="dim")


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
    cloud_count = sum(1 for p in PROVIDERS if env.get(p["env"]) or os.getenv(p["env"]))
    checks.append(("API Keys", f"{cloud_count}/{len(PROVIDERS)} configured", "✓" if cloud_count > 0 else "✗"))

    # AgileMind Engine
    agilemind_url = _check_agilemind()
    checks.append(("AgileMind API", agilemind_url or "not configured", "🐉" if agilemind_url and os.getenv("AGILEMIND_API_KEY") else "○"))

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
    model_path = _load_config().get("router", {}).get("model_path", "models/qwen2-1.5b-q4_k_m.gguf")
    model_exists = Path(model_path).exists()
    checks.append(("Router Model", model_path, "✓" if model_exists else "○"))

    _table("Health Check", ["Component", "Status", "Detail"],
           [(c, s, d) for c, d, s in checks])

    # Summary
    failures = [c for c, _, s in checks if s in ("✗", "⚠")]
    if failures:
        _print(f"\n  ⚠ Issues found: {', '.join(failures)}", style="yellow")
        _print("  Run: dragon setup  to fix configuration", style="dim")
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
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if key:
            env["DEEPSEEK_API_KEY"] = key
        for plat in GATEWAY_PLATFORMS:
            for v in plat["envs"]:
                val = os.getenv(v, "")
                if val:
                    env[v] = val
        _save_env(env)
        _print(f"  ✓ Quick setup: loaded {len(env)} vars from environment", style="green")

        if not CONFIG_FILE.exists():
            _save_config({
                "dispatch": {
                    "global_api": {
                        "model": "deepseek-v4-pro",
                        "base_url": "https://api.andlapi.cn/v1",
                    }
                },
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
        # Launch device-code OAuth QR onboarding
        _print("\n  🚀 启动飞书一键扫码创建...", style="bold cyan")
        _print("  如需手动输入 App ID/Secret，请用: dragon setup gateway\n")
        script = Path(__file__).resolve().parent.parent / "scripts" / "feishu_onboard.py"
        if script.exists():
            import subprocess
            subprocess.run([sys.executable, str(script)])
        else:
            _print("  ✗ feishu_onboard.py not found. 请更新 Dragon Agent.", style="red")
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
            "  dragon chat          Start chatting\n"
            "  dragon serve         Start API server\n"
            "  dragon gateway start Start messaging gateway\n"
            "  dragon doctor        Re-run diagnostics",
            style="bold green"
        )


# ── Model Picker (dragon model) ──────────────────────────────────────

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
                f"Start chatting: dragon chat",
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
                f"Start: dragon chat -p {provider['id']} -m {model['name']}",
                style="bold green"
            )
    except (ValueError, EOFError, KeyboardInterrupt):
        _print("  Cancelled", style="dim")
