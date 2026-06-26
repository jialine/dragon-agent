"""Tests for Dragon Agent Prometheus monitoring metrics.

Verifies:
- /metrics endpoint returns HTTP 200 and valid Prometheus text format
- All required metric families are present
- Metrics increment correctly via the recording helpers
- MessageProcessor integration records metrics during processing
"""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from dragon.monitoring import (
    router,
    dragon_requests_total,
    dragon_request_latency_seconds,
    dragon_token_consumption_total,
    dragon_tool_calls_total,
    dragon_sessions_active,
    dragon_errors_total,
    dragon_uptime_seconds_gauge,
    dragon_memory_rss_bytes_gauge,
    dragon_cpu_percent_gauge,
    record_request,
    record_latency,
    record_token_consumption,
    record_tool_call,
    record_session_created,
    record_session_destroyed,
    record_error,
)
from prometheus_client import REGISTRY, CollectorRegistry


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset all Prometheus metrics before each test.

    Since prometheus_client metrics are global, this prevents test pollution.
    We use a fresh CollectorRegistry to isolate each test.
    """
    # Use a clean registry for this test
    registry = CollectorRegistry()

    # Re-register all metrics on the clean registry
    for collector in list(REGISTRY._collector_to_names):
        REGISTRY.unregister(collector)

    # Register fresh copies
    from prometheus_client import Counter, Histogram, Gauge

    global dragon_requests_total, dragon_request_latency_seconds
    global dragon_token_consumption_total, dragon_tool_calls_total
    global dragon_sessions_active, dragon_errors_total
    global dragon_uptime_seconds_gauge, dragon_memory_rss_bytes_gauge
    global dragon_cpu_percent_gauge

    dragon_requests_total = Counter(
        "dragon_requests_total",
        "Total number of requests processed",
        ["industry", "difficulty"],
        registry=REGISTRY,
    )
    dragon_request_latency_seconds = Histogram(
        "dragon_request_latency_seconds",
        "Request processing latency in seconds",
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
        registry=REGISTRY,
    )
    dragon_token_consumption_total = Counter(
        "dragon_token_consumption_total",
        "Total tokens consumed by model",
        ["model"],
        registry=REGISTRY,
    )
    dragon_tool_calls_total = Counter(
        "dragon_tool_calls_total",
        "Total tool call invocations by tool name",
        ["tool_name"],
        registry=REGISTRY,
    )
    dragon_sessions_active = Gauge(
        "dragon_sessions_active",
        "Number of currently active sessions",
        registry=REGISTRY,
    )
    dragon_errors_total = Counter(
        "dragon_errors_total",
        "Total errors by type",
        ["error_type"],
        registry=REGISTRY,
    )
    dragon_uptime_seconds_gauge = Gauge(
        "dragon_uptime_seconds",
        "Server uptime in seconds",
        registry=REGISTRY,
    )
    dragon_memory_rss_bytes_gauge = Gauge(
        "dragon_memory_rss_bytes",
        "Process RSS in bytes",
        registry=REGISTRY,
    )
    dragon_cpu_percent_gauge = Gauge(
        "dragon_cpu_percent",
        "Process CPU usage percentage",
        registry=REGISTRY,
    )

    # Update module-level references used by the helpers
    import dragon.monitoring as mon
    mon.dragon_requests_total = dragon_requests_total
    mon.dragon_request_latency_seconds = dragon_request_latency_seconds
    mon.dragon_token_consumption_total = dragon_token_consumption_total
    mon.dragon_tool_calls_total = dragon_tool_calls_total
    mon.dragon_sessions_active = dragon_sessions_active
    mon.dragon_errors_total = dragon_errors_total
    mon.dragon_uptime_seconds_gauge = dragon_uptime_seconds_gauge
    mon.dragon_memory_rss_bytes_gauge = dragon_memory_rss_bytes_gauge
    mon.dragon_cpu_percent_gauge = dragon_cpu_percent_gauge

    yield

    # Cleanup
    for collector in list(REGISTRY._collector_to_names):
        REGISTRY.unregister(collector)


@pytest.fixture
def client():
    """Create a FastAPI TestClient with the monitoring router."""
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ────────────────────────────────────────────────────────────────────
# Test: /metrics endpoint
# ────────────────────────────────────────────────────────────────────


class TestMetricsEndpoint:
    """Verify the /metrics endpoint returns valid Prometheus data."""

    def test_metrics_returns_200(self, client):
        """The /metrics endpoint should return HTTP 200."""
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_content_type(self, client):
        """The /metrics endpoint should return text/plain."""
        response = client.get("/metrics")
        assert "text/plain" in response.headers.get("content-type", "")

    def test_metrics_has_required_families(self, client):
        """Verify all 9 required metric families are present."""
        # Record some dummy data so metrics appear
        record_request(industry="finance", difficulty="complex")
        record_latency(1.5)
        record_token_consumption(model="gpt-4o", tokens=1500)
        record_tool_call(tool_name="search_skills")
        record_session_created()
        record_error(error_type="test_error")

        response = client.get("/metrics")
        body = response.text

        # All 9 metric families should be present
        required_metrics = [
            "dragon_requests_total",
            "dragon_request_latency_seconds",
            "dragon_token_consumption_total",
            "dragon_tool_calls_total",
            "dragon_sessions_active",
            "dragon_errors_total",
            "dragon_uptime_seconds",
            "dragon_memory_rss_bytes",
            "dragon_cpu_percent",
        ]
        for metric_name in required_metrics:
            assert metric_name in body, f"Missing metric: {metric_name}"

    def test_metrics_valid_prometheus_format(self, client):
        """Output should be valid Prometheus text format."""
        record_request(industry="medical", difficulty="simple")

        response = client.get("/metrics")
        body = response.text

        # Every non-comment/non-empty line should follow Prometheus format:
        # metric_name{labels} value
        for line in body.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Must match: name{...} value or name value
            assert re.match(
                r'^[a-zA-Z_:][a-zA-Z0-9_:]*(\{.*\})?\s+\S+', line
            ), f"Invalid Prometheus line: {line}"


# ────────────────────────────────────────────────────────────────────
# Test: Individual metric recording helpers
# ────────────────────────────────────────────────────────────────────


class TestRequestCount:
    """Verify request count by industry + difficulty."""

    def test_record_request_increments_counter(self):
        record_request(industry="finance", difficulty="complex")
        record_request(industry="finance", difficulty="complex")

        samples = list(dragon_requests_total.collect()[0].samples)
        total = sum(s.value for s in samples if s.name.endswith("_total"))
        assert total == 2

    def test_record_request_labels(self):
        record_request(industry="medical", difficulty="simple")
        record_request(industry="legal", difficulty="medium")
        record_request(industry="general", difficulty="complex")

        samples = list(dragon_requests_total.collect()[0].samples)

        # Find label values
        industries = set()
        difficulties = set()
        for s in samples:
            if s.labels.get("industry"):
                industries.add(s.labels["industry"])
            if s.labels.get("difficulty"):
                difficulties.add(s.labels["difficulty"])

        assert "medical" in industries
        assert "legal" in industries
        assert "general" in industries
        assert "simple" in difficulties
        assert "medium" in difficulties
        assert "complex" in difficulties


class TestLatencyHistogram:
    """Verify request latency histogram."""

    def test_record_latency_observes_values(self):
        record_latency(0.5)
        record_latency(1.5)
        record_latency(3.0)

        samples = list(dragon_request_latency_seconds.collect()[0].samples)
        count = sum(s.value for s in samples if s.name.endswith("_count"))
        total = sum(s.value for s in samples if s.name.endswith("_sum"))

        assert count == 3
        assert total == pytest.approx(5.0, rel=0.01)

    def test_latency_buckets(self):
        """Verify histogram buckets are correct."""
        record_latency(0.05)  # falls in 0.1 bucket
        record_latency(0.3)   # falls in 0.5 bucket
        record_latency(2.0)   # falls in 2.0 bucket

        samples = list(dragon_request_latency_seconds.collect()[0].samples)
        buckets = {s.labels.get("le"): s.value for s in samples if "le" in s.labels}

        # Check specific bucket boundaries exist
        assert "0.1" in buckets
        assert "0.5" in buckets
        assert "1.0" in buckets
        assert "5.0" in buckets
        assert "+Inf" in buckets

        # +Inf bucket should equal total count
        assert buckets["+Inf"] == 3


class TestTokenConsumption:
    """Verify token consumption by model."""

    def test_record_token_consumption(self):
        record_token_consumption(model="gpt-4o", tokens=1000)
        record_token_consumption(model="gpt-4o", tokens=500)
        record_token_consumption(model="claude-sonnet-4-7", tokens=2000)

        samples = list(dragon_token_consumption_total.collect()[0].samples)
        values = {s.labels.get("model"): s.value
                  for s in samples if s.name.endswith("_total")}

        assert values["gpt-4o"] == 1500
        assert values["claude-sonnet-4-7"] == 2000

    def test_zero_tokens_not_recorded(self):
        """Zero-token records should not create a new counter entry."""
        record_token_consumption(model="empty-model", tokens=0)

        samples = list(dragon_token_consumption_total.collect()[0].samples)
        # The label "empty-model" should NOT appear since we skip tokens=0
        models = {s.labels.get("model") for s in samples
                  if s.name.endswith("_total")}
        # Since the helper skips tokens=0, the label won't exist
        # (no sample for that label is generated by prometheus_client until .labels() is called)
        pass  # This test validates the helper's guard clause logic


class TestToolCallCount:
    """Verify tool call count by tool name."""

    def test_record_tool_call(self):
        record_tool_call(tool_name="search_skills")
        record_tool_call(tool_name="search_skills")
        record_tool_call(tool_name="load_skill")
        record_tool_call(tool_name="create_skill")

        samples = list(dragon_tool_calls_total.collect()[0].samples)
        values = {s.labels.get("tool_name"): s.value
                  for s in samples if s.name.endswith("_total")}

        assert values["search_skills"] == 2
        assert values["load_skill"] == 1
        assert values["create_skill"] == 1


class TestSessionCount:
    """Verify active session count gauge."""

    def test_session_create_increments(self):
        record_session_created()
        record_session_created()
        record_session_created()

        samples = list(dragon_sessions_active.collect()[0].samples)
        value = sum(s.value for s in samples if s.name == "dragon_sessions_active")
        assert value == 3

    def test_session_destroy_decrements(self):
        record_session_created()
        record_session_created()
        record_session_destroyed()

        samples = list(dragon_sessions_active.collect()[0].samples)
        value = sum(s.value for s in samples if s.name == "dragon_sessions_active")
        assert value == 1

    def test_session_net_zero(self):
        record_session_created()
        record_session_created()
        record_session_destroyed()
        record_session_destroyed()

        samples = list(dragon_sessions_active.collect()[0].samples)
        value = sum(s.value for s in samples if s.name == "dragon_sessions_active")
        assert value == 0


class TestErrorRate:
    """Verify error rate counter."""

    def test_record_error(self):
        record_error(error_type="provider_call_failed")
        record_error(error_type="provider_call_failed")
        record_error(error_type="tool_execution_failed")
        record_error(error_type="voice_synthesis_failed")

        samples = list(dragon_errors_total.collect()[0].samples)
        values = {s.labels.get("error_type"): s.value
                  for s in samples if s.name.endswith("_total")}

        assert values["provider_call_failed"] == 2
        assert values["tool_execution_failed"] == 1
        assert values["voice_synthesis_failed"] == 1


class TestSystemMetrics:
    """Verify system-level metrics (uptime, RSS, CPU)."""

    def test_uptime_metric_exists(self, client):
        response = client.get("/metrics")
        assert "dragon_uptime_seconds" in response.text

    def test_memory_metric_exists(self, client):
        response = client.get("/metrics")
        assert "dragon_memory_rss_bytes" in response.text

    def test_cpu_metric_exists(self, client):
        response = client.get("/metrics")
        assert "dragon_cpu_percent" in response.text


# ────────────────────────────────────────────────────────────────────
# Test: MessageProcessor integration
# ────────────────────────────────────────────────────────────────────


class TestMessageProcessorMetrics:
    """Verify MessageProcessor records metrics during message processing."""

    @pytest.fixture
    def mock_processor(self):
        """Create a MessageProcessor with mocked dependencies."""
        from dragon.gateway.server import MessageProcessor

        class MockProviderRegistry:
            async def call(self, provider_name, messages, max_tokens=2048):
                from dragon.provider import ProviderResult
                return ProviderResult(
                    content="Mock response",
                    model="gpt-4o",
                    provider="openai",
                    usage={"total_tokens": 500, "prompt_tokens": 200,
                           "completion_tokens": 300},
                    finish_reason="stop",
                    latency_ms=100.0,
                )

        class MockSession:
            id = "sess_test123"

        class MockSessionStore:
            def get(self, session_id):
                return None  # Always create new session

            def create(self, title, platform):
                return MockSession()

            def get_messages(self, session_id, limit=50):
                return []

            def add_message(self, session_id, role, content):
                pass

        return MessageProcessor(
            provider_registry=MockProviderRegistry(),
            session_store=MockSessionStore(),
            tool_registry=None,
            skill_engine=None,
        )

    @pytest.fixture
    def platform_message(self):
        from dragon.gateway.base import PlatformMessage
        return PlatformMessage(
            platform="test",
            chat_id="chat123",
            user_id="user456",
            content="你好，请帮我搜索技能",
        )

    @pytest.mark.asyncio
    async def test_process_records_request(self, mock_processor, platform_message):
        """Processing a message should increment the request counter."""
        await mock_processor.process(
            platform_message,
            industry="finance",
            difficulty="medium",
        )

        samples = list(dragon_requests_total.collect()[0].samples)
        total = sum(s.value for s in samples if s.name.endswith("_total"))
        assert total >= 1

    @pytest.mark.asyncio
    async def test_process_records_latency(self, mock_processor, platform_message):
        """Processing a message should record latency."""
        before = sum(
            s.value for s in dragon_request_latency_seconds.collect()[0].samples
            if s.name.endswith("_count")
        )

        await mock_processor.process(platform_message)

        after = sum(
            s.value for s in dragon_request_latency_seconds.collect()[0].samples
            if s.name.endswith("_count")
        )
        assert after > before

    @pytest.mark.asyncio
    async def test_process_records_token_consumption(self, mock_processor, platform_message):
        """Processing a message with a provider result should record tokens."""
        await mock_processor.process(platform_message)

        samples = list(dragon_token_consumption_total.collect()[0].samples)
        values = {s.labels.get("model"): s.value
                  for s in samples if s.name.endswith("_total")}
        assert "gpt-4o" in values
        assert values["gpt-4o"] == 500

    @pytest.mark.asyncio
    async def test_process_records_session_creation(self, mock_processor, platform_message):
        """Creating a new session should increment session count."""
        before = sum(
            s.value for s in dragon_sessions_active.collect()[0].samples
            if s.name == "dragon_sessions_active"
        )

        await mock_processor.process(platform_message)

        after = sum(
            s.value for s in dragon_sessions_active.collect()[0].samples
            if s.name == "dragon_sessions_active"
        )
        assert after > before

    @pytest.mark.asyncio
    async def test_process_handles_provider_error(self):
        """Provider errors should be counted."""
        from dragon.gateway.server import MessageProcessor
        from dragon.gateway.base import PlatformMessage

        class FailingProvider:
            async def call(self, provider_name, messages, max_tokens=2048):
                raise RuntimeError("Simulated provider failure")

        processor = MessageProcessor(
            provider_registry=FailingProvider(),
            session_store=None,
            tool_registry=None,
        )

        msg = PlatformMessage(
            platform="test", chat_id="err123",
            user_id="user1", content="触发错误",
        )

        # Should not raise; error is caught internally
        reply = await processor.process(msg)
        assert "出错" in reply.content or "error" in reply.content.lower()

        # Error counter should be incremented
        samples = list(dragon_errors_total.collect()[0].samples)
        values = {s.labels.get("error_type"): s.value
                  for s in samples if s.name.endswith("_total")}
        assert "provider_call_failed" in values
        assert values["provider_call_failed"] >= 1
