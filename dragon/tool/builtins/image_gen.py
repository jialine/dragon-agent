"""
Dragon Agent — Image Generation Tools
=====================================

Pluggable image generation with ComfyUI backend support.
Falls back to DummyBackend when no real backend is configured.

Backends:
    - StabilityAIBackend: Stability AI cloud API (SD3.5, no GPU needed)
    - ReplicateBackend: Replicate cloud API (SDXL, Flux, pay-per-use)
    - RunningHubBackend: RunningHub.ai cloud API (Chinese ComfyUI platform)
    - ComfyUIBackend: Local ComfyUI server (requires NVIDIA GPU)
    - DummyBackend: No-op placeholder (no backend configured)

Tools:
    - image_generate: Generate an image from a text prompt
    - image_models: List available checkpoints/models
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("dragon.tool.builtins.image_gen")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_DIR = Path.home() / "dragon_data" / "images"
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_TIMEOUT = 300  # seconds
POLL_INTERVAL = 2.0  # seconds between polling ComfyUI
MAX_POLL_ITERATIONS = 60  # max 2 minutes of polling (60 * 2s)

# Style presets: descriptive words prepended to the prompt
STYLE_PRESETS: Dict[str, str] = {
    "anime": "anime style, studio ghibli, vibrant",
    "realistic": "photorealistic, 8k, highly detailed",
    "oil painting": "oil painting style, textured brushstrokes",
    "watercolor": "watercolor painting, soft edges, artistic",
    "pixel art": "pixel art, 8-bit, retro game style",
    "cyberpunk": "cyberpunk, neon lights, futuristic, blade runner style",
}

# SDXL txt2img workflow template — keys are ComfyUI node IDs
SDXL_WORKFLOW_TEMPLATE: Dict[str, Any] = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["4", 1], "text": "prompt here"},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["4", 1], "text": ""},
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "dragon_gen", "images": ["8", 0]},
    },
}


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _apply_style(prompt: str, style: str) -> str:
    """Prepend style descriptor words to the prompt if a known style is given."""
    if not style:
        return prompt
    style_lower = style.strip().lower()
    if style_lower in STYLE_PRESETS:
        styled = f"{STYLE_PRESETS[style_lower]}, {prompt}"
        logger.debug("Applied style '%s': %s", style, styled)
        return styled
    return prompt


def _build_workflow(
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    seed: int,
    model: str,
) -> dict:
    """Build a ComfyUI workflow JSON with the given parameters."""
    import copy

    workflow = copy.deepcopy(SDXL_WORKFLOW_TEMPLATE)

    # Inject parameters
    workflow["3"]["inputs"]["seed"] = seed
    workflow["3"]["inputs"]["steps"] = steps
    workflow["4"]["inputs"]["ckpt_name"] = model
    workflow["5"]["inputs"]["width"] = width
    workflow["5"]["inputs"]["height"] = height
    workflow["6"]["inputs"]["text"] = prompt
    workflow["7"]["inputs"]["text"] = negative_prompt

    return workflow


def _resolve_seed(seed: int) -> int:
    """Resolve seed: if -1, generate a random seed."""
    if seed == -1:
        return random.randint(0, 2**32 - 1)
    return seed


# ────────────────────────────────────────────────────────────────────
# Abstract Backend
# ────────────────────────────────────────────────────────────────────


class ImageGenBackend(ABC):
    """Abstract interface for image generation backends."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        seed: int = -1,
        model: str = "sd_xl_base_1.0.safetensors",
    ) -> dict:
        """Generate an image.

        Returns:
            {'success': bool, 'images': [{'path': str, 'url': str}], 'error': str}
        """
        ...

    @abstractmethod
    async def list_models(self) -> list:
        """List available models/checkpoints.

        Returns:
            List of model name strings.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check whether the backend is reachable and healthy.

        Returns:
            True if healthy, False otherwise.
        """
        ...


# ────────────────────────────────────────────────────────────────────
# Dummy Backend (test / offline fallback)
# ────────────────────────────────────────────────────────────────────


class DummyBackend(ImageGenBackend):
    """Placeholder backend that returns helpful error messages.

    Used when no real image generation backend is configured.
    """

    async def generate(
        self,
        prompt: str = "",
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        seed: int = -1,
        model: str = "sd_xl_base_1.0.safetensors",
    ) -> dict:
        return {
            "success": False,
            "error": (
                "No image generation backend configured. "
                "Install ComfyUI or configure an API backend."
            ),
            "images": [],
        }

    async def list_models(self) -> list:
        return []

    async def health_check(self) -> bool:
        return False


# ────────────────────────────────────────────────────────────────────
# ComfyUI Backend
# ────────────────────────────────────────────────────────────────────


class ComfyUIBackend(ImageGenBackend):
    """Image generation backend that delegates to a local ComfyUI server.

    ComfyUI is expected to be running on ``http://127.0.0.1:8188`` by default.
    """

    def __init__(self, base_url: str = DEFAULT_COMFY_URL, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (or create) a shared httpx AsyncClient."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Health ──────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Ping ComfyUI's system_stats endpoint to verify it is reachable."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/system_stats")
            return resp.status_code == 200
        except Exception:
            return False

    # ── Model listing ───────────────────────────────────────────────

    async def list_models(self) -> list:
        """Fetch the list of installed checkpoints from ComfyUI.

        Calls ``/object_info/CheckpointLoaderSimple`` and reads the
        ``ckpt_name`` input property.
        """
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/object_info/CheckpointLoaderSimple")
            if resp.status_code != 200:
                logger.warning("ComfyUI object_info returned HTTP %d", resp.status_code)
                return []
            data = resp.json()
            # The ckpt_name input contains a list of available checkpoints
            ckpt_input = (
                data.get("CheckpointLoaderSimple", {})
                .get("input", {})
                .get("required", {})
                .get("ckpt_name", [])
            )
            if isinstance(ckpt_input, list) and len(ckpt_input) >= 2:
                # First element is the list, second is default
                return ckpt_input[0] if isinstance(ckpt_input[0], list) else []
            return []
        except Exception as e:
            logger.warning("Failed to list ComfyUI models: %s", e)
            return []

    # ── Generate ────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        seed: int = -1,
        model: str = "sd_xl_base_1.0.safetensors",
    ) -> dict:
        """Submit an SDXL txt2img job to ComfyUI and wait for completion.

        Steps:
        1. Check server reachability
        2. Build workflow JSON with injected params
        3. POST workflow to ``/prompt``
        4. Poll ``/history/{prompt_id}`` until complete
        5. Download generated images to ``dragon_data/images/``
        6. Return result dict
        """
        # ── 1. Health check ─────────────────────────────────────
        if not await self.health_check():
            return {
                "success": False,
                "error": (
                    f"ComfyUI server is not reachable at {self.base_url}. "
                    "Make sure ComfyUI is running and accessible."
                ),
                "images": [],
            }

        # ── 2. Resolve seed & build workflow ────────────────────
        resolved_seed = _resolve_seed(seed)
        workflow = _build_workflow(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            seed=resolved_seed,
            model=model,
        )

        # ── 3. Submit workflow ──────────────────────────────────
        client = await self._get_client()
        try:
            resp = await client.post(
                f"{self.base_url}/prompt",
                json={"prompt": workflow},
            )
            if resp.status_code != 200:
                error_text = resp.text[:500]
                logger.error("ComfyUI /prompt returned HTTP %d: %s", resp.status_code, error_text)
                return {
                    "success": False,
                    "error": f"ComfyUI rejected workflow (HTTP {resp.status_code}): {error_text}",
                    "images": [],
                }
            prompt_data = resp.json()
        except httpx.RequestError as e:
            logger.error("Failed to reach ComfyUI: %s", e)
            return {
                "success": False,
                "error": f"Failed to connect to ComfyUI: {e}",
                "images": [],
            }

        prompt_id = prompt_data.get("prompt_id")
        if not prompt_id:
            return {
                "success": False,
                "error": "ComfyUI did not return a prompt_id",
                "images": [],
            }

        logger.info(
            "ComfyUI job submitted: prompt_id=%s, seed=%d, steps=%d, model=%s",
            prompt_id,
            resolved_seed,
            steps,
            model,
        )

        # ── 4. Poll for completion ──────────────────────────────
        history_url = f"{self.base_url}/history/{prompt_id}"
        output_images = []

        for iteration in range(MAX_POLL_ITERATIONS):
            await asyncio.sleep(POLL_INTERVAL)

            try:
                hist_resp = await client.get(history_url)
                if hist_resp.status_code != 200:
                    continue
                history = hist_resp.json()
            except Exception:
                continue

            # ComfyUI history returns {prompt_id: {...}} when complete
            if prompt_id not in history:
                continue

            entry = history[prompt_id]
            outputs = entry.get("outputs", {})

            if not outputs:
                continue

            # Collect image metadata from SaveImage node (node "9")
            for node_id, node_output in outputs.items():
                images = node_output.get("images", [])
                for img_info in images:
                    filename = img_info.get("filename", "")
                    subfolder = img_info.get("subfolder", "")
                    img_type = img_info.get("type", "output")
                    output_images.append({
                        "filename": filename,
                        "subfolder": subfolder,
                        "type": img_type,
                    })

            if output_images:
                logger.info(
                    "ComfyUI job completed: prompt_id=%s, images=%d (iteration %d)",
                    prompt_id,
                    len(output_images),
                    iteration + 1,
                )
                break
        else:
            # Polling exhausted
            return {
                "success": False,
                "error": (
                    f"ComfyUI generation timed out after "
                    f"{MAX_POLL_ITERATIONS * POLL_INTERVAL:.0f}s "
                    f"(prompt_id={prompt_id})"
                ),
                "images": [],
            }

        # ── 5. Download images ──────────────────────────────────
        saved_images: List[dict] = []
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        for img_info in output_images:
            filename = img_info["filename"]
            subfolder = img_info.get("subfolder", "")
            img_type = img_info.get("type", "output")

            # ComfyUI serves images via /view
            if subfolder:
                view_url = (
                    f"{self.base_url}/view"
                    f"?filename={filename}"
                    f"&subfolder={subfolder}"
                    f"&type={img_type}"
                )
            else:
                view_url = (
                    f"{self.base_url}/view"
                    f"?filename={filename}"
                    f"&type={img_type}"
                )

            try:
                img_resp = await client.get(view_url)
                if img_resp.status_code != 200:
                    logger.warning("Failed to download image %s: HTTP %d", filename, img_resp.status_code)
                    continue

                # Determine local path
                local_name = f"{prompt_id}_{filename}"
                local_path = DEFAULT_OUTPUT_DIR / local_name
                local_path.write_bytes(img_resp.content)

                saved_images.append({
                    "path": str(local_path),
                    "url": view_url,
                    "filename": filename,
                    "size_bytes": len(img_resp.content),
                })

                logger.info("Saved image: %s (%d bytes)", local_path, len(img_resp.content))
            except Exception as e:
                logger.warning("Failed to download image %s: %s", filename, e)

        # ── 6. Return result ────────────────────────────────────
        return {
            "success": len(saved_images) > 0,
            "images": saved_images,
            "prompt_id": prompt_id,
            "seed": resolved_seed,
            "error": "" if saved_images else "No images were generated or downloaded",
        }


# ────────────────────────────────────────────────────────────────────
# Cloud Backends (no GPU required)
# ────────────────────────────────────────────────────────────────────

DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "dragon_data", "images")


class StabilityAIBackend(ImageGenBackend):
    """Stability AI cloud API (no GPU needed).

    Env: ``STABILITY_API_KEY`` — get from https://platform.stability.ai
    Free tier includes 25 credits.  Supports SD3.5, SD3, Ultra, Core.
    """

    def __init__(self, api_key: str = "", base_url: str = "https://api.stability.ai"):
        self.api_key = api_key or os.getenv("STABILITY_API_KEY", "")
        self.base_url = base_url

    async def generate(self, prompt: str, negative_prompt: str = "", width: int = 1024,
                       height: int = 1024, steps: int = 30, seed: int = 0,
                       model: str = "", **kw: Any) -> dict:
        if not self.api_key:
            return {"success": False, "error": "STABILITY_API_KEY not set"}

        # Map size to aspect ratio
        aspect_ratio = _size_to_aspect(width, height)
        model_name = model or "sd3.5-large"

        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "image/*"}
        data = {"prompt": prompt, "output_format": "png"}
        if negative_prompt:
            data["negative_prompt"] = negative_prompt
        if seed != -1:
            data["seed"] = seed

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.base_url}/v2beta/stable-image/generate/sd3",
                    headers=headers,
                    files={"none": ("", "")},
                    data={**data, "aspect_ratio": aspect_ratio, "model": model_name},
                )
                if resp.status_code != 200:
                    err = resp.json().get("errors", [resp.text])[0] if resp.headers.get("content-type","")=="application/json" else resp.text[:200]
                    return {"success": False, "error": f"Stability API: {err}"}

                os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
                filename = f"stability_{uuid.uuid4().hex[:8]}.png"
                filepath = os.path.join(DEFAULT_OUTPUT_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(resp.content)

                return {"success": True, "images": [{"path": filepath, "filename": filename,
                        "size_bytes": len(resp.content), "url": ""}], "seed": seed}
        except Exception as e:
            return {"success": False, "error": f"Stability API: {e}"}

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.base_url}/v1/user/account", headers={"Authorization": f"Bearer {self.api_key}"})
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list:
        return ["sd3.5-large", "sd3.5-medium", "sd3-large-turbo", "sd3-medium",
                "stable-image-ultra", "stable-image-core"]


class ReplicateBackend(ImageGenBackend):
    """Replicate cloud API (pay-per-use, hundreds of models).

    Env: ``REPLICATE_API_TOKEN`` — get from https://replicate.com
    """

    def __init__(self, api_token: str = "", model: str = "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b"):
        self.api_token = api_token or os.getenv("REPLICATE_API_TOKEN", "")
        self.model = model

    async def generate(self, prompt: str, negative_prompt: str = "", width: int = 1024,
                       height: int = 1024, steps: int = 30, seed: int = -1,
                       model: str = "", **kw: Any) -> dict:
        if not self.api_token:
            return {"success": False, "error": "REPLICATE_API_TOKEN not set"}

        model_name = model or self.model
        headers = {"Authorization": f"Token {self.api_token}", "Content-Type": "application/json"}
        resolved_seed = seed if seed != -1 else random.randint(0, 2**32 - 1)
        body = {"version": model_name, "input": {"prompt": prompt, "negative_prompt": negative_prompt,
                "width": width, "height": height, "num_inference_steps": steps, "seed": resolved_seed}}

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                # Submit
                r = await client.post("https://api.replicate.com/v1/predictions", headers=headers, json=body)
                if r.status_code != 201:
                    return {"success": False, "error": f"Replicate: {r.text[:200]}"}
                pred = r.json()

                # Poll
                for _ in range(60):
                    await asyncio.sleep(2)
                    r = await client.get(pred["urls"]["get"], headers=headers)
                    pred = r.json()
                    if pred["status"] == "succeeded":
                        break
                    if pred["status"] == "failed":
                        return {"success": False, "error": f"Replicate failed: {pred.get('error','')}"}
                else:
                    return {"success": False, "error": "Replicate timeout"}

                # Download
                images = []
                for i, url in enumerate(pred.get("output", [])):
                    r = await client.get(url)
                    filename = f"replicate_{uuid.uuid4().hex[:8]}.png"
                    filepath = os.path.join(DEFAULT_OUTPUT_DIR, filename)
                    os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
                    with open(filepath, "wb") as f:
                        f.write(r.content)
                    images.append({"path": filepath, "filename": filename, "size_bytes": len(r.content), "url": url})

                return {"success": True, "images": images, "seed": resolved_seed}
        except Exception as e:
            return {"success": False, "error": f"Replicate: {e}"}

    async def health_check(self) -> bool:
        if not self.api_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://api.replicate.com/v1/models", headers={"Authorization": f"Token {self.api_token}"})
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list:
        return ["stability-ai/sdxl", "black-forest-labs/flux-schnell", "black-forest-labs/flux-dev",
                "stability-ai/stable-diffusion-3.5-large"]


class RunningHubBackend(ImageGenBackend):
    """RunningHub.ai cloud API — Chinese ComfyUI cloud platform.

    Env: ``RUNNINGHUB_API_KEY`` — get from https://www.runninghub.ai
    Uses ComfyUI-compatible API endpoint.
    """

    def __init__(self, api_key: str = "", base_url: str = "https://www.runninghub.ai"):
        self.api_key = api_key or os.getenv("RUNNINGHUB_API_KEY", "")
        self.base_url = base_url

    async def generate(self, prompt: str, negative_prompt: str = "", width: int = 1024,
                       height: int = 1024, steps: int = 20, seed: int = -1,
                       model: str = "", **kw: Any) -> dict:
        if not self.api_key:
            return {"success": False, "error": "RUNNINGHUB_API_KEY not set"}

        resolved_seed = seed if seed != -1 else random.randint(0, 2**32 - 1)

        # RunningHub uses ComfyUI-compatible API, submit a simple txt2img workflow
        workflow = {
            "3": {"class_type": "KSampler", "inputs": {"seed": resolved_seed, "steps": steps,
                  "cfg": 7, "sampler_name": "euler", "scheduler": "normal", "denoise": 1,
                  "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model or "sd_xl_base_1.0.safetensors"}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "dragon_gen", "images": ["8", 0]}},
        }

        try:
            async with httpx.AsyncClient(timeout=300) as client:
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
                r = await client.post(f"{self.base_url}/api/prompt", headers=headers, json={"prompt": workflow})
                if r.status_code != 200:
                    return {"success": False, "error": f"RunningHub: {r.text[:200]}"}
                data = r.json()
                prompt_id = data.get("prompt_id", "")

                # Poll
                for _ in range(60):
                    await asyncio.sleep(3)
                    r = await client.get(f"{self.base_url}/api/history/{prompt_id}", headers=headers)
                    if r.status_code == 200:
                        history = r.json()
                        if prompt_id in history:
                            outputs = history[prompt_id].get("outputs", {})
                            if outputs:
                                break
                else:
                    return {"success": False, "error": "RunningHub timeout"}

                # Download outputs
                images = []
                for node_id, items in outputs.items():
                    for item in items:
                        img_url = item.get("url") or item.get("filename", "")
                        if img_url.startswith("http"):
                            r = await client.get(img_url)
                            filename = f"runninghub_{uuid.uuid4().hex[:8]}.png"
                            filepath = os.path.join(DEFAULT_OUTPUT_DIR, filename)
                            os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
                            with open(filepath, "wb") as f:
                                f.write(r.content)
                            images.append({"path": filepath, "filename": filename, "size_bytes": len(r.content), "url": img_url})

                return {"success": len(images) > 0, "images": images, "prompt_id": prompt_id, "seed": resolved_seed}
        except Exception as e:
            return {"success": False, "error": f"RunningHub: {e}"}

    async def health_check(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.base_url}/api/system_stats", headers={"Authorization": f"Bearer {self.api_key}"})
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list:
        return ["sd_xl_base_1.0.safetensors", "sd3.5_large.safetensors", "flux1-dev.safetensors"]


def _size_to_aspect(width: int, height: int) -> str:
    """Convert pixel dimensions to Stability AI aspect ratio string."""
    ratio = width / height
    best = "1:1"
    best_diff = abs(ratio - 1)
    for name, target in [("16:9", 16/9), ("9:16", 9/16), ("4:3", 4/3), ("3:4", 3/4),
                          ("3:2", 3/2), ("2:3", 2/3), ("21:9", 21/9)]:
        diff = abs(ratio - target)
        if diff < best_diff:
            best_diff = diff
            best = name
    return best


# ────────────────────────────────────────────────────────────────────
# Global backend registry
# ────────────────────────────────────────────────────────────────────

_backend: Optional[ImageGenBackend] = None
_backend_lock = asyncio.Lock()


def set_backend(backend: ImageGenBackend):
    """Replace the global image generation backend."""
    global _backend
    _backend = backend
    logger.info("Image generation backend set to %s", type(backend).__name__)


def get_backend() -> ImageGenBackend:
    """Return the current global image generation backend.

    Lazily initializes to ``DummyBackend`` if none has been configured.
    """
    global _backend
    if _backend is None:
        _backend = DummyBackend()
        logger.debug("No image generation backend configured; using DummyBackend")
    return _backend


def _create_backend_from_env() -> ImageGenBackend:
    """Auto-detect backend from environment variables.

    Priority: RUNNINGHUB > STABILITY > REPLICATE > COMFYUI > DUMMY
    """
    # RunningHub (Chinese cloud, best for domestic users)
    if os.getenv("RUNNINGHUB_API_KEY"):
        logger.info("Auto-configuring RunningHubBackend")
        return RunningHubBackend()

    # Stability AI (global cloud)
    if os.getenv("STABILITY_API_KEY"):
        logger.info("Auto-configuring StabilityAIBackend")
        return StabilityAIBackend()

    # Replicate (global cloud, many models)
    if os.getenv("REPLICATE_API_TOKEN"):
        logger.info("Auto-configuring ReplicateBackend")
        return ReplicateBackend()

    # ComfyUI (local GPU)
    comfy_url = os.getenv("COMFYUI_URL", "")
    if comfy_url:
        timeout = int(os.getenv("COMFYUI_TIMEOUT", str(DEFAULT_TIMEOUT)))
        logger.info("Auto-configuring ComfyUIBackend at %s", comfy_url)
        return ComfyUIBackend(base_url=comfy_url, timeout=timeout)

    logger.info("No image gen backend env vars set; using DummyBackend")
    return DummyBackend()


async def _ensure_backend_initialized():
    """Async-safe initializer — ensures a backend is set, auto-detecting from env."""
    global _backend
    if _backend is not None:
        return
    async with _backend_lock:
        if _backend is None:
            _backend = _create_backend_from_env()


# ────────────────────────────────────────────────────────────────────
# Tool: image_models
# ────────────────────────────────────────────────────────────────────


async def tool_image_models() -> str:
    """List available image generation models/checkpoints.

    Returns:
        JSON array of model names, or an error if the backend is not available.
    """
    await _ensure_backend_initialized()
    backend = get_backend()

    try:
        models = await backend.list_models()
    except Exception as e:
        logger.exception("Failed to list image generation models")
        return json.dumps({
            "error": f"Failed to list models: {type(e).__name__}: {str(e)}",
            "models": [],
        })

    # If DummyBackend, provide a helpful hint
    if isinstance(backend, DummyBackend):
        return json.dumps({
            "models": [],
            "hint": (
                "No image generation backend configured. "
                "Set COMFYUI_URL / STABILITY_API_KEY / REPLICATE_API_TOKEN / RUNNINGHUB_API_KEY "
                "to enable image generation."
            ),
        })

    return json.dumps({
        "models": models,
        "total": len(models),
        "backend": type(backend).__name__,
    })


# ────────────────────────────────────────────────────────────────────
# Tool: image_generate
# ────────────────────────────────────────────────────────────────────


async def tool_image_generate(
    prompt: str,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
    seed: int = -1,
    style: str = "",
) -> str:
    """Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate.
        negative_prompt: Things to avoid in the generated image.
        width: Image width in pixels (default: 1024).
        height: Image height in pixels (default: 1024).
        steps: Number of diffusion steps (default: 20, higher = better quality but slower).
        seed: Random seed (-1 for random, specific value for reproducible results).
        style: Style preset to prepend to the prompt.
               Available: "anime", "realistic", "oil painting", "watercolor",
               "pixel art", "cyberpunk"

    Returns:
        JSON string with:
        - success: bool
        - images: [{path, url, filename, size_bytes}, ...]
        - prompt: the full prompt used
        - params: generation parameters
        - error: error message if success is False
    """
    # ── Validate input ──────────────────────────────────────────
    if not prompt or not prompt.strip():
        return json.dumps({"success": False, "error": "Prompt cannot be empty", "images": []})

    prompt = prompt.strip()

    # ── Apply style preset ──────────────────────────────────────
    full_prompt = _apply_style(prompt, style)

    # ── Resolve seed ────────────────────────────────────────────
    resolved_seed = _resolve_seed(seed)

    # ── Clamp parameters ────────────────────────────────────────
    width = max(64, min(width, 2048))
    height = max(64, min(height, 2048))
    steps = max(1, min(steps, 150))

    # ── Ensure backend ──────────────────────────────────────────
    await _ensure_backend_initialized()
    backend = get_backend()

    # ── Generate ────────────────────────────────────────────────
    params = {
        "prompt": full_prompt,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "steps": steps,
        "seed": resolved_seed,
        "style": style or "none",
        "backend": type(backend).__name__,
    }

    logger.info(
        "Image generation requested: prompt='%s...', style=%s, %dx%d, steps=%d, seed=%d",
        full_prompt[:80],
        style or "none",
        width,
        height,
        steps,
        resolved_seed,
    )

    try:
        result = await backend.generate(
            prompt=full_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            seed=resolved_seed,
        )
    except Exception as e:
        logger.exception("Image generation failed with unhandled exception")
        return json.dumps({
            "success": False,
            "error": f"Image generation error: {type(e).__name__}: {str(e)}",
            "images": [],
            "params": params,
        })

    # ── Build response ──────────────────────────────────────────
    response: dict = {
        "success": result.get("success", False),
        "images": result.get("images", []),
        "params": params,
    }

    if not result.get("success"):
        response["error"] = result.get("error", "Unknown error")
    else:
        response["image_count"] = len(response["images"])

    # Include prompt_id / seed from result if available
    if "prompt_id" in result:
        response["prompt_id"] = result["prompt_id"]
    if "seed" in result:
        params["seed"] = result["seed"]

    return json.dumps(response)
