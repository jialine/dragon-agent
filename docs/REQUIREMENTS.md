# Panda Agent — Requirements Document

## 1. Overview

### 1.1 Project Purpose

Panda Agent is a **self-evolving AI agent framework** that provides:
- Persistent cross-session memory
- A self-improving skill system
- Multi-platform messaging integration
- Scheduled autonomous jobs (cron)
- Terminal UI for interactive use

### 1.2 Target Users

- **Developers** — CLI-first AI assistant with code execution, debugging, and project management
- **Power users** — Multi-platform bot with Feishu/Telegram/Discord/WeChat integration
- **Autonomous workflows** — Scheduled cron jobs that run without user supervision

---

## 2. Functional Requirements

### 2.1 Core Agent (FR-CORE)

| ID | Requirement | Priority |
|---|---|---|
| FR-CORE-01 | Persistent memory across sessions (facts, preferences, conventions) | P0 |
| FR-CORE-02 | Multi-turn conversation context with session isolation | P0 |
| FR-CORE-03 | Tool invocation — terminal, file I/O, web search, code execution | P0 |
| FR-CORE-04 | Streaming response output | P1 |
| FR-CORE-05 | Multi-model support (OpenAI, Anthropic, local GGUF) | P0 |
| FR-CORE-06 | Provider registry with hot-swappable model routing | P1 |

### 2.2 Skill System (FR-SKILL)

| ID | Requirement | Priority |
|---|---|---|
| FR-SKILL-01 | Self-evolving skills — learn from successful task patterns | P1 |
| FR-SKILL-02 | Skill CRUD: create, search, list, evolve, rollback | P1 |
| FR-SKILL-03 | Tag-based skill categorization | P2 |
| FR-SKILL-04 | Skill content versioning | P2 |

### 2.3 Session Management (FR-SESSION)

| ID | Requirement | Priority |
|---|---|---|
| FR-SESSION-01 | Session CRUD: list, search, get, delete | P1 |
| FR-SESSION-02 | Session export (JSON) | P2 |
| FR-SESSION-03 | Usage statistics (token count, message count, time range) | P2 |
| FR-SESSION-04 | Session persistence to SQLite | P0 |

### 2.4 CLI (FR-CLI)

| ID | Requirement | Priority |
|---|---|---|
| FR-CLI-01 | Interactive chat with stdin/stdout | P0 |
| FR-CLI-02 | Single-query mode (`-q "query"`) | P0 |
| FR-CLI-03 | Model/provider selection via flags | P1 |
| FR-CLI-04 | Subcommand structure: chat, serve, gateway, mcp, config, skills, tools, sessions, cron, profile, test, doctor, tui, setup | P0 |

### 2.5 Terminal UI (FR-TUI)

| ID | Requirement | Priority |
|---|---|---|
| FR-TUI-01 | Split-pane layout: sidebar + chat panel | P1 |
| FR-TUI-02 | JSON-RPC protocol over stdin/stdout (newline-delimited) | P1 |
| FR-TUI-03 | Streaming message display | P1 |
| FR-TUI-04 | Tool call visualization as cards | P2 |
| FR-TUI-05 | Session switcher in sidebar | P2 |
| FR-TUI-06 | Skill browser in sidebar | P2 |
| FR-TUI-07 | Automatic backend spawn (TS frontend spawns Python server) | P1 |

### 2.6 Gateway & Multi-Platform (FR-GATEWAY)

| ID | Requirement | Priority |
|---|---|---|
| FR-GATEWAY-01 | Feishu (飞书) integration — webhook + bot | P1 |
| FR-GATEWAY-02 | Telegram bot integration | P2 |
| FR-GATEWAY-03 | Discord bot integration | P2 |
| FR-GATEWAY-04 | WeChat integration | P3 |
| FR-GATEWAY-05 | Unified message routing across platforms | P2 |

### 2.7 Scheduled Jobs (FR-CRON)

| ID | Requirement | Priority |
|---|---|---|
| FR-CRON-01 | Cron job CRUD: list, add, pause, resume, remove, run | P1 |
| FR-CRON-02 | Cron-style schedule expressions | P1 |
| FR-CRON-03 | Autonomous execution (no user interaction needed) | P1 |
| FR-CRON-04 | Delivery targets: origin chat, specific platform, local file | P2 |

### 2.8 Configuration (FR-CONFIG)

| ID | Requirement | Priority |
|---|---|---|
| FR-CONFIG-01 | YAML/JSON configuration file | P0 |
| FR-CONFIG-02 | CLI config management: show, edit, path, check, init, validate | P1 |
| FR-CONFIG-03 | Multi-profile support (dev, prod, etc.) | P2 |
| FR-CONFIG-04 | Environment variable overrides | P1 |

---

## 3. Non-Functional Requirements

### 3.1 Performance

| ID | Requirement | Target |
|---|---|---|
| NFR-PERF-01 | CLI startup time | < 500ms |
| NFR-PERF-02 | TUI backend request timeout | 60s per request |
| NFR-PERF-03 | Session creation | < 100ms |
| NFR-PERF-04 | Streaming latency (first token) | < 2s |

### 3.2 Reliability

| ID | Requirement |
|---|---|
| NFR-REL-01 | Graceful degradation — TUI frontend auto-reconnects on backend crash |
| NFR-REL-02 | Session data durability (SQLite with WAL mode) |
| NFR-REL-03 | Cron job retry on transient failure |

### 3.3 Platform Support

| ID | Requirement |
|---|---|
| NFR-PLAT-01 | Linux (primary) |
| NFR-PLAT-02 | macOS |
| NFR-PLAT-03 | WSL (Windows Subsystem for Linux) |
| NFR-PLAT-04 | Windows (via WSL only) |

### 3.4 Security

| ID | Requirement |
|---|---|
| NFR-SEC-01 | API keys stored in config file (not env-only) with `.gitignore` protection |
| NFR-SEC-02 | No hardcoded credentials |
| NFR-SEC-03 | File system access restricted to permitted paths |

---

## 4. Data Model

### 4.1 Session

```
Session {
    id: UUID
    title: string
    platform: string (cli|feishu|telegram|discord|wechat|tui)
    model: string
    created_at: datetime
    updated_at: datetime
    token_count: int
    message_count: int
}
```

### 4.2 Message

```
Message {
    id: UUID
    session_id: UUID (FK)
    role: string (user|assistant|system|tool)
    content: string
    created_at: datetime
}
```

### 4.3 Skill

```
Skill {
    name: string (PK)
    description: string
    content: string (markdown)
    tags: []string
    version: int
    created_at: datetime
    updated_at: datetime
}
```

### 4.4 Memory Entry

```
Memory {
    id: UUID
    target: string (user|memory)
    content: string
    created_at: datetime
}
```

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 1.0.0 | 2026-05 | Core agent, CLI, memory, skills |
| 1.2.0 | 2026-05 | TUI backend + frontend, 14 CLI commands, cron, profiles |
