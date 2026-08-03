#!/usr/bin/env python3
"""WAN 2.7 T2V 竖屏真人修仙短剧 — 50集大纲 + 第1集"""
import json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/tmp/xianxia_live")
OUT_DIR.mkdir(parents=True, exist_ok=True)

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

WAN_KEY = "sk-nveh4vt2hm1ewfthbazmm6a3nsxdktjd"
WAN_BASE = "https://api.lingyuncx.com"
LLM_URL = f"{WAN_BASE}/v1/chat/completions"
LLM_MODEL = "qwen3.6-flash"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def _curl(url, payload=None, timeout=120):
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer {WAN_KEY}"]
    if payload:
        cmd += ["-H", "Content-Type: application/json",
                "-d", json.dumps(payload, ensure_ascii=False)]
    else:
        cmd += ["-X", "GET"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout) if r.stdout.strip() else {}

# ===== PHASE 1: 50集大纲 =====
log("PHASE 1: 编剧 — 50集修仙真人剧大纲")

outline_prompt = """你是资深编剧。创作一部真人修仙短剧，50集大纲，严格JSON：

{
  "title": "剧名（修仙类，5字以内）",
  "logline": "全剧一句话梗概",
  "protagonist": {"name": "主角名", "actor_style": "演员形象描述"},
  "antagonist": {"name": "反派名", "actor_style": "演员形象描述"},
  "episodes": [
    {"ep": 1, "title": "集名", "conflict": "冲突梗概", "cliffhanger": "悬念"},
    {"ep": 2, "title": "集名", "conflict": "冲突梗概", "cliffhanger": "悬念"}
  ]
}

要求：
- 真人实拍风格，非动画
- 修仙世界观：宗门、秘境、灵兽、丹道、剑修
- 主角底层崛起，反派步步紧逼
- 每集有独立冲突+钩子悬念
- 前50集完整弧线"""

log("  生成50集大纲...")
raw = _curl(LLM_URL, {
    "model": LLM_MODEL, "messages": [{"role":"user","content":outline_prompt}],
    "temperature": 0.9, "max_tokens": 8192
})
content = raw["choices"][0]["message"]["content"]
content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
s, e = content.find("{"), content.rfind("}")
outline = json.loads(content[s:e+1])
log(f"  《{outline['title']}》— {outline['logline']}")
log(f"  主角: {outline['protagonist']['name']} · 反派: {outline['antagonist']['name']}")

with open(OUT_DIR / "outline.json","w") as f:
    json.dump(outline, f, ensure_ascii=False, indent=2)

# ===== PHASE 2: 第1集分镜 =====
ep1 = outline["episodes"][0]
log(f"\n  第1集: {ep1['title']} — {ep1['conflict']}")

script_prompt = f"""你是导演。为第1集创作真人拍摄脚本，严格JSON：

{{
  "episode": 1,
  "title": "{ep1['title']}",
  "duration_sec": 60,
  "shots": [
    {{
      "id": 1,
      "scene": "中文场景描述",
      "visual_prompt": "英文AI视频指令（真人电影质感，详细描述场景/动作/光线/运镜/服装/表情，cinematic photorealistic）",
      "emotion": "情绪",
      "camera": "运镜方式"
    }}
  ]
}}

要求：
- 20个镜头，每个3秒，总计60秒
- 真人实拍电影质感（cinematic photorealistic, Chinese drama, 4K film quality）
- 修仙元素融入真人场景：古装、剑术、特效光、灵气、符箓
- 剧情弧线：开场→冲突升级→小高潮→悬念钩子
- visual_prompt详细英文，不含\"animation/cartoon/ink wash\"等动画词
- 主角: {outline['protagonist']['name']}（{outline['protagonist']['actor_style']}）
- 反派: {outline['antagonist']['name']}（{outline['antagonist']['actor_style']}）"""

log("  创作分镜...")
raw = _curl(LLM_URL, {
    "model": LLM_MODEL, "messages": [{"role":"user","content":script_prompt}],
    "temperature": 0.9, "max_tokens": 8192
})
content = raw["choices"][0]["message"]["content"]
content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
s, e = content.find("{"), content.rfind("}")
script = json.loads(content[s:e+1])
log(f"  分镜: {len(script['shots'])} 镜")

with open(OUT_DIR / "script.json","w") as f:
    json.dump(script, f, ensure_ascii=False, indent=2)

# ===== PHASE 3: WAN 2.7 T2V 批量生成 =====
log(f"\nPHASE 3: WAN 2.7 竖屏生成 ({len(script['shots'])} 镜)")

tasks = []
for shot in script["shots"]:
    prompt = f"Cinematic photorealistic, Chinese drama, {shot['visual_prompt']}, 4K quality, professional lighting"
    log(f"  提交镜{shot['id']:02d}/{len(script['shots'])}...")
    try:
        r = _curl(f"{WAN_BASE}/v1/videos/generations", {
            "model": "wan2.7-t2v",
            "prompt": prompt,
            "duration": 3,
            "parameters": {"resolution": "720P", "ratio": "9:16", "prompt_extend": True},
            "watermark": False
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
                out = str(OUT_DIR / f"shot_{t['id']:02d}.mp4")
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

log(f"  完成: {len(clips)}/{len(script['shots'])}")

# ===== PHASE 4: 合成 =====
log("\nPHASE 4: 合成")

# Build concat list (fill missing with nearest)
final = []
for i in range(1, len(script['shots'])+1):
    if i in clips:
        final.append(clips[i])
    elif clips:
        nearest = min(clips.keys(), key=lambda k: abs(k-i))
        final.append(clips[nearest])
        log(f"  镜{i:02d} 缺失，用镜{nearest:02d}替补")

with open(OUT_DIR / "concat.txt", "w") as f:
    for clip in final:
        f.write(f"file '{clip}'\n")

output = str(OUT_DIR / "xianxia_ep1.mp4")
subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
    "-i", str(OUT_DIR / "concat.txt"),
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    output], check=True)

size_mb = os.path.getsize(output)/(1024*1024)
log(f"\n{'='*50}")
log(f"✅ 第1集《{script['title']}》完成!")
log(f"  {len(clips)}/{len(script['shots'])} 镜 | {size_mb:.1f}MB")
log(f"  {output}")
log(f"{'='*50}")
print(f"DONE:{output}")
