"""Dragon Agent — Prometheus-compatible metrics endpoint.

Defines all metrics and exposes them via the FastAPI /metrics endpoint.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
import time
import psutil

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    REGISTRY,
)

# ────────────────────────────────────────────────────────────────────
# Prometheus Metrics Definitions
# ────────────────────────────────────────────────────────────────────

# 1) Request count by industry + difficulty
dragon_requests_total = Counter(
    "dragon_requests_total",
    "Total number of requests processed",
    ["industry", "difficulty"],
)

# 2) Request latency histogram (seconds)
dragon_request_latency_seconds = Histogram(
    "dragon_request_latency_seconds",
    "Request processing latency in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

# 3) Token consumption by model
dragon_token_consumption_total = Counter(
    "dragon_token_consumption_total",
    "Total tokens consumed by model",
    ["model"],
)

# 4) Tool call count by tool name
dragon_tool_calls_total = Counter(
    "dragon_tool_calls_total",
    "Total tool call invocations by tool name",
    ["tool_name"],
)

# 5) Active session count
dragon_sessions_active = Gauge(
    "dragon_sessions_active",
    "Number of currently active sessions",
)

# 6) Error rate
dragon_errors_total = Counter(
    "dragon_errors_total",
    "Total errors by type",
    ["error_type"],
)

# ── Legacy system metrics (kept for backward compatibility) ─────────

_start = time.time()

dragon_uptime_seconds_gauge = Gauge(
    "dragon_uptime_seconds",
    "Server uptime in seconds",
)

dragon_memory_rss_bytes_gauge = Gauge(
    "dragon_memory_rss_bytes",
    "Process RSS in bytes",
)

dragon_cpu_percent_gauge = Gauge(
    "dragon_cpu_percent",
    "Process CPU usage percentage",
)

# ────────────────────────────────────────────────────────────────────
# FastAPI Router
# ────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["monitoring"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus-compatible metrics endpoint.

    Returns all registered metrics in the standard Prometheus text format,
    including both application-level metrics (requests, latency, tokens,
    tool calls, sessions, errors) and system-level metrics (uptime, RSS, CPU%).
    """
    # Update system gauges
    dragon_uptime_seconds_gauge.set(time.time() - _start)

    p = psutil.Process()
    m = p.memory_info()
    dragon_memory_rss_bytes_gauge.set(m.rss)
    dragon_cpu_percent_gauge.set(p.cpu_percent())

    # Generate Prometheus text format for all registered metrics
    return PlainTextResponse(generate_latest(REGISTRY))


# ────────────────────────────────────────────────────────────────────
# Convenience helpers for recording metrics from MessageProcessor
# ────────────────────────────────────────────────────────────────────

def record_request(industry: str = "unknown", difficulty: str = "unknown") -> None:
    """Record a processed request with industry/difficulty labels."""
    dragon_requests_total.labels(industry=industry, difficulty=difficulty).inc()


def record_latency(seconds: float) -> None:
    """Observe request latency in seconds."""
    dragon_request_latency_seconds.observe(seconds)


def record_token_consumption(model: str, tokens: int) -> None:
    """Record token consumption for a specific model."""
    if tokens > 0:
        dragon_token_consumption_total.labels(model=model).inc(tokens)


def record_tool_call(tool_name: str) -> None:
    """Record a tool call invocation."""
    dragon_tool_calls_total.labels(tool_name=tool_name).inc()


def record_session_created() -> None:
    """Increment active session count."""
    dragon_sessions_active.inc()


def record_session_destroyed() -> None:
    """Decrement active session count."""
    dragon_sessions_active.dec()


def record_error(error_type: str = "unknown") -> None:
    """Record an error occurrence."""
    dragon_errors_total.labels(error_type=error_type).inc()
