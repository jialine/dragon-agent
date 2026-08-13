#!/usr/bin/env python3
"""drama_common.py — 短剧工作流共用工具模块"""

import os, sys, json, argparse, time, requests
from pathlib import Path
from typing import Optional

# ═══════════════ 配置 ═══════════════
API_BASE = os.environ.get("DRAGON_API_BASE", "https://api.andlapi.cn/v1")
API_KEY  = os.environ.get("DRAGON_API_KEY", os.environ.get("VIDEO_API_KEY", ""))
MODEL    = os.environ.get("DRAMA_MODEL", "deepseek-v4-pro")
VERIFY   = os.environ.get("VERIFY_SSL", "false").lower() == "true"
SIGNOSS_API_KEY = os.environ.get("SIGNOSS_API_KEY", "")
SIGNOSS_BASE    = os.environ.get("SIGNOSS_BASE", "")

if not API_KEY:
    # fallback: read from dragon config
    try:
        cfg = Path.home() / ".dragon" / "config.yaml"
        if not cfg.exists():
            # try project config
            cfg = Path(__file__).parent.parent / "config.yaml"
        if cfg.exists():
            import yaml
            with open(cfg) as f:
                data = yaml.safe_load(f)
            API_KEY = data.get("dispatch", {}).get("global_api", {}).get("api_key", "")
            if not API_KEY:
                # direct top-level api_key
                API_KEY = data.get("api_key", "")
    except Exception:
        pass

# ═══════════════ LLM 调用 ═══════════════

def call_llm(prompt: str, system: str = "", max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """调用 andlapi.cn LLM API"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = requests.post(
        f"{API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
        timeout=600, verify=VERIFY
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]["content"]


def call_llm_json(prompt: str, system: str = "") -> dict:
    """调用 LLM 并解析 JSON 返回"""
    text = call_llm(prompt, system)
    # Extract JSON block
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


# ═══════════════ 文件操作 ═══════════════

def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, content: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def list_files(dir: str, pattern: str = "*") -> list:
    return sorted(Path(dir).glob(pattern))


# ═══════════════ SignOSS ═══════════════

def signoss_upload(filepath: str, remote_path: str = "") -> str:
    """上传图片到 SignOSS，返回公网 URL"""
    if not SIGNOSS_API_KEY or not SIGNOSS_BASE:
        raise RuntimeError("SignOSS not configured (set SIGNOSS_API_KEY and SIGNOSS_BASE)")

    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{SIGNOSS_BASE}/upload",
            headers={"Authorization": f"Bearer {SIGNOSS_API_KEY}"},
            files={"file": (os.path.basename(filepath), f)},
            data={"path": remote_path},
            timeout=60, verify=VERIFY
        )
    if resp.status_code != 200:
        raise RuntimeError(f"SignOSS upload failed: {resp.text[:200]}")
    return resp.json()["url"]


# ═══════════════ 视频生成 ═══════════════

def submit_video(prompt: str, model: str = "wan2.7-t2v", size: str = "1280*720",
                 ref_image: str = "", seed: int = -1) -> str:
    """提交视频生成任务，返回 task_id"""
    body = {"model": model, "prompt": prompt, "size": size}
    if ref_image:
        body["ref_image"] = ref_image
    if seed >= 0:
        body["seed"] = seed

    resp = requests.post(
        f"{API_BASE.replace('/v1','')}/v1/video/generations",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=body, timeout=30, verify=VERIFY
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Video submit failed: {resp.text[:200]}")
    data = resp.json()
    return data.get("task_id") or data.get("id")


def poll_video(task_id: str, max_wait: int = 600, interval: int = 10) -> dict:
    """轮询视频任务状态，返回 {status, url}"""
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        resp = requests.get(
            f"https://api.andlapi.cn/task/{task_id}",
            timeout=10, verify=VERIFY
        )
        if resp.status_code != 200:
            continue
        data = resp.json()
        status = data.get("status", "")
        print(f"  [{elapsed}s] {task_id[:20]}... → {status}")
        if status in ("SUCCESS", "COMPLETED", "succeeded"):
            return {"status": "SUCCESS", "url": data.get("url", "")}
        if status in ("FAILED", "ERROR", "failed"):
            return {"status": "FAILED", "error": str(data)}
    return {"status": "TIMEOUT"}


def download_video(url: str, output: str):
    """下载视频文件"""
    r = requests.get(url, timeout=120, stream=True, verify=VERIFY)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    size_mb = os.path.getsize(output) / (1024 * 1024)
    print(f"  Downloaded: {output} ({size_mb:.1f}MB)")


# ═══════════════ Seed 管理 ═══════════════

class SeedRegistry:
    """种子注册表：同场景/同角色尽量复用种子"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.seeds: dict = {}
        if self.path.exists():
            self.seeds = json.loads(self.path.read_text())

    def get(self, key: str, default: int = -1) -> int:
        """获取种子，不存在则生成新种子"""
        if key in self.seeds:
            return self.seeds[key]
        import random
        seed = default if default >= 0 else random.randint(1, 2147483647)
        self.seeds[key] = seed
        return seed

    def set(self, key: str, seed: int):
        self.seeds[key] = seed

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.seeds, indent=2))


# ═══════════════ 参数解析 ═══════════════

def arg_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--workspace", "-w", default=".", help="工作区目录")
    p.add_argument("--output", "-o", default="", help="输出文件路径")
    p.add_argument("--verbose", "-v", action="store_true")
    return p
