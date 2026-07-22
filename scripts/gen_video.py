#!/usr/bin/env python3
"""
Dragon Agent 视频生成工具
用法:
  python3 gen_video.py "一只猫在花园里玩耍"
  python3 gen_video.py "prompt" --model wan2.7-t2v
"""
import sys, time, json, argparse, subprocess
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_URL = "https://api.andlapi.cn/v1/video/generations"
API_KEY = "sk-T7KJ6eiZHjTmJ4WjsZLPHUl0k8jPq8dx3jBS13NTJgK5z6ur"
SSH_HOST = "jialine@172.16.74.45"
MYSQL_CMD = "/tmp/mysql/usr/bin/mysql -h 172.16.74.43 -u oneapi -poneapi_pass oneapi -N -e"

def submit(prompt, model, size):
    r = requests.post(API_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": model, "prompt": prompt, "size": size},
        timeout=60, verify=False)
    data = r.json()
    if "task_id" not in data:
        print(f"❌ 提交失败: {data}", file=sys.stderr); sys.exit(1)
    return data["task_id"]

def query_task(task_id):
    """通过 SSH 查询 MySQL"""
    sql = f'"SELECT status, JSON_UNQUOTE(JSON_EXTRACT(private_data, \\\\\"$.result_url\\\\\")) FROM tasks WHERE task_id=\\\\\"{task_id}\\\\\""'
    cmd = f"ssh -o StrictHostKeyChecking=no {SSH_HOST} '{MYSQL_CMD} {sql}'"
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        out = r.stdout.strip()
        if not out:
            return None, None
        parts = out.split("\t")
        return parts[0] if len(parts) > 0 else None, parts[1] if len(parts) > 1 else None
    except:
        return None, None

def wait(task_id, timeout=300):
    print(f"⏳ 等待任务完成...", file=sys.stderr)
    for i in range(0, timeout, 5):
        time.sleep(5)
        status, url = query_task(task_id)
        if status == "SUCCESS":
            return url
        elif status in ("FAILED", "CANCELED"):
            print(f"❌ 任务{status}", file=sys.stderr); return None
        print(f"   {(i+5)}s... [{status}]", file=sys.stderr)
    print(f"⚠️ 超时", file=sys.stderr); return None

def download(url, output):
    r = requests.get(url, timeout=120, verify=False)
    with open(output, "wb") as f:
        f.write(r.content)
    return len(r.content)

def main():
    p = argparse.ArgumentParser(description="视频生成")
    p.add_argument("prompt", help="文本提示词")
    p.add_argument("--model", default="happyhorse-1.1-t2v",
                   choices=["happyhorse-1.1-t2v", "wan2.7-t2v"])
    p.add_argument("--size", default="1280*720")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    print(f"🎬 提交: {args.model} | {args.prompt[:40]}...", file=sys.stderr)
    task_id = submit(args.prompt, args.model, args.size)
    print(f"✅ task_id: {task_id}", file=sys.stderr)

    url = wait(task_id)
    if not url:
        sys.exit(1)

    output = args.output or f"/tmp/video_{task_id[-8:]}.mp4"
    size = download(url, output)
    print(f"✅ 完成! {output} ({size/1024/1024:.1f}MB)")
    print(output)

if __name__ == "__main__":
    main()
