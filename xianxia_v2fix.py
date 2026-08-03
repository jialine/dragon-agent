#!/usr/bin/env python3
"""V2FIX: Simplified FFmpeg zoompan for imageio_ffmpeg compatibility"""
import json, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/tmp/xianxia_drama")
import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

WAN_KEY = "sk-nveh4vt2hm1ewfthbazmm6a3nsxdktjd"
WAN_BASE = "https://api.lingyuncx.com"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def _post(url, payload, timeout=60):
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer {WAN_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout)

with open(OUT_DIR / "ep1_script.json") as f:
    script = json.load(f)

# Check if images already exist
existing = sorted(OUT_DIR.glob("img_*.png"))
img_ids = {int(p.stem.split("_")[1]): str(p) for p in existing}
log(f"已有图片: {len(img_ids)}/{len(script['shots'])}")

# Fill missing (only 镜13 failed)
for shot in script["shots"]:
    sid = shot["id"]
    if sid in img_ids:
        continue
    prompt = shot["visual_prompt"]
    if "ink wash" not in prompt.lower():
        prompt = f"Chinese ink wash painting, {prompt}, flowing brushstrokes"
    log(f"  补镜{sid:02d}: {prompt[:50]}...")
    try:
        r = _post(f"{WAN_BASE}/v1/images/generations", {
            "model": "wan2.7-image-pro", "prompt": prompt, "n": 1, "size": "720*1280"
        }, timeout=60)
        img_url = r["output"]["choices"][0]["message"]["content"][0]["image"]
        fpath = str(OUT_DIR / f"img_{sid:02d}.png")
        subprocess.run(["curl", "-s", "-o", fpath, img_url, "--max-time", "30"], check=True)
        img_ids[sid] = fpath
        log(f"    ✓")
    except Exception as e:
        log(f"    ✗ {str(e)[:60]}")

existing = sorted(OUT_DIR.glob("img_*.png"))
img_ids = {int(p.stem.split("_")[1]): str(p) for p in existing}
log(f"图片就绪: {len(img_ids)}")

# PHASE 2: FFmpeg motion (simplified)
log("\nPHASE 2: FFmpeg 运镜")

MOTION = {
    "zoom_in":  "zoompan=z=zoom+0.0018:d=1:s=720x1280:fps=24",
    "zoom_out": "zoompan=z=1.35-0.0015*n:d=1:s=720x1280:fps=24",
    "pan_left": "zoompan=z=1.2:d=1:x='iw/2-(iw/zoom/2)-n*2':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
    "pan_right":"zoompan=z=1.2:d=1:x='iw/2-(iw/zoom/2)+n*2':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
    "static":   "zoompan=z=1.0:d=1:s=720x1280:fps=24",
}

def pick(cam):
    c = cam.lower()
    if any(w in c for w in ["推", "zoom", "特写", "近"]): return "zoom_in"
    if any(w in c for w in ["拉", "远"]): return "zoom_out"
    if any(w in c for w in ["跟", "摇", "左", "移"]): return "pan_left"
    if any(w in c for w in ["右"]): return "pan_right"
    return "zoom_in"

videos = {}
for shot in script["shots"]:
    sid = shot["id"]
    if sid not in img_ids:
        continue
    motion = pick(shot.get("camera", ""))
    vf = MOTION[motion]
    out = str(OUT_DIR / f"vid_{sid:02d}.mp4")
    dur = shot.get("duration_sec", 5)
    subprocess.run([FFMPEG, "-y", "-loop", "1", "-i", img_ids[sid],
        "-vf", vf, "-t", str(dur),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", out], capture_output=True, check=True)
    videos[sid] = out
    log(f"  镜{sid:02d}: {motion} {dur}s ✓")

# PHASE 3: Voice
log("\nPHASE 3: 配音")
import edge_tts, asyncio

async def gen_v(text, path):
    await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(path)

audio = {}
for shot in script["shots"]:
    sid = shot["id"]
    text = shot.get("dialogue", "").strip() or shot.get("scene", "")
    path = str(OUT_DIR / f"voice_{sid:02d}.mp3")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(gen_v(text, path))
    audio[sid] = path
    log(f"  配音{sid:02d} ✓")

# PHASE 4: Composite
log("\nPHASE 4: 合成")

with open(OUT_DIR / "concat.txt", "w") as f:
    for i in range(1, 25):
        if i in videos:
            f.write(f"file '{videos[i]}'\n")
        else:
            blk = str(OUT_DIR / f"black_{i:02d}.mp4")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                "color=c=black:s=720x1280:d=5:r=24",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", blk],
                capture_output=True)
            f.write(f"file '{blk}'\n")

subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
    "-i", str(OUT_DIR / "concat.txt"),
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-pix_fmt", "yuv420p", str(OUT_DIR / "video_raw.mp4")], check=True)

with open(OUT_DIR / "audio_concat.txt", "w") as f:
    for i in range(1, 25):
        if i in audio:
            f.write(f"file '{audio[i]}'\n")

subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
    "-i", str(OUT_DIR / "audio_concat.txt"),
    "-c:a", "libmp3lame", "-b:a", "128k",
    str(OUT_DIR / "audio_raw.mp3")], check=True)

output = str(OUT_DIR / "xianxia_ep1_final.mp4")
subprocess.run([FFMPEG, "-y", "-i", str(OUT_DIR / "video_raw.mp4"),
    "-i", str(OUT_DIR / "audio_raw.mp3"),
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-c:a", "aac", "-b:a", "128k",
    "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    output], check=True)

size_mb = os.path.getsize(output) / (1024*1024)
log(f"\n✅ 第1集《{script['title']}》完成!")
log(f"  镜头: {len(videos)}/{len(script['shots'])}")
log(f"  大小: {size_mb:.1f}MB")
log(f"  文件: {output}")
print(f"DONE: {output}")
