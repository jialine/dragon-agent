# Dragon Agent — 使用文档

> 版本: 1.1 | 更新: 2026-06-17

## 1. 快速开始

### 一键安装

```bash
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/main/scripts/install.sh | bash
```

### 手动安装

```bash
git clone https://gitee.com/jialine/dragon-agent.git
cd dragon-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 启动

```bash
# HTTP API 服务
python -m dragon

# CLI 交互模式
python -m dragon cli
```

服务默认运行在 `http://localhost:8780`。

## 2. 配置

编辑 `~/.dragon/config.yaml`：

```yaml
# 主模型 (AgileMind API)
provider:
  agilemind:
    api_key: "${AGILEMIND_API_KEY}"
    api_url: "https://api.agilemind.ai/v1"
    model: "122b-moe"

# Feishu 网关
gateway:
  feishu:
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

# 可选: SMTP/IMAP 邮件
email:
  smtp_host: "${DRAGON_SMTP_HOST}"
  smtp_user: "${DRAGON_SMTP_USER}"
```

## 3. 工具列表 (36 个)

### 基础工具
| 工具 | 说明 |
|------|------|
| `search` | 文件内容正则搜索 |
| `file_read` | 读取文件 |
| `file_write` | 写入文件 |
| `execute` | 执行 Shell 命令 |

### Web 工具
| 工具 | 说明 |
|------|------|
| `http_get` | HTTP GET 请求 |
| `web_search` | DuckDuckGo 搜索 |
| `web_fetch` | 抓取网页内容 |
| `web_download` | 下载文件 |

### 浏览器工具 (Playwright)
| 工具 | 说明 |
|------|------|
| `browser_open` | 打开 URL |
| `browser_screenshot` | 页面截图 |
| `browser_get_text` | 提取文本 |
| `browser_click` | 点击元素 |
| `browser_type` | 输入文字 |
| `browser_close` | 关闭浏览器 |

### 语音工具
| 工具 | 说明 |
|------|------|
| `tts` | 文本转语音 (edge-tts) |
| `tts_voices` | 列出可用语音 |

### 视觉工具
| 工具 | 说明 |
|------|------|
| `vision_analyze` | AI 图片分析 |
| `vision_info` | 图片元数据 |
| `ocr` | 图片文字识别 |

### 数据分析
| 工具 | 说明 |
|------|------|
| `code_exec` | 沙箱执行 Python |
| `data_explore` | 数据文件概览 |
| `data_plot` | matplotlib 绘图 |

### 文档工具
| 工具 | 说明 |
|------|------|
| `pptx_read` | 读取 PPTX |
| `pptx_create` | 创建 PPTX |
| `pdf_read` | 读取 PDF |
| `pdf_extract` | 提取 PDF 页面 |
| `docx_read` | 读取 DOCX |

### 邮件工具
| 工具 | 说明 |
|------|------|
| `email_send` | 发送邮件 |
| `email_search` | 搜索邮件 |
| `email_read` | 读取邮件 |

### 项目管理
| 工具 | 说明 |
|------|------|
| `kanban_create_board` | 创建看板 |
| `kanban_add_task` | 添加任务 |
| `kanban_list` | 列出任务 |
| `kanban_move` | 移动任务 |
| `kanban_delete_task` | 删除任务 |
| `kanban_list_boards` | 列出看板 |

## 4. API 参考

### 对话

```bash
curl -X POST http://localhost:8780/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "你好"}], "stream": false}'
```

### 工具调用

```bash
# 搜索文件
curl -X POST http://localhost:8780/v1/tools/search \
  -H "Content-Type: application/json" \
  -d '{"query": "hello"}'

# 文本转语音
curl -X POST http://localhost:8780/v1/tools/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "你好世界"}'
```

### 健康检查

```bash
curl http://localhost:8780/health
# {"status": "ok", "tools": 36}
```

## 5. 部署到生产

### 使用 systemd

```bash
sudo tee /etc/systemd/system/dragon.service << 'EOF'
[Unit]
Description=Dragon Agent
After=network.target

[Service]
Type=simple
User=jialine
WorkingDirectory=/home/jialine/dragon-agent
ExecStart=/home/jialine/dragon-agent/.venv/bin/python -m dragon
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now dragon
```

### 使用守护脚本

```bash
python3 scripts/daemon.py start
python3 scripts/daemon.py status
python3 scripts/daemon.py stop
```

## 6. 环境变量

| 变量 | 说明 |
|------|------|
| `AGILEMIND_API_KEY` | AgileMind API 密钥 |
| `AGILEMIND_API_URL` | AgileMind API 地址 |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |
| `DRAGON_SMTP_HOST` | SMTP 服务器 |
| `DRAGON_SMTP_USER` | SMTP 用户名 |
| `DRAGON_SMTP_PASSWORD` | SMTP 密码 |
| `DRAGON_IMAP_HOST` | IMAP 服务器 |
| `DRAGON_IMAP_USER` | IMAP 用户名 |
| `DRAGON_IMAP_PASSWORD` | IMAP 密码 |

## 7. 验证安装

```bash
# 健康检查
curl http://localhost:8780/health

# 运行测试
pytest tests/ -q
```
