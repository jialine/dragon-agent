# Dragon Agent — 待完成事项

> 更新: 2026-06-27 | 代码量: 50,696 LOC | 模块: 111 文件 | 测试: 28 文件

---

## ✅ 已完成 (vs 旧版清单)

### P0 核心引擎 — 全部完成 ✅

| 模块 | LOC | 状态 |
|------|-----|------|
| Jury/Debate 引擎 | 1,421 | ✅ |
| Fact Checker | 515 | ✅ |
| Consensus Builder | 397 | ✅ |
| Hallucination Metrics | 436 | ✅ |
| Source Attribution | (内嵌 Consensus) | ✅ |
| Confidence Calibration | 479 | ✅ |
| Consult Mode | 964 | ✅ |

### P1 产品化 — 全部完成 ✅

| 模块 | LOC | 状态 |
|------|-----|------|
| Auth (JWT/OAuth) | 618 | ✅ |
| Billing/Subscription | 606 | ✅ |
| API Key Management | 352 | ✅ |
| Usage Pricing | 639 | ✅ |

### 工具对齐 Hermes — 全部完成 ✅

20/20 工具已实现，涵盖 Vision/TTS/Browser/Documents/Email/...

### Gateway 实时交互增强 — 全部完成 ✅ (2026-06-27)

| 模块 | 说明 | 状态 |
|------|------|------|
| Multi-turn Agent Loop | 90轮工具调用循环，steer + queue + progress | ✅ |
| Steer 中断注入 | 忙时消息排队，下轮以 `[新指令]` 注入历史 | ✅ |
| 消息队列 | 主任务完成后处理最多3条排队消息 | ✅ |
| Progress Reporter | 每180秒 `⏳ 执行中...` 状态推送 | ✅ |
| Processing Reactions | Hermes对齐 ✍️ Typing / ❌ CrossMark | ✅ |
| Critical/Alert Push | `[CRITICAL]`/`[ALERT]`/`!!!` 即时推送 | ✅ |
| Edit Past Messages | `[EDIT:target]` 原地编辑已发消息 | ✅ |


### Gateway — 全部完成 ✅

16 平台适配器：飞书/Telegram/Discord/微信/企业微信/钉钉/Slack/...

---

## ⬜ 真实剩余

### P1: 工程加固

| 项目 | 状态 | 估价 |
|------|------|------|
| **Monitoring** | ⚠️ 18 LOC stub，需 Prometheus metrics | 2 天 |
| **Docker/Docker Compose** | ❌ 未开始 | 2 天 |
| **CI/CD (Gitee Actions)** | ❌ 未开始 | 1 天 |
| **测试修复** | ⚠️ 28 测试文件，2 个 import 错误 | 1 天 |
| **生产压测 (100并发)** | ❌ 未开始 | 2 天 |
| **API 限流增强** | ⚠️ rate_limiter 已完成，需接入 API | 1 天 |

### P2: 体验提升

| 项目 | 状态 | 估价 |
|------|------|------|
| **Web UI** | ⚠️ 70 LOC placeholder | 1-2 周 |
| **TUI 前端** | ⚠️ Python 后端完成，Node.js 前端部分完成 | 1 周 |
| **Voice Mode 流式** | ⚠️ voice_engine.py 完成，前端未接 | 2 天 |
| **行业 SKILL.md** | ❌ 金融/医疗/法律/教育知识模板 | 1-2 周 |
| **U盘版打包** | ⚠️ 基础完成，需测试 | 1 天 |

### P3: 可选

| 项目 | 状态 |
|------|------|
| Computer Use (CUA) | ❌ 未实现，非 To-C |
| X/Twitter 社媒 | ❌ 非中国场景 |
| 智能家居 | ❌ 非核心 |
| 游戏服务器 | ❌ 非核心 |

---

## 📊 完成度

```
模块:     51/53 已实现 (96%)
工具:     20/20 Hermes 对齐 (100%)
P0 核心:   7/7  已完成 (100%)
P1 产品化: 4/4  已完成 (100%)
Gateway:  16/16 已完成 (100%)
测试:     28 文件, 需修复 2 个 import 错误
文档:     REQUIREMENTS.md + DESIGN.md 已更新
```

**优先级**: 修复测试 → 加 Monitoring → Docker → CI/CD
