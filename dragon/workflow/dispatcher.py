"""
Intent-Driven Workflow Dispatcher
==================================
根据用户消息意图自动选择或创建执行工作流。

核心流程：
1. 扫描 workflows/ 目录，建立工作流目录
2. 用 LLM 将用户意图匹配到最佳现有工作流
3. 如果没有合适的工作流，自动生成一个新的
4. 返回 WorkflowDefinition 和填充好的 context

用法：
    from dragon.workflow.dispatcher import WorkflowDispatcher
    dispatcher = WorkflowDispatcher()
    wf_def, context = await dispatcher.dispatch("帮我研究一下量子计算")
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from dragon.workflow import WorkflowDefinition

logger = logging.getLogger("dragon.workflow.dispatcher")

# ════════════════════════════════════════════════════════════════════
# Auxiliary types
# ════════════════════════════════════════════════════════════════════


@dataclass
class WorkflowEntry:
    """工作流目录条目"""
    name: str
    filename: str          # e.g. "research.yaml"
    description: str
    step_count: int
    step_types: List[str]


# ════════════════════════════════════════════════════════════════════
# Intent classification prompt — compact so it doesn't burn tokens
# ════════════════════════════════════════════════════════════════════

INTENT_CLASSIFY_PROMPT = """你是一个任务路由器。分析用户意图，判断应该用哪个工作流。

## 可用的工作流

{workflow_catalog}

## 任务

用户说："{user_message}"

分析后输出 JSON（不要其他内容）：
{{
  "match": "best_workflow_filename" 或 null（无匹配）,
  "confidence": 0.0 到 1.0,
  "reasoning": "简短理由",
  "suggested_context": {{"key": "value"}} 
}}

匹配规则：
- match 用上面的文件名（如 "research.yaml"），不要用显示名
- 如果用户意图明确匹配某个工作流，confidence ≥ 0.7
- 如果用户意图模糊或跨多个工作流，confidence < 0.6 且 match 选最接近的
- 如果和任何工作流都不相关，match=null
- suggested_context 填工作流需要的初始参数（如 research 填 query）
"""

AUTO_CREATE_PROMPT = """你是一个工作流设计师。用户没有匹配到合适的工作流，需要你为他自动生成一个。

## 用户需要

"{user_message}"

## 可用步骤类型

- llm_call: 调用大语言模型（需要 system+prompt+温度参数）
- tool_call: 调用工具（需要 tool 名称+input）
- conditional: 条件分支（需要 expression+then+else）
- loop: 循环执行（需要 items+step_template）

## 要求

设计一个精简的工作流 YAML，直接解决问题。输出 JSON（不要其他内容）：

{{
  "name": "工作流名称",
  "description": "一句话描述",
  "context": {{"key": "value"}},
  "yaml": "完整的 YAML 文本（注意用 \\n 换行）"
}}

YAML 模板参考：
---
name: {name}
description: ...
steps:
  - id: step_1
    type: llm_call
    config:
      system: "你是..."
      prompt: "用户问题：{{query}}"
      temperature: 0.5
      max_tokens: 2000
  - id: step_2
    type: tool_call
    config:
      tool: web_search
      input: "{{step_1.output}}"
---

要求：
- 步骤不要超过 8 个，精简为主
- prompt 中用 {{变量名}} 引用前序步骤的输出
- 不要设计循环或条件分支除非确实需要
- 用中文
"""


# ════════════════════════════════════════════════════════════════════
# WorkflowDispatcher
# ════════════════════════════════════════════════════════════════════


class WorkflowDispatcher:
    """意图驱动的自动工作流分发器。"""

    def __init__(
        self,
        workflows_dir: str = "workflows",
        provider_fn=None,
    ):
        """
        Args:
            workflows_dir: 工作流 YAML 文件的目录
            provider_fn: async callable(history) -> str, 用于 LLM 分类调用。
                         如果为 None，使用 _default_llm_call。
        """
        self._dir = Path(workflows_dir)
        self._catalog: List[WorkflowEntry] = []
        self._catalog_text: str = ""
        self._provider_fn = provider_fn
        self._last_scan: float = 0
        self._scan_interval: float = 30  # 每 30 秒重新扫描

    # ── Catalog management ──────────────────────────────────────────

    def _scan_catalog(self, force: bool = False) -> None:
        """扫描 workflows/ 目录，建立/刷新工作流目录。"""
        now = time.monotonic()
        if not force and (now - self._last_scan) < self._scan_interval:
            return

        entries: List[WorkflowEntry] = []
        for pattern in ("*.yaml", "*.yml"):
            for fpath in sorted(self._dir.glob(pattern)):
                try:
                    raw = yaml.safe_load(fpath.read_text(encoding="utf-8")) or {}
                    name = raw.get("name", fpath.stem)
                    desc = raw.get("description", "(无描述)")
                    steps = raw.get("steps", [])
                    step_types = [s.get("type", "llm_call") for s in steps]
                    entries.append(WorkflowEntry(
                        name=name,
                        filename=fpath.name,
                        description=desc,
                        step_count=len(steps),
                        step_types=step_types,
                    ))
                except Exception:
                    pass

        self._catalog = entries
        self._last_scan = now

        # Build compact catalog text for the prompt
        lines = []
        for e in entries:
            lines.append(
                f"- `{e.filename}` | {e.name} | {e.step_count}步 | {e.description}"
            )
        self._catalog_text = "\n".join(lines) if lines else "（无可用工作流）"
        logger.info("Workflow catalog scanned: %d entries", len(entries))

    def get_catalog_text(self) -> str:
        self._scan_catalog()
        return self._catalog_text

    def get_entry(self, filename: str) -> Optional[WorkflowEntry]:
        self._scan_catalog()
        for e in self._catalog:
            if e.filename == filename:
                return e
        return None

    # ── Dispatch ────────────────────────────────────────────────────

    async def dispatch(
        self,
        user_message: str,
    ) -> Tuple[Optional[WorkflowDefinition], Dict[str, Any], str]:
        """根据用户消息分发工作流。

        Returns:
            (workflow_definition, context, source)
            source: "matched" | "auto_created" | "fallback"
            如果无法确定，返回 (None, {}, "fallback")。
        """
        self._scan_catalog()

        # ── Step 1: 意图分类 ──
        classify_result = await self._classify_intent(user_message)

        match_file = classify_result.get("match")
        confidence = classify_result.get("confidence", 0)
        context = classify_result.get("suggested_context", {})

        logger.info(
            "Intent dispatch: match=%s confidence=%.2f catalog=%d",
            match_file, confidence, len(self._catalog),
        )

        # ── Step 2: 匹配到现有工作流 ──
        if match_file and confidence >= 0.6:
            entry = self.get_entry(match_file)
            if entry:
                wf_path = self._dir / entry.filename
                if wf_path.exists():
                    try:
                        wf_def = WorkflowDefinition.from_yaml(str(wf_path))
                        return wf_def, context, "matched"
                    except Exception as exc:
                        logger.error("Failed to load workflow %s: %s", wf_path, exc)

        # ── Step 3: 自动创建新工作流 ──
        if confidence < 0.6 or not match_file:
            try:
                wf_def, context = await self._auto_create(user_message)
                if wf_def:
                    return wf_def, context, "auto_created"
            except Exception as exc:
                logger.error("Auto-create workflow failed: %s", exc)

        return None, {}, "fallback"

    # ── LLM helpers ─────────────────────────────────────────────────

    async def _classify_intent(self, user_message: str) -> Dict[str, Any]:
        """用 LLM 分类用户意图到工作流目录。"""
        prompt = INTENT_CLASSIFY_PROMPT.format(
            workflow_catalog=self._catalog_text or "（无可用工作流）",
            user_message=user_message,
        )
        raw = await self._call_llm(prompt, max_tokens=300, temperature=0.1)
        return self._parse_json(raw, default={
            "match": None, "confidence": 0, "reasoning": "parse error",
            "suggested_context": {},
        })

    async def _auto_create(
        self, user_message: str
    ) -> Tuple[Optional[WorkflowDefinition], Dict[str, Any]]:
        """自动生成一个新的工作流定义。"""
        prompt = AUTO_CREATE_PROMPT.format(user_message=user_message)
        raw = await self._call_llm(prompt, max_tokens=1500, temperature=0.3)
        data = self._parse_json(raw, default=None)
        if not data:
            return None, {}

        yaml_text = data.get("yaml", "")
        context = data.get("context", {})

        if not yaml_text:
            logger.warning("Auto-created workflow has empty YAML")
            return None, {}

        try:
            parsed = yaml.safe_load(yaml_text) or {}
            wf_def = WorkflowDefinition.from_dict(parsed)
            logger.info(
                "Auto-created workflow: %s (%d steps)",
                data.get("name", "unknown"), len(wf_def.steps),
            )
            return wf_def, context
        except Exception as exc:
            logger.error("Failed to parse auto-created YAML: %s", exc)
            return None, {}

    async def _call_llm(
        self, prompt: str, max_tokens: int = 300, temperature: float = 0.1,
    ) -> str:
        """调用 LLM，优先用注入的 provider_fn，否则用默认。"""
        if self._provider_fn:
            history = [{"role": "user", "content": prompt}]
            return await self._provider_fn(history)

        # Default fallback: try imported provider
        return await _default_llm_call(prompt, max_tokens, temperature)

    @staticmethod
    def _parse_json(raw: str, default: Any) -> Any:
        """从 LLM 输出中提取 JSON。"""
        # Find the outermost { } block
        raw = raw.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                pass
        return default


# ════════════════════════════════════════════════════════════════════
# Default LLM call (uses dispatch provider)
# ════════════════════════════════════════════════════════════════════


async def _default_llm_call(prompt: str, max_tokens: int, temperature: float) -> str:
    """默认 LLM 调用 — 从 provider_registry 获取当前 provider。"""
    try:
        from dragon.provider.registry import provider_registry

        provider_name = provider_registry.available_providers()[0]
        provider = provider_registry.get(provider_name)

        if hasattr(provider, "chat") and callable(provider.chat):
            response = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.get("content", "")
        elif hasattr(provider, "complete"):
            response = await provider.complete(
                prompt, max_tokens=max_tokens, temperature=temperature,
            )
            return response
    except Exception as exc:
        logger.error("Default LLM call failed: %s", exc)

    return ""
