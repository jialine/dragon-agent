#!/usr/bin/env python3
"""
修仙短剧全自动管线 — 第1集
水墨国风 × WAN 2.7 T2V × edge_tts 配音
输出: 720*1280 竖屏 MP4, ~2分钟
"""
import json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path

# ===== FFMPEG setup =====
import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = FFMPEG.replace("ffmpeg", "ffprobe")

# ===== CONFIG =====
WAN_KEY = "sk-nve...ktjd"
WAN_BASE = "https://api.lingyuncx.com"
LLM_URL = "https://api.lingyuncx.com/v1/chat/completions"
LLM_MODEL = "qwen3.6-flash"
OUT_DIR = Path("/tmp/xianxia_drama")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SIGNOSS_URL = "https://api.andlapi.cn/signoss/upload"
SIGNOSS_KEY = "sk-0c12c3fc39512eafa1a76adb07d25849abd10eb4305be405"

# ===== HELPERS =====
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def signoss_upload(filepath, category="characters"):
    """Upload to OSS via signOSS proxy (no AK/SK needed on Dragon)."""
    cmd = ["curl", "-s", "--max-time", "60",
           "-X", "POST", SIGNOSS_URL,
           "-H", f"X-API-Key: {SIGNOSS_KEY}",
           "-F", f"category={category}",
           "-F", f"file=@{filepath}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=65)
    if r.returncode != 0:
        raise RuntimeError(f"signOSS upload failed: {r.stderr[:200]}")
    result = json.loads(r.stdout)
    if not result.get("success"):
        raise RuntimeError(f"signOSS error: {result.get('error', 'unknown')}")
    return result["files"][0]["url"]

def _curl_post(url, payload, api_key=WAN_KEY, timeout=120):
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer ***
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr[:200]}")
    return json.loads(r.stdout)

def _curl_get(url, api_key=WAN_KEY, timeout=30):
    cmd = ["curl", "-s", "--max-time", str(timeout), url,
           "-H", f"Authorization: Bearer ***
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
    return json.loads(r.stdout) if r.stdout.strip() else {}

def llm_chat(messages, max_tokens=4096, temperature=0.8):
    payload = {"model": LLM_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    r = _curl_post(LLM_URL, payload, WAN_KEY, timeout=120)
    content = r["choices"][0]["message"]["content"]
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content

def parse_json(text):
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        text = text[s:e+1]
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return json.loads(text)

def get_audio_duration(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())

# ===== PHASE 1: 50集大纲 + 第1集完整剧本 =====
log("PHASE 1: 编剧 — 50集大纲 + 第1集分镜")

world_prompt = """你是一位修仙小说大师。请严格按照以下JSON格式输出，不要任何解释：

{
  "title": "小说名（水墨国风修仙类，10字以内）",
  "world_setting": "世界观简介（50字）",
  "episodes": [
    {"ep": 1, "title": "第1集名", "summary": "本集剧情梗概（30字）", "highlight": "本集高潮"},
    {"ep": 2, "title": "第2集名", "summary": "续集梗概（30字）", "highlight": "本集高潮"}
  ]
}

要求：
- 世界观：修仙世界，水墨意境，有宗门、秘境、灵兽
- 主角：一个出身平凡但意志坚定的少年/少女，意外获得神秘传承
- 主线：从底层崛起，揭开上古秘密
- 融入中国传统文化元素：五行、八卦、丹道、剑修"""
# 只请求2集大纲，节省token

log("  生成大纲...")
outline = llm_chat([{"role": "user", "content": world_prompt}])
outline_data = parse_json(outline)
log(f"  标题: {outline_data['title']}")
log(f"  世界观: {outline_data['world_setting']}")

with open(OUT_DIR / "outline.json", "w") as f:
    json.dump(outline_data, f, ensure_ascii=False, indent=2)

# ===== PHASE 1B: 第1集详细分镜 =====
ep1_outline = outline_data["episodes"][0]
log(f"\n  第1集: {ep1_outline['title']}")

script_prompt = f"""你是分镜导演。请为第1集创作详细分镜脚本，严格按照JSON：

{{
  "episode": 1,
  "title": "{ep1_outline['title']}",
  "duration_sec": 120,
  "shots": [
    {{
      "id": 1,
      "scene": "场景描述（中文）",
      "duration_sec": 5,
      "camera": "特写/中景/远景/跟拍/推进",
      "dialogue": "角色台词（留空为旁白或无声）",
      "visual_prompt": "英文视频生成提示词（详细描述画面、动作、风格）",
      "style_note": "水墨国风标签"
    }}
  ]
}}

关键要求：
- 总共24个镜头，每个5秒（总计120秒=2分钟）
- 水墨国风 (Chinese ink wash painting style, traditional Chinese watercolor animation)
- 叙事清晰：开场→冲突→小高潮→钩子（悬念结尾）
- visual_prompt必须详细英文且包含"Chinese ink wash animation style, traditional watercolor, flowing brushstrokes, xianxia cultivation"
- 避免人物面部特写说话——用旁白+动作镜头代替
- 融入：竹林、山峦、云雾、剑光、丹炉、符箓等修仙元素"""

log("  创作第1集分镜...")
script_raw = llm_chat([{"role": "user", "content": script_prompt}], max_tokens=8192)
ep1_script = parse_json(script_raw)
log(f"  分镜完成: {len(ep1_script['shots'])} 个镜头")

with open(OUT_DIR / "ep1_script.json", "w") as f:
    json.dump(ep1_script, f, ensure_ascii=False, indent=2)

# ===== PHASE 2: 角色四要素图 =====
log("\nPHASE 2: 角色定妆 — 四要素图")

char_prompt = f"""根据以下剧本，输出3个核心角色的四要素设定，严格JSON：

{{
  "characters": [
    {{
      "name": "角色名（中文）",
      "role": "主角/反派/导师",
      "description": "外貌描述（30字）",
      "four_views": {{
        "front": "英文prompt：正面全身，白色背景，Chinese ink wash",
        "profile": "英文prompt：侧面，展示发型轮廓",
        "closeup": "英文prompt：面部大特写，眼神细节",
        "action": "英文prompt：动态姿势，战斗中/施法中，水墨特效"
      }}
    }}
  ]
}}

剧本：{ep1_outline['summary']}
世界：{outline_data['world_setting']}"""

log("  生成角色设定...")
chars_raw = llm_chat([{"role": "user", "content": char_prompt}])
chars_data = parse_json(chars_raw)
log(f"  {len(chars_data['characters'])} 个角色")

# Generate character images
log("  生成角色定妆照...")
for char in chars_data["characters"]:
    log(f"    {char['name']}...")
    for view_name, prompt in char["four_views"].items():
        try:
            full_prompt = f"Chinese ink wash painting, character design sheet, {prompt}, traditional watercolor style, clean white background, high quality"
            r = _curl_post(f"{WAN_BASE}/v1/images/generations", {
                "model": "wan2.7-image-pro",
                "prompt": full_prompt,
                "n": 1,
                "size": "720*1280"
            }, WAN_KEY, timeout=60)
            img_url = r["output"]["choices"][0]["message"]["content"][0]["image"]
            fname = f"char_{char['name']}_{view_name}.png"
            subprocess.run(["curl", "-s", "-o", str(OUT_DIR / fname), img_url, "--max-time", "30"], check=True)
            log(f"      ✓ {view_name}")
        except Exception as e:
            log(f"      ✗ {view_name}: {str(e)[:80]}")

# Upload to OSS via signOSS (keep local files, record URLs)
log("  上传角色图到 OSS...")
for char in chars_data["characters"]:
    char["oss_urls"] = {}
    for view_name in char["four_views"]:
        fname = f"char_{char['name']}_{view_name}.png"
        fpath = OUT_DIR / fname
        if fpath.exists():
            try:
                url = signoss_upload(str(fpath), category="characters")
                char["oss_urls"][view_name] = url
                log(f"    ✓ {char['name']}/{view_name}")
            except Exception as e:
                log(f"    ✗ {char['name']}/{view_name}: {str(e)[:80]}")
                char["oss_urls"][view_name] = None

with open(OUT_DIR / "characters.json", "w") as f:
    json.dump(chars_data, f, ensure_ascii=False, indent=2)

# ===== PHASE 3: 配音 =====
log("\nPHASE 3: 录音 — edge_tts")

import edge_tts, asyncio

async def gen_voice(text, path, voice="zh-CN-XiaoxiaoNeural"):
    await edge_tts.Communicate(text, voice).save(path)

audio_clips = []
for i, shot in enumerate(ep1_script["shots"]):
    text = shot.get("dialogue", "").strip()
    if not text:
        text = shot.get("scene", "场景过渡")
    path = OUT_DIR / f"voice_{i+1:02d}.mp3"
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(gen_voice(text, str(path)))
    dur = get_audio_duration(path)
    audio_clips.append({"id": i + 1, "path": str(path), "duration": dur, "text": text})
    log(f"  镜头{i+1}: {dur:.1f}s")

# ===== PHASE 4: WAN 2.7 T2V =====
log("\nPHASE 4: 视频生成 — WAN 2.7 T2V")

tasks = []
for i, shot in enumerate(ep1_script["shots"]):
    prompt = shot["visual_prompt"]
    if "ink wash" not in prompt.lower():
        prompt = f"Chinese ink wash animation, {prompt}, flowing brushstrokes, traditional watercolor"
    
    log(f"  提交镜头{i+1}/24: {prompt[:60]}...")
    try:
        r = _curl_post(f"{WAN_BASE}/v1/videos/generations", {
            "model": "wan2.7-t2v",
            "prompt": prompt,
            "size": "720*1280"
        }, WAN_KEY, timeout=30)
        tasks.append({"id": i + 1, "task_id": r["task_id"], "prompt": prompt})
        log(f"    task_id: {r['task_id'][:16]}...")
    except Exception as e:
        log(f"    ✗ {str(e)[:80]}")
        tasks.append({"id": i + 1, "task_id": None})

# Poll
log(f"\n  等待 {len([t for t in tasks if t['task_id']])} 个视频...")
video_files = {}
pending = {t["task_id"]: t for t in tasks if t["task_id"]}
deadline = time.time() + 900

while pending and time.time() < deadline:
    for task_id in list(pending.keys()):
        t = pending[task_id]
        try:
            s = _curl_get(f"{WAN_BASE}/v1/tasks/{task_id}", WAN_KEY, timeout=10)
            status = s.get("status", "")
            if status == "SUCCEEDED":
                vurl = s["result"]["video_url"]
                out = str(OUT_DIR / f"shot_{t['id']:02d}.mp4")
                subprocess.run(["curl", "-s", "-o", out, vurl, "--max-time", "60"], check=True)
                video_files[t["id"]] = out
                log(f"  ✓ 镜头{t['id']:02d}")
                del pending[task_id]
            elif status == "FAILED":
                log(f"  ✗ 镜头{t['id']:02d} 失败")
                del pending[task_id]
        except Exception as e:
            pass
    if pending:
        time.sleep(10)

log(f"  生成: {len(video_files)}/{len(tasks)} 成功")

# ===== PHASE 5: 合成 =====
log("\nPHASE 5: 合成")

# Build concat list
concat = OUT_DIR / "concat.txt"
with open(concat, "w") as f:
    for i in range(1, len(ep1_script["shots"]) + 1):
        if i in video_files:
            f.write(f"file '{video_files[i]}'\n")
        else:
            blk = str(OUT_DIR / f"black_{i:02d}.mp4")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                           "color=c=black:s=720x1280:d=5:r=24",
                           "-c:v", "libx264", "-preset", "fast", "-crf", "20", blk],
                          capture_output=True)
            f.write(f"file '{blk}'\n")

subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", str(OUT_DIR / "video_raw.mp4")], check=True)

# Audio concat
aconcat = OUT_DIR / "audio_concat.txt"
with open(aconcat, "w") as f:
    for c in audio_clips:
        f.write(f"file '{c['path']}'\n")
subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(aconcat),
                "-c:a", "libmp3lame", "-b:a", "128k", str(OUT_DIR / "audio_raw.mp3")], check=True)

# Final
output = str(OUT_DIR / f"xianxia_ep1.mp4")
subprocess.run([FFMPEG, "-y", "-i", str(OUT_DIR / "video_raw.mp4"),
                "-i", str(OUT_DIR / "audio_raw.mp3"),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                output], check=True)

size_mb = os.path.getsize(output) / (1024 * 1024)
log(f"\n{'='*50}")
log(f"✅ 第1集完成!")
log(f"  标题: {ep1_script['title']}")
log(f"  镜头: {len(video_files)}/{len(ep1_script['shots'])}")
log(f"  输出: {output}")
log(f"  大小: {size_mb:.1f} MB")
log(f"{'='*50}")
print(f"OUTPUT_FILE: {output}")