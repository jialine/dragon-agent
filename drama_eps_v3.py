#!/usr/bin/env python3
import json, os, re, sys, time
from datetime import datetime
from pathlib import Path
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
API_KEY = os.environ['ANDLAPI_KEY']
API_BASE = 'https://api.andlapi.cn/v1'
LLM_MODEL = 'deepseek-v3.2'
WAN_MODEL = 'wan2.7-t2v'
SIZE = '1080*1920'
SECS = 5
OUT_DIR = Path('/tmp/drama_eps')
OUT_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)

def api_post(url, payload, timeout=120):
    r = httpx.post(url, json=payload, headers={'Authorization': f'Bearer {API_KEY}'}, timeout=timeout, verify=False)
    return r.json()

def api_get(url, timeout=30):
    r = httpx.get(url, headers={'Authorization': f'Bearer {API_KEY}'}, timeout=timeout, verify=False)
    return r.json()

def fix_ids(obj):
    if isinstance(obj, dict):
        if 'id' in obj and not isinstance(obj['id'], int):
            obj['id'] = int(re.sub(r'[^0-9]', '', str(obj['id'])) or '1')
        for v in obj.values():
            fix_ids(v)
    elif isinstance(obj, list):
        for item in obj:
            fix_ids(item)
    return obj

def fix_id_pre(s):
    # Fix "id":非数字  before JSON parsing
    return re.sub(r'"id":\s*[^0-9\s,}\]]+', lambda m: '"id": ' + re.sub(r'[^0-9]', '', m.group(0).split(':')[1]) or '1', s)

def json_fix(s):
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    return s

# ===== PHASE 1 =====
log('=' * 60)
log('PHASE 1: Script')
log('=' * 60)

# Ep 1
log('  LLM for Ep1...')
ep1_resp = api_post(f'{API_BASE}/chat/completions', {
    'model': LLM_MODEL,
    'messages': [{'role': 'user', 'content': (
        'Output ONLY valid JSON: '
        '{"title":"Title","episodes":[{"ep":1,"title":"Ep1","shots":['
        '{"id":1,"visual_prompt":"50-70 words English cinematic prompt","dialogue":"one Chinese line","beat":"shock_hook"}'
        ']}]} '
        'Rules: 6 shots. Beat order: shock_hook, conflict, escalation, power_reveal, face_slap, cliffhanger. '
        'Visual: hyperrealistic live-action, Arri Alexa 65, vertical 9:16, dramatic lighting, anamorphic lens, film grain. NO animation, cartoon, 2D.'
    )}],
    'temperature': 0.7, 'max_tokens': 8192
}, timeout=180)

c1 = ep1_resp['choices'][0]['message']['content']
s1, e1 = c1.find('{'), c1.rfind('}')
js1 = fix_id_pre(json_fix(c1[s1:e1+1]))
try:
    scr1 = fix_ids(json.loads(js1))
except:
    cut = js1.rfind('}')
    js1 = fix_id_pre(json_fix(js1[:cut+1] + ']}'))
    scr1 = fix_ids(json.loads(js1))
ep1 = scr1['episodes'][0]
log(f'  Ep1: {ep1["title"]} ({len(ep1["shots"])} shots)')

# Ep 2
log('  LLM for Ep2...')
last_dialogue = ep1['shots'][-1]['dialogue']
ep2_resp = api_post(f'{API_BASE}/chat/completions', {
    'model': LLM_MODEL,
    'messages': [{'role': 'user', 'content': (
        f'Continue "{scr1["title"]}". Ep1 ended: {last_dialogue}. '
        'Output ONLY valid JSON: '
        '{"episodes":[{"ep":2,"title":"Ep2 Title","shots":['
        '{"id":7,"visual_prompt":"50-70 words English","dialogue":"one Chinese line","beat":"twist"}'
        ']}]} '
        '6 shots (id 7-12). Beats: aftermath, twist, counter_attack, ultimate_power, revenge, open_ending.'
    )}],
    'temperature': 0.7, 'max_tokens': 8192
}, timeout=180)

c2 = ep2_resp['choices'][0]['message']['content']
s2, e2 = c2.find('{'), c2.rfind('}')
js2 = fix_id_pre(json_fix(c2[s2:e2+1]))
try:
    scr2 = fix_ids(json.loads(js2))
except:
    cut = js2.rfind('}')
    js2 = fix_id_pre(json_fix(js2[:cut+1] + ']}'))
    scr2 = fix_ids(json.loads(js2))
ep2 = scr2['episodes'][0]
log(f'  Ep2: {ep2["title"]} ({len(ep2["shots"])} shots)')

# Merge
script = {'title': scr1['title'], 'episodes': [ep1, ep2]}

with open(OUT_DIR / 'script.json', 'w', encoding='utf-8') as f:
    json.dump(script, f, ensure_ascii=False, indent=2)

# ===== PHASE 2 =====
all_shots = []
for ep in script['episodes']:
    for s in ep['shots']:
        all_shots.append({
            'ep': ep['ep'],
            'label': f'E{ep["ep"]}S{s["id"]:02d}',
            'prompt': s['visual_prompt'],
            'dialogue': s['dialogue'],
            'beat': s['beat']
        })

log('')
log('=' * 60)
log(f'PHASE 2: {len(all_shots)} Videos')
log('=' * 60)

tasks = {}
for s in all_shots:
    log(f'  Submit {s["label"]}...')
    resp = api_post(f'{API_BASE}/video/generations', {
        'model': WAN_MODEL, 'prompt': s['prompt'], 'size': SIZE, 'seconds': str(SECS)
    }, timeout=30)
    tid = None
    for path in ['data.task_id', 'data.id', 'task_id', 'id']:
        d = resp
        for k in path.split('.'):
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                d = None
                break
        if d:
            tid = d
            break
    if tid:
        tasks[tid] = s
        log(f'    OK {tid}')
    else:
        log(f'    FAIL: {json.dumps(resp, ensure_ascii=False)[:150]}')

log(f'  Waiting for {len(tasks)} videos...')
completed = {}
deadline = time.time() + len(tasks) * 120

while len(completed) < len(tasks) and time.time() < deadline:
    for tid, info in list(tasks.items()):
        if tid in completed:
            continue
        try:
            sr = api_get(f'{API_BASE}/video/generations/{tid}', timeout=15)
            status = ''
            url = ''
            if isinstance(sr, dict):
                d = sr.get('data', sr)
                if isinstance(d, dict):
                    status = d.get('status', '')
                    url = d.get('result_url') or d.get('video_url') or d.get('url', '')
            if status in ('succeeded', 'completed', 'done'):
                vp = OUT_DIR / f'{info["label"]}.mp4'
                if url:
                    dl = httpx.get(url, timeout=120, verify=False)
                    if dl.status_code == 200 and len(dl.content) > 1024:
                        vp.write_bytes(dl.content)
                completed[tid] = str(vp)
                log(f'    DONE {info["label"]}')
            elif status in ('failed', 'error', 'cancelled'):
                completed[tid] = 'FAILED'
                log(f'    FAIL {info["label"]}')
        except Exception as e:
            log(f'    ERR {info["label"]}: {e}')
    time.sleep(10)

# ===== SUMMARY =====
log('')
log('=' * 60)
log(f'DONE: {len(completed)}/{len(tasks)} videos')
log(f'Output: {OUT_DIR}')
for tid, info in tasks.items():
    log(f'  {info["label"]}: {completed.get(tid, "pending")}')
