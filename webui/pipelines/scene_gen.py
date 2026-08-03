"""Scene concept art generation via hy-image, then upload to SignOSS."""

import os
import sys
import subprocess
import time

HAPPYHORSE_DIR = "/home/jialine/dragon-agent/scripts"
sys.path.insert(0, HAPPYHORSE_DIR)

SIGNOSS_URL = "https://api.andlapi.cn/signoss/upload"
ASSETS_DIR = "/home/jialine/dragon-agent/assets/scenes"

try:
    from happyhorse_api import SIGNOSS_KEY
except ImportError:
    SIGNOSS_KEY = ""


def generate_scene_image(prompt, output_path, size="1024*1024"):
    """Generate a scene concept art image using andlapi hy-image."""
    from happyhorse_api import API_KEY
    url = f"https://api.andlapi.cn/v1/images/generations"

    body = {
        "model": "hy-image-v3.0",
        "prompt": f"电影级场景概念图，{prompt}，宽广构图，环境氛围感强，电影光影，高细节",
        "n": 1,
        "size": size,
    }

    import requests, urllib3
    urllib3.disable_warnings()

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
        verify=False,
    )
    data = resp.json()

    image_url = None
    if "data" in data and data["data"]:
        image_url = data["data"][0].get("url")
    elif "images" in data and data["images"]:
        image_url = data["images"][0]

    if not image_url:
        raise RuntimeError(f"Scene gen failed: {resp.text[:300]}")

    # Download
    img_resp = requests.get(image_url, timeout=60, verify=False)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img_resp.content)

    # Crop bottom 40px to remove watermark
    try:
        subprocess.run(
            ["convert", output_path, "-gravity", "south", "-chop", "0x40", output_path],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass

    return output_path


def upload_to_signoss(local_path, category="scenes"):
    """Upload scene image to SignOSS, return public OSS URL."""
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location(
        "happyhorse_api", "/home/jialine/dragon-agent/scripts/happyhorse_api.py"
    )
    hh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hh)
    key = hh.SIGNOSS_KEY
    if not key or len(key) < 10:
        raise RuntimeError("SignOSS key not configured")

    # Compress large images to avoid 502 from nginx
    upload_path = local_path
    orig_size = _os.path.getsize(local_path)
    if orig_size > 500_000:
        from PIL import Image
        compressed = local_path.rsplit(".", 1)[0] + "_upload.jpg"
        img = Image.open(local_path)
        img = img.convert("RGB")
        img.thumbnail((1280, 720), Image.LANCZOS)
        img.save(compressed, "JPEG", quality=85)
        upload_path = compressed
        print(f"  Compressed: {orig_size//1024}KB → {_os.path.getsize(compressed)//1024}KB")

    filename = _os.path.basename(upload_path)
    import subprocess as _sp, json as _json, hashlib
    # Use ASCII-safe filename to avoid nginx 502 with Chinese chars
    safe_ext = _os.path.splitext(filename)[1] or ".jpg"
    safe_name = hashlib.md5(filename.encode()).hexdigest()[:12] + safe_ext
    cmd = [
        "curl", "-s", "-k",
        "-X", "POST", SIGNOSS_URL,
        "-H", f"X-API-Key: {key}",
        "-F", f"file=@{upload_path};filename={safe_name}",
        "-F", f"category={category}",
        "--max-time", "60"
    ]
    r = _sp.run(cmd, capture_output=True, text=True, timeout=65)
    if not r.stdout.strip() or r.stdout.strip().startswith("<"):
        raise RuntimeError(f"SignOSS error (rc={r.returncode}): {r.stdout[:200]}")
    data = _json.loads(r.stdout)
    if not data.get("success"):
        raise RuntimeError(f"SignOSS upload failed: {r.stdout[:200]}")
    return data.get("files", [{}])[0].get("url", ""), data.get("expires_at", "")


def generate_scene_full(scene_name, description, project_name="default"):
    """Generate concept art → upload to OSS. Returns (local_path, oss_url, expires)."""
    os.makedirs(ASSETS_DIR, exist_ok=True)

    safe_name = scene_name.replace("/", "_").replace(" ", "_")
    output_path = os.path.join(ASSETS_DIR, f"{project_name}_{safe_name}_{int(time.time())}.png")

    prompt = description or f"{scene_name}场景概念图"
    local_path = generate_scene_image(prompt, output_path)

    oss_url, expires = "", ""
    if SIGNOSS_KEY:
        oss_url, expires = upload_to_signoss(local_path)

    return local_path, oss_url, expires
