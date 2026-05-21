"""
Panda Agent — Self-Evolving Skill System
========================================

Four innovations over Hermes Agent skills:

1. **Semantic Matching** — embedding-based, not keyword triggers
2. **Self-Evolution** — skills auto-improve from execution experience
3. **Versioned A/B Testing** — track performance per version, auto-rollback
4. **Skill Pipelines** — compose skills into workflows with contracts
"""
from panda.skill.skill import (
    PandaSkill, SkillMeta, SkillVersion, SkillMatch,
    SkillExecutionReport, SkillOutcome, SkillStatus,
    EvolutionProposal, ExecutionMode,
)
from panda.skill.engine import (
    SkillEngine, SkillPipeline, PipelineStep, PipelineResult,
)
from panda.skill.importer import (
    SkillImporter, SkillSource, ImportReport, KNOWN_SOURCES,
)

__all__ = [
    "PandaSkill", "SkillMeta", "SkillVersion", "SkillMatch",
    "SkillExecutionReport", "SkillOutcome", "SkillStatus",
    "EvolutionProposal", "ExecutionMode",
    "SkillEngine", "SkillPipeline", "PipelineStep", "PipelineResult",
    "SkillImporter", "SkillSource", "ImportReport", "KNOWN_SOURCES",
]
