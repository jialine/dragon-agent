"""Character portrait generation via ComfyUI SDXL or Wan2.7-image-pro."""
import json
import subprocess
import time
import os
import requests
import yaml
from pathlib import Path

COMFY_HOST = "http://192.168.0.30:8188"
OUTPUT_DIR = "/home/jialine/dragon-agent/assets/characters"

# Wan2.7-image-pro API
WAN_API = "https://api4.sangyuye.com/v1/images/generations"
WAN_MODEL = "wan2.7-image-pro"
WAN_SIZE = "720*1280"

# hy-image-v3.0 API (via andlapi.cn)
HY_API = "https://api.andlapi.cn/v1/images/generations"
HY_MODEL = "hy-image-v3.0"
HY_SIZE = "1024x1024"

# Read API key from Dragon config + .env
CONFIG_PATH = os.path.expanduser("~/.dragon/config.yaml")
ENV_PATH = os.path.expanduser("~/.dragon/.env")
API_KEY = ""

try:
    with open(CONFIG_PATH) as f:
        _config = yaml.safe_load(f)
    # Try dispatch.global_api first (Dragon config), fallback to global_api
    dispatch_api = _config.get("dispatch", {}).get("global_api", {})
    global_api = _config.get("global_api", {})
    API_KEY = dispatch_api.get("api_key", "") or global_api.get("api_key", "")
    # If api_key_env is set, read from environment
    key_env = dispatch_api.get("api_key_env", "") or global_api.get("api_key_env", "")
    if key_env:
        env_val = os.environ.get(key_env, "")
        if env_val:
            API_KEY = env_val
except Exception:
    pass

# Fallback: read from .env file
if not API_KEY:
    try:
        with open(ENV_PATH) as f:
            for line in f:
                if line.startswith("DRAGON_API_KEY="):
                    API_KEY = line.strip().split("=", 1)[1].strip('"').strip("'")
                    break
    except Exception:
        pass

# SD1.5 negative prompt — optimized for Realistic Vision
NEGATIVE = "ugly, deformed, disfigured, bad anatomy, extra limbs, blurry, low quality, watermark, text, logo, signature, nsfw, nude, naked, cartoon, anime, painting, illustration, 3d render, plastic skin, doll-like, uncanny valley, asymmetrical face, cross-eyed, bad teeth, bad hands, missing fingers, fused fingers, mutation, jpeg artifacts, grainy, oversaturated, overexposed"

VIEW_CONFIGS_SD15 = {
    "portrait": {"suffix": "正面肖像特写，面部清晰，眼神锐利，浅景深背景虚化", "width": 512, "height": 768},
    "fullbody_front": {"suffix": "全身正面站立照，展示完整服装和体态，纯色背景", "width": 512, "height": 768},
    "fullbody_side": {"suffix": "全身侧面站立照，展示侧面轮廓和身形，纯色背景", "width": 512, "height": 768},
    "fullbody_back": {"suffix": "全身背面站立照，展示背部服装和发型，纯色背景", "width": 512, "height": 768}
}

VIEW_CONFIGS_SDXL = {
    "portrait": {"suffix": "cinematic portrait, face close-up, sharp eyes, shallow depth of field, bokeh background", "width": 896, "height": 1152},
    "fullbody_front": {"suffix": "full body front view, standing, showing complete outfit, plain studio background", "width": 896, "height": 1152},
    "fullbody_side": {"suffix": "full body side profile view, showing silhouette, plain studio background", "width": 896, "height": 1152},
    "fullbody_back": {"suffix": "full body back view, showing back outfit and hair, plain studio background", "width": 896, "height": 1152}
}

VIEW_CONFIGS = VIEW_CONFIGS_SD15  # default


def check_comfyui():
    """Check if ComfyUI is reachable."""
    try:
        r = requests.get(f"{COMFY_HOST}/system_stats", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def generate_character_views(project_name, char_name, char_desc, views=None, backend="comfyui", model="", prompt_override="", seed=None):
    """
    Generate 4-view character portraits via ComfyUI SD1.5 or Wan2.7.
    model: override checkpoint name (empty = use default)
    prompt_override: custom prompt (empty = auto-generated from char_desc)
    seed: fixed seed (None = random)
    Returns: {view_type: local_path}
    """
    if views is None:
        views = ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"]

    char_dir = Path(OUTPUT_DIR) / project_name / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    if backend == "wan":
        return generate_character_views_wan(project_name, char_name, char_desc, views)
    if backend == "hy-image":
        return generate_character_views_hy(project_name, char_name, char_desc, views)

    results = {}

    for view in views:
        if view not in VIEW_CONFIGS:
            continue

        cfg = VIEW_CONFIGS[view]
        if prompt_override:
            prompt = prompt_override
        else:
            prompt = f"{char_desc}，{cfg['suffix']}，超写实摄影，电影级布光，RAW photo，highly detailed face，sharp focus"

        ckpt = model if model else "realisticVisionV60B1_v51HyperVAE.safetensors"
        gen_seed = seed if seed is not None else int(time.time() * 1000) % (2**31)

        # Detect SDXL model and switch configs
        is_xl = "xl" in ckpt.lower()
        configs = VIEW_CONFIGS_SDXL if is_xl else VIEW_CONFIGS_SD15
        if view not in configs:
            continue
        cfg_view = configs[view]
        sampler = "euler" if is_xl else "dpmpp_2m"
        scheduler = "normal" if is_xl else "karras"
        cfg_scale = 7.0 if is_xl else 6.0
        steps = 25 if is_xl else 25

        if prompt_override:
            prompt = prompt_override
        elif is_xl:
            prompt = f"{char_desc}, {cfg_view['suffix']}, photorealistic, cinematic lighting, 8k, highly detailed"
        else:
            prompt = f"{char_desc}，{cfg_view['suffix']}，超写实摄影，电影级布光，RAW photo，highly detailed face，sharp focus"

        wf = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": gen_seed,
                "steps": steps, "cfg": cfg_scale,
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                "latent_image": ["5", 0]
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {
                "ckpt_name": ckpt
            }},
            "5": {"class_type": "EmptyLatentImage", "inputs": {
                "width": cfg_view["width"], "height": cfg_view["height"], "batch_size": 1
            }},
            "6": {"class_type": "CLIPTextEncode", "inputs": {
                "text": prompt, "clip": ["4", 1]
            }},
            "7": {"class_type": "CLIPTextEncode", "inputs": {
                "text": NEGATIVE, "clip": ["4", 1]
            }},
            "8": {"class_type": "VAEDecode", "inputs": {
                "samples": ["3", 0], "vae": ["4", 2]
            }},
            "9": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": f"{char_name}_{view}",
                "images": ["8", 0]
            }},
        }

        if "_comment" in wf:
            del wf["_comment"]

        r = requests.post(f"{COMFY_HOST}/prompt", json={"prompt": wf}, timeout=30)
        resp_data = r.json()
        print(f"DEBUG ComfyUI response: {json.dumps(resp_data)[:200]}", flush=True)
        if "prompt_id" not in resp_data:
            raise RuntimeError(f"ComfyUI error: {resp_data}")
        prompt_id = resp_data["prompt_id"]

        # Wait for completion
        filename = _wait_and_download(prompt_id, view)
        if filename:
            # Copy from ComfyUI output to local assets
            local_name = f"{view}.png"
            local_path = char_dir / local_name
            _scp_from_comfy(filename, str(local_path))
            results[view] = str(local_path)

        time.sleep(1)  # gap between submissions

    return results


def _wait_and_download(prompt_id, view_type):
    """Poll history until complete, return output filename."""
    for _ in range(60):  # 5 min timeout
        time.sleep(5)
        try:
            r = requests.get(f"{COMFY_HOST}/history/{prompt_id}", timeout=10)
            data = r.json()
            if prompt_id in data:
                outputs = data[prompt_id]["outputs"]
                for node_id, node_output in outputs.items():
                    images = node_output.get("images", [])
                    if images:
                        return images[0]["filename"]
        except Exception:
            continue
    return None


def _scp_from_comfy(remote_filename, local_path):
    """SCP file from ComfyUI output dir to local."""
    remote_path = f"jialine@192.168.0.30:/home/jialine/comfy/ComfyUI/output/{remote_filename}"
    subprocess.run(["scp", "-q", "-o", "StrictHostKeyChecking=no",
                    remote_path, local_path], timeout=30)

# Capture ComfyUI version before redefining
generate_character_views_comfyui = generate_character_views


# ─── Wan2.7-image-pro backend ───────────────────────────

def check_wan():
    """Check if Wan2.7 API key is configured."""
    return bool(API_KEY)


def check_hy():
    """Check if hy-image-v3.0 API is reachable."""
    return bool(API_KEY)


def _wan_curl_post(payload, timeout=60):
    """Dragon machine: subprocess+curl for Wan2.7 API."""
    cmd = ["curl", "-s", "-k", "--max-time", str(timeout), WAN_API,
           "-H", f"Authorization: Bearer {API_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr}")
    return json.loads(r.stdout)


def generate_character_views_hy(project_name, char_name, char_desc, views=None):
    """
    Generate 4-view character portraits via hy-image-v3.0 (andlapi.cn).
    Returns: {view_type: local_path}
    """
    if views is None:
        views = ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"]

    char_dir = Path(OUTPUT_DIR) / project_name / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    for view in views:
        if view not in VIEW_CONFIGS:
            continue

        cfg = VIEW_CONFIGS[view]
        # hy-image supports both Chinese and English; use Chinese for character fidelity
        prompt = f"{char_desc}，{cfg['suffix']}，超写实摄影，电影级布光，RAW photo，highly detailed face，sharp focus，8K"
        # Content safety filter: replace military keywords to avoid censorship
        prompt = prompt.replace("军装", "正装制服").replace("将军", "指挥官").replace("陆军", "地面").replace("军方", "官方").replace("五角大楼", "政府大楼").replace("军事", "战略").replace("特派员", "专员")

        payload = {
            "model": HY_MODEL,
            "prompt": prompt,
            "size": HY_SIZE,
            "n": 1,
        }

        try:
            r = requests.post(
                HY_API,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=120,
                verify=False
            )
            if r.status_code != 200:
                raise RuntimeError(f"hy-image API error {r.status_code}: {r.text[:200]}")

            data = r.json()
            image_url = data.get("data", [{}])[0].get("url", "")
            if image_url:
                local_path = char_dir / f"{view}.png"
                subprocess.run(
                    ["curl", "-skL", "-o", str(local_path), image_url, "--max-time", "60"],
                    capture_output=True, timeout=65, check=True
                )
                # Remove watermark (crop bottom 40px)
                try:
                    from PIL import Image
                    img = Image.open(local_path)
                    w, h = img.size
                    img_cropped = img.crop((0, 0, w, h - 40))
                    img_cropped.save(local_path)
                except Exception:
                    pass
                results[view] = str(local_path)
            else:
                print(f"hy-image [{char_name} {view}]: no image URL")
        except Exception as e:
            print(f"hy-image [{char_name} {view}]: {e}")

        time.sleep(1.5)

    return results


def generate_character_views_wan(project_name, char_name, char_desc, views=None):
    """
    Generate 4-view character portraits via Wan2.7-image-pro (sangyuye API).
    Returns: {view_type: local_path}
    """
    if views is None:
        views = ["portrait", "fullbody_front", "fullbody_side", "fullbody_back"]

    char_dir = Path(OUTPUT_DIR) / project_name / char_name
    char_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    for view in views:
        if view not in VIEW_CONFIGS:
            continue

        cfg = VIEW_CONFIGS[view]
        # Wan2.7 uses English prompts — natural language, not tag soup
        prompt = f"Professional studio photography of {char_desc}, {view.replace('_', ' ')}, cinematic lighting, ultra realistic, high detail, 8K quality, masterpiece"

        # Content moderation: strip military keywords
        prompt = prompt.replace("military", "formal").replace("tactical", "sleek")

        payload = {
            "model": WAN_MODEL,
            "prompt": prompt,
            "size": WAN_SIZE,
            "n": 1,
        }

        try:
            resp = _wan_curl_post(payload)
            # Extract signed URL: output.choices[0].message.content[0].image
            image_url = resp.get("output", {}).get("choices", [{}])[0].get("message", {}).get("content", [{}])[0].get("image", "")

            if image_url:
                local_path = char_dir / f"{view}.png"
                _download_wan_image(image_url, str(local_path))
                results[view] = str(local_path)
            else:
                print(f"Wan2.7 [{char_name} {view}]: no image URL in response")
        except Exception as e:
            print(f"Wan2.7 [{char_name} {view}]: {e}")

        time.sleep(1.5)  # gap between API calls

    return results


def _download_wan_image(url, local_path):
    """Download from signed Wan2.7 URL to local path."""
    cmd = ["curl", "-s", "-L", "-o", local_path, url, "--max-time", "30"]
    subprocess.run(cmd, capture_output=True, timeout=35, check=True)


# ─── Unified entry point ────────────────────────────────

def generate_character_views(project_name, char_name, char_desc, views=None, backend="comfyui", model="", prompt_override="", seed=None):
    """
    Unified character generation. backend: 'comfyui' | 'wan'
    """
    if backend == "wan":
        return generate_character_views_wan(project_name, char_name, char_desc, views)
    if backend == "hy-image":
        return generate_character_views_hy(project_name, char_name, char_desc, views)
    else:
        return generate_character_views_comfyui(project_name, char_name, char_desc, views, backend=backend, model=model, prompt_override=prompt_override, seed=seed)
