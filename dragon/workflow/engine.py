"""
Workflow Engine — 解析 YAML 并驱动步骤执行。

YAML 格式::

    name: 示例工作流
    description: 展示完整工作流结构
    steps:
      - id: step_1
        type: llm_call
        config:
          prompt: "请分析：{query}"
          model: default

      - id: step_2
        type: tool_call
        config:
          tool: web_search
          input: "{step_1.output}"

      - id: branch
        type: conditional
        config:
          expression: "{{step_2.success}} == True"
          then: next_step
          else: fallback_step

      - id: batch
        type: loop
        config:
          array: "{step_2.results}"
          item_key: item
          sub_steps:
            - id: process_item
              type: llm_call
              config:
                prompt: "处理：{item}"

      - id: nested
        type: sub_workflow
        config:
          workflow: report_generation
          input:
            data: "{batch.output}"
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

from dragon.workflow import (
    StepDefinition,
    StepType,
    StepResult,
    WorkflowDefinition,
    WorkflowResult,
    WorkflowState,
)

# Lazy import to avoid circular dependency at module level
from dragon.workflow.steps import (
    render_template,
    evaluate_expression,
    execute_llm_call,
    execute_tool_call,
    execute_conditional,
    execute_loop,
    execute_sub_workflow,
)

logger = logging.getLogger("dragon.workflow.engine")


# ════════════════════════════════════════════════════════════════════
# Workflow Engine
# ════════════════════════════════════════════════════════════════════

class WorkflowEngine:
    """
    工作流执行引擎。

    - 解析 YAML 工作流定义
    - 按顺序执行步骤，支持条件跳转、循环、子工作流
    - 步骤间通过上下文模板传递数据

    用法::

        engine = WorkflowEngine()
        wf = engine.load("workflows/research.yaml")
        result = await engine.run(wf, context={"query": "AI发展趋势"})
        print(result.final_output)
    """

    def __init__(
        self,
        dispatcher: Any = None,
        tool_registry: Any = None,
        workflows_dir: str = "workflows",
    ):
        """
        Args:
            dispatcher:    LLM 调度器（用于 llm_call 步骤）。需实现 dispatch(messages, ...) 方法。
            tool_registry: 工具注册表（用于 tool_call 步骤）。需实现 call(tool_name, **kwargs) 方法。
            workflows_dir: 工作流 YAML 文件目录。
        """
        self.dispatcher = dispatcher
        self.tool_registry = tool_registry
        self.workflows_dir = Path(workflows_dir)
        self._cache: Dict[str, WorkflowDefinition] = {}

    # ── Load ───────────────────────────────────────────────────────

    def load(self, path: Union[str, Path]) -> WorkflowDefinition:
        """加载 YAML 工作流定义（带缓存）"""
        path = Path(path)
        if not path.is_absolute():
            path = self.workflows_dir / path
            if not path.exists():
                path = path.with_suffix(".yaml")
            if not path.exists():
                raise FileNotFoundError(f"Workflow file not found: {path}")

        cache_key = str(path.resolve())
        if cache_key in self._cache:
            return self._cache[cache_key]

        wf = WorkflowDefinition.from_yaml(path)
        self._cache[cache_key] = wf
        logger.info("Loaded workflow '%s' (%d steps) from %s", wf.name, len(wf.steps), path)
        return wf

    def parse(self, data: Dict[str, Any]) -> WorkflowDefinition:
        """从字典解析工作流定义（用于内嵌定义）"""
        return WorkflowDefinition.from_dict(data)

    # ── Run ────────────────────────────────────────────────────────

    async def run(
        self,
        workflow: WorkflowDefinition,
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResult:
        """
        执行工作流。

        Args:
            workflow: 工作流定义
            context:  初始上下文（如 {"query": "..."}），会传递给每个步骤

        Returns:
            WorkflowResult 包含所有步骤输出和最终结果
        """
        t_start = time.perf_counter()
        context = dict(context or {})
        context.setdefault("_engine", self)
        context.setdefault("_dispatcher", self.dispatcher)
        context.setdefault("_tool_registry", self.tool_registry)

        result = WorkflowResult(
            name=workflow.name,
            status=WorkflowState.RUNNING,
        )

        logger.info("Starting workflow '%s' (%d steps)", workflow.name, len(workflow.steps))

        # Build step lookup for conditional jumps
        step_index = {s.id: i for i, s in enumerate(workflow.steps)}
        executed_ids: set = set()

        i = 0
        while i < len(workflow.steps):
            step = workflow.steps[i]

            # Skip already-executed steps (from conditional jumps)
            if step.id in executed_ids:
                i += 1
                continue

            # Execute step
            step_result = await self._execute_step(step, context)
            result.steps.append(step_result)
            executed_ids.add(step.id)

            if step_result.success and not step_result.skipped:
                # Store output in context under step.id (as StepResult for {step.output} access)
                context[step.id] = step_result
            elif not step_result.success:
                error_msg = step_result.error or "Unknown error"
                logger.error("Step '%s' failed: %s", step.id, error_msg)
                result.status = WorkflowState.FAILED
                result.error = f"Step '{step.id}' failed: {error_msg}"
                result.total_elapsed_ms = (time.perf_counter() - t_start) * 1000
                return result

            # Handle conditional jumps
            if step.type == StepType.CONDITIONAL and step_result.success:
                jump_target = step_result.output
                if jump_target and jump_target in step_index:
                    # Execute the target branch step, then stop
                    target_i = step_index[jump_target]
                    if target_i > i:  # Forward jump only
                        target_step = workflow.steps[target_i]
                        if target_step.id not in executed_ids:
                            target_result = await self._execute_step(target_step, context)
                            result.steps.append(target_result)
                            executed_ids.add(target_step.id)
                            if target_result.success and not target_result.skipped:
                                context[target_step.id] = target_result.output
                    # Stop after branch — don't continue to subsequent steps
                    break

            i += 1

        # ── Build final result ──
        result.status = WorkflowState.COMPLETED

        # Collect public outputs (non-internal keys, unwrap StepResult)
        result.outputs = {}
        for k, v in context.items():
            if k.startswith("_"):
                continue
            if isinstance(v, StepResult):
                result.outputs[k] = v.output
            else:
                result.outputs[k] = v

        # Determine final output: use explicit 'final' key or last step
        result.final_output = context.get("final")
        if result.final_output is None:
            for sr in reversed(result.steps):
                if sr.success and not sr.skipped and sr.output is not None:
                    result.final_output = sr.output
                    break

        result.total_elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "Workflow '%s' completed: %d steps, %.0fms",
            workflow.name, len(result.steps), result.total_elapsed_ms,
        )
        return result

    async def _execute_step(
        self,
        step: StepDefinition,
        context: Dict[str, Any],
    ) -> StepResult:
        """执行单个步骤"""
        t0 = time.perf_counter()

        # Check skip condition (supports both "condition" and "skip_if" keys)
        skip_if = step.config.get("condition", "") or step.config.get("skip_if", "")
        if skip_if:
            cond = render_template(skip_if, context)
            if cond and evaluate_expression(cond, context):
                return StepResult(
                    step_id=step.id,
                    step_type=step.type,
                    skipped=True,
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )

        try:
            if step.type == StepType.LLM_CALL:
                output = await execute_llm_call(step, context)
            elif step.type == StepType.TOOL_CALL:
                output = await execute_tool_call(step, context)
            elif step.type == StepType.CONDITIONAL:
                output = await execute_conditional(step, context)
            elif step.type == StepType.LOOP:
                output = await execute_loop(step, context)
            elif step.type == StepType.SUB_WORKFLOW:
                output = await execute_sub_workflow(step, context)
            else:
                return StepResult(
                    step_id=step.id,
                    step_type=step.type,
                    success=False,
                    error=f"Unknown step type: {step.type}",
                )

        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            on_error = step.config.get("on_error", "fail")
            if on_error == "skip":
                logger.warning("Step '%s' error (skip): %s", step.id, exc)
                return StepResult(
                    step_id=step.id,
                    step_type=step.type,
                    success=True,
                    skipped=True,
                    error=str(exc),
                    elapsed_ms=elapsed,
                )
            logger.exception("Step '%s' failed", step.id)
            return StepResult(
                step_id=step.id,
                step_type=step.type,
                success=False,
                error=str(exc),
                elapsed_ms=elapsed,
            )

        elapsed = (time.perf_counter() - t0) * 1000
        return StepResult(
            step_id=step.id,
            step_type=step.type,
            success=True,
            output=output,
            elapsed_ms=elapsed,
        )

    # ── Convenience ────────────────────────────────────────────────

    async def run_file(
        self,
        path: Union[str, Path],
        context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResult:
        """加载并执行 YAML 工作流文件"""
        wf = self.load(path)
        return await self.run(wf, context)
