"""
Dragon Agent — YouTube Content Tools
=====================================

Transcript retrieval and summarization for YouTube videos using the
youtube-transcript-api library (no API key required).

Tools:
    - youtube_transcript: Get video transcript/subtitles
    - youtube_summarize: Get transcript as plain text summary

Dependencies:
    - youtube-transcript-api (pip install youtube-transcript-api)
    - httpx (already in Dragon Agent deps)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("dragon.tool.builtins.youtube")

# ── Helpers ──────────────────────────────────────────────────────────

_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})"
)


def _extract_video_id(raw: str) -> Optional[str]:
    """Extract an 11-character YouTube video ID from a URL or bare ID.

    Supports:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://youtube.com/embed/VIDEO_ID
        - https://youtube.com/v/VIDEO_ID
        - Bare 11-character IDs
    """
    raw = raw.strip()
    # Try URL patterns first
    m = _VIDEO_ID_RE.search(raw)
    if m:
        return m.group(1)
    # Fall back: if it looks like a bare 11-char ID, accept it
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", raw):
        return raw
    return None


async def _get_video_title(video_id: str) -> str:
    """Try to get the video title via YouTube oEmbed endpoint (no API key needed)."""
    try:
        url = "https://www.youtube.com/oembed"
        params = {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "format": "json",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": "DragonAgent/1.0"},
        ) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("title", "")
    except Exception:
        pass
    return ""


# ── Tool Implementations ─────────────────────────────────────────────


async def tool_youtube_transcript(
    video_id: str,
    language: str = "zh-Hans",
) -> str:
    """Get transcript/subtitles from a YouTube video.

    Uses the youtube-transcript-api library (no API key required).

    Args:
        video_id: YouTube video ID (e.g., "dQw4w9WgXcQ") or full URL
            (e.g., "https://www.youtube.com/watch?v=dQw4w9WgXcQ").
        language: Language code for transcript. Default: "zh-Hans" (Simplified Chinese).
            Use "en" for English, "ja" for Japanese, etc.

    Returns:
        JSON with video_id, title, transcript (list of {text, start, duration}),
        language, and segment_count. Returns error field on failure.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return json.dumps({
            "error": (
                "youtube-transcript-api is not installed. "
                "Install it with: pip install youtube-transcript-api"
            ),
            "video_id": video_id,
        })

    vid = _extract_video_id(video_id)
    if vid is None:
        return json.dumps({
            "error": (
                f"Could not extract a valid YouTube video ID from: {video_id!r}. "
                "Provide a valid 11-character video ID or a YouTube URL "
                "(e.g., https://www.youtube.com/watch?v=dQw4w9WgXcQ)."
            ),
            "video_id": video_id,
        })

    # Fetch transcript — try specified language first, then fall back
    transcript = None
    used_language = language

    try:
        transcript = YouTubeTranscriptApi.get_transcript(vid, languages=[language])
    except Exception as first_error:
        logger.debug(
            "Could not get transcript for video %s in language %s: %s",
            vid, language, first_error,
        )
        # Try to get available transcript languages and fetch the first available
        try:
            available = YouTubeTranscriptApi.list_transcripts(vid)
            for first_transcript in available:
                used_language = first_transcript.language_code
                transcript = first_transcript.fetch()
                break  # Take the first available
        except Exception:
            pass

    if transcript is None:
        return json.dumps({
            "error": (
                f"No transcript found for video {vid}. "
                f"The video may not have subtitles available."
            ),
            "video_id": vid,
        })

    # Try to get title
    title = await _get_video_title(vid)

    segments = [
        {
            "text": seg.get("text", "").strip(),
            "start": round(seg.get("start", 0), 2),
            "duration": round(seg.get("duration", 0), 2),
        }
        for seg in transcript
    ]

    return json.dumps({
        "video_id": vid,
        "title": title,
        "transcript": segments,
        "language": used_language,
        "segment_count": len(segments),
    })


async def tool_youtube_summarize(
    video_id: str,
    language: str = "zh-Hans",
) -> str:
    """Get transcript and return it as a plain-text summary with timestamps.

    Suitable for further processing (AI summarization, translation, etc.).

    Args:
        video_id: YouTube video ID (e.g., "dQw4w9WgXcQ") or full URL.
        language: Language code for transcript. Default: "zh-Hans" (Simplified Chinese).

    Returns:
        JSON with video_id, title, full_text (plain text with timestamps),
        segment_count, and duration_seconds. Returns error field on failure.
    """
    # Reuse the transcript tool to get the raw data
    raw_json = await tool_youtube_transcript(video_id=video_id, language=language)
    data = json.loads(raw_json)

    if "error" in data:
        return raw_json  # Pass through the error

    segments = data.get("transcript", [])
    lines: List[str] = []
    total_duration = 0.0

    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "")
        duration = seg.get("duration", 0)
        total_duration = max(total_duration, start + duration)

        # Format: [MM:SS] text
        minutes = int(start // 60)
        seconds = int(start % 60)
        timestamp = f"[{minutes:02d}:{seconds:02d}]"
        lines.append(f"{timestamp} {text}")

    full_text = "\n".join(lines)

    return json.dumps({
        "video_id": data.get("video_id", video_id),
        "title": data.get("title", ""),
        "full_text": full_text,
        "segment_count": len(segments),
        "duration_seconds": round(total_duration, 1),
    })
