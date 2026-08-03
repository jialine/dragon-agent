#!/usr/bin/env python3
import json, os, subprocess, sys, time, re, httpx
from datetime import datetime
from pathlib import Path

OUT_DIR = Path('/tmp/drama_eps')
OUT_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = 'sk-T7KJ6eiZHjTmJ4WjsZLPHUl0k8jPq8dx3jBS13NTJgK5z6ur'
API_BASE = 'https://api.andlapi.cn/v1'
LLM_URL = 'https://api.andlapi.cn/v1/chat/completions'
SIZE = '1080*1920'

def log(msg):
    t = datetime.now().strftime('%H:%M:%S')
    print(f'[{t}] {msg}', flush=True)

def api_post(url, payload, timeout=120):
    r = httpx.post(url, json=payload, headers={'Authorization': f'Bearer {API_KEY}'}, timeout=timeout, verify=False)
    return r.json()

def api_get(url, timeout=30):
    r = httpx.get(url, headers={'Authorization': f'Bearer {API_KEY}'}, timeout=timeout, verify=False)
    return r.json()

# ===== PHASE 1: Script =====
log('=' * 60)
log('PHASE 1: 双集剧本')
log('=' * 60)

prompt = """You are a viral short drama director. Create a 2-episode xianxia short drama. STRICT JSON:

{
  "title": "Series Title (Chinese)",
  "episodes": [
    {
      "ep": 1,
      "title": "Episode 1 Title",
      "shots": [
        {
          "id": 1,
          "visual_prompt": "English 80-120 words: character looks, costume, action, lighting, camera, cinematic quality",
          "dialogue": "One punchy line",
          "beat": "shock/power_up/face_slap/twist"
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

RULES:
- STRICTLY 6 shots per episode, no more no less
- Shot 1: shock hook, Shot 2-3: conflict, Shot 4-5: power + face slap, Shot 6: cliffhanger
- visual_prompt: hyperrealistic live-action, Arri Alexa, vertical 9:16, detailed
- NO animation, cartoon, 2D
- Dialogue: one sentence max per shot"""

log('  Calling LLM...')
raw = api_post(LLM_URL, {
    'model': 'gpt-4o',
    'messages': [{'role': 'user', 'content': prompt}],
    'temperature': 0.9
}, timeout=120)

content = raw['choices'][0]['message']['content']
s, e = content.find('{'), content.rfind('}')
script = json.loads(content[s:e+1])

log(f"  《{script['title']}》")
log(f"  Ep1: {script['episodes'][0]['title']} ({len(script['episodes'][0]['shots'])} shots)")
log(f"  Ep2: {script['episodes'][1]['title']} ({len(script['episodes'][1]['shots'])} shots)")

with open(OUT_DIR / 'script.json', 'w') as f:
    json.dump(script, f, ensure_ascii=False, indent=2)

# ===== PHASE 2: Video Generation =====
for ep_data in script['episodes']:
    ep_num = ep_data['ep']
    log(f"\n{'=' * 60}")
    log(f"PHASE 2: Ep{ep_num} — {ep_data['title']}")
    log(f"{'=' * 60}")

    tasks = []
    for shot in ep_data['shots']:
        vp = f"Hyperrealistic live-action, vertical 9:16, {shot['visual_prompt']}, Arri Alexa 65, anamorphic, shallow DOF, film grain, cinematic lighting, photorealistic skin, Chinese fantasy drama"
        log(f"  Submit shot{shot['id']:02d}...")
        try:
            r = api_post(f"{API_BASE}/video/generations", {
                'model': 'wan2.7-t2v', 'prompt': vp, 'size': SIZE, 'duration': 5
            })
            tid = r.get('task_id', r.get('data', {}).get('task_id', ''))
            tasks.append({'id': shot['id'], 'task_id': tid})
            log(f"    OK {tid[:20]}")
        except Exception as e:
            log(f"    FAIL {str(e)[:60]}")
            tasks.append({'id': shot['id'], 'task_id': None})

    valid = [t for t in tasks if t['task_id']]
    log(f"  Polling {len(valid)} tasks...")

    clips = {}
    for t in valid:
        tid = t['task_id']
        for i in range(30):
            time.sleep(10)
            try:
                sr = api_get(f"{API_BASE}/video/generations/{tid}")
                outer = sr.get('data', sr)
                d = outer.get('data', outer)
                status = d.get('status', outer.get('status', '?'))

                if i % 3 == 0:
                    log(f"    [{tid[:12]}] {status}")

                done_statuses = ('SUCCESS', 'SUCCEEDED', 'completed', 'succeeded', 'success', 'succeed')
                if status in done_statuses:
                    result_url = d.get('result_url', '') or d.get('video_url', '') or outer.get('result_url', '')
                    if result_url and ('http' in result_url or '/video' in str(result_url)):
                        if result_url.startswith('http'):
                            vurl = result_url
                        else:
                            vurl = f"https://api.andlapi.cn{result_url}"
                        clip = str(OUT_DIR / f"ep{ep_num}_shot{t['id']:02d}.mp4")
                        dl = httpx.get(vurl, timeout=120, verify=False)
                        if dl.status_code == 200 and len(dl.content) > 1000:
                            with open(clip, 'wb') as f:
                                f.write(dl.content)
                            clips[t['id']] = clip
                            log(f"    OK shot{t['id']:02d} ({len(dl.content)//1024}KB)")
                        else:
                            log(f"    Download failed: HTTP {dl.status_code} size={len(dl.content)}")
                    break

                fail_statuses = ('FAILURE', 'FAILED', 'failed', 'failure')
                if status in fail_statuses:
                    log(f"    FAIL shot{t['id']:02d}")
                    break
            except Exception as e:
                log(f"    ERR {str(e)[:40]}")

    # Fill missing with black
    final_clips = []
    for shot in ep_data['shots']:
        if shot['id'] in clips:
            final_clips.append(clips[shot['id']])
        else:
            blk = str(OUT_DIR / f"black_ep{ep_num}_{shot['id']:02d}.mp4")
            subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                'color=c=black:s=1080x1920:d=5:r=24',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28', blk],
                capture_output=True)
            final_clips.append(blk)
            log(f"    FILL black shot{shot['id']:02d}")

    # xfade
    log(f"  Compositing Ep{ep_num}...")
    n = len(final_clips)
    fade_dur = 0.5
    offset = 5.0 - fade_dur

    filter_parts = []
    for i in range(n):
        filter_parts.append(f"[{i}:v]settb=AVTB,fps=24,setpts=PTS-STARTPTS[v{i}]")

    filter_parts.append(f"[v0][v1]xfade=transition=fade:duration={fade_dur}:offset={offset}[xf1]")
    for i in range(2, n):
        filter_parts.append(f"[xf{i-1}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset}[xf{i}]")

    vf = ';'.join(filter_parts)
    inputs = []
    for clip in final_clips:
        inputs += ['-i', clip]

    raw_path = str(OUT_DIR / f"ep{ep_num}_raw.mp4")
    subprocess.run(['ffmpeg', '-y'] + inputs +
        ['-filter_complex', vf, '-map', f"[xf{n-1}]",
         '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
         '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
         raw_path], check=True)
    log(f"  Ep{ep_num} done")

log(f"\n{'=' * 60}")
log(f"ALL DONE: {script['title']} x 2 eps")

# Push to Feishu
import subprocess as sp
sp.run([sys.executable, str(Path(__file__).parent / 'drama_feishu_push.py'),
    f"🎬 《{script['title']}》", '2集修仙短剧·1080P竖屏', str(OUT_DIR)], timeout=60)
