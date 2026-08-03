#!/usr/bin/env python3
"""drama_phase1_story.py — 根据大纲扩展完整故事"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位短剧故事作家。将大纲扩展为有血有肉的完整故事。
要求：
- 每个情节有具体的场景、对话、动作描写
- 对话简洁有力，每句≤30字
- 情绪节奏明确，高潮点突出
- 适合短剧拍摄，每个描述都"可视可拍"
- 人物行为要符合其性格和动机"""

STORY_PROMPT = """将以下大纲扩展为完整的故事。

## 大纲
{outline}

## 世界观
{worldbuilding}

## 要求
- 共 {episodes} 集
- 写出完整的叙事，不需要分集格式（分集在后面单独做）
- 每场戏标注：[场景名 | 地点 | 时长估算]
- 关键对话用「角色名：台词」格式
- 情感转折点用 **加粗** 标注

## 扩展结构

### 开场钩子（详细）
（展开大纲里的钩子，写出具体的画面和第一句台词）

### 正文
（按大纲的三幕结构，逐一展开每个情节点）

### 结尾
（详细写出结尾画面和最后一句台词）

{extra}
"""

def main():
    p = arg_parser("扩展完整故事")
    p.add_argument("--outline", required=True, help="大纲文件路径")
    p.add_argument("--episodes", type=int, default=3)
    args = p.parse_args()

    outline_path = Path(args.outline)
    ws = outline_path.parent.parent  # outline_dir → workspace
    world_path = outline_path.parent / "worldbuilding.md"

    outline = read(str(outline_path))
    world = read(str(world_path)) if world_path.exists() else "(无)"

    extra = ""
    if args.episodes >= 5:
        extra = "\n注意：这是多集短剧，确保故事有足够的转折和钩子支撑{args.episodes}集。每集结尾留悬念。"

    print(f"📖 扩展完整故事（{args.episodes}集）...")
    story = call_llm(
        STORY_PROMPT.format(outline=outline, worldbuilding=world, episodes=args.episodes, extra=extra),
        SYSTEM, max_tokens=8192, temperature=0.8
    )

    story_dir = ws / "02_story"
    story_dir.mkdir(parents=True, exist_ok=True)
    write(str(story_dir / "full_story.md"), story)
    print(f"  ✅ 完整故事 → {story_dir}/full_story.md")
    print("🎉 Phase 1.2 完成")

if __name__ == "__main__":
    main()
