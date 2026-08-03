# 🐉 Dragon Agent

**Self-Evolving AI Agent Framework** — CLI + TUI + multi-platform gateway.

Dragon Agent is the **AgileMind Engine** (灵思引擎) To-C Editor, powered by a local **Qwen2-1.5B** model for routing and inference. Features persistent memory, skill system, scheduled jobs, and multi-platform messaging (Feishu, Telegram, Discord, WeChat).

> **Business Model**: Dragon Agent (Editor) → AgileMind API (SaaS) → Sell Tokens  
> **Architecture**: Qwen2-1.5B (local) + AgileMind API (default) + Cloud API fallback

---

## Quick Start

### 一键安装（推荐）

```bash
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/master/install.sh | bash
```

脚本自动完成：
1. 系统检查（Python ≥ 3.11 + git）
2. 拉取最新代码到 `~/dragon-agent`
3. 创建 `.venv` 并安装全部依赖
4. 生成默认配置（DeepSeek V4 Pro）
5. 运行单元测试验证

安装完成后设置 API Key 即可使用：

```bash
export DEEPSEEK_API_KEY=sk-xxx
```

### USB Edition (U盘版)

```bash
# For distributors: build USB package
bash scripts/make-usb.sh --with-model

# For end users: plug in and run
bash run.sh        # Linux/Mac
run.bat            # Windows (double-click)
```

First run auto-creates venv and launches setup wizard. See `scripts/make-usb.sh --help` for options.

### Manual Install

```bash
git clone https://gitee.com/jialine/dragon-agent.git ~/dragon-agent
cd ~/dragon-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# (Optional) TUI frontend
cd tui && npm install && cd ..
```

**Prerequisites:** Python ≥ 3.11, git, (optional) Node.js ≥ 20 for TUI

### First Run

```bash
source ~/dragon-agent/.venv/bin/activate  # or restart shell for alias

dragon setup     # Interactive setup wizard
dragon chat      # Start chatting
dragon --help    # See all commands
```

---

## CLI Commands

| Command | Description |
|---|---|
| `dragon chat` | Interactive or single-query chat |
| `dragon serve` | Start API server (REST) |
| `dragon gateway` | Multi-platform gateway (Feishu/Telegram/Discord/WeChat) |
| `dragon mcp` | MCP server for tool integration |
| `dragon config` | Manage configuration (show/edit/init/validate) |
| `dragon skills` | Manage self-evolving skills (list/search/create/delete/evolve) |
| `dragon tools` | Manage tools (list/search/call) |
| `dragon sessions` | Manage sessions (list/search/get/delete/export/stats) |
| `dragon cron` | Scheduled jobs (list/add/pause/resume/remove/run) |
| `dragon profile` | Profile management (list/create/edit/clone/export/import) |
| `dragon test` | Run tests |
| `dragon doctor` | Diagnostic checks |
| `dragon tui` | Start TUI backend server |
| `dragon setup` | Interactive setup wizard |

---

## Terminal UI (TUI)

```
┌─ Ink/React TUI ──────────────────────────┐
│  app.tsx → Chat / Sidebar / ToolCall     │
│  backend.ts (spawn + stdin/stdout RPC)   │
└──────────────┬───────────────────────────┘
               │ JSON-RPC (newline-delimited)
┌──────────────▼───────────────────────────┐
│  dragon tui (Python)                      │
│  server.py: 12 RPC methods              │
└──────────────────────────────────────────┘
```

### Start

```bash
# Install frontend deps (first time)
cd tui && npm install && cd ..

# Launch full TUI
npm --prefix tui start
```

This spawns `python -m dragon.tui.server` automatically via stdin/stdout JSON-RPC.

### RPC Methods

| Method | Description |
|---|---|
| `ping` | Health check |
| `chat.send` | Send message, create/continue session |
| `chat.history` | Get session message history |
| `tools.list` | List available tools |
| `tools.call` | Invoke a tool by name |
| `sessions.list` | List sessions |
| `sessions.get` | Get session details |
| `skills.list` | List skills |
| `skills.search` | Search skills by query |
| `config.get` | Get configuration value |
| `health` | System health check |
| `doctor` | Full diagnostic report |

---

## Deployment

### 一键安装（推荐）

```bash
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/master/install.sh | bash
```

### 手动安装

```bash
# 1. Clone
git clone git@gitee.com:jialine/dragon-agent.git ~/dragon-agent
cd ~/dragon-agent

# 2. Create venv
python3 -m venv .venv
source .venv/bin/activate

# 3. Install
pip install -e .

# 4. Configure
dragon setup --quick

# 5. Run
dragon chat
```

### Docker (coming soon)

```bash
docker build -t dragon-agent .
docker run -it dragon-agent
```

### Headless (server mode)

```bash
# REST API on port 8000
dragon serve --host 0.0.0.0 --port 8000

# Multi-platform gateway
dragon gateway start --feishu --telegram
```

---

## Project Structure

```
dragon-agent/
├── dragon/                  # Python package
│   ├── cli.py              # CLI entry (14 commands)
│   ├── session/            # Session management
│   ├── skill/              # Self-evolving skills engine
│   ├── tool/               # Tool registry + builtins
│   ├── config/             # Configuration management
│   └── tui/                # TUI backend
│       ├── __init__.py
│       └── server.py       # JSON-RPC server (703 lines)
├── tui/                    # Ink/React TUI frontend
│   ├── package.json        # Node.js deps
│   ├── tsconfig.json       # TypeScript config
│   └── src/
│       ├── app.tsx         # Main app (406 lines)
│       ├── backend.ts      # JSON-RPC client (447 lines)
│       └── components/
│           ├── Chat.tsx    # Chat panel (206 lines)
│           ├── Sidebar.tsx # Sidebar (188 lines)
│           └── ToolCall.tsx # Tool call card (123 lines)
├── install.sh              # 一键网络安装脚本
├── scripts/                # Deployment & packaging
│   ├── deploy.sh           # Local deployment script
│   └── make-usb.sh         # USB edition packager
├── docs/                   # Documentation
│   ├── REQUIREMENTS.md     # Requirements & feature spec
│   └── DESIGN.md           # Architecture design doc
└── README.md               # This file
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE)
