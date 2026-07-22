"""
Dragon Agent — Wan2.7 Video Generation Tool
============================================

Wan2.7 (通义万相) text-to-video generation via api.andlapi.cn proxy.
Supports async submission, polling, and download.

Tools:
    - wan_video: Generate video from text prompt

Models:
    - wan2.7-t2v: Text-to-video
    - wan2.7-r2v: Reference/image-to-video
    - wan2.7-image / wan2.7-image-pro: Image generation

API: POST /v1/video/generations (OpenAI-compatible proxy)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dragon.constants import API_BASE_URL
import tempfile
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("dragon.tool.builtins.wan_video")

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_BASE = API_BASE_URL
DEFAULT_MODEL = "wan2.7-t2v"
DEFAULT_SIZE = "1280x720"
DEFAULT_DURATION = 5
MAX_POLL_TIME = 600  # 10 minutes max polling
POLL_INTERVAL = 10   # seconds between polls
def _unwrap_proxy(data: dict) -> dict:
    """Unwrap new-api-sms proxy response: {"code":"fail_to_fetch_task","message":"{...}","data":null}"""
    if isinstance(data, dict) and data.get("code") == "fail_to_fetch_task":
        msg = data.get("message", "")
        if isinstance(msg, str) and msg.startswith("{"):
            try:
                inner = json.loads(msg)
                if isinstance(inner, dict):
                    return inner
            except (json.JSONDecodeError, TypeError):
                pass
    return data



def _get_api_key() -> str:
    """Get API key from environment."""
    return os.getenv("DRAGON_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")


async def tool_wan_video(
    prompt: str,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    duration: int = DEFAULT_DURATION,
    negative_prompt: str = "",
    reference_image: str = "",
    output_path: str = "",
) -> str:
    """Generate a video from a text prompt using Wan2.7.

    Submits a generation task, polls until complete, downloads the result.

    Args:
        prompt: Text description of the video to generate.
        model: Model name (wan2.7-t2v for text-to-video, wan2.7-r2v for image-to-video).
        size: Resolution (1280x720, 720x1280, etc.).
        duration: Video duration in seconds (minimum 5).
        negative_prompt: Things to avoid in the video.
        reference_image: URL or base64 image for R2V (wan2.7-r2v). Required for R2V mode.
        output_path: Where to save the video. Defaults to temp directory.

    Returns:
        JSON string with status, video_path, task_id, and error if any.
    """
    import httpx

    api_key = _get_api_key()
    if not api_key:
        return json.dumps({
            "success": False,
            "error": "No API key. Set DRAGON_API_KEY or DASHSCOPE_API_KEY.",
            "video_path": "",
            "task_id": "",
        }, ensure_ascii=False)

    base_url = API_BASE_URL
    url = f"{base_url}/video/generations"

    # Step 1: Submit
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "duration": max(duration, 5),
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if reference_image:
        payload["reference_image"] = reference_image
    if reference_image:
        payload["reference_image"] = reference_image

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=120, verify=False) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json() or {}

            data = _unwrap_proxy(data)

            # Handle proxy-level errors
            if data.get("code") and data.get("code") != "fail_to_fetch_task" and not data.get("task_id") and not (data.get("data") or {}).get("task_id"):
                error_msg = data.get("message", str(data))
                # Check for quota
                if "insufficient" in str(error_msg).lower() or "quota" in str(error_msg).lower():
                    return json.dumps({
                        "success": False,
                        "error": f"配额不足: {error_msg}",
                        "video_path": "",
                        "task_id": "",
                        "hint": "请在 sangyuye.com 充值。5秒720p视频约¥73.50。",
                    }, ensure_ascii=False)
                return json.dumps({
                    "success": False,
                    "error": f"API error: {error_msg}",
                    "video_path": "",
                    "task_id": "",
                }, ensure_ascii=False)

            task_id = data.get("task_id") or (data.get("data") or {}).get("task_id", "")
            if not task_id:
                # Maybe it returned the video directly?
                video_url = data.get("video_url") or (data.get("data") or {}).get("video_url", "")
                if video_url:
                    task_id = "direct"
                else:
                    return json.dumps({
                        "success": False,
                        "error": f"No task_id in response: {json.dumps(data, ensure_ascii=False)[:500]}",
                        "video_path": "",
                        "task_id": "",
                    }, ensure_ascii=False)

            # Step 2: Poll
            if task_id != "direct":
                poll_deadline = start + MAX_POLL_TIME
                video_url = ""

                while time.monotonic() < poll_deadline:
                    await asyncio.sleep(POLL_INTERVAL)
                    poll_url = f"{url}/{task_id}"
                    try:
                        poll_resp = await client.get(poll_url, headers=headers)
                        poll_data = _unwrap_proxy(poll_resp.json() or {})
                    except Exception:
                        continue

                    status = poll_data.get("status") or (poll_data.get("data") or {}).get("status", "")
                    if status in ("completed", "succeeded", "done", "SUCCEEDED"):
                        video_url = (
                            poll_data.get("video_url")
                            or (poll_data.get("data") or {}).get("url", "")
                            or (poll_data.get("data") or {}).get("video_url", "")
                            or (poll_data.get("output") or {}).get("video_url", "")
                        )
                        break
                    elif status in ("failed", "error", "cancelled", "FAILED"):
                        return json.dumps({
                            "success": False,
                            "error": f"Generation failed: status={status}",
                            "video_path": "",
                            "task_id": task_id,
                        }, ensure_ascii=False)

                if not video_url:
                    return json.dumps({
                        "success": False,
                        "error": f"Polling timeout after {MAX_POLL_TIME}s",
                        "video_path": "",
                        "task_id": task_id,
                    }, ensure_ascii=False)

            # Step 3: Download
            if not output_path:
                suffix = ".mp4"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="wan_")
                output_path = tmp.name
                tmp.close()

            dl_resp = await client.get(video_url)
            dl_resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(dl_resp.content)

            duration_ms = (time.monotonic() - start) * 1000
            file_size = os.path.getsize(output_path)

            return json.dumps({
                "success": True,
                "video_path": output_path,
                "task_id": task_id,
                "duration_ms": round(duration_ms, 1),
                "file_size": file_size,
                "prompt": prompt,
                "model": model,
            }, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        return json.dumps({
            "success": False,
            "error": f"HTTP {e.response.status_code}: {str(e)[:300]}",
            "video_path": "",
            "task_id": "",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"Unexpected error: {type(e).__name__}: {e}",
            "video_path": "",
            "task_id": "",
        }, ensure_ascii=False)
