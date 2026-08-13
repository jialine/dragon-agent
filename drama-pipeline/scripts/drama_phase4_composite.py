#!/usr/bin/env python3
"""drama_phase4_composite.py — 最终合成（ffmpeg）"""

import sys, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

def main():
    p = arg_parser("视频合成")
    p.add_argument("--shots", required=True)
    p.add_argument("--intros", required=True)
    p.add_argument("--bgm", default="")
    p.add_argument("--subtitles", default="")
    p.add_argument("--storyboard", required=True)
    p.add_argument("--aspect", default="16:9")
    args = p.parse_args()

    ws = Path(args.workspace)
    final_dir = ws / "10_final"
    final_dir.mkdir(parents=True, exist_ok=True)

    storyboard = yaml.safe_load(read(args.storyboard))
    shots = storyboard.get("shots", [])

    # 按集聚合镜头
    episodes = {}
    for shot in shots:
        ep = shot.get("episode", 1)
        if ep not in episodes:
            episodes[ep] = []
        episodes[ep].append(shot)

    for ep, ep_shots in episodes.items():
        output = str(final_dir / f"{ws.name}_ep{ep:02d}.mp4")

        # 构建 concat 文件列表
        concat_file = str(ws / "09_composite" / f"concat_ep{ep:02d}.txt")
        file_list = []

        # 片头
        intro = f"{args.intros}/intro_ep{ep:02d}.mp4"
        epnum = f"{args.intros}/ep_number_{ep:02d}.mp4"
        if os.path.exists(intro):
            file_list.append(intro)
        if os.path.exists(epnum):
            file_list.append(epnum)

        # 分镜头
        for shot in ep_shots:
            sid = shot.get("id", "")
            shot_path = f"{args.shots}/ep{ep:02d}/{sid}.mp4"
            if os.path.exists(shot_path):
                file_list.append(shot_path)
            else:
                print(f"  ⚠️ 缺失: {shot_path}")

        with open(concat_file, "w") as f:
            for fp in file_list:
                f.write(f"file '{fp}'\n")

        if not file_list:
            print(f"  ⚠️ EP{ep}: 无可用镜头，跳过")
            continue

        # ffmpeg 合成
        print(f"🔧 合成 EP{ep} ({len(file_list)} 个素材)...")

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file]

        # 字幕
        srt_file = f"{args.subtitles}/ep{ep:02d}.srt"
        if os.path.exists(srt_file):
            cmd += ["-vf", f"subtitles={srt_file}:force_style='FontName=Noto Sans CJK SC,FontSize=24,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&'"]

        # BGM
        bgm_file = f"{args.bgm}/ep{ep:02d}_bgm.mp3"
        if args.bgm and os.path.exists(bgm_file):
            cmd += ["-i", bgm_file, "-filter_complex", "[0:a]volume=0.8[a0];[1:a]volume=0.3[a1];[a0][a1]amix=inputs=2:duration=first[aout]", "-map", "0:v", "-map", "[aout]"]

        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-c:a", "aac", "-b:a", "128k", output]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                size_mb = os.path.getsize(output) / (1024*1024)
                print(f"  ✅ EP{ep}: {output} ({size_mb:.1f}MB)")
            else:
                print(f"  ❌ EP{ep} 合成失败: {result.stderr[:200]}")
        except Exception as e:
            print(f"  ❌ EP{ep}: {e}")

    print(f"🎉 Phase 4.4 完成")

if __name__ == "__main__":
    main()
