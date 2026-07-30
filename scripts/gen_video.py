#!/usr/bin/env python3
"""
Dragon Agent 视频生成工具
T2V → andlapi.cn 简单格式
R2V/I2V → andlapi.cn 透传 DashScope video-synthesis 格式

用法:
  python3 gen_video.py "prompt"
  python3 gen_video.py "prompt" --model happyhorse-1.1-r2v --ref-image URL1 --ref-image URL2
"""
import sys, time, argparse, json, re, os
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ANDLAPI_URL = "https://api.andlapi.cn/v1/video/generations"


def _get_api_key():
    k = os.environ.get("DRAGON_API_KEY", "")
    if k:
        return k
    try:
        import yaml
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml")
        if os.path.exists(p):
            with open(p) as f:
                c = yaml.safe_load(f)
            k = c.get("dispatch", {}).get("global_api", {}).get("api_key", "")
            if k:
                return k
    except Exception:
        pass
    return ""


ANDLAPI_KEY = _get_api_key()


def _extract_urls_from_prompt(prompt):
    # Priority 1: [url] bracket format — most reliable
    bracket_pattern = re.compile(r'\[(https?://[^\]]+)\]')
    bracket_urls = bracket_pattern.findall(prompt)
    if bracket_urls:
        clean = bracket_pattern.sub('', prompt).strip()
        clean = re.sub(r'\s{2,}', ' ', clean)
        return clean, bracket_urls
    # Priority 2: bare URLs — stop at Chinese/punctuation/brackets
    url_pattern = re.compile(r"https?://[^\s<>'\"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\[\]]+")
    urls = url_pattern.findall(prompt)
    if not urls:
        return prompt, []
    clean = url_pattern.sub('', prompt).strip()
    clean = re.sub(r'\s{2,}', ' ', clean)
    return clean, urls

def _merge_refs(prompt, ref_images):
    clean_prompt, extracted = _extract_urls_from_prompt(prompt)
    all_refs = list(dict.fromkeys((ref_images or []) + extracted))
    return clean_prompt, all_refs


def submit(prompt, model, size, duration, ref_images):
    """提交视频任务。R2V/I2V 走 DashScope 透传格式，T2V 走简单格式。"""
    clean_prompt, all_refs = _merge_refs(prompt, ref_images)

    is_dashscope = True  # andlapi now passthroughs all models to DashScope

    if is_dashscope:
        # 解析分辨率
        w, h = 1920, 1080
        try:
            parts = size.replace("*", "x").split("x")
            w, h = int(parts[0]), int(parts[1])
        except:
            pass
        resolution = "1080P" if max(w, h) >= 1080 else "720P"
        ratios = {
            (16, 9): "16:9", (9, 16): "9:16", (4, 3): "4:3",
            (3, 4): "3:4", (1, 1): "1:1", (21, 9): "21:9", (9, 21): "9:21"
        }
        # 简化比值
        from math import gcd
        g = gcd(w, h)
        ratio = ratios.get((w//g, h//g), "16:9")

        # 构建 [Image N] 引用
        ref_tags = "，".join(f"[Image {i+1}]" for i in range(len(all_refs)))
        dashscope_prompt = f"{ref_tags}中的场景，{clean_prompt}" if all_refs else clean_prompt

        body = {
            "model": model,
            "input": {
                "prompt": dashscope_prompt,
                "media": [{"type": "first_frame", "url": u} for u in all_refs]
            },
            "parameters": {
                "resolution": resolution,
                "ratio": ratio,
                "duration": duration,
                "watermark": False
            }
        }
        headers = {
            "Authorization": f"Bearer {ANDLAPI_KEY}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable"
        }
    else:
        body = {
            "model": model,
            "prompt": clean_prompt,
            "size": size,
            "duration": duration
        }
        headers = {
            "Authorization": f"Bearer {ANDLAPI_KEY}",
            "Content-Type": "application/json"
        }

    r = requests.post(ANDLAPI_URL, headers=headers, json=body, timeout=60, verify=False)
    data = r.json()
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        msg = f"提交失败: {data}"
        print(f"❌ {msg}", file=sys.stderr)
        raise RuntimeError(msg)
    return task_id


def query_task(task_id):
    try:
        r = requests.get(f"{ANDLAPI_URL}/{task_id}",
                         headers={"Authorization": f"Bearer {ANDLAPI_KEY}"},
                         timeout=15, verify=False)
        d = r.json().get("data", {})
        status = d.get("status", "")
        url = d.get("result_url", "") or d.get("url", "")
        fail_reason = d.get("fail_reason", "")
        return status, url, fail_reason
    except Exception:
        return None, None, ""


def wait(task_id, timeout=600):
    print("⏳ 等待任务完成...", file=sys.stderr)
    for i in range(0, timeout, 15):
        time.sleep(15)
        status, url, reason = query_task(task_id)
        print(f"   {(i+15)}s... [{status}]", file=sys.stderr)
        if status in ("SUCCESS", "SUCCEEDED", "COMPLETED", "succeeded", "DONE"):
            return url
        elif status in ("FAILED", "FAILURE", "CANCELED"):
            if reason:
                print(f"❌ {reason}", file=sys.stderr)
            return None
    print("⚠️ 超时", file=sys.stderr)
    return None


def download(url, output):
    r = requests.get(url, timeout=120, verify=False)
    with open(output, "wb") as f:
        f.write(r.content)
    return len(r.content)


def main():
    p = argparse.ArgumentParser(description="视频生成")
    p.add_argument("prompt", help="文本提示词")
    p.add_argument("--model", default="happyhorse-1.1-t2v",
                   choices=["happyhorse-1.1-t2v", "wan2.7-t2v", "happyhorse-1.1-r2v", "happyhorse-1.1-i2v"])
    p.add_argument("--size", default="1920*1080")
    p.add_argument("--duration", type=int, default=8, help="视频时长(秒)")
    p.add_argument("--ref-image", action="append", dest="ref_images", help="参考图URL(可多次指定)")
    p.add_argument("--submit-only", action="store_true", help="仅提交任务，不等待/下载")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    ref_imgs = args.ref_images or []
    print(f"🎬 提交: {args.model} | {args.duration}s | refs={len(ref_imgs)} | {args.prompt[:40]}...", file=sys.stderr)

    task_id = submit(args.prompt, args.model, args.size, args.duration, ref_imgs)
    print(f"task_id: {task_id}")

    if args.submit_only:
        sys.exit(0)

    url = wait(task_id)
    if not url:
        sys.exit(1)

    output = args.output or f"/tmp/video_{task_id[-8:]}.mp4"
    size = download(url, output)
    print(f"✅ 完成! {output} ({size/1024/1024:.1f}MB)", file=sys.stderr)
    print(output)


if __name__ == "__main__":
    main()
