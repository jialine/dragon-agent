"""
Step executors — 执行工作流中的各个步骤类型。

每个 executor 接收步骤定义和上下文，返回 StepResult。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from . import StepDefinition, StepResult, StepType

logger = logging.getLogger("dragon.workflow.steps")


class StepExecutor:
    """步骤执行器 — 根据 StepType 分发到对应的 handler"""

    async def execute(
        self,
        step: StepDefinition,
        context: Dict[str, Any],
    ) -> StepResult:
        """
        执行一个步骤。

        Args:
            step:    步骤定义
            context: 运行时上下文（包含所有已执行步骤的输出）

        Returns:
            StepResult
        """
        t0 = time.perf_counter()

        try:
            if step.type == StepType.LLM:
                output = await self._execute_llm(step, context)
            elif step.type == StepType.TOOL:
                output = await self._execute_tool(step, context)
            elif step.type == StepType.SKILL:
                output = await self._execute_skill(step, context)
            elif step.type == StepType.TRANSFORM:
                output = self._execute_transform(step, context)
            else:
                return StepResult(
                    step_id=step.id,
                    step_name=step.name,
                    success=False,
                    error=f"Unknown step type: {step.type}",
                )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.exception("Step %s (%s) failed", step.id, step.type)
            return StepResult(
                step_id=step.id,
                step_name=step.name,
                success=False,
                error=str(exc),
                elapsed_ms=elapsed,
            )

        elapsed = (time.perf_counter() - t0) * 1000
        return StepResult(
            step_id=step.id,
            step_name=step.name,
            success=True,
            output=output,
            elapsed_ms=elapsed,
        )

    # ------------------------------------------------------------------
    # LLM step
    # ------------------------------------------------------------------

    async def _execute_llm(
        self, step: StepDefinition, context: Dict[str, Any]
    ) -> str:
        """执行 LLM 推理步骤"""
        prompt = self._render_template(step.prompt, context)

        logger.debug("LLM step '%s': prompt=%s", step.id, prompt[:200])

        # Use dispatcher from context (set by main.py)
        dispatcher = context.get("_dispatcher")
        if dispatcher is None:
            logger.error("No dispatcher in context — LLM step '%s' cannot run", step.id)
            raise RuntimeError("LLM step requires dispatcher in context")

        result = await dispatcher.dispatch(
            industry="general",
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        return result.content

    # ------------------------------------------------------------------
    # Tool step
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, step: StepDefinition, context: Dict[str, Any]
    ) -> Any:
        """执行内置工具调用"""
        # Determine which tool(s) to call
        tool_names: list[str] = []
        if step.tools_from:
            # Dynamic: read from plan output
            plan = context.get("plan", {})
            tools = plan.get(step.tools_from, [])
            if isinstance(tools, list):
                tool_names = tools
            elif isinstance(tools, str):
                tool_names = [tools]
        elif step.tool:
            tool_names = [step.tool]

        if not tool_names:
            logger.warning("Tool step '%s': no tools selected", step.id)
            return None

        # Resolve input
        if step.input_from:
            query = str(context.get(step.input_from, ""))
        elif step.input:
            query = self._render_template(step.input, context)
        else:
            query = str(context.get("_query", ""))

        results = {}
        for tool_name in tool_names:
            try:
                result = await self._call_tool(tool_name, query)
                results[tool_name] = result
            except Exception as exc:
                logger.warning("Tool '%s' failed: %s", tool_name, exc)
                results[tool_name] = None

        return results if len(results) > 1 else results.get(tool_names[0])

    async def _call_tool(self, tool_name: str, query: str) -> Any:
        """Call a Dragon tool by name."""
        # Map common tool names to actual implementations
        if tool_name == "web_search":
            try:
                from dragon.web_search import search
                return await search(query)
            except ImportError:
                return f"[web_search not available] query: {query}"
        elif tool_name == "vision":
            return "[vision tool — stub]"
        elif tool_name == "maps":
            return "[maps tool — stub]"
        else:
            logger.warning("Unknown tool: %s", tool_name)
            return None

    # ------------------------------------------------------------------
    # Skill step
    # ------------------------------------------------------------------

    async def _execute_skill(
        self, step: StepDefinition, context: Dict[str, Any]
    ) -> Any:
        """执行技能调用"""
        # Determine which skill(s) to call
        skill_names: list[str] = []
        if step.skills_from:
            plan = context.get("plan", {})
            skills = plan.get(step.skills_from, [])
            if isinstance(skills, list):
                skill_names = skills
            elif isinstance(skills, str):
                skill_names = [skills]
        elif step.skill:
            skill_names = [step.skill]

        if not skill_names:
            logger.warning("Skill step '%s': no skills selected", step.id)
            return None

        # Build skill context
        skill_context = {}
        for key, template in step.context.items():
            skill_context[key] = self._render_template(template, context)

        results = {}
        for skill_name in skill_names:
            try:
                result = await self._call_skill(skill_name, skill_context)
                results[skill_name] = result
            except Exception as exc:
                logger.warning("Skill '%s' failed: %s", skill_name, exc)
                results[skill_name] = None

        return results if len(results) > 1 else results.get(skill_names[0])

    async def _call_skill(self, skill_name: str, context: Dict[str, Any]) -> Any:
        """Call a Dragon skill by name."""
        # Map common skill names
        if skill_name in ("jury_debate", "jury", "debate"):
            return "[jury_debate skill — stub: would call multi-model debate]"
        elif skill_name in ("fact_check", "factcheck"):
            return "[fact_check skill — stub: would verify facts]"
        elif skill_name in ("consensus",):
            return "[consensus skill — stub: would aggregate sources]"
        else:
            logger.warning("Unknown skill: %s", skill_name)
            return None

    # ------------------------------------------------------------------
    # Transform step
    # ------------------------------------------------------------------

    def _execute_transform(
        self, step: StepDefinition, context: Dict[str, Any]
    ) -> str:
        """执行纯文本变换"""
        return self._render_template(step.template, context)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_template(template: str, context: Dict[str, Any]) -> str:
        """Simple {key} template rendering with fallback to empty string."""
        if not template:
            return ""

        result = template
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                # Convert value to string for display
                if isinstance(value, (dict, list)):
                    val_str = json.dumps(value, ensure_ascii=False, indent=2)
                elif value is None:
                    val_str = ""
                else:
                    val_str = str(value)
                result = result.replace(placeholder, val_str)

        return result
