#!/usr/bin/env python3
"""
Dragon CLI — Command-line interface for Dragon Agent.

Usage:
    dragon chat              Start interactive chat
    dragon serve             Start API server
    dragon gateway           Start multi-platform gateway
    dragon mcp               Start MCP server
    dragon config            Manage configuration (init, validate, show, check)
    dragon skills            Manage self-evolving skills
    dragon tools             Manage tools
    dragon sessions          Manage sessions (list, export, stats, search, get, delete)
    dragon cron              Manage scheduled jobs
    dragon profile           Manage profiles (list, create, edit, clone, export, import, use, delete)
    dragon test              Run tests
    dragon doctor            运行诊断检查
    dragon version           Show version
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

VERSION = "1.2.0"

logger = logging.getLogger("dragon.cli")


def main():
    # Load .env file for API keys and platform credentials
    try:
        from dotenv import load_dotenv
        env_path = Path.home() / ".dragon" / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        prog="dragon",
        description="Dragon Agent — Self-Evolving AI Agent Framework",
    )
    parser.add_argument("--version", "-V", action="store_true", help="Show version")

    sub = parser.add_subparsers(dest="command", help="Commands")

    # ── chat ──────────────────────────────────────────────────────
    chat_p = sub.add_parser("chat", help="Start interactive chat")
    chat_p.add_argument("-q", "--query", help="Single query (non-interactive)")
    chat_p.add_argument("-m", "--model", default="gpt-4o", help="Model to use")
    chat_p.add_argument("-p", "--provider", default="openai", help="Provider name")
    chat_p.add_argument("--stream", action="store_true", help="Stream output")

    # ── serve ─────────────────────────────────────────────────────
    serve_p = sub.add_parser("serve", help="Start API server")
    serve_p.add_argument("--host", default="0.0.0.0", help="Host to bind")
    serve_p.add_argument("--port", type=int, default=8000, help="Port")
    serve_p.add_argument("--reload", action="store_true", help="Auto-reload")

    # ── gateway ───────────────────────────────────────────────────
    gw_p = sub.add_parser("gateway", help="Start or check multi-platform gateway")
    gw_p.add_argument("action", nargs="?", default="start", choices=["start", "status", "install"],
                      help="start=启动网关, status=查看状态, install=安装配置")
    gw_p.add_argument("--host", default="0.0.0.0")
    gw_p.add_argument("--port", type=int, default=8000)
    gw_p.add_argument("--feishu", action="store_true", help="Enable Feishu")
    gw_p.add_argument("--telegram", action="store_true", help="Enable Telegram")
    gw_p.add_argument("--discord", action="store_true", help="Enable Discord")
    gw_p.add_argument("--wechat", action="store_true", help="Enable WeChat")

    # ── mcp ───────────────────────────────────────────────────────
    mcp_p = sub.add_parser("mcp", help="Start MCP server")
    mcp_p.add_argument("action", nargs="?", default="serve", choices=["serve", "tools"], help="Action")

    # ── config ────────────────────────────────────────────────────
    cfg_p = sub.add_parser("config", help="Manage configuration")
    cfg_p.add_argument("action", nargs="?", default="show",
                       choices=["show", "edit", "path", "check", "init", "validate"],
                       help="show/edit/path/check/init/validate")
    cfg_p.add_argument("key", nargs="?", help="Config key (e.g., router.model_path)")
    cfg_p.add_argument("value", nargs="?", help="Config value")
    cfg_p.add_argument("--output", "-o", help="Output file for config init")

    # ── skills ────────────────────────────────────────────────────
    sk_p = sub.add_parser("skills", help="Manage skills")
    sk_p.add_argument("action", nargs="?", default="list",
                       choices=["list", "search", "create", "delete", "evolve", "rollback", "import", "discover", "scan"])
    sk_p.add_argument("name", nargs="?", help="Skill name or source (for import/scan)")
    sk_p.add_argument("--query", "-q", help="Search query")
    sk_p.add_argument("--description", "-d", help="Skill description")
    sk_p.add_argument("--content", "-c", help="Skill content")
    sk_p.add_argument("--tags", "-t", help="Comma-separated tags")
    sk_p.add_argument("--filter", "-f", help="Filter by tags (comma-separated)")
    sk_p.add_argument("--source", "-s", help="Source name (hermes, openclaw, all)")
    sk_p.add_argument("--search", help="Keyword search across name/desc/tags")
    sk_p.add_argument("--show", help="Preview full content of a skill by name")
    sk_p.add_argument("--json", action="store_true", help="Output as JSON")
    sk_p.add_argument("--overwrite", action="store_true", help="Overwrite existing skills")
    sk_p.add_argument("--dry-run", action="store_true", help="Preview without importing")

    # ── tools ─────────────────────────────────────────────────────
    tl_p = sub.add_parser("tools", help="Manage tools")
    tl_p.add_argument("action", nargs="?", default="list", choices=["list", "search", "call", "circuit"])
    tl_p.add_argument("name", nargs="?", help="Tool name")
    tl_p.add_argument("--query", "-q", help="Search query")
    tl_p.add_argument("--args", "-a", help="JSON arguments for tool call")

    # ── workflow ──────────────────────────────────────────────────
    wf_p = sub.add_parser("workflow", help="Manage and execute workflows")
    wf_sub = wf_p.add_subparsers(dest="wf_action", help="Workflow actions")

    # dragon workflow run <file>
    wf_run = wf_sub.add_parser("run", help="Execute a workflow YAML file")
    wf_run.add_argument("file", help="Path to workflow YAML file")
    wf_run.add_argument("--context", "-c", help="JSON context to pass to the workflow (e.g., '{\"query\": \"...\"}')")

    # dragon workflow list
    wf_sub.add_parser("list", help="List available workflows in workflows/ directory")

    # dragon workflow validate <file>
    wf_val = wf_sub.add_parser("validate", help="Validate a workflow YAML file syntax and structure")
    wf_val.add_argument("file", help="Path to workflow YAML file")

    # ── sessions ──────────────────────────────────────────────────
    sess_p = sub.add_parser("sessions", help="Manage sessions")
    sess_p.add_argument("action", nargs="?", default="list",
                        choices=["list", "search", "get", "delete", "export", "stats"])
    sess_p.add_argument("session_id", nargs="?", help="Session ID")
    sess_p.add_argument("--query", "-q", help="Search query")
    sess_p.add_argument("--output", "-o", help="Output file (for export)")
    sess_p.add_argument("--since", help="Start date for stats (YYYY-MM-DD)")
    sess_p.add_argument("--until", help="End date for stats (YYYY-MM-DD)")

    # ── cron ──────────────────────────────────────────────────────
    cron_p = sub.add_parser("cron", help="Manage scheduled jobs")
    cron_p.add_argument("action", nargs="?", default="list", choices=["list", "add", "pause", "resume", "remove", "run"])
    cron_p.add_argument("job_id", nargs="?", help="Job ID")
    cron_p.add_argument("--name", help="Job name")
    cron_p.add_argument("--schedule", help="Schedule: 30m, 2h, '0 9 * * *'")
    cron_p.add_argument("--task", help="Task description")

    # ── profile ───────────────────────────────────────────────────
    prof_p = sub.add_parser("profile", help="Manage profiles")
    prof_p.add_argument("action", nargs="?", default="list",
                        choices=["list", "create", "delete", "use", "rename", "export", "import", "edit", "clone"])
    prof_p.add_argument("name", nargs="?", help="Profile name")
    prof_p.add_argument("target", nargs="?", help="Target name (for clone/rename)")
    prof_p.add_argument("--clone", help="Clone from profile")
    prof_p.add_argument("--output", "-o", help="Output path (for export)")

    # ── test ──────────────────────────────────────────────────────
    test_p = sub.add_parser("test", help="Run tests")
    test_p.add_argument("path", nargs="?", default="tests/", help="Test path")
    test_p.add_argument("-v", "--verbose", action="store_true")

    # ── doctor ────────────────────────────────────────────────────
    doc_p = sub.add_parser("doctor", help="运行诊断检查 (run diagnostic checks)")
    doc_p.add_argument("--json", action="store_true", help="Output as JSON")

    # ── tui ────────────────────────────────────────────────────────
    tui_p = sub.add_parser("tui", help="Start TUI backend server (for Ink/React terminal UI)")

    # ── model ───────────────────────────────────────────────────────
    model_p = sub.add_parser("model", help="Interactive model and provider picker")
    model_p.add_argument("name", nargs="?", help="Model name to switch to")

    # ── setup ──────────────────────────────────────────────────────
    setup_p = sub.add_parser("setup", help="Interactive setup wizard (交互式配置向导)")
    setup_p.add_argument("section", nargs="?", default="", choices=["", "model", "providers", "gateway", "doctor"],
                         help="Section: model, providers, gateway, doctor")
    setup_p.add_argument("--feishu", action="store_true", help="Feishu-only setup")
    setup_p.add_argument("--providers", action="store_true", help="Provider keys only")
    setup_p.add_argument("--quick", action="store_true", help="Non-interactive (from env vars)")

    args = parser.parse_args()

    if args.version or not args.command:
        print(f"Dragon Agent v{VERSION}")
        if not args.command:
            parser.print_help()
        return

    # Route to command handler
    handlers = {
        "chat": cmd_chat,
        "serve": cmd_serve,
        "gateway": cmd_gateway,
        "mcp": cmd_mcp,
        "config": cmd_config,
        "skills": cmd_skills,
        "tools": cmd_tools,
        "sessions": cmd_sessions,
        "cron": cmd_cron,
        "profile": cmd_profile,
        "test": cmd_test,
        "doctor": cmd_doctor,
        "tui": cmd_tui,
        "setup": cmd_setup,
        "model": cmd_model,
        "workflow": cmd_workflow,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args)
    else:
        print(f"Unknown command: {args.command}")


# ── Command Handlers ────────────────────────────────────────────────


def cmd_chat(args):
    """Interactive or single-query chat."""
    if args.query:
        print(f"[Dragon] Processing: {args.query}")
        print(f"[Using {args.provider}/{args.model}]")
        print()

        try:
            from dragon.provider import auto_setup_providers
            registry = auto_setup_providers()

            async def _run():
                result = await registry.call(
                    args.provider, args.model,
                    messages=[{"role": "user", "content": args.query}],
                )
                return result.content

            reply = asyncio.run(_run())
            print(reply)
        except Exception as e:
            print(f"Error: {e}")
            print("Tip: Set OPENAI_API_KEY or DEEPSEEK_API_KEY in environment.")
    else:
        print(f"Dragon Agent v{VERSION} — Interactive Mode")
        print("Type your message, or /quit to exit.")
        print()

        from dragon.provider import auto_setup_providers
        registry = auto_setup_providers()

        async def _chat():
            history = []
            while True:
                try:
                    user_input = input("> ")
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if user_input.lower() in ("/quit", "/exit", "/q"):
                    print("Goodbye!")
                    break

                history.append({"role": "user", "content": user_input})

                try:
                    result = await registry.call(
                        args.provider, args.model,
                        messages=history,
                    )
                    print(result.content)
                    history.append({"role": "assistant", "content": result.content})
                except Exception as e:
                    print(f"Error: {e}")

        asyncio.run(_chat())


def cmd_serve(args):
    """Start API server."""
    print(f"Starting Dragon API server on {args.host}:{args.port}...")
    try:
        import uvicorn
        uvicorn.run(
            "dragon.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_level="info",
        )
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install uvicorn")



def _load_dispatch_config():
    """Load dispatch.global_api settings from config.yaml."""
    import os, yaml
    paths = ['config.yaml', os.path.expanduser('~/.dragon/config.yaml')]
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                cfg = yaml.safe_load(f) or {}
            dispatch = cfg.get('dispatch', {})
            api = dispatch.get('global_api', {})
            if api:
                return {
                    "api_key": os.getenv(api.get('api_key_env', ''), ''),
                    "base_url": api.get('base_url'),
                    "model": api.get('model', 'gpt-4o'),
                    "timeout_secs": api.get('timeout_secs', 60),
                }
    # Also check agent section for max_turns
    for p in paths:
        if os.path.exists(p):
            with open(p) as f:
                cfg = yaml.safe_load(f) or {}
            agent = cfg.get('agent', {})
            if 'max_turns' in agent:
                result = {'api_key': 'not-needed', 'base_url': None, 'model': 'gpt-4o', 'timeout_secs': 60}
                result['max_turns'] = agent['max_turns']
                return result
            break
    return {'api_key': 'not-needed', 'base_url': None, 'model': 'gpt-4o', 'timeout_secs': 60}
def cmd_gateway(args):
    """Start or show status of multi-platform gateway."""
    if args.action == "status":
        _cmd_gateway_status()
        return

    if args.action == "install":
        _cmd_gateway_install()
        return

    # ── start ──
    print(f"Starting Dragon Gateway on {args.host}:{args.port}...")

    from dragon.provider import auto_setup_providers
    from dragon.session import SessionStore
    from dragon.tool import ToolRegistry
    from dragon.tool.builtins import register_builtins
    from dragon.gateway.server import GatewayServer

    registry = auto_setup_providers()

    # If 'openai' not auto-registered (no OPENAI_API_KEY), register it
    # using the dispatch.global_api config (supports llama.cpp backends)
    if 'openai' not in registry.available_providers():
        from dragon.provider import OpenAIProvider, ProviderConfig
        _cfg = _load_dispatch_config()
        registry.register('openai', OpenAIProvider(ProviderConfig(
            provider='openai',
            api_key=_cfg.get('api_key', 'not-needed'),
            base_url=_cfg.get('base_url', None),
            default_model=_cfg.get('model', 'gpt-4o'),
            timeout_secs=_cfg.get('timeout_secs', 60),
        )))
        print(f"  ✓ Registered 'openai' provider "
              f"(model={_cfg.get('model')}, base_url={_cfg.get('base_url')})")

    session_store = SessionStore()
    tool_registry = ToolRegistry()
    register_builtins(tool_registry)

    # Read agent.max_turns from config (default 90, range 10-150)
    _gw_cfg = _load_dispatch_config()
    _max_turns = _gw_cfg.get('max_turns', 90)
    _max_turns = max(10, min(150, _max_turns))

    server = GatewayServer(
        provider_registry=registry,
        session_store=session_store,
        tool_registry=tool_registry,
        max_tool_iterations=_max_turns,
    )
    print(f"  ✓ Agent max turns: {_max_turns}")

    if args.feishu:
        from dragon.gateway.feishu import FeishuAdapter
        server.register_adapter(FeishuAdapter())
        print("  ✓ Feishu enabled")

    if args.telegram:
        from dragon.gateway.telegram import TelegramAdapter
        server.register_adapter(TelegramAdapter())
        print("  ✓ Telegram enabled")

    if args.discord:
        from dragon.gateway.discord import DiscordAdapter
        server.register_adapter(DiscordAdapter())
        print("  ✓ Discord enabled")

    if args.wechat:
        from dragon.gateway.wechat import WeChatAdapter
        server.register_adapter(WeChatAdapter())
        print("  ✓ WeChat enabled")

    if not any([args.feishu, args.telegram, args.discord, args.wechat]):
        from dragon.gateway.feishu import FeishuAdapter
        server.register_adapter(FeishuAdapter())
        print("  ✓ Feishu enabled (default)")

    import uvicorn
    uvicorn.run(server.app, host=args.host, port=args.port, log_level="info")


def _cmd_gateway_status():
    """Show gateway status — active adapters, config, and session stats."""
    print("Dragon Gateway Status")
    print("=" * 40)

    # Check adapters — detect which platforms are configured via env vars
    adapters = []
    if os.getenv("FEISHU_APP_ID"):
        adapters.append(("Feishu", "✓", os.getenv("FEISHU_APP_ID", "")[:8] + "..."))
    elif os.getenv("FEISHU_APP_ID") is not None:
        adapters.append(("Feishu", "✓", "configured"))
    else:
        adapters.append(("Feishu", "○", "not configured"))

    if os.getenv("TELEGRAM_BOT_TOKEN"):
        adapters.append(("Telegram", "✓", "configured"))
    else:
        adapters.append(("Telegram", "○", "not configured"))

    if os.getenv("DISCORD_BOT_TOKEN"):
        adapters.append(("Discord", "✓", "configured"))
    else:
        adapters.append(("Discord", "○", "not configured"))

    if os.getenv("WECHAT_APP_ID"):
        adapters.append(("WeChat", "✓", "configured"))
    else:
        adapters.append(("WeChat", "○", "not configured"))

    print("\n活跃适配器 (Active Adapters):")
    for name, status, detail in adapters:
        print(f"  {status} {name:10s} — {detail}")

    # Session stats
    try:
        from dragon.session import SessionStore
        store = SessionStore()
        st = store.stats()
        print(f"\n会话统计 (Session Stats):")
        print(f"  总会话数: {st['sessions']}")
        print(f"  总消息数: {st['messages']}")
        print(f"  最近活动: {st['latest_activity'][:16] if st['latest_activity'] != 'never' else 'N/A'}")
    except Exception as e:
        print(f"\n会话统计: unavailable ({e})")

    # Provider check
    print(f"\nProvider 状态:")
    keys_configured = []
    for env_var in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
                     "DRAGON_GENERAL_API_KEY", "OPENROUTER_API_KEY"]:
        if os.getenv(env_var):
            keys_configured.append(env_var)
    if keys_configured:
        for k in keys_configured:
            print(f"  ✓ {k}")
    else:
        print("  ⚠ 未设置任何 API Key")

    print(f"\n端点 (Endpoint): http://0.0.0.0:8000")
    print(f"健康检查: GET /health")


def _cmd_gateway_install():
    """Interactive gateway setup — configure platform adapters."""
    print("\n🐉 Dragon Gateway — 安装配置向导")
    print("=" * 50)

    platforms = {
        "feishu": {
            "name": "Feishu (飞书)",
            "env_vars": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
            "docs": "https://open.feishu.cn/app",
        },
        "telegram": {
            "name": "Telegram",
            "env_vars": ["TELEGRAM_BOT_TOKEN"],
            "docs": "https://t.me/BotFather",
        },
        "discord": {
            "name": "Discord",
            "env_vars": ["DISCORD_BOT_TOKEN"],
            "docs": "https://discord.com/developers/applications",
        },
        "wechat": {
            "name": "WeChat (微信)",
            "env_vars": ["WECHAT_APP_ID", "WECHAT_APP_SECRET"],
            "docs": "https://mp.weixin.qq.com/",
        },
    }

    env_file = Path.home() / ".dragon" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)

    configured = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                configured[key] = val.strip().strip('"')

    for key, plat in platforms.items():
        env_vars = plat["env_vars"]
        already = all(configured.get(v) or os.getenv(v) for v in env_vars)
        status = "✓ 已配置" if already else "○ 未配置"
        print(f"\n{status} {plat['name']}")
        print(f"  文档: {plat['docs']}")
        print(f"  需要: {', '.join(env_vars)}")

        if already:
            continue

        try:
            choice = input(f"  是否现在配置? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"

        if choice == "y":
            for var in env_vars:
                try:
                    val = input(f"  {var}: ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if val:
                    configured[var] = val
                    print(f"    → {var} 已设置")

    # Save to .env
    lines = []
    for key, val in configured.items():
        lines.append(f"{key}={val}")
    env_file.write_text("\n".join(lines) + "\n")
    print(f"\n✓ 配置已保存到 {env_file}")

    # Quick check
    print(f"\n配置概览:")
    for key, plat in platforms.items():
        env_vars = plat["env_vars"]
        ready = all(configured.get(v) or os.getenv(v) for v in env_vars)
        print(f"  {'✓' if ready else '○'} {plat['name']}")
    print(f"\n启动网关: dragon gateway start --feishu")


def cmd_mcp(args):
    """Start MCP server."""
    if args.action == "tools":
        print("Dragon MCP Tools:")
        print("  dragon.skills.search")
        print("  dragon.skills.evolve")
        print("  dragon.consult.assess")
        print("  dragon.consult.debate")
        print("  dragon.memory.search")
        print("  dragon.memory.graph")
        print("  dragon.search")
        print("  dragon.file_read")
        print("  dragon.file_write")
        print("  dragon.execute")
        print("  dragon.http_get")
    else:
        print("Starting Dragon MCP Server...")
        from dragon.mcp.server import main as mcp_main
        mcp_main()


def cmd_config(args):
    """Manage configuration."""
    if args.action == "init":
        _cmd_config_init(args)
        return
    elif args.action == "validate":
        _cmd_config_validate()
        return

    from dragon.config import DragonConfig

    cfg = DragonConfig.load()

    if args.action == "show":
        print("Dragon Configuration:")
        print(f"  Router model: {cfg.router.model_path}")
        print(f"  Router threads: {cfg.router.n_threads}")
        print(f"  Server: {cfg.server.host}:{cfg.server.port}")
        print(f"  Memory: {cfg.memory.persist_dir}")
        print(f"  Memory embedding: {cfg.memory.embedding_model}")

    elif args.action == "path":
        print("config.yaml: ~/.dragon/config.yaml")
        print(".env:        ~/.dragon/.env")

    elif args.action == "check":
        issues = []
        if not os.getenv("OPENAI_API_KEY") and not os.getenv("DEEPSEEK_API_KEY"):
            issues.append("No API key found. Set OPENAI_API_KEY or DEEPSEEK_API_KEY")
        model_path = Path(cfg.router.model_path)
        if not model_path.exists():
            issues.append(f"Router model not found: {cfg.router.model_path}")

        if issues:
            print("Issues found:")
            for i in issues:
                print(f"  ⚠ {i}")
        else:
            print("✓ Configuration looks good")


def _cmd_config_init(args):
    """Interactive config wizard — generate config.yaml from user input."""
    import yaml

    print("Dragon Config Init — 交互式配置向导")
    print("=" * 40)
    print("按 Enter 使用默认值。\n")

    # Model path
    default_model = "models/qwen2-1.5b-q4_k_m.gguf"
    model_path = input(f"模型路径 (model path) [{default_model}]: ").strip()
    if not model_path:
        model_path = default_model

    # Server port
    default_port = "8000"
    port_str = input(f"服务器端口 (server port) [{default_port}]: ").strip()
    if not port_str:
        port_str = default_port
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    # Server host
    default_host = "0.0.0.0"
    host = input(f"服务器地址 (server host) [{default_host}]: ").strip()
    if not host:
        host = default_host

    # API keys
    print("\nAPI Keys (可选, 留空跳过):")
    openai_key = input("  OPENAI_API_KEY: ").strip()
    deepseek_key = input("  DEEPSEEK_API_KEY: ").strip()

    # Build config
    config = {
        "router": {
            "model_path": model_path,
            "n_threads": 4,
            "n_ctx": 512,
            "temperature": 0.1,
            "max_tokens": 128,
            "fallback_on_failure": True,
        },
        "server": {
            "host": host,
            "port": port,
            "log_level": "info",
        },
        "memory": {
            "persist_dir": "dragon_data/vectordb",
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "search_top_k": 5,
            "search_threshold": 0.5,
        },
        "guard": {
            "max_consecutive_repeats": 3,
            "max_loop_rounds": 2,
            "max_ineffective_retries": 3,
            "task_timeout_secs": 300,
        },
    }

    output_path = args.output or "config.yaml"
    with open(output_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"\n✓ 配置文件已生成: {output_path}")

    # Write .env if API keys provided
    if openai_key or deepseek_key:
        env_path = Path(output_path).parent / ".env"
        env_lines = []
        if Path(env_path).exists():
            env_lines = Path(env_path).read_text().strip().split("\n")
        if openai_key and not any(l.startswith("OPENAI_API_KEY=") for l in env_lines):
            env_lines.append(f"OPENAI_API_KEY={openai_key}")
        if deepseek_key and not any(l.startswith("DEEPSEEK_API_KEY=") for l in env_lines):
            env_lines.append(f"DEEPSEEK_API_KEY={deepseek_key}")
        Path(env_path).write_text("\n".join(env_lines) + "\n")
        print(f"✓ API Keys 已写入: {env_path}")


def _cmd_config_validate():
    """Validate config.yaml against the DragonConfig schema."""
    from dragon.config import DragonConfig

    config_path = "config.yaml"
    if not Path(config_path).exists():
        print(f"⚠ 未找到配置文件: {config_path}")
        config_path = os.path.expanduser("~/.dragon/config.yaml")
        if not Path(config_path).exists():
            print("⚠ 未找到配置文件: ~/.dragon/config.yaml")
            print("请运行: dragon config init")
            return

    print(f"验证配置文件: {config_path}")
    try:
        cfg = DragonConfig.load(config_path)
        print("✓ 配置文件格式正确")

        # Extra checks beyond schema validation
        warnings = []
        model_path = Path(cfg.router.model_path)
        if not model_path.exists():
            warnings.append(f"模型文件不存在: {cfg.router.model_path}")
        if not os.getenv("OPENAI_API_KEY") and not os.getenv("DEEPSEEK_API_KEY"):
            warnings.append("未设置任何 API Key (OPENAI_API_KEY / DEEPSEEK_API_KEY)")

        if warnings:
            print(f"\n⚠ 发现 {len(warnings)} 个警告:")
            for w in warnings:
                print(f"  • {w}")
        else:
            print("✓ 所有检查通过")

        # Show key settings
        print(f"\n当前配置摘要:")
        print(f"  Router model: {cfg.router.model_path}")
        print(f"  Server: {cfg.server.host}:{cfg.server.port}")
        print(f"  Memory: {cfg.memory.persist_dir}")
        print(f"  Embedding: {cfg.memory.embedding_model}")

    except Exception as e:
        print(f"✗ 配置验证失败: {e}")
        print(f"\n请检查 {config_path} 的格式是否正确。")


def cmd_skills(args):
    """Manage skills."""
    from dragon.skill import SkillEngine

    engine = SkillEngine()

    if args.action == "list":
        skills = engine.list_skills()
        if not skills:
            print("No skills installed.")
            return
        print(f"Skills ({len(skills)}):")
        for s in skills:
            status_icon = "✓" if s.get("success_rate", 0) > 0.7 else "⚠"
            print(f"  {status_icon} {s['name']} v{s['version']} ({s.get('success_rate', 0):.0%})")
            print(f"    {s['description'][:80]}")

    elif args.action == "search":
        query = args.query or args.name or ""
        matches = asyncio.run(engine.discover(query, top_k=5))
        if not matches:
            print(f"No skills found for: {query}")
            return
        for m in matches:
            print(f"  {m.skill_name} (similarity: {m.similarity:.2f}, success: {m.skill.success_rate:.0%})")

    elif args.action == "create":
        if not args.name or not args.content:
            print("Usage: dragon skills create <name> --content <text> --description <desc>")
            return
        engine.register(
            name=args.name,
            description=args.description or "",
            content=args.content,
            tags=args.tags.split(",") if args.tags else [],
        )
        print(f"Created skill: {args.name}")

    elif args.action == "delete":
        if not args.name:
            print("Usage: dragon skills delete <name>")
            return
        if engine.delete(args.name):
            print(f"Deleted skill: {args.name}")
        else:
            print(f"Skill not found: {args.name}")

    elif args.action == "import":
        from dragon.skill.importer import SkillImporter

        source = args.source or args.name or "hermes"
        filter_tags = args.filter.split(",") if getattr(args, "filter", None) else None

        importer = SkillImporter(engine)
        report = importer.import_from(
            source=source,
            filter_tags=filter_tags,
            dry_run=getattr(args, "dry_run", False),
            overwrite=getattr(args, "overwrite", False),
        )

        print(f"\nImport Report [{report.source}]:")
        print(f"  Imported:  {report.imported}")
        print(f"  Dry-run:   {report.dry_run}")
        print(f"  Skipped:   {report.skipped}")
        print(f"  Errors:    {report.errors}")
        if report.details:
            for d in report.details[:20]:
                icon = "✓" if d.get("status") == "imported" else "○" if d.get("status") == "dry_run" else "✗"
                name = d.get("name", d.get("file", "?"))
                print(f"  {icon} {name}")

    elif args.action == "scan":
        from dragon.skill.importer import SkillImporter

        source = args.source or args.name or "hermes"
        filter_tags = args.filter.split(",") if getattr(args, "filter", None) else None
        search_kw = getattr(args, "search", "") or ""
        show_name = getattr(args, "show", None)
        as_json = getattr(args, "json", False)

        importer = SkillImporter(engine)
        report = importer.scan(
            source=source,
            filter_tags=filter_tags,
            search=search_kw,
            show_content=show_name,
        )

        if as_json:
            import json as _json
            print(_json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return

        # Content preview mode
        if report.content_preview:
            cp = report.content_preview
            print(f"\n{'='*60}")
            print(f" Skill: {cp['name']}")
            print(f" File:  {cp['file']}")
            print(f"{'='*60}\n")
            print(cp["body"])
            return

        print(f"\nScan [{report.source}]: {report.total} skills matched")

        imported_count = sum(1 for s in report.skills if s.get("imported"))
        print(f"  Imported: {imported_count}  |  Available: {report.total - imported_count}")
        if report.errors:
            print(f"  Errors: {report.errors}")
        print()

        for skill in report.skills:
            icon = "✓" if skill.get("imported") else "○"
            name = skill.get("name", "?")
            ver = skill.get("version", "")
            tags = ", ".join(skill.get("tags", [])[:5])
            desc = (skill.get("description", "") or "")[:80]
            extra = ""
            if skill.get("imported"):
                extra = f" [local v{skill.get('local_version','')} sr={skill.get('local_success_rate',0):.0%}]"

            print(f"  {icon} {name} v{ver}{extra}")
            print(f"     {desc}")
            print(f"     tags: {tags}")
            print()

    elif args.action == "discover":
        from dragon.skill.importer import SkillImporter

        importer = SkillImporter(engine)
        sources = importer.discover_sources()

        if not sources:
            print("No external skill sources found.")
            print("Supported sources: hermes (~/.hermes/skills/), openclaw (~/.openclaw/skills/)")
            return

        print(f"Discovered {len(sources)} skill source(s):\n")
        for src in sources:
            print(f"  {src.name}: {src.description}")
            print(f"    Path: {src.path}")
            print(f"    Skills: {src.skill_count}")
            print()


def cmd_tools(args):
    """Manage tools."""
    from dragon.tool import ToolRegistry
    from dragon.tool.builtins import register_builtins

    registry = ToolRegistry()
    register_builtins(registry)

    if args.action == "list":
        tools = registry.list_tools()
        print(f"Tools ({len(tools)}):")
        for t in tools:
            print(f"  {t['name']} [{t['category']}] — {t['description'][:60]}")

    elif args.action == "search":
        query = args.query or args.name or ""
        results = registry.search(query)
        for r in results:
            print(f"  {r['name']} (score: {r['score']}) — {r['description'][:60]}")

    elif args.action == "call":
        if not args.name:
            print("Usage: dragon tools call <name> --args '{\"key\":\"value\"}'")
            return
        try:
            tool_args = json.loads(args.args or "{}")
        except json.JSONDecodeError:
            tool_args = {}
        result = asyncio.run(registry.call(args.name, tool_args))
        print(f"Tool: {result.tool_name}")
        print(f"Success: {result.success}")
        print(f"Output: {str(result.output)[:500]}")


def cmd_sessions(args):
    """Manage sessions."""
    from dragon.session import SessionStore

    store = SessionStore()

    if args.action == "list":
        sessions = store.list_recent(limit=20)
        print(f"Recent sessions ({len(sessions)}):")
        for s in sessions:
            print(f"  {s.id} | {s.platform:8s} | {s.title[:40]:40s} | {s.message_count} msgs")

    elif args.action == "search":
        query = args.query or ""
        results = store.search(query)
        for r in results:
            print(f"  {r['session_id']} | {r.get('platform','')} | {r['title'][:40]} | {r.get('message_count',0)} msgs")

    elif args.action == "get":
        if not args.session_id:
            print("Usage: dragon sessions get <session_id>")
            return
        session = store.get(args.session_id)
        if session:
            print(f"Session: {session.id}")
            print(f"Title: {session.title}")
            print(f"Platform: {session.platform}")
            print(f"Messages: {session.message_count}")
            msgs = store.get_messages(session.id, limit=20)
            for m in msgs:
                print(f"  [{m.role}] {m.content[:100]}")
        else:
            print(f"Session not found: {args.session_id}")

    elif args.action == "delete":
        if not args.session_id:
            print("Usage: dragon sessions delete <session_id>")
            return
        if store.delete(args.session_id):
            print(f"Deleted session: {args.session_id}")
        else:
            print(f"Session not found: {args.session_id}")

    elif args.action == "export":
        _cmd_sessions_export(args, store)

    elif args.action == "stats":
        _cmd_sessions_stats(args, store)


def _cmd_sessions_export(args, store):
    """Export session history to JSON."""
    if not args.session_id:
        print("Usage: dragon sessions export <session_id> [--output <file>]")
        return

    session = store.get(args.session_id)
    if not session:
        print(f"Session not found: {args.session_id}")
        return

    messages = store.get_messages(session.id, limit=10000)

    export_data = {
        "session": session.to_dict(),
        "messages": [m.to_dict() for m in messages],
        "exported_at": __import__("datetime").datetime.now().isoformat(),
    }

    output_path = args.output or f"session_{session.id}.json"
    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)

    print(f"✓ 导出会话: {session.id} ({len(messages)} 条消息)")
    print(f"  输出文件: {output_path}")


def _cmd_sessions_stats(args, store):
    """Show session statistics: total, by platform, by date range."""
    print("会话统计 (Session Statistics)")
    print("=" * 40)

    # Use store.stats for basic counts
    st = store.stats()
    print(f"  总会话数: {st['sessions']}")
    print(f"  总消息数: {st['messages']}")
    print(f"  数据库: {st['db_path']}")

    # Per-platform breakdown
    print(f"\n按平台统计 (By Platform):")
    platforms = ["feishu", "telegram", "discord", "wechat", "api", "cli"]
    for plat in platforms:
        sessions = store.list_recent(limit=1000, platform=plat)
        if sessions:
            total_msgs = sum(s.message_count for s in sessions)
            print(f"  {plat:10s}: {len(sessions)} sessions, {total_msgs} messages")

    # Date range filter
    if args.since or args.until:
        print(f"\n日期筛选 (Date Filter):")
        print(f"  开始: {args.since or '最早'}")
        print(f"  结束: {args.until or '最新'}")
        filtered = []
        for s in store.list_recent(limit=1000):
            if args.since and s.created_at < args.since:
                continue
            if args.until and s.created_at > args.until:
                continue
            filtered.append(s)
        print(f"  筛选结果: {len(filtered)} sessions")

    # Latest activity
    print(f"\n  最近活动: {st['latest_activity'][:16] if st['latest_activity'] != 'never' else 'N/A'}")


def cmd_cron(args):
    """Manage cron jobs."""
    from dragon.cron import CronScheduler

    scheduler = CronScheduler()

    if args.action == "list":
        jobs = scheduler.list_jobs()
        print(f"Cron jobs ({len(jobs)}):")
        for j in jobs:
            status_icon = "●" if j.status == "active" else "○"
            print(f"  {status_icon} {j.id} | {j.name:20s} | {j.schedule:15s} | next: {j.next_run_at[:16] if j.next_run_at else 'N/A'}")

    elif args.action == "add":
        if not args.name or not args.schedule:
            print("Usage: dragon cron add --name <name> --schedule <30m|0 9 * * *> --task <desc>")
            return
        job = scheduler.add(name=args.name, schedule=args.schedule, task=args.task or "")
        print(f"Added cron job: {job.id} [{job.schedule}]")

    elif args.action == "pause":
        if args.job_id and scheduler.pause(args.job_id):
            print(f"Paused: {args.job_id}")
        else:
            print("Job not found")

    elif args.action == "resume":
        if args.job_id and scheduler.resume(args.job_id):
            print(f"Resumed: {args.job_id}")

    elif args.action == "remove":
        if args.job_id and scheduler.remove(args.job_id):
            print(f"Removed: {args.job_id}")

    elif args.action == "run":
        if args.job_id and scheduler.run_now(args.job_id):
            print(f"Triggered: {args.job_id}")


def cmd_profile(args):
    """Manage profiles."""
    from dragon.profile import ProfileManager

    pm = ProfileManager()

    if args.action == "list":
        profiles = pm.list_profiles()
        print(f"Profiles ({len(profiles)}):")
        for p in profiles:
            default_mark = " ★" if p.is_default else ""
            print(f"  {p.name}{default_mark} ({p.base_dir})")

    elif args.action == "create":
        if not args.name:
            print("Usage: dragon profile create <name> [--clone <source>]")
            return
        pm.create(args.name, clone_from=args.clone)
        print(f"Created profile: {args.name}")

    elif args.action == "edit":
        _cmd_profile_edit(args, pm)

    elif args.action == "clone":
        _cmd_profile_clone(args, pm)

    elif args.action == "export":
        _cmd_profile_export(args, pm)

    elif args.action == "import":
        _cmd_profile_import(args, pm)

    elif args.action == "rename":
        if not args.name or not args.target:
            print("Usage: dragon profile rename <old_name> <new_name>")
            return
        if pm.rename(args.name, args.target):
            print(f"Renamed: {args.name} → {args.target}")
        else:
            print(f"Rename failed. Check that '{args.name}' exists and '{args.target}' does not.")

    elif args.action == "use":
        if not args.name:
            print("Usage: dragon profile use <name>")
            return
        if pm.set_default(args.name):
            print(f"Default profile: {args.name}")

    elif args.action == "delete":
        if not args.name:
            print("Usage: dragon profile delete <name>")
            return
        if pm.delete(args.name):
            print(f"Deleted profile: {args.name}")


def _cmd_profile_edit(args, pm):
    """Open profile config.yaml in $EDITOR or show current settings."""
    profile_name = args.name or pm._default_profile
    if not profile_name:
        print("No profile specified and no default profile set.")
        return

    profile = pm.get(profile_name)
    if not profile:
        print(f"Profile not found: {profile_name}")
        return

    config_file = str(profile.config_file)
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vi"))

    if not profile.config_file.exists():
        print(f"Config file does not exist: {config_file}")
        print(f"Creating default config.yaml...")
        profile.config_file.write_text(
            "# Dragon Agent Configuration\n"
            f"# Profile: {profile_name}\n"
            "router:\n"
            "  model_path: models/qwen2-1.5b-q4_k_m.gguf\n"
            "  n_threads: 4\n"
            "server:\n"
            "  host: 0.0.0.0\n"
            "  port: 8000\n"
        )

    print(f"Opening {config_file} with {editor}...")
    print(f"(或手动编辑: {config_file})")
    os.system(f"{editor} {config_file}")


def _cmd_profile_clone(args, pm):
    """Clone a profile: dragon profile clone <source> <target>."""
    if not args.name:
        print("Usage: dragon profile clone <source_profile> <new_name>")
        return
    source_name = args.name
    target_name = args.target or args.clone
    if not target_name:
        print("Usage: dragon profile clone <source_profile> <new_name>")
        return

    if source_name not in pm._profiles:
        print(f"Source profile not found: {source_name}")
        print(f"Available: {', '.join(pm._profiles.keys())}")
        return

    try:
        pm.create(target_name, clone_from=source_name)
        print(f"✓ 克隆完成: {source_name} → {target_name}")
    except ValueError as e:
        print(f"Error: {e}")


def _cmd_profile_export(args, pm):
    """Export profile to tar.gz."""
    if not args.name:
        print("Usage: dragon profile export <name> [--output <path>]")
        return

    output_path = args.output or f"{args.name}.tar.gz"
    if pm.export_profile(args.name, output_path):
        size_mb = Path(output_path).stat().st_size / (1024 * 1024)
        print(f"✓ 导出完成: {output_path} ({size_mb:.1f} MB)")
    else:
        print(f"Profile not found: {args.name}")


def _cmd_profile_import(args, pm):
    """Import profile from tar.gz."""
    if not args.name:
        print("Usage: dragon profile import <archive.tar.gz> [new_name]")
        return

    archive_path = args.name
    new_name = args.target or None

    if not Path(archive_path).exists():
        print(f"Archive not found: {archive_path}")
        return

    result = pm.import_profile(archive_path, new_name)
    if result:
        print(f"✓ 导入完成: {result.name}")
    else:
        print(f"Import failed. Check that the archive is valid and the profile doesn't already exist.")


def cmd_test(args):
    """Run tests."""
    import subprocess
    cmd = [sys.executable, "-m", "pytest", args.path]
    if args.verbose:
        cmd.append("-v")
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)


def cmd_doctor(args):
    """运行诊断检查 — check Python version, deps, config, model, env vars."""
    import platform
    import importlib

    results = []
    all_ok = True

    def check(name, ok, detail="", severity="error"):
        nonlocal all_ok
        icon = "✓" if ok else ("⚠" if severity == "warning" else "✗")
        if not ok and severity == "error":
            all_ok = False
        results.append({"name": name, "ok": ok, "detail": detail, "icon": icon})

    # 1. Python version
    py_ver = platform.python_version()
    check("Python 版本", py_ver >= "3.10", f"Python {py_ver}")

    # 2. Core dependencies
    deps = {
        "pydantic": "pydantic",
        "yaml": "yaml (pyyaml)",
        "dotenv": "dotenv (python-dotenv)",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "sqlite3": "sqlite3 (built-in)",
        "aiohttp": "aiohttp",
    }
    for mod, display in deps.items():
        try:
            importlib.import_module(mod)
            check(f"依赖: {display}", True, "已安装")
        except ImportError:
            check(f"依赖: {display}", False, "未安装", "warning")

    # 3. Config file
    config_paths = ["config.yaml", os.path.expanduser("~/.dragon/config.yaml")]
    found_config = None
    for cp in config_paths:
        if Path(cp).exists():
            found_config = cp
            break
    if found_config:
        try:
            from dragon.config import DragonConfig
            DragonConfig.load(found_config)
            check("配置文件", True, found_config)
        except Exception as e:
            check("配置文件", False, f"{found_config} — {e}")
    else:
        check("配置文件", False, "未找到 config.yaml", "warning")

    # 4. Model exists
    try:
        from dragon.config import DragonConfig
        cfg = DragonConfig.load()
        model_path = Path(cfg.router.model_path)
        if model_path.exists():
            size_mb = model_path.stat().st_size / (1024 * 1024)
            check("模型文件", True, f"{cfg.router.model_path} ({size_mb:.1f} MB)")
        else:
            check("模型文件", False, f"未找到: {cfg.router.model_path}", "warning")
    except Exception:
        check("模型文件", False, "无法检查 (配置加载失败)", "warning")

    # 5. Environment variables
    api_keys = ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "DRAGON_GENERAL_API_KEY"]
    any_key = False
    for k in api_keys:
        if os.getenv(k):
            any_key = True
            break
    if any_key:
        set_keys = [k for k in api_keys if os.getenv(k)]
        check("API Keys", True, f"已设置: {', '.join(set_keys)}")
    else:
        check("API Keys", False, "未设置任何 API Key", "warning")

    # 6. Dragon data directory
    data_dir = Path("dragon_data")
    home_data = Path.home() / ".dragon"
    if data_dir.exists() or home_data.exists():
        check("数据目录", True, f"{data_dir if data_dir.exists() else home_data}")
    else:
        check("数据目录", False, "dragon_data/ 和 ~/.dragon/ 均不存在", "warning")

    # 7. Platform check
    system = platform.system()
    check("操作系统", True, f"{system} ({platform.release()})")

    # Output
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("Dragon Doctor — 诊断报告")
        print("=" * 50)
        for r in results:
            print(f"  {r['icon']} {r['name']}: {r['detail']}")
        print("=" * 50)
        if all_ok:
            print("✓ 所有关键检查通过！")
        else:
            print("⚠ 发现问题，请检查上述标记为 ✗ 的项目。")


def cmd_tui(args):
    """Start the TUI backend server over stdin/stdout."""
    from dragon.tui.server import main as tui_main
    tui_main()


def cmd_model(args):
    """Interactive model and provider picker — aligned with Hermes 'hermes model'."""
    from dragon.setup import pick_model
    pick_model(args.name if hasattr(args, 'name') else None)


def cmd_setup(args):
    """Interactive setup wizard."""
    from dragon.setup import run_setup
    run_setup(
        section=getattr(args, "section", ""),
        feishu_only=args.feishu,
        providers_only=args.providers,
        quick=args.quick,
    )


def cmd_workflow(args):
    """Manage and execute workflows."""
    wf_action = getattr(args, "wf_action", None)

    if wf_action == "run":
        _cmd_workflow_run(args)
    elif wf_action == "list":
        _cmd_workflow_list()
    elif wf_action == "validate":
        _cmd_workflow_validate(args)
    else:
        print("Usage: dragon workflow {run|list|validate} [...]")
        print("  dragon workflow run <file>       — 执行工作流")
        print("  dragon workflow list              — 列出可用工作流")
        print("  dragon workflow validate <file>   — 验证工作流 YAML 语法和结构")


def _cmd_workflow_run(args):
    """Execute a workflow YAML file."""
    import asyncio
    import json as _json

    file_path = args.file
    if not Path(file_path).exists():
        print(f"✗ 文件不存在: {file_path}")
        return

    # Parse context
    context = {}
    if args.context:
        try:
            context = _json.loads(args.context)
        except _json.JSONDecodeError as e:
            print(f"✗ Context JSON 解析失败: {e}")
            return

    async def _run():
        try:
            from dragon.workflow.engine import WorkflowEngine

            engine = WorkflowEngine()
            result = await engine.run_file(file_path, context=context)

            # Print results
            print(f"\n{'='*60}")
            print(f"  工作流: {result.name}")
            print(f"  状态:   {result.status.value}")
            print(f"  耗时:   {result.total_elapsed_ms:.0f}ms")
            print(f"{'='*60}\n")

            for i, sr in enumerate(result.steps, 1):
                icon = "✓" if sr.success and not sr.skipped else ("○" if sr.skipped else "✗")
                extra = ""
                if sr.skipped:
                    extra = " (已跳过)"
                if sr.error:
                    extra = f" (错误: {sr.error})"
                output_preview = str(sr.output)[:120] if sr.output else "(无输出)"
                print(f"  [{i}] {icon} {sr.step_id} [{sr.step_type.value}] {extra}")
                if sr.success and not sr.skipped:
                    print(f"      输出: {output_preview}")
                print(f"      耗时: {sr.elapsed_ms:.0f}ms")

            if result.status.value == "failed":
                print(f"\n✗ 工作流执行失败: {result.error}")
            else:
                print(f"\n✓ 工作流执行完成")
                if result.final_output:
                    print(f"\n最终输出:\n{str(result.final_output)[:500]}")

        except FileNotFoundError as e:
            print(f"✗ {e}")
        except Exception as e:
            print(f"✗ 执行失败: {e}")

    asyncio.run(_run())


def _cmd_workflow_list():
    """List available workflows in the workflows/ directory."""
    workflows_dir = Path("workflows")

    if not workflows_dir.exists() or not workflows_dir.is_dir():
        print(f"✗ workflows/ 目录不存在")
        return

    yaml_files = sorted(workflows_dir.glob("*.yaml"))
    yml_files = sorted(workflows_dir.glob("*.yml"))
    all_files = yaml_files + yml_files

    if not all_files:
        print("workflows/ 目录中没有工作流文件")
        return

    print(f"可用工作流 ({len(all_files)}):\n")
    for f in all_files:
        try:
            import yaml as _yaml
            raw = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            name = raw.get("name", f.stem)
            desc = raw.get("description", "(无描述)")[:80]
            steps = raw.get("steps", [])
            step_count = len(steps)
            print(f"  • {name}")
            print(f"    文件: {f.name}")
            print(f"    步骤数: {step_count}")
            print(f"    描述: {desc}")
            print()
        except Exception:
            print(f"  • {f.stem}")
            print(f"    文件: {f.name}")
            print(f"    (无法解析 YAML)")
            print()


def _cmd_workflow_validate(args):
    """Validate a workflow YAML file syntax and step structure."""
    import yaml as _yaml

    file_path = args.file
    if not Path(file_path).exists():
        print(f"✗ 文件不存在: {file_path}")
        return

    errors = []
    warnings = []
    raw = {}

    # ── 1. Check file extension ──
    if not str(file_path).endswith((".yaml", ".yml")):
        warnings.append("文件扩展名不是 .yaml / .yml")

    # ── 2. Parse YAML syntax ──
    try:
        raw = _yaml.safe_load(Path(file_path).read_text(encoding="utf-8"))
    except _yaml.YAMLError as e:
        errors.append(f"YAML 语法错误: {e}")
    except Exception as e:
        errors.append(f"文件读取错误: {e}")

    if errors:
        _print_validate_result(file_path, errors, warnings)
        return

    if raw is None:
        errors.append("YAML 文件为空")
        _print_validate_result(file_path, errors, warnings)
        return

    # ── 3. Validate top-level fields ──
    if not isinstance(raw, dict):
        errors.append("YAML 根节点必须是字典 (mapping)")
        _print_validate_result(file_path, errors, warnings)
        return

    # Check required fields
    if "name" not in raw:
        warnings.append("缺少 'name' 字段（将使用文件名）")

    if "steps" not in raw:
        errors.append("缺少 'steps' 字段")
        _print_validate_result(file_path, errors, warnings)
        return

    steps = raw.get("steps", [])
    if not isinstance(steps, list):
        errors.append("'steps' 必须是列表 (sequence)")
        _print_validate_result(file_path, errors, warnings)
        return

    if len(steps) == 0:
        warnings.append("工作流没有任何步骤")

    # ── 4. Validate each step ──
    valid_types = {"llm_call", "tool_call", "conditional", "loop", "sub_workflow"}
    step_ids = set()

    for i, step in enumerate(steps):
        prefix = f"步骤 [{i}]"

        if not isinstance(step, dict):
            errors.append(f"{prefix}: 不是字典格式")
            continue

        # Check step id
        sid = step.get("id")
        if not sid:
            errors.append(f"{prefix}: 缺少 'id' 字段")
        elif sid in step_ids:
            errors.append(f"{prefix}: 重复的步骤 ID '{sid}'")
        else:
            step_ids.add(sid)

        # Check step type
        stype = step.get("type", "llm_call")
        if stype not in valid_types:
            errors.append(f"{prefix} ({sid or '?'}): 无效的步骤类型 '{stype}'，有效类型: {', '.join(sorted(valid_types))}")

        # Check config
        config = step.get("config", {})
        if not isinstance(config, dict):
            errors.append(f"{prefix} ({sid or '?'}): 'config' 必须是字典")
            continue

        # Type-specific validation
        if stype == "llm_call":
            if "prompt" not in config:
                errors.append(f"{prefix} ({sid or '?'}): llm_call 缺少 'prompt' 配置")
        elif stype == "tool_call":
            if "tool" not in config:
                errors.append(f"{prefix} ({sid or '?'}): tool_call 缺少 'tool' 配置")
        elif stype == "conditional":
            if "expression" not in config:
                warnings.append(f"{prefix} ({sid or '?'}): conditional 缺少 'expression' 配置")
            # Check that then/else targets exist (best-effort)
            for key in ("then", "else"):
                target = config.get(key)
                if target and isinstance(target, str) and target not in step_ids:
                    # Will be checked later when all IDs are collected
                    pass
        elif stype == "loop":
            if "array" not in config and "items" not in config:
                errors.append(f"{prefix} ({sid or '?'}): loop 缺少 'array' 或 'items' 配置")
            if "sub_steps" not in config:
                errors.append(f"{prefix} ({sid or '?'}): loop 缺少 'sub_steps' 配置")
        elif stype == "sub_workflow":
            if "workflow" not in config:
                errors.append(f"{prefix} ({sid or '?'}): sub_workflow 缺少 'workflow' 配置")

    # Check conditional targets reference existing steps
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") == "conditional":
            config = step.get("config", {})
            sid = step.get("id", "?")
            for key in ("then", "else"):
                target = config.get(key)
                if target and isinstance(target, str) and target not in step_ids:
                    errors.append(f"步骤 '{sid}': {key} 目标 '{target}' 不存在于步骤列表中")

    # ── 5. Try parsing via WorkflowDefinition ──
    try:
        from dragon.workflow import WorkflowDefinition
        wf = WorkflowDefinition.from_dict(raw)
        # Successfully parsed
    except Exception as e:
        errors.append(f"WorkflowDefinition 解析失败: {e}")

    _print_validate_result(file_path, errors, warnings)


def _print_validate_result(file_path, errors, warnings):
    """Helper: print validation results in a consistent format."""
    print(f"\n验证工作流: {file_path}")
    print("=" * 50)

    if not errors and not warnings:
        print("✓ 工作流验证通过！")
        return

    if errors:
        print(f"\n✗ 发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"  ✗ {e}")

    if warnings:
        print(f"\n⚠ 发现 {len(warnings)} 个警告:")
        for w in warnings:
            print(f"  ⚠ {w}")

    if not errors:
        print(f"\n✓ 语法检查通过（有警告但不影响使用）")


if __name__ == "__main__":
    main()
