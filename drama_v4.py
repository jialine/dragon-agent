import os, json, httpx, re, sys, time
from datetime import datetime
from pathlib import Path
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

def json_fix(s):
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    return s

def fix_id_pre(s):
    return re.sub(r'"id":\s*[^0-9\s,}\]]+', lambda m: '"id": ' + (re.sub(r'[^0-9]', '', m.group(0).split(':')[1]) or '1'), s)

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

# ===== PHASE 1: Script =====
log('=' * 60)
log('PHASE 1: Script')
log('=' * 60)

log('  LLM for Ep1...')
r1 = api_post(f'{API_BASE}/chat/completions', {
    'model': LLM_MODEL,
    'messages': [{'role': 'user', 'content': (
        '你是修仙短剧导演。输出纯JSON：'
        '{"title":"剧名","episodes":[{"ep":1,"title":"第1集","shots":['
        '{"id":1 (整数), "visual_prompt":"50-70词英文","dialogue":"一句中文","beat":"shock_hook"}'
        ']}]} '
        '6镜。beat顺序：shock_hook, conflict, escalation, power_reveal, face_slap, cliffhanger。'
        '修仙/仙侠题材。visual_prompt: 超写实真人实拍, Arri Alexa 65, 竖屏9:16, 电影感灯光。禁止动画。'
    )}],
    'temperature': 0.7, 'max_tokens': 8192
}, timeout=180)

c1 = r1['choices'][0]['message']['content']
js1 = fix_id_pre(json_fix(c1[c1.find('{'):c1.rfind('}')+1]))
try:
    scr1 = fix_ids(json.loads(js1))
except:
    cut = js1.rfind('}')
    js1 = fix_id_pre(json_fix(js1[:cut+1] + ']}'))
    scr1 = fix_ids(json.loads(js1))
ep1 = scr1['episodes'][0]
log(f'  Ep1: {ep1["title"]} ({len(ep1["shots"])} shots)')

log('  LLM for Ep2...')
last_line = ep1['shots'][-1]['dialogue']
r2 = api_post(f'{API_BASE}/chat/completions', {
    'model': LLM_MODEL,
    'messages': [{'role': 'user', 'content': (
        f'继续《{scr1["title"]}》。上集结尾：{last_line}。'
        '输出纯JSON：{"episodes":[{"ep":2,"title":"第2集","shots":['
        '{"id":7 (整数),"visual_prompt":"50-70词英文","dialogue":"一句中文","beat":"aftermath"}'
        ']}]} '
        '6镜(id 7-12)。beat顺序：aftermath, twist, counter_attack, ultimate_power, revenge, open_ending。'
        '修仙/仙侠。超写实真人实拍。'
    )}],
    'temperature': 0.7, 'max_tokens': 8192
}, timeout=180)

c2 = r2['choices'][0]['message']['content']
js2 = fix_id_pre(json_fix(c2[c2.find('{'):c2.rfind('}')+1]))
try:
    scr2 = fix_ids(json.loads(js2))
except:
    cut = js2.rfind('}')
    js2 = fix_id_pre(json_fix(js2[:cut+1] + ']}'))
    scr2 = fix_ids(json.loads(js2))
ep2 = scr2['episodes'][0]
log(f'  Ep2: {ep2["title"]} ({len(ep2["shots"])} shots)')

script = {'title': scr1['title'], 'episodes': [ep1, ep2]}
with open(OUT_DIR / 'script.json', 'w', encoding='utf-8') as f:
    json.dump(script, f, ensure_ascii=False, indent=2)

# ===== PHASE 2: Videos =====
all_shots = []
for ep in script['episodes']:
    for s in ep['shots']:
        all_shots.append({'ep': ep['ep'], 'label': f'E{ep["ep"]}S{s["id"]:02d}',
                          'prompt': s['visual_prompt'], 'dialogue': s['dialogue'], 'beat': s['beat']})

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
    # Task ID: try flat "id" or "task_id" (API returns flat, NOT nested in data)
    tid = resp.get('id') or resp.get('task_id')
    if tid:
        tasks[tid] = s
        log(f'    OK {tid}')
    else:
        log(f'    FAIL: {json.dumps(resp, ensure_ascii=False)[:150]}')
        # Check for quota error
        if 'insufficient' in str(resp).lower():
            log('    QUOTA EXHAUSTED — stopping submissions')
            break

log(f'  Waiting for {len(tasks)} videos...')
completed = {}
deadline = time.time() + len(tasks) * 180

while len(completed) < len(tasks) and time.time() < deadline:
    for tid, info in list(tasks.items()):
        if tid in completed:
            continue
        try:
            sr = api_get(f'{API_BASE}/video/generations/{tid}', timeout=15)
            # API returns: {"code":"success","data":{"status":"QUEUED","data":{"status":"succeeded","result_url":"..."}}}
            outer = sr.get('data', sr)
            inner = outer.get('data', {}) if isinstance(outer, dict) else {}
            outer_status = (outer.get('status') or '').upper() if isinstance(outer, dict) else ''
            inner_status = (inner.get('status') or '').upper() if isinstance(inner, dict) else ''
            result_url = inner.get('result_url') or inner.get('video_url') or inner.get('url') or ''
            
            if inner_status in ('SUCCEEDED', 'COMPLETED', 'DONE', 'SUCCESS'):
                vp = OUT_DIR / f'{info["label"]}.mp4'
                if result_url:
                    dl = httpx.get(result_url, timeout=120, verify=False)
                    if dl.status_code == 200 and len(dl.content) > 1024:
                        vp.write_bytes(dl.content)
                        completed[tid] = str(vp)
                        log(f'    DONE {info["label"]} ({len(dl.content)//1024}KB)')
                    else:
                        completed[tid] = f'DOWNLOAD_FAILED'
                        log(f'    DLED {info["label"]} but bad ({dl.status_code}, {len(dl.content)}B)')
                else:
                    completed[tid] = 'NO_URL'
                    log(f'    DONE {info["label"]} but no URL')
            elif inner_status in ('FAILED', 'ERROR', 'CANCELLED'):
                completed[tid] = 'FAILED'
                log(f'    FAIL {info["label"]}: {inner.get("fail_reason", inner_status)}')
            elif outer_status in ('FAILED', 'ERROR'):
                completed[tid] = 'FAILED'
                log(f'    FAIL {info["label"]}: outer {outer_status}')
        except Exception as e:
            log(f'    ERR {info["label"]}: {e}')
    time.sleep(10)

# ===== SUMMARY =====
log('')
log('=' * 60)
log(f'DONE: {len(completed)}/{len(tasks)} videos')
for tid, info in tasks.items():
    log(f'  {info["label"]}: {completed.get(tid, "pending")}')
log(f'Output: {OUT_DIR}')

# Auto-push to Feishu
try:
    import subprocess
    subprocess.run(['bash', str(Path(__file__).parent / 'push_drama.sh')], 
                   capture_output=True, text=True, timeout=30)
    log('Pushed to Feishu')
except Exception as e:
    log(f'Push failed: {e}')
