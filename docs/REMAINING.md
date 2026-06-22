# Dragon Agent — 待完成事项

> 更新: 2026-06-17 | 基于 Hermes Agent 对齐

## ✅ 已完成

| 模块 | 工具数 | 状态 |
|------|--------|------|
| 核心工具 (search/file/execute/http) | 5 | ✅ |
| TTS 语音 | 2 | ✅ |
| Vision 图片 | 3 | ✅ |
| Browser 浏览器 | 6 | ✅ |
| Web 搜索抓取 | 3 | ✅ |
| Analysis 数据分析 | 3 | ✅ |
| Documents 文档 | 5 | ✅ |
| Email 邮件 | 3 | ✅ |
| Kanban 看板 | 6 | ✅ |
| **合计** | **36** | **1626 测试通过** |

## ⬜ 工具缺口（vs Hermes）

### 高优先（To-C 竞争力）
| 功能 | Hermes 实现 | 说明 |
|------|-----------|------|
| **语音模式** | voice_mode.py + neutts_synth | 实时 TTS 流式播放，飞书场景加分 |
| **图片生成** | image_generation_tool.py | ComfyUI/Stable Diffusion 集成 |
| **网络搜索增强** | web_providers/ (brave, searxng) | 多搜索引擎，目前只有 DDG |
| **地图/地理** | maps 技能 | OSM 地理编码、路径规划 |

### 中优先（差异化）
| 功能 | Hermes 实现 | 说明 |
|------|-----------|------|
| Obsidian 笔记 | obsidian 技能 | 个人知识库管理 |
| Google Workspace | google-workspace 技能 | Gmail/Drive/Docs/Sheets |
| 飞书文档编辑 | feishu_doc/drive_tool | 原生的飞书文档读写 |
| YouTube 内容 | youtube-content 技能 | 字幕提取、摘要生成 |
| Notion/Linear/Airtable | 对应技能 | 项目管理集成 |
| Spotify 音乐 | spotify 技能 | 娱乐场景 |
| GIF 搜索 | gif-search 技能 | Tenor 集成 |

### 低优先（锦上添花）
| 功能 | 说明 |
|------|------|
| 社媒 (X/Twitter) | To-C 场景非必须 |
| 智能家居 (HomeAssistant) | To-C 场景非必须 |
| 游戏服务器 | To-C 场景非必须 |
| 红队/越狱测试 | 内部工具 |
| Computer Use (CUA) | 高成本，ROI 低 |
| 音频生成 (MusicGen) | 小众需求 |

## ⬜ 工程缺口

### P4: 行业知识 SKILL.md
```
状态: 未开始
内容: 金融/医疗/法律/教育 行业术语、法规、模板
格式: SKILL.md 格式，可被 Dragon Skill Engine 加载
估价: 2 周
```

### P5: Web UI 管理面板
```
状态: 未开始
内容: 对话界面、工具管理、知识库管理、用量统计
技术: 可用 Gradio/Streamlit 快速搭建，或用 Next.js
估价: 2-3 周
```

### P6: 生产加固
```
状态: 未开始
内容:
  - 压测 (locust/k6) — 目标 100 并发
  - 监控 (Prometheus metrics 端点)
  - 日志系统完善
  - 异常告警
  - API 限流增强
  - Docker 部署
估价: 2 周
```

## ⬜ TUI 升级

```
当前: Node.js Ink TUI (tui/ 目录，未完成)
目标: Python Rich TUI (对齐 Hermes 体验)
收益: 去掉 Node.js 依赖，统一技术栈
估价: 1-2 周
```

## ⬜ 部署/运维

| 项目 | 状态 |
|------|------|
| Dockerfile | ❌ |
| docker-compose | ❌ |
| systemd 服务文件 | ❌ (需用户手动创建) |
| CI/CD (Gitee Actions) | ❌ |
| 版本号/Changelog | ❌ |
| Gitee 仓库改名 (panda→dragon) | ✅ 已完成 |

## 📊 总结

```
工具对齐:   36/36 ✅
核心差距:   语音模式 + 图片生成 (To-C 加分项)
工程缺口:   行业知识 + Web UI + 生产加固
部署:       Docker + CI/CD
```

**建议优先顺序:**
1. Gitee 仓库改名 + 语音模式（2天）
2. Web UI 快速原型（1周，Gradio）
3. 行业 SKILL.md（2周）
4. 生产加固 + Docker（2周）
