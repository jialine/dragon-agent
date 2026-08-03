# Changelog

All notable changes to Dragon Agent will be documented in this file.

## [1.0.1] — 2026-06-28

### 🔧 Fixes
- **andlapi.cn SSL fix**: Added `verify=False` to httpx client in `dragon/auxiliary.py` — andlapi.cn has self-signed/invalid SSL certificate.
- **Provider switch**: Gateway server now routes through `andlapi` provider instead of `openai` (both classify and chat paths).

### ✨ Features
- **OpenAI provider env override**: `OPENAI_BASE_URL` and `OPENAI_MODEL` env vars now supported, enabling DeepSeek/andlapi compatibility without code changes.

### 🔨 gen_video.py Refactor
- **HTTP API polling**: Replaced SSH+MySQL task querying with proper HTTP API calls (`GET /v1/video/generations/{task_id}`).
- **New arguments**: `--duration` (default 15s), `--ref-image` (multiple allowed, for R2V reference image input).
- **New model**: `happyhorse-1.1-r2v` added to model choices.
- **Timeout**: Increased from 300s to 600s for longer video generation.

### 📝 Skills
- **video_generation.md**: Updated HappyHorse pricing info, added explicit note that HappyHorse supports Chinese prompts directly (no translation needed).

## [1.0.0] — 2026-06-25

### 🎉 First Release — Dragon Agent v1.0

**63 tools across 14 categories. P0 + P1 complete.**

### ✨ Features

#### Core Intelligence
- **DragonRouter**: 1.5B local model for intent classification and industry routing
- **DragonDispatcher**: Multi-industry model dispatch with fallback
- **JuryDebate**: Multi-model debate system with consensus building
- **FactChecker**: Claim extraction and fact verification
- **HallucinationTracker**: Hallucination rate monitoring
- **AutoLoopGuard**: Infinite loop detection and prevention

#### Tools (63 total)
- **Core** (5): search, file_read, file_write, execute, http_get
- **Web** (4): web_search (Brave/SearXNG/DDG), web_fetch, web_download, web_providers
- **Voice** (2): tts (edge-tts), tts_voices
- **Vision** (3): vision_analyze, vision_info, ocr
- **Browser** (6): open, screenshot, get_text, click, type, close
- **Image Gen** (2): image_generate (5 backends), image_models
- **Maps** (4): geocode, reverse_geocode, get_route, search_poi
- **Analysis** (3): code_exec, data_explore, data_plot
- **Documents** (5): pptx_read/create, pdf_read/extract, docx_read
- **Email** (3): send, search, read
- **Kanban** (6): create_board, add_task, list, move, delete_task, list_boards

#### Integrations
- **Feishu** (3): read_doc, list_docs, create_doc
- **YouTube** (2): transcript, summarize
- **Obsidian** (3): read, search, create
- **Notion** (3): search, read_page, create_page
- **Linear** (2): list_issues, create_issue
- **Airtable** (2): list_records, create_record
- **Google Workspace** (4): gmail_send, gmail_search, drive_search, calendar_list
- **Spotify** (2): search, now_playing
- **GIF** (2): search, trending

#### Platforms
- **Feishu/Lark**: Full bot support (WebSocket + Webhook)
- **WeChat**: Official Account adapter
- **Telegram**: Bot API
- **Discord**: Bot integration

#### Interfaces
- **REST API**: FastAPI with /v1/chat, /v1/chat/stream, /v1/chat/voice
- **Rich TUI**: Python Rich terminal interface (/quit, /help, /new commands)
- **Gradio Web UI**: Chat + Status + Tools management panel
- **CLI**: Full command-line setup wizard

#### Operations
- **Docker**: Dockerfile + docker-compose.yml
- **Monitoring**: Prometheus /metrics endpoint
- **Load Testing**: scripts/load_test.py
- **Backup**: S3/OSS cloud backup support
- **Skill Engine**: Self-evolving skill system with version tracking

### 🏗️ Architecture

```
User → Gateway (Feishu/WeChat/Telegram) → DragonRouter → DragonDispatcher
                                    ↓
                              JuryDebate → FactChecker → Consensus
                                    ↓
                              API Response (REST/SSE/WebSocket)
```

### 📦 Deployment

```bash
# One-line install
curl -fsSL https://gitee.com/jialine/dragon-agent/raw/main/scripts/install.sh | bash

# Docker
docker-compose up -d

# Systemd
sudo systemctl enable --now dragon-agent@$USER
```

### 🔧 Requirements

- Python 3.10+
- 4GB+ RAM (8GB recommended for local router model)
- Optional: NVIDIA GPU for local ComfyUI image generation
- Optional: API keys for cloud image generation (RunningHub/Stability/Replicate)
