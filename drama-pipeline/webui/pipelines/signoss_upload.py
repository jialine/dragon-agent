"""SignOSS image upload via andlapi.cn.

Upload endpoint: POST https://api.andlapi.cn/signoss/upload
Auth: X-API-Key header (key from happyhorse_api.py)
"""

import os
import sys
import subprocess
import json

# Import SIGNOSS_KEY from existing happyhorse_api module
HAPPYHORSE_DIR = "/home/jialine/dragon-agent/scripts"
sys.path.insert(0, HAPPYHORSE_DIR)

SIGNOSS_URL = "https://api.andlapi.cn/signoss/upload"

try:
    from happyhorse_api import SIGNOSS_KEY
    API_KEY = SIGNOSS_KEY
except ImportError:
    # Fallback: parse the file directly
    API_KEY = ""
    try:
        with open(os.path.join(HAPPYHORSE_DIR, "happyhorse_api.py")) as f:
            in_signoss = False
            for line in f:
                if "SIGNOSS_KEY" in line:
                    in_signoss = True
                    continue
                if in_signoss and '"sk-' in line:
                    API_KEY = line.strip().strip('"').strip("'").strip(",")
                    break
    except Exception:
        pass


def is_configured():
    return bool(API_KEY) and len(API_KEY) > 10


def upload_file(local_path, category="characters"):
    """Upload a file to SignOSS. Returns public OSS URL."""
    if not is_configured():
        raise RuntimeError("SignOSS key not configured")

    if not os.path.exists(local_path):
        raise FileNotFoundError(f"文件不存在: {local_path}")

    filename = os.path.basename(local_path)

    cmd = [
        "curl", "-s", "-k",
        "-X", "POST", SIGNOSS_URL,
        "-H", f"X-API-Key: {API_KEY}",
        "-F", f"file=@{local_path};filename={filename}",
        "-F", f"category={category}",
        "--max-time", "60"
    ]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=65)
    if r.returncode != 0:
        raise RuntimeError(f"SignOSS curl failed: {r.stderr}")

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"SignOSS unexpected response: {r.stdout[:200]}")

    if not data.get("success"):
        error = data.get("error", r.stdout[:200])
        raise RuntimeError(f"SignOSS upload failed: {error}")

    url = data.get("url", "")
    if not url:
        raise RuntimeError(f"SignOSS response missing URL: {r.stdout[:200]}")

    return url


def upload_character_views(project_name, char_name, views_map):
    """Upload all 4 character views to SignOSS."""
    if not is_configured():
        return {}

    oss_urls = {}
    for view_type, local_path in views_map.items():
        if not local_path or not os.path.exists(local_path):
            continue
        try:
            url = upload_file(local_path, category="characters")
            oss_urls[view_type] = url
            print(f"  📤 {char_name}/{view_type} → OSS")
        except Exception as e:
            print(f"  ⚠️ {char_name}/{view_type}: {e}")

    return oss_urls
