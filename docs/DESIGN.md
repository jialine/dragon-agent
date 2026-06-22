# Dragon Agent — Architecture Design Document

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Dragon Agent                            │
├───────────┬───────────┬───────────┬───────────┬────────────┤
│   CLI     │   TUI     │  Gateway  │   MCP     │   Cron     │
│  (chat,   │ (Ink/     │ (Feishu,  │ (tools    │ (scheduled │
│  config…) │  React)   │ Telegram…)│ server)   │  jobs)     │
├───────────┴───────────┴───────────┴───────────┴────────────┤
│                    Core Services                           │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐ │
│  │ Session  │  Skill   │  Tool    │  Memory  │ Compress │  Config  │ │
│  │ Manager  │  Engine  │ Registry │  Store   │  Engine  │  Manager │ │
│  └──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘ │
├─────────────────────────────────────────────────────────────┤
│                   Provider Layer                           │
│  ┌──────────┬──────────┬──────────┬──────────────────┐    │
│  │AgileMind │  DeepSeek│  OpenAI  │   Anthropic ...  │    │
│  │(default) │(fallback)│(fallback)│                  │    │
│  └──────────┴──────────┴──────────┴──────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Role |
|---|---|
| **Interface** | CLI, TUI, Gateway, MCP — user-facing entry points |
| **Core Services** | Session, Skill, Tool, Memory, Compression, Config — business logic |
| **Provider** | Model abstraction layer — OpenAI, Anthropic, local GGUF |

---

## 2. TUI Architecture

### 2.1 Process Model

```
┌─ Node.js Process (tui/) ──────────────────┐
│  Ink/React app                             │
│  backend.ts — spawns Python subprocess     │
│     │ pipe (stdin/stdout)                  │
│     ▼                                      │
│  ┌─ Python Subprocess ───────────────┐     │
│  │  dragon.tui.server                 │     │
│  │  JSON-RPC 2.0 over stdio          │     │
│  └───────────────────────────────────┘     │
└────────────────────────────────────────────┘
```

### 2.2 Protocol: JSON-RPC over Stdio

Each message is a single newline-delimited JSON line.

**Request:**
```json
{"jsonrpc": "2.0", "id": 1, "method": "chat.send", "params": {"message": "Hello"}}
```

**Response:**
```json
{"jsonrpc": "2.0", "id": 1, "result": {"session_id": "abc", "content": "Hi!"}}
```

**Error:**
```json
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Unknown method"}}
```

### 2.3 Component Tree

```
App (app.tsx)
├── Sidebar (Sidebar.tsx)
│   ├── Session list
│   ├── Skill browser
│   └── Status indicator
└── Chat (Chat.tsx)
    ├── Message list (streaming)
    └── Input area
        └── ToolCall cards (ToolCall.tsx)
```

### 2.4 State Flow

```
User types message
    → backend.call("chat.send", {message})
    → stdin: JSON-RPC request
    → Python server processes
    → stdout: JSON-RPC response
    → backend.ts resolves
    → React state update → Ink re-renders
```

---

## 3. Core Service Design

### 3.1 Session Manager

```
SessionStore
├── create(title, platform, model) → Session
├── get(session_id) → Session | None
├── list(limit, offset) → List[Session]
├── delete(session_id)
├── add_message(session_id, role, content) → Message
├── get_messages(session_id, limit) → List[Message]
├── search(query) → List[Session]
└── stats(since, until) → Stats
```

**Storage:** SQLite with WAL mode. `sessions` and `messages` tables.

### 3.2 Skill Engine

```
SkillEngine
├── list() → List[SkillMeta]
├── search(query) → List[SkillMeta]
├── create(name, content, tags, description) → Skill
├── delete(name)
├── evolve(name, new_content) → Skill  # increments version
├── rollback(name) → Skill            # restores previous version
└── get(name) → Skill | None
```

**Storage:** File-based — each skill is a markdown file in `dragon_data/skills/` with YAML frontmatter.

### 3.3 Tool Registry

```
ToolRegistry
├── register(tool: Tool)
├── list() → List[ToolMeta]
├── search(query) → List[ToolMeta]
├── call(name, args) → ToolResult
└── get_schema(name) → JSONSchema
```

**Built-in tools:** terminal, file read/write, web search, code execution, session search, memory management.

### 3.4 Config Manager

```
ConfigManager
├── get(key: str) → Any
├── get_all() → Dict
├── set(key, value)
├── validate() → List[Issue]
├── path() → str
└── check() → bool
```

**Storage:** YAML file at `~/.dragon/config.yaml`. Environment variable overrides with `DRAGON_` prefix.

### 3.5 Provider Registry

```
ProviderRegistry
├── register(name, provider: Provider)
├── call(provider_name, model, messages) → Response
├── list() → List[ProviderMeta]
└── get(provider_name) → Provider | None
```

**Supported providers:** OpenAI (GPT-4o, etc.), Anthropic (Claude), Local GGUF (via llama.cpp).

### 3.6 Context Compressor

```
ContextCompressor
├── compress(messages, current_query, max_tokens=512) → CompressedContext
│   ├── Step 1: Semantic retrieval from MemoryStore (top_k=5)
│   ├── Step 2: Retrieval from Skill Store (top_k=3)
│   ├── Step 3: Summary generation via local router model (0.8B)
│   └── Step 4: Build injection-friendly system prompt
├── stats(session_id) → CompressionStats
│   ├── original_tokens, compressed_tokens, ratio
│   └── latency_ms, relevance_score
└── get_summary(session_id) → str | None
```

**Purpose:** Before each request to the industry LLM, extract relevant context from conversation history and knowledge base, compress into a compact summary (≤512 tokens). Reduces upstream token count by 5-26x, saving API costs.

**Integration point:** Sits between Router/Dispatch and the actual Provider call. Takes full `messages` array, returns `compressed_messages` array with summary injected as system prompt.

**Resource:** Reuses the 0.8B router model for summary generation. No additional model loading required.

---

## 4. Data Flow: Chat Message

```
User Input
    │
    ▼
CLI / TUI / Gateway
    │
    ▼
Router.classify(query) → industry, confidence
    │
    ▼
ContextCompressor.compress(messages, query)
    │  Step 1: MemoryStore.recall(query)    → relevant history
    │  Step 2: SkillStore.search(query)     → knowledge hits
    │  Step 3: Local model summary          → ≤512 tokens
    │
    ▼
Build compressed_messages = [summary_system_prompt, current_question]
    │
    ▼
ProviderRegistry.call(provider, model, compressed_messages)
    │
    ├─→ OpenAI HTTP API ──→ response
    ├─→ Anthropic API   ──→ response
    └─→ Local GGUF      ──→ response
    │
    ▼
SessionStore.add_message(session_id, role, content)
    │
    ▼
Return to caller (CLI stdout / TUI JSON-RPC / Gateway platform)
```

---

## 5. Cron Job Architecture

```
Scheduler (background thread)
├── Tick loop: every 30s checks schedule
├── For each due job:
│   ├── Create isolated session
│   ├── Load attached skills
│   ├── Execute prompt (agent loop)
│   └── Deliver result to target
└── Job storage: SQLite `cron_jobs` table
```

**Delivery targets:**
- `origin` → Back to originating chat/channel
- `local` → Save to file only
- `all` → Broadcast to all connected platforms
- `platform:chat_id:thread_id` → Specific destination

---

## 6. Gateway (Multi-Platform)

```
Gateway
├── Feishu Adapter (lark-oapi)
│   ├── Webhook receiver (HTTP endpoint)
│   ├── Bot sender (API client)
│   └── Event subscription
├── Telegram Adapter (python-telegram-bot)
├── Discord Adapter (discord.py)
├── WeChat Adapter (wechatpy)
└── Message Router
    └── Unified message format → Core Agent → Response routing
```

---

## 7. Directory Layout

```
dragon-agent/
├── dragon/                    # Python package (namespace)
│   ├── __init__.py
│   ├── cli.py                # CLI entry point
│   ├── session/              # Session management
│   │   ├── __init__.py
│   │   ├── store.py          # SQLite store
│   │   └── models.py         # Pydantic models
│   ├── skill/                # Skill engine
│   │   ├── __init__.py
│   │   ├── engine.py         # CRUD + versioning
│   │   └── models.py
│   ├── tool/                 # Tool registry
│   │   ├── __init__.py
│   │   ├── registry.py
│   │   ├── builtins.py       # Terminal, file, web, etc.
│   │   └── models.py
│   ├── memory/               # Persistent memory
│   ├── compressor/           # Context compression engine
│   │   ├── __init__.py
│   │   ├── compressor.py     # Summary generation + retrieval
│   │   └── estimator.py      # Token counting + stats
│   ├── config/               # Config management
│   ├── provider/             # Model provider abstraction
│   ├── cron/                 # Scheduled jobs
│   └── tui/                  # TUI backend
│       ├── __init__.py
│       └── server.py         # JSON-RPC server
├── tui/                      # TUI frontend (Node.js)
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── app.tsx
│       ├── backend.ts
│       └── components/
│           ├── Chat.tsx
│           ├── Sidebar.tsx
│           └── ToolCall.tsx
├── docs/                     # Documentation
│   ├── REQUIREMENTS.md
│   └── DESIGN.md
├── tests/                    # Test suite
├── README.md
├── LICENSE
└── .gitignore
```

---

## 8. Key Design Decisions

### 8.1 Why JSON-RPC over stdio for TUI?

- **No network port** — No firewall, no port conflicts, no security exposure
- **Process lifecycle coupling** — Frontend spawns backend; when frontend exits, backend dies
- **Simplicity** — Newline-delimited JSON is trivial to parse in any language
- **Streaming compatible** — Chunk/done envelope pattern for real-time streaming

### 8.2 Why SQLite for sessions?

- Zero-config, no server process
- WAL mode for concurrent reads
- Single file for backup/export
- Sufficient for single-user agent workloads

### 8.3 Why file-based skills?

- Skills are markdown — human-readable and editable
- Git-friendly (can be versioned alongside code)
- Easy to share and copy between installations
- YAML frontmatter for structured metadata

### 8.4 Why Python + Node.js split?

- Python: agent logic, tools, ML/AI integration, ecosystem
- Node.js + Ink: best-in-class terminal UI framework
- Clean boundary via JSON-RPC — each side can evolve independently

---

## 9. Future Roadmap

| Phase | Features |
|---|---|
| v1.2 | Context compression engine — auto-summarize history before LLM requests |
| v1.3 | pip installable `dragon-agent` package, Docker support |
| v1.4 | Plugin system, custom tool SDK |
| v2.0 | Distributed agent mesh (multi-node), agent-to-agent communication |
| v2.1 | Web UI (React SPA) alongside TUI |
