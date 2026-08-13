#!/usr/bin/env python3
"""drama_phase4_intro.py — 片头 + 集数标题制作"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

def main():
    p = arg_parser("片头制作")
    p.add_argument("--title", required=True)
    p.add_argument("--episodes", type=int, required=True)
    p.add_argument("--aspect", default="16:9")
    args = p.parse_args()

    out_dir = Path(args.workspace) / "09_composite" / "intros"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 片头：2秒标题画面
    for ep in range(1, args.episodes + 1):
        intro_file = out_dir / f"intro_ep{ep:02d}.mp4"
        epnum_file = out_dir / f"ep_number_{ep:02d}.mp4"

        if intro_file.exists():
            continue

        # 用 T2V 生成标题画面
        prompt = f"cinematic title card, '{args.title}', dark atmospheric background, dramatic lighting, {args.aspect} aspect ratio, 2 seconds"
        print(f"🎬 EP{ep} 片头...")
        try:
            tid = submit_video(prompt, "wan2.7-t2v", "1280*720")
            result = poll_video(tid, max_wait=120)
            if result.get("url"):
                download_video(result["url"], str(intro_file))
                print(f"  ✅ {intro_file}")
        except Exception as e:
            print(f"  ⚠️ {e}")

        # 集数：2秒
        ep_prompt = f"cinematic text overlay, '第{ep}集', elegant typography, dark background, animation, {args.aspect}, 2 seconds"
        try:
            tid = submit_video(ep_prompt, "wan2.7-t2v", "1280*720")
            result = poll_video(tid, max_wait=120)
            if result.get("url"):
                download_video(result["url"], str(epnum_file))
                print(f"  ✅ {epnum_file}")
        except Exception as e:
            print(f"  ⚠️ {e}")

    print(f"🎉 Phase 4.1 完成")

if __name__ == "__main__":
    main()
