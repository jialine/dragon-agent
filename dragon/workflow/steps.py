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
        prompt = self._render_template(step.config.get("prompt", ""), context)

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
        if step.config.get("tools_from"):
            # Dynamic: read from plan output
            plan = context.get("plan", {})
            tools = plan.get(step.config["tools_from"], [])
            if isinstance(tools, list):
                tool_names = tools
            elif isinstance(tools, str):
                tool_names = [tools]
        elif step.config.get("tool"):
            tool_names = [step.config["tool"]]

        if not tool_names:
            logger.warning("Tool step '%s': no tools selected", step.id)
            return None

        # Resolve input
        if step.config.get("input_from"):
            query = str(context.get(step.config["input_from"], ""))
        elif step.config.get("input"):
            query = self._render_template(step.config["input"], context)
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
        if tool_name == "web_search":
            try:
                from dragon.web_search import web_search
                result = await web_search(query)
                return result.results if hasattr(result, 'results') else str(result)
            except ImportError:
                return f"[web_search not available] query: {query}"
        elif tool_name == "vision":
            return "[vision tool — stub]"
        elif tool_name == "maps":
            return "[maps tool — stub]"
        elif tool_name == "comfyui_generate":
            return await self._comfyui_generate(query)
        elif tool_name == "edge_tts":
            return await self._edge_tts(query)
        elif tool_name == "ffmpeg_composite":
            return await self._ffmpeg_composite(query)
        else:
            logger.warning("Unknown tool: %s", tool_name)
            return None

    async def _comfyui_generate(self, query: str) -> Any:
        """调用 ComfyUI API 生成图像/视频"""
        import aiohttp
        import json as _json
        import uuid

        comfyui_host = "http://192.168.0.30:8188"

        # Parse query as JSON: {"workflow": "...", "prompt": "...", "negative": "..."}
        try:
            params = _json.loads(query) if isinstance(query, str) and query.startswith("{") else {"prompt": query}
        except _json.JSONDecodeError:
            params = {"prompt": query}

        prompt_text = params.get("prompt", query)
        negative = params.get("negative", "low quality, blurry")
        workflow_name = params.get("workflow", "sd15_txt2img")

        # Build a simple SD1.5/SDXL workflow
        workflow = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": params.get("seed", -1),
                    "steps": params.get("steps", 20),
                    "cfg": params.get("cfg", 7.5),
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                }
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": params.get("model", "v1-5-pruned-emaonly.safetensors")}
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": params.get("width", 512), "height": params.get("height", 512), "batch_size": 1}
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt_text, "clip": ["4", 1]}
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["4", 1]}
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["3", 0], "vae": ["4", 2]}
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": f"drama_{uuid.uuid4().hex[:8]}", "images": ["8", 0]}
            }
        }

        client_id = str(uuid.uuid4())
        async with aiohttp.ClientSession() as session:
            # Submit workflow
            async with session.post(
                f"{comfyui_host}/api/prompt",
                json={"prompt": workflow, "client_id": client_id}
            ) as resp:
                if resp.status != 200:
                    return f"[ComfyUI error: HTTP {resp.status}]"
                result = await resp.json()
                prompt_id = result.get("prompt_id")

            # Poll for completion (via WebSocket or polling)
            import asyncio
            for _ in range(60):  # 60 * 5s = 5 min timeout
                await asyncio.sleep(5)
                async with session.get(f"{comfyui_host}/api/history/{prompt_id}") as hr:
                    if hr.status == 200:
                        history = await hr.json()
                        if prompt_id in history:
                            outputs = history[prompt_id].get("outputs", {})
                            images = []
                            for node_id, node_output in outputs.items():
                                for img in node_output.get("images", []):
                                    images.append(f"{comfyui_host}/api/view?filename={img['filename']}&type=output")
                            if images:
                                return {"prompt_id": prompt_id, "images": images, "total": len(images)}
            return {"prompt_id": prompt_id, "images": [], "status": "timeout"}

    async def _edge_tts(self, query: str) -> Any:
        """调用 Edge TTS 生成配音"""
        import subprocess as _sp
        import tempfile
        import os

        # query format: {"text": "...", "voice": "...", "output": "..."}
        import json as _json
        try:
            params = _json.loads(query) if isinstance(query, str) and query.startswith("{") else {"text": query}
        except _json.JSONDecodeError:
            params = {"text": query}

        text = params.get("text", query)
        voice = params.get("voice", "zh-CN-XiaoxiaoNeural")
        output_file = params.get("output", os.path.join(tempfile.gettempdir(), f"tts_output.mp3"))

        try:
            result = _sp.run(
                ["edge-tts", "--text", text, "--voice", voice, "--write-media", output_file],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return {"file": output_file, "status": "ok"}
            else:
                return {"error": result.stderr, "status": "failed"}
        except FileNotFoundError:
            return {"error": "edge-tts not installed. Run: pip install edge-tts", "status": "not_available"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    async def _ffmpeg_composite(self, query: str) -> Any:
        """调用 FFmpeg 合成视频"""
        import subprocess as _sp
        import tempfile
        import os
        import json as _json

        try:
            params = _json.loads(query) if isinstance(query, str) and query.startswith("{") else {}
        except _json.JSONDecodeError:
            params = {}

        input_files = params.get("files", [])
        audio_file = params.get("audio")
        output_file = params.get("output", os.path.join(tempfile.gettempdir(), "composite_output.mp4"))

        cmd = ["ffmpeg", "-y"]
        if input_files:
            # Concatenate video files
            concat_list = os.path.join(tempfile.gettempdir(), "concat.txt")
            with open(concat_list, "w") as f:
                for vid in input_files:
                    f.write(f"file '{vid}'\n")
            cmd += ["-f", "concat", "-safe", "0", "-i", concat_list]
        if audio_file:
            cmd += ["-i", audio_file]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_file]

        try:
            result = _sp.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return {"file": output_file, "status": "ok"}
            else:
                return {"error": result.stderr[:200], "status": "failed"}
        except FileNotFoundError:
            return {"error": "ffmpeg not installed", "status": "not_available"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

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
        """Render {key} and {nested.key.subkey} templates from context."""
        if not template:
            return ""

        import re

        def _resolve(key_path: str) -> str:
            parts = key_path.split(".")
            current = context
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                elif hasattr(current, part):
                    current = getattr(current, part)
                else:
                    return ""  # Not found
                if current is None:
                    return ""
            # Convert to string
            if isinstance(current, (dict, list)):
                return json.dumps(current, ensure_ascii=False, indent=2)
            return str(current)

        return re.sub(
            r"\{([a-zA-Z_][\w.]*)\}",
            lambda m: _resolve(m.group(1)),
            template,
        )


# ════════════════════════════════════════════════════════════════════
# Compatibility wrappers for engine.py (standalone function API)
# ════════════════════════════════════════════════════════════════════

def render_template(template: str, context: Dict[str, Any]) -> str:
    """Render a Jinja2-style template string using context vars."""
    return StepExecutor._render_template(template, context)


def evaluate_expression(expr: str, context: Dict[str, Any]) -> bool:
    """Evaluate a simple boolean expression (truthy check)."""
    val = render_template(expr, context)
    if not val:
        return False
    return val.lower() not in ("false", "0", "no", "none", "")


async def execute_llm_call(step, context: Dict[str, Any]) -> str:
    """Execute an LLM step via StepExecutor."""
    executor = StepExecutor()
    return await executor._execute_llm(step, context)


async def execute_tool_call(step, context: Dict[str, Any]) -> Any:
    """Execute a tool step via StepExecutor."""
    executor = StepExecutor()
    return await executor._execute_tool(step, context)


async def execute_conditional(step, context: Dict[str, Any]) -> Any:
    """Execute a conditional step (stub — delegates to LLM)."""
    executor = StepExecutor()
    return await executor._execute_llm(step, context)


async def execute_loop(step, context: Dict[str, Any]) -> Any:
    """Execute a loop step (stub — delegates to LLM)."""
    executor = StepExecutor()
    return await executor._execute_llm(step, context)


async def execute_sub_workflow(step, context: Dict[str, Any]) -> Any:
    """Execute a sub-workflow step (stub — delegates to LLM)."""
    executor = StepExecutor()
    return await executor._execute_llm(step, context)
