"""
Dragon Agent — Skill Management Tools
=====================================

Tools for searching, loading, installing, and creating skills.
These are the conversational interface to Dragon's self-evolving skill system.

Tools:
    - load_skill: Load a skill's full content into conversation context
    - search_skills: Search available skills by name/description/tags
    - install_skill: Install a skill from Hermes (import on demand)
    - create_skill: Create a new skill from conversation experience
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dragon.tool.skills")


# ── Skill engine reference (set by main.py) ──────────────────────────
_skill_engine = None
_skill_importer = None


def set_skill_engine(engine, importer=None) -> None:
    """Set the global skill engine and importer (called from main.py)."""
    global _skill_engine, _skill_importer
    _skill_engine = engine
    _skill_importer = importer
    logger.info("Skill tools: engine wired (%d skills)", len(engine._skills) if engine else 0)


# ── Tool Implementations ─────────────────────────────────────────────


async def tool_search_skills(
    query: str = "",
    top_k: int = 10,
) -> str:
    """Search available skills by name, description, or tags.

    Use this to find relevant skills for a task. Returns skill names,
    descriptions, tags, success rates, and usage counts.

    Args:
        query: Search keywords. Empty returns all skills.
        top_k: Maximum results to return (default 10, max 50).
    """
    if _skill_engine is None:
        return json.dumps({"error": "Skill engine not initialized", "total": 0, "results": []})

    try:
        if query and query.strip():
            # Manual keyword search (fast, always works)
            results = []
            query_lower = query.lower()
            for name, skill in _skill_engine._skills.items():
                score = 0.0
                if query_lower in name.lower():
                    score += 1.0
                if query_lower in skill.meta.description.lower():
                    score += 0.5
                for tag in skill.meta.tags:
                    if query_lower in tag.lower():
                        score += 0.3
                if score > 0:
                    results.append({
                        "name": name,
                        "description": skill.meta.description,
                        "tags": skill.meta.tags[:10],
                        "version": skill.meta.version,
                        "success_rate": round(skill.success_rate, 2),
                        "total_uses": skill.total_uses,
                        "score": round(score, 2),
                    })
            results.sort(key=lambda r: r["score"], reverse=True)
            return json.dumps({
                "query": query,
                "total": len(results),
                "results": results[:top_k],
            })
        else:
            # List all skills
            skills_list = _skill_engine.list_skills()
            return json.dumps({
                "total": len(skills_list),
                "results": skills_list[:top_k],
            })

    except Exception as e:
        logger.exception("search_skills failed")
        return json.dumps({"error": str(e), "total": 0, "results": []})


async def tool_load_skill(
    name: str,
    max_content_length: int = 3000,
) -> str:
    """Load a skill's full content into the conversation.

    Use this when you need detailed instructions for a specific task.
    The skill content provides step-by-step guidance.

    Args:
        name: Exact name of the skill to load.
        max_content_length: Truncate content beyond this length (default 3000, max 10000).
    """
    if _skill_engine is None:
        return json.dumps({"error": "Skill engine not initialized"})

    try:
        skill = _skill_engine.get(name)
        if skill is None:
            # Suggest similar skills
            similar = []
            for sname in _skill_engine._skills:
                if name.lower() in sname.lower() or sname.lower() in name.lower():
                    similar.append(sname)
            return json.dumps({
                "error": f"Skill '{name}' not found",
                "suggestions": similar[:5] if similar else list(_skill_engine._skills.keys())[:5],
            })

        content = skill.content[:min(max_content_length, 10000)]

        return json.dumps({
            "name": skill.name,
            "description": skill.meta.description,
            "version": skill.meta.version,
            "tags": skill.meta.tags,
            "success_rate": round(skill.success_rate, 2),
            "total_uses": skill.total_uses,
            "content_truncated": len(skill.content) > max_content_length,
            "content_length": len(skill.content),
            "content": content,
        })

    except Exception as e:
        logger.exception("load_skill failed")
        return json.dumps({"error": str(e)})


async def tool_install_skill(
    name: str = "",
    source: str = "hermes",
    query: str = "",
) -> str:
    """Install a skill from an external source (Hermes, OpenClaw).

    If name is provided, installs that specific skill.
    If query is provided, searches and installs matching skills.

    Args:
        name: Specific skill name to install from source.
        source: Source to install from ('hermes' or 'openclaw', default 'hermes').
        query: Search query to find and install matching skills.
    """
    if _skill_engine is None:
        return json.dumps({"error": "Skill engine not initialized"})

    if not name and not query:
        return json.dumps({"error": "Provide either 'name' (specific skill) or 'query' (search to install)"})

    try:
        if _skill_importer is None:
            from dragon.skill.importer import SkillImporter
            _imp = SkillImporter(_skill_engine)
        else:
            _imp = _skill_importer

        # Discover available sources
        sources = _imp.discover_sources()
        source_names = [s.name for s in sources]

        if source not in source_names:
            return json.dumps({
                "error": f"Source '{source}' not found",
                "available": source_names,
            })

        if name:
            # Install specific skill by name
            scan_result = _imp.scan(source=source, search=name)
            if scan_result.total == 0:
                return json.dumps({
                    "error": f"Skill '{name}' not found in source '{source}'",
                    "status": "not_found",
                })

            report = _imp.import_from(source=source, overwrite=False)
            imported_names = [d.get("name") for d in report.details if d.get("status") == "imported"]

            return json.dumps({
                "status": "success" if imported_names else "partial",
                "source": source,
                "imported": imported_names,
                "total_imported": report.imported,
                "total_skipped": report.skipped,
                "total_errors": report.errors,
                "message": f"Installed {len(imported_names)} skills from {source}",
            })

        elif query:
            # Search and install matching skills
            scan_result = _imp.scan(source=source, search=query)
            if scan_result.total == 0:
                return json.dumps({
                    "error": f"No skills matching '{query}' found in {source}",
                    "status": "not_found",
                })

            report = _imp.import_from(source=source, overwrite=False)
            imported_names = [d.get("name") for d in report.details if d.get("status") == "imported"]

            return json.dumps({
                "status": "success" if imported_names else "partial",
                "source": source,
                "query": query,
                "found": scan_result.total,
                "imported": imported_names[:20],
                "total_imported": report.imported,
                "total_skipped": report.skipped,
                "message": f"Found {scan_result.total} matching skills, imported {len(imported_names)}",
            })

        return json.dumps({"error": "Provide either 'name' or 'query'"})

    except Exception as e:
        logger.exception("install_skill failed")
        return json.dumps({"error": str(e)})


async def tool_create_skill(
    name: str,
    description: str,
    content: str,
    tags: str = "",
    version: str = "1.0.0",
) -> str:
    """Create a new skill from conversation experience.

    Use this after successfully completing a complex task to save the
    approach as a reusable skill. The skill will be available for future
    sessions and will auto-evolve based on execution outcomes.

    Args:
        name: Unique skill name (lowercase, hyphens, max 64 chars).
        description: One-line description of what the skill does.
        content: Full skill instructions (markdown, step-by-step).
        tags: Comma-separated tags for discovery (e.g., 'python,debug,git').
        version: Semantic version (default '1.0.0').
    """
    if _skill_engine is None:
        return json.dumps({"error": "Skill engine not initialized"})

    # Validate name
    import re
    name = name.strip().lower().replace(" ", "-")[:64]
    if not re.match(r'^[a-z0-9._-]+$', name):
        return json.dumps({"error": f"Invalid skill name '{name}': use lowercase letters, numbers, hyphens, dots, underscores"})

    if not content or len(content) < 50:
        return json.dumps({"error": "Skill content too short (minimum 50 characters)"})

    # Check if already exists
    existing = _skill_engine.get(name)
    if existing:
        return json.dumps({
            "error": f"Skill '{name}' already exists (v{existing.meta.version})",
            "suggestion": "Use a different name or evolve the existing skill",
        })

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    try:
        skill = _skill_engine.register(
            name=name,
            description=description.strip(),
            content=content.strip(),
            tags=tag_list,
            version=version,
            execution_mode="sequential",
        )

        return json.dumps({
            "status": "success",
            "name": name,
            "version": version,
            "description": description,
            "tags": tag_list,
            "content_length": len(content),
            "message": f"Skill '{name}' created successfully! It will be available for future sessions.",
        })

    except Exception as e:
        logger.exception("create_skill failed")
        return json.dumps({"error": str(e)})
