#!/usr/bin/env python3
"""v5: Minimal smoke test — 1 shot, prove pipeline end-to-end, then scale."""
import os, json, httpx, re, sys, time, subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')
K = os.environ['ANDLAPI_KEY']
API = 'https://api.andlapi.cn/v1'
OUT = Path('/tmp/drama_eps')
OUT.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)

def post(url, payload, timeout=120):
    r = httpx.post(url, json=payload, headers={'Authorization': f'Bearer {K}'}, timeout=timeout, verify=False)
    return r.json()

def get(url, timeout=30):
    r = httpx.get(url, headers={'Authorization': f'Bearer {K}'}, timeout=timeout, verify=False)
    return r.json()

# === PHASE 1: Mini script (1 shot only, prove LLM→JSON works) ===
log('=' * 60)
log('SMOKE TEST: 1-shot pipeline')
log('=' * 60)

log('LLM script...')
r = post(f'{API}/chat/completions', {
    'model': 'deepseek-v3.2',
    'messages': [{'role': 'user', 'content': (
        'Output ONLY valid JSON, no markdown: '
        '{"title":"修仙短剧","episodes":[{"ep":1,"title":"试炼","shots":['
        '{"id":1,"visual_prompt":"50 words English: xianxia cultivator in ancient Chinese palace, '
        'flowing robes, golden light, hyperrealistic live-action, Arri Alexa 65, vertical 9:16, '
        'dramatic lighting, anamorphic lens, film grain. NO animation.","dialogue":"修炼之道，在于本心。","beat":"power_reveal"}'
        ']}]}'
    )}],
    'temperature': 0.7, 'max_tokens': 4096
}, timeout=60)

c = r['choices'][0]['message']['content']
js = c[c.find('{'):c.rfind('}')+1]
js = re.sub(r',\s*}', '}', js)
js = re.sub(r',\s*]', ']', js)
js = re.sub(r'"id":\s*[^0-9\s,}\]]+', '"id": 1', js)
script = json.loads(js)
log(f'  Script: {script["title"]}')

# === PHASE 2: 1 video ===
shot = script['episodes'][0]['shots'][0]
log(f'  Shot: {shot["dialogue"]}')
log(f'  Submit video...')

resp = post(f'{API}/video/generations', {
    'model': 'wan2.7-t2v',
    'prompt': shot['visual_prompt'],
    'size': '1080*1920',
    'seconds': '2'  # 2s to fit budget (~¥29.40)
}, timeout=30)

tid = resp.get('id') or resp.get('task_id')
if not tid:
    log(f'  FAIL: {json.dumps(resp, ensure_ascii=False)[:300]}')
    sys.exit(1)
log(f'  Task: {tid}')

# === PHASE 3: Poll + Download ===
log(f'  Waiting...')
deadline = time.time() + 180
while time.time() < deadline:
    sr = get(f'{API}/video/generations/{tid}', timeout=15)
    outer = sr.get('data', sr)
    inner = outer.get('data', {}) if isinstance(outer, dict) else {}
    outer_s = (outer.get('status') or '').upper() if isinstance(outer, dict) else ''
    inner_s = (inner.get('status') or '').upper() if isinstance(inner, dict) else ''
    result_url = inner.get('result_url') or inner.get('video_url') or ''

    if inner_s in ('SUCCEEDED', 'COMPLETED', 'DONE'):
        vp = OUT / 'test.mp4'
        if result_url:
            dl = httpx.get(result_url, timeout=120, verify=False)
            if dl.status_code == 200 and len(dl.content) > 1024:
                vp.write_bytes(dl.content)
                log(f'  ✅ DOWNLOADED: {vp} ({len(dl.content)//1024}KB)')
            else:
                log(f'  ⚠️ Download bad: {dl.status_code} {len(dl.content)}B')
        else:
            log(f'  ⚠️ No result_url in response')
        break
    elif inner_s in ('FAILED', 'ERROR'):
        log(f'  ❌ Failed: {inner.get("fail_reason", "")}')
        break
    elif outer_s in ('FAILED', 'ERROR'):
        log(f'  ❌ Outer failed: {outer_s}')
        break

    time.sleep(10)
    log(f'    status: outer={outer_s} inner={inner_s}')

# === PHASE 4: Push to Feishu ===
log('  Push to Feishu...')
try:
    result = subprocess.run(
        ['bash', str(Path(__file__).parent / 'push_drama.sh')],
        capture_output=True, text=True, timeout=30
    )
    log(f'  Push result: {result.stdout.strip() or result.stderr.strip()}')
except Exception as e:
    log(f'  Push error: {e}')

log('=' * 60)
log('SMOKE TEST DONE')
log(f'Output: {OUT}')
