#!/usr/bin/env python3
"""drama_phase1_episodes.py — 分集编剧"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = """你是一位短剧编剧。依据故事和世界观，写出每集的剧本。
格式：标准影视剧本，含画面描述+对话。
要求：
- 每集必须有一个完整的情绪弧线（起 → 承 → 转 → 钩子）
- 对话简洁，每句≤30字，符合角色性格
- 画面描述具体、可视、可拍摄
- 每集结尾必须留钩子/悬念，让观众想看下一集
- 标注关键镜头的景别和时长"""

EPISODE_PROMPT = """根据完整故事，写出第 {ep_num} 集的剧本。

## 完整故事
{story}

## 每集时长
约 {duration} 秒

## 输出格式

# 第{ep_num}集：《本集小标题》

## 前情提要（3秒）
（上一集发生了什么，一句话）

## 本集钩子（5秒）
（本集开篇，立刻抓住观众。具体的画面+第一句台词）

---
（以下为正式剧本，每场戏包含画面描述+对话）

【场景X | 地点 | 时间 | 时长估算】

[画面描述：镜头语言、氛围、关键动作、色调]

角色名：（表情/动作提示）对话内容

---

## 片尾钩子（5秒）
（本集最后一帧画面 + 最后一句台词/音效，留下悬念）

## 本集情绪曲线
（简单描述：开场情绪值→中段→结尾）
"""

def main():
    p = arg_parser("分集编剧")
    p.add_argument("--story", required=True, help="完整故事文件路径")
    p.add_argument("--episodes", type=int, required=True)
    p.add_argument("--duration", type=int, default=120, help="每集秒数")
    args = p.parse_args()

    story = read(args.story)
    script_dir = Path(args.workspace) / "04_script"
    script_dir.mkdir(parents=True, exist_ok=True)

    for ep in range(1, args.episodes + 1):
        print(f"✍️ 第 {ep}/{args.episodes} 集...")
        script = call_llm(
            EPISODE_PROMPT.format(ep_num=ep, story=story, duration=args.duration),
            SYSTEM, max_tokens=4096, temperature=0.8
        )
        write(str(script_dir / f"episode_{ep:02d}.md"), script)
        print(f"  ✅ → episode_{ep:02d}.md")

    print(f"🎉 {args.episodes} 集剧本完成 → {script_dir}/")

if __name__ == "__main__":
    main()
