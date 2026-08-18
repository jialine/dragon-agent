#!/usr/bin/env python3
"""
Happyhorse 1.1 API 客户端（via andlapi.cn）
支持 T2V / R2V / I2V 视频生成 + SignOSS 参考图上传 + 轮询下载

用法:
  # T2V 文生视频
  python3 happyhorse_api.py t2v "一只猫在花园里玩耍" --output /tmp/cat.mp4

  # R2V 参考图生视频
  python3 happyhorse_api.py r2v "角色走在走廊" --ref "https://oss.../ref.jpg"

  # I2V 接上一帧
  python3 happyhorse_api.py i2v "继续走" --ref "https://oss.../lastframe.jpg"

  # 上传参考图到 SignOSS
  python3 happyhorse_api.py upload /path/to/image.png --category characters

  # 轮询已有任务
  python3 happyhorse_api.py poll <task_id> --download /tmp/out.mp4
"""

import sys
import time
import json
import argparse
import requests
import os
from pathlib import Path
from typing import Optional

# ============================================================
# 配置
# ============================================================

API_BASE = os.environ.get("ANDLAPI_BASE", "https://api.andlapi.cn")
API_KEY = os.environ.get(
    "ANDLAPI_API_KEY",
    "sk-T7KJ6eiZHjTmJ4WjsZLPHUl0k8jPq8dx3jBS13NTJgK5z6ur"
)
SIGNOSS_KEY = os.environ.get(
    "SIGNOSS_API_KEY",
    "sk-0c12c3fc39512eafa1a76adb07d25849abd10eb4305be405"
)
SIGNOSS_URL = f"{API_BASE}/signoss/upload"
VIDEO_URL = f"{API_BASE}/v1/video/generations"
TASK_URL = f"{API_BASE}/task"

DEFAULT_NEGATIVE = (
    "watermark, text, logo, happyhorse, subtitle, words, letters, "
    "brand, label, copyright, UI element, overlay text, signature, "
    "username, channel name, low quality, blurry, jpeg artifacts, "
    "distorted, deformed"
)


# ============================================================
# API 客户端
# ============================================================

class HappyhorseAPI:
    """Happyhorse 1.1 视频生成 API 客户端"""

    def __init__(self, api_key: str = "", signoss_key: str = ""):
        self.api_key = api_key or API_KEY
        self.signoss_key = signoss_key or SIGNOSS_KEY

    # ---- 参考图上传 ----

    def upload_ref(self, filepath: str, category: str = "characters") -> str:
        """上传本地图片到 SignOSS，返回公网 URL"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")

        with open(filepath, "rb") as f:
            resp = requests.post(
                SIGNOSS_URL,
                headers={"X-API-Key": self.signoss_key},
                files={"file": (os.path.basename(filepath), f)},
                data={"category": category},
                timeout=60,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"SignOSS 上传失败 ({resp.status_code}): {resp.text[:300]}")

        result = resp.json()
        if not result.get("success"):
            raise RuntimeError(f"SignOSS 错误: {result.get('error', 'unknown')}")

        url = result["files"][0]["url"]
        print(f"  📤 上传成功: {url}")
        return url

    # ---- 视频生成提交 ----

    def submit(
        self,
        prompt: str,
        negative_prompt: str = "",
        ref_image: str = "",
        num_frames: int = 81,
        fps: int = 16,
        aspect_ratio: str = "16:9",
        image_size: str = "1280x720",
        model: str = "happyhorse-1.1",
    ) -> str:
        """提交视频生成任务，返回 task_id"""
        body = {
            "model": model,
            "prompt": prompt,
            "negative_prompt": negative_prompt or DEFAULT_NEGATIVE,
            "num_frames": num_frames,
            "fps": fps,
            "logo": False,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        }
        if ref_image:
            body["ref_image"] = ref_image

        resp = requests.post(
            VIDEO_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"提交失败 ({resp.status_code}): {resp.text[:300]}")

        data = resp.json()
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise RuntimeError(f"响应中没有 task_id: {data}")
        print(f"  ✅ 任务已提交: {task_id}")
        return task_id

    # ---- 轮询 ----

    def poll(self, task_id: str, max_wait: int = 600, interval: int = 10) -> dict:
        """轮询任务状态，返回 {status, url, raw}"""
        elapsed = 0
        print(f"  ⏳ 等待任务完成 (最多 {max_wait}s)...")
        while elapsed < max_wait:
            time.sleep(interval)
            elapsed += interval

            try:
                resp = requests.get(f"{TASK_URL}/{task_id}", timeout=10)
                if resp.status_code != 200:
                    print(f"    [{elapsed}s] HTTP {resp.status_code}")
                    continue
                data = resp.json()
            except Exception as e:
                print(f"    [{elapsed}s] 请求异常: {e}")
                continue

            status = data.get("status", "")

            # 成功
            if status in ("SUCCESS", "COMPLETED", "succeeded"):
                url = ""
                # 多种可能的返回格式
                result = data.get("result", {})
                if isinstance(result, dict):
                    url = result.get("url", "") or result.get("video_url", "")
                if not url:
                    url = data.get("url", "")
                print(f"  ✅ [{elapsed}s] 完成!")
                return {"status": "SUCCESS", "url": url, "raw": data}

            # 失败
            if status in ("FAILED", "ERROR", "failed", "CANCELED"):
                print(f"  ❌ [{elapsed}s] {status}: {data}")
                return {"status": "FAILED", "error": str(data), "raw": data}

            # 进行中
            print(f"    [{elapsed}s] {status}...")

        print(f"  ⚠️ 超时 ({max_wait}s)")
        return {"status": "TIMEOUT"}

    # ---- 下载 ----

    def download(self, url: str, output: str) -> int:
        """下载视频文件，返回文件大小(bytes)"""
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(output, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        size = os.path.getsize(output)
        print(f"  📥 下载完成: {output} ({size / 1024 / 1024:.1f} MB)")
        return size

    # ---- 一键生成（提交+轮询+下载） ----

    def generate(
        self,
        prompt: str,
        output: str,
        negative_prompt: str = "",
        ref_image: str = "",
        num_frames: int = 81,
        fps: int = 16,
        max_wait: int = 600,
        **kwargs,
    ) -> Optional[str]:
        """一键生成视频：提交 → 轮询 → 下载，返回输出路径或 None"""
        task_id = self.submit(
            prompt=prompt,
            negative_prompt=negative_prompt,
            ref_image=ref_image,
            num_frames=num_frames,
            fps=fps,
            **kwargs,
        )
        result = self.poll(task_id, max_wait=max_wait)
        if result["status"] == "SUCCESS" and result.get("url"):
            self.download(result["url"], output)
            return output
        else:
            print(f"  ❌ 生成失败: {result}")
            return None


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Happyhorse 1.1 视频生成客户端 (via andlapi.cn)"
    )
    sub = parser.add_subparsers(dest="command", help="命令")

    # ---- t2v ----
    p_t2v = sub.add_parser("t2v", help="文生视频")
    p_t2v.add_argument("prompt", help="提示词")
    p_t2v.add_argument("--negative", default="", help="负面提示词")
    p_t2v.add_argument("--frames", type=int, default=81, help="帧数 (默认81)")
    p_t2v.add_argument("--fps", type=int, default=16, help="帧率 (默认16)")
    p_t2v.add_argument("--aspect", default="16:9", help="画幅比例")
    p_t2v.add_argument("--size", default="1280x720", help="分辨率")
    p_t2v.add_argument("--output", "-o", required=True, help="输出文件路径")
    p_t2v.add_argument("--wait", type=int, default=600, help="最大等待秒数")

    # ---- r2v ----
    p_r2v = sub.add_parser("r2v", help="参考图生视频")
    p_r2v.add_argument("prompt", help="提示词")
    p_r2v.add_argument("--ref", required=True, help="参考图 URL 或本地路径（自动上传）")
    p_r2v.add_argument("--negative", default="", help="负面提示词")
    p_r2v.add_argument("--frames", type=int, default=81)
    p_r2v.add_argument("--fps", type=int, default=16)
    p_r2v.add_argument("--aspect", default="16:9")
    p_r2v.add_argument("--size", default="1280x720")
    p_r2v.add_argument("--output", "-o", required=True)
    p_r2v.add_argument("--wait", type=int, default=600)

    # ---- i2v ----
    p_i2v = sub.add_parser("i2v", help="图生视频（接上一帧）")
    p_i2v.add_argument("prompt", help="提示词")
    p_i2v.add_argument("--ref", required=True, help="上一帧尾帧 URL 或本地路径")
    p_i2v.add_argument("--negative", default="")
    p_i2v.add_argument("--frames", type=int, default=81)
    p_i2v.add_argument("--fps", type=int, default=16)
    p_i2v.add_argument("--aspect", default="16:9")
    p_i2v.add_argument("--size", default="1280x720")
    p_i2v.add_argument("--output", "-o", required=True)
    p_i2v.add_argument("--wait", type=int, default=600)

    # ---- upload ----
    p_upload = sub.add_parser("upload", help="上传参考图到 SignOSS")
    p_upload.add_argument("filepath", help="本地图片路径")
    p_upload.add_argument("--category", default="characters",
                          choices=["characters", "scenes", "props", "frames"])

    # ---- poll ----
    p_poll = sub.add_parser("poll", help="轮询已有任务")
    p_poll.add_argument("task_id", help="任务 ID")
    p_poll.add_argument("--download", "-d", default="", help="下载到指定路径")
    p_poll.add_argument("--wait", type=int, default=600)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    api = HappyhorseAPI()

    if args.command == "upload":
        url = api.upload_ref(args.filepath, args.category)
        print(url)  # 只输出 URL，方便管道

    elif args.command == "poll":
        result = api.poll(args.task_id, max_wait=args.wait)
        if result["status"] == "SUCCESS":
            print(f"URL: {result['url']}")
            if args.download and result.get("url"):
                api.download(result["url"], args.download)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(1)

    elif args.command in ("t2v", "r2v", "i2v"):
        # 处理 ref_image：如果是本地路径则先上传
        ref_url = ""
        if args.command in ("r2v", "i2v"):
            ref = args.ref
            if ref.startswith("http://") or ref.startswith("https://"):
                ref_url = ref
            else:
                print(f"  📤 检测到本地路径，先上传: {ref}")
                ref_url = api.upload_ref(ref)

        output = api.generate(
            prompt=args.prompt,
            output=args.output,
            negative_prompt=args.negative,
            ref_image=ref_url,
            num_frames=args.frames,
            fps=args.fps,
            aspect_ratio=args.aspect,
            image_size=args.size,
            max_wait=args.wait,
        )
        if output:
            print(f"\n✅ 视频已生成: {output}")
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
