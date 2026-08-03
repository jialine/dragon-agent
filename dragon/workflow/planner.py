"""
Plan executor — 每个工作流的第一步。

调用 LLM 分析任务，从行业工具箱中选择合适的 tool/skill，
输出执行方案，供后续步骤按 condition 动态组装。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

from . import PlanConfig, StepResult, Toolbox, WorkflowCallbacks

logger = logging.getLogger("dragon.workflow.planner")

# Regex to extract JSON from LLM output (handles markdown fences)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def execute_plan(
    plan_config: PlanConfig,
    toolbox: Toolbox,
    query: str,
    route_result: Any,
    callbacks: WorkflowCallbacks,
    dispatcher: Any = None,
) -> tuple[Dict[str, Any], float]:
    """
    执行 plan 步骤：让 LLM 制定执行方案。

    Args:
        plan_config:   Plan prompt 模板
        toolbox:       行业可用工具池
        query:         用户原始查询
        route_result:  Router 分类结果（含 difficulty 等）
        callbacks:     进度回调
        dispatcher:    LLM 调度器（可选）

    Returns:
        (plan_dict, elapsed_ms)
    """
    await callbacks.on_plan_start()
    t0 = time.perf_counter()

    # Build prompt
    available_tools = ", ".join(toolbox.tools) if toolbox.tools else "无"
    available_skills = ", ".join(toolbox.skills) if toolbox.skills else "无"

    # Extract route info
    difficulty = getattr(route_result, "difficulty", "simple")
    difficulty_score = getattr(route_result, "difficulty_score", 0)
    industry = getattr(route_result, "industry", "general")

    prompt = plan_config.prompt.format(
        available_tools=available_tools,
        available_skills=available_skills,
        query=query,
        difficulty=difficulty,
        difficulty_score=difficulty_score,
        industry=industry,
    )

    logger.info("Plan step: query=%r, industry=%s, difficulty=%s", query[:80], industry, difficulty)
    logger.debug("Plan prompt: %s", prompt[:500])

    # Call LLM
    llm_output = await _call_llm(prompt, dispatcher, route_result)
    logger.debug("Plan raw output: %s", llm_output[:500])

    # Parse JSON from output
    plan = _parse_plan_json(llm_output)

    if plan is None:
        # Fallback: minimal plan
        logger.warning("Failed to parse plan JSON, using fallback")
        plan = {
            "approach": f"直接回答{industry}相关问题",
            "need_search": False,
            "need_debate": False,
            "need_fact_check": False,
            "selected_tools": [],
            "selected_skills": [],
            "sub_questions": [],
            "risk_level": "low",
        }

    elapsed = (time.perf_counter() - t0) * 1000
    await callbacks.on_plan_complete(plan)

    return plan, elapsed


async def _call_llm(prompt: str, dispatcher: Any, route_result: Any) -> str:
    """调用 LLM 获取 plan 输出"""
    # Use the dispatcher
    if dispatcher is not None:
        try:
            result = await dispatcher.dispatch(
                industry="general",
                messages=[
                    {"role": "system", "content": "你是一个任务方案制定者。只输出JSON，不要其他内容。"},
                    {"role": "user", "content": prompt},
                ],
                stream=False,
            )
            return result.content
        except Exception as exc:
            logger.warning("Dispatcher LLM call failed: %s, trying fallback", exc)

    # Fallback: use OpenAI-compatible API directly
    try:
        import os
        from openai import AsyncOpenAI

        from dragon.constants import API_BASE_URL  # noqa: E402
        base_url = API_BASE_URL
        api_key = os.getenv("DRAGON_API_KEY", "")

        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        response = await client.chat.completions.create(
            model=os.getenv("DRAGON_PLAN_MODEL", ""),
            messages=[
                {"role": "system", "content": "你是一个任务方案制定者。只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=512,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("All LLM call methods failed")
        return "{}"


def _parse_plan_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 plan JSON"""
    if not text:
        return None

    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extract from markdown fences
    match = _JSON_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None
