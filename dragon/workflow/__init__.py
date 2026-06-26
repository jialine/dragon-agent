"""
Dragon Agent Workflow Engine

一等模块 — 与 router / skill / dispatch 平级。
每个行业工作流：plan（制定方案）→ 按方案组装 tool/skill/llm 执行。

参考 MoneyPrinterTurbo 架构：pipeline + checkpoint + state machine。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger("dragon.workflow")


# ════════════════════════════════════════════════════════════════════
# Enums
# ════════════════════════════════════════════════════════════════════

class WorkflowState(str, Enum):
    """工作流执行状态"""
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepType(str, Enum):
    """步骤类型"""
    PLAN = "plan"         # 方案制定（每个工作流第一步）
    TOOL = "tool"         # 调用内置工具
    SKILL = "skill"       # 调用已有技能
    LLM = "llm"           # 原始 LLM 推理
    TRANSFORM = "transform"  # 纯数据变换


class FailurePolicy(str, Enum):
    """步骤失败策略"""
    SKIP = "skip"     # 跳过继续
    ABORT = "abort"   # 终止工作流
    RETRY = "retry"   # 重试一次


# ════════════════════════════════════════════════════════════════════
# Data Classes
# ════════════════════════════════════════════════════════════════════

@dataclass
class StepDefinition:
    """工作流中的一个步骤定义（从 YAML 解析）"""
    id: str
    name: str
    type: StepType
    condition: str = ""              # Jinja2 表达式，如 "plan.need_data == true"
    on_failure: FailurePolicy = FailurePolicy.SKIP

    # tool 类型
    tool: str = ""                   # 单个工具名
    tools_from: str = ""             # 从 plan 中动态读取工具列表

    # skill 类型
    skill: str = ""                  # 单个技能名
    skills_from: str = ""            # 从 plan 中动态读取技能列表

    # llm 类型
    prompt: str = ""                 # LLM prompt 模板
    model: str = ""                  # 指定模型（空=默认）

    # transform 类型
    template: str = ""

    # 通用
    input: str = ""                  # 输入模板
    input_from: str = ""             # 从前一步的输出读取
    context: Dict[str, str] = field(default_factory=dict)
    output_key: str = ""             # 输出存储的 key（空=用 step.id）


@dataclass
class PlanConfig:
    """Plan 步骤的配置"""
    prompt: str = ""


@dataclass
class Toolbox:
    """行业可用工具池"""
    tools: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)


@dataclass
class WorkflowDefinition:
    """一个完整的工作流定义"""
    name: str
    industry: str
    version: str = "1.0"
    timeout_secs: int = 120

    toolbox: Toolbox = field(default_factory=Toolbox)
    plan: PlanConfig = field(default_factory=PlanConfig)
    steps: List[StepDefinition] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "WorkflowDefinition":
        """从 YAML 文件加载工作流定义"""
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Parse toolbox
        toolbox_raw = raw.get("toolbox", {})
        toolbox = Toolbox(
            tools=toolbox_raw.get("tools", []),
            skills=toolbox_raw.get("skills", []),
        )

        # Parse plan
        plan_raw = raw.get("plan", {})
        plan = PlanConfig(prompt=plan_raw.get("prompt", ""))

        # Parse steps
        steps = []
        for s in raw.get("steps", []):
            steps.append(StepDefinition(
                id=s["id"],
                name=s["name"],
                type=StepType(s.get("type", "llm")),
                condition=s.get("condition", ""),
                on_failure=FailurePolicy(s.get("on_failure", "skip")),
                tool=s.get("tool", ""),
                tools_from=s.get("tools_from", ""),
                skill=s.get("skill", ""),
                skills_from=s.get("skills_from", ""),
                prompt=s.get("prompt", ""),
                model=s.get("model", ""),
                template=s.get("template", ""),
                input=s.get("input", ""),
                input_from=s.get("input_from", ""),
                context=s.get("context", {}),
                output_key=s.get("output_key", ""),
            ))

        return cls(
            name=raw.get("name", path.stem),
            industry=raw.get("industry", "general"),
            version=raw.get("version", "1.0"),
            timeout_secs=raw.get("timeout_secs", 120),
            toolbox=toolbox,
            plan=plan,
            steps=steps,
        )


@dataclass
class StepResult:
    """单个步骤的执行结果"""
    step_id: str
    step_name: str
    output: Any = None
    error: str = ""
    success: bool = True
    skipped: bool = False
    elapsed_ms: float = 0.0


@dataclass
class WorkflowResult:
    """整个工作流的执行结果"""
    industry: str
    status: WorkflowState
    outputs: Dict[str, Any] = field(default_factory=dict)
    plan: Optional[Dict[str, Any]] = None
    final_response: str = ""
    error: str = ""
    progress: float = 0.0
    steps: List[StepResult] = field(default_factory=list)
    total_elapsed_ms: float = 0.0


# ════════════════════════════════════════════════════════════════════
# Callbacks
# ════════════════════════════════════════════════════════════════════

class WorkflowCallbacks:
    """工作流执行回调接口 — 用于进度上报"""

    async def on_plan_start(self) -> None:
        pass

    async def on_plan_complete(self, plan: Dict[str, Any]) -> None:
        pass

    async def on_step_start(self, step_id: str, step_name: str, progress: float) -> None:
        pass

    async def on_step_complete(self, step_id: str, output: Any, progress: float) -> None:
        pass

    async def on_step_skipped(self, step_id: str, reason: str, progress: float) -> None:
        pass

    async def on_step_failed(self, step_id: str, error: str, progress: float) -> None:
        pass

    async def on_workflow_complete(self, result: WorkflowResult) -> None:
        pass


# ════════════════════════════════════════════════════════════════════
# Top-level Engine (facade)
# ════════════════════════════════════════════════════════════════════

class WorkflowEngine:
    """工作流执行引擎 — 顶层门面"""

    def __init__(self, workflows_dir: str = "workflows"):
        self._workflows_dir = Path(workflows_dir)
        self._cache: Dict[str, WorkflowDefinition] = {}

    def load(self, industry: str) -> WorkflowDefinition:
        """加载行业工作流定义（带缓存）"""
        if industry in self._cache:
            return self._cache[industry]

        path = self._workflows_dir / f"{industry}.yaml"
        if not path.exists():
            logger.warning("Workflow not found for %s, falling back to general", industry)
            path = self._workflows_dir / "general.yaml"

        wf = WorkflowDefinition.from_yaml(path)
        self._cache[industry] = wf
        return wf

    async def run(
        self,
        industry: str,
        query: str,
        route_result: Any,  # RouteResult from dragon.router
        dispatcher: Any = None,   # DragonDispatcher for LLM calls
        callbacks: WorkflowCallbacks = None,
    ) -> WorkflowResult:
        """
        执行完整工作流。

        Args:
            industry:     Router 分类结果中的行业
            query:        用户原始查询
            route_result: Router 返回的 RouteResult
            dispatcher:   调度器，用于 LLM 步骤和 plan 步骤
            callbacks:    进度回调
        """
        from .runner import run_workflow
        from .planner import execute_plan
        from .steps import StepExecutor

        wf = self.load(industry)
        step_executor = StepExecutor()

        return await run_workflow(
            workflow=wf,
            query=query,
            route_result=route_result,
            dispatcher=dispatcher,
            plan_executor=execute_plan,
            step_executor=step_executor,
            callbacks=callbacks or WorkflowCallbacks(),
        )
