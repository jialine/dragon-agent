"""
Workflow runner — 工作流执行引擎核心。

职责：
1. 执行 plan 步骤（制定方案）
2. 按顺序执行后续步骤
3. 根据 condition 决定是否跳过步骤
4. 处理失败（skip/abort/retry）
5. 上报进度
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from . import (
    FailurePolicy,
    StepDefinition,
    StepResult,
    WorkflowCallbacks,
    WorkflowDefinition,
    WorkflowResult,
    WorkflowState,
)

logger = logging.getLogger("dragon.workflow.runner")


async def run_workflow(
    workflow: WorkflowDefinition,
    query: str,
    route_result: Any,
    plan_executor,
    step_executor,
    callbacks: WorkflowCallbacks,
) -> WorkflowResult:
    """
    执行完整工作流。

    流程：
    1. Plan  →  调用 LLM 制定方案
    2. Steps →  按顺序执行，condition 过滤，失败处理
    3. Done  →  返回 WorkflowResult
    """
    t_start = time.perf_counter()
    result = WorkflowResult(
        industry=workflow.industry,
        status=WorkflowState.PLANNING,
    )
    context: Dict[str, Any] = {
        "_query": query,
        "_industry": workflow.industry,
    }

    total_steps = 1 + len(workflow.steps)  # plan + steps

    # ── Step 0: Plan ──
    logger.info("Workflow [%s]: starting plan phase", workflow.industry)

    try:
        plan, plan_elapsed = await plan_executor(
            plan_config=workflow.plan,
            toolbox=workflow.toolbox,
            query=query,
            route_result=route_result,
            callbacks=callbacks,
        )
    except Exception as exc:
        logger.exception("Plan step failed")
        result.status = WorkflowState.FAILED
        result.error = f"方案制定失败: {exc}"
        result.total_elapsed_ms = (time.perf_counter() - t_start) * 1000
        await callbacks.on_workflow_complete(result)
        return result

    result.plan = plan
    context["plan"] = plan
    result.status = WorkflowState.RUNNING

    # ── Execute steps ──
    logger.info(
        "Workflow [%s]: plan done (approach=%s), executing %d steps",
        workflow.industry,
        plan.get("approach", "?")[:60],
        len(workflow.steps),
    )

    for i, step in enumerate(workflow.steps):
        step_num = i + 1
        progress = (step_num / total_steps) * 100

        # Check condition — skip if not met
        if step.condition:
            should_run = _evaluate_condition(step.condition, context)
            if not should_run:
                logger.info(
                    "Workflow [%s]: skip step '%s' (condition: %s)",
                    workflow.industry, step.id, step.condition,
                )
                await callbacks.on_step_skipped(step.id, step.condition, progress)
                result.steps.append(StepResult(
                    step_id=step.id,
                    step_name=step.name,
                    skipped=True,
                ))
                continue

        # Execute
        await callbacks.on_step_start(step.id, step.name, progress)
        logger.info("Workflow [%s]: running step '%s'", workflow.industry, step.id)

        step_result = await step_executor.execute(step, context)
        result.steps.append(step_result)

        if step_result.success:
            # Store output in context
            output_key = step.output_key or step.id
            context[output_key] = step_result.output
            await callbacks.on_step_complete(step.id, step_result.output, progress)
            logger.info(
                "Workflow [%s]: step '%s' OK (%.0fms)",
                workflow.industry, step.id, step_result.elapsed_ms,
            )
        else:
            # Handle failure
            await callbacks.on_step_failed(step.id, step_result.error, progress)
            logger.warning(
                "Workflow [%s]: step '%s' FAILED: %s",
                workflow.industry, step.id, step_result.error,
            )

            if step.on_failure == FailurePolicy.ABORT:
                result.status = WorkflowState.FAILED
                result.error = f"步骤 '{step.name}' 失败: {step_result.error}"
                result.total_elapsed_ms = (time.perf_counter() - t_start) * 1000
                await callbacks.on_workflow_complete(result)
                return result
            elif step.on_failure == FailurePolicy.RETRY:
                logger.info("Workflow [%s]: retrying step '%s'", workflow.industry, step.id)
                retry_result = await step_executor.execute(step, context)
                result.steps.append(retry_result)
                if retry_result.success:
                    output_key = step.output_key or step.id
                    context[output_key] = retry_result.output
                elif step.on_failure != FailurePolicy.ABORT:
                    # Retry failed, but policy allows continue
                    pass
            # SKIP: continue to next step

    # ── Build final response ──
    result.status = WorkflowState.COMPLETED

    # Extract final response from context
    for candidate_key in ("final_response", "summarize", "respond", "debate_result"):
        val = context.get(candidate_key)
        if val and isinstance(val, str) and len(val) > 10:
            result.final_response = val
            break

    if not result.final_response:
        # Fallback: use last successful step output
        for sr in reversed(result.steps):
            if sr.success and sr.output:
                result.final_response = str(sr.output)
                break

    if not result.final_response:
        result.final_response = f"[{workflow.industry}] 无法生成回答: {result.error}"

    result.outputs = {
        k: v for k, v in context.items()
        if not k.startswith("_") and k != "plan"
    }
    result.total_elapsed_ms = (time.perf_counter() - t_start) * 1000

    logger.info(
        "Workflow [%s]: complete (%.0fms, %d steps, %d succeeded)",
        workflow.industry,
        result.total_elapsed_ms,
        len(result.steps),
        sum(1 for s in result.steps if s.success),
    )

    await callbacks.on_workflow_complete(result)
    return result


def _evaluate_condition(condition: str, context: Dict[str, Any]) -> bool:
    """
    评估步骤 condition 表达式。

    支持的表达式格式：
    - "plan.need_data == true"  →  True/False
    - "plan.is_emergency == true"
    - "plan.risk_level == 'high'"
    """
    if not condition:
        return True

    condition = condition.strip()

    # Parse: "plan.key == value"
    parts = condition.split("==", 1)
    if len(parts) != 2:
        logger.warning("Cannot parse condition: %s", condition)
        return True  # Default to run

    left = parts[0].strip()
    right = parts[1].strip().lower()

    # Get the value from context: "plan.need_data" → context["plan"]["need_data"]
    value = _resolve_path(left, context)

    # Compare
    if right == "true":
        return bool(value)
    elif right == "false":
        return not bool(value)
    else:
        # String comparison
        return str(value).lower() == right.strip("'\"").lower()


def _resolve_path(path: str, context: Dict[str, Any]) -> Any:
    """Resolve 'plan.key.subkey' from context dict."""
    parts = path.split(".")
    current = context
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
