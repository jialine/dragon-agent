"""
Dragon Agent — Patch Tool (Hermes-aligned)
===========================================

Targeted find-and-replace file editing with unified diff output.
Hermes alignment: patch(path, old_string, new_string, replace_all=False).

Returns unified diff format showing what changed.
"""

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dragon.tool.builtins.patch")


async def tool_patch(
    mode: str = "replace",
    path: str = None,
    old_string: str = None,
    new_string: str = None,
    replace_all: bool = False,
    patch: str = None,
) -> str:
    """Targeted find-and-replace edits in files. Hermes-aligned.

    REPLACE MODE (mode='replace'): find a unique string and replace it.
    PATCH MODE (mode='patch'): apply V4A multi-file patches.

    Args:
        mode: 'replace' or 'patch'.
        path: File path (required for replace mode).
        old_string: Text to find (required for replace mode).
        new_string: Replacement text (required for replace mode).
        replace_all: Replace all occurrences.
        patch: V4A patch content (required for patch mode).
    """
    """Targeted find-and-replace edits in files. Returns unified diff format.

    Reads the file, replaces old_string with new_string, and writes the result.
    Returns a unified diff showing the change.

    Hermes-aligned signature: patch(path, old_string, new_string, replace_all=False)

    Args:
        path: File path to edit (absolute or relative).
        old_string: Exact text to find and replace.
        new_string: Replacement text. Pass empty string to delete.
        replace_all: If True, replace ALL occurrences. If False (default),
            old_string must be unique in the file.

    Returns:
        JSON with success status and unified diff of the change.
    """
    p = Path(path).expanduser().resolve()

    # Validation
    if not p.exists():
        return json.dumps({"error": f"File not found: {path}"})
    if p.is_dir():
        return json.dumps({"error": f"Path is a directory, not a file: {path}"})
    if not old_string:
        return json.dumps({"error": "old_string cannot be empty"})

    try:
        original_text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return json.dumps({"error": "File is not UTF-8 encoded; cannot patch binary files"})
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {e}"})

    # Find occurrences
    count = original_text.count(old_string)
    if count == 0:
        return json.dumps({"error": "old_string not found in file"})

    if not replace_all and count > 1:
        msg = (
            f"old_string is not unique — found {count} occurrences. " f"Use replace_all=True to replace all occurrences, or provide " f"more context in old_string to make it unique.")
        return json.dumps({"error": msg, "occurrences": count})

    # Perform replacement
    if replace_all:
        new_text = original_text.replace(old_string, new_string)
    else:
        new_text = original_text.replace(old_string, new_string, 1)

    # Generate unified diff
    original_lines = original_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        new_lines,
        fromfile=str(p),
        tofile=str(p),
        lineterm="",
    )
    diff_text = "\n".join(diff)

    # Write the modified file
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as e:
        return json.dumps({"error": f"Failed to write file: {e}"})

    logger.info(
        "Patched %s: %d replacement(s), %d bytes",
        p.name,
        count if replace_all else 1,
        len(diff_text),
    )

    return json.dumps({
        "file": str(p),
        "replacements": count if replace_all else 1,
        "diff": diff_text[:5000],
        "diff_truncated": len(diff_text) > 5000,
    })
