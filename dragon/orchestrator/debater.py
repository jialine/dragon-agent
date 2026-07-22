"""
Debate Engine — multi-model parallel calls + voting for Tier 3 complex questions.

Flow:
  1. Call 3+ models in parallel with the same prompt
  2. Each model produces its own answer
  3. Synthesizer model compares, finds consensus, produces final answer
  4. Returns final answer + voting verdict
"""

import asyncio
import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("dragon.orchestrator.debater")

SYNTHESIZER_MODEL = "deepseek-v4-pro"  # Best reasoning for synthesis

SYNTHESIS_PROMPT = """你是一个方案评审委员会主席。以下是对同一个问题的{count}个不同模型的回答。

请完成以下任务：
1. 找出所有模型**一致同意**的核心观点
2. 找出明显的**分歧点**
3. 综合各模型优点，给出**最终方案**
4. 标注最终方案的**置信度**（高/中/低）

格式要求：
## 共识点
- ...
## 分歧点
- ...
## 最终方案
...
## 置信度：[高/中/低]

{answers}
"""


class DebateEngine:
    """Multi-model debate + voting engine."""

    def __init__(self, provider_registry: Any):
        self.registry = provider_registry

    async def debate(
        self,
        messages: List[Dict[str, str]],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run multi-model debate and return synthesized result."""
        models = config["models"]
        temperature = config.get("temperature", 0.7)

        # Step 1: Call all models in parallel
        t0 = time.monotonic()
        tasks = []
        for i, m in enumerate(models):
            tasks.append(self._call_model(
                provider=m["provider"],
                model=m["model"],
                messages=messages,
                temperature=temperature,
                max_tokens=m.get("max_tokens", 2048),
                index=i,
            ))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        call_time = time.monotonic() - t0

        # Collect valid responses
        answers: List[Dict[str, str]] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning("Model %s failed: %s", models[i]["model"], r)
                answers.append({
                    "model": models[i]["model"],
                    "content": f"[调用失败: {r}]",
                })
            else:
                answers.append({
                    "model": r["model"],
                    "content": r["content"],
                })

        valid_count = sum(1 for a in answers if not a["content"].startswith("[调用失败"))

        if valid_count == 0:
            raise RuntimeError("All models failed in debate")

        # Step 2: If only 1 valid, return directly
        if valid_count == 1:
            for a in answers:
                if not a["content"].startswith("[调用失败"):
                    return {
                        "final_answer": a["content"],
                        "verdict": "single_model",
                        "models_called": len(models),
                        "models_succeeded": 1,
                        "total_time_s": round(call_time, 1),
                    }

        # Step 3: Synthesize
        answers_text = "\n\n---\n\n".join(
            f"### 模型 {i+1}: {a['model']}\n{a['content']}"
            for i, a in enumerate(answers)
        )

        synthesis_messages = [
            {"role": "system", "content": "你是方案评审专家，擅长综合多种观点给出最优解。"},
            {"role": "user", "content": SYNTHESIS_PROMPT.format(
                count=len(answers),
                answers=answers_text,
            )},
        ]

        try:
            synth_result = await self.registry.call(
                provider_name="openai",
                model=SYNTHESIZER_MODEL,
                messages=synthesis_messages,
                temperature=0.3,
                max_tokens=4096,
            )
        except Exception as e:
            logger.warning("Synthesis failed: %s — using best single answer", e)
            # Fallback: return longest answer
            best = max(
                (a for a in answers if not a["content"].startswith("[调用失败")),
                key=lambda a: len(a["content"]),
                default=answers[0],
            )
            return {
                "final_answer": f"[多模型综合暂不可用，以下为 {best['model']} 的回答]\n\n{best['content']}",
                "verdict": "fallback_single",
                "models_called": len(models),
                "models_succeeded": valid_count,
                "total_time_s": round(call_time, 1),
            }

        return {
            "final_answer": synth_result.content,
            "verdict": "synthesized",
            "models_called": len(models),
            "models_succeeded": valid_count,
            "total_time_s": round(call_time, 1),
        }

    async def _call_model(
        self, provider: str, model: str, messages: List[Dict[str, str]],
        temperature: float, max_tokens: int, index: int,
    ) -> Dict[str, Any]:
        """Call a single model with timing."""
        t0 = time.monotonic()
        result = await self.registry.call(
            provider_name=provider,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "Model %s (#%d) done in %.1fs, %d tokens",
            model, index, elapsed,
            result.usage.get("total_tokens", 0) if result.usage else 0,
        )
        return {"model": model, "content": result.content}
