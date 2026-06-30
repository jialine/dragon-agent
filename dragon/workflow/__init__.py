"""
Dragon Agent Workflow Engine
============================
YAML 驱动的工作流执行引擎。

核心能力：
- 解析 YAML 工作流定义
- 支持 llm_call / tool_call / conditional / loop / sub_workflow 五种步骤类型
- 步骤间数据通过上下文模板 {step_id.field} 传递
- 条件分支、循环迭代、嵌套子工作流

用法::

    from dragon.workflow import WorkflowEngine, WorkflowDefinition

    engine = WorkflowEngine()
    wf = engine.load("workflows/research.yaml")
    result = await engine.run(wf, context={"query": "..."})
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

logger = logging.getLogger("dragon.workflow")


# ════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════

class WorkflowState(str, Enum):
    """工作流整体执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepType(str, Enum):
    """步骤类型"""
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    CONDITIONAL = "conditional"
    LOOP = "loop"
    SUB_WORKFLOW = "sub_workflow"
    # Legacy aliases for backward compatibility
    LLM = "llm"
    TOOL = "tool"
    PLAN = "plan"
    SKILL = "skill"


# Mapping from legacy type strings to canonical StepType
_LEGACY_TYPE_MAP: Dict[str, StepType] = {
    "llm": StepType.LLM_CALL,
    "tool": StepType.TOOL_CALL,
    "plan": StepType.LLM_CALL,
    "skill": StepType.LLM_CALL,
    "tool_call": StepType.TOOL_CALL,
}


def _parse_step_type(raw_type: str) -> StepType:
    """Parse step type string, handling legacy names."""
    if raw_type in _LEGACY_TYPE_MAP:
        return _LEGACY_TYPE_MAP[raw_type]
    return StepType(raw_type)


# ════════════════════════════════════════════════════════════════════
# Shared Data Classes
# ════════════════════════════════════════════════════════════════════

@dataclass
class StepDefinition:
    """单个步骤定义（从 YAML 解析）"""
    id: str
    type: StepType
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowDefinition:
    """完整工作流定义"""
    name: str
    description: str = ""
    steps: List[StepDefinition] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "WorkflowDefinition":
        """从 YAML 文件加载工作流定义"""
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        steps = []
        for s in raw.get("steps", []):
            # Merge all YAML fields into config (id/type are reserved)
            config = dict(s)
            config.pop("id", None)
            config.pop("type", None)
            # Merge explicit config block if present
            explicit_config = s.get("config", {})
            if isinstance(explicit_config, dict):
                config.update(explicit_config)
            config.pop("config", None)
            
            steps.append(StepDefinition(
                id=s["id"],
                type=_parse_step_type(s.get("type", "llm_call")),
                config=config,
            ))

        return cls(
            name=raw.get("name", Path(path).stem),
            description=raw.get("description", ""),
            steps=steps,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowDefinition":
        """从字典构建工作流定义（用于内嵌子工作流）"""
        steps = []
        for s in data.get("steps", []):
            config = dict(s)
            config.pop("id", None)
            config.pop("type", None)
            explicit_config = s.get("config", {})
            if isinstance(explicit_config, dict):
                config.update(explicit_config)
            config.pop("config", None)
            
            steps.append(StepDefinition(
                id=s["id"],
                type=_parse_step_type(s.get("type", "llm_call")),
                config=config,
            ))
        return cls(
            name=data.get("name", "inline"),
            description=data.get("description", ""),
            steps=steps,
        )


@dataclass
class StepResult:
    """单步执行结果"""
    step_id: str
    step_type: StepType
    output: Any = None
    success: bool = True
    skipped: bool = False
    error: str = ""
    elapsed_ms: float = 0.0

    @property
    def result(self) -> Any:
        """兼容 {step_id.result} 模板语法"""
        return self.output


@dataclass
class WorkflowResult:
    """工作流整体执行结果"""
    name: str
    status: WorkflowState = WorkflowState.PENDING
    outputs: Dict[str, Any] = field(default_factory=dict)
    final_output: Any = None
    error: str = ""
    steps: List[StepResult] = field(default_factory=list)
    total_elapsed_ms: float = 0.0

    @property
    def success(self) -> bool:
        return self.status == WorkflowState.COMPLETED


# ════════════════════════════════════════════════════════════════════
# Exports (lazy to avoid circular imports)
# ════════════════════════════════════════════════════════════════════

def __getattr__(name: str):
    """Lazy import to break circular dependency."""
    if name == "WorkflowEngine":
        from dragon.workflow.engine import WorkflowEngine as _cls
        return _cls
    if name in (
        "StepExecutor", "execute_llm_call", "execute_tool_call",
        "execute_conditional", "execute_loop", "execute_sub_workflow",
        "render_template", "resolve_path",
    ):
        from dragon.workflow import steps as _mod
        return getattr(_mod, name)
    raise AttributeError(f"module 'dragon.workflow' has no attribute {name!r}")


__all__ = [
    # Types
    "WorkflowState", "StepType",
    # Data Classes
    "StepDefinition", "WorkflowDefinition", "StepResult", "WorkflowResult",
    # Engine
    "WorkflowEngine",
    # Steps
    "StepExecutor",
    "execute_llm_call", "execute_tool_call", "execute_conditional",
    "execute_loop", "execute_sub_workflow",
    "render_template", "resolve_path",
]
