#!/usr/bin/env python3
"""drama_phase2_extract.py — 从剧本提取角色/场景/道具清单"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = "你是一个影视制片助理。从剧本中提取所有制作资源需求。输出必须是合法的 YAML。"

EXTRACT_PROMPT = """从以下剧本中提取完整的制作资源清单。输出 YAML 格式（只输出 YAML，不要 Markdown 代码块）：

```yaml
characters:
  - name: "角色名"
    role: "主角/配角/龙套"
    gender: "男/女"
    age_range: "年龄段"
    traits: ["性格关键词1", "性格关键词2"]
    appearance: "外貌描述（50字以内，适合AI生图）"
    costume: "服装风格"
    signature: "标志性特征/动作"
    scenes: ["epXX_sXX"]  # 出场的分镜ID

scenes:
  - id: "scene_01"
    name: "场景名"
    location: "地点描述"
    time: "时间（白天/夜晚/黄昏）"
    mood: "氛围（3-5个关键词）"
    lighting: "光线描述"
    props: ["道具1", "道具2"]
    episodes: [1, 2]  # 在哪几集出现

props:
  - name: "道具名"
    description: "详细描述"
    scenes: ["scene_01"]
    essential: true/false  # 关键道具
```

剧本内容：
{scripts}

请确保每个角色、场景、道具都被覆盖，不要遗漏。
"""

def main():
    p = arg_parser("提取资源清单")
    p.add_argument("--scripts", required=True, help="剧本目录")
    args = p.parse_args()

    # 收集所有剧本
    scripts_text = ""
    for f in sorted(Path(args.scripts).glob("*.md")):
        scripts_text += f"\n\n{read(str(f))}"

    print("📋 提取资源清单...")
    yaml_text = call_llm(EXTRACT_PROMPT.format(scripts=scripts_text), SYSTEM, max_tokens=4096)

    # 清理并验证 YAML
    if "```" in yaml_text:
        yaml_text = yaml_text.split("```")[1]
        if yaml_text.startswith("yaml"):
            yaml_text = yaml_text[4:]

    output = Path(args.workspace) / "05_assets" / "asset_manifest.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    write(str(output), yaml_text.strip())

    # 打印摘要
    try:
        import yaml_lib as yaml
        data = yaml.safe_load(yaml_text)
        chars = len(data.get("characters", []))
        scenes = len(data.get("scenes", []))
        props = len(data.get("props", []))
        print(f"  ✅ {chars} 角色 | {scenes} 场景 | {props} 道具")
    except Exception:
        print(f"  ✅ 保存到 {output}")
    print(f"🎉 Phase 2.1 完成")

if __name__ == "__main__":
    main()
