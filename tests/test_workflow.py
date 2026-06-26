"""
Integration test for Workflow Engine.

Tests:
1. WorkflowDefinition.from_yaml()  — YAML 解析
2. Plan execution                   — LLM 输出 JSON 解析
3. Condition evaluation              — plan.need_search == true/false
4. Full workflow run                 — general.yaml 端到端

Run from dragon-agent root:
    python -m pytest tests/test_workflow.py -v
or:
    cd /home/jialine/code/dragon-agent && python tests/test_workflow.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dragon.workflow import (
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowCallbacks,
    WorkflowState,
    StepType,
    Toolbox,
    PlanConfig,
)


# ════════════════════════════════════════════════════════════════════
# Mock callbacks — capture events for assertion
# ════════════════════════════════════════════════════════════════════

class TestCallbacks(WorkflowCallbacks):
    def __init__(self):
        self.events = []

    async def on_plan_start(self):
        self.events.append(("plan_start",))

    async def on_plan_complete(self, plan):
        self.events.append(("plan_complete", plan))

    async def on_step_start(self, step_id, step_name, progress):
        self.events.append(("step_start", step_id, step_name))

    async def on_step_complete(self, step_id, output, progress):
        self.events.append(("step_complete", step_id))

    async def on_step_skipped(self, step_id, reason, progress):
        self.events.append(("step_skipped", step_id, reason))

    async def on_step_failed(self, step_id, error, progress):
        self.events.append(("step_failed", step_id, error))

    async def on_workflow_complete(self, result):
        self.events.append(("workflow_complete", result.status))


# ════════════════════════════════════════════════════════════════════
# Mock plan executor
# ════════════════════════════════════════════════════════════════════

async def mock_plan_executor(plan_config, toolbox, query, route_result, callbacks, dispatcher=None):
    """返回预定义的 plan，不调用真实 LLM"""
    await callbacks.on_plan_start()

    plan = {
        "approach": "直接回答用户问题",
        "need_search": any(kw in query for kw in ["新闻", "最新", "行情", "数据"]),
        "selected_tools": ["web_search"] if any(kw in query for kw in ["新闻", "最新", "行情", "数据"]) else [],
        "selected_skills": [],
        "sub_questions": [],
        "risk_level": "low",
    }

    await callbacks.on_plan_complete(plan)
    return plan, 5.0  # 5ms


# ════════════════════════════════════════════════════════════════════
# Mock step executor
# ════════════════════════════════════════════════════════════════════

class MockStepExecutor:
    async def execute(self, step, context):
        from dragon.workflow import StepResult

        if step.type == StepType.TOOL:
            return StepResult(
                step_id=step.id,
                step_name=step.name,
                success=True,
                output={"web_search": "搜索结果：这是一条模拟的搜索结果。"},
                elapsed_ms=10.0,
            )

        if step.type == StepType.LLM:
            return StepResult(
                step_id=step.id,
                step_name=step.name,
                success=True,
                output=f"[LLM回答] 关于 '{context.get('_query', '?')}' 的回答。",
                elapsed_ms=20.0,
            )

        return StepResult(
            step_id=step.id,
            step_name=step.name,
            success=False,
            error=f"Unknown type: {step.type}",
        )


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════

def test_parse_yaml():
    """Test: YAML 解析 → WorkflowDefinition"""
    wf_dir = Path(__file__).parent.parent / "workflows"
    wf = WorkflowDefinition.from_yaml(wf_dir / "general.yaml")

    assert wf.name == "通用工作流"
    assert wf.industry == "general"
    assert wf.toolbox.tools == ["web_search"]
    assert wf.toolbox.skills == []
    assert len(wf.steps) == 2
    assert wf.steps[0].id == "search"
    assert wf.steps[0].type == StepType.TOOL
    assert wf.steps[0].condition == "plan.need_search == true"
    assert wf.steps[1].id == "respond"
    assert wf.steps[1].type == StepType.LLM
    print("✅ test_parse_yaml PASSED")


def test_condition_evaluation():
    """Test: condition 表达式求值"""
    from dragon.workflow.runner import _evaluate_condition

    context = {
        "plan": {
            "need_search": True,
            "risk_level": "high",
            "selected_tools": ["web_search"],
        }
    }

    assert _evaluate_condition("plan.need_search == true", context) is True
    assert _evaluate_condition("plan.need_search == false", context) is False
    assert _evaluate_condition("plan.risk_level == 'high'", context) is True
    assert _evaluate_condition("plan.risk_level == 'low'", context) is False
    print("✅ test_condition_evaluation PASSED")


def test_workflow_without_search():
    """Test: plan 决定不需要搜索 → search 步骤跳过"""
    result = asyncio.run(_run_test_workflow("Python是什么"))

    assert result.status == WorkflowState.COMPLETED
    # search should be skipped (need_search=false for simple queries)
    search_step = next((s for s in result.steps if s.step_id == "search"), None)
    assert search_step is not None
    assert search_step.skipped is True
    # respond should have run
    respond_step = next((s for s in result.steps if s.step_id == "respond"), None)
    assert respond_step is not None
    assert respond_step.success is True
    print("✅ test_workflow_without_search PASSED")


def test_workflow_with_search():
    """Test: plan 决定需要搜索 → search 执行"""
    result = asyncio.run(_run_test_workflow("今天有什么最新新闻"))

    assert result.status == WorkflowState.COMPLETED
    search_step = next((s for s in result.steps if s.step_id == "search"), None)
    assert search_step is not None
    assert search_step.skipped is False
    assert search_step.success is True
    print("✅ test_workflow_with_search PASSED")


def test_callbacks_received():
    """Test: 回调事件完整触发"""
    cbs = TestCallbacks()
    result = asyncio.run(_run_test_workflow("测试问题", callbacks=cbs))

    event_names = [e[0] for e in cbs.events]
    assert "plan_start" in event_names
    assert "plan_complete" in event_names
    assert "workflow_complete" in event_names
    print(f"✅ test_callbacks_received PASSED — {len(cbs.events)} events")


async def _run_test_workflow(query, callbacks=None):
    """Helper: run general workflow with mocks."""
    from dragon.workflow.runner import run_workflow

    wf_dir = Path(__file__).parent.parent / "workflows"
    workflow = WorkflowDefinition.from_yaml(wf_dir / "general.yaml")

    # Mock route result
    class MockRoute:
        industry = "general"
        difficulty = "simple"
        difficulty_score = 2.0

    return await run_workflow(
        workflow=workflow,
        query=query,
        route_result=MockRoute(),
        plan_executor=mock_plan_executor,
        step_executor=MockStepExecutor(),
        callbacks=callbacks or TestCallbacks(),
    )


# ════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 50)
    print("Workflow Engine — Integration Tests")
    print("=" * 50)
    print()

    test_parse_yaml()
    test_condition_evaluation()
    test_workflow_without_search()
    test_workflow_with_search()
    test_callbacks_received()

    print()
    print("=" * 50)
    print("ALL TESTS PASSED ✅")
    print("=" * 50)
