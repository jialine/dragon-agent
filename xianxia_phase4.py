#!/usr/bin/env python3
"""Phase 4+5 only: Video generation + composite from existing script"""
import json, os, subprocess, sys, time
from datetime import datetime

# Load existing assets
OUT_DIR = "/tmp/xianxia_drama"
with open(f"{OUT_DIR}/ep1_script.json") as f:
    script = json.load(f)

# FFMPEG
import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")

WAN_KEY = "sk-nveh4vt2hm1ewfthbazmm6a3nsxdktjd"
WAN_BASE = "https://api.lingyuncx.com"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def _curl_post(url, payload, timeout=120):
    import json
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer {WAN_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout)

def _curl_get(url, timeout=30):
    import json
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer {WAN_KEY}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout) if r.stdout.strip() else {}

log(f"PHASE 4: 视频生成 — {script['title']}, {len(script['shots'])} 镜头")

# Submit all
tasks = []
for shot in script["shots"]:
    prompt = shot["visual_prompt"]
    if "ink wash" not in prompt.lower():
        prompt = f"Chinese ink wash animation, {prompt}, flowing brushstrokes, traditional watercolor"
    
    log(f"  提交镜头{shot['id']}/24...")
    try:
        r = _curl_post(f"{WAN_BASE}/v1/videos/generations", {
            "model": "wan2.7-t2v", "prompt": prompt, "size": "720*1280"
        }, timeout=30)
        tasks.append({"id": shot["id"], "task_id": r["task_id"]})
        log(f"    task={r['task_id'][:16]}")
    except Exception as e:
        log(f"    ✗ {str(e)[:60]}")
        tasks.append({"id": shot["id"], "task_id": None})

# Poll
log(f"\n  等待 {sum(1 for t in tasks if t['task_id'])} 个视频...")
video_files = {}
pending = {t["task_id"]: t for t in tasks if t["task_id"]}
deadline = time.time() + 1200

while pending and time.time() < deadline:
    done_now = 0
    for task_id in list(pending.keys()):
        t = pending[task_id]
        try:
            s = _curl_get(f"{WAN_BASE}/v1/tasks/{task_id}", timeout=10)
            status = s.get("status", "")
            if status == "SUCCEEDED":
                vurl = s["result"]["video_url"]
                out = f"{OUT_DIR}/shot_{t['id']:02d}.mp4"
                subprocess.run(["curl", "-s", "-o", out, vurl, "--max-time", "60"], check=True)
                video_files[t["id"]] = out
                done_now += 1
                del pending[task_id]
            elif status == "FAILED":
                log(f"  ✗ 镜头{t['id']:02d} FAILED")
                del pending[task_id]
        except:
            pass
    if done_now:
        log(f"  ✓ {done_now} done, {len(pending)} remaining")
    if pending:
        time.sleep(10)

log(f"  完成: {len(video_files)}/{len(tasks)}")

# Composite
log("PHASE 5: 合成")
with open(f"{OUT_DIR}/concat.txt", "w") as f:
    for i in range(1, 25):
        if i in video_files:
            f.write(f"file '{video_files[i]}'\n")
        else:
            blk = f"{OUT_DIR}/black_{i:02d}.mp4"
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                           "color=c=black:s=720x1280:d=5:r=24",
                           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", blk],
                          capture_output=True)
            f.write(f"file '{blk}'\n")

output = f"{OUT_DIR}/xianxia_ep1.mp4"
subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
                "-i", f"{OUT_DIR}/concat.txt",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                output], check=True)

size_mb = os.path.getsize(output) / (1024*1024)
log(f"✅ 完成: {output} ({size_mb:.1f}MB)")
print(f"OUTPUT_FILE: {output}")
