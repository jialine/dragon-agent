"""
Dragon Agent — Text-to-Speech (TTS) Tools
==========================================

Uses Microsoft Edge TTS (edge-tts) for free, high-quality neural text-to-speech.
Chinese voices are the default, reflecting the project's primary language.

Tools:
    - tts: Convert text to speech (MP3 output)
    - tts_voices: List available TTS voices
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import List

logger = logging.getLogger("dragon.tool.builtins.tts")

# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────

MAX_TEXT_LENGTH = 5000  # edge-tts practical character limit

# Available Chinese neural voices via Microsoft Edge TTS
VOICES: List[str] = [
    "zh-CN-XiaoxiaoNeural",   # 女声-活泼 (Female, lively)
    "zh-CN-YunxiNeural",      # 男声-新闻 (Male, news-style)
    "zh-CN-XiaoyiNeural",     # 女声-温柔 (Female, gentle)
    "zh-CN-YunjianNeural",    # 男声-成熟 (Male, mature)
    "zh-CN-YunxiaNeural",     # 男声-可爱 (Male, cute)
    "zh-CN-YunyangNeural",    # 男声-新闻 (Male, news-style)
]

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_OUTPUT_DIR = Path.home() / ".dragon" / "tts"


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


def _check_edge_tts() -> bool:
    """Check if edge-tts is available.

    Tries the CLI first (fastest), then the Python module as fallback.
    """
    # Check CLI
    try:
        result = subprocess.run(
            ["edge-tts", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check Python module
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        pass

    return False


def _check_edge_tts_module() -> bool:
    """Check if edge-tts Python module is importable."""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


def _truncate_text(text: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Truncate text to max_length characters, preserving whole sentences where possible."""
    if len(text) <= max_length:
        return text

    # Try to truncate at the last sentence boundary
    truncated = text[:max_length]
    # Look for sentence-ending punctuation: 。!?！？.\n
    for sep in ("。", "！", "？", ". ", "! ", "? ", "\n"):
        last_idx = truncated.rfind(sep)
        if last_idx > max_length * 0.5:  # Only truncate at boundary if it's reasonable
            return truncated[:last_idx + len(sep.rstrip())]

    return truncated


def _generate_output_path(custom_path: str | None = None) -> str:
    """Generate an output file path for TTS audio.

    Args:
        custom_path: User-specified path. If None, auto-generate under ~/.dragon/tts/.

    Returns:
        Absolute path to the output MP3 file.
    """
    if custom_path:
        path = Path(custom_path).expanduser().resolve()
    else:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        path = DEFAULT_OUTPUT_DIR / f"tts_{timestamp}.mp3"

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure .mp3 extension
    if path.suffix.lower() not in (".mp3", ".wav", ".ogg", ".flac"):
        path = path.with_suffix(".mp3")

    return str(path)


def _estimate_duration_seconds(audio_path: str | None = None, file_size: int | None = None) -> float:
    """Estimate audio duration in seconds.

    For MP3 at ~128kbps: duration ≈ (bytes * 8) / (128 * 1000)
    Falls back to file stat if audio_path is provided.
    """
    if audio_path:
        try:
            file_size = Path(audio_path).stat().st_size
        except OSError:
            return 0.0

    if file_size and file_size > 0:
        # MP3 ~128 kbps rough estimate
        return round(file_size * 8 / (128 * 1000), 1)

    return 0.0


# ────────────────────────────────────────────────────────────────────
# Tool: tts_voices
# ────────────────────────────────────────────────────────────────────


async def tool_tts_voices() -> str:
    """List available TTS voices.

    Returns a JSON array of available Chinese voice identifiers
    that can be used with the tts tool.
    """
    return json.dumps({
        "default_voice": DEFAULT_VOICE,
        "voices": VOICES,
        "total": len(VOICES),
    })


# ────────────────────────────────────────────────────────────────────
# Tool: tts
# ────────────────────────────────────────────────────────────────────


async def tool_tts(
    text: str,
    voice: str = DEFAULT_VOICE,
    output_path: str | None = None,
) -> str:
    """Convert text to speech using Microsoft Edge TTS.

    Args:
        text: Text to convert to speech (max 5000 characters).
        voice: Voice identifier (default: zh-CN-XiaoxiaoNeural).
               Use tts_voices to see available voices.
        output_path: Output MP3 file path.
                     Defaults to ~/.dragon/tts/<timestamp>.mp3.

    Returns:
        JSON string with:
        - path: Absolute path to the generated audio file
        - duration_seconds: Estimated audio duration
        - voice: Voice used
        - text_length: Input text length (after truncation)
        Or an error object if TTS fails.
    """
    # Validate input
    if not text or not text.strip():
        return json.dumps({"error": "Text cannot be empty"})

    # Truncate long text
    original_length = len(text)
    truncated = False
    if original_length > MAX_TEXT_LENGTH:
        text = _truncate_text(text, MAX_TEXT_LENGTH)
        truncated = True
        logger.info("TTS text truncated from %d to %d characters", original_length, len(text))

    # Generate output path
    resolved_path = _generate_output_path(output_path)

    # Check edge-tts availability
    if not _check_edge_tts():
        return json.dumps({
            "error": (
                "edge-tts is not installed. "
                "Install it with: pip install edge-tts"
            ),
            "install_command": "pip install edge-tts",
        })

    # Try Python API first (more reliable), fall back to CLI
    if _check_edge_tts_module():
        try:
            import edge_tts

            communicate = edge_tts.Communicate(text=text, voice=voice)
            await communicate.save(resolved_path)

            output_file = Path(resolved_path)
            if not output_file.exists() or output_file.stat().st_size == 0:
                return json.dumps({
                    "error": "TTS completed but output file is empty or missing",
                })

            duration = _estimate_duration_seconds(resolved_path)
            file_size = output_file.stat().st_size

            logger.info("TTS completed (Python API): %s (%.1fs, %d bytes)", resolved_path, duration, file_size)

            result = {
                "path": resolved_path,
                "duration_seconds": duration,
                "file_size_bytes": file_size,
                "voice": voice,
                "text_length": len(text),
            }
            if truncated:
                result["info"] = f"Text truncated from {original_length} to {len(text)} characters"

            return json.dumps(result)

        except Exception as e:
            logger.warning("edge-tts Python API failed: %s, falling back to CLI", e)
            # Fall through to CLI approach

    # Fallback: use edge-tts CLI
    cmd = [
        "edge-tts",
        "--voice", voice,
        "--text", text,
        "--write-media", resolved_path,
    ]

    logger.debug("Running edge-tts CLI: voice=%s, output=%s, text_len=%d", voice, resolved_path, len(text))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2 minutes max for long text
        )

        if proc.returncode != 0:
            error_msg = proc.stderr.strip() or proc.stdout.strip() or "Unknown error"
            logger.error("edge-tts CLI failed (exit=%d): %s", proc.returncode, error_msg[:500])
            return json.dumps({
                "error": f"TTS generation failed: {error_msg[:500]}",
                "exit_code": proc.returncode,
            })

        output_file = Path(resolved_path)
        if not output_file.exists() or output_file.stat().st_size == 0:
            return json.dumps({
                "error": "TTS completed but output file is empty or missing",
            })

        duration = _estimate_duration_seconds(resolved_path)
        file_size = output_file.stat().st_size

        logger.info("TTS completed (CLI): %s (%.1fs, %d bytes)", resolved_path, duration, file_size)

        result = {
            "path": resolved_path,
            "duration_seconds": duration,
            "file_size_bytes": file_size,
            "voice": voice,
            "text_length": len(text),
        }
        if truncated:
            result["info"] = f"Text truncated from {original_length} to {len(text)} characters"

        return json.dumps(result)

    except subprocess.TimeoutExpired:
        logger.error("edge-tts CLI timed out for text length %d", len(text))
        return json.dumps({
            "error": "TTS generation timed out after 120 seconds",
        })

    except FileNotFoundError:
        return json.dumps({
            "error": (
                "edge-tts command not found. "
                "Install it with: pip install edge-tts"
            ),
        })

    except Exception as e:
        logger.exception("Unexpected error in TTS tool")
        return json.dumps({
            "error": f"TTS error: {type(e).__name__}: {str(e)}",
        })
