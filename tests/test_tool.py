"""
Unit tests for ToolRegistry and ToolPipeline.
"""
import os
import asyncio
import json
import tempfile

import pytest

from dragon.tool.registry import (
    ToolRegistry, ToolPipeline, PipelineToolStep,
    ToolDef, ToolResult, ToolOutcome, CircuitState, CircuitBreaker,
)


# ── Test Tool Implementations ──────────────────────────────────────

async def _echo(**kwargs) -> str:
    return json.dumps(kwargs)


async def _fail(**kwargs) -> str:
    raise RuntimeError("intentional failure")


async def _slow(**kwargs) -> str:
    await asyncio.sleep(1.0)
    return json.dumps({"done": True})


# ── Sync-only helpers ──────────────────────────────────────────────

def _sync_call(registry, tool_name, args, timeout=None):
    """Synchronous wrapper for registry.call() — runs in new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            registry.call(tool_name, args, timeout_secs=timeout)
        )
    finally:
        loop.close()


# ── Tests ──────────────────────────────────────────────────────────

class TestToolRegistration:
    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_decorator(self):
        @self.registry.register(name="my-tool", description="Does things", tags=["test"])
        async def my_tool(x: int) -> str:
            return str(x)

        tool = self.registry.get("my-tool")
        assert tool is not None
        assert tool.name == "my-tool"
        assert tool.description == "Does things"
        assert tool.tags == ["test"]

    def test_register_tool_def(self):
        tool_def = ToolDef(
            name="def-tool",
            description="From ToolDef",
            handler=_echo,
            tags=["def"],
        )
        self.registry.register_tool(tool_def)
        assert self.registry.get("def-tool") is not None

    def test_unregister(self):
        self.registry.register(name="temp", description="temp")(_echo)
        assert self.registry.unregister("temp") is True
        assert self.registry.get("temp") is None

    def test_unregister_missing(self):
        assert self.registry.unregister("ghost") is False

    def test_duplicate_registration_overwrites(self):
        self.registry.register(name="dup", description="first")(_echo)
        self.registry.register(name="dup", description="second")(_echo)
        tool = self.registry.get("dup")
        assert tool.description == "second"


class TestToolListing:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="t1", description="Tool one", category="web")(_echo)
        self.registry.register(name="t2", description="Tool two", category="file")(_echo)
        self.registry.register(name="t3", description="Tool three", category="web")(_echo)

    def test_list_all(self):
        tools = self.registry.list_tools()
        assert len(tools) == 3

    def test_filter_by_category(self):
        tools = self.registry.list_tools(category="web")
        assert len(tools) == 2
        names = [t["name"] for t in tools]
        assert "t1" in names
        assert "t3" in names

    def test_filter_missing_category(self):
        tools = self.registry.list_tools(category="nonexistent")
        assert len(tools) == 0


class TestToolSearch:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            name="search-web",
            description="Search the web for information",
            tags=["search", "web"],
        )(_echo)
        self.registry.register(
            name="read-file",
            description="Read file contents",
            tags=["file", "read"],
        )(_echo)

    def test_search_by_name(self):
        results = self.registry.search("web")
        assert len(results) >= 1
        assert results[0]["name"] == "search-web"

    def test_search_by_description(self):
        results = self.registry.search("file contents")
        assert len(results) >= 1

    def test_search_by_tag(self):
        results = self.registry.search("read")
        assert any(r["name"] == "read-file" for r in results)


class TestToolExecution:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="echo", description="Echo")(_echo)

    def test_call_success(self):
        result = _sync_call(self.registry, "echo", {"x": "hello"})
        assert result.success is True
        assert result.outcome == ToolOutcome.SUCCESS
        assert "hello" in str(result.output)

    def test_call_missing_tool(self):
        result = _sync_call(self.registry, "no-such-tool", {})
        assert result.success is False
        assert result.outcome == ToolOutcome.ERROR
        assert "not found" in result.error

    def test_call_with_retry_on_failure(self):
        self.registry.register(
            name="flaky",
            description="Flaky tool",
            max_retries=2,
        )(_fail)
        result = _sync_call(self.registry, "flaky", {})
        assert result.success is False
        assert result.retries_used == 2

    def test_call_times_out(self):
        self.registry.register(
            name="slow-poke",
            description="Slow tool",
            timeout_secs=0.1,
            max_retries=0,
        )(_slow)
        result = _sync_call(self.registry, "slow-poke", {}, timeout=0.1)
        assert result.outcome == ToolOutcome.ERROR


class TestCircuitBreaker:
    def setup_method(self):
        self.cb = CircuitBreaker(failure_threshold=2, reset_timeout_secs=60)

    def test_initial_state_closed(self):
        assert self.cb.before_call("tool1") is True

    def test_opens_after_threshold(self):
        self.cb.on_failure("tool1")
        self.cb.on_failure("tool1")
        assert self.cb.before_call("tool1") is False
        assert self.cb.get_state("tool1") == CircuitState.OPEN

    def test_success_resets(self):
        self.cb.on_failure("tool1")
        self.cb.on_success("tool1")
        assert self.cb.get_state("tool1") == CircuitState.CLOSED

    def test_independent_per_tool(self):
        self.cb.on_failure("tool1")
        self.cb.on_failure("tool1")
        assert self.cb.before_call("tool2") is True


class TestToolPipeline:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="step1", description="Step 1")(_echo)
        self.registry.register(name="step2", description="Step 2")(_echo)

    def test_pipeline_sequential(self):
        pipeline = ToolPipeline(
            self.registry,
            [
                PipelineToolStep("step1", {"key": "value1"}),
                PipelineToolStep("step2", {"key": "value2"}),
            ],
            name="test-pipeline",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(pipeline.run())
        finally:
            loop.close()
        assert result["success"] is True
        assert len(result["steps"]) == 2
        assert result["name"] == "test-pipeline"

    def test_pipeline_failure_without_retry(self):
        self.registry.register(name="doomed", description="Always fails", max_retries=0)(_fail)
        pipeline = ToolPipeline(
            self.registry,
            [
                PipelineToolStep("step1", {"key": "ok"}),
                PipelineToolStep("doomed", {}, retry_on_failure=False),
                PipelineToolStep("step2", {"key": "never-runs"}),
            ],
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(pipeline.run())
        finally:
            loop.close()
        assert result["success"] is False
        assert len(result["steps"]) == 2  # step2 skipped


class TestToolDef:
    def test_to_openai_schema(self):
        td = ToolDef(
            name="test",
            description="A test tool",
            handler=_echo,
            schema={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        schema = td.to_openai_schema()
        assert schema["name"] == "test"
        assert "parameters" in schema

    def test_auto_inferred_schema(self):
        td = ToolDef(
            name="inferred",
            description="Auto schema",
            handler=_echo,
        )
        schema = td.to_openai_schema()
        assert "parameters" in schema
        props = schema["parameters"]["properties"]
        assert isinstance(props, dict)


class TestToolRegistryStats:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="a", description="A", category="cat1")(_echo)
        self.registry.register(name="b", description="B", category="cat2")(_echo)

    def test_stats(self):
        stats = self.registry.stats()
        assert stats["total_tools"] == 2
        assert "cat1" in stats["categories"]
        assert "cat2" in stats["categories"]


class TestToolResult:
    def test_success_property(self):
        r = ToolResult(tool_name="t", outcome=ToolOutcome.SUCCESS, output="ok")
        assert r.success is True

    def test_to_dict(self):
        r = ToolResult(tool_name="t", outcome=ToolOutcome.ERROR, error="bad")
        d = r.to_dict()
        assert d["tool"] == "t"
        assert d["success"] is False
        assert d["error"] == "bad"


# ── Extended Tests: Complex Schemas ─────────────────────────────────

class TestComplexSchemas:
    """ToolDef with complex parameter schemas (nested objects, arrays)."""

    def test_nested_object_schema(self):
        td = ToolDef(
            name="complex",
            description="Tool with nested schema",
            handler=_echo,
            schema={
                "type": "object",
                "properties": {
                    "config": {
                        "type": "object",
                        "properties": {
                            "host": {"type": "string"},
                            "port": {"type": "integer"},
                            "ssl": {"type": "boolean"},
                        },
                        "required": ["host"],
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["config"],
            },
        )
        schema = td.to_openai_schema()
        assert schema["name"] == "complex"
        params = schema["parameters"]
        assert params["type"] == "object"
        assert "config" in params["properties"]
        assert params["properties"]["config"]["type"] == "object"
        assert "host" in params["properties"]["config"]["properties"]
        assert "tags" in params["properties"]
        assert params["properties"]["tags"]["type"] == "array"
        assert "config" in params["required"]

    def test_array_of_objects_schema(self):
        td = ToolDef(
            name="items",
            description="Array of objects",
            handler=_echo,
            schema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "number"},
                            },
                        },
                    },
                },
            },
        )
        schema = td.to_openai_schema()
        items_schema = schema["parameters"]["properties"]["items"]
        assert items_schema["type"] == "array"
        assert items_schema["items"]["type"] == "object"


# ── Extended Tests: Default Parameter Values ────────────────────────

class TestDefaultParameterValues:
    @staticmethod
    async def _with_defaults(x: int, y: int = 10, name: str = "default") -> str:
        return json.dumps({"x": x, "y": y, "name": name})

    @pytest.mark.skip(reason="Partial work")
    def test_auto_inferred_schema_marks_defaults_optional(self):
        td = ToolDef(
            name="with_defaults",
            description="Has defaults",
            handler=self._with_defaults,
        )
        schema = td.to_openai_schema()
        required = schema["parameters"]["required"]
        # x has no default → required; y and name have defaults → not required
        assert "x" in required
        assert "y" not in required
        assert "name" not in required
        assert "y" in schema["parameters"]["properties"]
        assert schema["parameters"]["properties"]["y"]["type"] == "integer"


# ── Extended Tests: Tool Error Results ──────────────────────────────

class TestToolErrorResults:
    def test_call_returns_error_result_on_exception(self):
        registry = ToolRegistry()
        registry.register(name="failing", description="Always fails", max_retries=0)(_fail)
        result = _sync_call(registry, "failing", {})
        assert result.success is False
        assert result.outcome == ToolOutcome.ERROR
        assert "intentional failure" in result.error

    def test_call_returns_circuit_open_result(self):
        registry = ToolRegistry()
        registry.register(name="cb-test", description="CB test")(_fail)
        cb = registry._circuit_breaker
        # Force circuit open
        for _ in range(cb.failure_threshold):
            cb.on_failure("cb-test")
        result = _sync_call(registry, "cb-test", {})
        assert result.success is False
        assert result.outcome == ToolOutcome.CIRCUIT_OPEN
        assert "Circuit breaker open" in result.error


# ── Extended Tests: Tool Stats Aggregation ──────────────────────────

class TestToolStatsAggregation:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="s1", description="Stats tool 1", category="cat-a")(_echo)
        self.registry.register(name="s2", description="Stats tool 2", category="cat-b")(_echo)

    def test_stats_includes_call_counts(self):
        _sync_call(self.registry, "s1", {"k": "v"})
        _sync_call(self.registry, "s1", {"k": "v2"})
        _sync_call(self.registry, "s2", {"k": "v"})
        stats = self.registry.stats()
        assert stats["total_tools"] == 2
        assert stats["total_calls"] == 3
        assert "cat-a" in stats["categories"]

    def test_stats_includes_open_circuits(self):
        cb = self.registry._circuit_breaker
        # Force circuit open for s1
        for _ in range(cb.failure_threshold):
            cb.on_failure("s1")
        stats = self.registry.stats()
        assert stats["open_circuits"] == 1

    def test_stats_after_no_calls(self):
        stats = self.registry.stats()
        assert stats["total_calls"] == 0
        assert stats["open_circuits"] == 0


# ── Extended Tests: Category Management ─────────────────────────────

class TestCategoryManagement:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="web-search", description="Search web", category="web")(_echo)
        self.registry.register(name="web-fetch", description="Fetch URL", category="web")(_echo)
        self.registry.register(name="file-read", description="Read file", category="file")(_echo)
        self.registry.register(name="file-write", description="Write file", category="file")(_echo)
        self.registry.register(name="terminal", description="Run command", category="system")(_echo)

    def test_list_all_categories_present(self):
        tools = self.registry.list_tools()
        categories = set(t["category"] for t in tools)
        assert categories == {"web", "file", "system"}

    def test_filter_by_web_category(self):
        tools = self.registry.list_tools(category="web")
        assert len(tools) == 2
        names = [t["name"] for t in tools]
        assert "web-search" in names
        assert "web-fetch" in names

    def test_filter_by_system_category(self):
        tools = self.registry.list_tools(category="system")
        assert len(tools) == 1
        assert tools[0]["name"] == "terminal"

    def test_default_category_is_general(self):
        self.registry.register(name="misc", description="Misc tool")(_echo)
        tools = self.registry.list_tools(category="general")
        assert len(tools) == 1
        assert tools[0]["name"] == "misc"


# ── Extended Tests: Pipeline with Dependent Outputs ─────────────────

class TestPipelineDependentOutputs:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(name="get-id", description="Get an ID")(_echo)
        self.registry.register(name="use-id", description="Use an ID")(_echo)

    def test_pipeline_with_input_from_previous_step(self):
        pipeline = ToolPipeline(
            self.registry,
            [
                PipelineToolStep("get-id", {"name": "alice"}),
                PipelineToolStep(
                    "use-id",
                    {"extra": "data"},
                    input_from="get-id",
                    input_key="output",
                ),
            ],
            name="dependent-pipeline",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(pipeline.run())
        finally:
            loop.close()
        assert result["success"] is True
        assert len(result["steps"]) == 2
        # Context should have outputs from both steps
        assert "get-id" in result["context"]
        assert "use-id" in result["context"]

    def test_pipeline_input_from_missing_step_ignored(self):
        """When input_from references a step that hasn't run, it's gracefully ignored."""
        pipeline = ToolPipeline(
            self.registry,
            [
                PipelineToolStep("get-id", {"name": "bob"}),
                PipelineToolStep(
                    "use-id",
                    {"extra": "data"},
                    input_from="nonexistent-step",
                    input_key="output",
                ),
            ],
            name="missing-ref-pipeline",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(pipeline.run())
        finally:
            loop.close()
        assert result["success"] is True
        assert len(result["steps"]) == 2

    def test_pipeline_input_from_failed_step_skipped(self):
        self.registry.register(name="will-fail", description="Fails", max_retries=0)(_fail)
        pipeline = ToolPipeline(
            self.registry,
            [
                PipelineToolStep("will-fail", {}, retry_on_failure=False),
                PipelineToolStep(
                    "use-id",
                    {"extra": "never-injected"},
                    input_from="will-fail",
                    input_key="output",
                ),
            ],
            name="fail-pipeline",
        )
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(pipeline.run())
        finally:
            loop.close()
        # First step fails, second step skipped because retry_on_failure=False
        assert result["success"] is False
        assert len(result["steps"]) == 1


# ── Extended Tests: Tool Search with Description Matching ───────────

class TestToolDescriptionSearch:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            name="web-search",
            description="Search the internet for information using Google API",
            tags=["web", "search"],
        )(_echo)
        self.registry.register(
            name="file-search",
            description="Search local files for text patterns",
            tags=["file", "search"],
        )(_echo)
        self.registry.register(
            name="memory-recall",
            description="Recall information from long-term memory storage",
            tags=["memory"],
        )(_echo)

    def test_search_description_partial_word_match(self):
        results = self.registry.search("internet")
        assert len(results) >= 1
        assert results[0]["name"] == "web-search"
        assert results[0]["score"] > 0

    def test_search_description_case_insensitive(self):
        results = self.registry.search("GOOGLE")
        assert len(results) >= 1
        assert results[0]["name"] == "web-search"

    def test_search_matches_multiple_criteria_scores_higher(self):
        """Tool matching name AND description AND tags scores higher."""
        results = self.registry.search("search")
        assert len(results) == 2
        # web-search and file-search both match; web-search matches name+tag
        scores = {r["name"]: r["score"] for r in results}
        assert scores["web-search"] >= scores.get("file-search", 0)

    def test_search_no_match_returns_empty(self):
        results = self.registry.search("zzzxnonexistent")
        assert len(results) == 0

    def test_search_results_sorted_by_score(self):
        results = self.registry.search("search")
        if len(results) >= 2:
            for i in range(len(results) - 1):
                assert results[i]["score"] >= results[i + 1]["score"]


# ── Extended Tests: Concurrent Tool Calls ───────────────────────────

class TestConcurrentToolCalls:
    async def _quick_echo(self, **kwargs) -> str:
        return json.dumps(kwargs)

    def test_concurrent_calls_independent(self):
        registry = ToolRegistry()
        registry.register(name="echo-a", description="Echo A")(_echo)
        registry.register(name="echo-b", description="Echo B")(_echo)
        registry.register(name="echo-c", description="Echo C")(_echo)

        async def run_all():
            return await asyncio.gather(
                registry.call("echo-a", {"key": "a"}),
                registry.call("echo-b", {"key": "b"}),
                registry.call("echo-c", {"key": "c"}),
            )

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(run_all())
        finally:
            loop.close()

        assert len(results) == 3
        for r in results:
            assert r.success is True
            assert r.outcome == ToolOutcome.SUCCESS

    def test_concurrent_calls_mixed_results(self):
        registry = ToolRegistry()
        registry.register(name="good", description="Works")(_echo)
        registry.register(name="bad", description="Fails", max_retries=0)(_fail)

        async def run_all():
            return await asyncio.gather(
                registry.call("good", {"k": "v"}),
                registry.call("bad", {}),
                return_exceptions=False,  # Let ToolResult handle errors
            )

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(run_all())
        finally:
            loop.close()

        assert results[0].success is True
        assert results[1].success is False

    @pytest.mark.skip(reason="Partial work")
    def test_concurrent_calls_share_circuit_breaker(self):
        """Concurrent calls to the same tool share circuit breaker state."""
        registry = ToolRegistry()
        registry.register(name="shared-cb", description="Shared CB")(_fail)
        cb = registry._circuit_breaker
        for _ in range(cb.failure_threshold):
            cb.on_failure("shared-cb")

        async def run_call():
            return await registry.call("shared-cb", {})

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(asyncio.gather(
                run_call(), run_call(), run_call(),
            ))
        finally:
            loop.close()

        for r in results:
            assert r.outcome == ToolOutcome.CIRCUIT_OPEN


# ── Extended Tests: Tool Registration Edge Cases ────────────────────

class TestRegistrationEdgeCases:
    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_without_name_uses_function_name(self):
        @self.registry.register(description="No name given")
        async def my_special_tool(x: int) -> str:
            return str(x)

        assert self.registry.get("my_special_tool") is not None
        assert self.registry.get("my_special_tool").description == "No name given"

    def test_register_with_env_requirements(self):
        import os
        os.environ["DRAGON_TEST_REQUIRED_ENV"] = "1"
        try:
            self.registry.register(
                name="env-tool",
                description="Needs env",
                requires_env=["DRAGON_TEST_REQUIRED_ENV"],
            )(_echo)
            result = _sync_call(self.registry, "env-tool", {})
            assert result.success is True
        finally:
            del os.environ["DRAGON_TEST_REQUIRED_ENV"]

    def test_register_env_requirement_missing_blocks_call(self):
        self.registry.register(
            name="env-missing-tool",
            description="Missing env",
            requires_env=["DRAGON_NONEXISTENT_ENV_VAR_XYZ"],
        )(_echo)
        result = _sync_call(self.registry, "env-missing-tool", {})
        assert result.success is False
        assert "Missing requirements" in result.error

    def test_unregister_clears_tool(self):
        self.registry.register(name="to-remove", description="Temp")(_echo)
        assert self.registry.unregister("to-remove") is True
        assert self.registry.get("to-remove") is None
        assert len(self.registry.list_tools()) == 0
