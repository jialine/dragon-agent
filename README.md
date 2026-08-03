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

### 获取 API Key

前往 **[api.andlapi.cn](https://api.andlapi.cn)** 注册账号，即可获取 DeepSeek V4 Pro 等模型的 API 密钥。注册后在控制台复制 `sk-` 开头的 Key，填入 `config.yaml` 或设置环境变量：

```bash
export DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
```

## 测试

Dragon Agent 拥有完整的单元测试覆盖，176+ 测试用例，覆盖 20 个核心模块。

### 运行测试

```bash
# 安装测试依赖
pip install -r requirements-dev.txt

# 运行全部测试（纯函数，无需 API key）
python3 -m pytest tests/ -v

# 运行特定模块
python3 -m pytest tests/test_tool.py tests/test_rate_limiter.py -v

# 带覆盖率报告
python3 -m pytest tests/ --cov=dragon --cov-report=term-missing
```

### 测试覆盖

| 测试文件 | 覆盖模块 | 用例数 |
|---------|---------|--------|
| `test_tool.py` | ToolRegistry, ToolDef, CircuitBreaker, Pipeline, 去重 | 57 |
| `test_rate_limiter.py` | TokenBucket, RateLimiter, CircuitBreaker, parse_retry_after | 23 |
| `test_credential_pool.py` | Credential, CredentialPool, CredentialManager | 12 |
| `test_prompt_builder.py` | MiniTemplate, PromptBuilder, CacheEntry | 10 |
| `test_guardrails.py` | ToolGuardrails, GuardrailCheck, ToolCallSignature | 13 |
| `test_think_scrubber.py` | strip_think_blocks, StreamingThinkScrubber | 11 |
| `test_error_classifier.py` | classify_api_error, is_retryable, format_chinese_error | 9 |
| `test_usage_pricing.py` | get_pricing, get_cost, list_models, format_cost | 10 |
| `test_redact.py` | mask_secret, redact_sensitive_text, redact_for_logs | 8 |
| `test_feishu_pure.py` | handle_url_verification, verify_hmac_signature | 5 |
| `test_file_safety.py` | is_file_extension_safe, sanitize_filename, SafetyValidator | 8 |
| `test_orch_classifier.py` | classify (orchestrator), Tier, Classification | 5 |
| `test_factcheck.py` | ClaimExtractor, FactClaim, ClaimType | 5 |
| `test_hallmetrics.py` | BenchmarkRunner, HallucinationReport | 5 |

查看更多已有测试文件：`test_gateway.py`, `test_workflow.py`, `test_skill.py`, `test_session.py` 等。

### 一键安装+测试脚本

```bash
# 在任何干净 Linux 上运行（需要 Python 3.11+）
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/master/install.sh | bash
```

脚本会自动：创建虚拟环境 → 安装依赖 → 运行全量测试。

### 环境要求

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.11+ | 单元测试不需要 3.12 特性 |
| pip | 23+ | 虚拟环境自动管理 |
| 磁盘 | 500MB | venv + 依赖 |
| 网络 | 出站 | pip install 时需要 |
| API Key | **不需要** | 纯函数测试全面 mock |

### 测试哲学

- **纯函数优先**：TokenBucket、MiniTemplate、mask_secret 等无 IO 模块完整覆盖
- **边界测试**：空输入、超限、异常路径、并发安全
- **集成验证**：真实 builtins registry 去重（98 工具→0 重复）
- **回归保护**：每次 patch 后跑全量 `pytest tests/`


见 [LICENSE.md](LICENSE.md)

## 贡献

欢迎提交 Issue 和 PR。

---

**Dragon Agent** — Build agents that learn, remember, and evolve. 🐉
