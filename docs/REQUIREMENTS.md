# Dragon Agent — 产品需求文档

> **版本**: v1.0 | **日期**: 2026-06-26 | **状态**: 核心已完工，对齐 Hermes
> **代码量**: 50,696 LOC | **模块**: 111 文件 | **测试**: 28 文件

---

## 1. 产品定位

**Dragon Agent = 多模型陪审团诚实 AI Editor × AgileMind Engine Token API × 飞书/微信全平台**

### 商业模式

```
终端用户 (To C)
    │ 飞书/微信/Telegram/CLI/TUI
    ▼
┌──────────────────────────────────────────┐
│  Dragon Agent (Editor)                    │
│                                           │
│  ┌──────────────────────────────────┐    │
│  │ 内置 Router (0.8B 本地小模型)      │    │
│  │  └─ 意图识别 → 行业分类            │    │
│  └──────────┬───────────────────────┘    │
│             │                             │
│  ┌──────────▼───────────────────────┐    │
│  │ Jury Engine (3+ 模型辩论)         │    │
│  │  └─ 3轮审议 → 加权投票 → 裁决     │    │
│  └──────────┬───────────────────────┘    │
│             │                             │
│  ┌──────────▼───────────────────────┐    │
│  │ 诚实 AI 引擎                       │    │
│  │  ├─ FactChecker (事实核查)         │    │
│  │  ├─ Consensus (共识输出)           │    │
│  │  ├─ Source Attribution (来源标注)  │    │
│  │  └─ HallMetrics (幻觉率追踪)       │    │
│  └──────────────────────────────────┘    │
│                                           │
│  后端模型: AgileMind 122B MoE (默认)       │
│  Fallback: DeepSeek / OpenAI / Anthropic  │
│  记忆: ChromaDB + 向量检索                 │
│  工具: 20+ 集成 (Hermes 对齐)              │
└──────────────────────────────────────────┘
```

---

## 2. Hermes 对齐现状

### 2.1 核心功能对齐 ✅

| Hermes 功能 | Dragon 实现 | 状态 |
|------------|-----------|------|
| Session 管理 | `session.py` (SQLite) | ✅ |
| Skill 系统 | `skill/engine.py` (114 Hermes skills 已导入) | ✅ |
| Tool Registry | `tool/registry.py` (20+ 内置工具) | ✅ |
| Memory | `memory/__init__.py` (ChromaDB + bge-small-zh) | ✅ |
| Context Compression | `compressor/compressor.py` | ✅ |
| Config System | `config.py` (+ YAML) | ✅ |
| Provider 插件 | `provider/__init__.py` (OpenAI 兼容) | ✅ |
| Plugin System | `plugin/__init__.py` | ✅ |
| Cron Jobs | `cron.py` (SQLite 调度) | ✅ |
| Subagent | `subagent.py` | ✅ |
| CLI | `cli.py` (1291 LOC) | ✅ |
| Gateway (多平台) | `gateway/` (16 平台) | ✅ |
| MCP Server | `mcp/server.py` | ✅ |
| Rate Limiter | `rate_limiter.py` | ✅ |

### 2.2 Dragon 独有差异化 ✅

| 差异化能力 | 实现 | 说明 |
|-----------|------|------|
| **Jury/Debate** | `jury/__init__.py` (1421 LOC) | 3+ 模型多轮辩论 |
| **Fact Checker** | `factcheck.py` (515 LOC) | 知识库+Web 验证 |
| **Consensus** | `consensus.py` (397 LOC) | 语义聚合+来源标注 |
| **HallMetrics** | `hallmetrics.py` (436 LOC) | 幻觉率追踪仪表板 |
| **Confidence** | `confidence.py` (479 LOC) | 置信度校准 |
| **Consult Mode** | `consult.py` (964 LOC) | 多模型协商 |
| **Guard** | `guard/__init__.py` (601 LOC) | 输出安全检查 |
| **Think Scrubber** | `think_scrubber.py` (561 LOC) | 清洗推理 token |
| **Redact** | `redact.py` (626 LOC) | PII 脱敏 |
| **Insights** | `insights.py` (1066 LOC) | 使用分析 |

### 2.3 工具对齐 ✅ (20/20)

| 工具 | 实现 | LOC | Hermes 对应 |
|------|------|-----|-----------|
| Vision | `vision.py` | 506 | vision_analyze |
| TTS | `tts.py` | 319 | text_to_speech |
| Browser | `browser.py` | 463 | browser |
| Web Search | `web_search.py` | 181 | web_search |
| Documents | `documents.py` | 496 | ocr-and-documents |
| Analysis | `analysis.py` | 625 | data-science |
| Email | `email.py` | 442 | himalaya |
| Kanban | `kanban.py` | 412 | kanban |
| Image Gen | `image_gen.py` | 972 | comfyui |
| Maps | `maps.py` | 465 | maps |
| Feishu Docs | `feishu_docs.py` | 380 | feishu_doc/drive |
| Google Workspace | `google_workspace.py` | 410 | google-workspace |
| Notion | `notion.py` | 404 | notion |
| Linear | `linear.py` | 303 | linear |
| Airtable | `airtable.py` | 212 | airtable |
| Obsidian | `obsidian.py` | 347 | obsidian |
| Spotify | `spotify.py` | 414 | spotify |
| YouTube | `youtube.py` | 218 | youtube-content |
| GIF Search | `gif_search.py` | 212 | gif-search |
| Skills | `skills.py` | 301 | skills |

### 2.4 Gateway 平台 ✅ (16/16)

Feishu, Telegram, Discord, WeChat, WeCom, DingTalk, Slack, WhatsApp, Signal, Matrix, Mattermost, QQ, SMS, Email, Webhook, Pairing

### 2.5 API/商业化 ✅

| 功能 | 实现 | LOC |
|------|------|-----|
| Auth (JWT/OAuth) | `api/auth.py` | 618 |
| Billing/Subscription | `api/billing.py` | 606 |
| API Key Mgmt | `api/apikeys.py` | 352 |
| Usage Pricing | `usage_pricing.py` | 639 |
| Credential Pool | `credential_pool.py` | 835 |
| Rate Limiter | `rate_limiter.py` | 631 |

---

## 3. 剩余差距

### 3.1 工程缺口

| 项目 | 状态 | 优先级 |
|------|------|--------|
| Monitoring/Prometheus | ⚠️ 18 LOC stub | P1 |
| Docker/Docker Compose | ❌ 未开始 | P1 |
| CI/CD (Gitee Actions) | ❌ 未开始 | P1 |
| Web UI | ⚠️ 70 LOC stub | P2 |
| TUI 前端 (Node.js) | ⚠️ 部分完成 | P2 |
| Voice Mode 流式播放 | ⚠️ 引擎完成，前端未接 | P2 |
| 行业 SKILL.md (4行业) | ❌ 未开始 | P2 |
| 生产压测 (100并发) | ❌ 未开始 | P2 |

### 3.2 功能缺口 vs Hermes

| Hermes 功能 | Dragon 状态 |
|------------|-----------|
| `delegate_task` (子代理) | ✅ `subagent.py` |
| `session_search` | ✅ `memory/` + ChromaDB |
| `memory` tool | ✅ `memory/__init__.py` |
| `cronjob` | ✅ `cron.py` |
| `execute_code` | ✅ `tool/builtins/analysis.py` |
| `voice_mode` | ⚠️ `voice_engine.py` (引擎完成) |
| `computer_use` | ❌ 未实现 (非 To-C 场景) |
| X/Twitter 社媒 | ❌ 未实现 (非中国场景) |
| Smart Home | ❌ 未实现 (非核心) |
| Game Servers | ❌ 未实现 (非核心) |

---

## 4. 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 路由模型 | Qwen3-0.6B GGUF | 本地推理，<400MB |
| 后端 LLM | AgileMind 122B MoE | 33 tok/s, 256K ctx |
| 向量库 | ChromaDB + bge-small-zh | 语义检索 |
| 嵌入模型 | bge-small-zh-v1.5 | 中文优化, ~100MB |
| 会话存储 | SQLite (WAL) | 零配置 |
| API 框架 | FastAPI | REST + WebSocket |
| 配置 | YAML + ENV | `~/.dragon/config.yaml` |
| 部署 | Python 包 + systemd | pip installable |
| 网关 | 16 平台适配器 | lark-oapi, discord.py 等 |
