# Dragon Agent — 架构设计文档

> **版本**: v1.0 | **日期**: 2026-06-26 | **代码量**: 50,696 LOC
> **对齐**: Hermes Agent feature parity

---

## 1. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                       Dragon Agent                            │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│   CLI    │   TUI    │ Gateway  │   MCP    │   Cron / API    │
│ (chat/   │ (Ink/    │ (16 平台) │ (tools   │ (REST + Jobs)   │
│  config) │  React)  │          │  server) │                 │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│                     Core Engine                              │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────┐ │
│  │  Router  │  Jury    │  Fact    │Consensus │ HallMetrics│ │
│  │ (0.8B)   │ (debate) │ Checker  │ Builder  │ (幻觉追踪) │ │
│  ├──────────┼──────────┼──────────┼──────────┼────────────┤ │
│  │ Session  │  Skill   │  Tool    │  Memory  │  Config    │ │
│  │ Manager  │  Engine  │ Registry │ (Chroma) │  Manager   │ │
│  └──────────┴──────────┴──────────┴──────────┴────────────┘ │
├──────────────────────────────────────────────────────────────┤
│                    Provider Layer                            │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────┐ │
│  │AgileMind │ DeepSeek │  OpenAI  │Anthropic │  Local     │ │
│  │(default) │(fallback)│(fallback)│(fallback)│  GGUF      │ │
│  └──────────┴──────────┴──────────┴──────────┴────────────┘ │
├──────────────────────────────────────────────────────────────┤
│                    API Layer (FastAPI)                        │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────┐ │
│  │  Auth    │  API Key │  Billing │  Usage   │  Health    │ │
│  │ (JWT)    │  Mgmt    │  (订阅)   │  Pricing │  (监控)    │ │
│  └──────────┴──────────┴──────────┴──────────┴────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 层职责

| 层 | 职责 |
|----|------|
| **Interface** | CLI, TUI (JSON-RPC), Gateway (16平台), MCP, Cron, REST API |
| **Core Engine** | Router→Jury→FactCheck→Consensus 流水线 + Session/Skill/Tool/Memory |
| **Provider** | 多模型抽象 — AgileMind/DeepSeek/OpenAI/Anthropic/本地 GGUF |
| **API** | 商业化 — 认证/计费/Key管理/用量统计 |

---

## 2. 核心引擎流水线

```
User Input
    │
    ▼
Router.classify(query) → 意图识别 + 行业分类
    │
    ▼
ContextCompressor.compress(messages, query)
    │  ├─ MemoryStore.recall(query)     → 相关对话
    │  ├─ SkillStore.search(query)      → 知识命中
    │  └─ Router 本地摘要               → ≤512 tokens
    │
    ▼
【诚实 AI 管线】
    │
    ├─→ Jury.debate(query, models=3)
    │     └─ 3 轮审议 → 加权投票 → 裁决书
    │
    ├─→ FactChecker.verify(verdict)
    │     ├─ 知识库检索 (ChromaDB)
    │     ├─ Web Search (DDG/Brave)
    │     └─ 置信度评分
    │
    ├─→ Consensus.build(verified_verdicts)
    │     ├─ 语义聚类模型立场
    │     ├─ 共识/分歧判定
    │     └─ 来源标注
    │
    └─→ HallMetrics.record(session, verdict)
          └─ 幻觉率更新 + 趋势追踪
    │
    ▼
Guard.check(output) → 安全检查 (PII/违规/注入)
    │
    ▼
Session.add_message() → 持久化
    │
    ▼
Response → CLI stdout / TUI JSON-RPC / Gateway platform
```

---

## 3. 目录结构 (实际)

```
dragon-agent/
├── dragon/                        # Python 包
│   ├── main.py                    # 主入口 (1165 LOC)
│   ├── cli.py                     # CLI (1291 LOC)
│   ├── config.py                  # 配置管理 (222 LOC)
│   ├── session.py                 # 会话管理 [SQLite] (467 LOC)
│   │
│   ├── router/                    # 路由模型 (0.8B GGUF)
│   │   └── __init__.py            # 行业分类 (551 LOC)
│   │
│   ├── jury/                      # 陪审辩论引擎
│   │   └── __init__.py            # 3模型×3轮辩论 (1421 LOC)
│   │
│   ├── debate/                    # 辩论框架
│   │   └── __init__.py            # (978 LOC)
│   │
│   ├── dispatch/                  # 模型调度
│   │   └── __init__.py            # 行业→模型派发 (859 LOC)
│   │
│   ├── factcheck.py               # 事实核查 (515 LOC)
│   ├── consensus.py               # 共识+来源标注 (397 LOC)
│   ├── hallmetrics.py             # 幻觉率追踪 (436 LOC)
│   ├── confidence.py              # 置信度校准 (479 LOC)
│   ├── consult.py                 # 协商模式 (964 LOC)
│   ├── auxiliary.py               # 辅助模型 (1032 LOC)
│   │
│   ├── guard/                     # 输出安全
│   │   └── __init__.py            # PII/违规检测 (601 LOC)
│   │
│   ├── compressor/                # 上下文压缩
│   │   ├── compressor.py          # 摘要生成 (782 LOC)
│   │   └── estimator.py           # Token 估算 (273 LOC)
│   │
│   ├── skill/                     # Skill 系统
│   │   ├── engine.py              # CRUD+版本 (614 LOC)
│   │   ├── importer.py            # Hermes 导入 (664 LOC)
│   │   └── skill.py               # Skill 模型 (374 LOC)
│   │
│   ├── tool/                      # 工具系统
│   │   ├── registry.py            # 注册/发现 (592 LOC)
│   │   ├── guardrails.py          # 安全护栏 (562 LOC)
│   │   └── builtins/              # 20+ 内置工具
│   │       ├── __init__.py         # 核心工具集 (1126 LOC)
│   │       ├── vision.py, tts.py, browser.py, ...
│   │       └── (共19个工具模块, ~7000 LOC)
│   │
│   ├── memory/                    # 向量记忆
│   │   └── __init__.py            # ChromaDB + bge-small-zh (1252 LOC)
│   │
│   ├── provider/                  # 模型抽象
│   │   └── __init__.py            # OpenAI 兼容 (1564 LOC)
│   │
│   ├── plugin/                    # 插件系统
│   │   ├── __init__.py            # 核心 (1007 LOC)
│   │   ├── hooks.py               # 钩子 (454 LOC)
│   │   └── loader.py              # 加载 (397 LOC)
│   │
│   ├── gateway/                   # 多平台网关
│   │   ├── server.py              # FastAPI 主服务 (577 LOC)
│   │   ├── feishu.py              # 飞书 (814 LOC)
│   │   ├── telegram.py            # Telegram (298 LOC)
│   │   ├── wechat.py              # 微信 (285 LOC)
│   │   └── (13 个其他平台, ~2400 LOC)
│   │
│   ├── mcp/                       # MCP Server
│   │   ├── server.py              # (745 LOC)
│   │   └── protocol.py            # (158 LOC)
│   │
│   ├── api/                       # REST API (FastAPI)
│   │   ├── app.py                 # 应用 (86 LOC)
│   │   ├── auth.py                # JWT/OAuth (618 LOC)
│   │   ├── billing.py             # 计费 (606 LOC)
│   │   ├── apikeys.py             # Key 管理 (352 LOC)
│   │   └── models.py              # DB 模型 (185 LOC)
│   │
│   ├── tui/                       # TUI 后端
│   │   └── server.py              # JSON-RPC (703 LOC)
│   │
│   └── (其他: cron, subagent, insights, redact, ...)
│
├── docs/                          # 文档
│   ├── REQUIREMENTS.md
│   ├── DESIGN.md
│   └── ...
│
└── tests/ → build/usb-package/src/tests/  # 28 测试文件
```

---

## 4. 关键设计决策

### 4.1 为什么 0.8B 本地路由 + 122B 云端推理？

- **隐私**: 路由在本地完成，用户问题不出本机
- **速度**: 0.8B 分类 <200ms，不影响体验
- **成本**: 避免每次请求都送完整上下文给大模型
- **兜底**: AgileMind 不可用时自动 fallback

### 4.2 为什么多模型陪审团？

- **诚实**: 单一模型幻觉率高（15-25%），3 模型交叉验证可降至 <5%
- **来源**: 每个结论可追溯是哪个模型的观点
- **差异化**: 市场上尚无竞品

### 4.3 为什么 ChromaDB 而非 FTS5？

- **语义检索**: 中文场景语义匹配优于关键词
- **嵌入本地**: bge-small-zh 仅 100MB，无需外部 API
- **易用**: pip install 即用

### 4.4 为什么 TUI 用 Node.js + Ink？

- Ink/React 是最佳终端 UI 框架
- JSON-RPC over stdio 隔离进程
- 前后端可独立演进

---

## 5. 数据流: 完整 Chat 请求

```
1. User sends message via CLI/TUI/Gateway
2. Session.load() → 恢复历史
3. Router.classify() → 行业分类
4. Compressor.compress() → 上下文压缩
5. Jury.debate() → 多模型辩论
6. FactChecker.verify() → 事实核查
7. Consensus.build() → 共识输出 + 来源标注
8. Guard.check() → 安全检查
9. Session.save() → 持久化
10. Response → 用户
```

---

## 6. 部署模式

### 模式 A: CLI (开发/调试)
```bash
pip install dragon-agent
dragon chat "什么是量子计算？"
```

### 模式 B: Gateway (飞书机器人)
```bash
dragon gateway --feishu --port 8080
```

### 模式 C: API Server (商业化)
```bash
dragon serve --host 0.0.0.0 --port 8000
```

### 模式 D: USB 便携版
```
dragon-agent-usb/
├── dragon-agent.pyz  (zipapp)
├── models/           (可选)
└── config.yaml
```

---

## 7. Hermes 对齐对照表

| Hermes 概念 | Dragon 实现 | 文件 |
|------------|-----------|------|
| Agent Loop | `main.py::DragonAgent.run()` | main.py |
| Provider | `provider/__init__.py::ProviderRegistry` | provider/ |
| Skill System | `skill/engine.py::SkillEngine` | skill/ |
| Tool System | `tool/registry.py::ToolRegistry` | tool/ |
| Memory | `memory/__init__.py::DragonMemory` | memory/ |
| Session | `session.py::SessionStore` | session.py |
| Gateway | `gateway/server.py::GatewayServer` | gateway/ |
| MCP | `mcp/server.py::MCPServer` | mcp/ |
| Cron | `cron.py::CronScheduler` | cron.py |
| Subagent | `subagent.py::SubAgentManager` | subagent.py |
| Config | `config.py::ConfigManager` | config.py |
| CLI | `cli.py::main()` | cli.py |
| TUI | `tui/server.py::TUIServer` (Python) + `tui/` (Node.js) | tui/ |
