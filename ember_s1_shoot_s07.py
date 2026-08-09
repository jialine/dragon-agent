#!/usr/bin/env python3
"""S07 拍摄 — T2V用WAN 2.7 (无水印) + R2V用HappyHorse"""
import re, json, subprocess, time, sys
from pathlib import Path

# Config
OUT = Path("/home/jialine/dragon-agent/ember_s1/videos/S07")
OUT.mkdir(parents=True, exist_ok=True)

# WAN API (lingyuncx — 无水印)
wan_key_line = subprocess.run(
    ["grep", "-oP", 'WAN_KEY = "\\K[^"]+', "/home/jialine/dragon-agent/drama_real.py"],
    capture_output=True, text=True).stdout.strip()
WAN_KEY = wan_key_line
WAN_BASE = "https://api.lingyuncx.com"

# HappyHorse via andlapi
ANDLAPI_KEY = subprocess.run(
    ["grep", "-oP", 'api_key: "\\K[^"]+', "/home/jialine/dragon-agent/config.yaml"],
    capture_output=True, text=True).stdout.strip()
ANDLAPI_BASE = "https://api.andlapi.cn/v1/video/generations"

REF_DIR = Path("/home/jialine/dragon-agent/ember_s1/03_角色参考图")

def wan_t2v(prompt_en, duration, output_path, size="1920*1080"):
    """WAN 2.7 T2V — 无水印"""
    payload = {
        "model": "wan2.7-t2v",
        "prompt": prompt_en,
        "duration": duration,
        "size": size,
        "parameters": {"prompt_extend": True}
    }
    r = subprocess.run(["curl", "-s", f"{WAN_BASE}/v1/videos/generations",
        "-H", f"Authorization: Bearer {WAN_KEY}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload)], capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(r.stdout)
        task_id = data.get("task_id") or data.get("data", {}).get("task_id")
    except:
        print(f"  FAIL(submit): {r.stdout[:100]}")
        return False
    if not task_id:
        print(f"  FAIL(no_id): {r.stdout[:100]}")
        return False

    print(f"  task={task_id[:12]}...", end=" ", flush=True)
    for _ in range(120):  # 10 min timeout
        time.sleep(5)
        r2 = subprocess.run(["curl", "-s", f"{WAN_BASE}/v1/tasks/{task_id}",
            "-H", f"Authorization: Bearer {WAN_KEY}"],
            capture_output=True, text=True, timeout=10)
        try:
            d = json.loads(r2.stdout)
            data = d.get("data", d)
            status = data.get("status", "")
        except:
            continue
        if status in ("completed", "succeeded"):
            url = data.get("video_url") or data.get("result_url")
            if url:
                subprocess.run(["curl", "-s", "-o", str(output_path), url], timeout=120)
                if output_path.stat().st_size > 1000:
                    print("✓")
                    return True
        elif status == "failed":
            print("✗(failed)")
            return False
        print(".", end="", flush=True)
    print("✗(timeout)")
    return False

def hh_r2v(prompt_cn, ref_path, duration, output_path, size="1920*1080"):
    """HappyHorse R2V — 参考图驱动"""
    import requests, urllib3
    urllib3.disable_warnings()
    
    # Upload ref image to get URL (use local path directly since andlapi supports it)
    # Actually andlapi needs URL, let's use a local approach
    body = {
        "model": "happyhorse-1.1-r2v",
        "prompt": prompt_cn,
        "size": size,
        "duration": duration,
        "ref_image": [f"file://{ref_path}"] if ref_path.exists() else []
    }
    r = requests.post(ANDLAPI_BASE,
        headers={"Authorization": f"Bearer {ANDLAPI_KEY}", "Content-Type": "application/json"},
        json=body, timeout=60, verify=False)
    data = r.json()
    task_id = data.get("task_id")
    if not task_id:
        print(f"  FAIL: {r.text[:100]}")
        return False
    
    print(f"  task={task_id[:12]}...", end=" ", flush=True)
    for _ in range(120):
        time.sleep(5)
        r2 = requests.get(f"{ANDLAPI_BASE}/{task_id}",
            headers={"Authorization": f"Bearer {ANDLAPI_KEY}"},
            timeout=15, verify=False)
        d = r2.json().get("data", {})
        status = d.get("status", "")
        if status in ("completed", "succeeded"):
            url = d.get("result_url", "")
            if url:
                subprocess.run(["curl", "-s", "-o", str(output_path), url], timeout=120)
                if output_path.stat().st_size > 1000:
                    print("✓")
                    return True
        elif status == "failed":
            print("✗(failed)")
            return False
        print(".", end="", flush=True)
    print("✗(timeout)")
    return False

def hh_t2v(prompt_cn, duration, output_path, size="1920*1080"):
    """HappyHorse T2V — fallback"""
    import requests, urllib3
    urllib3.disable_warnings()
    body = {
        "model": "happyhorse-1.1-t2v",
        "prompt": prompt_cn,
        "size": size,
        "duration": duration
    }
    r = requests.post(ANDLAPI_BASE,
        headers={"Authorization": f"Bearer {ANDLAPI_KEY}", "Content-Type": "application/json"},
        json=body, timeout=60, verify=False)
    data = r.json()
    task_id = data.get("task_id")
    if not task_id:
        return False
    print(f"  task={task_id[:12]}...", end=" ", flush=True)
    for _ in range(120):
        time.sleep(5)
        r2 = requests.get(f"{ANDLAPI_BASE}/{task_id}",
            headers={"Authorization": f"Bearer {ANDLAPI_KEY}"},
            timeout=15, verify=False)
        d = r2.json().get("data", {})
        if d.get("status") in ("completed", "succeeded"):
            url = d.get("result_url", "")
            if url:
                subprocess.run(["curl", "-s", "-o", str(output_path), url], timeout=120)
                if output_path.stat().st_size > 1000:
                    print("✓")
                    return True
        elif d.get("status") == "failed":
            print("✗")
            return False
        print(".", end="", flush=True)
    print("✗")
    return False


# Parse storyboard
sb = Path("/home/jialine/dragon-agent/ember_s1/02_分镜表/S1-E07_分镜.md").read_text()

shots = []
# Parse each shot block
blocks = re.split(r'\n## ', sb)
for block in blocks[1:]:  # skip header
    header = block.split('\n')[0]
    m = re.match(r'(S1-E07-\d+) \| 镜\d+ \| (.+?) \| (T2V|R2V|I2V) \| (\d+)秒', header)
    if not m:
        continue
    sid, desc, model, dur = m.groups()
    
    # Extract prompt
    pm = re.search(r'\*\*提示词\*\* \| (.+?)\n', block)
    prompt = pm.group(1).strip() if pm else desc
    
    # Extract ref image if R2V
    ref = None
    if model == "R2V":
        rm = re.search(r'\*\*参考图\*\* \| `(.+?)`', block)
        if rm:
            ref_name = rm.group(1).replace("ref_images/", "")
            ref = REF_DIR / ref_name
    
    shots.append({
        "id": sid, "desc": desc, "model": model, 
        "duration": int(dur), "prompt": prompt, "ref": ref
    })

print(f"S07: {len(shots)} shots")
for s in shots:
    ref_info = f" ref={s['ref'].name}" if s['ref'] else ""
    print(f"  {s['id']} | {s['model']} | {s['duration']}s | {s['prompt'][:50]}...{ref_info}")

if len(sys.argv) > 1 and sys.argv[1] == "--dry":
    print("\nDry run — done")
    sys.exit(0)

# Shoot!
success = 0
failed = []
for i, s in enumerate(shots):
    out = OUT / f"{s['id']}.mp4"
    if out.exists() and out.stat().st_size > 1000:
        print(f"[{i+1}/{len(shots)}] {s['id']} SKIP (exists)")
        success += 1
        continue
    
    print(f"[{i+1}/{len(shots)}] {s['id']} | {s['model']} | {s['duration']}s", end=" ", flush=True)
    
    if s['model'] == "R2V" and s['ref'] and s['ref'].exists():
        ok = hh_r2v(s['prompt'], s['ref'], s['duration'], out)
    elif s['model'] == "T2V":
        # Use WAN for T2V (watermark-free)
        en_prompt = f"Epic dark sci-fi cinematic shot, {s['prompt']}, photorealistic, 8K, cinematic 16:9, no watermark, no text, no logo"
        ok = wan_t2v(en_prompt, s['duration'], out)
        if not ok:
            # Fallback to HappyHorse
            print("  WAN failed, fallback HH...", end=" ", flush=True)
            ok = hh_t2v(s['prompt'], s['duration'], out)
    else:
        print("SKIP (no model)")
        continue
    
    if ok:
        success += 1
    else:
        failed.append(s['id'])
        print(f"  ✗ FAILED")

print(f"\n{'='*50}")
print(f"Done: {success}/{len(shots)} success")
if failed:
    print(f"Failed: {', '.join(failed)}")
print(f"Output: {OUT}")
