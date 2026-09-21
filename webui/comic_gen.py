"""漫画生成 pipeline：小说→漫画脚本→分镜面板→ComfyUI生成。

复用 novel_writer.py 的 API 调用模式（subprocess+curl），
漫画脚本用 deepseek-v3.2（JSON 稳定），面板生图走 ComfyUI。
"""
import json
import os
import re
import subprocess
import time
import requests

import yaml

CONFIG_PATH = os.path.expanduser("~/.dragon/config.yaml")
ENV_PATH = os.path.expanduser("~/.dragon/.env")

API_URL = "https://api.andlapi.cn/v1/chat/completions"
COMIC_MODEL = "deepseek-v3.2"  # 结构化 JSON 改编

# ComfyUI 配置
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://192.168.0.30:8188")
COMFYUI_MODEL = os.environ.get("COMIC_MODEL", "GuoFeng3.4.safetensors")  # 国风3.4 默认
COMFYUI_TIMEOUT = 300  # 生图超时

# 漫画输出尺寸（条漫竖屏：600×900 单面板）
PANEL_WIDTH = 600
PANEL_HEIGHT = 900


def _load_api_key():
    """读 API key：config.yaml global_api.api_key → .env 回退。"""
    key = ""
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        global_api = cfg.get("global_api", {})
        key = global_api.get("api_key", "")
        if not key:
            env_name = global_api.get("api_key_env", "")
            if env_name:
                key = os.environ.get(env_name, "")
    except Exception:
        pass
    if not key:
        try:
            with open(ENV_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("DRAGON_API_KEY="):
                        key = line.split("=", 1)[1].strip('"').strip("'")
                        if key:
                            break
        except Exception:
            pass
    return key


API_KEY = _load_api_key()


def _curl_post(payload, timeout=180):
    """Dragon 机器必须用 subprocess+curl（httpx/requests 会 hang）。"""
    cmd = ["curl", "-s", "-k", "--max-time", str(timeout), API_URL,
           "-H", f"Authorization: Bearer {API_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr}")
    resp = json.loads(r.stdout)
    if isinstance(resp, dict) and "error" in resp:
        raise RuntimeError(f"API error: {resp['error']}")
    return resp


def adapt_novel_to_comic(project, chapters, panels_per_chapter=8):
    """小说 → 漫画脚本（分镜面板描述）。

    返回格式：
    {
      "title": "漫画名",
      "genre": "类型",
      "chapters": [
        {
          "chapter_number": 1,
          "chapter_title": "标题",
          "panels": [
            {
              "panel_number": 1,
              "scene_desc": "面板场景描述（中文，含动作、情绪、氛围、构图）",
              "dialogue": "对白（多个角色用 [角色名]: 前缀）",
              "sfx": "音效文字（如：轰！、唰——、哒哒哒）",
              "camera": "特写/中景/远景/全景"
            }
          ]
        }
      ]
    }
    """
    novel_text = "\n\n".join(
        f"第{c['chapter_number']}章 {c.get('title', '')}\n{c.get('content', '')[:2000]}"
        for c in chapters
    )

    system = """你是专业漫画编剧，擅长将小说改编成漫画分镜。严格输出 JSON 格式，不要任何解释。

输出 JSON 格式：
{
  "title": "漫画名",
  "genre": "类型",
  "chapters": [
    {
      "chapter_number": 1,
      "chapter_title": "标题",
      "panels": [
        {
          "panel_number": 1,
          "scene_desc": "面板场景描述——详细描述画面内容、角色动作、表情、背景、氛围、构图要点。中文，50-150字。",
          "dialogue": "对白——多个角色用 [角色名]: 前缀，旁白用 [旁白]: 前缀。如无对白则为空字符串。",
          "sfx": "音效——如：轰！、唰——、哒哒哒、呼——。如无音效则为空字符串。",
          "camera": "特写/中景/远景/全景/俯视"
        }
      ]
    }
  ]
}

规则：
1. 忠实于小说剧情，每章提取关键场景改编成 6-12 个面板
2. 漫画分镜要有视觉冲击力：开场远景定场 → 中景对话 → 特写表情 → 动作大场面
3. 对白精炼，每句不超过 30 字，保持网感
4. 音效文字不超过 8 个字
5. 角色外貌、性格必须与小说一致
6. 直接输出 JSON，不要包裹在 ```json``` 中"""

    user = f"【小说内容】\n{novel_text}\n\n改编成漫画分镜，每章 {panels_per_chapter} 个面板左右，直接输出 JSON："

    payload = {
        "model": COMIC_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }
    resp = _curl_post(payload)
    content = resp["choices"][0]["message"]["content"]
    content = re.sub(r"```json\s*|```", "", content).strip()
    idx = content.find("{")
    if idx > 0:
        content = content[idx:]
    idx = content.rfind("}")
    if idx > 0:
        content = content[:idx + 1]
    return json.loads(content)


def build_panel_prompt(panel, characters, project, style="国风漫画"):
    """构建 ComfyUI 生图提示词（中文→英文翻译+ComfyUI 关键词）。

    Args:
        panel: 面板数据（scene_desc, dialogue, sfx, camera）
        characters: 角色列表
        project: 项目信息
        style: 画风描述

    Returns:
        str: 英文提示词
    """
    system = """你是 ComfyUI 提示词专家。将中文漫画分镜描述转换为英文 Stable Diffusion 提示词。

要求：
1. 输出格式：英文提示词，逗号分隔，长度 50-100 词
2. 包含：画风关键词（comic art, manga style, Chinese ink wash）、角色描述、场景、动作、构图
3. 画风：Chinese comic art, gufeng, ink wash painting style, manhua
4. 质量词：masterpiece, best quality, highly detailed, sharp focus
5. 负面词不需要输出，由调用方统一处理
6. 直接输出提示词文本，不要任何解释"""

    char_desc = "\n".join(
        f"- {c['name']}: {c.get('description', '')}" for c in (characters or [])
    )

    user = f"""【画风】{style}
【角色】\n{char_desc}
【场景描述】{panel.get('scene_desc', '')}
【对白】{panel.get('dialogue', '')}
【镜头】{panel.get('camera', '中景')}

输出英文提示词："""

    payload = {
        "model": COMIC_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.5,
        "max_tokens": 500,
    }
    resp = _curl_post(payload)
    prompt = resp["choices"][0]["message"]["content"].strip()
    # 清理可能的多余输出
    prompt = re.sub(r"^(prompt:|英文提示词:|English prompt:)\s*", "", prompt, flags=re.IGNORECASE)
    return prompt


def generate_comic_panel(panel_prompt, negative_prompt=None, seed=None, width=None, height=None):
    """通过 ComfyUI API 生成一张漫画面板。

    Args:
        panel_prompt: 英文提示词
        negative_prompt: 负面提示词
        seed: 随机种子（None=随机）
        width: 宽度（默认 PANEL_WIDTH）
        height: 高度（默认 PANEL_HEIGHT）

    Returns:
        dict: {"success": True, "image_base64": "...", "seed": 12345}
    """
    if negative_prompt is None:
        negative_prompt = "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry, ugly, deformed"

    width = width or PANEL_WIDTH
    height = height or PANEL_HEIGHT

    # 构建 ComfyUI workflow
    workflow = _build_comfyui_workflow(panel_prompt, negative_prompt, seed, width, height)

    # 提交到 ComfyUI
    try:
        # Step 1: 提交 workflow
        r = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow},
                         timeout=30)
        r.raise_for_status()
        prompt_id = r.json()["prompt_id"]

        # Step 2: 轮询直到完成
        return _wait_comfyui_result(prompt_id)
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def _build_comfyui_workflow(positive_prompt, negative_prompt, seed, width, height):
    """构建 ComfyUI SDXL 文生图 workflow（国风3.4 模型）。"""
    import random
    if seed is None:
        seed = random.randint(1, 2**31 - 1)

    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 30,
                "cfg": 7,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": COMFYUI_MODEL},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": f"masterpiece, best quality, highly detailed, {positive_prompt}",
                "clip": ["4", 1],
            },
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt,
                "clip": ["4", 1],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2],
            },
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "ComicGen",
                "images": ["8", 0],
            },
        },
    }


def _wait_comfyui_result(prompt_id, timeout=COMFYUI_TIMEOUT, poll_interval=5):
    """轮询 ComfyUI 直到图片生成完成，返回结果。"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
            r.raise_for_status()
            history = r.json()
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                for node_id, node_output in outputs.items():
                    images = node_output.get("images", [])
                    if images:
                        img = images[0]
                        return {
                            "success": True,
                            "filename": img.get("filename", ""),
                            "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", ""),
                            "prompt_id": prompt_id,
                        }
            # 还没完成，继续等
            time.sleep(poll_interval)
        except requests.exceptions.RequestException:
            time.sleep(poll_interval)

    return {"success": False, "error": f"ComfyUI timeout after {timeout}s"}


def check_comfyui_ready():
    """检查 ComfyUI 是否在线且模型已加载。"""
    try:
        r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=10)
        if r.status_code == 200:
            return True, "ComfyUI 在线"
        return False, f"ComfyUI 返回状态码 {r.status_code}"
    except requests.exceptions.RequestException as e:
        return False, f"ComfyUI 不可达: {e}"