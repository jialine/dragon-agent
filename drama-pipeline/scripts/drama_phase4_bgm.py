#!/usr/bin/env python3
"""drama_phase4_bgm.py — BGM匹配"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

SYSTEM = "你是一个影视配乐师。为每个场景匹配BGM描述（用于AI音乐生成）。"

def main():
    p = arg_parser("BGM匹配")
    p.add_argument("--storyboard", required=True)
    p.add_argument("--scripts", default="")
    args = p.parse_args()

    out_dir = Path(args.workspace) / "09_composite" / "bgm"
    out_dir.mkdir(parents=True, exist_ok=True)

    storyboard = yaml.safe_load(read(args.storyboard))
    shots = storyboard.get("shots", [])

    # 按集提取唯一场景和情绪
    episodes = {}
    for shot in shots:
        ep = shot.get("episode", 1)
        if ep not in episodes:
            episodes[ep] = []
        episodes[ep].append({
            "scene": shot.get("scene", ""),
            "mood": shot.get("notes", ""),
            "duration": shot.get("duration_secs", 5)
        })

    for ep, ep_shots in episodes.items():
        bgm_prompt = f"为第{ep}集设计BGM：{json.dumps(ep_shots[:10], ensure_ascii=False)}"
        bgm_desc = call_llm(bgm_prompt, SYSTEM, max_tokens=512)
        write(str(out_dir / f"ep{ep:02d}_bgm.md"), bgm_desc)

        # 尝试AI音乐生成
        bgm_prompt_en = call_llm(
            f"Convert this BGM description to a single English prompt for AI music generation (Suno/Udio style). One line only:\n{bgm_desc}",
            "You are a music producer. Output one line only.", max_tokens=128
        )
        write(str(out_dir / f"ep{ep:02d}_prompt.txt"), bgm_prompt_en.strip())
        print(f"  🎵 EP{ep} BGM → {out_dir}/ep{ep:02d}_bgm.md")

    print(f"🎉 Phase 4.2 完成")

if __name__ == "__main__":
    main()
