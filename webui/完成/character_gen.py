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

# Read API key from Dragon config
CONFIG_PATH = os.path.expanduser("~/.dragon/config.yaml")
try:
    with open(CONFIG_PATH) as f:
        _config = yaml.safe_load(f)
    API_KEY = _config.get("global_api", {}).get("api_key", "")
except Exception:
    API_KEY = ""

# SDXL negative prompt
NEGATIVE = "ugly, deformed, disfigured, bad anatomy, extra limbs, blurry, low quality, watermark, text, logo, signature, nsfw, nude, naked, cartoon, anime, painting, illustration, 3d render, plastic skin, doll-like, uncanny valley, asymmetrical face, cross-eyed, bad teeth, wrinkles, old, duplicate, clone, same face, identical"

VIEW_CONFIGS = {
    "portrait": {
        "suffix": "close-up portrait, face closeup, sharp focus on eyes, detailed facial features, professional headshot",
        "width": 896, "height": 1152
    },
    "fullbody_front": {
        "suffix": "full body shot, standing facing camera, full outfit visible, character design sheet, front view reference",
        "width": 896, "height": 1152
    },
    "fullbody_side": {
        "suffix": "full body shot, side profile view, standing sideways, showing body silhouette and profile, character design sheet, side view reference",
        "width": 896, "height": 1152
    },
    "fullbody_back": {
        "suffix": "full body shot, standing facing away from camera, back view, showing back outfit and hairstyle from behind, character design sheet, back view reference",
        "width": 896, "height": 1152
    }
}


def check_comfyui():
    """Check if ComfyUI is reachable."""
    try:
        r = requests.get(f"{COMFY_HOST}/system_stats", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def generate_character_views(project_name, char_name, char_desc, views=None):
    """
    Generate 4-view character portraits via ComfyUI SDXL.
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
        prompt = f"{char_desc}, {cfg['suffix']}, cinematic lighting, photorealistic, 8K, masterpiece, best quality, highly detailed"

        wf = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": int(time.time() * 1000) % (2**31),
                "steps": 30, "cfg": 7.0,
                "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                "latent_image": ["5", 0]
            }},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {
                "ckpt_name": "sd_xl_base_1.0.safetensors"
            }},
            "5": {"class_type": "EmptyLatentImage", "inputs": {
                "width": cfg["width"], "height": cfg["height"], "batch_size": 1
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
        prompt_id = r.json()["prompt_id"]

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


def _wan_curl_post(payload, timeout=60):
    """Dragon machine: subprocess+curl for Wan2.7 API."""
    cmd = ["curl", "-s", "--max-time", str(timeout), WAN_API,
           "-H", f"Authorization: Bearer {API_KEY}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(payload, ensure_ascii=False)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if r.returncode != 0:
        raise RuntimeError(f"curl failed: {r.stderr}")
    return json.loads(r.stdout)


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

def generate_character_views(project_name, char_name, char_desc, views=None, backend="comfyui"):
    """
    Unified character generation. backend: 'comfyui' | 'wan'
    """
    if backend == "wan":
        return generate_character_views_wan(project_name, char_name, char_desc, views)
    else:
        return generate_character_views_comfyui(project_name, char_name, char_desc, views)
