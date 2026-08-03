#!/usr/bin/env python3
"""
WAN 2.7 T2V 真人短剧 — 1分钟 × 无缝转场
12 clips × 5s = 60s, 1920×1080
"""
import json, os, subprocess, sys, time, re
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/tmp/drama_real")
OUT_DIR.mkdir(parents=True, exist_ok=True)

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

WAN_KEY = "sk-nveh4vt2hm1ewfthbazmm6a3nsxdktjd"
WAN_BASE = "https://api.lingyuncx.com"
LLM_URL = "https://api.lingyuncx.com/v1/chat/completions"
LLM_MODEL = "qwen3.6-flash"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def _curl(url, payload=None, timeout=120):
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer {WAN_KEY}"]
    if payload:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload, ensure_ascii=False), "-X", "POST"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout) if r.stdout.strip() else {}

# ===== PHASE 1: 剧本 =====
log("PHASE 1: 1分钟真人短剧剧本")

prompt = """你是一位短剧导演。创作一段1分钟真人短剧（12个5秒镜头），输出严格JSON：

{
  "title": "短剧标题",
  "genre": "类型",
  "logline": "一句话梗概",
  "shots": [
    {
      "id": 1,
      "scene": "场景描述（中文，10字内）",
      "visual_prompt": "英文AI视频生成指令（详细描述画面、运镜、光线、人物动作、服装、场景细节）",
      "camera": "特写/中景/远景/跟拍",
      "transition": "cut/crossfade",
      "emotion": "情绪"
    }
  ]
}

要求：
- 真人写实风格（cinematic photorealistic, Chinese drama quality）
- 现代都市或古装剧情均可
- 有起承转合的完整剧情弧线
- 每段visual_prompt必须极详细英文，包含：人物外貌、动作、服装、场景、光线、运镜、电影质感
- 12个镜头，每个5秒，总计60秒
- 不要动画/水墨，要真人电影质感"""

log("  生成剧本...")
raw = _curl(LLM_URL, {
    "model": LLM_MODEL,
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.9, "max_tokens": 4096
}, timeout=120)
content = raw["choices"][0]["message"]["content"]
content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
s, e = content.find("{"), content.rfind("}")
script = json.loads(content[s:e+1])
log(f"  《{script['title']}》 — {script['logline']}")
log(f"  镜头: {len(script['shots'])}")

with open(OUT_DIR / "script.json", "w") as f:
    json.dump(script, f, ensure_ascii=False, indent=2)

# ===== PHASE 2: 生成视频 =====
log("\nPHASE 2: WAN 2.7 T2V 批量生成")

# Submit all
tasks = []
for shot in script["shots"]:
    prompt = f"Cinematic photorealistic, {shot['visual_prompt']}, 4K film quality, Chinese drama aesthetic, professional lighting"
    log(f"  提交镜{shot['id']:02d}/12...")
    try:
        r = _curl(f"{WAN_BASE}/v1/videos/generations", {
            "model": "wan2.7-t2v", "prompt": prompt, "size": "1920*1080"
        }, timeout=30)
        tasks.append({"id": shot["id"], "task_id": r["task_id"]})
        log(f"    task={r['task_id'][:16]}")
    except Exception as e:
        log(f"    ✗ {str(e)[:60]}")
        tasks.append({"id": shot["id"], "task_id": None})

# Poll
log(f"\n  等待 {sum(1 for t in tasks if t['task_id'])} 个视频生成...")
clips = {}
pending = {t["task_id"]: t for t in tasks if t["task_id"]}
deadline = time.time() + 1200

while pending and time.time() < deadline:
    for task_id in list(pending.keys()):
        t = pending[task_id]
        try:
            s = _curl(f"{WAN_BASE}/v1/tasks/{task_id}", timeout=10)
            status = s.get("status", "")
            if status == "SUCCEEDED":
                vurl = s["result"]["video_url"]
                out = str(OUT_DIR / f"clip_{t['id']:02d}.mp4")
                subprocess.run(["curl", "-s", "-o", out, vurl, "--max-time", "60"], check=True)
                clips[t["id"]] = out
                log(f"  ✓ 镜{t['id']:02d}")
                del pending[task_id]
            elif status == "FAILED":
                log(f"  ✗ 镜{t['id']:02d} FAILED")
                del pending[task_id]
        except:
            pass
    if pending:
        time.sleep(15)

log(f"  完成: {len(clips)}/12")

# ===== PHASE 3: 无缝衔接 =====
log("\nPHASE 3: 无缝转场合成")

# Fill missing clips with closest available or black
final_clips = []
for i in range(1, 13):
    if i in clips:
        final_clips.append(clips[i])
    else:
        # Use nearest available clip
        nearest = min(clips.keys(), key=lambda k: abs(k - i)) if clips else None
        if nearest:
            final_clips.append(clips[nearest])
        else:
            blk = str(OUT_DIR / f"black_{i:02d}.mp4")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                "color=c=black:s=1920x1080:d=5:r=24",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", blk],
                capture_output=True)
            final_clips.append(blk)

# Build xfade filter for seamless transitions
# xfade transition: crossfade 0.5s between each clip
n = len(final_clips)
fade_dur = 0.5
offset = 5.0 - fade_dur  # each clip 5s, overlap 0.5s

# Build complex filter
filter_parts = []
for i, clip in enumerate(final_clips):
    filter_parts.append(f"[{i}:v]settb=AVTB,fps=24,setpts=PTS-STARTPTS[v{i}]")

# xfade chain
xfade_chain = f"[v0]"
for i in range(1, n):
    xfade_chain = f"[xf{i-1}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset}[xf{i}]"
    filter_parts.append(xfade_chain)

vf_filter = ";".join(filter_parts)
inputs = []
for clip in final_clips:
    inputs += ["-i", clip]

subprocess.run([FFMPEG, "-y"] + inputs +
    ["-filter_complex", vf_filter,
     "-map", f"[xf{n-1}]",
     "-c:v", "libx264", "-preset", "medium", "-crf", "18",
     "-pix_fmt", "yuv420p", "-movflags", "+faststart",
     str(OUT_DIR / "drama_raw.mp4")], check=True)

# ===== PHASE 4: 配音 =====
log("\nPHASE 4: 配音")

import edge_tts, asyncio

# One narration for the whole drama
narration = f"{script['title']}。{script['logline']}。" + "。".join(
    s["scene"] for s in script["shots"][:6])
narration = narration[:300]

async def gen_voice(text, path, voice="zh-CN-XiaoxiaoNeural"):
    await edge_tts.Communicate(text, voice).save(path)

voice_path = str(OUT_DIR / "narration.mp3")
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
loop.run_until_complete(gen_voice(narration, voice_path))
log(f"  配音完成: {narration[:50]}...")

# ===== FINAL =====
output = str(OUT_DIR / "drama_final.mp4")
subprocess.run([FFMPEG, "-y",
    "-i", str(OUT_DIR / "drama_raw.mp4"),
    "-i", voice_path,
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-c:a", "aac", "-b:a", "128k",
    "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    output], check=True)

size_mb = os.path.getsize(output) / (1024*1024)
dur = float(subprocess.run([FFMPEG, "-i", output], capture_output=True, text=True, stderr=subprocess.PIPE).stderr.split("Duration: ")[1].split(",")[0]) if True else 60

log(f"\n✅ 《{script['title']}》完成!")
log(f"  镜头: {len(clips)}/12")
log(f"  大小: {size_mb:.1f}MB")
log(f"  文件: {output}")
print(f"DONE: {output}")
