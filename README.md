# Dragon Agent 🐉 —— 你的全能 AI 员工

> 短剧、写代码、做硬件。一句话，全搞定。

[![Stars](https://img.shields.io/github/stars/jialine/dragon-agent?style=social)](https://github.com/jialine/dragon-agent)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE.md)
[![Powered by andlapi](https://img.shields.io/badge/API-andlapi.cn-ff6b35)](https://andlapi.cn)
[![Python](https://img.shields.io/badge/Python-3.11+-green)]()

---

## 🤯 它能做什么

| 🎬 短剧生产 | 💻 写代码 | 🔧 硬件开发 |
|------------|----------|------------|
| 剧本→分镜→角色图→视频→配音→合成 | 需求→拆任务→写代码→测试→PR | 方案→选型→画板子→BOM→固件 |
| **30 分钟一集** | **40 分钟一个完整应用** | **半小时出完整方案** |

---

## ⚡ 30 秒上手

```bash
# 安装
git clone git@gitee.com:jialine/dragon-agent.git
cd dragon-agent
pip install -r requirements.txt

# 配置 API Key（去 andlapi.cn 注册就送 ¥10）
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入你的 API Key

# 开始干活
python dragon_agent_loop.py
```

**WebUI 一键安装：**
```bash
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/master/install.sh | bash -s -- --start-webui
```

---

## 🔥 为什么用 Dragon

| | 传统方式 | Dragon Agent |
|--|---------|-------------|
| 短剧一集 | 5 人 × 2 天 | 1 人 × 30 分钟 |
| 一个功能 | 开发 3 天 | 一句话 + 等 10 分钟 |
| 硬件方案 | 团队 1 周 | 1 人 × 1 小时 |
| API 成本 | 5 个平台来回切 | 一个 andlapi Key 全搞定 |

---

## 🎬 短剧生产全流程

```
剧本 → Dragon 自动分镜 → 角色图生成 → 场景图生成 → 视频生成 → TTS 配音 → 合成输出
```

实际案例：**猩族纪元**——50 集科幻短剧系列，Dragon Agent 全流程自动生产。

- 自动解析剧本，拆分为分镜
- 为每个角色生成统一形象图
- 为每个场景生成背景图
- 调用视频生成模型，将静态素材转为动态视频
- TTS 配音 + 音效 + 合成
- 支持竖屏（9:16）和横屏（16:9）

---

## 💻 写代码能力

给需求，出完整项目：

```bash
dragon run --mode code --prompt "做一个短剧项目管理后台，支持创建项目、上传剧本、查看分镜状态、预览视频"
```

Dragon 自动完成：技术选型 → 架构设计 → 前后端代码 → 测试 → 可运行的完整应用。

---

## 🔧 硬件开发能力

```bash
dragon run --mode hardware --prompt "设计一个低功耗温湿度传感器，ESP32 + SHT30，MQTT 上报，电池供电 6 个月"
```

输出：芯片选型对比表 + 电路原理图草案 + BOM 清单（含价格和链接）+ 固件框架代码 + 功耗估算。

---

## 🛠 技术架构

Dragon Agent 是一个完整的 AI Agent 框架：

| 模块 | 说明 |
|------|------|
| 🤖 **Agent Loop** | 多轮推理 + 工具调用，支持 90 轮迭代 |
| 🔧 **Tool System** | terminal、file、web_search、memory、skills、视频生成等 15+ 工具 |
| 📚 **Skill Engine** | 可学习的技能系统，支持创建/加载/版本管理 |
| 🔀 **Workflow Engine** | DAG 工作流编排，支持 LLM → 工具 → 条件分支 |
| 🌐 **Gateway** | 飞书/Lark WebSocket 网关，实时消息收发 |
| 🧠 **Multi-Provider** | 支持 andlapi / DeepSeek / OpenAI / Ollama 等多模型后端 |
| 💾 **Memory** | 跨会话持久记忆，支持自动压缩 |

---

## 📦 内置工具

| 工具 | 功能 |
|------|------|
| `terminal` | Shell 命令执行 |
| `file_read` / `file_write` | 文件读写 |
| `web_search` | 网页搜索 |
| `memory` | 持久记忆存储 |
| `skills` | 技能管理 |
| `session_search` | 跨会话搜索 |
| `wan_video` | 视频生成 |
| `comfyui_generate` | ComfyUI 图像生成 |
| `edge_tts` | 文本转语音 |
| `ffmpeg_composite` | 视频合成 |
| `execute_code` | Python 代码执行 |
| `todo` | 任务管理 |

---

## 🧪 测试

176+ 测试用例，覆盖 20 个核心模块。纯函数测试，无需 API Key。

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

---

## 🚀 API 服务

Dragon Agent 底层模型调用全部走 **[andlapi.cn](https://andlapi.cn)**：

- 一个 Key，跑所有模型（DeepSeek V4 Pro / GPT-4o / Claude / 文生图 / 文生视频 / TTS）
- 比直连便宜 60%
- 注册送 ¥10 体验金，充 200 到手 350

```yaml
# config.yaml
dispatch:
  global_api:
    model: deepseek-v4-pro
    base_url: https://api.andlapi.cn/v1
    api_key: "sk-你的API密钥"
```

---

## ⭐ 支持我们

如果 Dragon 帮到了你，给个 Star ⭐

- Star 破 **500** → 开源短剧视频流水线
- Star 破 **1000** → 开源 SDK
- Star 破 **5000** → 全开源

---

## 📄 协议

见 [LICENSE.md](LICENSE.md)

---

**Dragon Agent** — 2025 年，AI 员工来了。🐉

*Powered by [andlapi.cn](https://andlapi.cn)*
