# 短剧工作区目录规范 v1.0

```
{workspace}/                           # 例: /data/dramas/重生之AI大佬/
│
├── 00_meta.yaml                       # 项目元信息
│   # title, genre, episode_count, created_at, status, seed_registry
│
├── 01_outline/
│   ├── outline.md                     # 故事大纲
│   └── worldbuilding.md               # 世界观设定
│
├── 02_story/
│   ├── full_story.md                  # 完整故事
│   └── characters_brief.md            # 角色简介
│
├── 03_review/
│   ├── story/                         # 故事审阅记录
│   │   ├── round1_*.md                # 第一轮审阅意见
│   │   ├── round2_*.md
│   │   ├── fix_diff.md
│   │   └── final_fix.md
│   └── script/                        # 剧本审阅记录
│       └── ...
│
├── 04_script/
│   ├── episode_01.md                  # 分集剧本（标准格式）
│   ├── episode_02.md
│   └── ...
│
├── 05_assets/
│   ├── asset_manifest.yaml            # 资源清单
│   │   # characters: [{name, role, traits, ...}]
│   │   # scenes: [{id, location, time, mood, ...}]
│   │   # props: [{name, scene_ids, description}]
│   ├── characters/
│   │   ├── 主角_李明/
│   │   │   ├── portrait.jpg           # 正面肖像
│   │   │   ├── front_full.jpg         # 正面全身
│   │   │   ├── side_full.jpg          # 侧面全身
│   │   │   ├── back_full.jpg          # 背面全身
│   │   │   ├── expressions/           # 表情变体（可选）
│   │   │   └── design_prompt.md       # 设计提示词记录
│   │   └── 配角_王芳/
│   │       └── ...
│   ├── scenes/
│   │   ├── scene_01_咖啡厅.jpg
│   │   ├── scene_02_办公室.jpg
│   │   └── ...
│   └── props/
│       └── props_list.md
│
├── 06_storyboard/
│   ├── storyboard.yaml                # 分镜表（原始路径）
│   └── storyboard_oss.yaml            # 分镜表（SignOSS URL 替换后）
│   # storyboard 格式：
│   # shots:
│   #   - id: ep01_s01
│   #     episode: 1
│   #     scene: 咖啡厅
│   #     characters: [李明, 王芳]
│   #     model: r2v                   # r2v / i2v / t2v
│   #     ref_image: oss://...          # R2V/I2V 参考图
│   #     prev_frame: ep01_s00_last     # I2V 上一帧尾帧引用
│   #     prompt: "..."                # 完整生成提示词
│   #     seed: 42                     # 固定种子号
│   #     duration_secs: 5
│   #     dialogue: "李明：好久不见"
│   #     sfx: "door_bell.mp3"         # 同步音效
│   #     camera: "中景，平视"
│
├── 07_shots/
│   ├── seeds.yaml                     # 种子注册表
│   │   # scene_01: seed=42
│   │   # episode_01_opening: seed=100
│   ├── ep01/
│   │   ├── s01.mp4                    # 分镜头1
│   │   ├── s01_prompt.md              # 实际使用的prompt（含种子）
│   │   └── ...
│   └── ep02/
│       └── ...
│
├── 08_audio/
│   ├── ep01/
│   │   ├── s01_dialogue.mp3           # 对话语音
│   │   ├── s01_sfx.mp3               # 音效
│   │   └── ...
│   └── voices.yaml                    # 音色配置
│       # 李明: voice=zh-CN-YunxiNeural
│       # 王芳: voice=zh-CN-XiaoxiaoNeural
│
├── 09_composite/
│   ├── intros/
│   │   ├── intro_ep01.mp4             # 片头（2秒标题）
│   │   ├── ep_number_01.mp4           # 第X集（2秒）
│   │   └── ...
│   ├── bgm/
│   │   ├── ep01_bgm.mp3
│   │   └── ...
│   └── subtitles/
│       ├── ep01.srt                   # 字幕时间轴
│       └── ...
│
└── 10_final/
    ├── 剧本_重生之AI大佬.pdf           # 剧本PDF（Phase 1产出）
    ├── 重生之AI大佬_ep01.mp4           # 最终成品
    ├── 重生之AI大佬_ep02.mp4
    ├── FINAL_VERDICT.md               # 终审结论
    └── final_review_*.md              # 各项审阅报告
```

## 命名规范

| 规则 | 示例 |
|------|------|
| 中文标题用拼音目录名 | `重生之AI大佬` → `/data/dramas/chongsheng_ai/` |
| 文件统一前缀编号 | `ep01_s03.mp4` |
| prompt记录与镜头同名 `.md` | `s03.mp4` ↔ `s03_prompt.md` |
| 种子统一注册 `seeds.yaml` | 每个场景/镜头的种子号不重复记录 |
| 审阅意见按轮次编号 | `round1_剧情逻辑审查.md` |

## R2V / I2V 图片上传规范

在分镜生成前，所有参考图片必须通过 SignOSS 上传：

```bash
# 脚本: scripts/drama_signoss_upload.py
# 输入: 06_storyboard/storyboard.yaml (含本地路径)
# 输出: 06_storyboard/storyboard_oss.yaml (本地路径 → OSS URL)
# OSS URL 格式: https://signoss.example.com/dramas/{project}/{path}
```

`storyboard_oss.yaml` 中每个镜头的 `ref_image` 字段为 OSS 公网 URL，供 wan_video API 直接使用。
