# Dragon Agent — Image Generation 实现计划

> 目标: 1 天 | 优先级: P0

## 架构

```
Agent 调用 tool_image_generate(prompt, style, size)
    │
    ▼
ImageGenTool
    │
    ├─→ ComfyUIBackend (本地/云端, 完整管线)
    ├─→ ReplicateBackend (API, 开箱即用)
    └─→ DummyBackend (测试/fallback)
```

## 任务拆分

### Task 1: ImageGen 核心 + ComfyUI 后端
**文件:** `dragon/tool/builtins/image_gen.py`
**内容:**
- `ImageGenBackend` 抽象基类
- `ComfyUIBackend` — 连接本地 ComfyUI API，提交 workflow + 下载结果
- `tool_image_generate(prompt, negative_prompt, width, height, steps, seed)` — 工具函数
- `tool_image_models()` — 列出可用模型

### Task 2: 配置 + 测试
**文件:** `dragon/config.py` (修改), `tests/test_image_gen.py` (新建)
**内容:**
- `ImageGenConfig` — backend_type, comfyui_url, default_model 等
- 单元测试

## 关键决策
- **不依赖 ComfyUI 必须安装**：如果没有 ComfyUI，用 DummyBackend 返回占位
- **端口默认 8188**：ComfyUI 标准端口
- **输出格式 PNG**：标准
- **工具集成到 `register_builtins`**
