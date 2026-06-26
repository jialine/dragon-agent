"""
Step Executors — 标准步骤类型的执行逻辑。

五种标准步骤类型:
  - llm_call:       调用 LLM（通过 dispatcher）
  - tool_call:      调用工具（通过 tool_registry）
  - conditional:    条件分支（表达式求值 → 跳转目标 step id）
  - loop:           数组迭代（对每个元素执行子步骤）
  - sub_workflow:   嵌套子工作流（递归执行）

通用模板语法: {step_id.field.subfield}
  - {query}             → context["query"]
  - {step_1.output}     → context["step_1"].output (StepResult 属性)
  - {step_1.result}     → StepResult.output 别名
  - {plan.text}         → context["plan"]["text"]   (dict)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from dragon.workflow import (
    StepDefinition,
    StepResult,
    StepType,
    WorkflowResult,
    WorkflowDefinition,
)

logger = logging.getLogger("dragon.workflow.steps")


# ════════════════════════════════════════════════════════════════════
# Template Rendering
# ════════════════════════════════════════════════════════════════════

# Matches {identifier} or {identifier.field.subfield}, and {{...}} escape
_TEMPLATE_RE = re.compile(r"\{([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\}")
_ESCAPE_RE = re.compile(r"\{\{([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\}\}")


def render_template(template: str, context: Dict[str, Any]) -> str:
    """
    渲染模板字符串。将 {step_id.field} 替换为上下文中的值。
    {{step_id.field}} 转义为 {step_id.field}（不解析，用于表达式）。

    >>> render_template("Hello {name}", {"name": "World"})
    'Hello World'
    """
    if not template or not isinstance(template, str):
        return str(template) if template is not None else ""

    # Step 1: Handle {{...}} escape → {literal}
    template = _ESCAPE_RE.sub(r"{\1}", template)

    # Step 2: Replace {ident.field} with values
    def _replace(match: re.Match) -> str:
        path = match.group(1)
        value = resolve_path(path, context)
        if value is None:
            return f"{{{path}}}"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _TEMPLATE_RE.sub(_replace, template)


def resolve_path(path: str, context: Dict[str, Any]) -> Any:
    """
    从上下文中解析点号分隔的路径。

    Supports:
      - step_id.output  → StepResult 的 output 属性
      - step_id.result  → StepResult.output (别名)
      - step_id.success → StepResult 的 success 属性
      - dict.key.subkey → 嵌套字典取值
    """
    if not path:
        return None

    parts = path.split(".")
    current: Any = context

    for part in parts:
        if current is None:
            return None

        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, StepResult):
            # StepResult 属性访问
            if part in ("output", "result"):
                current = current.output
            elif part == "success":
                current = current.success
            elif part == "error":
                current = current.error
            elif part == "step_id":
                current = current.step_id
            elif part == "skipped":
                current = current.skipped
            else:
                return None
        elif isinstance(current, WorkflowResult):
            if part == "final_output":
                current = current.final_output
            elif part == "outputs":
                current = current.outputs
            elif part == "success":
                current = current.success
            elif part == "error":
                current = current.error
            else:
                return None
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None

    return current


def render_config(config: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """递归渲染配置中的所有模板字符串"""
    result = {}
    for key, value in config.items():
        if isinstance(value, str):
            result[key] = render_template(value, context)
        elif isinstance(value, dict):
            result[key] = render_config(value, context)
        elif isinstance(value, list):
            result[key] = [
                render_template(v, context) if isinstance(v, str)
                else render_config(v, context) if isinstance(v, dict)
                else v
                for v in value
            ]
        else:
            result[key] = value
    return result


# ════════════════════════════════════════════════════════════════════
# Expression Evaluation (for conditional steps)
# ════════════════════════════════════════════════════════════════════

def evaluate_expression(expression: str, context: Dict[str, Any]) -> bool:
    """
    评估条件表达式。支持的格式：

    - "{{step_id.success}}"           → 布尔值
    - "{{step_id.output}} == 'text'"  → 字符串比较
    - "{{plan.count}} > 0"            → 数值比较
    - "{{plan.level}} != 'high'"      → 不等比较
    - "len({{items}}) > 3"            → 长度比较

    每个 {{...}} 会被替换为上下文中的值，然后对整体表达式求值。
    """
    if not expression or not expression.strip():
        return True

    expr = expression.strip()

    # Simple boolean path: just "{step_id.success}"
    simple_match = re.match(r"^\{([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\}$", expr)
    if simple_match:
        val = resolve_path(simple_match.group(1), context)
        return bool(val)

    # Expression with comparison: replace {path} with values
    def _replace_val(m: re.Match) -> str:
        path = m.group(1)
        val = resolve_path(path, context)
        if val is None:
            return "None"
        if isinstance(val, bool):
            return "True" if val else "False"
        if isinstance(val, str):
            return json.dumps(val)
        return str(val)

    rendered = re.sub(r"\{([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\}", _replace_val, expr)

    # Evaluate with safe builtins only
    allowed_names = {
        "True": True, "False": False, "None": None,
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict,
    }
    try:
        result = eval(rendered, {"__builtins__": {}}, allowed_names)
        return bool(result)
    except Exception:
        logger.warning("Cannot evaluate expression: %r (rendered: %r)", expression, rendered)
        return True  # default to run


# ════════════════════════════════════════════════════════════════════
# Step Executors
# ════════════════════════════════════════════════════════════════════

async def execute_llm_call(
    step: StepDefinition,
    context: Dict[str, Any],
) -> str:
    """
    执行 LLM 调用步骤。

    config:
      prompt:     提示词模板（必填）
      system:     系统提示词（可选）
      model:      模型名称（可选）
      temperature: 温度参数（可选）
      max_tokens:  最大 token 数（可选）
    """
    config = render_config(step.config, context)

    prompt = config.get("prompt", "")
    if not prompt:
        raise ValueError(f"llm_call step '{step.id}' requires 'prompt' in config")

    dispatcher = context.get("_dispatcher")
    if dispatcher is None:
        raise RuntimeError(
            f"llm_call step '{step.id}' requires a dispatcher. "
            "Set engine.dispatcher or pass _dispatcher in context."
        )

    messages = []
    system_prompt = config.get("system", "")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs: Dict[str, Any] = {"messages": messages, "stream": False}
    if config.get("model"):
        kwargs["model"] = config["model"]
    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]
    if config.get("max_tokens") is not None:
        kwargs["max_tokens"] = config["max_tokens"]

    logger.debug("LLM step '%s': prompt=%r...", step.id, prompt[:100])
    dispatch_result = await dispatcher.dispatch(**kwargs)

    # Extract content from dispatch result
    if hasattr(dispatch_result, "content"):
        content = dispatch_result.content
    elif isinstance(dispatch_result, dict):
        content = dispatch_result.get("content", str(dispatch_result))
    else:
        content = str(dispatch_result)

    return content or ""


async def execute_tool_call(
    step: StepDefinition,
    context: Dict[str, Any],
) -> Any:
    """
    执行工具调用步骤。

    config:
      tool:       工具名称（必填）
      input:      输入参数模板
      params:     额外参数字典
      timeout:    超时秒数
    """
    config = render_config(step.config, context)

    tool_name = config.get("tool", "")
    if not tool_name:
        raise ValueError(f"tool_call step '{step.id}' requires 'tool' in config")

    tool_registry = context.get("_tool_registry")
    tool_input = config.get("input", "")

    # Try tool_registry first
    if tool_registry is not None:
        logger.debug("Tool step '%s': calling '%s' via registry", step.id, tool_name)

        kwargs = dict(config.get("params", {}))
        if tool_input:
            kwargs["input"] = tool_input

        if hasattr(tool_registry, "call"):
            result = await tool_registry.call(tool_name, **kwargs)
        elif hasattr(tool_registry, "execute"):
            result = await tool_registry.execute(tool_name, **kwargs)
        else:
            raise RuntimeError("Tool registry has no 'call' or 'execute' method")

        return result

    # Fallback: use known built-in tools
    logger.debug("Tool step '%s': falling back to built-in '%s'", step.id, tool_name)
    return await _call_builtin_tool(tool_name, tool_input, config)


async def _call_builtin_tool(tool_name: str, input_text: str, config: Dict[str, Any]) -> Any:
    """Fallback tool call for known built-in Dragon tools."""
    try:
        if tool_name == "web_search":
            from dragon.web_search import web_search
            result = await web_search(input_text)
            if hasattr(result, "results"):
                return result.results
            return str(result)
    except ImportError:
        pass

    logger.warning("Unknown/built-in tool '%s' — returning stub", tool_name)
    return f"[tool:{tool_name}] executed with input: {input_text[:200]}"


async def execute_conditional(
    step: StepDefinition,
    context: Dict[str, Any],
) -> Optional[str]:
    """
    执行条件分支步骤。

    config:
      expression:  条件表达式，如 "{s1.success} == True"
      then:        条件为 True 时跳转的目标 step id
      else:        条件为 False 时跳转的目标 step id
      branches:    多路分支: [{"if": "{plan.level} == 'high'", "goto": "id"}, ...]
      default:     默认跳转（没有匹配分支时）

    Returns:
        目标 step id (字符串)，由 WorkflowEngine 处理跳转。

    注意：表达式中的 {path} 引用由 evaluate_expression 直接解析，
    不需要经过 render_template 预渲染（避免字符串未加引号的问题）。
    """
    config = step.config  # Raw config — do NOT pre-render expressions

    # Multi-branch mode
    branches = config.get("branches", [])
    if branches:
        for branch in branches:
            expr = branch.get("if", "")
            if evaluate_expression(expr, context):
                target = branch.get("goto", branch.get("then", ""))
                logger.debug("Conditional '%s': branch matched -> '%s'", step.id, target)
                return target
        default = config.get("default", "")
        logger.debug("Conditional '%s': no branch matched -> default '%s'", step.id, default)
        return default

    # Simple if/else mode
    expression = config.get("expression", "")
    if expression:
        result = evaluate_expression(expression, context)
    else:
        result = True

    if result:
        target = config.get("then", "")
    else:
        target = config.get("else", "")

    logger.debug(
        "Conditional '%s': expression=%r → %s → '%s'",
        step.id, expression, result, target,
    )
    return target


async def execute_loop(
    step: StepDefinition,
    context: Dict[str, Any],
) -> List[Any]:
    """
    执行循环步骤。

    config:
      array:      待迭代数组的模板路径，如 "{search.results}" 或直接 [1,2,3]
      item_key:   当前元素在子步骤中的 key 名称（默认 "item"）
      index_key:  当前索引的 key（默认 "index"）
      sub_steps:  对每个元素执行的子步骤列表
      max_iterations: 最大迭代次数（默认 100）

    Returns:
        所有迭代结果的列表 [result_1, result_2, ...]
    """
    config = render_config(step.config, context)

    # Resolve the array
    array_raw = config.get("array", [])
    if isinstance(array_raw, str):
        # Try resolving as a context path
        resolved = resolve_path(array_raw, context)
        if resolved is not None and isinstance(resolved, list):
            items = resolved
        else:
            try:
                items = json.loads(array_raw)
            except (json.JSONDecodeError, TypeError):
                items = []
    elif isinstance(array_raw, list):
        items = array_raw
    else:
        items = []

    if not items:
        logger.debug("Loop '%s': empty array, skipping", step.id)
        return []

    item_key = config.get("item_key", "item")
    index_key = config.get("index_key", "index")
    max_iterations = config.get("max_iterations", 100)
    sub_steps_raw = config.get("sub_steps", [])

    results = []
    items = items[:max_iterations]

    logger.debug("Loop '%s': iterating %d items", step.id, len(items))

    engine = context.get("_engine")

    for idx, item in enumerate(items):
        sub_context = dict(context)
        sub_context[item_key] = item
        sub_context[index_key] = idx

        if engine is not None and hasattr(engine, "_execute_step"):
            # Execute sub_steps using the engine
            sub_wf = WorkflowDefinition(
                name=f"{step.id}[{idx}]",
                steps=[
                    StepDefinition(
                        id=s.get("id", f"{step.id}_sub_{i}"),
                        type=StepType(s.get("type", "llm_call")),
                        config=s.get("config", {}),
                    )
                    for i, s in enumerate(sub_steps_raw)
                ],
            )
            sub_result = await engine.run(sub_wf, context=sub_context)
            results.append(sub_result.final_output)
        else:
            # Fallback: execute sub_steps sequentially
            for sub_step_raw in sub_steps_raw:
                sub_step = StepDefinition(
                    id=sub_step_raw.get("id", f"{step.id}_sub_{idx}"),
                    type=StepType(sub_step_raw.get("type", "llm_call")),
                    config=sub_step_raw.get("config", {}),
                )
                if sub_step.type == StepType.LLM_CALL:
                    output = await execute_llm_call(sub_step, sub_context)
                elif sub_step.type == StepType.TOOL_CALL:
                    output = await execute_tool_call(sub_step, sub_context)
                else:
                    continue
                sub_context[sub_step.id] = StepResult(
                    step_id=sub_step.id,
                    step_type=sub_step.type,
                    output=output,
                )
            results.append(sub_context.get("_loop_result", item))

    return results


async def execute_sub_workflow(
    step: StepDefinition,
    context: Dict[str, Any],
) -> Any:
    """
    执行嵌套子工作流。

    config:
      workflow:   子工作流名称（YAML 文件名，不含 .yaml）或内联定义
      input:      传递给子工作流的输入映射 {"key": "{template}"}
      inherit_context: 是否继承父上下文（默认 true）

    Returns:
        子工作流的 final_output
    """
    config = render_config(step.config, context)

    engine = context.get("_engine")
    if engine is None:
        raise RuntimeError("sub_workflow step requires _engine in context")

    # Build sub-workflow context
    inherit = config.get("inherit_context", True)
    sub_context: Dict[str, Any] = {}
    if inherit:
        sub_context = {k: v for k, v in context.items() if not k.startswith("_")}

    # Apply input mapping
    input_map = config.get("input", {})
    if isinstance(input_map, dict):
        for key, value in input_map.items():
            sub_context[key] = value
    elif isinstance(input_map, str):
        sub_context["input"] = input_map

    workflow_ref = config.get("workflow", "")
    if not workflow_ref:
        raise ValueError(f"sub_workflow step '{step.id}' requires 'workflow' in config")

    # Load sub-workflow
    if isinstance(workflow_ref, dict):
        sub_wf = engine.parse(workflow_ref)
    else:
        sub_wf = engine.load(workflow_ref)

    logger.debug("Sub-workflow '%s': invoking '%s'", step.id, sub_wf.name)
    result = await engine.run(sub_wf, context=sub_context)
    return result.final_output


# ════════════════════════════════════════════════════════════════════
# StepExecutor (compatibility shim)
# ════════════════════════════════════════════════════════════════════

class StepExecutor:
    """
    步骤执行器门面 — 根据 StepType 分发到对应的执行函数。
    主要用于向后兼容和统一入口场景。
    """

    async def execute(
        self,
        step: StepDefinition,
        context: Dict[str, Any],
    ) -> StepResult:
        """执行一个步骤并返回 StepResult。"""
        t0 = time.perf_counter()

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
