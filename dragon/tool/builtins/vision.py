"""
Dragon Agent — Vision / Image Recognition Tools
===============================================

Tools for image analysis, metadata extraction, and OCR.

Tools:
    - vision_analyze: AI-powered image description (with fallback to basic info)
    - vision_info: Image metadata (format, size, dimensions, EXIF)
    - ocr: Optical Character Recognition (requires pytesseract)

Dependencies:
    - Pillow (soft): For image metadata and analysis fallback.
    - pytesseract (soft): For OCR capability.
    - httpx (hard): For URL downloads.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("dragon.tool.builtins.vision")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

SUPPORTED_FORMATS = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]

_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB max download size
_DOWNLOAD_TIMEOUT = 30.0  # seconds


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _is_url(path: str) -> bool:
    """Check if a path string is an HTTP(S) URL."""
    return path.startswith(("http://", "https://"))


def _is_image_extension(path: str) -> bool:
    """Check if the file has a supported image extension."""
    suffix = Path(path).suffix.lower()
    return suffix in SUPPORTED_FORMATS


def _format_from_suffix(path: str) -> str:
    """Get a human-readable format name from file extension."""
    suffix = Path(path).suffix.lower()
    mapping = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".gif": "GIF",
        ".webp": "WebP",
        ".bmp": "BMP",
    }
    return mapping.get(suffix, suffix.lstrip(".").upper())


# ────────────────────────────────────────────────────────────────────
# Pillow-dependent helpers (soft dependency)
# ────────────────────────────────────────────────────────────────────


def _get_pil_image_info(filepath: str) -> dict:
    """Extract image metadata using Pillow.

    Returns a dict with format, width, height, mode, and exif info.
    Returns minimal info if Pillow is not installed.
    """
    file_size = os.path.getsize(filepath) if os.path.isfile(filepath) else 0
    result = {
        "format": _format_from_suffix(filepath),
        "width": 0,
        "height": 0,
        "size_bytes": file_size,
        "mode": "",
        "exif": {},
    }

    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow not installed; returning basic file info only")
        result["_pillow_missing"] = True
        return result

    try:
        with Image.open(filepath) as img:
            result["format"] = img.format or result["format"]
            result["width"] = img.width
            result["height"] = img.height
            result["mode"] = img.mode

            # Extract EXIF data if available
            exif_data = img.getexif()
            if exif_data:
                from PIL.ExifTags import TAGS
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, str(tag_id))
                    # Only include serializable values
                    try:
                        json.dumps({tag_name: value})
                        result["exif"][tag_name] = (
                            value.decode("utf-8", errors="replace")
                            if isinstance(value, bytes)
                            else value
                        )
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        logger.warning("Failed to open image with Pillow: %s", e)

    return result


def _has_pillow() -> bool:
    """Check if Pillow is available."""
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


# ────────────────────────────────────────────────────────────────────
# URL download helper
# ────────────────────────────────────────────────────────────────────


async def _download_image(url: str, destination: Path) -> Path:
    """Download an image from a URL to a local file.

    Args:
        url: The image URL.
        destination: Local path to save the downloaded image.

    Returns:
        The destination path.

    Raises:
        ValueError: If download fails or content is too large.
    """
    import httpx

    destination.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "image/*,*/*;q=0.8",
            },
        )
        response.raise_for_status()

        # Check Content-Length header
        cl = response.headers.get("content-length")
        if cl and int(cl) > _MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Image too large ({int(cl)} bytes, max {_MAX_DOWNLOAD_BYTES})")

        content = response.content
        if len(content) > _MAX_DOWNLOAD_BYTES:
            raise ValueError(f"Image too large ({len(content)} bytes, max {_MAX_DOWNLOAD_BYTES})")

        destination.write_bytes(content)
        return destination


# ────────────────────────────────────────────────────────────────────
# Vision API call (optional — tries to use available provider)
# ────────────────────────────────────────────────────────────────────


async def _call_vision_api(
    image_path: str,
    question: str,
    provider_name: str = "",
) -> Optional[str]:
    """Try to call a vision-capable LLM API to analyze the image.

    Uses the Dragon provider system if available. Falls back to direct
    OpenAI-compatible API call if DEEPSEEK_API_KEY or OPENAI_API_KEY is set.

    Returns the model's description or None if no vision API is available.
    """
    import base64

    # Read and encode the image
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
    except Exception:
        return None

    mime_type = "image/jpeg"
    suffix = Path(image_path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    mime_type = mime_map.get(suffix, "image/jpeg")

    base64_image = base64.b64encode(image_data).decode("ascii")
    data_url = f"data:{mime_type};base64,{base64_image}"

    # Build messages for vision API
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    # Try to use Dragon provider system first
    try:
        from dragon.provider import ProviderRegistry, auto_setup_providers

        providers = auto_setup_providers()
        available = providers.available_providers()

        if available:
            # Pick the first available provider
            pname = available[0]
            result = await providers.call(
                pname,
                messages=messages,
                max_tokens=1024,
                temperature=0.3,
            )
            return result.content
    except Exception as e:
        logger.debug("Dragon provider vision call failed: %s", e)

    # Fallback: try direct API call with common env vars
    for env_var, base_url, model in [
        ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
        ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o"),
        ("MOONSHOT_API_KEY", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ]:
        api_key = os.getenv(env_var, "")
        if not api_key:
            continue

        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 1024,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.debug("Direct vision API call to %s failed: %s", base_url, e)

    return None


# ────────────────────────────────────────────────────────────────────
# Tool: vision_analyze
# ────────────────────────────────────────────────────────────────────


async def tool_vision_analyze(
    image_path: str,
    question: str = "Describe this image in detail.",
) -> str:
    """Analyze image content using AI vision or basic metadata fallback.

    Supports local file paths and HTTP(S) URLs.

    Args:
        image_path: Path to a local image file or an image URL.
        question: Question to ask about the image (default: detailed description).

    Returns:
        JSON string with description, format, size, and metadata.
    """
    result: dict = {}

    # ── 1. Resolve the image source ────────────────────────────────
    local_path: Optional[str] = None
    temp_file: Optional[str] = None

    if _is_url(image_path):
        # Download URL to a temp file
        import tempfile
        import uuid

        suffix = Path(urlparse(image_path).path).suffix or ".jpg"
        tmp_dir = Path(tempfile.gettempdir()) / "dragon_vision"
        dest = tmp_dir / f"{uuid.uuid4().hex}{suffix}"

        try:
            await _download_image(image_path, dest)
            local_path = str(dest)
            temp_file = local_path
        except Exception as e:
            return json.dumps({"error": f"Failed to download image from URL: {e}"})
    else:
        # Local file
        p = Path(image_path).expanduser().resolve()
        if not p.exists():
            return json.dumps({"error": f"File not found: {image_path}"})
        if not p.is_file():
            return json.dumps({"error": f"Path is not a file: {image_path}"})
        local_path = str(p)

    # ── 2. Validate it's a supported image ─────────────────────────
    if not _is_image_extension(local_path):
        # Clean up temp file
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception:
                pass
        return json.dumps({
            "error": f"Unsupported image format. Supported: {', '.join(SUPPORTED_FORMATS)}"
        })

    # ── 3. Get basic image info ────────────────────────────────────
    info = _get_pil_image_info(local_path)
    result.update(info)

    # ── 4. Try AI vision analysis ──────────────────────────────────
    description = await _call_vision_api(local_path, question)
    if description:
        result["description"] = description
        result["ai_analyzed"] = True
    else:
        # Fallback: return basic info with a note
        result["description"] = (
            f"[No vision API available] Image: {info['format']}, "
            f"{info['width']}x{info['height']}, "
            f"{info['size_bytes']} bytes."
        )
        result["fallback"] = True
        result["ai_analyzed"] = False

    # ── 5. Clean up temp file ──────────────────────────────────────
    if temp_file and os.path.exists(temp_file):
        try:
            os.unlink(temp_file)
        except Exception:
            pass

    return json.dumps(result, default=str)


# ────────────────────────────────────────────────────────────────────
# Tool: vision_info
# ────────────────────────────────────────────────────────────────────


async def tool_vision_info(image_path: str) -> str:
    """Get image file metadata without AI analysis.

    Extracts format, dimensions, file size, color mode, and EXIF data
    if available.

    Args:
        image_path: Path to a local image file.

    Returns:
        JSON string with format, width, height, size_bytes, mode, and exif.
    """
    p = Path(image_path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"File not found: {image_path}"})

    if not p.is_file():
        return json.dumps({"error": f"Path is not a file: {image_path}"})

    # Always return basic file info, even for non-images
    info = _get_pil_image_info(str(p))

    # Add file path info
    info["path"] = str(p)
    info["filename"] = p.name

    return json.dumps(info, default=str)


# ────────────────────────────────────────────────────────────────────
# Tool: ocr
# ────────────────────────────────────────────────────────────────────


async def tool_ocr(
    image_path: str,
    lang: str = "chi_sim+eng",
) -> str:
    """Extract text from an image using OCR (Optical Character Recognition).

    Requires pytesseract and Tesseract OCR to be installed.

    Installation:
        - Ubuntu/Debian: sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
        - macOS: brew install tesseract tesseract-lang
        - Python: pip install pytesseract Pillow

    Args:
        image_path: Path to the image file.
        lang: Language codes for OCR (default: "chi_sim+eng" for Chinese + English).

    Returns:
        JSON string with extracted text or error.
    """
    p = Path(image_path).expanduser().resolve()

    if not p.exists():
        return json.dumps({"error": f"File not found: {image_path}"})

    if not p.is_file():
        return json.dumps({"error": f"Path is not a file: {image_path}"})

    # Check for pytesseract
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return json.dumps({
            "error": "pytesseract is not installed",
            "hint": "Install with: pip install pytesseract Pillow",
            "system_hint": (
                "Also install Tesseract OCR engine:\n"
                "  Ubuntu/Debian: sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim\n"
                "  macOS: brew install tesseract tesseract-lang"
            ),
        })

    # Check that Image is available (for pytesseract)
    if not _has_pillow():
        return json.dumps({
            "error": "Pillow is required for OCR",
            "hint": "Install with: pip install Pillow",
        })

    try:
        from PIL import Image
        import pytesseract

        img = Image.open(str(p))
        text = pytesseract.image_to_string(img, lang=lang)

        if not text or not text.strip():
            return json.dumps({
                "text": "",
                "warning": "No text detected in image",
                "lang": lang,
            })

        return json.dumps({
            "text": text.strip(),
            "lang": lang,
            "lines": len(text.strip().split("\n")),
        })
    except Exception as e:
        # Check if it's a Tesseract not found error
        err_str = str(e).lower()
        if "tesseract" in err_str and ("not found" in err_str or "not installed" in err_str):
            return json.dumps({
                "error": "Tesseract OCR engine not found on system",
                "hint": (
                    "Install Tesseract OCR engine:\n"
                    "  Ubuntu/Debian: sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim\n"
                    "  macOS: brew install tesseract tesseract-lang"
                ),
                "detail": str(e),
            })
        return json.dumps({
            "error": f"OCR failed: {e}",
            "detail": str(e),
        })
