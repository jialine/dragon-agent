"""
Dragon Agent — Tool System

1. **Auto-Discovery** — decorator-based registration
2. **Semantic Search** — find tools by intent
3. **Tool Pipelines** — compose with retry, timeout, chaining
4. **Circuit Breaker** — prevent cascading failures
5. **Guardrails** — pre/post-execution safety checks and output filtering
"""
from dragon.tool.registry import (
    ToolRegistry, ToolPipeline, PipelineToolStep,
    ToolDef, ToolResult, ToolOutcome, CircuitState, CircuitBreaker,
)
from dragon.tool.guardrails import (
    ToolGuardrails, GuardrailConfig, GuardrailAction, GuardrailCheck,
    CheckType, classify_tool_failure,
)

__all__ = [
    "ToolRegistry", "ToolPipeline", "PipelineToolStep",
    "ToolDef", "ToolResult", "ToolOutcome",
    "CircuitState", "CircuitBreaker",
    "ToolGuardrails", "GuardrailConfig", "GuardrailAction",
    "GuardrailCheck", "CheckType", "classify_tool_failure",
]
