# 🐼 Panda Agent

**Self-Evolving AI Agent Framework** — CLI + TUI + multi-platform gateway.

Panda Agent is a self-evolving AI agent with persistent memory, skill system, scheduled jobs, and multi-platform messaging (Feishu, Telegram, Discord, WeChat). Ships with a terminal UI built on Ink/React.

---

## Quick Start

### One-Click Install

```bash
curl -fsSL https://gitee.com/jialine/panda-agent/raw/main/scripts/install.sh | bash
```

This script will:
1. Clone / update the repo to `~/panda-agent`
2. Create Python virtual environment and install dependencies
3. Install TUI frontend (Node.js, if available)
4. Create data directories (`~/.panda/`)
5. Add `panda` alias to your shell rc file

### Manual Install

```bash
git clone https://gitee.com/jialine/panda-agent.git ~/panda-agent
cd ~/panda-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# (Optional) TUI frontend
cd tui && npm install && cd ..
```

**Prerequisites:** Python ≥ 3.11, git, (optional) Node.js ≥ 20 for TUI

### First Run

```bash
source ~/panda-agent/.venv/bin/activate  # or restart shell for alias

panda setup     # Interactive setup wizard
panda chat      # Start chatting
panda --help    # See all commands
```

---

## CLI Commands

| Command | Description |
|---|---|
| `panda chat` | Interactive or single-query chat |
| `panda serve` | Start API server (REST) |
| `panda gateway` | Multi-platform gateway (Feishu/Telegram/Discord/WeChat) |
| `panda mcp` | MCP server for tool integration |
| `panda config` | Manage configuration (show/edit/init/validate) |
| `panda skills` | Manage self-evolving skills (list/search/create/delete/evolve) |
| `panda tools` | Manage tools (list/search/call) |
| `panda sessions` | Manage sessions (list/search/get/delete/export/stats) |
| `panda cron` | Scheduled jobs (list/add/pause/resume/remove/run) |
| `panda profile` | Profile management (list/create/edit/clone/export/import) |
| `panda test` | Run tests |
| `panda doctor` | Diagnostic checks |
| `panda tui` | Start TUI backend server |
| `panda setup` | Interactive setup wizard |

---

## Terminal UI (TUI)

```
┌─ Ink/React TUI ──────────────────────────┐
│  app.tsx → Chat / Sidebar / ToolCall     │
│  backend.ts (spawn + stdin/stdout RPC)   │
└──────────────┬───────────────────────────┘
               │ JSON-RPC (newline-delimited)
┌──────────────▼───────────────────────────┐
│  panda tui (Python)                      │
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

This spawns `python -m panda.tui.server` automatically via stdin/stdout JSON-RPC.

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

### One-Click (Recommended)

```bash
curl -fsSL https://gitee.com/jialine/panda-agent/raw/main/scripts/install.sh | bash
```

### Linux / WSL

```bash
# 1. Clone
git clone git@gitee.com:jialine/panda-agent.git ~/panda-agent
cd ~/panda-agent

# 2. Create venv
python3 -m venv .venv
source .venv/bin/activate

# 3. Install
pip install -e .

# 4. Configure
panda setup --quick

# 5. Run
panda chat
```

### Docker (coming soon)

```bash
docker build -t panda-agent .
docker run -it panda-agent
```

### Headless (server mode)

```bash
# REST API on port 8000
panda serve --host 0.0.0.0 --port 8000

# Multi-platform gateway
panda gateway start --feishu --telegram
```

---

## Project Structure

```
panda-agent/
├── panda/                  # Python package
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
├── docs/                   # Documentation
│   ├── REQUIREMENTS.md     # Requirements & feature spec
│   └── DESIGN.md           # Architecture design doc
└── README.md               # This file
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE)
