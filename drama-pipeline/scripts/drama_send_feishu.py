#!/usr/bin/env python3
"""drama_send_feishu.py — 发送文件/消息到飞书"""

import sys, os, glob, json, requests, argparse
from pathlib import Path

FEISHU_APP_ID     = os.environ.get("FEISHU_APP_ID", "cli_aab694730bb8dcd6")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "3lxuIiJiTwxwYaXYXhdSZUe4YdY1ssZP")
FEISHU_CHAT_ID    = os.environ.get("FEISHU_CHAT_ID", "oc_683756dd47394fb46ef5693cd1187b4c")

def get_tenant_token():
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10
    )
    return resp.json()["tenant_access_token"]

def send_text(token, chat_id, text):
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": chat_id, "msg_type": "text",
              "content": json.dumps({"text": text})},
        timeout=10
    )
    return resp.json()

def send_file(token, chat_id, filepath):
    # Upload file
    fname = os.path.basename(filepath)
    fsize = os.path.getsize(filepath)
    ftype = "pdf" if filepath.endswith(".pdf") else "stream"

    with open(filepath, "rb") as f:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (fname, f, "application/octet-stream")},
            data={"file_type": ftype, "file_name": fname},
            timeout=30
        )
    file_key = resp.json().get("data", {}).get("file_key", "")
    if not file_key:
        print(f"⚠️ 文件上传失败: {resp.text[:200]}")
        return None

    # Send file message
    resp = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": chat_id, "msg_type": "file",
              "content": json.dumps({"file_key": file_key})},
        timeout=10
    )
    return resp.json()

def main():
    p = argparse.ArgumentParser(description="发送文件/消息到飞书")
    p.add_argument("--file", default="", help="要发送的文件路径")
    p.add_argument("--dir", default="", help="要发送的目录（发送所有文件）")
    p.add_argument("--message", required=True, help="附带消息文本")
    p.add_argument("--chat", default=FEISHU_CHAT_ID)
    args = p.parse_args()

    print("🔑 获取飞书token...")
    token = get_tenant_token()

    # 发送文本消息
    print(f"📤 发送消息...")
    send_text(token, args.chat, args.message)

    # 发送文件
    files_to_send = []
    if args.file:
        files_to_send = [args.file]
    elif args.dir:
        files_to_send = sorted(glob.glob(f"{args.dir}/*.mp4") + glob.glob(f"{args.dir}/*.pdf"))

    for fp in files_to_send:
        print(f"📎 上传: {os.path.basename(fp)} ({os.path.getsize(fp)/1024/1024:.1f}MB)")
        result = send_file(token, args.chat, fp)
        if result:
            print(f"  ✅ 已发送")
        else:
            print(f"  ⚠️ 发送失败")

    print("🎉 完成")

if __name__ == "__main__":
    main()
