# Dragon Agent — 开发路线图 v2

> 审计日期: 2026-06-27 | 代码基: 730cc91 | 62模块审计完成

## 审计结论

```
✅ DONE: 60模块 (96.8%)
⚠️ STUB:  2模块 (3.2%)
❌ MISSING: 工作流引擎
```

| 层级 | 模块数 | 状态 |
|------|:--:|------|
| Router + Provider + Tier | 2 | ✅ |
| Session + Memory + Subagent | 3 | ✅ |
| 工具 (Tools) | 22 | ✅ 21 / ⚠️ spotify |
| 网关 (Gateway) | 18 | ✅ |
| MCP Server | 3 | ✅ |
| API (auth/billing/db) | 8 | ✅ |
| Pipeline + Report + Cron | 5 | ✅ |
| 可观测性 | 2 | ✅ 1 / ⚠️ monitoring |

---

## P0 — 核心差距 (必须实现)

### 1. 🔴 Workflow 引擎 (3天)

**当前**: `workflows/` 目录为空。README和DESIGN.md宣称的工作流引擎不存在。

**实现**:
- [ ] `dragon/workflow/engine.py` — YAML工作流解析 + 步骤执行器
- [ ] `dragon/workflow/steps/` — 标准步骤: LLM调用、工具调用、条件分支、循环、子工作流
- [ ] `workflows/` — 至少3个示例工作流:
  - `research.yaml` — 研究+写报告
  - `code_review.yaml` — 代码审查
  - `daily_briefing.yaml` — 每日简报
- [ ] 与 Pipeline 集成: 复杂任务路由到工作流
- [ ] 测试: `tests/test_workflow.py`

**验收**: 能通过 `dragon run workflow research.yaml --topic "XXX"` 执行完整工作流

---

### 2. 🔴 Voice 流式模式 (2天)

**当前**: `voice_engine.py` (180行) 支持 edge-tts 全量合成，但不支持流式。

**实现**:
- [ ] `VoiceEngine.stream()` — 句子级流式合成
- [ ] 句子边界检测 (。！？\n 作为分割点)
- [ ] 与 Gateway 集成: 飞书语音消息、实时播报
- [ ] 与 Pipeline 集成: 流式输出同时合成语音
- [ ] 测试: `tests/test_voice.py` (已有 `tests/test_voice_ap`)

**验收**: Agent 回复时边生成文字边播放语音，首句延迟 <2s

---

## P1 — 功能补全 (1周)

### 3. 🟡 Spotify 工具完善
- [ ] 补全 `tool_spotify_*` 函数（当前0类/函数检测）
- [ ] 实现 search + now_playing + play 功能
- [ ] OAuth 流程: 授权码 + refresh token

### 4. 🟡 Monitoring 生产化
- [ ] 添加更多 Prometheus 指标（请求延迟、工具调用数、token消耗）
- [ ] Grafana dashboard JSON
- [ ] 告警规则

### 5. 🟡 模型修复
- [ ] 重下 `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf` (当前0字节)
- [ ] 验证本地推理可用: `dragon run --local "hello"`
- [ ] 切换到 `Qwen3-1.7B` 作为默认（1.1G已存在，比1.5B更新）

---

## P2 — 质量加固 (持续)

### 6. 🟢 测试覆盖率
- 当前38个测试文件，需验证通过率
- 目标: 核心模块 >80% 覆盖率

### 7. 🟢 Docker 部署
- 验证 Dockerfile 构建 + 运行
- 添加 docker-compose 完整配置（含nginx反代）

### 8. 🟢 文档
- API 文档自动生成
- 部署指南

---

## 优先级排序

| 序号 | 任务 | 工期 | 状态 |
|:--:|------|:--:|:--:|
| 1 | Workflow 引擎 | 3天 | ⬜ |
| 2 | Voice 流式 | 2天 | ⬜ |
| 3 | Spotify | 1天 | ⬜ |
| 4 | 模型切换 Qwen3-1.7B | 0.5天 | ⬜ |
| 5 | Monitoring | 1天 | ⬜ |
| 6 | 测试覆盖率 | 2天 | ⬜ |
| 7 | Docker 部署 | 1天 | ⬜ |

> **总工期: ~10天 | 当前可发布: ✅ (60/62模块就绪)**

---

## 当前技术栈

| 组件 | 实现 |
|------|------|
| 本地模型 | Qwen3-1.7B/4B/8B (GGUF CPU推理) |
| 云模型 | AgileMind API + 多Provider |
| 网关 | 飞书(WS/Webhook) + Telegram + 微信 + Slack... |
| 路由 | DragonRouter(本地) + RemoteRouter(云端GPU) |
| 管线 | Simple→Dispatcher / Complex→Jury(3-6模型辩论)→RiskGate |
| 技能 | 100+ skill JSON + 自进化引擎 |
| MCP | 自建MCP Server，暴露技能+辩论 |
| 记忆 | 知识图谱 (实体-关系) |
| 安全 | 红队检测 + 内容管线 + 文件安全 |
