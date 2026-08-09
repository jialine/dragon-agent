#!/usr/bin/env python3
"""
Dragon Agent — Toolsets Module (Hermes-aligned)
================================================

Group tools into named sets for targeted dispatch.
Mirrors Hermes's toolsets.py: each platform or mode picks a toolset,
and only those tools are sent to the LLM. No hard truncation.

Usage:
    from dragon.toolsets import resolve_toolset, DRAGON_DEFAULT_TOOLSET
    
    tool_names = resolve_toolset(DRAGON_DEFAULT_TOOLSET)
"""

from __future__ import annotations

from typing import Dict, List, Set

# ────────────────────────────────────────────────────────────────────
# Core tools — sent in every session (Hermes-aligned)
# ────────────────────────────────────────────────────────────────────

_DRAGON_CORE_TOOLS: List[str] = [
    # Web
    "web_search", "web_fetch",
    # Terminal + process management
    "terminal", "process",
    # File manipulation
    "read_file", "write_file", "patch", "search_files",
    # Vision
    "vision_analyze",
    # Skills (Dragon uses skill_view, skill_manage, search_skills)
    "search_skills", "skill_view", "skill_manage",
    # Text-to-speech
    "text_to_speech",
    # Planning & memory
    "todo", "memory",
    # Session history search
    "session_search",
    # Clarifying questions
    "clarify",
    # Code execution + delegation
    "execute_code", "delegate_task",
    # Cronjob management
    "cronjob",
    # Cross-platform messaging
    "send_message",
    # Document reading
    "pdf_read", "docx_read",
    # Browser automation
    "browser_open", "browser_screenshot", "browser_get_text",
    "browser_click", "browser_type", "browser_close",
    # Feishu document integration
    "feishu_doc_read", "feishu_list_docs",
    "feishu_drive_add_comment", "feishu_drive_list_comments",
    "feishu_drive_reply_comment", "feishu_drive_list_comment_replies",
]


# ────────────────────────────────────────────────────────────────────
# Toolset definitions
# ────────────────────────────────────────────────────────────────────

TOOLSETS: Dict[str, Dict] = {
    "web": {
        "description": "Web research and content extraction tools",
        "tools": ["web_search", "web_fetch"],
        "includes": [],
    },
    "search": {
        "description": "Web search only (no content extraction/scraping)",
        "tools": ["web_search"],
        "includes": [],
    },
    "vision": {
        "description": "Image analysis and vision tools",
        "tools": ["vision_analyze"],
        "includes": [],
    },
    "image_gen": {
        "description": "Creative generation tools (images)",
        "tools": ["image_generate"],
        "includes": [],
    },
    "terminal": {
        "description": "Terminal/command execution and process management tools",
        "tools": ["terminal", "process"],
        "includes": [],
    },
    "skills": {
        "description": "Access, create, edit, and manage skill documents",
        "tools": ["search_skills", "skill_view", "skill_manage", "load_skill", "install_skill"],
        "includes": [],
    },
    "browser": {
        "description": "Browser automation for web interaction",
        "tools": [
            "browser_open", "browser_screenshot", "browser_get_text",
            "browser_click", "browser_type", "browser_close",
        ],
        "includes": [],
    },
    "file": {
        "description": "File system operations (read, write, search, patch)",
        "tools": ["read_file", "write_file", "patch", "search_files"],
        "includes": [],
    },
    "delegation": {
        "description": "Task delegation and subagent spawning",
        "tools": ["delegate_task", "delegate_many"],
        "includes": [],
    },
    "interaction": {
        "description": "User interaction: clarify questions, TODO tracking",
        "tools": ["clarify", "todo"],
        "includes": [],
    },
    "memory": {
        "description": "Persistent memory and session search",
        "tools": ["memory", "session_search"],
        "includes": [],
    },
    "automation": {
        "description": "Scheduled tasks and cronjobs",
        "tools": ["cronjob"],
        "includes": [],
    },
    "document": {
        "description": "Document creation and reading (PDF, DOCX, PPTX)",
        "tools": [
            "pdf_read", "pdf_extract",
            "docx_read", "pptx_read", "pptx_create",
            "ocr",
        ],
        "includes": [],
    },
    "productivity": {
        "description": "Productivity tools (feishu, obsidian, notion, linear, airtable, google, kanban)",
        "tools": [
            "feishu_doc_read", "feishu_list_docs", "feishu_create_doc",
            "feishu_drive_add_comment", "feishu_drive_list_comments",
            "feishu_drive_reply_comment", "feishu_drive_list_comment_replies",
            "obsidian_read", "obsidian_search", "obsidian_create",
            "notion_search", "notion_read_page", "notion_create_page",
            "linear_list_issues", "linear_create_issue",
            "airtable_list_records", "airtable_create_record",
            "google_drive_search", "google_calendar_list",
            "kanban_create_board", "kanban_add_task", "kanban_list",
            "kanban_move", "kanban_delete_task", "kanban_list_boards",
        ],
        "includes": [],
    },
    "media": {
        "description": "Media tools (TTS, images, OCR, YouTube, GIF, Spotify)",
        "tools": [
            "text_to_speech", "tts", "tts_voices",
            "image_generate", "image_models",
            "youtube_transcript", "youtube_summarize",
            "gif_search", "gif_trending",
            "spotify_search", "spotify_now_playing", "spotify_play",
            "spotify_pause", "spotify_skip", "spotify_previous",
            "spotify_queue", "spotify_devices", "spotify_volume",
            "spotify_playlists",
        ],
        "includes": [],
    },
    "email": {
        "description": "Email operations",
        "tools": [
            "email_send", "email_search", "email_read",
            "gmail_send", "gmail_search",
        ],
        "includes": [],
    },
    "maps": {
        "description": "Geocoding, routing, and POI search",
        "tools": ["geocode", "reverse_geocode", "get_route", "search_poi"],
        "includes": [],
    },
    "analysis": {
        "description": "Data analysis and code execution",
        "tools": ["execute_code", "code_exec", "data_explore", "data_plot"],
        "includes": [],
    },
    "workflows": {
        "description": "Workflow management",
        "tools": ["create_workflow", "list_workflows", "update_workflow"],
        "includes": [],
    },
    "video": {
        "description": "Video generation tools",
        "tools": ["wan_video"],
        "includes": [],
    },
    "feishu": {
        "description": "Feishu/Lark document and drive integration",
        "tools": [
            "feishu_doc_read", "feishu_list_docs", "feishu_create_doc",
            "feishu_drive_add_comment", "feishu_drive_list_comments",
            "feishu_drive_reply_comment", "feishu_drive_list_comment_replies",
        ],
        "includes": [],
    },
}

# ────────────────────────────────────────────────────────────────────
# Composed toolsets (include other toolsets + own tools)
# ────────────────────────────────────────────────────────────────────

COMPOSED_TOOLSETS: Dict[str, Dict] = {
    "feishu_platform": {
        "description": "Default toolset for Feishu-based Dragon Agent",
        "tools": [
            # Additional feishu-specific tools beyond what's in core
            "feishu_create_doc",
        ],
        "includes": [
            # Start with core tools
            "__CORE__",
            # Add platform-relevant toolsets
            "document",
            "feishu",
            "vision",
            "media",
            "interaction",
            "memory",
            "skills",
            "file",
            "terminal",
            "web",
            "browser",
            "delegation",
            "automation",
            "analysis",
            "email",
            "maps",
            "productivity",
            "workflows",
            "video",
        ],
    },
}


# ────────────────────────────────────────────────────────────────────
# Resolver
# ────────────────────────────────────────────────────────────────────

def resolve_toolset(name: str, _seen: Set[str] | None = None) -> Set[str]:
    """Resolve a toolset name to a flat set of tool names.

    Handles:
    - Built-in toolsets from TOOLSETS
    - Composed toolsets from COMPOSED_TOOLSETS
    - ``"__CORE__"`` → _DRAGON_CORE_TOOLS
    - Arbitrary tool names passed directly (for toolsets that are just tool lists)

    Cycle-safe: raises RecursionError on circular includes.
    """
    if _seen is None:
        _seen = set()

    if name == "__CORE__":
        return set(_DRAGON_CORE_TOOLS)

    # Check composed toolsets first
    if name in COMPOSED_TOOLSETS:
        if name in _seen:
            raise RecursionError(f"Circular toolset include: {name}")
        _seen.add(name)

        ts = COMPOSED_TOOLSETS[name]
        result: Set[str] = set(ts.get("tools", []))
        for included in ts.get("includes", []):
            result |= resolve_toolset(included, _seen)
        return result

    # Check regular toolsets
    if name in TOOLSETS:
        if name in _seen:
            raise RecursionError(f"Circular toolset include: {name}")
        _seen.add(name)

        ts = TOOLSETS[name]
        result = set(ts.get("tools", []))
        for included in ts.get("includes", []):
            result |= resolve_toolset(included, _seen)
        return result

    # Not a named toolset — return as-is (allow raw tool name)
    return {name}


def get_all_toolsets() -> Dict[str, Dict]:
    """Return all defined toolsets (both base and composed)."""
    return {**TOOLSETS, **COMPOSED_TOOLSETS}


def validate_toolset(name: str) -> bool:
    """Check whether a named toolset exists."""
    return name in TOOLSETS or name in COMPOSED_TOOLSETS


# ────────────────────────────────────────────────────────────────────
# Default toolset (used when no specific platform is configured)
# ────────────────────────────────────────────────────────────────────

DRAGON_DEFAULT_TOOLSET = "feishu_platform"
