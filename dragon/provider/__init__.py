"""
Dragon Provider — Unified LLM Provider Abstraction Layer
========================================================

Supports 20 providers through a single async interface:

    - OpenAI (GPT-4o, GPT-4o-mini)
    - Anthropic (Claude Sonnet 4, Claude Haiku)
    - DeepSeek (DeepSeek-V3, DeepSeek-Reasoner)
    - Google (Gemini 2.5 Pro, Gemini 2.5 Flash)
    - xAI (Grok-3)
    - Local (llama.cpp via llama-cpp-python)
    - Together (Llama 4, Mixtral, via Together API)
    - Groq (Llama 4, Mixtral, via Groq LPU)
    - Mistral (Mistral Large, via La Plateforme)
    - Ollama (local models via Ollama serve)
    - OpenRouter (multi-model gateway)
    - Azure OpenAI (Azure-hosted OpenAI models)
    - Cohere (Command R+, via Cohere API)
    - Replicate (Llama 4, via Replicate API)
    - Perplexity (Sonar Pro, via Perplexity API)
    - Fireworks (Llama 4, via Fireworks AI)
    - Cloudflare (Llama 4, via Workers AI)
    - Vertex AI (Gemini, via Google Cloud Vertex AI)
    - Bedrock (Claude/Llama, via AWS Bedrock)
    - Moonshot (Kimi, via Moonshot API)

Usage::

    from dragon.provider import ProviderRegistry, create_openai, create_deepseek

    registry = ProviderRegistry()
    registry.register("openai", create_openai(api_key="sk-..."))
    registry.register("deepseek", create_deepseek(api_key="sk-..."))

    result = await registry.call("openai", "gpt-4o", messages=[...])
    print(result.content)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("dragon.provider")

def _log_400(msg: str):
    """Safely log 400 error details to file (no shell injection)."""
    try:
        with open("/tmp/dragon_400.log", "a") as f:
            f.write(msg.replace("\n", " ") + "\n")
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────
# Data Classes
# ────────────────────────────────────────────────────────────────────

@dataclass
class ProviderResult:
    content: str
    model: str
    provider: str
    usage: Dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    latency_ms: float = 0.0
    raw: Any = None
    tool_calls: Optional[List[Dict]] = None

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)


@dataclass
class StreamChunk:
    content: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    tool_calls: Optional[List[Dict]] = None


@dataclass
class ProviderConfig:
    provider: str
    api_key: str = ""
    api_key_env: str = ""
    base_url: Optional[str] = None
    default_model: str = ""
    api_version: Optional[str] = None
    timeout_secs: float = 120.0
    max_retries: int = 2


# ────────────────────────────────────────────────────────────────────
# Abstract Base Provider
# ────────────────────────────────────────────────────────────────────


class BaseProvider(ABC):
    """Abstract base for all LLM providers."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._api_key = config.api_key or os.getenv(config.api_key_env, "")

    @abstractmethod
    async def complete(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> ProviderResult:
        ...

    @abstractmethod
    async def complete_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        ...

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _build_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


# ────────────────────────────────────────────────────────────────────
# OpenAI Provider
# ────────────────────────────────────────────────────────────────────


class OpenAIProvider(BaseProvider):
    """OpenAI-compatible API provider."""

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        start = time.monotonic()
        url = f"{self.config.base_url or 'https://api.openai.com/v1'}/chat/completions"

        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
                    resp = await client.post(
                        url,
                        headers=self._build_headers(),
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            **kwargs,
                        },
                    )
                    if resp.status_code == 429:
                        wait = min(2 ** attempt, 30)
                        logger.warning("Rate limited, retrying in %ds", wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code >= 400:
                        import json as _json, os as _os
                        body = resp.text[:800]
                        req_body = _json.dumps({"model": model, "messages_count": len(messages), "first_msg": str(messages[0])[:200] if messages else "NONE"})
                        _log_400(f"REQ: {req_body}")
                        _log_400(f"RESP: {body}")
                        try:
                            full = _json.dumps({"model": model, "messages": [{"role": m.get("role","?"), "content": str(m.get("content",""))[:300]} for m in messages[:5]], "tools_count": len(kwargs.get("tools", []))}, ensure_ascii=False)
                            with open("/tmp/dragon_full_req.json", "w") as _df:
                                _df.write(full)
                        except Exception:
                            pass
                    resp.raise_for_status()
                    data = resp.json()

                    choice = data["choices"][0]
                    return ProviderResult(
                        content=choice["message"]["content"] or "",
                        tool_calls=choice["message"].get("tool_calls"),
                        model=data.get("model", model),
                        provider="openai",
                        usage=data.get("usage", {}),
                        finish_reason=choice.get("finish_reason", "stop"),
                        latency_ms=(time.monotonic() - start) * 1000,
                        raw=data,
                    )
            except Exception as e:
                if attempt == self.config.max_retries:
                    raise
                logger.warning("OpenAI attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(1)

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        url = f"{self.config.base_url or 'https://api.openai.com/v1'}/chat/completions"
        async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
            async with client.stream(
                "POST", url,
                headers=self._build_headers(),
                json={
                    "model": model, "messages": messages,
                    "temperature": temperature, "max_tokens": max_tokens,
                    "stream": True, **kwargs,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            chunk_content = delta.get("content", "") or delta.get("reasoning_content", "")
                            yield StreamChunk(
                                content=chunk_content,
                                finish_reason=data["choices"][0].get("finish_reason"),
                            )
                        except json.JSONDecodeError:
                            continue


# ────────────────────────────────────────────────────────────────────
# Anthropic Provider
# ────────────────────────────────────────────────────────────────────


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API provider."""

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        start = time.monotonic()
        url = self.config.base_url or "https://api.anthropic.com/v1/messages"

        # Convert OpenAI format to Anthropic format
        system_msg = ""
        anthropic_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                anthropic_msgs.append({"role": m["role"], "content": m["content"]})

        body = {
            "model": model,
            "messages": anthropic_msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_msg:
            body["system"] = system_msg

        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
                    resp = await client.post(
                        url,
                        headers={
                            "x-api-key": self._api_key,
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                    if resp.status_code >= 400:
                        import json as _json, os as _os
                        body = resp.text[:800]
                        req_body = _json.dumps({"model": model, "messages_count": len(messages), "first_msg": str(messages[0])[:200] if messages else "NONE"})
                        _log_400(f"REQ: {req_body}")
                        _log_400(f"RESP: {body}")
                    resp.raise_for_status()
                    data = resp.json()

                    return ProviderResult(
                        content=data["content"][0]["text"],
                        model=data.get("model", model),
                        provider="anthropic",
                        usage={
                            "prompt_tokens": data["usage"]["input_tokens"],
                            "completion_tokens": data["usage"]["output_tokens"],
                            "total_tokens": data["usage"]["input_tokens"] + data["usage"]["output_tokens"],
                        },
                        finish_reason=data.get("stop_reason", "stop"),
                        latency_ms=(time.monotonic() - start) * 1000,
                        raw=data,
                    )
            except Exception as e:
                if attempt == self.config.max_retries:
                    raise
                await asyncio.sleep(1)

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        yield StreamChunk(content="[Anthropic streaming not yet implemented]")
        yield StreamChunk(finish_reason="stop")


# ────────────────────────────────────────────────────────────────────
# DeepSeek Provider
# ────────────────────────────────────────────────────────────────────


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek API (OpenAI-compatible)."""

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.deepseek.com/v1"
        if not config.default_model:
            config.default_model = "deepseek-chat"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Google Gemini Provider
# ────────────────────────────────────────────────────────────────────


class GoogleProvider(BaseProvider):
    """Google Gemini API provider."""

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        start = time.monotonic()
        url = f"{self.config.base_url or 'https://generativelanguage.googleapis.com/v1beta'}/models/{model}:generateContent"
        url += f"?key={self._api_key}"

        # Convert to Gemini format
        contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

            text = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})

            return ProviderResult(
                content=text,
                model=model,
                provider="google",
                usage={
                    "prompt_tokens": usage.get("promptTokenCount", 0),
                    "completion_tokens": usage.get("candidatesTokenCount", 0),
                    "total_tokens": usage.get("totalTokenCount", 0),
                },
                latency_ms=(time.monotonic() - start) * 1000,
                raw=data,
            )

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        yield StreamChunk(content="[Gemini streaming not yet implemented]")
        yield StreamChunk(finish_reason="stop")


# ────────────────────────────────────────────────────────────────────
# xAI / Grok Provider
# ────────────────────────────────────────────────────────────────────


class XAIProvider(OpenAIProvider):
    """xAI Grok API (OpenAI-compatible)."""

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.x.ai/v1"
        if not config.default_model:
            config.default_model = "grok-3-beta"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Together AI Provider
# ────────────────────────────────────────────────────────────────────


class TogetherProvider(OpenAIProvider):
    """Together AI API (OpenAI-compatible).

    Together hosts many open-source models including Llama 4, Mixtral,
    and DeepSeek variants. Requires TOGETHER_API_KEY env var.

    See: https://docs.together.ai/reference/chat-completions
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.together.xyz/v1"
        if not config.default_model:
            config.default_model = "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Groq Provider
# ────────────────────────────────────────────────────────────────────


class GroqProvider(OpenAIProvider):
    """Groq Cloud API (OpenAI-compatible).

    Groq provides ultra-fast inference via LPU hardware for models
    like Llama 4 and Mixtral. Requires GROQ_API_KEY env var.

    See: https://console.groq.com/docs/api-reference#chat-create
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.groq.com/openai/v1"
        if not config.default_model:
            config.default_model = "llama-4-maverick-17b-128e-instruct"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Mistral Provider
# ────────────────────────────────────────────────────────────────────


class MistralProvider(OpenAIProvider):
    """Mistral AI API (OpenAI-compatible).

    Mistral's La Plateforme offers Mistral Large, Codestral, and
    other models. Requires MISTRAL_API_KEY env var.

    See: https://docs.mistral.ai/api/#tag/chat
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.mistral.ai/v1"
        if not config.default_model:
            config.default_model = "mistral-large-latest"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Ollama Provider
# ────────────────────────────────────────────────────────────────────


class OllamaProvider(OpenAIProvider):
    """Ollama local API (OpenAI-compatible).

    Connects to a locally running Ollama server. Set OLLAMA_HOST
    to override the default endpoint. No API key required.

    See: https://ollama.com/blog/openai-compatibility
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "http://localhost:11434/v1"
        if not config.default_model:
            config.default_model = "llama3"
        super().__init__(config)

    @property
    def available(self) -> bool:
        """Ollama is available if the endpoint is reachable (no API key needed)."""
        return True


# ────────────────────────────────────────────────────────────────────
# Moonshot Provider (月之暗面 / Kimi)
# ────────────────────────────────────────────────────────────────────


class MoonshotProvider(OpenAIProvider):
    """Moonshot AI / Kimi API (OpenAI-compatible).

    月之暗面 (Moonshot AI) — creators of the Kimi chatbot.
    API is fully OpenAI-compatible. Requires MOONSHOT_API_KEY env var.

    See: https://platform.moonshot.cn/docs/api/chat
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.moonshot.cn/v1"
        if not config.default_model:
            config.default_model = "moonshot-v1-8k"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# OpenRouter Provider
# ────────────────────────────────────────────────────────────────────


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter API (OpenAI-compatible).

    OpenRouter provides unified access to hundreds of models from
    various providers. Requires OPENROUTER_API_KEY env var.

    See: https://openrouter.ai/docs/api-reference/chat-completion
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://openrouter.ai/api/v1"
        if not config.default_model:
            config.default_model = "openai/gpt-4o"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Azure OpenAI Provider
# ────────────────────────────────────────────────────────────────────


class AzureOpenAIProvider(BaseProvider):
    """Azure OpenAI Service provider.

    Uses Azure-specific URL pattern and API key header format
    (api-key instead of Authorization: Bearer). Requires both
    AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT env vars.

    The base_url should be set to the Azure OpenAI endpoint, e.g.:
    ``https://{resource}.openai.azure.com``. The deployment name
    is passed as the model parameter.

    See: https://learn.microsoft.com/en-us/azure/ai-services/openai/reference
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._api_version = config.api_version or "2024-10-21"
        self._endpoint = config.base_url or os.getenv("AZURE_OPENAI_ENDPOINT", "")

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._endpoint)

    def _build_headers(self) -> Dict[str, str]:
        return {"api-key": self._api_key, "Content-Type": "application/json"}

    def _build_url(self, deployment: str) -> str:
        endpoint = self._endpoint.rstrip("/")
        return (
            f"{endpoint}/openai/deployments/{deployment}"
            f"/chat/completions?api-version={self._api_version}"
        )

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        start = time.monotonic()
        url = self._build_url(model)

        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
                    resp = await client.post(
                        url,
                        headers=self._build_headers(),
                        json={
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            **kwargs,
                        },
                    )
                    if resp.status_code == 429:
                        wait = min(2 ** attempt, 30)
                        logger.warning("Rate limited, retrying in %ds", wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code >= 400:
                        import json as _json, os as _os
                        body = resp.text[:800]
                        req_body = _json.dumps({"model": model, "messages_count": len(messages), "first_msg": str(messages[0])[:200] if messages else "NONE"})
                        _log_400(f"REQ: {req_body}")
                        _log_400(f"RESP: {body}")
                        try:
                            full = _json.dumps({"model": model, "messages": [{"role": m.get("role","?"), "content": str(m.get("content",""))[:300]} for m in messages[:5]], "tools_count": len(kwargs.get("tools", []))}, ensure_ascii=False)
                            with open("/tmp/dragon_full_req.json", "w") as _df:
                                _df.write(full)
                        except Exception:
                            pass
                    resp.raise_for_status()
                    data = resp.json()

                    choice = data["choices"][0]
                    return ProviderResult(
                        content=choice["message"]["content"] or "",
                        tool_calls=choice["message"].get("tool_calls"),
                        model=data.get("model", model),
                        provider="azure",
                        usage=data.get("usage", {}),
                        finish_reason=choice.get("finish_reason", "stop"),
                        latency_ms=(time.monotonic() - start) * 1000,
                        raw=data,
                    )
            except Exception as e:
                if attempt == self.config.max_retries:
                    raise
                logger.warning("Azure OpenAI attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(1)

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        url = self._build_url(model)
        async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
            async with client.stream(
                "POST", url,
                headers=self._build_headers(),
                json={
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                    **kwargs,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            chunk_content = delta.get("content", "") or delta.get("reasoning_content", "")
                            yield StreamChunk(
                                content=chunk_content,
                                finish_reason=data["choices"][0].get("finish_reason"),
                            )
                        except json.JSONDecodeError:
                            continue


# ────────────────────────────────────────────────────────────────────
# Cohere Provider
# ────────────────────────────────────────────────────────────────────


class CohereProvider(OpenAIProvider):
    """Cohere API (OpenAI-compatible).

    Cohere provides Command R+, Command R, and other models
    through an OpenAI-compatible endpoint. Requires COHERE_API_KEY env var.

    See: https://docs.cohere.com/reference/chat
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.cohere.com/v1"
        if not config.default_model:
            config.default_model = "command-r-plus"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Replicate Provider
# ────────────────────────────────────────────────────────────────────


class ReplicateProvider(OpenAIProvider):
    """Replicate API (OpenAI-compatible).

    Replicate hosts open-source models including Llama 4 Maverick
    and other community models. Requires REPLICATE_API_KEY env var.

    See: https://replicate.com/docs/reference/http
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.replicate.com/v1"
        if not config.default_model:
            config.default_model = "meta/meta-llama-4-maverick"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Perplexity Provider
# ────────────────────────────────────────────────────────────────────


class PerplexityProvider(OpenAIProvider):
    """Perplexity AI API (OpenAI-compatible).

    Perplexity provides the Sonar family of models (Sonar Pro, Sonar)
    with built-in web search capabilities. Requires PERPLEXITY_API_KEY env var.

    See: https://docs.perplexity.ai/api-reference/chat-completions
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.perplexity.ai"
        if not config.default_model:
            config.default_model = "sonar-pro"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Fireworks Provider
# ────────────────────────────────────────────────────────────────────


class FireworksProvider(OpenAIProvider):
    """Fireworks AI API (OpenAI-compatible).

    Fireworks provides fast, serverless inference for open-source models
    including Llama 4 Maverick. Requires FIREWORKS_API_KEY env var.

    See: https://docs.fireworks.ai/api-reference/chat-completions
    """

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            config.base_url = "https://api.fireworks.ai/inference/v1"
        if not config.default_model:
            config.default_model = "accounts/fireworks/models/llama-v4-maverick"
        super().__init__(config)


# ────────────────────────────────────────────────────────────────────
# Cloudflare Workers AI Provider
# ────────────────────────────────────────────────────────────────────


class CloudflareProvider(BaseProvider):
    """Cloudflare Workers AI provider.

    Uses Cloudflare Workers AI REST API to run models like Llama 4 at the edge.
    Requires CLOUDFLARE_API_KEY and CLOUDFLARE_ACCOUNT_ID env vars.

    See: https://developers.cloudflare.com/workers-ai/
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        if not config.base_url:
            config.base_url = f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/run"
        if not config.default_model:
            config.default_model = "@cf/meta/llama-4-maverick"

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._account_id)

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        start = time.monotonic()
        url = f"{self.config.base_url}/{model}" if not self.config.base_url.endswith("/run") else self.config.base_url.replace("/run", f"/{model}")

        payload = {"messages": messages}
        if temperature:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens

        async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            return ProviderResult(
                content=data["result"]["response"],
                model=model,
                provider="cloudflare",
                usage=data.get("result", {}).get("usage", {}),
                latency_ms=(time.monotonic() - start) * 1000,
                raw=data,
            )

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        yield StreamChunk(content="[Cloudflare streaming not yet implemented]")
        yield StreamChunk(finish_reason="stop")


# ────────────────────────────────────────────────────────────────────
# Google Cloud Vertex AI Provider
# ────────────────────────────────────────────────────────────────────


class VertexAIProvider(BaseProvider):
    """Google Cloud Vertex AI provider.

    Uses Vertex AI REST API for Gemini models. Requires VERTEX_PROJECT_ID
    and VERTEX_LOCATION env vars. Authentication via GOOGLE_APPLICATION_CREDENTIALS.

    See: https://cloud.google.com/vertex-ai/docs/reference/rest
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._project_id = os.getenv("VERTEX_PROJECT_ID", "")
        self._location = os.getenv("VERTEX_LOCATION", "us-central1")
        if not config.default_model:
            config.default_model = "gemini-2.5-flash"

    @property
    def available(self) -> bool:
        return bool(self._project_id and self._location)

    def _get_credentials(self):
        """Get Google Cloud credentials via ADC."""
        try:
            import google.auth
            from google.auth.transport.requests import Request as GoogleRequest
        except ImportError:
            raise ImportError(
                "google-auth is required for Vertex AI. Install with: pip install google-auth requests"
            )
        credentials, project = google.auth.default()
        credentials.refresh(GoogleRequest())
        return credentials.token

    def _build_url(self, model: str) -> str:
        return (
            f"https://{self._location}-aiplatform.googleapis.com/v1/"
            f"projects/{self._project_id}/locations/{self._location}/"
            f"publishers/google/models/{model}:generateContent"
        )

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import httpx

        start = time.monotonic()
        token = self._get_credentials()
        url = self._build_url(model)

        # Convert to Gemini format
        contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            elif m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            body["systemInstruction"] = system_instruction

        async with httpx.AsyncClient(verify=False, timeout=self.config.timeout_secs) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

            text = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})

            return ProviderResult(
                content=text,
                model=model,
                provider="vertex",
                usage={
                    "prompt_tokens": usage.get("promptTokenCount", 0),
                    "completion_tokens": usage.get("candidatesTokenCount", 0),
                    "total_tokens": usage.get("totalTokenCount", 0),
                },
                latency_ms=(time.monotonic() - start) * 1000,
                raw=data,
            )

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        yield StreamChunk(content="[Vertex AI streaming not yet implemented]")
        yield StreamChunk(finish_reason="stop")


# ────────────────────────────────────────────────────────────────────
# AWS Bedrock Provider
# ────────────────────────────────────────────────────────────────────


class BedrockProvider(BaseProvider):
    """AWS Bedrock provider.

    Uses the Bedrock Converse API to invoke models like Claude and Llama.
    Requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and AWS_REGION env vars.

    See: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
    """

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._region = os.getenv("AWS_REGION", "us-east-1")
        if not config.default_model:
            config.default_model = "us.anthropic.claude-sonnet-4-20250514-v1:0"

    @property
    def available(self) -> bool:
        return bool(os.getenv("AWS_ACCESS_KEY_ID") and self._region)

    def _get_client(self):
        """Get boto3 Bedrock Runtime client."""
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for AWS Bedrock. Install with: pip install boto3"
            )
        return boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        )

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        import json as json_lib

        start = time.monotonic()

        client = await asyncio.get_event_loop().run_in_executor(
            None, self._get_client
        )

        # Build Converse API messages
        converse_msgs = []
        system_prompts = []
        for m in messages:
            if m["role"] == "system":
                system_prompts.append({"text": m["content"]})
            else:
                converse_msgs.append({
                    "role": m["role"],
                    "content": [{"text": m["content"]}],
                })

        request_body = {
            "modelId": model,
            "messages": converse_msgs,
            "inferenceConfig": {
                "temperature": temperature,
                "maxTokens": max_tokens,
            },
        }
        if system_prompts:
            request_body["system"] = system_prompts

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.converse(**request_body),
        )

        output = response.get("output", {}).get("message", {})
        content = ""
        for part in output.get("content", []):
            if "text" in part:
                content += part["text"]

        usage = response.get("usage", {})

        return ProviderResult(
            content=content,
            model=model,
            provider="bedrock",
            usage={
                "prompt_tokens": usage.get("inputTokens", 0),
                "completion_tokens": usage.get("outputTokens", 0),
                "total_tokens": usage.get("totalTokens", 0),
            },
            latency_ms=(time.monotonic() - start) * 1000,
            raw=response,
        )

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        yield StreamChunk(content="[Bedrock streaming not yet implemented]")
        yield StreamChunk(finish_reason="stop")


# ────────────────────────────────────────────────────────────────────
# Local llama.cpp Provider
# ────────────────────────────────────────────────────────────────────


class LocalProvider(BaseProvider):
    """Local llama.cpp provider via llama-cpp-python."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._model: Any = None
        self._model_path = config.base_url or ""

    @property
    def available(self) -> bool:
        return bool(self._model_path and os.path.exists(self._model_path))

    async def complete(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        start = time.monotonic()

        if self._model is None:
            if not self._model_path:
                raise ValueError("Local provider requires model path in base_url config")
            from llama_cpp import Llama
            self._model = Llama(
                model_path=self._model_path,
                n_ctx=kwargs.get("n_ctx", 4096),
                n_threads=kwargs.get("n_threads", 4),
                verbose=False,
            )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._model.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

        raw_content = result["choices"][0]["message"]["content"]

        import re
        # Strip <think>...</think> blocks (Qwen thinking variants)
        cleaned = re.sub(
            r"<think>.*?</think>\s*",
            "",
            raw_content,
            flags=re.DOTALL,
        ).strip()

        # Fallback: if model only output thinking (no </think>), keep raw
        content = cleaned if cleaned else raw_content

        return ProviderResult(
            content=content,
            model="local",
            provider="local",
            usage={
                "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            },
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def complete_stream(self, model, messages, temperature=0.7, max_tokens=2048, **kwargs):
        result = await self.complete(model, messages, temperature, max_tokens, **kwargs)
        yield StreamChunk(content=result.content)
        yield StreamChunk(finish_reason="stop")


# ────────────────────────────────────────────────────────────────────
# Provider Registry
# ────────────────────────────────────────────────────────────────────


class ProviderRegistry:
    """Registry of LLM providers with fallback and load balancing.

    Usage::

        registry = ProviderRegistry()
        registry.register("openai", OpenAIProvider(ProviderConfig(
            provider="openai", api_key_env="OPENAI_API_KEY",
            default_model="gpt-4o",
        )))
        registry.register("deepseek", DeepSeekProvider(ProviderConfig(
            provider="deepseek", api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
        )))

        result = await registry.call("openai", "gpt-4o", messages=[...])
    """

    def __init__(self, credential_manager: Any = None) -> None:
        self._providers: Dict[str, BaseProvider] = {}
        self._default_provider: Optional[str] = None
        self._fallback_chain: List[str] = []
        # Optional credential pool integration
        self._credential_manager = credential_manager  # CredentialManager | None
        self._current_credentials: Dict[str, Any] = {}  # provider_name -> Credential
        logger.info("ProviderRegistry initialized")

    def register(self, name: str, provider: BaseProvider, default: bool = False) -> None:
        self._providers[name] = provider
        if default or self._default_provider is None:
            self._default_provider = name
        logger.info("Registered provider: %s (available=%s)", name, provider.available)

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)
        if self._default_provider == name:
            self._default_provider = next(iter(self._providers), None)

    def set_fallback_chain(self, chain: List[str]) -> None:
        self._fallback_chain = chain

    def available_providers(self) -> List[str]:
        return [name for name, p in self._providers.items() if p.available]

    async def call(
        self,
        provider_name: str,
        model: str = "",
        messages: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> ProviderResult:
        """Call a provider with automatic fallback."""
        messages = messages or []
        model = model or ""

        provider = self._providers.get(provider_name)
        if provider is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        if not provider.available:
            # Try fallback chain
            for fallback_name in self._fallback_chain:
                fb = self._providers.get(fallback_name)
                if fb and fb.available:
                    logger.info("Falling back to %s (from %s)", fallback_name, provider_name)
                    return await fb.complete(
                        model=model or fb.config.default_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
            raise RuntimeError(f"Provider '{provider_name}' is unavailable and no fallback available")

        # Try primary provider, fall back on error
        last_error = None
        providers_to_try = [provider_name] + [
            fb_name for fb_name in self._fallback_chain
            if fb_name != provider_name and fb_name in self._providers
        ]
        for name in providers_to_try:
            p = self._providers[name]
            if not p.available:
                continue
            try:
                return await p.complete(
                    model=model or p.config.default_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError("All providers failed. Last error: " + str(last_error))

    async def call_stream(
        self, provider_name, model="", messages=None, temperature=0.7, max_tokens=2048, **kwargs
    ) -> AsyncIterator[StreamChunk]:
        messages = messages or []
        last_error = None
        providers_to_try = [provider_name] + [
            fb_name for fb_name in self._fallback_chain
            if fb_name != provider_name and fb_name in self._providers
        ]
        for name in providers_to_try:
            p = self._providers.get(name)
            if p is None or not p.available:
                continue
            try:
                async for chunk in p.complete_stream(
                    model=model or p.config.default_model,
                    messages=messages, temperature=temperature,
                    max_tokens=max_tokens, **kwargs
                ):
                    yield chunk
                return
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError("All stream providers failed. Last error: " + str(last_error))

    # ── Credential Pool Integration ─────────────────────────────────

    def set_credential_manager(self, credential_manager: Any) -> None:
        """Attach a CredentialManager for multi-key rotation.

        Args:
            credential_manager: A CredentialManager instance from
                dragon.credential_pool.
        """
        self._credential_manager = credential_manager
        logger.info(
            "ProviderRegistry: credential manager attached (%d pools)",
            len(credential_manager._pools) if hasattr(credential_manager, '_pools') else 0,
        )

    def get_credential_manager(self) -> Any:
        """Return the attached CredentialManager, or None."""
        return self._credential_manager

    def _resolve_api_key(self, provider_name: str, config: ProviderConfig) -> str:
        """Resolve API key with credential pool fallback.

        Priority:
        1. Explicit api_key in ProviderConfig
        2. Credential pool (best available key for this provider)
        3. Environment variable (api_key_env)

        Args:
            provider_name: Provider name (for credential pool lookup).
            config: ProviderConfig with api_key and api_key_env.

        Returns:
            Resolved API key string.
        """
        # 1. Explicit key
        if config.api_key:
            return config.api_key

        # 2. Credential pool
        if self._credential_manager:
            cred = self._credential_manager.get_credential(provider_name)
            if cred and cred.key:
                self._current_credentials[provider_name] = cred
                logger.debug("Using credential pool key for '%s': %s",
                             provider_name, cred.masked_key())
                return cred.key

        # 3. Environment variable
        return os.getenv(config.api_key_env, "")

    def _on_call_success(
        self,
        provider_name: str,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Report successful API call to credential pool."""
        if not self._credential_manager:
            return
        cred = self._current_credentials.get(provider_name)
        if cred:
            self._credential_manager.mark_success(
                provider_name, cred,
                tokens_used=tokens_used,
                cost_usd=cost_usd,
            )

    def _on_call_error(
        self,
        provider_name: str,
        http_status: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        """Report API error to credential pool for key rotation."""
        if not self._credential_manager:
            return
        cred = self._current_credentials.get(provider_name)
        if cred:
            self._credential_manager.mark_error(
                provider_name, cred,
                http_status=http_status,
                retry_after=retry_after,
            )
            # Clear current credential so next call picks fresh key
            self._current_credentials.pop(provider_name, None)

    async def call_with_credential_rotation(
        self,
        provider_name: str,
        model: str = "",
        messages: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        max_key_retries: int = 3,
        **kwargs,
    ) -> ProviderResult:
        """Call a provider with automatic credential rotation on 429 errors.

        Unlike the basic call(), this method retries on rate-limit errors
        by automatically switching to the next available credential.

        Args:
            provider_name: Provider to call.
            model: Model name (empty = use provider default).
            messages: Chat messages.
            temperature: Sampling temperature.
            max_tokens: Maximum output tokens.
            max_key_retries: Maximum number of key rotation attempts.
            **kwargs: Additional provider-specific parameters.

        Returns:
            ProviderResult with content, usage, etc.

        Raises:
            ValueError: Unknown provider.
            RuntimeError: All credentials exhausted.
        """
        messages = messages or []
        model = model or ""
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        last_error = None
        for attempt in range(max_key_retries):
            # Resolve API key (may rotate if previous key was rate-limited)
            api_key = self._resolve_api_key(provider_name, provider.config)

            # Update provider's API key
            original_key = provider._api_key
            provider._api_key = api_key

            try:
                result = await provider.complete(
                    model=model or provider.config.default_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                # Success — report to credential pool
                self._on_call_success(
                    provider_name,
                    tokens_used=result.usage.get("total_tokens", 0),
                )
                return result

            except Exception as exc:
                # Detect HTTP status from exception
                http_status = self._extract_http_status(exc)
                retry_after = self._extract_retry_after(exc)

                logger.warning(
                    "ProviderRegistry: call to '%s' failed (attempt %d/%d, "
                    "status=%s): %s",
                    provider_name, attempt + 1, max_key_retries,
                    http_status, exc,
                )

                # Report to credential pool for rotation
                self._on_call_error(provider_name, http_status=http_status,
                                    retry_after=retry_after)

                # Restore original key
                provider._api_key = original_key

                # If not rate-limit related, don't retry with another key
                if http_status not in (429, 402, 403):
                    raise

                last_error = exc
                logger.info(
                    "ProviderRegistry: rotating credential for '%s' "
                    "(attempt %d of %d)",
                    provider_name, attempt + 1, max_key_retries,
                )

        raise RuntimeError(
            f"Provider '{provider_name}': all {max_key_retries} credential "
            f"rotation attempts failed. Last error: {last_error}"
        )

    @staticmethod
    def _extract_http_status(exc: Exception) -> Optional[int]:
        """Extract HTTP status code from provider exception."""
        # httpx.HTTPStatusError
        if hasattr(exc, 'response'):
            resp = exc.response  # type: ignore[union-attr]
            if hasattr(resp, 'status_code'):
                return resp.status_code
        # openai.APIStatusError
        if hasattr(exc, 'status_code'):
            return exc.status_code  # type: ignore[union-attr, return-value]
        # Check for "429" or "rate limit" in message
        import re
        msg = str(exc).lower()
        if '429' in msg or 'rate limit' in msg or 'too many requests' in msg:
            return 429
        if '402' in msg or 'quota' in msg or 'billing' in msg:
            return 402
        if '401' in msg or 'unauthorized' in msg:
            return 401
        if '403' in msg or 'forbidden' in msg:
            return 403
        return None

    @staticmethod
    def _extract_retry_after(exc: Exception) -> Optional[float]:
        """Extract Retry-After delay from exception if present."""
        if hasattr(exc, 'response'):
            resp = exc.response  # type: ignore[union-attr]
            if hasattr(resp, 'headers'):
                retry = resp.headers.get('Retry-After')  # type: ignore[union-attr]
                if retry:
                    try:
                        return float(retry)
                    except ValueError:
                        pass
        return None


# ────────────────────────────────────────────────────────────────────
# Auto-setup from environment
# ────────────────────────────────────────────────────────────────────


def auto_setup_providers() -> ProviderRegistry:
    """Create a ProviderRegistry with providers auto-detected from env vars."""
    registry = ProviderRegistry()

    # OpenAI
    if os.getenv("OPENAI_API_KEY"):
        registry.register("openai", OpenAIProvider(ProviderConfig(
            provider="openai", api_key_env="OPENAI_API_KEY",
            default_model="gpt-4o",
        )))

    # andlapi (OpenAI-compatible gateway)
    dragon_key = os.getenv("DRAGON_API_KEY", "")
    from dragon._domain_loader import API_BASE_URL as _DEF_URL
    dragon_base = os.getenv("DRAGON_BASE_URL", _DEF_URL)
    dragon_model = os.getenv("DRAGON_MODEL", "deepseek-v4-pro")
    if dragon_key:
        registry.register("andlapi", OpenAIProvider(ProviderConfig(
            provider="andlapi", api_key=dragon_key,
            base_url=dragon_base, default_model=dragon_model,
        )))
    elif os.path.exists("config.yaml"):
        import yaml
        try:
            with open("config.yaml") as cf:
                cfg = yaml.safe_load(cf) or {}
            api = cfg.get("dispatch", {}).get("global_api", {})
            key = api.get("api_key", "")
            from dragon._domain_loader import API_BASE_URL as _DEF_URL2
            base = api.get("base_url", _DEF_URL2)
            model = api.get("model", "deepseek-v4-pro")
            if key and "..." not in key:
                registry.register("andlapi", OpenAIProvider(ProviderConfig(
                    provider="andlapi", api_key=key,
                    base_url=base, default_model=model,
                )))
        except Exception:
            pass

    # Anthropic
    if os.getenv("ANTHROPIC_API_KEY"):
        registry.register("anthropic", AnthropicProvider(ProviderConfig(
            provider="anthropic", api_key_env="ANTHROPIC_API_KEY",
            default_model="claude-sonnet-4-20250514",
        )))

    # DeepSeek
    if os.getenv("DEEPSEEK_API_KEY"):
        registry.register("deepseek", DeepSeekProvider(ProviderConfig(
            provider="deepseek", api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
        )))

    # Google
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        registry.register("google", GoogleProvider(ProviderConfig(
            provider="google", api_key_env="GOOGLE_API_KEY",
            default_model="gemini-2.5-flash",
        )))

    # xAI
    if os.getenv("XAI_API_KEY"):
        registry.register("xai", XAIProvider(ProviderConfig(
            provider="xai", api_key_env="XAI_API_KEY",
            default_model="grok-3-beta",
        )))

    # Local llama.cpp
    model_path = os.getenv("DRAGON_LOCAL_MODEL", "")
    if model_path and os.path.exists(model_path):
        registry.register("local", LocalProvider(ProviderConfig(
            provider="local", base_url=model_path,
            default_model="local",
        )))

    # Together AI
    if os.getenv("TOGETHER_API_KEY"):
        registry.register("together", TogetherProvider(ProviderConfig(
            provider="together", api_key_env="TOGETHER_API_KEY",
            default_model="meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        )))

    # Groq
    if os.getenv("GROQ_API_KEY"):
        registry.register("groq", GroqProvider(ProviderConfig(
            provider="groq", api_key_env="GROQ_API_KEY",
            default_model="llama-4-maverick-17b-128e-instruct",
        )))

    # Mistral
    if os.getenv("MISTRAL_API_KEY"):
        registry.register("mistral", MistralProvider(ProviderConfig(
            provider="mistral", api_key_env="MISTRAL_API_KEY",
            default_model="mistral-large-latest",
        )))

    # Moonshot (月之暗面 / Kimi)
    if os.getenv("MOONSHOT_API_KEY"):
        registry.register("moonshot", MoonshotProvider(ProviderConfig(
            provider="moonshot", api_key_env="MOONSHOT_API_KEY",
            default_model="moonshot-v1-8k",
        )))

    # Ollama (check for OLLAMA_HOST or default endpoint reachability)
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    if ollama_host:
        registry.register("ollama", OllamaProvider(ProviderConfig(
            provider="ollama",
            base_url=f"{ollama_host.rstrip('/')}/v1",
            default_model="llama3",
        )))

    # OpenRouter
    if os.getenv("OPENROUTER_API_KEY"):
        registry.register("openrouter", OpenRouterProvider(ProviderConfig(
            provider="openrouter", api_key_env="OPENROUTER_API_KEY",
            default_model="openai/gpt-4o",
        )))

    # Azure OpenAI
    azure_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if azure_key and azure_endpoint:
        registry.register("azure", AzureOpenAIProvider(ProviderConfig(
            provider="azure", api_key_env="AZURE_OPENAI_API_KEY",
            base_url=azure_endpoint,
            default_model="gpt-4o",
        )))

    # Cohere
    if os.getenv("COHERE_API_KEY"):
        registry.register("cohere", CohereProvider(ProviderConfig(
            provider="cohere", api_key_env="COHERE_API_KEY",
            default_model="command-r-plus",
        )))

    # Replicate
    if os.getenv("REPLICATE_API_KEY"):
        registry.register("replicate", ReplicateProvider(ProviderConfig(
            provider="replicate", api_key_env="REPLICATE_API_KEY",
            default_model="meta/meta-llama-4-maverick",
        )))

    # Perplexity
    if os.getenv("PERPLEXITY_API_KEY"):
        registry.register("perplexity", PerplexityProvider(ProviderConfig(
            provider="perplexity", api_key_env="PERPLEXITY_API_KEY",
            default_model="sonar-pro",
        )))

    # Fireworks
    if os.getenv("FIREWORKS_API_KEY"):
        registry.register("fireworks", FireworksProvider(ProviderConfig(
            provider="fireworks", api_key_env="FIREWORKS_API_KEY",
            default_model="accounts/fireworks/models/llama-v4-maverick",
        )))

    # Cloudflare Workers AI
    cf_key = os.getenv("CLOUDFLARE_API_KEY", "")
    cf_account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    if cf_key and cf_account:
        registry.register("cloudflare", CloudflareProvider(ProviderConfig(
            provider="cloudflare", api_key_env="CLOUDFLARE_API_KEY",
            default_model="@cf/meta/llama-4-maverick",
        )))

    # Vertex AI
    vertex_project = os.getenv("VERTEX_PROJECT_ID", "")
    vertex_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if vertex_project or vertex_creds:
        registry.register("vertex", VertexAIProvider(ProviderConfig(
            provider="vertex",
            default_model="gemini-2.5-flash",
        )))

    # AWS Bedrock
    aws_key = os.getenv("AWS_ACCESS_KEY_ID", "")
    aws_region = os.getenv("AWS_REGION", "")
    if aws_key and aws_region:
        registry.register("bedrock", BedrockProvider(ProviderConfig(
            provider="bedrock",
            default_model="us.anthropic.claude-sonnet-4-20250514-v1:0",
        )))

    # Set fallback chain
    available = registry.available_providers()
    registry.set_fallback_chain(available)

    # ── Credential Pool Integration ─────────────────────────────
    # Auto-detect multi-key setups from environment and attach pool
    try:
        from dragon.credential_pool import CredentialManager
        cred_mgr = CredentialManager.from_env()
        if cred_mgr._pools:
            registry.set_credential_manager(cred_mgr)
            logger.info(
                "Credential pool auto-attached: %d providers with %d total keys",
                len(cred_mgr._pools),
                sum(p.total_count for p in cred_mgr._pools.values()),
            )
    except Exception as exc:
        logger.debug("Credential pool setup skipped: %s", exc)

    logger.info("Auto-setup: %d providers available: %s", len(available), available)
    return registry
