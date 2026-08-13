#!/usr/bin/env python3
"""drama_phase4_subtitles.py — SRT 字幕生成（时间轴对齐）"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

def main():
    p = arg_parser("字幕生成")
    p.add_argument("--scripts", required=True)
    p.add_argument("--audio", required=True)
    p.add_argument("--storyboard", required=True)
    args = p.parse_args()

    out_dir = Path(args.workspace) / "09_composite" / "subtitles"
    out_dir.mkdir(parents=True, exist_ok=True)

    storyboard = yaml.safe_load(read(args.storyboard))
    shots = storyboard.get("shots", [])

    # 按集聚合
    episodes = {}
    for shot in shots:
        ep = shot.get("episode", 1)
        if ep not in episodes:
            episodes[ep] = {"cumulative": 0.0, "subtitles": []}
        dur = shot.get("duration_secs", 5)
        dialogue = shot.get("dialogue", "")
        if dialogue:
            start = episodes[ep]["cumulative"]
            end = start + dur
            episodes[ep]["subtitles"].append({
                "index": len(episodes[ep]["subtitles"]) + 1,
                "start": start,
                "end": end,
                "text": dialogue,
                "voice": shot.get("dialogue_voice", "")
            })
        episodes[ep]["cumulative"] += dur

    # 生成 SRT
    for ep, data in episodes.items():
        srt_lines = []
        for sub in data["subtitles"]:
            srt_lines.append(str(sub["index"]))
            start_ts = f"{int(sub['start']//3600):02d}:{int((sub['start']%3600)//60):02d}:{int(sub['start']%60):02d},{int((sub['start']%1)*1000):03d}"
            end_ts = f"{int(sub['end']//3600):02d}:{int((sub['end']%3600)//60):02d}:{int(sub['end']%60):02d},{int((sub['end']%1)*1000):03d}"
            srt_lines.append(f"{start_ts} --> {end_ts}")
            srt_lines.append(f"{sub['voice']}: {sub['text']}" if sub['voice'] else sub['text'])
            srt_lines.append("")

        srt_content = "\n".join(srt_lines)
        write(str(out_dir / f"ep{ep:02d}.srt"), srt_content)
        print(f"  📝 EP{ep}: {len(data['subtitles'])} 条字幕")

    print(f"🎉 Phase 4.3 完成")

if __name__ == "__main__":
    main()
