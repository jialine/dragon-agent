# Dragon Agent — 架构设计文档

> **版本**: v1.2 | **日期**: 2026-06-27 | **代码量**: ~65,000 LOC
> **对齐**: Hermes Agent feature parity
> **新增**: Workflow 引擎 / 流式语音 / Prometheus 监控 / Pipeline 调度

---

## 1. 四层系统架构

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Dragon Agent v1.2                                  │
├────────────────┬────────────────┬────────────────┬────────────────┬──────────┤
│      CLI       │      TUI       │    Gateway     │      MCP       │ Cron/API │
│  (chat/config) │ (Ink/React)    │  (16 平台)      │  (tools server)│(REST+Job)│
│                │                │  Feishu/WeChat  │                │          │
├────────────────┴────────────────┴────────────────┴────────────────┴──────────┤
│                                                                              │
│                         Core Engine（核心调度层）                              │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                        Pipeline 调度管线                                 │ │
│  │  User → Router → Pipeline ─┬─ Simple: Dispatcher (单模型直达)            │ │
│  │                            └─ Complex: Jury [3-6 模型] → RiskGate → Report│ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌─────────────┬──────────────┬──────────────┬──────────────┬─────────────┐ │
│  │   Router    │    Jury      │   FactCheck  │  Consensus   │ HallMetrics │ │
│  │  (Qwen 1.5B)│  (多模型辩论) │  (事实核查)   │  (共识构建)   │ (幻觉追踪)   │ │
│  ├─────────────┼──────────────┼──────────────┼──────────────┼─────────────┤ │
│  │  Session    │    Skill     │ Memory       │  Workflow    │ Monitoring  │ │
│  │  Manager    │   Engine     │ (ChromaDB)   │  Engine ★     │ (Prometheus)│ │
│  │  [SQLite]   │              │              │  (YAML 驱动)  │  ★          │ │
│  ├─────────────┼──────────────┼──────────────┼──────────────┼─────────────┤ │
│  │  Tool       │  Guard       │  Compressor  │  Report ★    │ VoiceEngine │ │
│  │  Registry   │  (PII/安全)   │  (上下文压缩) │  (MD报告)     │  (流式TTS) ★│ │
│  └─────────────┴──────────────┴──────────────┴──────────────┴─────────────┘ │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         Provider Layer（模型层）                              │
│                                                                              │
│  ┌──────────────┬──────────────┬──────────────┬──────────────┬────────────┐  │
│  │  AgileMind   │  DeepSeek    │   OpenAI     │  Anthropic   │   Local    │  │
│  │  (default)   │  (fallback)  │  (fallback)  │  (fallback)  │   GGUF     │  │
│  └──────────────┴──────────────┴──────────────┴──────────────┴────────────┘  │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│                         API Layer（商业化层）                                 │
│                                                                              │
│  ┌──────────────┬──────────────┬──────────────┬──────────────┬────────────┐  │
│  │    Auth      │   API Key    │   Billing    │    Usage     │   Health   │  │
│  │   (JWT)      │   Mgmt       │   (订阅计费)  │   Pricing    │  (Prometheus)│  │
│  └──────────────┴──────────────┴──────────────┴──────────────┴────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 层职责

| 层 | 职责 | v1.2 变更 |
|----|------|-----------|
| **Interface** | CLI, TUI (JSON-RPC), Gateway (16平台含 Feishu WS), MCP, Cron, REST API | Feishu 新增 WebSocket 长连接模式 |
| **Core Engine** | Pipeline→Router→Jury→Workflow→FactCheck→Consensus + Session/Skill/Memory/Tool | **新增** Pipeline / Workflow / Report / Monitoring / VoiceEngine |
| **Provider** | 多模型抽象 — AgileMind/DeepSeek/OpenAI/Anthropic/本地 GGUF | 无变更 |
| **API** | 商业化 — 认证/计费/Key管理/用量统计/健康检查 | **新增** Prometheus `/metrics` 端点 |

---

## 2. 数据流：完整请求管线

```
                              ┌──────────────────────────┐
                              │       User Input          │
                              │  (CLI / TUI / Gateway /   │
                              │   MCP / API / Cron)       │
                              └────────────┬─────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │     Session 检查          │
                              │  · 创建/恢复会话 (SQLite) │
                              │  · 轮数上限 (150 轮)      │
                              └────────────┬─────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │     Router 路由分类       │
                              │  · Qwen2-1.5B GGUF 本地  │
                              │  · 行业: finance/medical  │
                              │    /legal/edu/general     │
                              │  · 难度: simple/medium/   │
                              │    complex + 置信度       │
                              └────────────┬─────────────┘
                                           │
                         ┌─────────────────┴─────────────────┐
                         │                                   │
                         ▼                                   ▼
              ┌──────────────────┐              ┌──────────────────────────┐
              │  Simple 任务      │              │  Complex 任务             │
              │  (difficulty < 5) │              │  (difficulty >= 5)        │
              └────────┬─────────┘              └────────────┬─────────────┘
                       │                                     │
                       ▼                                     ▼
              ┌──────────────────┐              ┌──────────────────────────┐
              │   Dispatcher     │              │   Jury 评审团             │
              │   单模型直接响应   │              │   3-6 个模型并行辩论       │
              │   行业→模型映射    │              │   · 3 轮交叉审议           │
              │   (AgileMind/    │              │   · 加权投票裁决           │
              │    DeepSeek/     │              │   · 少数派意见记录          │
              │    OpenAI/...)   │              └────────────┬─────────────┘
              └────────┬─────────┘                            │
                       │                                     ▼
                       │                          ┌──────────────────────────┐
                       │                          │   RiskGate 风险门禁       │
                       │                          │   · risk_score 0-100     │
                       │                          │   · LOW (<25): 自动执行   │
                       │                          │   · MEDIUM (25-50): 警告  │
                       │                          │   · HIGH (50-75): 需审批  │
                       │                          │   · CRITICAL (>75): 阻塞  │
                       │                          └────────────┬─────────────┘
                       │                                       │
                       │                                       ▼
                       │                          ┌──────────────────────────┐
                       │                          │   Report 生成             │
                       │                          │   · 执行摘要               │
                       │                          │   · 问题与方案             │
                       │                          │   · 辩论过程 (3轮)         │
                       │                          │   · 最终裁决 + 投票分布     │
                       │                          │   · 风险评估               │
                       │                          │   · 少数派意见             │
                       │                          │   · 建议                   │
                       │                          └────────────┬─────────────┘
                       │                                       │
                       └───────────────┬───────────────────────┘
                                       │
                                       ▼
                              ┌──────────────────────────┐
                              │   Session 持久化          │
                              │   · 存储 user/assistant  │
                              │   · 更新 HallMetrics     │
                              └────────────┬─────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────┐
                              │   Response → 用户          │
                              │   · CLI: stdout          │
                              │   · TUI: JSON-RPC        │
                              │   · Gateway: 平台消息      │
                              │   · Voice: 流式 TTS ★     │
                              └──────────────────────────┘
```

### 关键路径说明

| 路径 | 触发条件 | 延迟 | 模型调用次数 |
|------|----------|------|-------------|
| Simple 直达 | difficulty < 5.0 | ~2-5s | 1 次 |
| Jury 评审 | difficulty >= 5.0 | ~15-45s | 3-6 模型 × 3 轮 |
| Workflow 编排 | 显式 `dragon workflow run` | 取决于 YAML 定义 | 可变 (条件跳转) |
| Voice 语音 | 语音模式开启 | 首句 ~1s (流式边收边播) | 1+ 次 LLM + TTS |

---

## 3. v1.2 新增模块详解

### 3.1 Workflow 引擎 (`dragon/workflow/`)

**文件**: `dragon/workflow/__init__.py` (176 LOC) + `engine.py` (324 LOC) + `steps.py` (575 LOC)

**核心能力**: YAML 驱动的工作流编排引擎，支持 5 种标准步骤类型。

#### 架构

```
┌─────────────────────────────────────────────────────┐
│                Workflow Engine                       │
│                                                     │
│  WorkflowDefinition.from_yaml(path)                 │
│       │                                             │
│       ▼                                             │
│  WorkflowEngine.run(definition, context)             │
│       │                                             │
│       ├─→ Step: llm_call      → dispatcher.dispatch │
│       ├─→ Step: tool_call      → tool_registry.call │
│       ├─→ Step: conditional    → 表达式求值 → 跳转   │
│       ├─→ Step: loop           → 数组迭代 → 子步骤   │
│       └─→ Step: sub_workflow   → 递归执行子工作流     │
│                                                     │
│  Context 模板: {step_id.field.subfield}              │
│    · {query}              → context["query"]        │
│    · {step_1.output}      → StepResult.output       │
│    · {step_1.success}     → StepResult.success      │
│    · {plan.text}          → dict["plan"]["text"]    │
└─────────────────────────────────────────────────────┘
```

#### 五种步骤类型

| 类型 | 说明 | 配置示例 |
|------|------|----------|
| `llm_call` | 调用 LLM（通过 dispatcher） | `prompt: "分析: {query}"`, `temperature: 0.3` |
| `tool_call` | 调用工具（通过 tool_registry） | `tool: web_search`, `input: "{query}"` |
| `conditional` | 条件分支，表达式求值 → 跳转目标 | `expression: "{plan.success} == True"`, `then: deep`, `else: quick` |
| `loop` | 数组迭代，对每个元素执行子步骤 | `array: "{search.results}"`, `item_key: item` |
| `sub_workflow` | 嵌套子工作流（递归执行） | `workflow: report_generation`, `input: {data: "{batch.output}"}` |

#### YAML 工作流示例 (`workflows/research.yaml`)

```yaml
name: 研究工作流
steps:
  - id: plan              # 制定研究计划
    type: llm_call
    config:
      prompt: "研究问题：{query}\n请制定研究计划..."
      temperature: 0.3

  - id: search            # 执行搜索
    type: tool_call
    config: { tool: web_search, input: "{query}" }

  - id: depth_check       # 条件分支 ★
    type: conditional
    config:
      expression: "{plan.success} == True"
      then: deep_analysis
      else: quick_summary

  - id: deep_analysis     # 深度分析
    type: llm_call
    config:
      prompt: "基于搜索资料：{search.output}\n深度分析..."

  - id: quick_summary     # 快速摘要
    type: llm_call
    config:
      prompt: "搜索资料：{search.output}\n3-5 要点概括..."

  - id: final             # 生成最终报告
    type: llm_call
    config:
      prompt: "整合 {deep_analysis.output} 和 {quick_summary.output}\n生成报告..."
```

#### 数据模型

```
WorkflowDefinition
├── name: str
├── description: str
└── steps: List[StepDefinition]
    ├── id: str
    ├── type: StepType (llm_call|tool_call|conditional|loop|sub_workflow)
    └── config: dict

WorkflowResult
├── status: WorkflowState (pending|running|completed|failed)
├── outputs: Dict[str, Any]      # 所有步骤的 {step_id: StepResult}
├── final_output: Any            # 最后一步的输出
└── steps: List[StepResult]
    ├── step_id, step_type
    ├── output, success, skipped
    └── error, elapsed_ms
```

---

### 3.2 流式语音引擎 (`dragon/voice_engine.py`)

**文件**: `dragon/voice_engine.py` (311 LOC)

**核心能力**: 句子级流式 TTS，使用 Microsoft Edge TTS (edge-tts) 提供免费高质量神经语音合成。

#### 架构

```
┌──────────────────────────────────────────────────────────────┐
│                     VoiceEngine                               │
│                                                              │
│  LLM 流式输出 (文本块)                                        │
│       │                                                      │
│       ▼                                                      │
│  buffer 缓冲区                                               │
│       │                                                      │
│       ▼                                                      │
│  句子边界检测                                                 │
│  · 硬边界: 。！？.!?\n (正则匹配)                             │
│  · 软边界: ，,;；:： (300字符溢出时触发)                      │
│       │                                                      │
│       ▼                                                      │
│  完整句子 → sentence_queue → asyncio.Queue                   │
│       │                                                      │
│       ▼ (后台异步任务)                                        │
│  edge-tts 合成 (zh-CN-XiaoxiaoNeural)                        │
│       │                                                      │
│       ▼                                                      │
│  (句子文本, MP3 bytes) → audio_queue → 播放器                │
└──────────────────────────────────────────────────────────────┘
```

#### 两种使用模式

| 模式 | 方法 | 适用场景 |
|------|------|----------|
| 后台队列模式 | `consume(text_chunk)` + `next_audio()` | 长时间流式输入，边收边播 |
| 异步生成器模式 | `stream(text / async_iterable)` → AsyncGenerator | 一次性或流式文本，逐句 yield |

#### 关键特性

- **句子边界检测**: 正则表达式驱动，支持中英文标点
  - 硬边界: `。！？.!?\n` — 检测到即合成
  - 缓冲区上限 300 字符 — 超限时在软边界 `，,;；:：` 处强制断句
- **默认语音**: `zh-CN-XiaoxiaoNeural` (Microsoft Edge TTS)
- **零外部 API 依赖**: edge-tts 免费使用，无需 Azure Key
- **异步设计**: TTS 合成在后台任务中运行，不阻塞 LLM 流式输出

#### 设计决策: 流式语音句子边界

```
为什么是句子级而非字符级/段落级？

1. 字符级: TTS 开销过大，每次 1 字符合成 1 次 → 延迟高、不自然
2. 段落级: 等待全文完成再合成 → 首音延迟高，失去"流式感"
3. 句子级 ★: 边收边断句，首句 1-2s 即开始播放，后续句子无感衔接

句子边界定义：
- 标准边界: 。！？.!?\n — 自然语言句子终止符
- 防溢出机制: 300 字符内无硬边界 → 在逗号/分号处强制断开
- 确保缓冲区始终 ≤ 300 字符，内存可控
```

---

### 3.3 Prometheus 监控 (`dragon/monitoring.py`)

**文件**: `dragon/monitoring.py` (147 LOC)

**核心能力**: 基于 `prometheus_client` 的 9 个标准指标，通过 FastAPI `/metrics` 端点暴露。

#### 9 个指标定义

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `dragon_requests_total` | Counter | `industry`, `difficulty` | 按行业和难度统计请求量 |
| `dragon_request_latency_seconds` | Histogram | — | 请求处理延迟分布 (0.1s ~ 120s) |
| `dragon_token_consumption_total` | Counter | `model` | 按模型统计 Token 消耗 |
| `dragon_tool_calls_total` | Counter | `tool_name` | 工具调用次数 |
| `dragon_sessions_active` | Gauge | — | 当前活跃会话数 |
| `dragon_errors_total` | Counter | `error_type` | 按错误类型统计 |
| `dragon_uptime_seconds` | Gauge | — | 服务运行时间 |
| `dragon_memory_rss_bytes` | Gauge | — | 进程 RSS 内存 |
| `dragon_cpu_percent` | Gauge | — | 进程 CPU 使用率 |

#### 集成方式

```python
# 在 Pipeline.process() 中自动埋点
from dragon.monitoring import (
    dragon_requests_total, dragon_request_latency_seconds,
    dragon_token_consumption_total, dragon_tool_calls_total
)

dragon_requests_total.labels(
    industry=route.industry, difficulty=route.difficulty
).inc()

dragon_request_latency_seconds.observe(elapsed_seconds)
```

#### `/metrics` 端点

```
# 示例输出
dragon_requests_total{industry="finance",difficulty="complex"} 42
dragon_request_latency_seconds_bucket{le="5.0"} 128
dragon_token_consumption_total{model="deepseek-v4"} 1048576
dragon_tool_calls_total{tool_name="web_search"} 356
dragon_sessions_active 12.0
dragon_uptime_seconds 86400.0
dragon_memory_rss_bytes 524288000.0
dragon_cpu_percent 2.5
```

---

### 3.4 Pipeline 调度管线 (`dragon/pipeline.py`)

**文件**: `dragon/pipeline.py` (591 LOC)

**核心能力**: 请求生命周期的中心编排器，统一 Router → Dispatch/Jury → RiskGate → Report 全流程。

#### 组件关系

```
DragonPipeline
├── DragonRouter (本地 Qwen2-1.5B)      → 意图识别 & 复杂度评估
├── DragonDispatcher (行业→模型映射)     → 简单任务单模型直达
├── JuryDebate (多模型辩论引擎)          → 复杂任务多模型评审
├── SessionStore (SQLite)               → 会话状态 & 轮数控制
├── InterruptManager                    → 中断控制
└── RiskGate (内置)                     → 风险评分 & 门禁决策
```

#### 风险等级分类

| RiskLevel | risk_score | PipelineAction | 说明 |
|-----------|-----------|----------------|------|
| LOW | < 25 | AUTO_EXECUTE | 自动执行 |
| MEDIUM | 25-50 | AUTO_EXECUTE | 自动执行 + 警告标记 |
| HIGH | 50-75 | REQUIRE_APPROVAL | 需用户审批 |
| CRITICAL | > 75 | REQUIRE_APPROVAL | 阻塞，需显式放行 |

#### 会话轮数控制

- 默认上限: **150 轮**
- 超限后: `SESSION_PAUSED` → 返回暂停报告，不再处理新消息
- 设计意图: 防止无限对话消耗 Token，提示用户开启新会话

---

### 3.5 Report 报告生成 (`dragon/report.py`)

**文件**: `dragon/report.py` (474 LOC)

**核心能力**: 从 `JuryVerdict` 生成结构化 Markdown 报告，包含 7 个标准章节。

```
报告结构:
1. 执行摘要 (风险值 + 决策)
2. 问题与方案
3. 辩论过程 (3 轮详录)
4. 最终裁决 (投票分布)
5. 风险评估
6. 少数派意见
7. 建议
```

---

### 3.6 Feishu 适配器 (`dragon/feishu.py`)

**文件**: `dragon/feishu.py` (814 LOC) — WebSocket 长连接模式

```
┌─────────────┐   WSS outbound   ┌──────────────────┐
│  Feishu     │ ◄────────────── │  Dragon Agent    │
│  Server     │ ────────►       │  (lark_oapi.ws)  │
└─────────────┘   events        └──────┬───────────┘
                                       │ dispatch events
                                ┌──────▼───────────┐
                                │  Message Router   │
                                │  → LLM response   │
                                │  → Feishu API     │
                                └──────────────────┘
```

支持双模式: **WebSocket** (推荐，无需公网 IP) / **Webhook** (HTTP 回调)

---

## 4. 集成点

```
                      ┌──────────────────────────────────────┐
                      │          Monitoring (Prometheus)     │
                      │  · 所有模块埋点                      │
                      │  · /metrics 端点                     │
                      └──────┬───────────────────────────────┘
                             │
    ┌────────────────────────┼────────────────────────────┐
    │                        │                            │
    ▼                        ▼                            ▼
┌────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│ Router │───▶│       Pipeline        │───▶│    Workflow Engine   │
│        │    │                      │    │                      │
│ 行业   │    │  Simple → Dispatcher  │    │  YAML 步骤编排        │
│ 难度   │    │  Complex → Jury       │    │  5 种步骤类型         │
│ 分类   │    │  → RiskGate → Report  │    │  {template} 上下文    │
└────────┘    └──────────┬───────────┘    └──────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌──────────────┐
   │ Dispatcher│   │  Jury    │   │ VoiceEngine  │
   │ 单模型    │   │ 3-6 模型 │   │ 流式 TTS     │
   │ 行业→模型 │   │ 辩论评审 │   │ 句子边界检测  │
   └──────────┘   └──────────┘   └──────────────┘
         │               │
         └───────┬───────┘
                 ▼
         ┌──────────────┐
         │   Report     │
         │  MD 报告     │
         │  7 章节      │
         └──────────────┘
```

### 模块间数据流

| 源模块 | 目标模块 | 数据类型 | 协议 |
|--------|----------|----------|------|
| Gateway/CLI | Pipeline | `user_message: str` | 函数调用 |
| Pipeline | Router | `query: str` | async method |
| Pipeline | Dispatcher (Simple) | `industry + messages` | async method |
| Pipeline | Jury (Complex) | `query + proposals` | async method |
| Pipeline | Report | `JuryVerdict` | 函数调用 |
| Workflow | Dispatcher/ToolRegistry | 步骤配置 | template render |
| VoiceEngine | LLM 流式输出 | 文本块 `AsyncIterable[str]` | consume/stream |
| 所有模块 | Monitoring | Counter/Gauge/Histogram | prometheus_client |

---

## 5. 关键设计决策

### 5.1 评分解耦 (Router ↔ Pipeline)

```
传统做法: Router 直接决定"走哪条路" → 耦合度高，难以调整
Dragon 做法: Router 只输出 (industry, difficulty, difficulty_score)
              Pipeline 根据 score + complexity_threshold 独立决策

好处:
· Router 专注分类，不感知下游策略
· Pipeline 可独立调整阈值 (DEFAULT_COMPLEXITY_THRESHOLD=5.0)
· 未来可引入强化学习动态调整阈值
· 新增中间难度路径 (medium) 时无需改 Router
```

**评分模型**: Router → `RouteResult(industry, difficulty, difficulty_score 0-10, confidence 0-1)` → Pipeline 根据 `difficulty_score >= 5.0` 判定 simple/complex。

### 5.2 流式语音句子边界

```
问题: LLM 输出是字符级流式，TTS 需要完整句子才能自然合成
挑战: 何时"切句"？太早 → 句子不完整，太晚 → 延迟高

解决方案: 三级边界检测策略

  1. 硬边界（优先）: 。！？.!?\n
     → 检测到立即切句合成，延迟 ~1-2s
  2. 软边界（防溢出）: ，,;；:：
     → 缓冲区累积 300 字符仍无硬边界时，在逗号/分号处强制断开
  3. 硬截断（兜底）: 300 字符无任何标点 → 按字符数强制断开

缓冲区上限 300 字符的设计依据:
· 中文正常句子平均 20-50 字
· 300 字足够容纳最长合理句子
· 内存占用可控 (~600 bytes per buffer)
· 极端长句（法律/学术文本）不会导致 OOM
```

### 5.3 Workflow 条件跳转

```
核心机制: conditional 步骤类型 + 表达式求值 + 跳转表

YAML 定义:
  - id: depth_check
    type: conditional
    config:
      expression: "{plan.success} == True"
      then: deep_analysis     # 条件为真 → 跳转到此步骤
      else: quick_summary     # 条件为假 → 跳转到此步骤

执行流程:
  1. render_template("{plan.success}") → context["plan"].success → True/False
  2. evaluate_expression("True == True") → True
  3. 查找 then/else 指向的 step id
  4. 跳过中间步骤，直接执行目标步骤

支持的操作符:
  · ==, !=, >, <, >=, <=
  · and, or, not
  · in, not in
  · 字面量: True, False, None, 数字, 字符串

与 Pipeline 的 Simple/Complex 分支的关系:
  · Pipeline 分支: 系统级判断（用 Router 的 difficulty_score）
  · Workflow 分支: 用户级判断（在 YAML 中显式定义）
  · 两者正交：Pipeline 决定用 Dispatcher 还是 Jury，
    Workflow 编排具体的 LLM/Tool 调用序列
```

### 5.4 为什么 Qwen2-1.5B 本地模型 + 云端 API？

- **隐私**: 路由在本地完成，用户问题不出本机
- **速度**: 1.5B 分类 <300ms，不影响体验
- **成本**: 避免每次请求都送完整上下文给大模型
- **兜底**: AgileMind 不可用时自动 fallback

### 5.5 为什么多模型陪审团？

- **诚实**: 单一模型幻觉率高（15-25%），3-6 模型交叉验证可降至 <5%
- **来源**: 每个结论可追溯是哪个模型的观点
- **差异化**: 市场上尚无竞品

### 5.6 为什么 ChromaDB 而非 FTS5？

- **语义检索**: 中文场景语义匹配优于关键词
- **嵌入本地**: bge-small-zh 仅 100MB，无需外部 API
- **易用**: pip install 即用

### 5.7 为什么 Prometheus 而非自定义监控？

- **标准**: Prometheus 是云原生监控事实标准
- **生态**: Grafana 可视化、AlertManager 告警开箱即用
- **低成本**: `prometheus_client` 库 <50KB，零外部依赖
- **埋点**: Counter/Histogram/Gauge 覆盖全部核心指标

---

## 6. 目录结构 (v1.2 更新)

```
dragon-agent/
├── dragon/                        # Python 包
│   ├── main.py                    # 主入口
│   ├── cli.py                     # CLI
│   ├── config.py                  # 配置管理
│   ├── session.py                 # 会话管理 [SQLite]
│   │
│   ├── pipeline.py          ★     # Pipeline 调度管线 (591 LOC)
│   ├── report.py            ★     # Markdown 报告生成 (474 LOC)
│   ├── monitoring.py        ★     # Prometheus 监控 (147 LOC)
│   ├── voice_engine.py      ★     # 流式语音 TTS (311 LOC)
│   │
│   ├── workflow/            ★     # Workflow 引擎
│   │   ├── __init__.py            # 数据模型 + 导出 (175 LOC)
│   │   ├── engine.py              # 执行引擎 (324 LOC)
│   │   └── steps.py               # 5 种步骤执行器 (575 LOC)
│   │
│   ├── router/                    # 路由模型 (Qwen 1.5B GGUF)
│   │   └── __init__.py            # 行业分类 + RemoteRouter
│   │
│   ├── jury/                      # 陪审辩论引擎
│   │   └── __init__.py            # 3-6 模型 × 3 轮辩论
│   │
│   ├── dispatch/                  # 模型调度
│   │   └── __init__.py            # 行业 → 模型派发
│   │
│   ├── factcheck.py               # 事实核查
│   ├── consensus.py               # 共识 + 来源标注
│   ├── hallmetrics.py             # 幻觉率追踪
│   ├── confidence.py              # 置信度校准
│   │
│   ├── guard/                     # 输出安全 (PII/违规)
│   ├── compressor/                # 上下文压缩
│   ├── skill/                     # Skill 系统
│   ├── tool/                      # 工具系统 (20+ 内置工具)
│   ├── memory/                    # 向量记忆 [ChromaDB]
│   ├── provider/                  # 模型抽象
│   ├── plugin/                    # 插件系统
│   │
│   ├── gateway/                   # 多平台网关
│   │   ├── server.py              # FastAPI 主服务
│   │   ├── base.py                # 平台适配基类
│   │   ├── feishu.py        ★     # Feishu WS 适配器 (814 LOC)
│   │   └── (Telegram, WeChat, ... 共 16 平台)
│   │
│   ├── mcp/                       # MCP Server
│   ├── api/                       # REST API (FastAPI)
│   │   ├── app.py
│   │   ├── auth.py                # JWT/OAuth
│   │   ├── billing.py             # 计费
│   │   └── apikeys.py             # Key 管理
│   │
│   └── tui/                       # TUI 后端 (Node.js + Ink)
│
├── workflows/               ★     # YAML 工作流定义
│   ├── research.yaml              # 研究工作流 (114 行)
│   └── code_review.yaml           # 代码审查工作流
│
├── docs/                          # 文档
│   ├── REQUIREMENTS.md
│   └── DESIGN.md                  # 本文档
│
└── tests/                         # 测试
    ├── test_workflow.py
    ├── test_workflow_cli.py
    ├── test_monitoring.py
    └── test_gateway.py
```

★ = v1.2 新增/重大更新

---

## 7. 部署模式

### 模式 A: CLI (开发/调试)
```bash
pip install dragon-agent
dragon chat "什么是量子计算？"
dragon workflow run workflows/research.yaml --query "AI 发展趋势"
```

### 模式 B: Gateway (飞书/多平台机器人)
```bash
dragon gateway --feishu --port 8080
# Feishu WebSocket 模式自动连接，无需公网 IP
```

### 模式 C: API Server (商业化)
```bash
dragon serve --host 0.0.0.0 --port 8000
# /metrics → Prometheus 指标
# /health  → 健康检查
# /chat    → 对话 API
```

### 模式 D: 语音模式 (流式 TTS)
```bash
dragon chat --voice "今天天气怎么样？"
# LLM 流式输出 → VoiceEngine 句子边界检测 → edge-tts 逐句播放
```

### 模式 E: USB 便携版
```
dragon-agent-usb/
├── dragon-agent.pyz  (zipapp)
├── models/           (可选)
└── config.yaml
```

---

## 8. Hermes 对齐对照表 (v1.2 更新)

| Hermes 概念 | Dragon 实现 | 文件 | 状态 |
|------------|-----------|------|------|
| Agent Loop | `main.py::DragonAgent.run()` | main.py | ✓ |
| Provider | `provider/__init__.py::ProviderRegistry` | provider/ | ✓ |
| Skill System | `skill/engine.py::SkillEngine` | skill/ | ✓ |
| Tool System | `tool/registry.py::ToolRegistry` | tool/ | ✓ |
| Memory | `memory/__init__.py::DragonMemory` | memory/ | ✓ |
| Session | `session.py::SessionStore` | session.py | ✓ |
| Gateway | `gateway/server.py::GatewayServer` | gateway/ | ✓ |
| MCP | `mcp/server.py::MCPServer` | mcp/ | ✓ |
| Cron | `cron.py::CronScheduler` | cron.py | ✓ |
| Subagent | `subagent.py::SubAgentManager` | subagent.py | ✓ |
| Config | `config.py::ConfigManager` | config.py | ✓ |
| CLI | `cli.py::main()` | cli.py | ✓ |
| TUI | `tui/server.py::TUIServer` | tui/ | ✓ |
| **Workflow** ★ | `workflow/engine.py::WorkflowEngine` | workflow/ | **新增** |
| **Pipeline** ★ | `pipeline.py::DragonPipeline` | pipeline.py | **新增** |
| **Voice/TTS** ★ | `voice_engine.py::VoiceEngine` | voice_engine.py | **新增** |
| **Monitoring** ★ | `monitoring.py` (Prometheus) | monitoring.py | **新增** |
| **Report** ★ | `report.py::generate_verdict_report` | report.py | **新增** |
| **Feishu WS** ★ | `feishu.py::FeishuAdapter` (WebSocket) | feishu.py | **更新** |
