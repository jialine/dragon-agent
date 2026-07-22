#!/usr/bin/env python3
"""
Dragon 短剧管线 — 2集 × 真人写实修仙 × 1080P竖屏
走 api.andlapi.cn，ffmpeg 合成
"""
import json, os, subprocess, sys, time, re, httpx
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("/tmp/drama_eps")
OUT_DIR.mkdir(parents=True, exist_ok=True)

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

API_KEY = "sk-T7KJ6eiZHjTmJ4WjsZLPHUl0k8jPq8dx3jBS13NTJgK5z6ur"
API_BASE = "https://api.andlapi.cn/v1"
LLM_URL = "https://api.andlapi.cn/v1/chat/completions"

SIZE = "1080*1920"

def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)

def api_post(url, payload, timeout=120):
    r = httpx.post(url, json=payload, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=timeout, verify=False)
    return r.json()

def api_get(url, timeout=30):
    r = httpx.get(url, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=timeout, verify=False)
    return r.json()

# ===== SCRIPT GEN =====
log("=" * 60)
log("PHASE 1: 双集剧本")
log("=" * 60)

prompt = """You are a viral short drama director for Douyin/TikTok vertical series. Create a 2-episode hyperrealistic xianxia (cultivation) short drama in STRICT JSON:

{
  "title": "Series Title (Chinese)",
  "genre": "修仙/系统/逆袭",
  "logline": "One-sentence viral hook capturing the core reversal",
  "episodes": [
    {
      "ep": 1,
      "title": "Episode 1 Title",
      "hook": "The 3-second opening visual hook that grabs attention instantly",
      "shots": [
        {
          "id": 1,
          "scene": "Chinese scene description",
          "visual_prompt": "ULTRA-DETAILED English (80-150 words): character appearance (age, hair, face, expression), costume (fabric, color, style), action (dynamic pose, movement), environment (location, weather, atmosphere), lighting (direction, mood, color temperature), camera (shot type, movement, focal length), cinematic quality references (Arri Alexa, anamorphic, film grain)",
          "camera": "Extreme close-up / Close-up / Medium shot / Full shot / Wide / Dutch angle / Overhead",
          "emotion": "Emotional beat",
          "dialogue": "One impactful line",
          "beat": "shock / power_up / face_slap / reveal / twist / cliffhanger"
        }
      ]
    },
    {
      "ep": 2,
      "title": "Episode 2 Title",
      "shots": [...]
    }
  ]
}

VIRAL FORMULA:
- Shot 1: SHOCK HOOK (extreme situation, high stakes, 0-3 seconds must GRAB)
- Shot 2: CONFLICT ESCALATION (enemy appears, power gap revealed)
- Shot 3: SYSTEM ACTIVATION (power awakening, visual spectacle)
- Shot 4: POWER SURGE (cultivation breakthrough, energy explosion)
- Shot 5: FACE SLAP (underdog dominates, humiliates arrogant rival)
- Shot 6: CLIFFHANGER TWIST (bigger enemy appears / hidden truth / next level tease)

CHARACTER DESIGN:
- Male lead: 25-30, sharp features, disheveled hair in early shots → glowing eyes post-awakening, tattered modern clothes → flowing dark combat robe
- Female lead/rival: cold beauty, designer outfit, contemptuous expression → shocked/fearful

VISUAL STYLE REFERENCE:
- Like "The Untamed" meets "John Wick" in vertical format
- Realistic skin, sweat, dust, blood — not plastic CGI
- Dynamic Dutch angles for tension, smooth dolly for power moments
- Practical lighting: neon city lights + ethereal golden cultivation glow
- Shot on ARRI Alexa 65, anamorphic lenses, shallow depth of field

CRITICAL RULES:
- visual_prompt MUST be 80-150 English words with ALL details
- Each shot must have a clear emotional BEAT that drives the story forward
- NO animation, NO 2D, NO cartoon — photorealistic live-action ONLY
- Vertical 9:16 framing — subject centered, cinematic composition
- Dialogue must be PUNCHY — one sentence max, like subtitle text
- Two episodes must form a complete arc with escalating stakes"""

log("  调用LLM生成剧本...")
raw = api_post(LLM_URL, {
    "model": "qwen3.6-flash",
    "messages": [{"role": "user", "content": prompt}],
    "temperature": 0.95, "max_tokens": 4096
})
content = raw["choices"][0]["message"]["content"]
content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
s, e = content.find("{"), content.rfind("}")
script = json.loads(content[s:e+1])

log(f"  《{script['title']}》")
log(f"  第1集: {script['episodes'][0]['title']} ({len(script['episodes'][0]['shots'])}镜)")
log(f"  第2集: {script['episodes'][1]['title']} ({len(script['episodes'][1]['shots'])}镜)")

with open(OUT_DIR / "script.json", "w") as f:
    json.dump(script, f, ensure_ascii=False, indent=2)

# ===== VIDEO GEN =====
for ep_data in script["episodes"]:
    ep_num = ep_data["ep"]
    log(f"\n{"=" * 60}")
    log(f"PHASE 2: 第{ep_num}集 — {ep_data['title']}")
    log(f"{"=" * 60}")
    
    tasks = []
    for shot in ep_data["shots"]:
        vp = f"Hyperrealistic live-action cinematic, vertical 9:16 portrait, {shot['visual_prompt']}, Arri Alexa 65 anamorphic, shallow DOF, volumetric lighting, film grain, 8K mastered, Chinese fantasy drama aesthetic, photorealistic skin pores, practical effects, no CGI plastic look"
        log(f"  提交镜{shot['id']:02d}...")
        try:
            r = api_post(f"{API_BASE}/video/generations", {
                "model": "wan2.7-t2v", "prompt": vp, "size": SIZE, "duration": 5
            })
            tid = r.get("task_id", "")
            if not tid:
                d = r.get("data", {})
                tid = d.get("task_id", "")
            tasks.append({"id": shot["id"], "task_id": tid})
            log(f"    ✓ {tid[:20]}")
        except Exception as e:
            log(f"    ✗ {str(e)[:60]}")
            tasks.append({"id": shot["id"], "task_id": None})

    # Poll
    valid_tasks = [t for t in tasks if t["task_id"]]
    log(f"  等待 {len(valid_tasks)} 个视频生成 (~3min each)...")
    
    clips = {}
    for t in valid_tasks:
        tid = t["task_id"]
        for i in range(30):
            time.sleep(10)
            try:
                sr = api_get(f"{API_BASE}/video/generations/{tid}")
                outer = sr.get("data", sr)
                d = outer.get("data", outer)
                status = d.get("status", outer.get("status", "?"))
                if i % 3 == 0:
                    log(f"    [{tid[:12]}] {status}")
                if status in ("SUCCESS", "SUCCEEDED", "completed", "succeeded", "success", "succeed"):
                    result_url = d.get("result_url", "") or d.get("video_url", "") or outer.get("result_url", "")
                    if result_url:
                        # result_url may be relative or absolute
                        if result_url.startswith("http"):
                            vurl = result_url
                        else:
                            vurl = f"https://api.andlapi.cn{result_url}"

                        if vurl:
                            clip_path = str(OUT_DIR / f"ep{ep_num}_shot{t['id']:02d}.mp4")
                            dl = httpx.get(vurl, timeout=120, verify=False)
                            with open(clip_path, "wb") as f:
                                f.write(dl.content)
                            clips[t["id"]] = clip_path
                            log(f"    ✓ 镜{t['id']:02d} 完成 ({os.path.getsize(clip_path)//1024}KB)")
                    break
                elif status in ("FAILURE", "FAILED", "failed", "failure"):
                    log(f"    ✗ 镜{t['id']:02d} 失败")
                    break
            except Exception as e:
                log(f"    ⚠ {str(e)[:40]}")
    
    # Fill missing with black
    final_clips = []
    for shot in ep_data["shots"]:
        if shot["id"] in clips:
            final_clips.append(clips[shot["id"]])
        else:
            blk = str(OUT_DIR / f"black_ep{ep_num}_{shot['id']:02d}.mp4")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                f"color=c=black:s=1080x1920:d=5:r=24",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", blk],
                capture_output=True)
            final_clips.append(blk)
            log(f"    ⚠ 镜{shot['id']:02d} 用黑屏填充")
    
    # xfade
    log(f"  合成第{ep_num}集 ({len(final_clips)} 镜)...")
    n = len(final_clips)
    fade_dur = 0.5
    offset = 5.0 - fade_dur
    
    filter_parts = []
    for i in range(n):
        filter_parts.append(f"[{i}:v]settb=AVTB,fps=24,setpts=PTS-STARTPTS[v{i}]")
    
    # First xfade: [v0][v1] -> [xf1]
    filter_parts.append(f"[v0][v1]xfade=transition=fade:duration={fade_dur}:offset={offset}[xf1]")
    # Chain remaining
    for i in range(2, n):
        filter_parts.append(f"[xf{i-1}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset}[xf{i}]")
    vf_filter = ";".join(filter_parts)
    inputs = []
    for clip in final_clips:
        inputs += ["-i", clip]
    
    raw_path = str(OUT_DIR / f"ep{ep_num}_raw.mp4")
    subprocess.run([FFMPEG, "-y"] + inputs +
        ["-filter_complex", vf_filter,
         "-map", f"[xf{n-1}]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         raw_path], check=True)
    log(f"  第{ep_num}集合成完成")

log(f"\n{"=" * 60}")
log(f"✅ 全剧完成！")
log(f"  《{script['title']}》共2集")
log(f"  文件: {OUT_DIR}/")
for ep_num in [1, 2]:
    path = OUT_DIR / f"ep{ep_num}_raw.mp4"
    if path.exists():
        sz = os.path.getsize(path) / (1024*1024)
        log(f"  第{ep_num}集: ep{ep_num}_raw.mp4 ({sz:.1f}MB)")
    import subprocess
    subprocess.run([sys.executable, str(Path(__file__).parent / "drama_feishu_push.py"),
        f"🎬 《{script[chr(39)+chr(39)]}",
        "2集修仙短剧·1080P竖屏",
        str(OUT_DIR)], timeout=60)

# ===== Feishu Push =====
def feishu_send(title, text, video_paths):
    Send completion notification + videos to Feishu user
    import httpx
    APP_ID = cli_aab694730bb8dcd6
    APP_SECRET = 3lxuIiJiTwxwYaXYXhdSZUe4YdY1ssZP
    OPEN_ID = ou_640a24ce510f7fa22bab74af213e4cbb
    
    # Get tenant token
    r = httpx.post(https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal,
        json={app_id: APP_ID, app_secret: APP_SECRET}, verify=False)
    token = r.json().get(tenant_access_token, )
    if not token:
        log(f ⚠ 飞书token获取失败)
        return
    
    # Send text message
    r = httpx.post(https://open.feishu.cn/open-apis/im/v1/messages,
        params={receive_id_type: open_id},
        headers={Authorization: fBearer {token}, Content-Type: application/json},
        json={
            receive_id: OPEN_ID,
            msg_type: text,
            content: json.dumps({text: f{title}
{text}})
        }, verify=False)
    
    # Send video files
    for vp in video_paths:
        if Path(vp).exists():
            r = httpx.post(https://open.feishu.cn/open-apis/im/v1/messages,
                params={receive_id_type: open_id},
                headers={Authorization: fBearer {token}, Content-Type: application/json},
                json={
                    receive_id: OPEN_ID,
                    msg_type: file,
                    content: json.dumps({file_key: })  # placeholder
                }, verify=False)
    
    log(f ✓ 已推送到飞书)

# Collect final video paths
final_videos = []
for ep_num in [1, 2]:
    path = str(OUT_DIR / fep{ep_num}_raw.mp4)
    if os.path.exists(path):
        final_videos.append(path)

# Push to Feishu
feishu_send(
    f🎬 《{script[title]}》生成完成,
    f第1集+第2集，1080P竖屏修仙短剧,
    final_videos
)
