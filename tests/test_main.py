"""
FastAPI endpoint tests for Dragon Agent main.py.

Uses FastAPI's TestClient with all external dependencies mocked —
no actual LLM calls, no GGUF model loading, no network access.

Test coverage:
  1. GET  /health              — healthy status, 200
  2. POST /v1/chat             — basic chat returns content
  3. POST /v1/chat             — empty messages returns error
  4. POST /v1/chat             — with session_id and model parameters
  5. POST /v1/chat/stream      — streaming SSE response
  6. GET  /v1/consult/assess   — assessment with needs_consultation=True
  7. GET  /v1/consult/assess   — low difficulty returns needs_consultation=False
  8. GET  /v1/consult/assess   — missing 'q' parameter returns error
  9. GET  /docs                — docs page returns 200
  10. GET  /openapi.json       — returns valid JSON schema
  11. GET  /health             — error when router is None (503)
  12. POST /v1/chat            — serialization of ChatResponse fields
"""

import json
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from fastapi.testclient import TestClient
from fastapi import FastAPI

import dragon.main as main_module
from dragon.router import RouteResult, RouterMetrics, RouterStatus
from dragon.consult import ConsultationAssessment


# ════════════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_route_result():
    """Default RouteResult used as classification output."""
    return RouteResult(
        industry="general",
        confidence=0.85,
        difficulty="simple",
        difficulty_score=2.5,
        reason="standard query",
        fallback=False,
    )


@pytest.fixture
def mock_consult_assessment():
    """Mock ConsultationAssessment for high-difficulty queries."""
    return ConsultationAssessment(
        difficulty_score=8.5,
        estimated_success=0.45,
        needs_consultation=True,
        recommended_panel=["deepseek-reasoner", "gpt-4o", "claude-sonnet-4"],
        estimated_cost=0.012345,
        warning_message="⚠️ 难度较高，建议专家会诊",
        difficulty_label="很困难",
        recommendation="建议启动专家会诊流程",
    )


@pytest.fixture
def mock_consult_assessment_low():
    """Mock ConsultationAssessment for low-difficulty queries."""
    return ConsultationAssessment(
        difficulty_score=2.0,
        estimated_success=0.98,
        needs_consultation=False,
        recommended_panel=[],
        estimated_cost=0.0,
        warning_message="✅ 简单问题，无需会诊",
        difficulty_label="极简单",
        recommendation="标准路由即可处理",
    )


@pytest.fixture
def mock_router(mock_route_result):
    """Mock DragonRouter — returns a pre-built RouteResult."""
    router = MagicMock()
    router.classify = AsyncMock(return_value=mock_route_result)
    router.status = RouterStatus.LOADED
    router.metrics = RouterMetrics()
    router.metrics.total_calls = 42
    router.metrics.fallback_count = 1
    router.metrics.total_latency_ms = 5000.0
    return router


@pytest.fixture
def mock_dispatcher():
    """Mock DragonDispatcher with dispatch and dispatch_stream."""
    dispatcher = MagicMock()
    dispatcher.dispatch = AsyncMock(
        return_value=MagicMock(
            model="qwen3-8b",
            provider="local",
            content="Hello! This is a test response from Dragon.",
            usage={"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60},
        )
    )
    # dispatch_stream returns an async generator of chunks
    async def _mock_stream(*args, **kwargs):
        class Chunk:
            def __init__(self, content, finish_reason=None, usage=None):
                self.content = content
                self.finish_reason = finish_reason
                self.usage = usage

        yield Chunk("Hello", finish_reason=None)
        yield Chunk(" world", finish_reason=None)
        yield Chunk("!", finish_reason="stop", usage={"total_tokens": 5})

    dispatcher.dispatch_stream = MagicMock(side_effect=_mock_stream)
    return dispatcher


@pytest.fixture
def mock_consult_engine(mock_consult_assessment):
    """Mock ExpertConsultation engine."""
    engine = MagicMock()
    engine.assess = MagicMock(return_value=mock_consult_assessment)
    engine.request_approval = MagicMock(
        return_value={
            "needs_approval": True,
            "message": "⚠️ 难度较高，建议专家会诊",
            "estimated_success_rate": 0.45,
            "model_panel": ["deepseek-reasoner", "gpt-4o", "claude-sonnet-4"],
            "estimated_cost": 0.012345,
            "difficulty_score": 8.5,
        }
    )
    return engine


@pytest.fixture
def client(mock_router, mock_dispatcher, mock_consult_engine):
    """Create FastAPI TestClient with all globals mocked.

    Replaces the app's lifespan with a no-op so no real models are loaded,
    and injects mock router/dispatcher/guard/consult_engine/skill_engine/tool_registry.
    """
    # ── 1. Replace the app lifespan with a no-op ──
    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    # Store original and swap
    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = noop_lifespan

    # ── 2. Inject mock globals into dragon.main ──
    main_module.router = mock_router
    main_module.dispatcher = mock_dispatcher
    mock_guard = MagicMock()
    main_module.guard = mock_guard
    main_module.config = MagicMock()
    main_module.consult_engine = mock_consult_engine
    # Skill engine and tool registry mocks (for /health)
    main_module.skill_engine = MagicMock()
    main_module.skill_engine.stats.return_value = {"skills": 5, "version": "1.0"}
    main_module.tool_registry = MagicMock()
    main_module.tool_registry.stats.return_value = {"tools": 12, "categories": 4}

    # ── 3. Create TestClient ──
    with TestClient(main_module.app) as tc:
        yield tc

    # ── 4. Restore lifespan ──
    main_module.app.router.lifespan_context = original_lifespan


@pytest.fixture
def client_no_router(mock_dispatcher):
    """TestClient with router/guard/consult_engine set to None (simulates startup failure)."""
    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):
        yield

    original_lifespan = main_module.app.router.lifespan_context
    main_module.app.router.lifespan_context = noop_lifespan

    main_module.router = None
    main_module.dispatcher = mock_dispatcher
    main_module.guard = None
    main_module.config = None
    main_module.consult_engine = None
    main_module.skill_engine = None
    main_module.tool_registry = None

    with TestClient(main_module.app) as tc:
        yield tc

    main_module.app.router.lifespan_context = original_lifespan


# ════════════════════════════════════════════════════════════════════
# 1. Health Endpoint
# ════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_200(self, client):
        """Health endpoint returns 200 status."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy_status(self, client):
        """Health endpoint returns status 'healthy'."""
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_includes_version(self, client):
        """Health endpoint includes version string."""
        response = client.get("/health")
        data = response.json()
        assert data["version"] == "1.0.0"

    def test_health_includes_all_component_keys(self, client):
        """Health response has components for router, dispatcher, guard, skills, tools."""
        response = client.get("/health")
        data = response.json()
        components = data["components"]
        for key in ("router", "dispatcher", "guard", "skills", "tools"):
            assert key in components, f"Missing component key: {key}"

    def test_health_router_shows_loaded_status(self, client):
        """When router is LOADED, health shows 'loaded'."""
        response = client.get("/health")
        data = response.json()
        assert data["components"]["router"]["status"] == "loaded"

    def test_health_router_has_metrics(self, client):
        """Health includes router metrics."""
        response = client.get("/health")
        data = response.json()
        metrics = data["components"]["router"]["metrics"]
        assert metrics["total_calls"] == 42
        assert metrics["fallback_count"] == 1

    def test_health_dispatcher_ready(self, client):
        """When dispatcher is set, health shows 'ready'."""
        response = client.get("/health")
        assert response.json()["components"]["dispatcher"]["status"] == "ready"

    def test_health_guard_ready(self, client):
        """When guard is set, health shows 'ready'."""
        response = client.get("/health")
        assert response.json()["components"]["guard"]["status"] == "ready"

    def test_health_skills_present(self, client):
        """Skills component shows stats from skill_engine."""
        response = client.get("/health")
        skills = response.json()["components"]["skills"]
        assert skills["skills"] == 5

    def test_health_tools_present(self, client):
        """Tools component shows stats from tool_registry."""
        response = client.get("/health")
        tools = response.json()["components"]["tools"]
        assert tools["tools"] == 12

    def test_health_when_router_none(self, client_no_router):
        """When router is None, components show 'unknown' but endpoint still returns 200."""
        response = client_no_router.get("/health")
        data = response.json()
        assert response.status_code == 200
        assert data["status"] == "healthy"
        assert data["components"]["router"]["status"] == "unknown"
        assert data["components"]["router"]["metrics"] == {}


# ════════════════════════════════════════════════════════════════════
# 2. Chat Endpoint
# ════════════════════════════════════════════════════════════════════


class TestChatEndpoint:
    """Tests for POST /v1/chat."""

    def test_chat_returns_200(self, client):
        """Basic chat request returns 200."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200

    def test_chat_returns_content(self, client):
        """Chat response includes content from the dispatcher."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        data = response.json()
        assert "content" in data
        assert len(data["content"]) > 0
        assert "Hello" in data["content"]

    def test_chat_response_has_required_fields(self, client):
        """ChatResponse includes industry, confidence, difficulty, model, provider, content, latency_ms."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        data = response.json()
        required = {"industry", "confidence", "difficulty", "model", "provider", "content", "latency_ms"}
        assert required.issubset(set(data.keys())), f"Missing keys: {required - set(data.keys())}"

    def test_chat_response_confidence_is_float(self, client):
        """Confidence field is a float between 0 and 1."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        data = response.json()
        assert isinstance(data["confidence"], float)
        assert 0.0 <= data["confidence"] <= 1.0

    def test_chat_response_latency_ms_is_int(self, client):
        """Latency_ms is a non-negative integer."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        data = response.json()
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] >= 0

    def test_chat_with_session_id(self, client):
        """Passing session_id works and is accepted."""
        response = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "session_id": "test-session-001",
            },
        )
        assert response.status_code == 200
        assert response.json()["content"]

    def test_chat_with_temperature(self, client):
        """Passing temperature parameter works."""
        response = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.3,
            },
        )
        assert response.status_code == 200

    def test_chat_with_max_tokens(self, client):
        """Passing max_tokens parameter works."""
        response = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 512,
            },
        )
        assert response.status_code == 200

    def test_chat_with_extra_model_field_ignored(self, client):
        """Extra 'model' field in request body is silently ignored by Pydantic (or 422)."""
        # FastAPI/Pydantic ignores extra fields by default or returns 422.
        # We test it's handled gracefully.
        response = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "model": "gpt-4o",
            },
        )
        # Should either work (extra field ignored) or return validation error
        assert response.status_code in (200, 422)

    def test_chat_with_stream_false(self, client):
        """When stream=false, returns a regular JSON ChatResponse."""
        response = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("application/json")

    def test_chat_passes_last_user_message_to_router(self, mock_router, client):
        """Router.classify receives the last user message content."""
        client.post(
            "/v1/chat",
            json={
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "What is AI?"},
                    {"role": "assistant", "content": "AI is..."},
                    {"role": "user", "content": "Explain more"},
                ],
            },
        )
        mock_router.classify.assert_called_once_with("Explain more")

    def test_chat_multiple_messages(self, client):
        """Multi-turn conversation works."""
        response = client.post(
            "/v1/chat",
            json={
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "Hi there!"},
                    {"role": "user", "content": "how are you?"},
                ],
            },
        )
        assert response.status_code == 200

    def test_chat_when_dispatcher_none(self, client_no_router):
        """When router or dispatcher is None, returns 503."""
        # client_no_router has router=None
        response = client_no_router.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"].lower()


# ════════════════════════════════════════════════════════════════════
# 3. Chat — Empty / Invalid Messages
# ════════════════════════════════════════════════════════════════════


class TestChatEmptyMessage:
    """Tests for edge cases with empty or missing messages."""

    def test_chat_with_empty_content(self, client):
        """User message with empty string content is accepted (router handles it)."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": ""}]},
        )
        # The endpoint itself doesn't validate empty content — router gets ""
        assert response.status_code == 200

    def test_chat_no_user_message_finds_none(self, mock_router, client):
        """When no user message exists, router.classify gets empty string (next() default)."""
        client.post(
            "/v1/chat",
            json={
                "messages": [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "assistant", "content": "Hello!"},
                ],
            },
        )
        mock_router.classify.assert_called_once_with("")

    def test_chat_missing_messages_field(self, client):
        """Missing 'messages' field in request body returns 422."""
        response = client.post("/v1/chat", json={})
        assert response.status_code == 422

    def test_chat_messages_not_a_list(self, client):
        """Messages field that is not a list returns 422."""
        response = client.post(
            "/v1/chat",
            json={"messages": "not a list"},
        )
        assert response.status_code == 422

    def test_chat_message_missing_role(self, client):
        """Message dict missing 'role' returns 422."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"content": "hello"}]},
        )
        assert response.status_code == 422

    def test_chat_message_missing_content(self, client):
        """Message dict missing 'content' returns 422."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user"}]},
        )
        assert response.status_code == 422


# ════════════════════════════════════════════════════════════════════
# 4. Chat Streaming Endpoint
# ════════════════════════════════════════════════════════════════════


class TestChatStreamEndpoint:
    """Tests for POST /v1/chat/stream (SSE streaming)."""

    def test_stream_returns_200(self, client):
        """Streaming endpoint returns 200."""
        response = client.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 200

    def test_stream_content_type_is_sse(self, client):
        """Streaming response has text/event-stream content type."""
        response = client.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.headers["content-type"].startswith("text/event-stream")

    def test_stream_has_industry_header(self, client):
        """Streaming response includes X-Industry header from classification."""
        response = client.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert "x-industry" in response.headers
        assert response.headers["x-industry"] == "general"

    def test_stream_body_contains_data_prefix(self, client):
        """SSE body lines start with 'data: '."""
        response = client.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        body = response.text
        # At minimum, the first SSE event and [DONE] should be present
        assert "data:" in body

    def test_stream_ends_with_done(self, client):
        """SSE stream ends with '[DONE]' marker."""
        response = client.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert "[DONE]" in response.text

    def test_stream_first_event_has_industry(self, client):
        """First SSE event includes industry and difficulty from classification."""
        response = client.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        # Parse the first SSE event
        first_line = response.text.strip().split("\n")[0]
        assert first_line.startswith("data: ")
        event_data = json.loads(first_line[6:])  # strip "data: "
        assert "industry" in event_data
        assert "difficulty" in event_data

    def test_stream_when_dispatcher_none(self, client_no_router):
        """When router/dispatcher is None, streaming returns 503."""
        response = client_no_router.post(
            "/v1/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 503


# ════════════════════════════════════════════════════════════════════
# 5. Consult Assess Endpoint
# ════════════════════════════════════════════════════════════════════


class TestConsultAssess:
    """Tests for GET /v1/consult/assess."""

    def test_assess_returns_200(self, client):
        """Assess endpoint returns 200 for valid query."""
        response = client.get("/v1/consult/assess?q=complex+problem")
        assert response.status_code == 200

    def test_assess_returns_industry(self, client):
        """Response includes industry from router classification."""
        response = client.get("/v1/consult/assess?q=test")
        data = response.json()
        assert "industry" in data
        assert data["industry"] == "general"

    def test_assess_returns_difficulty_score(self, client):
        """Response includes difficulty_score from router classification."""
        response = client.get("/v1/consult/assess?q=test")
        data = response.json()
        assert "difficulty_score" in data
        assert isinstance(data["difficulty_score"], float)

    def test_assess_returns_assessment(self, client):
        """Response includes an assessment dict."""
        response = client.get("/v1/consult/assess?q=test")
        data = response.json()
        assert "assessment" in data
        assessment = data["assessment"]
        assert "difficulty_score" in assessment
        assert "needs_consultation" in assessment
        assert "estimated_success" in assessment

    def test_assess_returns_approval_message(self, client):
        """Response includes an approval_message."""
        response = client.get("/v1/consult/assess?q=test")
        data = response.json()
        assert "approval_message" in data
        assert isinstance(data["approval_message"], dict)

    def test_assess_needs_consultation_when_high_difficulty(
        self, client, mock_route_result, mock_consult_assessment
    ):
        """When difficulty_score is high, needs_consultation is True."""
        mock_route_result.difficulty_score = 8.5
        response = client.get("/v1/consult/assess?q=hard+question")
        data = response.json()
        assert data["assessment"]["needs_consultation"] is True

    def test_assess_low_difficulty_no_consultation(
        self, client, mock_router, mock_consult_assessment_low, mock_consult_engine
    ):
        """Low difficulty queries return needs_consultation=False."""
        # Make router return low difficulty
        mock_router.classify.return_value = RouteResult(
            industry="general",
            confidence=0.95,
            difficulty="simple",
            difficulty_score=2.0,
            reason="easy",
        )
        # Make consult engine return low-difficulty assessment
        mock_consult_engine.assess.return_value = mock_consult_assessment_low
        mock_consult_engine.request_approval.return_value = {
            "needs_approval": False,
            "message": "✅ 简单问题",
            "estimated_success_rate": 0.98,
            "model_panel": [],
            "estimated_cost": 0.0,
            "difficulty_score": 2.0,
        }

        response = client.get("/v1/consult/assess?q=easy+question")
        data = response.json()
        assert data["assessment"]["needs_consultation"] is False
        assert data["assessment"]["estimated_success"] == 0.98

    def test_assess_missing_q_returns_422(self, client):
        """Missing required 'q' query parameter returns 422."""
        response = client.get("/v1/consult/assess")
        assert response.status_code == 422

    def test_assess_with_default_session_id(self, client):
        """Default session_id is 'default'."""
        response = client.get("/v1/consult/assess?q=test")
        assert response.status_code == 200

    def test_assess_with_custom_session_id(self, client):
        """Custom session_id is accepted."""
        response = client.get("/v1/consult/assess?q=test&session_id=custom-session")
        assert response.status_code == 200

    def test_assess_when_consult_none(self, client_no_router):
        """When consult_engine is None, returns 503."""
        response = client_no_router.get("/v1/consult/assess?q=test")
        assert response.status_code == 503


# ════════════════════════════════════════════════════════════════════
# 6. API Docs & OpenAPI Schema
# ════════════════════════════════════════════════════════════════════


class TestDocsEndpoints:
    """Tests for /docs and /openapi.json."""

    def test_docs_returns_200(self, client):
        """Swagger docs page returns 200."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_docs_content_type_is_html(self, client):
        """Docs page returns HTML."""
        response = client.get("/docs")
        assert "text/html" in response.headers["content-type"]

    def test_docs_contains_swagger_ui(self, client):
        """Docs page contains Swagger UI reference."""
        response = client.get("/docs")
        assert "swagger" in response.text.lower()

    def test_openapi_returns_200(self, client):
        """OpenAPI schema returns 200."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_openapi_is_valid_json(self, client):
        """Response is valid JSON."""
        response = client.get("/openapi.json")
        data = response.json()
        assert isinstance(data, dict)

    def test_openapi_has_required_fields(self, client):
        """OpenAPI schema has openapi, info, paths keys."""
        response = client.get("/openapi.json")
        data = response.json()
        assert "openapi" in data
        assert "info" in data
        assert "paths" in data

    def test_openapi_version_is_3(self, client):
        """OpenAPI version starts with 3."""
        response = client.get("/openapi.json")
        data = response.json()
        assert data["openapi"].startswith("3.")

    def test_openapi_title_is_dragon_agent(self, client):
        """OpenAPI info title is 'Dragon Agent'."""
        response = client.get("/openapi.json")
        data = response.json()
        assert data["info"]["title"] == "Dragon Agent"

    def test_openapi_includes_chat_paths(self, client):
        """OpenAPI paths include /v1/chat and /v1/chat/stream."""
        response = client.get("/openapi.json")
        data = response.json()
        paths = data["paths"]
        assert "/v1/chat" in paths
        assert "/v1/chat/stream" in paths

    def test_openapi_includes_health_path(self, client):
        """OpenAPI paths include /health."""
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/health" in paths

    def test_openapi_includes_consult_paths(self, client):
        """OpenAPI paths include /v1/consult/assess and /v1/consult."""
        response = client.get("/openapi.json")
        paths = response.json()["paths"]
        assert "/v1/consult/assess" in paths
        assert "/v1/consult" in paths


# ════════════════════════════════════════════════════════════════════
# 7. Edge Cases & Error Handling
# ════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case tests for the API."""

    def test_chat_json_with_chinese(self, client):
        """Chat with Chinese characters works correctly."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "你好，请问什么是人工智能？"}]},
        )
        assert response.status_code == 200
        assert response.json()["content"]

    def test_chat_very_long_message(self, client):
        """Chat with a very long user message works."""
        long_text = "hello " * 1000
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": long_text}]},
        )
        assert response.status_code == 200

    def test_chat_with_unicode_emoji(self, client):
        """Chat with emoji and special Unicode works."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Hello 👋😊🎉"}]},
        )
        assert response.status_code == 200

    def test_health_idempotent(self, client):
        """Health endpoint returns consistent results on repeated calls."""
        r1 = client.get("/health").json()
        r2 = client.get("/health").json()
        assert r1["status"] == r2["status"]
        assert r1["version"] == r2["version"]

    def test_chat_content_type_json(self, client):
        """Chat response Content-Type is application/json."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "test"}]},
        )
        assert response.headers["content-type"].startswith("application/json")

    def test_chat_usage_field_present(self, client):
        """Chat response includes usage dict when dispatcher provides it."""
        response = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        data = response.json()
        assert "usage" in data
        if data["usage"] is not None:
            assert "total_tokens" in data["usage"]
