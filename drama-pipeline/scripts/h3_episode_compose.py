#!/usr/bin/env python3
"""
H3 集合成器 — 英文对白 TTS + 无BGM合成
========================================
把 h3_batch_gen.py 生成的镜头按顺序 concat，配英文对白 (edge-tts)。

流程: 镜头归一化 -> concat 成集 -> 英文对白 edge-tts -> 时间戳混音 -> 合成
输出: {project}_EP{ep}_final.mp4 (纯对白, 无BGM)

用法:
  python3 h3_episode_compose.py --project 猩族纪元 --episode 9 --spec episode_09.json

episode spec 格式 (含 dialogue):
{
  "project": "Ember",
  "episode": 9,
  "ep_title": "Southeast Bound",
  "width": 1920, "height": 1080, "fps": 24,
  "voices": {"SILVERBACK": "en-US-GuyNeural", "KUNLUN": "en-US-ChristopherNeural"},
  "shots": [
    {
      "shot_number": 1,
      "duration": 8,
      "video": "/abs/path/EP09_S01_V001_T01_s123.mp4",
      "dialogue": [
        {"speaker": "SILVERBACK", "text": "You came alone.", "start": 1.5}
      ]
    }
  ]
}
"""
import json
import os
import subprocess
import tempfile
import argparse
from pathlib import Path

VOICE_MAP = {
    "SILVERBACK": "en-US-GuyNeural",       # 银背 - 低沉有力
    "KUNLUN": "en-US-ChristopherNeural",   # 昆仑 - 权威
    "SHEPHERD": "en-US-BrianNeural",       # 深度牧者 - 冰冷
    "LUZHENG": "en-US-AndrewNeural",       # 陆铮 - 温和坚毅
    "SUWANQING": "en-US-JennyNeural",      # 苏晚晴 - 女
    "MORRIS": "en-US-RogerNeural",         # 莫里斯 - 沧桑
    "PRESIDENT": "en-US-ChristopherNeural",
    "NARRATOR": "en-US-AriaNeural",        # 旁白 - 女声新闻腔
    "SOLDIER": "en-US-EricNeural",
}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def tts(text, voice, out_mp3, rate="-2%"):
    """edge-tts 生成英文对白."""
    subprocess.run(["edge-tts", "--voice", voice, "--rate", rate,
                    "--text", text, "--write-media", out_mp3],
                   check=True, capture_output=True)
    return out_mp3


def normalize(video, out, width, height, fps):
    """归一化: 统一分辨率/帧率 (无音轨)."""
    vf = (f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p")
    r = run(["ffmpeg", "-y", "-v", "error", "-i", video,
             "-vf", vf, "-an", "-c:v", "libx264", "-preset", "fast",
             "-crf", "18", out])
    return out if os.path.exists(out) else None


def compose_episode(spec, workdir):
    """核心: 镜头 concat + 英文对白混音 + 合成."""
    project = spec["project"]
    ep = spec["episode"]
    width = spec.get("width", 1920)
    height = spec.get("height", 1080)
    fps = spec.get("fps", 24)
    voices = {**VOICE_MAP, **(spec.get("voices") or {})}
    shots = spec["shots"]

    tmp = Path(workdir)
    tmp.mkdir(parents=True, exist_ok=True)
    norm_dir = tmp / "norm"
    tts_dir = tmp / "tts"
    norm_dir.mkdir(exist_ok=True)
    tts_dir.mkdir(exist_ok=True)

    # 1. 归一化所有镜头 + 计算全局时间戳
    norm_files = []
    tts_jobs = []  # (mp3, global_start)
    t = 0.0
    for shot in shots:
        video = shot["video"]
        norm = norm_dir / f"S{shot['shot_number']:02d}.mp4"
        normalize(video, str(norm), width, height, fps)
        if not norm.exists():
            raise RuntimeError(f"归一化失败: {video}")
        norm_files.append(str(norm))
        for line in shot.get("dialogue", []):
            gstart = t + line.get("start", 0.5)
            voice = voices.get(line["speaker"], "en-US-GuyNeural")
            mp3 = tts_dir / f"d{shot['shot_number']:02d}_{len(tts_jobs)}.mp3"
            tts(line["text"], voice, str(mp3))
            tts_jobs.append((str(mp3), gstart))
        t += shot.get("duration", 8)

    total_dur = t

    # 2. concat 成集 (concat demuxer, 零质量损失)
    concat_txt = tmp / "concat.txt"
    with open(concat_txt, "w") as f:
        for nf in norm_files:
            f.write(f"file '{nf}'\n")
    body = tmp / "body.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(concat_txt), "-c", "copy", str(body)])
    if not body.exists():
        raise RuntimeError("concat 失败")

    # 3. 英文对白混音: anullsrc 基轨 + 逐句 adelay + amix (无BGM)
    if tts_jobs:
        inputs = ["-f", "lavfi", "-i",
                  f"anullsrc=r=44100:cl=stereo:d={total_dur}"]
        fc_parts = []
        for idx, (mp3, gstart) in enumerate(tts_jobs):
            inputs += ["-i", mp3]
            ms = int(gstart * 1000)
            fc_parts.append(f"[{idx+1}:a]adelay={ms}|{ms},apad=whole_dur={total_dur}s[a{idx}]")
        mix_inputs = "".join(f"[a{idx}]" for idx in range(len(tts_jobs)))
        fc = ";".join(fc_parts) + f";[0:a]{mix_inputs}amix=inputs={len(tts_jobs)+1}:normalize=0,volume={len(tts_jobs)+1}.0[out]"
        dialogue = tmp / "dialogue.aac"
        run(["ffmpeg", "-y", "-v", "error"] + inputs +
            ["-filter_complex", fc, "-map", "[out]",
             "-c:a", "aac", "-b:a", "192k", str(dialogue)])
        if not dialogue.exists():
            raise RuntimeError("对白混音失败")
        audio_input = ["-i", str(dialogue)]
        amap = ["-map", "0:v", "-map", "1:a"]
    else:
        # 无对白镜头: 保持静音
        audio_input = ["-f", "lavfi", "-i",
                       f"anullsrc=r=44100:cl=stereo:d={total_dur}"]
        amap = ["-map", "0:v", "-map", "1:a"]

    # 4. 合成
    final = tmp / f"{project}_EP{ep:02d}_final.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(body)] + audio_input +
        amap + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", str(final)])
    if not final.exists():
        raise RuntimeError("最终合成失败")
    return str(final), total_dur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="episode spec JSON")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--output", default=None, help="最终输出路径")
    args = ap.parse_args()

    with open(args.spec) as f:
        spec = json.load(f)

    workdir = args.workdir or f"/tmp/h3compose_{spec['project']}_EP{spec['episode']:02d}"
    final, dur = compose_episode(spec, workdir)
    if args.output:
        subprocess.run(["cp", final, args.output])
        final = args.output
    print(f"=== 集合成完成: {final} ({dur:.1f}s) ===")