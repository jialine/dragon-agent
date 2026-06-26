"""
Workflow Engine — 完整测试套件

测试覆盖：
1. YAML 解析                            — WorkflowDefinition.from_yaml
2. 模板渲染                              — render_template, resolve_path
3. 表达式求值                            — evaluate_expression
4. LLM 调用步骤（mock dispatcher）        — execute_llm_call
5. 工具调用步骤（mock tool_registry）      — execute_tool_call
6. 条件分支步骤                          — execute_conditional
7. 循环步骤                              — execute_loop
8. 子工作流步骤                          — execute_sub_workflow
9. 工作流引擎端到端                       — WorkflowEngine.run
10. 研究工作流                            — research.yaml 完整执行
11. 代码审查工作流                        — code_review.yaml 完整执行
12. 错误处理                              — on_error: skip / fail
13. 步骤跳过                              — skip_if 条件

Run::

    cd /root/dragon-agent
    python -m pytest tests/test_workflow.py -v

Or directly::

    python tests/test_workflow.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dragon.workflow import (
    WorkflowEngine,
    WorkflowDefinition,
    StepDefinition,
    StepResult,
    StepType,
    WorkflowState,
)
from dragon.workflow.steps import (
    render_template,
    resolve_path,
    evaluate_expression,
    execute_llm_call,
    execute_tool_call,
    execute_conditional,
    execute_loop,
    execute_sub_workflow,
    StepExecutor,
)


# ════════════════════════════════════════════════════════════════════
# Helpers — Mock dispatchers and tool registries
# ════════════════════════════════════════════════════════════════════

class MockDispatchResult:
    """Mock dispatch result mimicking DispatchResult"""
    def __init__(self, content: str, model: str = "mock-model"):
        self.content = content
        self.model = model


class MockDispatcher:
    """Mock LLM dispatcher for testing"""
    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self.responses = responses or {}
        self.calls: List[Dict] = []

    async def dispatch(self, **kwargs) -> MockDispatchResult:
        self.calls.append(kwargs)
        messages = kwargs.get("messages", [])
        prompt = messages[-1]["content"] if messages else ""

        # Match response by keyword
        for keyword, response in self.responses.items():
            if keyword in prompt:
                return MockDispatchResult(response)

        # Default: echo the prompt
        return MockDispatchResult(f"[LLM response to: {prompt[:50]}...]")


class MockToolRegistry:
    """Mock tool registry for testing"""
    def __init__(self):
        self.calls: List[Dict] = []

    async def call(self, tool_name: str, **kwargs) -> Any:
        self.calls.append({"tool": tool_name, "kwargs": kwargs})
        return {
            "tool": tool_name,
            "input": kwargs.get("input", ""),
            "results": [f"Result from {tool_name}"],
        }


def make_step(id: str, type: str, config: Dict[str, Any] = None) -> StepDefinition:
    """Helper: create a StepDefinition quickly"""
    return StepDefinition(id=id, type=StepType(type), config=config or {})


def make_context(**kwargs) -> Dict[str, Any]:
    """Helper: create a context dict with required internals"""
    ctx = {
        "_dispatcher": kwargs.pop("_dispatcher", MockDispatcher()),
        "_tool_registry": kwargs.pop("_tool_registry", MockToolRegistry()),
        "_engine": kwargs.pop("_engine", None),
    }
    ctx.update(kwargs)
    return ctx


# ════════════════════════════════════════════════════════════════════
# 1. YAML 解析测试
# ════════════════════════════════════════════════════════════════════

class TestYamlParsing:
    """测试 WorkflowDefinition.from_yaml 和 from_dict"""

    def test_parse_minimal_yaml(self):
        """解析最小 YAML 定义"""
        yaml_content = """
name: 测试工作流
description: 最小测试
steps:
  - id: step1
    type: llm_call
    config:
      prompt: "Hello"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            wf = WorkflowDefinition.from_yaml(tmp_path)
            assert wf.name == "测试工作流"
            assert wf.description == "最小测试"
            assert len(wf.steps) == 1
            assert wf.steps[0].id == "step1"
            assert wf.steps[0].type == StepType.LLM_CALL
            assert wf.steps[0].config["prompt"] == "Hello"
        finally:
            Path(tmp_path).unlink()

    def test_parse_all_step_types(self):
        """解析包含所有步骤类型的 YAML"""
        yaml_content = """
name: 全类型测试
steps:
  - id: s1
    type: llm_call
    config:
      prompt: "test"
  - id: s2
    type: tool_call
    config:
      tool: web_search
  - id: s3
    type: conditional
    config:
      expression: "{{s2.success}} == True"
      then: s4
      else: s5
  - id: s4
    type: loop
    config:
      array: "[1,2,3]"
      item_key: num
      sub_steps:
        - id: inner
          type: llm_call
          config:
            prompt: "process {num}"
  - id: s5
    type: sub_workflow
    config:
      workflow: nested
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            wf = WorkflowDefinition.from_yaml(tmp_path)
            assert len(wf.steps) == 5
            assert wf.steps[0].type == StepType.LLM_CALL
            assert wf.steps[1].type == StepType.TOOL_CALL
            assert wf.steps[2].type == StepType.CONDITIONAL
            assert wf.steps[3].type == StepType.LOOP
            assert wf.steps[4].type == StepType.SUB_WORKFLOW
        finally:
            Path(tmp_path).unlink()

    def test_from_dict(self):
        """测试 from_dict 构建"""
        data = {
            "name": "内联工作流",
            "steps": [
                {"id": "a", "type": "llm_call", "config": {"prompt": "hi"}}
            ],
        }
        wf = WorkflowDefinition.from_dict(data)
        assert wf.name == "内联工作流"
        assert len(wf.steps) == 1

    def test_default_type_is_llm_call(self):
        """未指定 type 时默认为 llm_call"""
        data = {
            "name": "默认类型",
            "steps": [{"id": "x", "config": {"prompt": "hello"}}],
        }
        wf = WorkflowDefinition.from_dict(data)
        assert wf.steps[0].type == StepType.LLM_CALL

    def test_load_workflow_file(self):
        """WorkflowEngine.load 加载文件"""
        yaml_content = """
name: 引擎加载测试
steps:
  - id: test
    type: llm_call
    config:
      prompt: "test"
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.yaml"
            path.write_text(yaml_content, encoding="utf-8")

            engine = WorkflowEngine(workflows_dir=tmpdir)
            wf = engine.load("test.yaml")
            assert wf.name == "引擎加载测试"

            # Test caching
            wf2 = engine.load("test.yaml")
            assert wf is wf2  # Same object from cache

    def test_load_nonexistent_file(self):
        """加载不存在的文件应抛出异常"""
        engine = WorkflowEngine(workflows_dir="/nonexistent")
        with pytest.raises(FileNotFoundError):
            engine.load("does_not_exist.yaml")


# ════════════════════════════════════════════════════════════════════
# 2. 模板渲染测试
# ════════════════════════════════════════════════════════════════════

class TestTemplateRendering:
    """测试 render_template 和 resolve_path"""

    def test_simple_variable(self):
        assert render_template("Hello {name}", {"name": "World"}) == "Hello World"

    def test_nested_path(self):
        ctx = {"plan": {"text": "Hello World"}}
        assert render_template("{plan.text}", ctx) == "Hello World"

    def test_step_result_output(self):
        ctx = {
            "step_1": StepResult(
                step_id="step_1",
                step_type=StepType.LLM_CALL,
                output="analyzed content",
            ),
        }
        assert render_template("{step_1.output}", ctx) == "analyzed content"
        assert render_template("{step_1.result}", ctx) == "analyzed content"

    def test_step_result_success(self):
        ctx = {
            "step_1": StepResult(
                step_id="step_1",
                step_type=StepType.LLM_CALL,
                success=True,
            ),
        }
        assert render_template("{step_1.success}", ctx) == "True"

    def test_unresolved_placeholder(self):
        """未解析的占位符保留原样"""
        result = render_template("Hello {unknown}", {})
        assert "{unknown}" in result

    def test_dict_value(self):
        """字典值序列化为 JSON"""
        ctx = {"data": {"key": "value"}}
        result = render_template("{data}", ctx)
        assert '"key"' in result
        assert '"value"' in result

    def test_list_value(self):
        ctx = {"items": [1, 2, 3]}
        result = render_template("{items}", ctx)
        assert "[1, 2, 3]" in result

    def test_resolve_path_deep_nesting(self):
        ctx = {"a": {"b": {"c": "deep"}}}
        assert resolve_path("a.b.c", ctx) == "deep"

    def test_resolve_path_step_result_properties(self):
        r = StepResult(step_id="x", step_type=StepType.LLM_CALL, success=True, error="", output="out", skipped=False)
        ctx = {"s": r}
        assert resolve_path("s.step_id", ctx) == "x"
        assert resolve_path("s.success", ctx) is True
        assert resolve_path("s.skipped", ctx) is False
        assert resolve_path("s.output", ctx) == "out"

    def test_resolve_path_none(self):
        assert resolve_path("a.b.c", {}) is None
        assert resolve_path("", {}) is None

    def test_render_template_non_string(self):
        assert render_template(None, {}) == ""
        assert render_template(42, {}) == "42"


# ════════════════════════════════════════════════════════════════════
# 3. 表达式求值测试
# ════════════════════════════════════════════════════════════════════

class TestExpressionEvaluation:
    """测试 evaluate_expression"""

    def test_simple_boolean_true(self):
        ctx = make_context(s1=StepResult(step_id="s1", step_type=StepType.LLM_CALL, success=True))
        assert evaluate_expression("{s1.success}", ctx) is True

    def test_simple_boolean_false(self):
        ctx = make_context(s1=StepResult(step_id="s1", step_type=StepType.LLM_CALL, success=False))
        assert evaluate_expression("{s1.success}", ctx) is False

    def test_equality(self):
        ctx = make_context(plan={"level": "high"})
        assert evaluate_expression("{plan.level} == 'high'", ctx) is True
        assert evaluate_expression("{plan.level} == 'low'", ctx) is False

    def test_numeric_comparison(self):
        ctx = make_context(stats={"count": 10})
        assert evaluate_expression("{stats.count} > 5", ctx) is True
        assert evaluate_expression("{stats.count} < 3", ctx) is False
        assert evaluate_expression("{stats.count} == 10", ctx) is True

    def test_len_comparison(self):
        ctx = make_context(items=[1, 2, 3, 4, 5])
        assert evaluate_expression("len({items}) > 3", ctx) is True
        assert evaluate_expression("len({items}) == 0", ctx) is False

    def test_empty_expression(self):
        assert evaluate_expression("", {}) is True

    def test_invalid_expression(self):
        """无效表达式默认返回 True"""
        assert evaluate_expression("garbage syntax !!!", {}) is True


# ════════════════════════════════════════════════════════════════════
# 4. LLM 调用步骤测试
# ════════════════════════════════════════════════════════════════════

class TestLLMCallStep:
    """测试 execute_llm_call"""

    @pytest.mark.asyncio
    async def test_basic_llm_call(self):
        dispatcher = MockDispatcher()
        ctx = make_context(_dispatcher=dispatcher)
        step = make_step("llm1", "llm_call", {"prompt": "What is AI?"})

        result = await execute_llm_call(step, ctx)
        assert result is not None
        assert len(dispatcher.calls) == 1
        assert dispatcher.calls[0]["messages"][-1]["content"] == "What is AI?"

    @pytest.mark.asyncio
    async def test_llm_call_with_template(self):
        dispatcher = MockDispatcher()
        ctx = make_context(_dispatcher=dispatcher, query="什么是机器学习")
        step = make_step("llm2", "llm_call", {"prompt": "请解释：{query}"})

        result = await execute_llm_call(step, ctx)
        call = dispatcher.calls[0]
        assert "什么是机器学习" in call["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_llm_call_with_system_prompt(self):
        dispatcher = MockDispatcher()
        ctx = make_context(_dispatcher=dispatcher)
        step = make_step("llm3", "llm_call", {
            "system": "You are a helpful assistant.",
            "prompt": "Hello",
        })

        result = await execute_llm_call(step, ctx)
        assert len(dispatcher.calls[0]["messages"]) == 2
        assert dispatcher.calls[0]["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_llm_call_missing_prompt(self):
        ctx = make_context()
        step = make_step("llm4", "llm_call", {})

        with pytest.raises(ValueError, match="requires 'prompt'"):
            await execute_llm_call(step, ctx)

    @pytest.mark.asyncio
    async def test_llm_call_missing_dispatcher(self):
        ctx = {"_dispatcher": None}
        step = make_step("llm5", "llm_call", {"prompt": "test"})

        with pytest.raises(RuntimeError, match="requires a dispatcher"):
            await execute_llm_call(step, ctx)

    @pytest.mark.asyncio
    async def test_llm_call_with_model_params(self):
        dispatcher = MockDispatcher()
        ctx = make_context(_dispatcher=dispatcher)
        step = make_step("llm6", "llm_call", {
            "prompt": "test",
            "model": "gpt-4",
            "temperature": 0.5,
            "max_tokens": 500,
        })

        await execute_llm_call(step, ctx)
        call = dispatcher.calls[0]
        assert call.get("model") == "gpt-4"
        assert call.get("temperature") == 0.5
        assert call.get("max_tokens") == 500


# ════════════════════════════════════════════════════════════════════
# 5. 工具调用步骤测试
# ════════════════════════════════════════════════════════════════════

class TestToolCallStep:
    """测试 execute_tool_call"""

    @pytest.mark.asyncio
    async def test_basic_tool_call(self):
        registry = MockToolRegistry()
        ctx = make_context(_tool_registry=registry, query="latest news")
        step = make_step("tool1", "tool_call", {"tool": "web_search", "input": "{query}"})

        result = await execute_tool_call(step, ctx)
        assert result is not None
        assert len(registry.calls) == 1
        assert registry.calls[0]["tool"] == "web_search"

    @pytest.mark.asyncio
    async def test_tool_call_missing_tool_name(self):
        ctx = make_context()
        step = make_step("tool2", "tool_call", {})

        with pytest.raises(ValueError, match="requires 'tool'"):
            await execute_tool_call(step, ctx)

    @pytest.mark.asyncio
    async def test_tool_call_with_params(self):
        registry = MockToolRegistry()
        ctx = make_context(_tool_registry=registry)
        step = make_step("tool3", "tool_call", {
            "tool": "web_search",
            "input": "test query",
            "params": {"num_results": 10},
        })

        await execute_tool_call(step, ctx)
        assert registry.calls[0]["kwargs"].get("num_results") == 10

    @pytest.mark.asyncio
    async def test_tool_call_fallback_no_registry(self):
        """没有 registry 时，回退到内置工具（stub）"""
        ctx = make_context(_tool_registry=None)
        step = make_step("tool4", "tool_call", {"tool": "web_search", "input": "test"})

        result = await execute_tool_call(step, ctx)
        assert result is not None


# ════════════════════════════════════════════════════════════════════
# 6. 条件分支步骤测试
# ════════════════════════════════════════════════════════════════════

class TestConditionalStep:
    """测试 execute_conditional"""

    @pytest.mark.asyncio
    async def test_conditional_then(self):
        ctx = make_context(s1=StepResult(step_id="s1", step_type=StepType.LLM_CALL, success=True))
        step = make_step("cond1", "conditional", {
            "expression": "{s1.success} == True",
            "then": "next_step",
            "else": "fallback",
        })

        result = await execute_conditional(step, ctx)
        assert result == "next_step"

    @pytest.mark.asyncio
    async def test_conditional_else(self):
        ctx = make_context(s1=StepResult(step_id="s1", step_type=StepType.LLM_CALL, success=False))
        step = make_step("cond2", "conditional", {
            "expression": "{s1.success} == True",
            "then": "next_step",
            "else": "fallback",
        })

        result = await execute_conditional(step, ctx)
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_conditional_multi_branch(self):
        ctx = make_context(plan={"level": "medium"})
        step = make_step("cond3", "conditional", {
            "branches": [
                {"if": "{plan.level} == 'high'", "goto": "critical_path"},
                {"if": "{plan.level} == 'medium'", "goto": "normal_path"},
                {"if": "{plan.level} == 'low'", "goto": "simple_path"},
            ],
            "default": "unknown",
        })

        result = await execute_conditional(step, ctx)
        assert result == "normal_path"

    @pytest.mark.asyncio
    async def test_conditional_no_match_default(self):
        ctx = make_context(value=42)
        step = make_step("cond4", "conditional", {
            "branches": [
                {"if": "{value} == 1", "goto": "one"},
            ],
            "default": "not_found",
        })

        result = await execute_conditional(step, ctx)
        assert result == "not_found"

    @pytest.mark.asyncio
    async def test_conditional_no_expression(self):
        """没有表达式时默认走 then 分支"""
        ctx = make_context()
        step = make_step("cond5", "conditional", {"then": "always_go_here"})
        result = await execute_conditional(step, ctx)
        assert result == "always_go_here"


# ════════════════════════════════════════════════════════════════════
# 7. 循环步骤测试
# ════════════════════════════════════════════════════════════════════

class TestLoopStep:
    """测试 execute_loop"""

    @pytest.mark.asyncio
    async def test_loop_with_list(self):
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(_dispatcher=dispatcher, _engine=engine)
        step = make_step("loop1", "loop", {
            "array": [1, 2, 3],
            "item_key": "num",
            "sub_steps": [
                {"id": "process", "type": "llm_call", "config": {"prompt": "Process {num}"}},
            ],
        })

        result = await execute_loop(step, ctx)
        assert len(result) == 3
        assert len(dispatcher.calls) == 3

    @pytest.mark.asyncio
    async def test_loop_json_array_string(self):
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(_dispatcher=dispatcher, _engine=engine)
        step = make_step("loop2", "loop", {
            "array": "[1, 2]",
            "item_key": "num",
            "sub_steps": [
                {"id": "process", "type": "llm_call", "config": {"prompt": "Process {num}"}},
            ],
        })

        result = await execute_loop(step, ctx)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_loop_empty_array(self):
        ctx = make_context()
        step = make_step("loop3", "loop", {
            "array": [],
            "item_key": "item",
            "sub_steps": [],
        })
        result = await execute_loop(step, ctx)
        assert result == []

    @pytest.mark.asyncio
    async def test_loop_max_iterations(self):
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(_dispatcher=dispatcher, _engine=engine)
        step = make_step("loop4", "loop", {
            "array": list(range(100)),
            "item_key": "num",
            "max_iterations": 5,
            "sub_steps": [
                {"id": "process", "type": "llm_call", "config": {"prompt": "{num}"}},
            ],
        })

        result = await execute_loop(step, ctx)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_loop_with_index(self):
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(_dispatcher=dispatcher, _engine=engine)
        step = make_step("loop5", "loop", {
            "array": ["a", "b"],
            "item_key": "item",
            "index_key": "idx",
            "sub_steps": [
                {"id": "process", "type": "llm_call", "config": {"prompt": "#{idx}: {item}"}},
            ],
        })

        result = await execute_loop(step, ctx)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_loop_context_path_array(self):
        """从上下文中解析数组路径"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(
            _dispatcher=dispatcher,
            _engine=engine,
            search=StepResult(step_id="search", step_type=StepType.TOOL_CALL, output=["r1", "r2"]),
        )
        step = make_step("loop6", "loop", {
            "array": "{search.output}",
            "item_key": "r",
            "sub_steps": [
                {"id": "process", "type": "llm_call", "config": {"prompt": "{r}"}},
            ],
        })

        result = await execute_loop(step, ctx)
        assert len(result) == 2


# ════════════════════════════════════════════════════════════════════
# 8. 子工作流步骤测试
# ════════════════════════════════════════════════════════════════════

class TestSubWorkflowStep:
    """测试 execute_sub_workflow"""

    @pytest.mark.asyncio
    async def test_sub_workflow_inline(self):
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(_dispatcher=dispatcher, _engine=engine, query="test")
        step = make_step("sub1", "sub_workflow", {
            "workflow": {
                "name": "inline_sub",
                "steps": [
                    {"id": "inner", "type": "llm_call", "config": {"prompt": "Answer: {query}"}},
                ],
            },
        })

        result = await execute_sub_workflow(step, ctx)
        assert result is not None
        assert len(dispatcher.calls) == 1

    @pytest.mark.asyncio
    async def test_sub_workflow_input_mapping(self):
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        ctx = make_context(_dispatcher=dispatcher, _engine=engine, data="important")
        step = make_step("sub2", "sub_workflow", {
            "workflow": {
                "name": "mapped_sub",
                "steps": [
                    {"id": "inner", "type": "llm_call", "config": {"prompt": "Process: {input_data}"}},
                ],
            },
            "input": {"input_data": "mapped-{data}"},
        })

        result = await execute_sub_workflow(step, ctx)
        call = dispatcher.calls[0]
        assert "mapped-important" in call["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_sub_workflow_missing_engine(self):
        ctx = {"_engine": None}
        step = make_step("sub3", "sub_workflow", {"workflow": "some_wf"})
        with pytest.raises(RuntimeError, match="requires _engine"):
            await execute_sub_workflow(step, ctx)

    @pytest.mark.asyncio
    async def test_sub_workflow_missing_workflow_name(self):
        engine = WorkflowEngine()
        ctx = make_context(_engine=engine)
        step = make_step("sub4", "sub_workflow", {})
        with pytest.raises(ValueError, match="requires 'workflow'"):
            await execute_sub_workflow(step, ctx)


# ════════════════════════════════════════════════════════════════════
# 9. 工作流引擎端到端测试
# ════════════════════════════════════════════════════════════════════

class TestWorkflowEngine:
    """测试 WorkflowEngine.run 端到端"""

    @pytest.mark.asyncio
    async def test_simple_workflow(self):
        """最简工作流：一个 LLM 步骤"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="simple",
            steps=[
                make_step("step1", "llm_call", {"prompt": "Hello {name}"}),
            ],
        )

        result = await engine.run(wf, context={"name": "World"})
        assert result.status == WorkflowState.COMPLETED
        assert result.success
        assert len(result.steps) == 1
        assert result.steps[0].success
        assert len(dispatcher.calls) == 1

    @pytest.mark.asyncio
    async def test_multi_step_workflow(self):
        """多步骤工作流：LLM → Tool → LLM"""
        dispatcher = MockDispatcher()
        registry = MockToolRegistry()
        engine = WorkflowEngine(dispatcher=dispatcher, tool_registry=registry)
        wf = WorkflowDefinition(
            name="multi",
            steps=[
                make_step("plan", "llm_call", {"prompt": "Plan: {query}"}),
                make_step("search", "tool_call", {"tool": "web_search", "input": "{plan.output}"}),
                make_step("respond", "llm_call", {"prompt": "基于 {search.output} 回答：{query}"}),
            ],
        )

        result = await engine.run(wf, context={"query": "天气如何"})
        assert result.status == WorkflowState.COMPLETED
        assert len(result.steps) == 3
        assert all(s.success for s in result.steps)
        assert len(dispatcher.calls) == 2  # plan + respond
        assert len(registry.calls) == 1    # search

    @pytest.mark.asyncio
    async def test_conditional_jump(self):
        """条件分支跳转"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="branch",
            steps=[
                make_step("s1", "llm_call", {"prompt": "always run"}),
                make_step("check", "conditional", {
                    "expression": "{s1.success} == True",
                    "then": "branch_a",
                    "else": "branch_b",
                }),
                make_step("branch_a", "llm_call", {"prompt": "branch A"}),
                make_step("branch_b", "llm_call", {"prompt": "branch B"}),
            ],
        )

        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.COMPLETED
        # s1 + check + branch_a (conditional jumps then breaks)
        assert len(result.steps) == 3
        step_ids = [s.step_id for s in result.steps]
        assert "branch_a" in step_ids
        assert "branch_b" not in step_ids

    @pytest.mark.asyncio
    async def test_conditional_else_jump(self):
        """条件为 false 时跳到 else 分支"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="branch_else",
            steps=[
                make_step("check", "conditional", {
                    "expression": "False",
                    "then": "never_run",
                    "else": "fallback",
                }),
                make_step("never_run", "llm_call", {"prompt": "skip"}),
                make_step("fallback", "llm_call", {"prompt": "run this"}),
            ],
        )

        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.COMPLETED
        step_ids = [s.step_id for s in result.steps]
        assert "never_run" not in step_ids
        assert "fallback" in step_ids

    @pytest.mark.asyncio
    async def test_workflow_result_final_output(self):
        """最终输出使用最后一个步骤的结果"""
        dispatcher = MockDispatcher(responses={"answer": "42 is the answer"})
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="final_test",
            steps=[
                make_step("answer", "llm_call", {"prompt": "answer"}),
            ],
        )
        result = await engine.run(wf, context={})
        assert result.final_output is not None

    @pytest.mark.asyncio
    async def test_workflow_outputs_dict(self):
        """outputs 包含所有非内部上下文键"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="outputs_test",
            steps=[
                make_step("step1", "llm_call", {"prompt": "first"}),
                make_step("step2", "llm_call", {"prompt": "second {step1.output}"}),
            ],
        )
        ctx = {"query": "test question", "user": "alice"}
        result = await engine.run(wf, context=ctx)
        assert "query" in result.outputs
        assert "user" in result.outputs
        assert "step1" in result.outputs
        assert "step2" in result.outputs

    @pytest.mark.asyncio
    async def test_error_step_fails_workflow(self):
        """步骤失败导致工作流失败"""
        engine = WorkflowEngine()
        wf = WorkflowDefinition(
            name="fail_test",
            steps=[
                make_step("bad", "llm_call", {}),  # Missing prompt → error
            ],
        )
        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.FAILED
        assert not result.success

    @pytest.mark.asyncio
    async def test_on_error_skip(self):
        """on_error: skip — 失败跳过继续"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="skip_errors",
            steps=[
                make_step("bad", "llm_call", {"on_error": "skip"}),  # No prompt → error but skip
                make_step("good", "llm_call", {"prompt": "recovered!"}),
            ],
        )
        result = await engine.run(wf, context={})
        # The bad step is skipped (success=True, skipped=True), so the workflow continues
        assert result.status == WorkflowState.COMPLETED
        assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_skip_if_condition(self):
        """skip_if 条件满足时跳过步骤"""
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="skip_if_test",
            steps=[
                make_step("config", "llm_call", {"prompt": "setup"}),
                make_step("optional", "llm_call", {
                    "prompt": "optional step",
                    "skip_if": "{config.success} == True",
                }),
                make_step("required", "llm_call", {"prompt": "always run"}),
            ],
        )
        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.COMPLETED
        optional_step = next(s for s in result.steps if s.step_id == "optional")
        assert optional_step.skipped is True

    @pytest.mark.asyncio
    async def test_run_file(self):
        """通过 run_file 加载并执行工作流文件"""
        yaml_content = """
name: run_file_test
steps:
  - id: hello
    type: llm_call
    config:
      prompt: "Say hello to {name}"
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_wf.yaml"
            path.write_text(yaml_content, encoding="utf-8")

            dispatcher = MockDispatcher()
            engine = WorkflowEngine(dispatcher=dispatcher, workflows_dir=tmpdir)
            result = await engine.run_file("test_wf.yaml", context={"name": "World"})
            assert result.status == WorkflowState.COMPLETED
            assert len(dispatcher.calls) == 1


# ════════════════════════════════════════════════════════════════════
# 10. 研究工作流集成测试
# ════════════════════════════════════════════════════════════════════

class TestResearchWorkflow:
    """测试 workflows/research.yaml 完整执行"""

    @pytest.mark.asyncio
    async def test_research_workflow_parsing(self):
        """验证 research.yaml 能被正确解析"""
        engine = WorkflowEngine(workflows_dir=str(Path(__file__).parent.parent / "workflows"))
        wf = engine.load("research")
        assert wf.name == "研究工作流"
        assert len(wf.steps) >= 5
        # Verify step types
        types = [s.type for s in wf.steps]
        assert StepType.LLM_CALL in types
        assert StepType.TOOL_CALL in types
        assert StepType.CONDITIONAL in types

    @pytest.mark.asyncio
    async def test_research_workflow_execution(self):
        """端到端执行研究工作流"""
        dispatcher = MockDispatcher(responses={
            "研究计划": json.dumps({
                "approach": "文献综述法",
                "search_queries": ["AI trends"],
                "analysis_angles": ["技术发展"],
                "expected_sections": ["概述", "分析", "结论"],
                "need_deep_analysis": True,
                "confidence": "high",
            }),
            "深度分析": "深度分析结果：AI 发展迅速...",
            "快速摘要": "摘要：关键发现...",
            "最终研究": "完整研究报告...",
        })
        registry = MockToolRegistry()
        engine = WorkflowEngine(
            dispatcher=dispatcher,
            tool_registry=registry,
            workflows_dir=str(Path(__file__).parent.parent / "workflows"),
        )

        result = await engine.run_file("research", context={"query": "AI 发展趋势"})
        assert result.status == WorkflowState.COMPLETED
        assert result.final_output is not None
        # Should have executed: plan, search, depth_check, deep_analysis, final
        # (quick_summary might be skipped depending on branch)
        executed = [s.step_id for s in result.steps if s.success and not s.skipped]
        assert "plan" in executed
        assert "depth_check" in executed


# ════════════════════════════════════════════════════════════════════
# 11. 代码审查工作流集成测试
# ════════════════════════════════════════════════════════════════════

class TestCodeReviewWorkflow:
    """测试 workflows/code_review.yaml 完整执行"""

    @pytest.mark.asyncio
    async def test_code_review_parsing(self):
        """验证 code_review.yaml 能被正确解析"""
        engine = WorkflowEngine(workflows_dir=str(Path(__file__).parent.parent / "workflows"))
        wf = engine.load("code_review")
        assert wf.name == "代码审查工作流"
        assert len(wf.steps) >= 6
        types = [s.type for s in wf.steps]
        assert StepType.LLM_CALL in types
        assert StepType.CONDITIONAL in types

    @pytest.mark.asyncio
    async def test_code_review_execution(self):
        """端到端执行代码审查工作流"""
        dispatcher = MockDispatcher(responses={
            "风格和结构": json.dumps({
                "overall_score": 8,
                "issues": [],
                "strengths": ["清晰的命名"],
                "summary": "代码风格良好",
            }),
            "逻辑正确性": json.dumps({
                "overall_score": 7,
                "issues": [{"severity": "warning", "line": 10, "message": "空值检查缺失"}],
                "strengths": ["逻辑清晰"],
                "summary": "需要注意边界条件",
            }),
            "安全漏洞": json.dumps({
                "overall_score": 9,
                "vulnerabilities": [],
                "safe_practices": ["输入验证充分"],
                "summary": "无明显安全漏洞",
            }),
            "性能": json.dumps({
                "overall_score": 8,
                "bottlenecks": [],
                "complexity": {"time": "O(n)", "space": "O(1)"},
                "summary": "性能良好",
            }),
            "综合所有": "## 最终审查报告\n\n总体评分：8/10\n审核结论：通过 ✅",
        })
        engine = WorkflowEngine(
            dispatcher=dispatcher,
            workflows_dir=str(Path(__file__).parent.parent / "workflows"),
        )

        result = await engine.run_file("code_review", context={
            "code": "def foo(): pass\n",
            "diff": "",
            "language": "Python",
        })
        assert result.status == WorkflowState.COMPLETED
        assert result.final_output is not None
        executed = [s.step_id for s in result.steps if s.success and not s.skipped]
        assert "style_review" in executed
        assert "logic_review" in executed
        assert "security_review" in executed
        assert "perf_review" in executed


# ════════════════════════════════════════════════════════════════════
# 12. 错误处理测试
# ════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    """测试各种错误场景"""

    @pytest.mark.asyncio
    async def test_unknown_step_type(self):
        """未知步骤类型 → 失败"""
        engine = WorkflowEngine()
        wf = WorkflowDefinition(
            name="unknown_type",
            steps=[
                StepDefinition(id="bad", type="invalid_type", config={}),  # type: ignore
            ],
        )
        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.FAILED

    @pytest.mark.asyncio
    async def test_dispatcher_failure(self):
        """dispatcher 抛出异常 → 步骤失败"""
        bad_dispatcher = MagicMock()
        bad_dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("API down"))
        engine = WorkflowEngine(dispatcher=bad_dispatcher)
        wf = WorkflowDefinition(
            name="dispatcher_fail",
            steps=[
                make_step("llm", "llm_call", {"prompt": "test"}),
            ],
        )
        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.FAILED

    @pytest.mark.asyncio
    async def test_tool_call_error_skip(self):
        """工具调用异常 + on_error:skip → 跳过继续"""
        bad_registry = MagicMock()
        bad_registry.call = AsyncMock(side_effect=Exception("Tool down"))
        dispatcher = MockDispatcher()
        engine = WorkflowEngine(tool_registry=bad_registry, dispatcher=dispatcher)
        wf = WorkflowDefinition(
            name="tool_error_skip",
            steps=[
                make_step("bad_tool", "tool_call", {"tool": "broken", "on_error": "skip"}),
                make_step("recover", "llm_call", {"prompt": "recovered"}),
            ],
        )
        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.COMPLETED

    @pytest.mark.asyncio
    async def test_empty_workflow(self):
        """空工作流（无步骤）→ 正常完成"""
        engine = WorkflowEngine()
        wf = WorkflowDefinition(name="empty")
        result = await engine.run(wf, context={})
        assert result.status == WorkflowState.COMPLETED
        assert len(result.steps) == 0


# ════════════════════════════════════════════════════════════════════
# 13. StepExecutor 兼容性测试
# ════════════════════════════════════════════════════════════════════

class TestStepExecutor:
    """测试 StepExecutor 门面类"""

    @pytest.mark.asyncio
    async def test_executor_llm_call(self):
        executor = StepExecutor()
        dispatcher = MockDispatcher()
        ctx = make_context(_dispatcher=dispatcher)
        step = make_step("llm", "llm_call", {"prompt": "test"})

        result = await executor.execute(step, ctx)
        assert result.success
        assert result.step_id == "llm"

    @pytest.mark.asyncio
    async def test_executor_tool_call(self):
        executor = StepExecutor()
        registry = MockToolRegistry()
        ctx = make_context(_tool_registry=registry)
        step = make_step("tool", "tool_call", {"tool": "web_search", "input": "test"})

        result = await executor.execute(step, ctx)
        assert result.success

    @pytest.mark.asyncio
    async def test_executor_conditional(self):
        executor = StepExecutor()
        ctx = make_context(flag=True)
        step = make_step("cond", "conditional", {
            "expression": "{flag} == True",
            "then": "target",
        })
        result = await executor.execute(step, ctx)
        assert result.success
        assert result.output == "target"

    @pytest.mark.asyncio
    async def test_executor_unknown_type(self):
        executor = StepExecutor()
        ctx = make_context()
        step = StepDefinition(id="bad", type="nonexistent", config={})  # type: ignore
        result = await executor.execute(step, ctx)
        assert not result.success


# ════════════════════════════════════════════════════════════════════
# Direct runner (for python tests/test_workflow.py)
# ════════════════════════════════════════════════════════════════════

async def _run_all_manual():
    """Manual test runner (non-pytest mode)"""
    print("=" * 60)
    print("Workflow Engine — Manual Test Runner")
    print("=" * 60)

    tests = []

    # 1. YAML Parsing
    print("\n[1] YAML Parsing...")
    t = TestYamlParsing()
    t.test_parse_minimal_yaml()
    t.test_parse_all_step_types()
    t.test_from_dict()
    t.test_default_type_is_llm_call()
    t.test_load_workflow_file()
    t.test_load_nonexistent_file()
    tests.append(("YAML Parsing", True))

    # 2. Template Rendering
    print("\n[2] Template Rendering...")
    t = TestTemplateRendering()
    t.test_simple_variable()
    t.test_nested_path()
    t.test_step_result_output()
    t.test_step_result_success()
    t.test_unresolved_placeholder()
    t.test_dict_value()
    t.test_list_value()
    t.test_resolve_path_deep_nesting()
    t.test_resolve_path_step_result_properties()
    t.test_resolve_path_none()
    t.test_render_template_non_string()
    tests.append(("Template Rendering", True))

    # 3. Expression Evaluation
    print("\n[3] Expression Evaluation...")
    t = TestExpressionEvaluation()
    t.test_simple_boolean_true()
    t.test_simple_boolean_false()
    t.test_equality()
    t.test_numeric_comparison()
    t.test_len_comparison()
    t.test_empty_expression()
    t.test_invalid_expression()
    tests.append(("Expression Evaluation", True))

    # 4. LLM Call
    print("\n[4] LLM Call Steps...")
    t = TestLLMCallStep()
    await t.test_basic_llm_call()
    await t.test_llm_call_with_template()
    await t.test_llm_call_with_system_prompt()
    await t.test_llm_call_missing_prompt()
    await t.test_llm_call_missing_dispatcher()
    await t.test_llm_call_with_model_params()
    tests.append(("LLM Call Steps", True))

    # 5. Tool Call
    print("\n[5] Tool Call Steps...")
    t = TestToolCallStep()
    await t.test_basic_tool_call()
    await t.test_tool_call_missing_tool_name()
    await t.test_tool_call_with_params()
    await t.test_tool_call_fallback_no_registry()
    tests.append(("Tool Call Steps", True))

    # 6. Conditional
    print("\n[6] Conditional Steps...")
    t = TestConditionalStep()
    await t.test_conditional_then()
    await t.test_conditional_else()
    await t.test_conditional_multi_branch()
    await t.test_conditional_no_match_default()
    await t.test_conditional_no_expression()
    tests.append(("Conditional Steps", True))

    # 7. Loop
    print("\n[7] Loop Steps...")
    t = TestLoopStep()
    await t.test_loop_with_list()
    await t.test_loop_json_array_string()
    await t.test_loop_empty_array()
    await t.test_loop_max_iterations()
    await t.test_loop_with_index()
    await t.test_loop_context_path_array()
    tests.append(("Loop Steps", True))

    # 8. Sub-Workflow
    print("\n[8] Sub-Workflow Steps...")
    t = TestSubWorkflowStep()
    await t.test_sub_workflow_inline()
    await t.test_sub_workflow_input_mapping()
    await t.test_sub_workflow_missing_engine()
    await t.test_sub_workflow_missing_workflow_name()
    tests.append(("Sub-Workflow Steps", True))

    # 9. Engine E2E
    print("\n[9] Engine End-to-End...")
    t = TestWorkflowEngine()
    await t.test_simple_workflow()
    await t.test_multi_step_workflow()
    await t.test_conditional_jump()
    await t.test_conditional_else_jump()
    await t.test_workflow_result_final_output()
    await t.test_workflow_outputs_dict()
    await t.test_error_step_fails_workflow()
    await t.test_on_error_skip()
    await t.test_skip_if_condition()
    await t.test_run_file()
    tests.append(("Engine E2E", True))

    # 10. Research Workflow
    print("\n[10] Research Workflow...")
    t = TestResearchWorkflow()
    await t.test_research_workflow_parsing()
    await t.test_research_workflow_execution()
    tests.append(("Research Workflow", True))

    # 11. Code Review Workflow
    print("\n[11] Code Review Workflow...")
    t = TestCodeReviewWorkflow()
    await t.test_code_review_parsing()
    await t.test_code_review_execution()
    tests.append(("Code Review Workflow", True))

    # 12. Error Handling
    print("\n[12] Error Handling...")
    t = TestErrorHandling()
    await t.test_unknown_step_type()
    await t.test_dispatcher_failure()
    await t.test_tool_call_error_skip()
    await t.test_empty_workflow()
    tests.append(("Error Handling", True))

    # 13. StepExecutor
    print("\n[13] StepExecutor Compatibility...")
    t = TestStepExecutor()
    await t.test_executor_llm_call()
    await t.test_executor_tool_call()
    await t.test_executor_conditional()
    await t.test_executor_unknown_type()
    tests.append(("StepExecutor", True))

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in tests if ok)
    total = len(tests)
    print(f"Results: {passed}/{total} test groups passed")
    for name, ok in tests:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
    print("=" * 60)

    if passed == total:
        print("\n🎉 ALL TESTS PASSED")
    else:
        print(f"\n⚠️  {total - passed} groups FAILED")


if __name__ == "__main__":
    asyncio.run(_run_all_manual())
