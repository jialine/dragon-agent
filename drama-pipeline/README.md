# 🎬 Dragon 短剧流水线

> 竖屏/横屏短剧全自动生产流水线 — 编剧(LLM)→审核→分镜→生成(HappyHorse R2V/T2V)→配音(TTS)→合成(ffmpeg)→上传。

一键开箱即用，支持一键升级。

## 快速开始

```bash
# 1. 一键安装（开箱即用）
./install.sh

# 2. 填入 API Key（获取地址 https://api.andlapi.cn）
vi .env   # 改 DRAGON_API_KEY=sk-xxx

# 3. 打开 WebUI
# http://<本机IP>:5000/
```

就这么简单。全程无需 root，无需 docker。

## 一键升级

```bash
./upgrade.sh            # git pull 或本地覆盖 + 更新依赖 + 重启
./upgrade.sh --src /path/to/new-package   # 从新版本包覆盖
./upgrade.sh --from-git <repo_url>        # 从 git 仓库拉取
```

升级**绝不覆盖**你的 `config.yaml` / `.env` / `drama.db` / `assets`（角色图、视频）。

## 目录结构

```
dragon-drama-pipeline/
├── install.sh            # 一键安装
├── upgrade.sh            # 一键升级
├── uninstall.sh          # 卸载
├── requirements.txt      # 依赖清单
├── config.example.yaml   # 配置模板（首次安装生成 config.yaml）
├── .env.example          # 环境变量模板（首次安装生成 .env）
├── scripts/              # 流水线脚本（23 个阶段脚本）
│   ├── gen_video.py      #   视频生成 CLI（T2V/R2V/I2V）
│   ├── happyhorse_api.py #   HappyHorse API 封装
│   └── drama_*.py        #   五阶段流水线脚本
├── workflows/            # 工作流 YAML（4 个）
│   ├── drama_production.yaml
│   ├── drama_review_loop.yaml
│   ├── drama_multimodal_review.yaml
│   └── drama_final_review.yaml
├── webui/                # Drama Studio WebUI
│   ├── app.py            #   Flask 后端（含分镜管理、视频提交）
│   ├── database.py       #   SQLite 数据层
│   ├── index.html        #   前端
│   └── pipelines/        #   子管线（剧本/角色/分镜/视频）
└── docs/                 # 文档
```

## 文档

| 文档 | 内容 |
|------|------|
| [docs/安装指南.md](docs/安装指南.md) | 详细安装步骤、环境要求、常见问题 |
| [docs/使用手册.md](docs/使用手册.md) | WebUI 操作、脚本 CLI、模型选型规则 |
| [docs/工作流说明.md](docs/工作流说明.md) | 五阶段流水线、脚本清单、工作流 YAML |
| [docs/升级说明.md](docs/升级说明.md) | 升级/回滚/版本管理 |

## 系统架构

```
编剧(LLM) → 文学审核(LLM交叉) → 模型审核(自动) → 用户确认
    ↓
分镜生成 r2v(角色) / t2v(场景) → 轮询 andlapi
    ↓
配音 edge-tts → 音效混音 → ffmpeg 合成
    ↓
上传 → 推送飞书
```

## 核心依赖

- Python ≥ 3.8
- 轻量库：`requests` / `pyyaml` / `flask` / `flask-cors`（**不依赖 Dragon 核心 gateway**）
- 可选：`ffmpeg`（合成阶段）、`edge-tts`（配音阶段）

## 铁律（用户硬要求）

1. **视频模型只用 HappyHorse**（`happyhorse-1.1-t2v` / `happyhorse-1.1-r2v`），禁用 wan2.7
2. **有命名角色的镜头必须 R2V + 参考图**，T2V 仅用于空镜/场景
3. **分辨率 1080P**（1920×1080），竖屏项目 720×1280
4. **每集 ≥ 3 分钟**（剪辑 buffer）
5. **提示词用中文，≤ 80 字**（HappyHorse 轻量模型，长描述跑偏）

详见各 docs 文档。
