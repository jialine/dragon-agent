#!/usr/bin/env python3
"""drama_final_check.py — 终审单项检查"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

CHECKS = {
    "画面连贯性": "检查镜头切换是否流畅，跳帧/突兀转场/色彩不统一",
    "音画同步": "检查对话与口型、音效与画面是否对齐",
    "字幕准确性": "检查字幕内容与语音是否一致，时间轴是否准确",
    "整体观感": "检查整体节奏、色调、情绪是否统一",
}

def main():
    p = arg_parser("终审检查")
    p.add_argument("--dir", required=True)
    p.add_argument("--storyboard", default="")
    p.add_argument("--check", required=True)
    args = p.parse_args()

    final_dir = Path(args.dir)
    videos = list(final_dir.glob("*.mp4"))

    if not videos:
        write(str(final_dir / f"final_review_{args.check}.md"),
              f"# {args.check}\n\n⚠️ 无视频文件可检查")
        return

    check_desc = CHECKS.get(args.check, args.check)
    video_list = "\n".join([f"- {v.name} ({os.path.getsize(v)/1024/1024:.1f}MB)" for v in videos])

    review = call_llm(
        f"对以下短剧成品进行'{args.check}'审查：\n\n{check_desc}\n\n成品列表：\n{video_list}\n\n"
        f"请给出 PASS 或 NEED_FIX，以及具体意见。",
        "你是影视质量控制专家。", max_tokens=512
    )

    write(str(final_dir / f"final_review_{args.check}.md"), review)
    print(f"  ✅ {args.check} → {final_dir}/final_review_{args.check}.md")

if __name__ == "__main__":
    main()
