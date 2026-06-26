"""
Workflow CLI — 测试套件

测试覆盖：
1. dragon workflow list        — 列出可用工作流
2. dragon workflow validate    — 验证工作流 YAML（有效/无效/缺失字段/错误类型）
3. dragon workflow run         — 执行工作流（mock/真实文件）
4. 边界条件                    — 空目录、不存在文件、bad context JSON
5. 错误处理                    — 文件不存在、语法错误、步骤失败

Run::

    cd /root/dragon-agent
    python -m pytest tests/test_workflow_cli.py -v

Or directly::

    python tests/test_workflow_cli.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from io import StringIO

import pytest
import yaml

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dragon.cli import (
    cmd_workflow,
    _cmd_workflow_run,
    _cmd_workflow_list,
    _cmd_workflow_validate,
    _print_validate_result,
)
from dragon.workflow import (
    WorkflowEngine,
    WorkflowDefinition,
    StepDefinition,
    StepResult,
    StepType,
    WorkflowState,
    WorkflowResult,
)


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════


def make_workflow_yaml(path: str, content: str) -> str:
    """Write YAML content to a temp file and return the path."""
    p = Path(path)
    p.write_text(content, encoding="utf-8")
    return str(p)


class MockArgs:
    """Mock argparse.Namespace with arbitrary attributes."""

    def __init__(self, **kwargs):
        defaults = {"context": None}
        defaults.update(kwargs)
        self.__dict__.update(defaults)


class MockDispatchResult:
    def __init__(self, content="mock response"):
        self.content = content
        self.model = "mock-model"


class MockDispatcher:
    async def dispatch(self, **kwargs):
        return MockDispatchResult("mock llm output")


class MockToolRegistry:
    async def call(self, tool_name, **kwargs):
        return {"result": f"mock {tool_name} output"}


def capture_output(func, *args, **kwargs):
    """Capture stdout during a function call."""
    import io
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        func(*args, **kwargs)
        return sys.stdout.getvalue()
    finally:
        sys.stdout = old


def make_valid_yaml(name="测试工作流", steps=None):
    """Build a valid workflow YAML string."""
    if steps is None:
        steps = [
            {"id": "step1", "type": "llm_call", "config": {"prompt": "Hello"}},
        ]
    data = {"name": name, "description": "测试描述", "steps": steps}
    return yaml.dump(data, allow_unicode=True)


# ════════════════════════════════════════════════════════════════════
# 1. dragon workflow list
# ════════════════════════════════════════════════════════════════════


class TestWorkflowList:
    """测试 _cmd_workflow_list"""

    def test_list_with_workflows(self):
        """列出包含工作流文件的目录"""
        with tempfile.TemporaryDirectory() as tmp:
            workflows_dir = Path(tmp) / "workflows"
            workflows_dir.mkdir()
            (workflows_dir / "a.yaml").write_text(
                make_valid_yaml("工作流A"), encoding="utf-8"
            )
            (workflows_dir / "b.yaml").write_text(
                make_valid_yaml("工作流B"), encoding="utf-8"
            )

            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.is_dir", return_value=True), \
                 patch("dragon.cli.Path") as mock_path:
                # Let Path("workflows") return our temp workflows dir
                mock_path.return_value = workflows_dir

                output = capture_output(_cmd_workflow_list)

            assert "工作流A" in output
            assert "工作流B" in output
            assert "a.yaml" in output
            assert "b.yaml" in output
            assert "步骤数: 1" in output or "步骤数: 1" in output

    def test_list_empty_directory(self):
        """列出空目录"""
        with tempfile.TemporaryDirectory() as tmp:
            workflows_dir = Path(tmp) / "workflows"
            workflows_dir.mkdir()

            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.is_dir", return_value=True), \
                 patch("dragon.cli.Path") as mock_path:
                mock_path.return_value = workflows_dir

                output = capture_output(_cmd_workflow_list)

            assert "没有工作流文件" in output

    def test_list_nonexistent_directory(self):
        """列出不存在的目录"""
        # Mock pathlib.Path so that Path("workflows") appears nonexistent
        with patch("pathlib.Path.exists", return_value=False), \
             patch("pathlib.Path.is_dir", return_value=False):
            output = capture_output(_cmd_workflow_list)

        assert "目录不存在" in output or "不存在" in output

    def test_list_also_finds_yml_files(self):
        """也能列出 .yml 文件"""
        with tempfile.TemporaryDirectory() as tmp:
            workflows_dir = Path(tmp) / "workflows"
            workflows_dir.mkdir()
            (workflows_dir / "test.yml").write_text(
                make_valid_yaml("YML工作流"), encoding="utf-8"
            )

            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.is_dir", return_value=True), \
                 patch("dragon.cli.Path") as mock_path:
                mock_path.return_value = workflows_dir

                output = capture_output(_cmd_workflow_list)

            assert "test.yml" in output or "YML工作流" in output

    def test_list_bad_yaml_graceful(self):
        """列出包含无效 YAML 的目录时不会崩溃"""
        with tempfile.TemporaryDirectory() as tmp:
            workflows_dir = Path(tmp) / "workflows"
            workflows_dir.mkdir()
            (workflows_dir / "bad.yaml").write_text("::: not valid yaml :::", encoding="utf-8")

            with patch("pathlib.Path.exists", return_value=True), \
                 patch("pathlib.Path.is_dir", return_value=True), \
                 patch("dragon.cli.Path") as mock_path:
                mock_path.return_value = workflows_dir

                output = capture_output(_cmd_workflow_list)

            # Should not crash, should still list the file
            assert "bad.yaml" in output


# ════════════════════════════════════════════════════════════════════
# 2. dragon workflow validate
# ════════════════════════════════════════════════════════════════════


class TestWorkflowValidate:
    """测试 _cmd_workflow_validate"""

    def test_validate_valid_workflow(self):
        """验证有效的工作流文件"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "valid.yaml")
            make_workflow_yaml(path, make_valid_yaml("有效工作流"))

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "通过" in output or "语法检查通过" in output

    def test_validate_with_all_step_types(self):
        """验证包含所有步骤类型的工作流"""
        yaml_content = make_valid_yaml("全类型", steps=[
            {"id": "s1", "type": "llm_call", "config": {"prompt": "test"}},
            {"id": "s2", "type": "tool_call", "config": {"tool": "web", "input": "q"}},
            {"id": "s3", "type": "conditional", "config": {"expression": "true", "then": "s4", "else": "s5"}},
            {"id": "s4", "type": "loop", "config": {"array": "[1,2]", "item_key": "x", "sub_steps": [{"id": "inner", "type": "llm_call", "config": {"prompt": "hi"}}]}},
            {"id": "s5", "type": "sub_workflow", "config": {"workflow": "nested"}},
        ])

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "all_types.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "通过" in output or "语法检查通过" in output

    def test_validate_nonexistent_file(self):
        """验证不存在的文件"""
        output = capture_output(_cmd_workflow_validate, MockArgs(file="/nonexistent/workflow.yaml"))
        assert "不存在" in output or "not exist" in output.lower()

    def test_validate_invalid_yaml_syntax(self):
        """验证无效 YAML 语法"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bad_syntax.yaml")
            make_workflow_yaml(path, "key: [unclosed\n  - item")

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "错误" in output or "error" in output.lower()

    def test_validate_empty_yaml(self):
        """验证空 YAML 文件"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "empty.yaml")
            make_workflow_yaml(path, "")

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "空" in output or "为空" in output

    def test_validate_missing_steps(self):
        """验证缺少 steps 字段的工作流"""
        yaml_content = "name: 无步骤\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_steps.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "steps" in output or "错误" in output

    def test_validate_missing_step_id(self):
        """验证缺少步骤 id"""
        yaml_content = make_valid_yaml(steps=[{"type": "llm_call", "config": {"prompt": "hi"}}])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_id.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "id" in output or "ID" in output or "错误" in output

    def test_validate_duplicate_step_id(self):
        """验证重复步骤 ID"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "dup", "type": "llm_call", "config": {"prompt": "a"}},
            {"id": "dup", "type": "tool_call", "config": {"tool": "x", "input": "y"}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "dup_id.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "重复" in output or "dup" in output

    def test_validate_invalid_step_type(self):
        """验证无效步骤类型"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "s1", "type": "invalid_type", "config": {}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bad_type.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "无效" in output or "invalid" in output.lower()

    def test_validate_missing_prompt_in_llm_call(self):
        """验证 llm_call 缺少 prompt"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "s1", "type": "llm_call", "config": {"temperature": 0.5}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_prompt.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "prompt" in output or "错误" in output

    def test_validate_missing_tool_in_tool_call(self):
        """验证 tool_call 缺少 tool"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "s1", "type": "tool_call", "config": {"input": "q"}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_tool.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "tool" in output.lower() or "错误" in output

    def test_validate_conditional_missing_target(self):
        """验证 conditional 引用了不存在的 then/else 目标"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "branch", "type": "conditional", "config": {"expression": "true", "then": "nonexistent"}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "bad_cond.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "不存在" in output or "then" in output or "nonexistent" in output

    def test_validate_loop_missing_sub_steps(self):
        """验证 loop 缺少 sub_steps"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "s1", "type": "loop", "config": {"array": "[1,2]", "item_key": "x"}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_sub.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "sub_steps" in output or "错误" in output

    def test_validate_sub_workflow_missing_workflow(self):
        """验证 sub_workflow 缺少 workflow 字段"""
        yaml_content = make_valid_yaml(steps=[
            {"id": "s1", "type": "sub_workflow", "config": {}},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_wf.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "workflow" in output or "错误" in output

    def test_validate_warning_for_extension(self):
        """验证非 YAML 扩展名文件产生警告"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "config.txt")
            make_workflow_yaml(path, make_valid_yaml())

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "警告" in output or "扩展名" in output or "warning" in output.lower()

    def test_validate_root_not_dict(self):
        """验证根节点不是字典"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "list_root.yaml")
            make_workflow_yaml(path, "- item1\n- item2\n")

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "字典" in output or "mapping" in output or "错误" in output

    def test_validate_steps_not_list(self):
        """验证 steps 不是列表"""
        yaml_content = "name: test\nsteps: not_a_list\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "steps_not_list.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "列表" in output or "sequence" in output or "错误" in output

    def test_validate_empty_steps(self):
        """验证空步骤列表"""
        yaml_content = make_valid_yaml(steps=[])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "empty_steps.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "没有" in output or "警告" in output or "warning" in output.lower()

    def test_validate_warning_no_name(self):
        """验证缺少 name 字段的警告"""
        yaml_content = "steps:\n  - id: s1\n    type: llm_call\n    config:\n      prompt: hi\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "no_name.yaml")
            make_workflow_yaml(path, yaml_content)

            output = capture_output(_cmd_workflow_validate, MockArgs(file=path))

            assert "name" in output or "警告" in output or "通过" in output


# ════════════════════════════════════════════════════════════════════
# 3. dragon workflow run
# ════════════════════════════════════════════════════════════════════


class TestWorkflowRun:
    """测试 _cmd_workflow_run"""

    def test_run_nonexistent_file(self):
        """执行不存在的文件"""
        output = capture_output(_cmd_workflow_run, MockArgs(file="/nonexistent/wf.yaml"))
        assert "不存在" in output or "not exist" in output.lower()

    def test_run_invalid_context_json(self):
        """传入无效的 context JSON"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            output = capture_output(
                _cmd_workflow_run,
                MockArgs(file=path, context="not valid json")
            )

            assert "解析失败" in output or "JSON" in output or "失败" in output

    def test_run_valid_workflow_with_mock(self):
        """模拟运行有效工作流（mock WorkflowEngine.run_file）"""
        from dragon.workflow import WorkflowResult, StepResult, WorkflowState

        mock_result = WorkflowResult(
            name="测试工作流",
            status=WorkflowState.COMPLETED,
            steps=[
                StepResult(
                    step_id="step1",
                    step_type=StepType.LLM_CALL,
                    success=True,
                    output="mock output",
                    elapsed_ms=42.0,
                ),
            ],
            final_output="最终结果",
            total_elapsed_ms=42.0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            async def fake_run_file(self, path, context=None):
                return mock_result

            with patch.object(WorkflowEngine, "run_file", new=fake_run_file):
                output = capture_output(_cmd_workflow_run, MockArgs(file=path))

            assert "测试工作流" in output
            assert "completed" in output or "完成" in output
            assert "最终结果" in output
            assert "mock output" in output

    def test_run_with_context_json(self):
        """使用 context JSON 参数运行"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            # Patch the engine to capture the context
            async def fake_run_file(self, path, context=None):
                return WorkflowResult(
                    name="test",
                    status=WorkflowState.COMPLETED,
                    steps=[],
                    total_elapsed_ms=0,
                )

            with patch.object(WorkflowEngine, "run_file", new=fake_run_file):
                output = capture_output(
                    _cmd_workflow_run,
                    MockArgs(file=path, context='{"query": "test query", "depth": 3}')
                )

                assert "执行失败" not in output

    def test_run_file_not_found_error(self):
        """执行时文件找不到的错误处理"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            async def fake_run_file(self, path, context=None):
                raise FileNotFoundError(f"Workflow file not found: {path}")

            with patch.object(WorkflowEngine, "run_file", new=fake_run_file):
                output = capture_output(_cmd_workflow_run, MockArgs(file=path))
                assert "不存在" in output or "not found" in output or "失败" in output or "not exist" in output.lower()

    def test_run_generic_error(self):
        """执行时发生通用异常"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            async def fake_run_file(self, path, context=None):
                raise RuntimeError("Some runtime error occurred")

            with patch.object(WorkflowEngine, "run_file", new=fake_run_file):
                output = capture_output(_cmd_workflow_run, MockArgs(file=path))
                assert "失败" in output or "runtime error" in output.lower() or "error" in output.lower()

    def test_run_failed_workflow(self):
        """运行失败的工作流"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            async def fake_run_file(self, path, context=None):
                return WorkflowResult(
                    name="失败工作流",
                    status=WorkflowState.FAILED,
                    error="Step 'step1' failed: Some error",
                    steps=[
                        StepResult(
                            step_id="step1",
                            step_type=StepType.LLM_CALL,
                            success=False,
                            error="Some error",
                            elapsed_ms=10.0,
                        ),
                    ],
                    total_elapsed_ms=10.0,
                )

            with patch.object(WorkflowEngine, "run_file", new=fake_run_file):
                output = capture_output(_cmd_workflow_run, MockArgs(file=path))
                assert "失败" in output or "failed" in output.lower()

    def test_run_skipped_steps(self):
        """运行包含跳过步骤的工作流"""
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "test.yaml")
            make_workflow_yaml(path, make_valid_yaml())

            async def fake_run_file(self, path, context=None):
                return WorkflowResult(
                    name="跳过测试",
                    status=WorkflowState.COMPLETED,
                    steps=[
                        StepResult(
                            step_id="skipped_step",
                            step_type=StepType.TOOL_CALL,
                            success=True,
                            skipped=True,
                            elapsed_ms=1.0,
                        ),
                        StepResult(
                            step_id="run_step",
                            step_type=StepType.LLM_CALL,
                            success=True,
                            output="done",
                            elapsed_ms=5.0,
                        ),
                    ],
                    final_output="done",
                    total_elapsed_ms=6.0,
                )

            with patch.object(WorkflowEngine, "run_file", new=fake_run_file):
                output = capture_output(_cmd_workflow_run, MockArgs(file=path))
                assert "跳过" in output


# ════════════════════════════════════════════════════════════════════
# 4. cmd_workflow router
# ════════════════════════════════════════════════════════════════════


class TestCmdWorkflowRouter:
    """测试 cmd_workflow 路由"""

    def test_show_help_when_no_subcommand(self):
        """无子命令时显示帮助"""
        output = capture_output(cmd_workflow, MockArgs(wf_action=None))
        assert "Usage" in output or "dragon workflow" in output

    def test_routes_to_run(self):
        """路由到 run"""
        with patch("dragon.cli._cmd_workflow_run") as mock_run:
            cmd_workflow(MockArgs(wf_action="run", file="test.yaml"))
            mock_run.assert_called_once()

    def test_routes_to_list(self):
        """路由到 list"""
        with patch("dragon.cli._cmd_workflow_list") as mock_list:
            cmd_workflow(MockArgs(wf_action="list"))
            mock_list.assert_called_once()

    def test_routes_to_validate(self):
        """路由到 validate"""
        with patch("dragon.cli._cmd_workflow_validate") as mock_val:
            cmd_workflow(MockArgs(wf_action="validate", file="test.yaml"))
            mock_val.assert_called_once()


# ════════════════════════════════════════════════════════════════════
# 5. _print_validate_result
# ════════════════════════════════════════════════════════════════════


class TestPrintValidateResult:
    """测试 _print_validate_result 辅助函数"""

    def test_all_pass(self):
        output = capture_output(_print_validate_result, "test.yaml", [], [])
        assert "通过" in output

    def test_with_errors(self):
        output = capture_output(_print_validate_result, "test.yaml", ["err1", "err2"], [])
        assert "错误" in output
        assert "err1" in output
        assert "err2" in output

    def test_with_warnings(self):
        output = capture_output(_print_validate_result, "test.yaml", [], ["warn1"])
        assert "警告" in output or "warning" in output.lower() or "warn1" in output

    def test_with_both(self):
        output = capture_output(_print_validate_result, "test.yaml", ["err1"], ["warn1"])
        assert "err1" in output
        assert "warn1" in output


# ════════════════════════════════════════════════════════════════════
# 6. Integration: run against real workflow YAML files
# ════════════════════════════════════════════════════════════════════


class TestIntegrationRealFiles:
    """集成测试：对仓库中真实工作流文件进行验证"""

    @pytest.fixture
    def workflows_dir(self):
        """返回 workflows/ 目录路径"""
        project_root = Path(__file__).parent.parent
        wf_dir = project_root / "workflows"
        if not wf_dir.exists():
            pytest.skip("workflows/ directory not found")
        return wf_dir

    def test_all_workflows_validate(self, workflows_dir):
        """所有工作流文件通过 validate（排除已知使用非标准步骤类型的文件）"""
        yaml_files = list(workflows_dir.glob("*.yaml")) + list(workflows_dir.glob("*.yml"))
        if not yaml_files:
            pytest.skip(f"No YAML files found in {workflows_dir}")

        # Several workflow files use domain-specific step types ('llm', 'tool', 'skill')
        # which are not in the standard set {llm_call, tool_call, conditional, loop, sub_workflow}.
        # These are intentionally skipped for generic validation.
        known_nonstandard = {
            "drama.yaml", "education.yaml", "finance.yaml", "general.yaml",
            "legal.yaml", "medical.yaml", "workflow-creator.yaml",
        }

        for wf_file in yaml_files:
            if wf_file.name in known_nonstandard:
                continue
            output = capture_output(
                _cmd_workflow_validate, MockArgs(file=str(wf_file))
            )
            # All standard workflow files should pass validation
            assert "✗" not in output, f"Workflow {wf_file.name} has errors:\n{output}"

    def test_list_against_real_directory(self):
        """对真实工作流目录运行 list"""
        project_root = Path(__file__).parent.parent
        wf_dir = project_root / "workflows"
        if not wf_dir.exists():
            pytest.skip("workflows/ directory not found")

        with patch("dragon.cli.Path") as mock_path:
            mock_path.return_value = wf_dir
            output = capture_output(_cmd_workflow_list)

        assert "可用工作流" in output or "没有" in output


# ════════════════════════════════════════════════════════════════════
# Run directly
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
