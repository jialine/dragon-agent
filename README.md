# Dragon Agent 🐉

> 可自我进化的 AI Agent 框架 — 工具系统 · 技能引擎 · 工作流编排 · 多平台网关

Dragon Agent 是一个 Python AI Agent 框架，支持工具调用、技能记忆、工作流编排、以及多平台消息网关（飞书/Lark 等）。

## 核心特性

| 模块 | 说明 |
|------|------|
| 🤖 **Agent Loop** | 多轮推理 + 工具调用，支持 90 轮迭代 |
| 🔧 **Tool System** | 内置工具：terminal、file、web_search、memory、skills、wan_video 等 |
| 📚 **Skill Engine** | 可学习的技能系统，支持创建/加载/版本管理 |
| 🔀 **Workflow Engine** | DAG 工作流编排，支持 LLM → 工具 → 条件分支 |
| 🌐 **Gateway** | 飞书/Lark WebSocket 网关，实时消息收发 |
| 🧠 **Multi-Provider** | DeepSeek / OpenAI / Ollama 多模型后端 |
| 💾 **Memory** | 跨会话持久记忆，支持自动压缩 |

## 快速开始

```bash
# 安装
git clone git@gitee.com:jialine/dragon-agent.git
cd dragon-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入你的 API 密钥

# 启动 CLI
python dragon_agent_loop.py

# 启动飞书网关
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
dragon gateway --feishu --port 8000
```

## 项目结构

```
dragon/
├── cli.py              # CLI 命令行入口
├── main.py             # Agent 主循环
├── config.py           # 配置管理
├── pipeline.py         # 流水线执行器
├── prompt_builder.py   # Prompt 构建
├── credential.py       # 凭证管理
├── web_providers.py    # Web 搜索提供者
├── identity.py         # Agent 身份
├── constants.py        # 常量定义
├── gateway/            # 消息网关
│   ├── server.py       # FastAPI 服务器
│   ├── feishu.py       # 飞书/Lark 适配器
│   └── cli.py          # 网关 CLI
├── tool/               # 工具系统
│   ├── registry.py     # 工具注册表
│   ├── guardrails.py   # 安全护栏
│   └── builtins/       # 内置工具
├── skill/              # 技能引擎
│   └── engine.py       # 技能加载/执行
├── workflow/           # 工作流引擎
│   └── planner.py      # 工作流规划器
├── provider/           # LLM 提供者
│   └── __init__.py     # 多模型后端注册
└── orchestrator/       # 多 Agent 编排
```

## 工具系统

Dragon 拥有丰富的内置工具集：

| 工具 | 功能 |
|------|------|
| `terminal` | Shell 命令执行 |
| `file_read` / `file_write` | 文件读写 |
| `web_search` | 网页搜索 |
| `memory` | 持久记忆存储 |
| `skills` | 技能管理 |
| `session_search` | 跨会话搜索 |
| `wan_video` | WAN 2.7 视频生成 |
| `todo` | 任务管理 |
| `clarify` | 用户澄清 |
| `execute_code` | Python 代码执行 |
| `patch` | 文件精准编辑 |
| `comfyui_generate` | ComfyUI 图像生成 |
| `edge_tts` | 文本转语音 |
| `ffmpeg_composite` | 视频合成 |

## 配置

```yaml
# config.yaml
dispatch:
  global_api:
    model: deepseek-v4-pro
    base_url: https://api.andlapi.cn/v1
    api_key: "sk-你的API密钥"

gateway:
  enabled: true
  platforms:
    feishu:
      app_id: "cli_xxxxxxxx"
      app_secret: "xxxxxxxx"
```

## 许可证

见 [LICENSE.md](LICENSE.md)

## 贡献

欢迎提交 Issue 和 PR。

---

**Dragon Agent** — Build agents that learn, remember, and evolve. 🐉
