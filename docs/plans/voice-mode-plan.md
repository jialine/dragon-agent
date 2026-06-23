# Dragon Agent — Voice Mode 实现计划

> 创建: 2026-06-25 | 目标: 2 天 | 优先级: P0

## 目标

实现实时流式语音输出：Agent 回复时，边生成文字边合成语音，用户听到的延迟 = TTFT + 一句话的时间。

## 与现有 TTS 工具的差异

| | 现有 `tool_tts` | Voice Mode |
|---|---|---|
| 触发方式 | Agent 主动调用工具 | 自动，每个回复都合成 |
| 输出 | 完整 MP3 文件 | 流式音频块 |
| 延迟 | 等全部文字生成完才开始 | 第一句话说完就开始播放 |
| 适用 | 用户说"读给我听" | 飞书语音消息、实时播报 |

## 架构

```
LLM 流式输出 (SSE)
    │
    ▼
VoiceEngine.consume(chunk)
    │  累积文字，检测句子边界
    │
    ▼ (每完成一句话)
edge-tts Communicate.stream()
    │  生成 MP3 chunk
    │
    ▼
客户端播放队列
    │  play_next() → 播完继续下一段
    ▼
无缝连续播放
```

## 任务拆分

### Task 1: VoiceEngine 核心类
**文件:** `dragon/voice_engine.py`
**内容:**
- `VoiceEngine` 类：管理 TTS 流式合成
- `consume(text_chunk: str)` — 接收 LLM 输出片段，累积到缓冲区
- `_detect_boundary(buffer: str) → str | None` — 检测句子边界（。！？\n），返回完整句子
- `synthesize(sentence: str) → bytes` — 调用 edge-tts 生成音频
- `flush()` — 输出缓冲区剩余文字
- `stop()` — 中断合成

### Task 2: edge-tts 流式集成
**文件:** `dragon/voice_engine.py` (同文件)
**内容:**
- 使用 `edge_tts.Communicate(text, voice).stream()` 获取音频块迭代器
- 每个 chunk 是 `(type, data)` 元组，type="audio" 时 data 是 MP3 帧
- 句子队列：`asyncio.Queue` 攒句子，后台协程逐句合成
- 错误处理：edge-tts 失败时静默跳过（不影响文字输出）

### Task 3: API 端点
**文件:** `dragon/main.py` (修改)
**内容:**
- `POST /v1/voice/chat` — 流式语音聊天
  - 输入：`{messages: [...], voice: "zh-CN-XiaoxiaoNeural"}`
  - 输出：`multipart/x-mixed-replace` 或 WebSocket
    - 每个帧：`{type: "text", content: "..."}` 或 `{type: "audio", data: "<base64>"}`
- 或复用 `/v1/chat/stream`，在 SSE 事件中加 `audio_base64` 字段

### Task 4: 飞书语音消息集成
**文件:** `dragon/gateway/feishu.py` (修改)
**内容:**
- 飞书消息类型支持 `voice`（需要上传 AMR/MP3 到飞书）
- 收到文字消息 → LLM 回复 → VoiceEngine 合成 → 上传语音 → 发送
- 命令：`/voice on` / `/voice off` 切换语音模式

### Task 5: 配置与测试
**文件:** `dragon/config.py` (修改), `tests/test_voice.py` (新建)
**内容:**
- 新增 `VoiceConfig` 类：`enabled, default_voice, speed, auto_play`
- 集成到 `DragonConfig`
- 单元测试：句子边界检测、音频生成、流式消费

## 执行顺序

```
Task 1 (VoiceEngine) ──→ Task 2 (edge-tts stream) ──→ Task 3 (API)
                                                           │
                                                    Task 5 (config + tests)
                                                           │
                                                    Task 4 (飞书集成)
```

Task 1+2 可合并为一个 subagent（同一文件），Task 3+5 合并，Task 4 独立。

## 关键决策

- **不修改现有 `tool_tts`**：VoiceEngine 是独立模块，tool_tts 保留用于"生成语音文件"场景
- **句子边界检测用正则**：简单可靠，不需要 NLP 分词
- **音频格式 MP3**：edge-tts 原生输出，飞书支持
- **WebSocket 优先于 SSE**：双向通信更适合语音场景
