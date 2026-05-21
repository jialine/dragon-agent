"""
Panda Setup — Interactive configuration wizard.

Guides the user through Feishu bot setup, provider API keys,
default model preferences, and connectivity testing.

Usage:
    panda setup              Full interactive setup
    panda setup --feishu     Feishu-only quick setup
    panda setup --providers  Provider keys only
    panda setup --quick      Non-interactive with defaults
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Constants ──────────────────────────────────────────────────────

ENV_FILE = Path(".env")
PROJECT_ROOT = Path(__file__).parent.parent

PROVIDER_TEMPLATES = [
    {"name": "openai",       "env": "OPENAI_API_KEY",     "label": "OpenAI (GPT-4o, GPT-4o-mini)",    "url": "https://platform.openai.com/api-keys"},
    {"name": "deepseek",     "env": "DEEPSEEK_API_KEY",   "label": "DeepSeek (V3, Reasoner)",          "url": "https://platform.deepseek.com/api_keys"},
    {"name": "anthropic",    "env": "ANTHROPIC_API_KEY",  "label": "Anthropic (Claude Sonnet, Haiku)","url": "https://console.anthropic.com/keys"},
    {"name": "google",       "env": "GOOGLE_API_KEY",     "label": "Google (Gemini 2.5)",             "url": "https://aistudio.google.com/apikey"},
    {"name": "xai",          "env": "XAI_API_KEY",        "label": "xAI (Grok-3)",                    "url": "https://console.x.ai"},
    {"name": "moonshot",     "env": "MOONSHOT_API_KEY",   "label": "Moonshot / Kimi (月之暗面)",      "url": "https://platform.moonshot.cn/console/api-keys"},
    {"name": "together",     "env": "TOGETHER_API_KEY",   "label": "Together AI",                    "url": "https://api.together.xyz/settings/api-keys"},
    {"name": "groq",         "env": "GROQ_API_KEY",       "label": "Groq",                           "url": "https://console.groq.com/keys"},
    {"name": "mistral",      "env": "MISTRAL_API_KEY",    "label": "Mistral AI",                     "url": "https://console.mistral.ai/api-keys"},
    {"name": "openrouter",   "env": "OPENROUTER_API_KEY", "label": "OpenRouter",                     "url": "https://openrouter.ai/keys"},
    {"name": "cohere",       "env": "COHERE_API_KEY",     "label": "Cohere",                         "url": "https://dashboard.cohere.com/api-keys"},
    {"name": "perplexity",   "env": "PERPLEXITY_API_KEY", "label": "Perplexity",                     "url": "https://www.perplexity.ai/settings/api"},
    {"name": "fireworks",    "env": "FIREWORKS_API_KEY",  "label": "Fireworks AI",                   "url": "https://fireworks.ai/account/api-keys"},
    {"name": "replicate",    "env": "REPLICATE_API_KEY",  "label": "Replicate",                      "url": "https://replicate.com/account/api-tokens"},
]

# ── Color Helpers ──────────────────────────────────────────────────

def G(s): return "\033[0;32m" + s + "\033[0m"
def B(s): return "\033[0;34m" + s + "\033[0m"
def Y(s): return "\033[1;33m" + s + "\033[0m"
def R(s): return "\033[0;31m" + s + "\033[0m"

def prompt(text, default="", secret=False):
    if default:
        text = text + " [" + default + "]"
    if secret:
        import getpass
        v = getpass.getpass("  " + text + ": ").strip()
    else:
        v = input("  " + text + ": ").strip()
    return v or default

def prompt_yn(text, default=True):
    s = " [Y/n]" if default else " [y/N]"
    a = input("  " + text + s + ": ").strip().lower()
    if not a:
        return default
    return a in ("y", "yes")

# ── Setup Steps ────────────────────────────────────────────────────

def setup_feishu(quick=False):
    print("\n" + B("═══ Step 1: Feishu / Lark ═══"))
    print("  Feishu open platform: https://open.feishu.cn/app\n")
    env = {}
    steps = [
        ("FEISHU_APP_ID", "Feishu App ID", False),
        ("FEISHU_APP_SECRET", "Feishu App Secret", True),
        ("FEISHU_VERIFICATION_TOKEN", "Verification Token (optional)", False),
    ]
    if quick:
        for k, _, _ in steps:
            v = os.getenv(k, "")
            if v:
                env[k] = v
                print("  " + G("✓") + " " + k + "=" + v[:12] + "... (from env)")
        return env
    for k, label, is_secret in steps:
        print("  " + label)
        v = prompt(k, os.getenv(k, ""), secret=is_secret)
        if v:
            env[k] = v
            print("  " + G("✓ Saved"))
        elif k != "FEISHU_VERIFICATION_TOKEN":
            print("  " + R("✗ Skipped (required)"))
        print()
    return env

def setup_providers(quick=False):
    print("\n" + B("═══ Step 2: Provider API Keys ═══"))
    print("  At least one provider required.\n")
    env = {}
    if quick:
        for p in PROVIDER_TEMPLATES:
            v = os.getenv(p["env"], "")
            if v:
                env[p["env"]] = v
                print("  " + G("✓") + " " + p["name"] + ": " + v[:12] + "... (from env)")
        return env
    for p in PROVIDER_TEMPLATES:
        existing = os.getenv(p["env"], "")
        if existing:
            print("  " + p["label"])
            print("  " + G("✓ Already set: " + existing[:12] + "...") + " (enter to keep)")
            v = prompt(p["env"], "", secret=True)
            if v.lower() == "clear":
                print("  " + Y("⚠ Removed"))
            elif v:
                env[p["env"]] = v
                print("  " + G("✓ Updated"))
            else:
                env[p["env"]] = existing
            print()
        else:
            if not prompt_yn("Configure " + p["label"] + "?", default=False):
                continue
            print("  " + Y("Get key: " + p["url"]))
            v = prompt(p["env"], "", secret=True)
            if v:
                env[p["env"]] = v
                print("  " + G("✓ Saved"))
            print()
    return env

def setup_defaults():
    print("\n" + B("═══ Step 3: Default Settings ═══"))
    d = {}
    m = prompt("Default chat model", "gpt-4o")
    d["PANDA_DEFAULT_MODEL"] = m
    p = prompt("Default provider", "openai")
    d["PANDA_DEFAULT_PROVIDER"] = p
    port = prompt("Server port", "8000")
    d["PANDA_SERVER_PORT"] = port
    print("  " + G("✓ Default: " + m + " (" + p + ") on port " + port))
    print()
    return d

def test_feishu(env):
    app_id = env.get("FEISHU_APP_ID", os.getenv("FEISHU_APP_ID", ""))
    app_secret = env.get("FEISHU_APP_SECRET", os.getenv("FEISHU_APP_SECRET", ""))
    if not app_id or not app_secret:
        print("  " + Y("⚠ Skipped — no Feishu credentials"))
        return False
    print("\n" + B("═══ Step 4: Connectivity Test ═══"))
    print("  Testing Feishu API...")
    try:
        import httpx
        async def t():
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret})
                if r.status_code == 200:
                    d = r.json()
                    if d.get("code") == 0:
                        return True, "Tenant token OK"
                    return False, "API error: " + str(d.get("msg", "?"))
                return False, "HTTP " + str(r.status_code)
        ok, msg = asyncio.run(t())
        if ok:
            print("  " + G("✓ " + msg))
        else:
            print("  " + R("✗ " + msg))
        return ok
    except Exception as e:
        print("  " + R("✗ Connection failed: " + str(e)))
        return False

def write_env(env_all):
    print("\n" + B("═══ Step 5: Save Configuration ═══"))
    t = ENV_FILE
    if t.exists():
        if not prompt_yn(t.name + " exists. Overwrite?", default=False):
            print("  " + Y("⚠ Kept existing .env"))
            return
    lines = ["# Panda Agent — Environment Configuration", "# Generated by: panda setup", ""]
    # Feishu
    lines.append("# ── Feishu / Lark ──")
    for k in sorted(env_all):
        if k.startswith("FEISHU_"):
            lines.append(k + "=" + env_all[k])
    # Providers
    lines.append("\n# ── Provider API Keys ──")
    for p in PROVIDER_TEMPLATES:
        if p["env"] in env_all:
            lines.append(p["env"] + "=" + env_all[p["env"]])
    # Defaults
    lines.append("\n# ── Defaults ──")
    for k in sorted(env_all):
        if k.startswith("PANDA_"):
            lines.append(k + "=" + env_all[k])
    t.write_text("\n".join(lines) + "\n")
    print("  " + G("✓ Written .env (" + str(len(env_all)) + " vars)"))

def generate_env_example():
    t = PROJECT_ROOT / ".env.example"
    lines = [
        "# Panda Agent — Environment Configuration Template",
        "# Copy to .env:  cp .env.example .env",
        "",
        "# ── Feishu / Lark ──",
        "# Get from: https://open.feishu.cn/app",
        "FEISHU_APP_ID=cli_xxxxxxxxxxxxx",
        "FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "# FEISHU_VERIFICATION_TOKEN=",
        "",
        "# ── Provider API Keys (at least one required) ──",
    ]
    for p in PROVIDER_TEMPLATES:
        lines.append("# " + p["label"])
        lines.append("# " + p["url"])
        lines.append("# " + p["env"] + "=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        lines.append("")
    lines.extend([
        "# ── Local ──",
        "# OLLAMA_HOST=http://localhost:11434",
        "",
        "# ── Cloud/Enterprise ──",
        "# AZURE_OPENAI_API_KEY=xxx",
        "# AZURE_OPENAI_ENDPOINT=https://xxx.openai.azure.com",
        "# VERTEX_PROJECT_ID=xxx",
        "# AWS_ACCESS_KEY_ID=xxx",
        "# AWS_REGION=us-east-1",
        "# CLOUDFLARE_API_KEY=xxx",
        "# CLOUDFLARE_ACCOUNT_ID=xxx",
        "",
        "# ── Defaults ──",
        "PANDA_DEFAULT_MODEL=gpt-4o",
        "PANDA_DEFAULT_PROVIDER=openai",
        "PANDA_SERVER_PORT=8000",
        "",
    ])
    t.write_text("\n".join(lines))
    print("  " + G("✓ Generated .env.example"))

def run_setup(feishu_only=False, providers_only=False, quick=False):
    if quick:
        feishu_vars = setup_feishu(quick=True)
        provider_vars = setup_providers(quick=True)
        all_v = {}
        all_v.update(feishu_vars)
        all_v.update(provider_vars)
        write_env(all_v)
        return
    print("\n" + B("🐼 Panda Agent Setup"))
    print("  Interactive configuration wizard. Ctrl+C to cancel.\n")
    env = {}
    defaults = {}
    if not providers_only:
        env.update(setup_feishu())
    if not feishu_only:
        env.update(setup_providers())
    if not feishu_only and not providers_only:
        defaults = setup_defaults()
    if not quick and not providers_only:
        test_feishu(env)
    all_v = {}
    all_v.update(env)
    all_v.update(defaults)
    write_env(all_v)
    if not (PROJECT_ROOT / ".env.example").exists():
        generate_env_example()
    print("\n" + G("╔══════════════════════════════════╗"))
    print(G("║   Panda setup complete! 🐼       ║"))
    print(G("╚══════════════════════════════════╝"))
    print("\n  panda serve       Start API server")
    print("  panda gateway start  Start multi-platform Gateway")
    print("  panda chat         Start chatting")
    print("  panda doctor       Run diagnostics\n")

