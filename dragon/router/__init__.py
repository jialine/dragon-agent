"""
Dragon Router — 智能路由模块

Uses a local Qwen3-0.6B (GGUF, Q4_K_M) model via llama-cpp-python to classify
user queries by industry (finance, medical, legal, education, general) and
difficulty (simple, medium, complex). Designed for async use — classification
runs in a thread pool so it never blocks the event loop.

Architecture:
    ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
    │  classify()  │────▶│ ThreadPoolExecutor│────▶│  llama.cpp   │
    │   (async)    │     │  (non-blocking)   │     │  Qwen3-0.6B  │
    └──────────────┘     └──────────────────┘     └──────────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger("dragon.router")

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RouterStatus(Enum):
    """Lifecycle status of the router / underlying LLM."""

    LOADING = "loading"   # model is being loaded in background
    LOADED = "loaded"     # model is ready for inference
    FAILED = "failed"     # model failed to load; all requests use fallback


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RouteResult:
    """The result of routing a single query.

    Attributes:
        industry:      Target industry — one of finance, medical, legal,
                       education, general.
        confidence:    0.0 – 1.0 confidence score assigned by the model.
        difficulty:    Estimated difficulty — simple, medium, or complex.
        reason:        Human-readable explanation (in Chinese) from the model.
        fallback:      ``True`` if this result came from a fallback path
                       (model not ready / parse failure / load failure).
    """

    industry: str
    confidence: float
    difficulty: str
    difficulty_score: float = 0.0
    reason: str = ""
    fallback: bool = False

    # ------------------------------------------------------------------
    # Pre-defined fallback instances (immutable-ish — don't mutate in place)
    # ------------------------------------------------------------------

    @classmethod
    def warming(cls) -> "RouteResult":
        """Returned while the model is still loading."""
        return cls(
            industry="general",
            confidence=0.3,
            difficulty="simple",
            difficulty_score=0.0,
            reason="模型正在预热中，暂时返回通用分类。",
            fallback=True,
        )

    @classmethod
    def fallback_general(cls, reason: str = "") -> "RouteResult":
        """Permanent fallback — model failed or parse error."""
        return cls(
            industry="general",
            confidence=0.1,
            difficulty="simple",
            difficulty_score=0.0,
            reason=reason or "路由模型不可用，返回通用分类。",
            fallback=True,
        )


@dataclass
class RouterMetrics:
    """Lightweight counters for observability.

    These are updated *inside* the thread-pool tasks; a simple Lock prevents
    races.  For high-throughput production use, consider atomics.
    """

    total_calls: int = 0
    fallback_count: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_ms / self.total_calls


# ---------------------------------------------------------------------------
# Classification prompt (Chinese, requests JSON output)
# ---------------------------------------------------------------------------

CLASSIFICATION_PROMPT = """你是一个智能路由分类器。请分析用户的查询，并输出一个 JSON 对象。

输出格式**必须严格遵守**，只输出 JSON，不要包含任何其他文本、解释或 Markdown 标记。

JSON 格式：
{{
  "industry": "finance" | "medical" | "legal" | "education" | "entertainment" | "general",
  "confidence": 0.0-1.0 之间的浮点数,
  "difficulty": "simple" | "medium" | "complex",
  "difficulty_score": 0-10 的数字,
  "reason": "简短的中文分类理由"
}}

分类规则：
- finance（金融）：涉及银行、投资、股票、保险、税务、会计、理财等。
- medical（医疗）：涉及疾病、药物、诊断、治疗、健康咨询、医保等。
- legal（法律）：涉及合同、诉讼、法规、知识产权、劳动法、刑事等。
- education（教育）：涉及课程、考试、学术、留学、培训、学习方法等。
- entertainment（娱乐）：涉及短剧、短视频、小说、剧本、故事创作、影视内容等。
- general（通用）：不属于上述任何类别的其他查询。
- difficulty：simple 为简单事实型问题，medium 为需要一定分析的问题，complex 为需要深入专业知识或多步骤推理的问题。
- difficulty_score：0-10的数字，0为极简单（如问候），5为中等（需要专业知识），10为极难（可能需要多个模型协同才能解决）。

用户查询：{query}

请输出 JSON："""

# Qwen2.5 chat template for formatting the full prompt
QWEN25_CHAT_TEMPLATE = (
    "<|im_start|>system\n{system_prompt}<|im_end|>\n"
    "<|im_start|>user\n{query}<|im_end|>\n"
    "<|im_start|>assistant\n"
)


def _build_prompt(query: str) -> str:
    """Build a Qwen2.5-compatible classification prompt."""
    system = CLASSIFICATION_PROMPT
    return QWEN25_CHAT_TEMPLATE.format(system_prompt=system, query=query)

# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_VALID_INDUSTRIES = frozenset({"finance", "medical", "legal", "education", "entertainment", "general"})
_VALID_DIFFICULTIES = frozenset({"simple", "medium", "complex"})


def _extract_json(text: str) -> Optional[dict]:
    """Try to pull a JSON object out of model output.

    Handles cases where the model:
    - wraps JSON in ``` fences
    - prefixes JSON with chatter
    - outputs bare text (e.g. "finance") — small models often skip JSON entirely
    """
    if not text:
        return None

    stripped = text.strip()

    # 1. Try direct parse
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 2. Try to find a JSON block inside the text
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 3. Bare-text fallback: small models often output just "finance" or
    #    "medical" instead of valid JSON.  If the output is a known industry
    #    keyword, treat it as a valid classification.
    bare = stripped.strip('"').strip("'").lower()
    if bare in _VALID_INDUSTRIES:
        return {"industry": bare, "confidence": 0.5, "difficulty": "simple"}

    return None


def _sanitise_parsed(parsed: dict) -> Optional[dict]:
    """Validate and coerce parsed keys to expected types/values."""
    industry = str(parsed.get("industry", "general")).strip().lower()
    if industry not in _VALID_INDUSTRIES:
        industry = "general"

    try:
        confidence = float(parsed.get("confidence", 0.1))
    except (TypeError, ValueError):
        confidence = 0.1
    confidence = max(0.0, min(1.0, confidence))

    difficulty = str(parsed.get("difficulty", "simple")).strip().lower()
    if difficulty not in _VALID_DIFFICULTIES:
        difficulty = "simple"

    # Parse difficulty_score with fallback mapping from difficulty string
    try:
        difficulty_score = float(parsed.get("difficulty_score", -1))
    except (TypeError, ValueError):
        difficulty_score = -1.0
    if difficulty_score < 0.0:
        # Fallback mapping based on difficulty string
        _DIFFICULTY_SCORE_MAP = {"simple": 2.0, "medium": 5.0, "complex": 8.0}
        difficulty_score = _DIFFICULTY_SCORE_MAP.get(difficulty, 2.0)
    difficulty_score = max(0.0, min(10.0, difficulty_score))

    reason = str(parsed.get("reason", ""))[:512]

    return {
        "industry": industry,
        "confidence": confidence,
        "difficulty": difficulty,
        "difficulty_score": difficulty_score,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# DragonRouter
# ---------------------------------------------------------------------------


class DragonRouter:
    """Industry-aware query router backed by a local Qwen3-0.6B model.

    Typical usage::

        router = DragonRouter(model_path="/models/qwen3-0.6b-q4_k_m.gguf")
        await router.initialize()        # non-blocking — model loads in thread

        result: RouteResult = await router.classify("什么是K线图？")
        print(result.industry)   # "finance"

        await router.shutdown()
    """

    # Maximum number of concurrent classification calls.
    _MAX_WORKERS = 2

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 2048,
        n_threads: int = 4,
        n_gpu_layers: int = 0,
        verbose: bool = False,
    ) -> None:
        """
        Args:
            model_path:   Absolute or relative path to the Qwen3 GGUF file.
            n_ctx:        Context window size (tokens).
            n_threads:    CPU threads for inference.
            n_gpu_layers: Number of layers to offload to GPU (0 = CPU-only).
            verbose:      Enable llama.cpp verbose logging.
        """
        self._model_path = Path(model_path).resolve()
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._n_gpu_layers = n_gpu_layers
        self._verbose = verbose

        # Internal state ---------------------------------------------------
        self._llm: Optional[Llama] = None
        self._status: RouterStatus = RouterStatus.LOADING
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=self._MAX_WORKERS, thread_name_prefix="dragon-router"
        )

        # Metrics ----------------------------------------------------------
        self._metrics = RouterMetrics()
        self._metrics_lock = threading.Lock()

        # Background load future (to avoid double-init) -------------------
        self._load_future: Optional[asyncio.Future] = None

        logger.info(
            "DragonRouter created (model=%s, n_ctx=%d, threads=%d, gpu_layers=%d)",
            self._model_path,
            n_ctx,
            n_threads,
            n_gpu_layers,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Start loading the model in a background thread.

        Idempotent — subsequent calls are no-ops.  The model load is
        deliberately offloaded to a thread so the caller's event loop is
        never blocked.
        """
        if self._load_future is not None:
            await self._load_future
            return

        loop = asyncio.get_running_loop()
        self._load_future = loop.run_in_executor(self._executor, self._load_model)

        try:
            await self._load_future
        except Exception:
            # Exception is already logged inside _load_model; future
            # contains the exception so callers can inspect it.
            pass

    async def shutdown(self) -> None:
        """Gracefully release all resources (model memory, thread pool).

        Safe to call multiple times.
        """
        logger.info("Shutting down DragonRouter …")

        with self._lock:
            if self._llm is not None:
                # llama-cpp-python __del__ handles cleanup, but explicit
                # close helps in some older versions.
                try:
                    # Llama objects don't always have an explicit close();
                    # setting to None + GC is the canonical approach.
                    del self._llm
                except Exception:
                    pass
                self._llm = None
            self._status = RouterStatus.FAILED

        # Shut down thread pool — wait=False so we don't block forever
        # on stuck classification tasks.
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info("DragonRouter shut down.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def classify(self, query: str) -> RouteResult:
        """Classify a user query asynchronously.

        If the model is still loading, a low-confidence ``general`` result is
        returned immediately.  If the model failed to load, a permanent
        fallback is returned.
        """
        # Fast-path: model not ready
        with self._lock:
            status = self._status

        if status is RouterStatus.LOADING:
            return RouteResult.warming()

        if status is RouterStatus.FAILED:
            return RouteResult.fallback_general("路由模型加载失败。")

        # Model is LOADED — run inference in thread pool
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()

        try:
            result = await loop.run_in_executor(
                self._executor, self._classify_sync, query
            )
        except Exception as exc:
            logger.exception("Classification error for query=%r", query[:80])
            result = RouteResult.fallback_general(f"分类异常: {exc}")

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Update metrics (non-blocking best-effort)
        with self._metrics_lock:
            self._metrics.total_calls += 1
            self._metrics.total_latency_ms += elapsed_ms
            if result.fallback:
                self._metrics.fallback_count += 1

        return result

    @property
    def status(self) -> RouterStatus:
        """Current router status (thread-safe)."""
        with self._lock:
            return self._status

    @property
    def metrics(self) -> RouterMetrics:
        """Return a **snapshot** of current metrics (thread-safe)."""
        with self._metrics_lock:
            return RouterMetrics(
                total_calls=self._metrics.total_calls,
                fallback_count=self._metrics.fallback_count,
                total_latency_ms=self._metrics.total_latency_ms,
            )

    # ------------------------------------------------------------------
    # Internal: model loading
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Load the GGUF model (called in a thread-pool worker)."""
        logger.info("Loading model from %s …", self._model_path)

        # Import here so the top-level module is importable even when
        # llama-cpp-python isn't installed (useful for CI / linting).
        from llama_cpp import Llama  # noqa: F811

        try:
            llm = Llama(
                model_path=str(self._model_path),
                n_ctx=self._n_ctx,
                n_threads=self._n_threads,
                n_gpu_layers=self._n_gpu_layers,
                verbose=self._verbose,
                # Use a lower batch size for classification (short prompts)
                n_batch=256,
                # Disable flash attention unless needed
                flash_attn=False,
            )
        except FileNotFoundError:
            logger.error(
                "Model file not found: %s. All classifications will use fallback.",
                self._model_path,
            )
            with self._lock:
                self._status = RouterStatus.FAILED
            return
        except Exception:
            logger.exception("Failed to load model from %s", self._model_path)
            with self._lock:
                self._status = RouterStatus.FAILED
            return

        with self._lock:
            self._llm = llm
            self._status = RouterStatus.LOADED

        logger.info("Model loaded successfully (status=%s)", self._status.value)

    # ------------------------------------------------------------------
    # Internal: synchronous classification
    # ------------------------------------------------------------------

    def _classify_sync(self, query: str) -> RouteResult:
        """Run classification on the current thread (must hold NO locks)."""
        llm: Optional[Llama] = None
        with self._lock:
            llm = self._llm

        if llm is None:
            return RouteResult.fallback_general("模型实例已释放。")

        prompt = _build_prompt(query)

        try:
            output = llm(
                prompt,
                max_tokens=256,
                temperature=0.1,
                top_p=0.95,
                stop=["<|im_end|>", "<|endoftext|>"],
                echo=False,
            )
        except Exception:
            logger.exception("llama.cpp inference failed for query=%r", query[:80])
            return RouteResult.fallback_general("推理异常，返回通用分类。")

        # Extract the generated text
        text = ""
        if isinstance(output, dict):
            text = output.get("choices", [{}])[0].get("text", "")
        elif isinstance(output, str):
            text = output
        else:
            logger.warning("Unexpected output type from llama-cpp: %s", type(output))
            return RouteResult.fallback_general("模型输出格式异常。")

        logger.debug("Raw model output: %s", text[:256])

        # Parse JSON
        parsed = _extract_json(text)

        if parsed is None:
            logger.warning(
                "Failed to extract JSON from model output for query=%r. "
                "Raw (truncated): %r",
                query[:80],
                text[:200],
            )
            return RouteResult.fallback_general("模型输出无法解析，返回通用分类。")

        sanitised = _sanitise_parsed(parsed)

        return RouteResult(
            industry=sanitised["industry"],
            confidence=sanitised["confidence"],
            difficulty=sanitised["difficulty"],
            difficulty_score=sanitised["difficulty_score"],
            reason=sanitised["reason"],
            fallback=False,
        )


# ---------------------------------------------------------------------------
# RemoteRouter — OpenAI-compatible API router (fast, no local model needed)
# ---------------------------------------------------------------------------


class RemoteRouter:
    """Industry router that calls a remote OpenAI-compatible API.

    Use this when you have a GPU server available — classification takes
    <1 second instead of 30-70s on CPU.

    Typical usage::

        router = RemoteRouter(
            base_url="http://192.168.0.21:8080/v1",
            model="Qwen3.5-122B-A10B",
        )
        await router.initialize()   # no-op (no model to load)

        result: RouteResult = await router.classify("什么是K线图？")
    """

    def __init__(
        self,
        base_url: str,
        model: str = "Qwen3.5-122B-A10B",
        *,
        api_key: str = "not-needed",
        timeout: float = 15.0,
        temperature: float = 0.1,
        max_tokens: int = 256,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens

        self._status: RouterStatus = RouterStatus.LOADING
        self._lock = threading.Lock()
        self._client: Optional[object] = None  # httpx.AsyncClient

        self._metrics = RouterMetrics()
        self._metrics_lock = threading.Lock()

        logger.info(
            "RemoteRouter created (base_url=%s, model=%s)",
            self._base_url,
            self._model,
        )

    async def initialize(self) -> None:
        """Create HTTP client (no model to load)."""
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        with self._lock:
            self._status = RouterStatus.LOADED
        logger.info("RemoteRouter ready (base_url=%s)", self._base_url)

    async def shutdown(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        with self._lock:
            self._status = RouterStatus.FAILED

    async def classify(self, query: str) -> RouteResult:
        """Classify via remote API."""
        with self._lock:
            status = self._status

        if status is RouterStatus.LOADING:
            return RouteResult.warming()
        if status is RouterStatus.FAILED:
            return RouteResult.fallback_general("远程路由不可用。")

        t0 = time.perf_counter()
        try:
            result = await self._classify_remote(query)
        except Exception as exc:
            logger.exception("Remote classification failed for query=%r", query[:80])
            result = RouteResult.fallback_general(f"远程分类异常: {exc}")

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        with self._metrics_lock:
            self._metrics.total_calls += 1
            self._metrics.total_latency_ms += elapsed_ms
            if result.fallback:
                self._metrics.fallback_count += 1

        return result

    async def _classify_remote(self, query: str) -> RouteResult:
        """Call the remote LLM API for classification."""
        import json as _json

        prompt = _build_prompt(query)

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "stop": ["<|im_end|>", "<|endoftext|>"],
        }

        assert self._client is not None
        response = await self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        text = ""
        choices = data.get("choices", [])
        if choices:
            text = choices[0].get("message", {}).get("content", "")

        logger.debug("Remote raw output: %s", text[:256])

        parsed = _extract_json(text)
        if parsed is None:
            logger.warning(
                "Failed to extract JSON from remote output for query=%r. "
                "Raw (truncated): %r",
                query[:80],
                text[:200],
            )
            return RouteResult.fallback_general("远程模型输出无法解析。")

        sanitised = _sanitise_parsed(parsed)
        return RouteResult(
            industry=sanitised["industry"],
            confidence=sanitised["confidence"],
            difficulty=sanitised["difficulty"],
            difficulty_score=sanitised["difficulty_score"],
            reason=sanitised["reason"],
            fallback=False,
        )

    @property
    def status(self) -> RouterStatus:
        with self._lock:
            return self._status

    @property
    def metrics(self) -> RouterMetrics:
        with self._metrics_lock:
            return RouterMetrics(
                total_calls=self._metrics.total_calls,
                fallback_count=self._metrics.fallback_count,
                total_latency_ms=self._metrics.total_latency_ms,
            )


# ---------------------------------------------------------------------------
# Singleton helpers (optional convenience)
# ---------------------------------------------------------------------------

_router_instance: Optional[DragonRouter] = None
_router_lock = threading.Lock()


def get_router(
    model_path: Optional[str] = None,
    **kwargs,
) -> DragonRouter:
    """Get or create the global DragonRouter singleton.

    Thread-safe.  Call ``await router.initialize()`` afterwards if you want
    the model to start loading immediately.
    """
    global _router_instance
    with _router_lock:
        if _router_instance is None:
            if model_path is None:
                raise ValueError(
                    "model_path is required to create the router singleton"
                )
            _router_instance = DragonRouter(model_path, **kwargs)
        return _router_instance


async def shutdown_router() -> None:
    """Shut down the global router singleton (if any)."""
    global _router_instance
    with _router_lock:
        router = _router_instance
        _router_instance = None
    if router is not None:
        await router.shutdown()
