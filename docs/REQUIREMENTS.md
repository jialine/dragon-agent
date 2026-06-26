# Dragon Agent v1.2 — 产品需求文档

> **版本**: v1.2 | **日期**: 2026-06-27 | **状态**: ✅ 核心已交付
> **定位**: To-C 多模型陪审团 · 诚实 AI Agent × 全平台覆盖

---

## 目录

1. [产品定位](#1-产品定位)
2. [用户画像](#2-用户画像)
3. [功能矩阵](#3-功能矩阵)
4. [平台支持](#4-平台支持)
5. [工具生态](#5-工具生态)
6. [性能指标](#6-性能指标)
7. [竞品对比](#7-竞品对比)
8. [技术栈](#8-技术栈)
9. [路线图](#9-路线图)

---

## 1. 产品定位

### 一句话

> **Dragon Agent — 比人类更诚实的 AI 员工：不知道就说不知道，说的每句话都能查到来源。**

### 三重定位

| 维度 | 定位 | 说明 |
|------|------|------|
| **To-C AI Agent** | 面向个人用户的生产力 AI | 无需编程，飞书/微信/Telegram 即用 |
| **多模型陪审团** | 3+ 大模型交叉验证 | 辩论 → 投票 → 裁决，幻觉率降低 60-75% |
| **全平台覆盖** | 16 通讯平台 + CLI/TUI | 用户在哪，Dragon 就在哪 |

### 商业模式

```
终端用户 (To C)
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│                     Dragon Agent v1.2                         │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Pipeline 智能管线                            │ │
│  │                                                          │ │
│  │  User Input → Router (意图分类) → Simple? → 单模型调度    │ │
│  │                                  → Complex? → Jury 辩论  │ │
│  │                                              ↓           │ │
│  │                              Risk Gate → 审批/自动执行    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              诚实 AI 引擎                                 │ │
│  │  ├─ Jury Debate (3-6 模型多轮辩论)  ├─ Consensus (共识)  │ │
│  │  ├─ FactChecker (事实核查)           ├─ HallMetrics (幻觉)│ │
│  │  ├─ Confidence (置信度校准)          └─ Guard (安全检查)  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌──────────┬──────────┬──────────┬──────────┬─────────────┐ │
│  │  Router  │  Memory  │  Voice   │ Workflow │    Cron     │ │
│  │ 1.5B本地 │ ChromaDB │ Streaming│  YAML   │  SQLite     │ │
│  │          │+知识图谱 │   TTS   │  Engine │  调度       │ │
│  └──────────┴──────────┴──────────┴──────────┴─────────────┘ │
│                                                               │
│  22 工具 · 16 平台 · MCP Server · REST API · Prometheus      │
└──────────────────────────────────────────────────────────────┘
```

### 核心差异化

| 能力 | Dragon | ChatGPT | Kimi | 豆包 |
|------|:------:|:-------:|:----:|:----:|
| 承认不知道 | ✅ 主动 | ❌ 编造 | ❌ 编造 | ❌ 编造 |
| 多模型交叉验证 | ✅ 3-6 模型辩论 | ❌ | ❌ | ❌ |
| 每条声明可溯源 | ✅ | 偶尔 | 偶尔 | ❌ |
| 置信度数值 | ✅ 0-100% | ❌ | ❌ | ❌ |
| 模型间分歧展示 | ✅ "A说X，B说Y" | ❌ | ❌ | ❌ |

---

## 2. 用户画像

### 2.1 核心用户

| 画像 | 场景 | 核心需求 | 使用平台 |
|------|------|----------|----------|
| **知识工作者** | 研究、写作、分析 | 可信信息、多视角讨论 | 飞书 / CLI / Web |
| **开发者** | 编码、调试、架构 | 工具调用、工作流自动化 | CLI / TUI / MCP |
| **企业员工** | 文档、日程、协作 | 飞书集成、自动化任务 | 飞书 / 企业微信 |
| **管理者** | 决策支持、报告 | 多方案对比、风险评估 | 飞书 / Telegram |
| **学生/研究者** | 学习、论文 | 事实核查、来源标注 | Web / CLI |

### 2.2 用户故事

```
作为一名知识工作者，我希望 Dragon 在不确定时主动告诉我，
而不是编造看似合理的答案，这样我可以信任它的输出。

作为一名开发者，我希望把 Dragon 作为 MCP Server 接入
Claude Desktop / Cursor，扩展 AI 编程助手的工具能力。

作为一名管理者，我希望 Dragon 对复杂决策启动多模型辩论，
展示不同方案的优劣和风险，帮我做出更明智的选择。

作为一名飞书用户，我希望在聊天窗口直接 @ Dragon，
让它帮我查文档、搜知识库、生成报告，无需切换工具。
```

---

## 3. 功能矩阵

### 3.1 核心引擎

| 功能 | 状态 | 说明 |
|------|:----:|------|
| **Router 意图分类** | ✅ | 本地 Qwen2-1.5B GGUF，行业+难度分类，<300ms |
| **Jury 多模型辩论** | ✅ | 3-6 模型 × 3 轮审议，加权投票 |
| **FactChecker 事实核查** | ✅ | 知识库 + Web Search 验证，515 LOC |
| **Consensus 共识引擎** | ✅ | 语义聚类 + 分歧标记 + 来源标注，397 LOC |
| **HallMetrics 幻觉追踪** | ✅ | 会话级幻觉率追踪 + 趋势仪表板，436 LOC |
| **Confidence 置信度校准** | ✅ | 0-100% 置信度评分，479 LOC |
| **Consult 协商模式** | ✅ | 多模型并行协商，964 LOC |
| **Guard 安全检查** | ✅ | PII 脱敏 / 违规检测 / 注入防护，601 LOC |
| **Think Scrubber** | ✅ | 清洗推理 token，节省成本，561 LOC |
| **Redact 脱敏** | ✅ | PII 自动识别与脱敏，626 LOC |
| **Context Compressor** | ✅ | 上下文压缩，≤512 tokens 摘要，782 LOC |

### 3.2 Pipeline 管线

| 功能 | 状态 | 说明 |
|------|:----:|------|
| Pipeline 编排 | ✅ | Router → Dispatch / Jury → Risk Gate → Response |
| 简单/复杂分流 | ✅ | difficulty_score ≥ 5.0 触发 Jury 辩论 |
| Risk Gate 风险门控 | ✅ | LOW→自动 / MEDIUM→警告 / HIGH→审批 / CRITICAL→阻断 |
| 会话轮数限制 | ✅ | 默认 150 轮，超限自动暂停 |
| 中断管理 | ✅ | 支持任务中断与恢复 |
| MD 报告生成 | ✅ | 复杂任务自动生成 Markdown 裁决报告 |

### 3.3 会话与记忆

| 功能 | 状态 | 说明 |
|------|:----:|------|
| Session 管理 | ✅ | SQLite WAL，多平台会话持久化 |
| ChromaDB 向量记忆 | ✅ | bge-small-zh-v1.5 嵌入，语义检索 |
| 知识图谱 | ✅ | NetworkX DiGraph，实体-关系图谱 |
| 记忆提取 | ✅ | 自动从对话中提取实体和关系 |
| Context Recall | ✅ | 根据当前查询检索历史相关对话 |

### 3.4 Skill 系统

| 功能 | 状态 | 说明 |
|------|:----:|------|
| Skill Engine | ✅ | 自进化 Skill CRUD + 版本追踪，614 LOC |
| Hermes 导入 | ✅ | 114 Hermes Skills 一键导入，664 LOC |
| 语义搜索 | ✅ | 按意义而非关键词查找 Skill |
| Skill 工具 | ✅ | 通过 tools 暴露 Skill 查询能力 |

### 3.5 自动化

| 功能 | 状态 | 说明 |
|------|:----:|------|
| **Workflow Engine** | ✅ | YAML 驱动，5 种步骤类型，439 LOC |
| ├─ llm_call | ✅ | LLM 调用步骤，支持模板变量 |
| ├─ tool_call | ✅ | 工具调用步骤，结果传递 |
| ├─ conditional | ✅ | 条件分支 (then/else 跳转) |
| ├─ loop | ✅ | 数组循环迭代 |
| └─ sub_workflow | ✅ | 嵌套子工作流 |
| **Cron 定时任务** | ✅ | Cron 表达式 + 间隔调度，SQLite 持久化 |
| **SubAgent 子代理** | ✅ | 委托子任务，独立上下文 |

### 3.6 语音与多媒体

| 功能 | 状态 | 说明 |
|------|:----:|------|
| **VoiceEngine 流式 TTS** | ✅ | edge-tts 中文神经网络语音，句级流式合成 |
| 流式播放模式 | ✅ | consume() + next_audio() 后台队列模式 |
| 异步生成器模式 | ✅ | stream() 一次传入分句 yield |
| 中文优化 | ✅ | zh-CN-XiaoxiaoNeural，句号/感叹号/问号断句 |
| 超长文本处理 | ✅ | 300 字符强制软断句 |

### 3.7 MCP Server

| 功能 | 状态 | 说明 |
|------|:----:|------|
| MCP Server | ✅ | JSON-RPC 2.0 over stdio，929 LOC |
| 自进化工具暴露 | ✅ | Skill 成功率追踪 + 自动改进 |
| 多模型辩论暴露 | ✅ | 专家协商作为 MCP 工具 |
| 语义 Skill 发现 | ✅ | 按意义查找 Skill |
| 知识图谱查询 | ✅ | 实体-关系图查询接口 |

### 3.8 API 与商业化

| 功能 | 状态 | 说明 |
|------|:----:|------|
| REST API (FastAPI) | ✅ | /v1/chat, /v1/chat/stream, /v1/chat/voice |
| Auth (JWT/OAuth) | ✅ | 618 LOC |
| Billing/Subscription | ✅ | 订阅管理，606 LOC |
| API Key Mgmt | ✅ | Key 创建/吊销/配额，352 LOC |
| Usage Pricing | ✅ | 用量计费，639 LOC |
| Rate Limiter | ✅ | 频率限制，631 LOC |
| Credential Pool | ✅ | 凭证池管理，835 LOC |
| DB (SQLite) | ✅ | ORM 模型，185 LOC |

### 3.9 监控与运维

| 功能 | 状态 | 说明 |
|------|:----:|------|
| **Prometheus Metrics** | ✅ | 9 指标完整实现，147 LOC |
| ├─ 请求计数 | ✅ | 按行业+难度标签 |
| ├─ 请求延迟 | ✅ | Histogram (0.1s-120s buckets) |
| ├─ Token 消耗 | ✅ | 按模型标签 |
| ├─ 工具调用计数 | ✅ | 按工具名标签 |
| ├─ 活跃会话数 | ✅ | Gauge |
| ├─ 错误计数 | ✅ | 按错误类型标签 |
| ├─ 运行时间 | ✅ | Uptime seconds |
| ├─ 内存占用 | ✅ | RSS bytes |
| └─ CPU 使用率 | ✅ | Percent |
| Docker 部署 | 🔜 | Dockerfile + compose (P1) |
| CI/CD | 🔜 | Gitee Actions (P1) |
| 生产压测 | 🔜 | 100 并发 (P2) |

### 3.10 前端界面

| 功能 | 状态 | 说明 |
|------|:----:|------|
| CLI | ✅ | 全功能命令行，1291 LOC |
| TUI (Python) | ✅ | JSON-RPC 后端，703 LOC |
| TUI (Node.js) | 🔜 | Ink/React 终端 UI (P2) |
| Web UI | 🔜 | 管理面板 + 聊天 (P2) |

---

## 4. 平台支持

### 4.1 即时通讯 (16 Gateways)

| 平台 | 适配器 | 协议 | 状态 |
|------|--------|------|:----:|
| **飞书 (Feishu/Lark)** | `feishu.py` | WebSocket + Webhook | ✅ |
| **Telegram** | `telegram.py` | Bot API (长轮询) | ✅ |
| **微信 (WeChat)** | `wechat.py` | 公众号回调 | ✅ |
| **企业微信 (WeCom)** | `wecom.py` | 应用消息 API | ✅ |
| **钉钉 (DingTalk)** | `dingtalk.py` | 机器人 Webhook | ✅ |
| **Discord** | `discord.py` | Gateway + REST | ✅ |
| **Slack** | `slack.py` | Events API + WebSocket | ✅ |
| **WhatsApp** | `whatsapp.py` | Cloud API | ✅ |
| **Signal** | `signal.py` | signal-cli REST | ✅ |
| **Matrix** | `matrix.py` | Matrix 协议 | ✅ |
| **Mattermost** | `mattermost.py` | WebSocket + REST | ✅ |
| **QQ** | `qqbot.py` | QQ Bot API | ✅ |
| **SMS** | `sms.py` | Twilio / 阿里云短信 | ✅ |
| **Email** | `email.py` | SMTP + IMAP | ✅ |
| **通用 Webhook** | `webhook.py` | HTTP POST / JSON | ✅ |
| **Pairing** | `pairing.py` | 配对码绑定 | ✅ |

### 4.2 本地接口

| 接口 | 说明 | 状态 |
|------|------|:----:|
| **CLI** | `dragon chat`, `dragon config`, `dragon workflow` | ✅ |
| **TUI** | Rich/Ink 终端 UI，支持 /quit /help /new | ✅ |

### 4.3 部署模式

| 模式 | 命令 | 场景 |
|------|------|------|
| CLI 交互 | `dragon chat` | 开发调试 |
| Gateway 服务 | `dragon gateway --feishu --port 8080` | 飞书机器人 |
| API Server | `dragon serve --port 8000` | 商业化 |
| MCP Server | `python -m dragon.mcp.server` | Claude/Cursor 扩展 |
| Workflow | `dragon workflow run research.yaml` | 自动化工作流 |

---

## 5. 工具生态

### 5.1 工具矩阵 (22 工具大类)

| # | 工具 | 核心函数 | 说明 | 状态 |
|---|------|----------|------|:----:|
| 1 | **Search** | search, file_read, file_write, execute | 文件搜索/读写/命令执行 | ✅ |
| 2 | **Web Search** | web_search, web_fetch, web_download | 多引擎搜索 (Brave/SearXNG/DDG) | ✅ |
| 3 | **Vision** | vision_analyze, vision_info, ocr | AI 图像分析 + OCR | ✅ |
| 4 | **TTS** | tts, tts_voices | edge-tts 文本转语音 | ✅ |
| 5 | **Browser** | open, screenshot, get_text, click, type, close | Playwright 无头浏览器 | ✅ |
| 6 | **Image Gen** | image_generate, image_models | 5 后端 (ComfyUI/RunningHub/Stability/Replicate/OpenAI) | ✅ |
| 7 | **Maps** | geocode, reverse_geocode, get_route, search_poi | 地理编码/路径规划/POI | ✅ |
| 8 | **Email** | send, search, read | SMTP/IMAP 邮件 | ✅ |
| 9 | **Kanban** | create_board, add_task, list, move, delete, list_boards | 看板管理 | ✅ |
| 10 | **Spotify** | search, now_playing, play, pause, skip, previous, queue, devices, volume, playlists | 🆕 完整播放控制 | ✅ |
| 11 | **Notion** | search, read_page, create_page | 知识库集成 | ✅ |
| 12 | **Linear** | list_issues, create_issue | 项目管理 | ✅ |
| 13 | **Airtable** | list_records, create_record | 数据库表格 | ✅ |
| 14 | **YouTube** | transcript, summarize | 字幕提取+摘要 | ✅ |
| 15 | **Obsidian** | read, search, create | 笔记管理 | ✅ |
| 16 | **GIF Search** | search, trending | GIF 搜索 | ✅ |
| 17 | **Google Workspace** | gmail_send, gmail_search, drive_search, calendar_list | Gmail/Drive/Calendar | ✅ |
| 18 | **Feishu Docs** | read_doc, list_docs, create_doc | 飞书文档读写 | ✅ |
| 19 | **Documents** | pptx_read/create, pdf_read/extract, docx_read | Office 文档处理 | ✅ |
| 20 | **Analysis** | code_exec, data_explore, data_plot | Python 代码/数据分析/绘图 | ✅ |
| 21 | **Skills** | skill_*, skill_view, skill_search | Skill 查询与管理 | ✅ |
| 22 | **HTTP** | http_get | 通用 HTTP 请求 | ✅ |

### 5.2 工具架构

```
每个工具支持:
  ├─ circuit_breaker (熔断: 连续失败 N 次后暂停)
  ├─ retry (重试: 指数退避)
  ├─ timeout (超时: 可配置)
  ├─ guardrails (安全护栏: 路径/命令白名单)
  └─ telemetry (指标: 调用次数/延迟/成功率 → Prometheus)
```

---

## 6. 性能指标

### 6.1 响应性能

| 指标 | 目标 | 当前 | 说明 |
|------|------|------|------|
| **Router 分类延迟** | < 500ms | < 300ms | Qwen2-1.5B 本地推理 |
| **简单任务端到端** | < 3s | ~2s | 单模型 dispatch |
| **复杂任务 (Jury)** | < 30s | ~15-25s | 3 模型 × 3 轮辩论 |
| **TTS 首句延迟** | < 2s | ~1.5s | edge-tts 流式合成 |
| **Web Search** | < 3s | ~2s | Brave/SearXNG/DDG |
| **MCP 工具调用** | < 5s | ~3s | JSON-RPC over stdio |

### 6.2 幻觉控制

| 指标 | 单模型基线 | Dragon v1.2 | 提升 |
|------|:---------:|:-----------:|:----:|
| TruthfulQA MC2 幻觉率 | 30-40% | **8-12%** | **60-75% ↓** |
| "I don't know" 率 | <2% | **15-20%** | 诚实度 10× |
| 来源可追溯率 | ~10% | **>90%** | — |
| 事实核查覆盖 | 0% | **100%** (复杂任务) | — |

### 6.3 可靠性

| 指标 | 目标 | 状态 |
|------|------|:----:|
| Provider Fallback | 3 级自动降级 | ✅ AgileMind → DeepSeek → Qwen 本地 |
| 模型加载容错 | Router 不可用时关键词 fallback | ✅ |
| 工具熔断 | 连续 5 次失败后暂停 60s | ✅ |
| 会话持久化 | SQLite WAL，崩溃恢复 | ✅ |
| 凭证池 | 多 Key 轮换 + 耗尽告警 | ✅ |

### 6.4 资源占用

| 资源 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4 核 |
| RAM | 4 GB | 8 GB |
| 磁盘 | 2 GB | 10 GB (含模型) |
| GPU | 不需要 | NVIDIA (ComfyUI 可选) |
| Router 模型 | Qwen2-1.5B Q4_K_M (~1GB) | — |
| 嵌入模型 | bge-small-zh-v1.5 (~100MB) | — |

---

## 7. 竞品对比

### 7.1 功能对比

| 维度 | Dragon v1.2 | ChatGPT | Kimi | 豆包 | Coze |
|------|:----------:|:-------:|:----:|:----:|:----:|
| **诚实 AI** | | | | | |
| 多模型交叉验证 | ✅ 3-6 模型 | ❌ | ❌ | ❌ | ❌ |
| 主动承认不知道 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 置信度数值 | ✅ 0-100% | ❌ | ❌ | ❌ | ❌ |
| 来源标注 | ✅ 每条 | 偶尔 | 偶尔 | ❌ | ❌ |
| 模型分歧展示 | ✅ | ❌ | ❌ | ❌ | ❌ |
| 幻觉率追踪 | ✅ HallMetrics | ❌ | ❌ | ❌ | ❌ |
| **平台覆盖** | | | | | |
| 即时通讯平台 | ✅ 16 个 | ⚠️ 3 个 | ⚠️ 2 个 | ⚠️ 2 个 | ⚠️ 5 个 |
| CLI/TUI | ✅ | ❌ | ❌ | ❌ | ❌ |
| MCP Server | ✅ | ❌ | ❌ | ❌ | ❌ |
| **工具生态** | | | | | |
| 工具数量 | ✅ 22 大类 | ⚠️ 有限 | ⚠️ 有限 | ⚠️ 有限 | ⚠️ 有限 |
| 工作流引擎 | ✅ YAML 驱动 | ❌ | ❌ | ❌ | ✅ 可视化 |
| 浏览器自动化 | ✅ Playwright | ⚠️ 受限 | ❌ | ❌ | ❌ |
| Image Gen | ✅ 5 后端 | ✅ DALL-E | ❌ | ❌ | ❌ |
| Spotify 播放 | ✅ 完整控制 | ❌ | ❌ | ❌ | ❌ |
| **部署** | | | | | |
| 本地部署 | ✅ 开源 | ❌ | ❌ | ❌ | ❌ |
| 本地 Router | ✅ 1.5B | ❌ | ❌ | ❌ | ❌ |
| 离线可用 | ✅ 核心功能 | ❌ | ❌ | ❌ | ❌ |
| **商业化** | | | | | |
| API 定价 | ✅ 内置 | ✅ | ✅ | 免费 | 免费 |
| 多级订阅 | ✅ Free/Pro/Team/Enterprise | ✅ | ❌ | ❌ | ❌ |
| Prometheus 监控 | ✅ 9 指标 | ❌ | ❌ | ❌ | ❌ |

### 7.2 不可替代场景

| 场景 | 为什么选 Dragon |
|------|-----------------|
| **法律咨询** | 多模型验证法条引用，避免编造假法条 |
| **医疗建议** | 模型分歧时主动拒绝给出危险建议 |
| **投资分析** | 标注数据时效性，区分"已知事实"和"推测" |
| **企业合规** | 全链路可审计，每条输出可追溯来源 |
| **隐私敏感** | 本地 Router 确保用户问题不出本机 |
| **多平台运营** | 一套系统覆盖 16 个通讯平台 |

---

## 8. 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 路由模型 | Qwen2-1.5B GGUF Q4_K_M | 本地推理，<300ms，~1GB 显存 |
| 后端 LLM | AgileMind / DeepSeek / OpenAI / Anthropic | 3 级 fallback |
| 向量库 | ChromaDB PersistentClient | 语义检索 |
| 嵌入模型 | BAAI/bge-small-zh-v1.5 | 中文优化，~100MB |
| 知识图谱 | NetworkX DiGraph | 实体-关系持久化 (JSON) |
| 会话存储 | SQLite (WAL 模式) | 零配置，崩溃恢复 |
| API 框架 | FastAPI | REST + WebSocket + SSE |
| 配置 | YAML + ENV | `~/.dragon/config.yaml` |
| TTS | edge-tts | 免费高质量神经语音 |
| 浏览器 | Playwright | 无头 Chromium |
| 监控 | prometheus_client | /metrics 端点 |
| MCP | JSON-RPC 2.0 over stdio | Claude/Cursor 集成 |
| 语言 | Python 3.10+ | 异步 (asyncio) |

---

## 9. 路线图

### v1.2 (当前) ✅

- ✅ Pipeline 管线 (Router → Jury → Risk Gate)
- ✅ VoiceEngine 流式 TTS (句级合成)
- ✅ WorkflowEngine (YAML 驱动，5 步骤类型)
- ✅ Prometheus 监控 (9 指标)
- ✅ MCP Server (929 LOC，JSON-RPC stdio)
- ✅ Spotify 完整播放控制 (10 函数)
- ✅ API 商业化完整 (Auth/Billing/Keys/Usage)
- ✅ Memory 知识图谱
- ✅ Cron 定时任务
- ✅ Context Compressor

### v1.3 (计划) 🔜

| 项目 | 优先级 |
|------|:------:|
| Docker / Docker Compose 部署 | P1 |
| CI/CD (Gitee Actions) | P1 |
| TUI Node.js 前端 (Ink/React) | P2 |
| Web UI 管理面板 | P2 |
| 行业 SKILL.md (4 行业) | P2 |
| 生产压测 (100 并发) | P2 |
| 多语言 TTS 引擎 | P3 |
| 语音输入 (ASR) | P3 |

---

> **Dragon Agent — 不是从不出错的 AI，而是知道自己会出错、并主动告诉你的 AI。**
>
> v1.2 · 22 工具 · 16 平台 · 6 层防幻觉 · 开源可部署
