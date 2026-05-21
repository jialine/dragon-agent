"""
Panda Agent — External Skill Importer
=====================================

Import skills from external agent frameworks (Hermes, OpenClaw, etc.)
and register them in Panda's self-evolving skill system.

Supported sources:
- **Hermes Agent**: ~/.hermes/skills/**/SKILL.md (YAML frontmatter + markdown)
- **OpenClaw**: TBD (plugin architecture ready)

Usage::

    from panda.skill.importer import SkillImporter

    importer = SkillImporter(engine)
    report = importer.import_from_hermes()
    # → {"imported": 23, "skipped": 2, "errors": 0, "details": [...]}

    # Auto-discover available sources
    sources = importer.discover_sources()
    for source in sources:
        report = importer.import_from(source)

CLI integration::

    panda skills import hermes
    panda skills import --source hermes --filter "python,debug"
    panda skills import --source all
    panda skills import --dry-run hermes
"""

from __future__ import annotations

import logging
import os
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from panda.skill.engine import SkillEngine
from panda.skill.skill import SkillMeta

logger = logging.getLogger("panda.skill.importer")

# ────────────────────────────────────────────────────────────────────
# Known skill sources
# ────────────────────────────────────────────────────────────────────

@dataclass
class SkillSource:
    """A discovered external skill source."""
    name: str                    # "hermes", "openclaw"
    path: Path                   # root directory of skills
    format: str                  # "hermes-skill-md", "openclaw-skill-md"
    skill_count: int = 0         # populated after scan
    description: str = ""


KNOWN_SOURCES = {
    "hermes": {
        "paths": [
            Path.home() / ".hermes" / "skills",
            Path.home() / ".hermes" / "hermes-agent" / "skills",
            Path.home() / ".hermes" / "hermes-agent" / "optional-skills",
        ],
        "format": "hermes-skill-md",
        "description": "Hermes Agent — SKILL.md (YAML frontmatter + markdown)",
    },
    "openclaw": {
        "paths": [
            Path.home() / ".openclaw" / "skills",
            Path.home() / ".config" / "openclaw" / "skills",
        ],
        "format": "openclaw-skill-md",
        "description": "OpenClaw — SKILL.md (YAML frontmatter + markdown)",
    },
}


# ────────────────────────────────────────────────────────────────────
# Importer
# ────────────────────────────────────────────────────────────────────

@dataclass
class ImportReport:
    source: str
    imported: int = 0
    skipped: int = 0
    dry_run: int = 0
    errors: int = 0
    details: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.imported + self.skipped + self.dry_run + self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "imported": self.imported,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
            "errors": self.errors,
            "total": self.total,
            "details": self.details,
        }


class SkillImporter:
    """Import skills from external agent frameworks into Panda.

    Parameters
    ----------
    engine : SkillEngine
        Panda's skill engine to register imported skills into.
    """

    def __init__(self, engine: SkillEngine) -> None:
        self.engine = engine

    # ── Source Discovery ─────────────────────────────────────────────

    def discover_sources(self) -> List[SkillSource]:
        """Auto-discover available external skill sources on this machine."""
        sources = []

        for name, config in KNOWN_SOURCES.items():
            for path_str in config["paths"]:
                path = Path(path_str).expanduser()
                if path.is_dir():
                    # Count SKILL.md files
                    skill_files = list(path.rglob("SKILL.md"))
                    if skill_files:
                        sources.append(SkillSource(
                            name=name,
                            path=path,
                            format=config["format"],
                            skill_count=len(skill_files),
                            description=config["description"],
                        ))
                    break  # found it, don't check other paths

        return sources

    # ── Main Import Entry ────────────────────────────────────────────

    def import_from(
        self,
        source: str,
        filter_tags: Optional[List[str]] = None,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> ImportReport:
        """Import skills from a named source.

        Parameters
        ----------
        source : str
            "hermes", "openclaw", or "all"
        filter_tags : list or None
            Only import skills matching these tags (case-insensitive).
        dry_run : bool
            If True, scan but don't actually register.
        overwrite : bool
            If True, replace existing skills with the same name.
        """
        if source == "all":
            report = ImportReport(source="all")
            sources = self.discover_sources()
            for s in sources:
                r = self._import_source(s, filter_tags, dry_run, overwrite)
                report.imported += r.imported
                report.skipped += r.skipped
                report.errors += r.errors
                report.details.extend(r.details)
            return report

        # Find the source
        sources = self.discover_sources()
        target = None
        for s in sources:
            if s.name == source:
                target = s
                break

        if target is None:
            # Source not found — report as error
            return ImportReport(
                source=source,
                errors=1,
                details=[{
                    "error": f"Source '{source}' not found. "
                             f"Available: {[s.name for s in sources]}"
                }],
            )

        return self._import_source(target, filter_tags, dry_run, overwrite)

    def _import_source(
        self,
        source: SkillSource,
        filter_tags: Optional[List[str]],
        dry_run: bool,
        overwrite: bool,
    ) -> ImportReport:
        report = ImportReport(source=source.name)

        skill_files = list(source.path.rglob("SKILL.md"))
        logger.info("Scanning %s: %d SKILL.md files found", source.path, len(skill_files))

        for filepath in skill_files:
            try:
                result = self._import_skill_file(
                    filepath, source.format, filter_tags, dry_run, overwrite,
                )
                report.details.append(result)
                if result.get("status") == "imported":
                    report.imported += 1
                elif result.get("status") == "dry_run":
                    report.dry_run += 1
                elif result.get("status") == "skipped":
                    report.skipped += 1
                elif result.get("status") == "error":
                    report.errors += 1
            except Exception as e:
                logger.exception("Failed to import %s", filepath)
                report.errors += 1
                report.details.append({
                    "file": str(filepath),
                    "status": "error",
                    "error": str(e),
                })

        return report

    # ── Single Skill Import ──────────────────────────────────────────

    def _import_skill_file(
        self,
        filepath: Path,
        fmt: str,
        filter_tags: Optional[List[str]],
        dry_run: bool,
        overwrite: bool,
    ) -> Dict[str, Any]:
        """Import a single SKILL.md file."""

        if fmt == "hermes-skill-md":
            return self._import_hermes_skill(filepath, filter_tags, dry_run, overwrite)
        elif fmt == "openclaw-skill-md":
            return self._import_openclaw_skill(filepath, filter_tags, dry_run, overwrite)
        else:
            return {"file": str(filepath), "status": "skipped", "reason": f"Unknown format: {fmt}"}

    # ── Hermes SKILL.md Parser ───────────────────────────────────────

    def _import_hermes_skill(
        self,
        filepath: Path,
        filter_tags: Optional[List[str]],
        dry_run: bool,
        overwrite: bool,
    ) -> Dict[str, Any]:
        """Parse Hermes SKILL.md and convert to PandaSkill."""

        base = {"file": str(filepath)}

        # Read file
        try:
            raw = filepath.read_text(encoding="utf-8")
        except Exception as e:
            return {**base, "status": "error", "error": f"Read error: {e}"}

        # Parse YAML frontmatter
        frontmatter, body = _parse_yaml_frontmatter(raw)
        if frontmatter is None:
            return {**base, "status": "skipped", "reason": "No YAML frontmatter"}

        # Extract fields
        name = frontmatter.get("name", filepath.parent.name)
        description = frontmatter.get("description", "")
        version = str(frontmatter.get("version", "1.0.0"))
        author = frontmatter.get("author", "hermes-agent")

        # Hermes-specific: metadata.hermes.tags
        tags = []
        hermes_meta = frontmatter.get("metadata", {}).get("hermes", {})
        if isinstance(hermes_meta, dict):
            tags = hermes_meta.get("tags", [])
        elif isinstance(frontmatter.get("metadata"), dict):
            tags = frontmatter.get("metadata", {}).get("tags", [])
        if not tags:
            # Fallback: use category directory name as tag
            tags = [frontmatter.get("category", filepath.parent.name)]

        # Hermes-specific: metadata.hermes.related_skills
        related = []
        if isinstance(hermes_meta, dict):
            related = hermes_meta.get("related_skills", [])

        # Validate name
        if not name or len(name) > 64:
            name = filepath.parent.name[:64]

        # Tag filter
        if filter_tags:
            filter_lower = [t.lower() for t in filter_tags]
            tag_lower = [t.lower() for t in tags]
            if not any(ft in tag_lower for ft in filter_lower):
                return {
                    **base,
                    "status": "skipped",
                    "reason": f"Tags {tags} don't match filter {filter_tags}",
                }

        # Check existing
        existing = self.engine.get(name)
        if existing and not overwrite:
            return {
                **base,
                "status": "skipped",
                "reason": f"Skill '{name}' already exists (use --overwrite to replace)",
                "name": name,
            }

        meta = SkillMeta(
            name=name,
            description=description,
            tags=tags,
            version=version,
            author=author,
            related_skills=related,
            execution_mode="sequential",
        )

        if dry_run:
            return {
                **base,
                "status": "dry_run",
                "name": name,
                "description": description,
                "tags": tags,
                "version": version,
                "body_length": len(body),
            }

        # Register
        self.engine.register(
            name=name,
            description=description,
            content=body,
            tags=tags,
            version=version,
            related_skills=related,
        )

        logger.info("Imported skill: %s (v%s, %d tags)", name, version, len(tags))
        return {
            **base,
            "status": "imported",
            "name": name,
            "description": description[:100],
            "tags": tags,
            "version": version,
            "body_length": len(body),
        }

    # ── OpenClaw SKILL.md Parser ────────────────────────────────────

    def _import_openclaw_skill(
        self,
        filepath: Path,
        filter_tags: Optional[List[str]],
        dry_run: bool,
        overwrite: bool,
    ) -> Dict[str, Any]:
        """Parse OpenClaw SKILL.md (same YAML frontmatter format as Hermes)."""

        base = {"file": str(filepath)}

        try:
            raw = filepath.read_text(encoding="utf-8")
        except Exception as e:
            return {**base, "status": "error", "error": f"Read error: {e}"}

        frontmatter, body = _parse_yaml_frontmatter(raw)
        if frontmatter is None:
            return {**base, "status": "skipped", "reason": "No YAML frontmatter"}

        name = frontmatter.get("name", filepath.parent.name)
        description = frontmatter.get("description", "")
        version = str(frontmatter.get("version", "1.0.0"))
        author = frontmatter.get("author", "openclaw")

        # OpenClaw may use different metadata structure — be flexible
        tags = []
        oc_meta = frontmatter.get("metadata", {})
        if isinstance(oc_meta, dict):
            tags = oc_meta.get("tags", oc_meta.get("openclaw", {}).get("tags", []))
        if not tags:
            tags = [frontmatter.get("category", filepath.parent.name)]

        related = frontmatter.get("related_skills", [])
        if isinstance(oc_meta, dict):
            oc_inner = oc_meta.get("openclaw", {})
            if isinstance(oc_inner, dict) and not related:
                related = oc_inner.get("related_skills", [])

        if not name or len(name) > 64:
            name = filepath.parent.name[:64]

        if filter_tags:
            filter_lower = [t.lower() for t in filter_tags]
            tag_lower = [t.lower() for t in tags]
            if not any(ft in tag_lower for ft in filter_lower):
                return {
                    **base,
                    "status": "skipped",
                    "reason": f"Tags don't match filter",
                }

        existing = self.engine.get(name)
        if existing and not overwrite:
            return {
                **base,
                "status": "skipped",
                "reason": f"Skill '{name}' already exists",
                "name": name,
            }

        meta = SkillMeta(
            name=name,
            description=description,
            tags=tags,
            version=version,
            author=author,
            related_skills=related,
        )

        if dry_run:
            return {
                **base,
                "status": "dry_run",
                "name": name,
                "tags": tags,
                "version": version,
            }

        self.engine.register(
            name=name,
            description=description,
            content=body,
            tags=tags,
            version=version,
            related_skills=related,
        )

        return {
            **base,
            "status": "imported",
            "name": name,
            "tags": tags,
            "version": version,
        }


# ────────────────────────────────────────────────────────────────────
# YAML Frontmatter Parser
# ────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_yaml_frontmatter(text: str) -> tuple:
    """Parse YAML frontmatter from a markdown document.

    Returns (dict, body_text) or (None, original_text) if no frontmatter.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text

    try:
        frontmatter = yaml.safe_load(match.group(1))
        if not isinstance(frontmatter, dict):
            return None, text
        body = text[match.end():]
        return frontmatter, body
    except yaml.YAMLError as e:
        logger.warning("YAML parse error: %s", e)
        return None, text
