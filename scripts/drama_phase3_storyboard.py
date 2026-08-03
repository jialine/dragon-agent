#!/usr/bin/env python3
"""drama_phase3_storyboard.py — 分镜表生成（模型选型/种子/对话/音效一体化）"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位资深分镜师和AI视频导演。你的任务是把剧本转为可执行的分镜表。
输出必须是合法 YAML，包含每个镜头的完整生成参数。
关键决策：
- R2V: 有主角/配角出现（需要角色参考图）
- I2V: 紧接上一镜头尾帧的延续镜头
- T2V: 纯场景/空镜/无人物
- 种子：同类型镜头用相同种子保证一致性"""

STORYBOARD_PROMPT = """根据以下剧本和资产，生成完整分镜表。

## 剧本
{scripts}

## 角色表（含参考图路径）
{characters}

## 场景表（含参考图路径）
{scenes}

## 技术参数
- 总集数：{episodes}
- 每集时长：约 {duration} 秒
- 画面比例：{aspect}

## 分镜要求

每个镜头包含以下字段，输出 YAML 数组：

```yaml
shots:
  - id: "ep01_s01"                          # 唯一ID
    episode: 1
    scene: "咖啡厅"                          # 所属场景名
    location: "室内咖啡厅靠窗位置"
    characters: ["陈默"]                     # 出场角色（空则为纯场景）
    model: "r2v"                            # r2v / i2v / t2v
    ref_image: "characters/陈默/portrait.jpg" # R2V参考图路径
    prev_frame: ""                          # I2V: 引用上一镜ID（如 ep01_s00_last）
    prompt: "..."                           # 完整英文视频生成prompt
    seed: 42                                # 固定种子（同场景/同角色复用）
    duration_secs: 5                        # 镜头时长
    dialogue: ""                            # 对话文本（如有）
    dialogue_voice: ""                      # 配音角色名
    sfx: ""                                 # 同步音效文件
    sfx_prompt: ""                          # 音效AI生成prompt
    camera: "中景，平视，缓慢推进"            # 镜头运动
    transition: "cut"                       # 转场：cut/fade/dissolve
    notes: "情绪紧张，光线昏暗"
```

### 模型选型规则
1. **R2V**：镜中有主角/配角出场 → 需要 ref_image 指向角色参考图
2. **I2V**：紧接上一个镜头结尾的延续 → prev_frame 指向前一镜 
3. **T2V**：纯场景/空镜/过渡/无人物

### 种子规则
- 同场景同角色的镜头使用相同种子
- 新场景/新角色组合用新种子
- 种子号范围：1-999999

### 对话和音效
- 有对话的镜头：dialogue 填文本，dialogue_voice 填角色名
- 有音效的镜头：sfx_prompt 填音效描述（用于AI生成音效）

请确保覆盖所有剧本内容，不要遗漏任何剧情点。
优先保证叙事清晰，镜头数适中（每集15-30个镜头）。
"""

def main():
    p = arg_parser("生成分镜表")
    p.add_argument("--scripts", required=True)
    p.add_argument("--assets", required=True)
    p.add_argument("--episodes", type=int, required=True)
    p.add_argument("--aspect", default="16:9")
    p.add_argument("--duration", type=int, default=120)
    args = p.parse_args()

    ws = Path(args.workspace)

    # 收集剧本
    scripts_text = ""
    for f in sorted(Path(args.scripts).glob("*.md")):
        scripts_text += f"\n\n{read(str(f))}"

    # 收集角色信息
    manifest = yaml.safe_load(read(str(Path(args.assets) / "asset_manifest.yaml")))
    char_text = yaml.dump(manifest.get("characters", []), allow_unicode=True)
    scene_text = yaml.dump(manifest.get("scenes", []), allow_unicode=True)

    print(f"🎬 生成分镜表... ({args.episodes}集 × ~{args.duration}秒)")
    yaml_text = call_llm(
        STORYBOARD_PROMPT.format(
            scripts=scripts_text, characters=char_text, scenes=scene_text,
            episodes=args.episodes, duration=args.duration, aspect=args.aspect
        ),
        SYSTEM, max_tokens=8192, temperature=0.6
    )

    # 清理和保存
    if "```" in yaml_text:
        yaml_text = yaml_text.split("```")[1]
        if yaml_text.startswith("yaml"):
            yaml_text = yaml_text[4:]

    storyboard_dir = ws / "06_storyboard"
    storyboard_dir.mkdir(parents=True, exist_ok=True)

    # 验证并添加种子
    try:
        data = yaml.safe_load(yaml_text)
        shots = data.get("shots", [])

        # 种子分配：同场景+同角色 → 同种子
        seed_registry = SeedRegistry(str(ws / "07_shots" / "seeds.yaml"))
        for shot in shots:
            key = f"{shot.get('scene','')}_{','.join(sorted(shot.get('characters',[])))}"
            shot["seed"] = seed_registry.get(key)
        seed_registry.save()

        # 重新序列化
        yaml_text = yaml.dump(data, allow_unicode=True, default_flow_style=False)

        print(f"  ✅ {len(shots)} 个镜头 | 种子已分配")
    except Exception as e:
        print(f"  ⚠️ YAML解析警告: {e}，保存原始输出")

    output = storyboard_dir / "storyboard.yaml"
    write(str(output), yaml_text)
    print(f"  → {output}")
    print(f"🎉 Phase 3.1 完成")

if __name__ == "__main__":
    main()
