"""
Dragon Agent — Obsidian Notes Tools
====================================

Local filesystem tools for reading, searching, and creating Obsidian notes.
Obsidian vaults are plain directories of Markdown files — no API needed.

Tools:
    - obsidian_read: Read an Obsidian note
    - obsidian_search: Search notes by keyword or regex
    - obsidian_create: Create a new note with YAML frontmatter
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dragon.tool.builtins.obsidian")


# ── Helpers ──────────────────────────────────────────────────────────


def _resolve_vault_path(vault_path: str = "") -> Path:
    """Resolve vault path from argument, OBSIDIAN_VAULT env var, or default ~/Documents/Obsidian.

    Returns the resolved Path, which may not exist.
    """
    if vault_path and vault_path.strip():
        return Path(vault_path.strip()).expanduser().resolve()

    env_vault = os.environ.get("OBSIDIAN_VAULT", "")
    if env_vault:
        return Path(env_vault).expanduser().resolve()

    return Path.home() / "Documents" / "Obsidian"


def _find_note(vault: Path, note_path: str) -> Optional[Path]:
    """Find a .md note file by path (with or without .md extension).

    Tries:
        1. Exact path as given (relative to vault, or absolute)
        2. Path with .md appended
        3. Name-only match in vault root

    Returns the resolved Path or None.
    """
    candidate = Path(note_path)

    # If absolute, use as-is
    if candidate.is_absolute():
        if candidate.exists() and candidate.is_file():
            return candidate
        md_candidate = candidate.with_suffix(".md")
        if md_candidate.exists() and md_candidate.is_file():
            return md_candidate
        return None

    # Relative to vault
    full = vault / candidate
    if full.exists() and full.is_file():
        return full

    md_full = vault / (candidate.name + ".md") if candidate.suffix != ".md" else vault / candidate
    if md_full.exists() and md_full.is_file():
        return md_full

    # Try with .md appended if the relative path didn't work
    if candidate.suffix != ".md":
        full_md = vault / (str(candidate) + ".md")
        if full_md.exists() and full_md.is_file():
            return full_md

    return None


def _extract_title(content: str, filepath: Path) -> str:
    """Extract title from first # heading, or fall back to filename stem."""
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return filepath.stem


def _sanitize_filename(title: str) -> str:
    """Convert a title to a safe filename."""
    # Remove/replace characters that are problematic in filenames
    safe = title.strip()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", safe)
    safe = re.sub(r"\s+", " ", safe)
    safe = safe.strip(" .")
    if not safe:
        safe = "untitled"
    # Truncate to reasonable length
    return safe[:200]


# ── Tool Implementations ─────────────────────────────────────────────


async def tool_obsidian_read(note_path: str, vault_path: str = "") -> str:
    """Read an Obsidian note.

    Args:
        note_path: Path to the note (relative to vault root, or absolute).
            Can include or omit the .md extension.
        vault_path: Path to the Obsidian vault directory.
            Defaults to OBSIDIAN_VAULT env var, then ~/Documents/Obsidian.

    Returns:
        JSON with path, title, content, word_count, modified timestamp,
        size_bytes, and line_count.
    """
    if not note_path or not note_path.strip():
        return json.dumps({"error": "note_path cannot be empty"})

    vault = _resolve_vault_path(vault_path)
    found = _find_note(vault, note_path.strip())

    if found is None:
        return json.dumps({
            "error": f"Note not found: {note_path}",
            "vault": str(vault),
            "searched": [
                str(vault / note_path.strip()),
                str(vault / (note_path.strip() + ".md")),
            ],
        })

    try:
        content = found.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({"error": f"Failed to read note: {type(e).__name__}: {str(e)}"})

    stat = found.stat()
    title = _extract_title(content, found)
    word_count = len(content.split())
    line_count = content.count("\n") + 1
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    return json.dumps({
        "path": str(found),
        "relative_path": str(found.relative_to(vault)) if vault in found.parents else None,
        "title": title,
        "content": content,
        "word_count": word_count,
        "line_count": line_count,
        "size_bytes": stat.st_size,
        "modified": modified,
    })


async def tool_obsidian_search(
    query: str,
    vault_path: str = "",
    max_results: int = 10,
) -> str:
    """Search Obsidian notes by keyword or regex.

    Walks the vault directory for .md files and searches each for the query.
    Results are ranked by match count.

    Args:
        query: Keyword or regex pattern to search for (case-insensitive).
        vault_path: Path to the Obsidian vault directory.
            Defaults to OBSIDIAN_VAULT env var, then ~/Documents/Obsidian.
        max_results: Maximum number of results to return (default: 10).

    Returns:
        JSON with query, results list (path, title, snippet, score, matches),
        total_notes_scanned.
    """
    if not query or not query.strip():
        return json.dumps({"error": "Query cannot be empty"})

    vault = _resolve_vault_path(vault_path)

    if not vault.exists() or not vault.is_dir():
        return json.dumps({
            "error": f"Vault directory not found: {vault}",
            "hint": "Set OBSIDIAN_VAULT env var or pass vault_path argument",
        })

    query = query.strip()

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as e:
        # If not a valid regex, escape and treat as literal keyword
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    scored = []

    for md_file in vault.rglob("*.md"):
        if md_file.is_dir():
            continue

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        matches = list(pattern.finditer(content))
        if not matches:
            continue

        score = len(matches)
        title = _extract_title(content, md_file)

        # Build snippet around the first match
        snippet = ""
        first = matches[0]
        start = max(0, first.start() - 60)
        end = min(len(content), first.end() + 120)
        snippet = content[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(content):
            snippet = snippet + "…"

        try:
            relative_path = str(md_file.relative_to(vault))
        except ValueError:
            relative_path = str(md_file)

        scored.append({
            "path": str(md_file),
            "relative_path": relative_path,
            "title": title,
            "snippet": snippet[:300],
            "score": score,
            "matches": score,
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    return json.dumps({
        "query": query,
        "vault": str(vault),
        "total_notes_scanned": len(list(vault.rglob("*.md"))),
        "total_matches": len(scored),
        "results": scored[:max_results],
    })


async def tool_obsidian_create(
    title: str,
    content: str = "",
    vault_path: str = "",
    folder: str = "",
    tags: str = "",
) -> str:
    """Create a new Obsidian note with YAML frontmatter.

    Creates a Markdown file in the vault with date metadata and optional tags.

    Args:
        title: Title of the note (will be used as the filename).
        content: Markdown content for the note body.
        vault_path: Path to the Obsidian vault directory.
            Defaults to OBSIDIAN_VAULT env var, then ~/Documents/Obsidian.
        folder: Optional subfolder within the vault (created if needed).
        tags: Optional comma-separated tags for YAML frontmatter.

    Returns:
        JSON with path, title, and size_bytes.
    """
    if not title or not title.strip():
        return json.dumps({"error": "Title cannot be empty"})

    title = title.strip()
    vault = _resolve_vault_path(vault_path)

    # Determine target directory
    if folder and folder.strip():
        target_dir = vault / folder.strip()
    else:
        target_dir = vault

    # Create directories
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return json.dumps({
            "error": f"Failed to create directory {target_dir}: {type(e).__name__}: {str(e)}",
        })

    # Build filename
    safe_name = _sanitize_filename(title)
    filepath = target_dir / f"{safe_name}.md"

    # Check for existing file
    counter = 1
    original_filepath = filepath
    while filepath.exists():
        filepath = target_dir / f"{safe_name}_{counter}.md"
        counter += 1

    # Build YAML frontmatter
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    datetime_str = now.isoformat()

    tag_list = ""
    if tags and tags.strip():
        tag_parts = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
        if tag_parts:
            tag_list = "\n  - " + "\n  - ".join(tag_parts)

    frontmatter = f"""---
title: "{title}"
date: {date_str}
created: {datetime_str}{tag_list}
---

"""

    full_content = frontmatter + (content if content else f"# {title}\n")

    try:
        filepath.write_text(full_content, encoding="utf-8")
    except Exception as e:
        return json.dumps({
            "error": f"Failed to write note: {type(e).__name__}: {str(e)}",
        })

    try:
        relative_path = str(filepath.relative_to(vault))
    except ValueError:
        relative_path = str(filepath)

    return json.dumps({
        "path": str(filepath),
        "relative_path": relative_path,
        "title": title,
        "size_bytes": len(full_content.encode("utf-8")),
        "was_renamed": filepath != original_filepath,
    })
