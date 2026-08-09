#!/usr/bin/env python3
"""Feishu push for drama_eps completion"""
import httpx, json, sys, os

APP_ID = os.environ.get("FEISHU_APP_ID", "cli_aab694730bb8dcd6")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "3lxuIiJiTwxwYaXYXhdSZUe4YdY1ssZP")
OPEN_ID = os.environ.get("FEISHU_OPEN_ID", "ou_640a24ce510f7fa22bab74af213e4cbb")

title = sys.argv[1] if len(sys.argv) > 1 else "短剧完成"
text = sys.argv[2] if len(sys.argv) > 2 else ""
video_dir = sys.argv[3] if len(sys.argv) > 3 else "/tmp/drama_eps"

# Token
r = httpx.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": APP_ID, "app_secret": APP_SECRET}, verify=False, timeout=10)
token = r.json().get("tenant_access_token", "")
if not token:
    print("Token failed", file=sys.stderr)
    sys.exit(1)

# Send text
r = httpx.post("https://open.feishu.cn/open-apis/im/v1/messages",
    params={"receive_id_type": "open_id"},
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    json={
        "receive_id": OPEN_ID,
        "msg_type": "text",
        "content": json.dumps({"text": f"{title}\n{text}"})
    }, verify=False, timeout=10)
print(f"Text: {r.status_code} {r.json().get('msg','')}")

# Send video files
for ep in [1, 2]:
    path = f"{video_dir}/ep{ep}_raw.mp4"
    if os.path.exists(path):
        sz = os.path.getsize(path) // 1024 // 1024
        print(f"Found ep{ep}: {path} ({sz}MB)")
        r = httpx.post("https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "receive_id": OPEN_ID,
                "msg_type": "text",
                "content": json.dumps({"text": f"第{ep}集文件: {path} ({sz}MB)\n请用 scp 拉取"})
            }, verify=False, timeout=10)
        print(f"Ep{ep}: {r.status_code}")

print("Done")
