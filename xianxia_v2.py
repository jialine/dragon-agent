#!/usr/bin/env python3
"""WAN 2.7 Image → FFmpeg Ken Burns 水墨短剧 (bypass broken T2V endpoint)"""
import json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/tmp/xianxia_drama")
OUT_DIR.mkdir(parents=True, exist_ok=True)

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")

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

# Load script
with open(OUT_DIR / "ep1_script.json") as f:
    script = json.load(f)

log(f"=== 水墨问道录 第1集: {script['title']} ===")
log(f"WAN 2.7 Image → 24 镜 → FFmpeg 运镜")

# PHASE 1: Generate all 24 images
log("\nPHASE 1: 图片生成 (WAN 2.7 Image Pro)")
images = {}
for shot in script["shots"]:
    sid = shot["id"]
    prompt = shot["visual_prompt"]
    if "ink wash" not in prompt.lower():
        prompt = f"Chinese ink wash painting, {prompt}, flowing brushstrokes, traditional watercolor, high quality"

    log(f"  镜{sid:02d}/24: {prompt[:60]}...")
    try:
        r = _post(f"{WAN_BASE}/v1/images/generations", {
            "model": "wan2.7-image-pro",
            "prompt": prompt,
            "n": 1, "size": "720*1280"
        }, timeout=60)
        img_url = r["output"]["choices"][0]["message"]["content"][0]["image"]
        fpath = str(OUT_DIR / f"img_{sid:02d}.png")
        subprocess.run(["curl", "-s", "-o", fpath, img_url, "--max-time", "30"], check=True)
        images[sid] = fpath
        log(f"    ✓ {os.path.getsize(fpath)//1024}KB")
    except Exception as e:
        log(f"    ✗ {str(e)[:60]}")

log(f"\n  图片: {len(images)}/{len(script['shots'])} 成功")

# PHASE 2: FFmpeg Ken Burns motion per shot
log("\nPHASE 2: FFmpeg 运镜 (Ken Burns)")
# Motion presets
PRESETS = {
    "zoom_in": "zoompan=z='min(zoom+0.0018,1.35)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
    "zoom_out": "zoompan=z='max(1.4-0.0015*n,1.0)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
    "pan_left": "zoompan=z='1.15':d=1:x='max(iw-iw/zoom-2,0)*n/120':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
    "pan_right": "zoompan=z='1.15':d=1:x='2+iw/zoom*n/120':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
    "static": "zoompan=z='1.0':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=24",
}

def pick_motion(camera_str):
    c = camera_str.lower()
    if "推" in c or "zoom" in c or "特写" in c:
        return "zoom_in"
    elif "拉" in c or "远" in c:
        return "zoom_out"
    elif "跟" in c or "摇" in c or "移动" in c:
        return "pan_left"
    elif "固定" in c:
        return "static"
    return "zoom_in"  # default

videos = {}
for shot in script["shots"]:
    sid = shot["id"]
    if sid not in images:
        continue
    
    motion = pick_motion(shot.get("camera", ""))
    vf = PRESETS[motion]
    out = str(OUT_DIR / f"vid_{sid:02d}.mp4")
    dur = shot.get("duration_sec", 5)
    
    subprocess.run([
        FFMPEG, "-y", "-loop", "1", "-i", images[sid],
        "-vf", vf, "-t", str(dur),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", out
    ], capture_output=True, check=True)
    videos[sid] = out
    log(f"  镜{sid:02d}: {motion} {dur}s ✓")

# PHASE 3: Voice (edge_tts)
log("\nPHASE 3: 配音 (edge_tts)")
import edge_tts, asyncio

async def gen_voice(text, path):
    await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural").save(path)

audio_clips = []
for shot in script["shots"]:
    sid = shot["id"]
    text = shot.get("dialogue", "").strip() or shot.get("scene", "场景过渡")
    path = str(OUT_DIR / f"voice_{sid:02d}.mp3")
    
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(gen_voice(text, path))
    audio_clips.append({"id": sid, "path": path})
    log(f"  配音{sid:02d} ✓")

# PHASE 4: Composite
log("\nPHASE 4: 合成")

# Video concat
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
                "-pix_fmt", "yuv420p",
                str(OUT_DIR / "video_raw.mp4")], check=True)

# Audio concat
with open(OUT_DIR / "audio_concat.txt", "w") as f:
    for c in audio_clips:
        f.write(f"file '{c['path']}'\n")
subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
                "-i", str(OUT_DIR / "audio_concat.txt"),
                "-c:a", "libmp3lame", "-b:a", "128k",
                str(OUT_DIR / "audio_raw.mp3")], check=True)

# Final mix
output = str(OUT_DIR / "xianxia_ep1_final.mp4")
subprocess.run([FFMPEG, "-y",
                "-i", str(OUT_DIR / "video_raw.mp4"),
                "-i", str(OUT_DIR / "audio_raw.mp3"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                output], check=True)

size_mb = os.path.getsize(output) / (1024*1024)
log(f"\n{'='*50}")
log(f"✅ 第1集《{script['title']}》完成!")
log(f"  镜头: {len(videos)}/{len(script['shots'])}")
log(f"  输出: {output}")
log(f"  大小: {size_mb:.1f} MB")
log(f"{'='*50}")
print(f"DONE: {output}")
