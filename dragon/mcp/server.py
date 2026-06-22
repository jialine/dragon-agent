"""
Dragon MCP Server — Expose Self-Evolving Skills + Multi-Model Debate over MCP
=============================================================================

What makes this different from Hermes/OpenClaw MCP:

1. **Self-Evolving Tools** — tools that track success rate and auto-improve
2. **Multi-Model Debate** — expert consultation exposed as MCP tools
3. **Semantic Skill Discovery** — find skills by meaning, not keywords
4. **Knowledge Graph** — entity-relation graph queries over MCP

Protocol: JSON-RPC 2.0 over stdio (MCP 2024-11-05)

Usage (from Claude Desktop config)::

    {
      "mcpServers": {
        "dragon": {
          "command": "python",
          "args": ["-m", "dragon.mcp.server"]
        }
      }
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from dragon.mcp.protocol import (
    StdioTransport, JSONRPCResponse, JSONRPCError,
    MCPTool, MCPResource, MCPPrompt,
    ServerCapabilities, Implementation, InitializeResult,
    MCP_VERSION,
    METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR, INVALID_REQUEST,
)

logger = logging.getLogger("dragon.mcp.server")

# ────────────────────────────────────────────────────────────────────
# Dragon MCPServer — The actual server
# ────────────────────────────────────────────────────────────────────


class DragonMCPServer:
    """MCP Server that exposes Dragon's self-evolving skills and tools.

    What's unique vs Hermes MCP:
    - Skills exposed as MCP Resources with version history
    - Expert consultation as MCP Tools (multi-model debate)
    - Tools track success rate and can auto-evolve
    - Semantic skill search as an MCP Tool
    """

    def __init__(
        self,
        name: str = "dragon-agent",
        version: str = "1.0.0",
    ) -> None:
        self.name = name
        self.version = version
        self.transport = StdioTransport()

        # Dragon components (lazy-loaded)
        self._skill_engine: Optional[Any] = None
        self._tool_registry: Optional[Any] = None
        self._router: Optional[Any] = None
        self._dispatcher: Optional[Any] = None
        self._consult_engine: Optional[Any] = None
        self._memory_graph: Optional[Any] = None

        # MCP handler registry
        self._handlers: Dict[str, Callable] = {}
        self._register_handlers()

        # Track the initialize state
        self._initialized = False
        self._client_capabilities: Dict = {}

    # ── Handler Registry ───────────────────────────────────────────

    def _register_handlers(self) -> None:
        """Register MCP protocol method handlers."""
        self._handlers["initialize"] = self._handle_initialize
        self._handlers["ping"] = self._handle_ping
        self._handlers["tools/list"] = self._handle_tools_list
        self._handlers["tools/call"] = self._handle_tools_call
        self._handlers["resources/list"] = self._handle_resources_list
        self._handlers["resources/read"] = self._handle_resources_read
        self._handlers["prompts/list"] = self._handle_prompts_list
        self._handlers["prompts/get"] = self._handle_prompts_get

    # ── Lazy Init ──────────────────────────────────────────────────

    def _ensure_skill_engine(self):
        if self._skill_engine is None:
            try:
                from dragon.skill import SkillEngine
                self._skill_engine = SkillEngine(
                    skills_dir=os.path.expanduser("~/.dragon/skills"),
                    auto_evolve=True,
                )
                logger.info("SkillEngine loaded")
            except Exception as e:
                logger.warning("SkillEngine unavailable: %s", e)

    def _ensure_tool_registry(self):
        if self._tool_registry is None:
            try:
                from dragon.tool import ToolRegistry
                from dragon.tool.builtins import register_builtins
                self._tool_registry = ToolRegistry()
                register_builtins(self._tool_registry)
                logger.info("ToolRegistry loaded with %d tools", len(self._tool_registry._tools))
            except Exception as e:
                logger.warning("ToolRegistry unavailable: %s", e)

    def _ensure_router(self):
        if self._router is None:
            try:
                from dragon.router import DragonRouter
                from dragon.config import DragonConfig
                cfg = DragonConfig.load()
                self._router = DragonRouter(
                    model_path=cfg.router.model_path,
                    n_threads=cfg.router.n_threads,
                )
                self._router.initialize()
                logger.info("Router loaded")
            except Exception as e:
                logger.warning("Router unavailable: %s", e)

    def _ensure_consult(self):
        if self._consult_engine is None:
            try:
                from dragon.consult import ExpertConsultation
                self._ensure_tool_registry()
                if self._tool_registry:
                    self._consult_engine = ExpertConsultation(
                        dispatcher=None,  # will use direct API calls
                        jury=None,
                        cost_optimizer=None,
                    )
                    logger.info("ConsultEngine loaded")
            except Exception as e:
                logger.warning("ConsultEngine unavailable: %s", e)

    def _ensure_memory(self):
        if self._memory_graph is None:
            try:
                from dragon.memory import MemoryGraph
                from dragon.config import DragonConfig
                cfg = DragonConfig.load()
                self._memory_graph = MemoryGraph(
                    persist_dir=cfg.memory.persist_dir,
                    embedding_model=cfg.memory.embedding_model,
                    search_top_k=cfg.memory.search_top_k,
                )
                self._memory_graph.initialize()
                logger.info("MemoryGraph loaded")
            except Exception as e:
                logger.warning("MemoryGraph unavailable: %s", e)

    # ── MCP Handlers ───────────────────────────────────────────────

    async def _handle_initialize(self, params: Dict) -> Dict:
        """Handle initialize request."""
        self._client_capabilities = params.get("capabilities", {})
        client_info = params.get("clientInfo", {})

        logger.info(
            "MCP initialize: client=%s v%s, protocol=%s",
            client_info.get("name", "unknown"),
            client_info.get("version", "?"),
            params.get("protocolVersion", "?"),
        )

        return {
            "protocolVersion": MCP_VERSION,
            "capabilities": {
                "tools": {},       # Dragon exposes tools
                "resources": {},   # Skills as resources
                "prompts": {},     # Expert consultation prompts
            },
            "serverInfo": {
                "name": self.name,
                "version": self.version,
            },
        }

    async def _handle_ping(self, params: Dict) -> Dict:
        return {}

    # ── Tools ──────────────────────────────────────────────────────

    async def _handle_tools_list(self, params: Dict) -> Dict:
        """List all available MCP tools."""
        self._ensure_tool_registry()
        self._ensure_skill_engine()
        self._ensure_consult()

        tools = []

        # 1. Dragon-specific tools (self-evolving, unique to Dragon)
        tools.append({
            "name": "dragon.skills.search",
            "description": "Semantic search for self-evolving skills. Finds the best skill for a task using embedding similarity. Skills track their own success rate and auto-improve.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What do you need help with?"},
                    "top_k": {"type": "integer", "description": "Number of results (default: 3)"},
                },
                "required": ["query"],
            },
        })

        tools.append({
            "name": "dragon.skills.evolve",
            "description": "Evolve a skill to a new version. The skill tracks success/failure across versions and auto-rolls back if the new version is worse.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "new_content": {"type": "string", "description": "Improved skill content"},
                    "reason": {"type": "string", "description": "Why this evolution is needed"},
                },
                "required": ["name", "new_content"],
            },
        })

        tools.append({
            "name": "dragon.consult.assess",
            "description": "Assess problem difficulty (0-10). If difficulty >= 7, strongly recommends expert consultation with multiple models debating the answer.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The problem to assess"},
                },
                "required": ["query"],
            },
        })

        tools.append({
            "name": "dragon.consult.debate",
            "description": "Multi-model expert consultation. THREE independent models debate the problem, vote, and synthesize a consensus answer. Use when dragon.consult.assess returns difficulty >= 7.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Complex problem requiring expert debate"},
                    "industry": {"type": "string", "description": "Domain: finance/medical/legal/education/general"},
                },
                "required": ["query"],
            },
        })

        tools.append({
            "name": "dragon.memory.search",
            "description": "Semantic search across the knowledge graph of entities and relations. Returns relevant entities with their 1-hop neighbors.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What knowledge are you looking for?"},
                    "max_entities": {"type": "integer", "description": "Max entities (default: 5)"},
                },
                "required": ["query"],
            },
        })

        tools.append({
            "name": "dragon.memory.graph",
            "description": "Query the entity-relation knowledge graph. Supports: NEIGHBORS <id>, PATH <src> TO <dst>, ALL ENTITIES, ALL RELATIONS.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Graph query (e.g., 'NEIGHBORS entity_id', 'PATH a TO b')"},
                },
                "required": ["query"],
            },
        })

        # 2. Standard built-in tools (from Dragon's ToolRegistry)
        if self._tool_registry:
            for name, tool in self._tool_registry._tools.items():
                tools.append({
                    "name": f"dragon.{name}",
                    "description": tool.description,
                    "inputSchema": tool.schema or {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                })

        return {"tools": tools}

    async def _handle_tools_call(self, params: Dict) -> Dict:
        """Call a specific tool."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Route to the appropriate handler
        if tool_name == "dragon.skills.search":
            return await self._call_skill_search(arguments)
        elif tool_name == "dragon.skills.evolve":
            return await self._call_skill_evolve(arguments)
        elif tool_name == "dragon.consult.assess":
            return await self._call_consult_assess(arguments)
        elif tool_name == "dragon.consult.debate":
            return await self._call_consult_debate(arguments)
        elif tool_name == "dragon.memory.search":
            return await self._call_memory_search(arguments)
        elif tool_name == "dragon.memory.graph":
            return await self._call_memory_graph(arguments)
        elif tool_name.startswith("dragon."):
            # Built-in tool: strip prefix and call via ToolRegistry
            builtin_name = tool_name[6:]  # remove "dragon."
            return await self._call_builtin_tool(builtin_name, arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    # ── Tool Implementations ───────────────────────────────────────

    async def _call_skill_search(self, args: Dict) -> Dict:
        self._ensure_skill_engine()
        if self._skill_engine is None:
            return {"content": [{"type": "text", "text": "Skill engine not available"}]}

        query = args.get("query", "")
        top_k = args.get("top_k", 3)
        matches = await self._skill_engine.discover(query, top_k=top_k)

        if not matches:
            return {"content": [{"type": "text", "text": f"No skills found for: {query}"}]}

        lines = [f"# Skills matching: {query}\n"]
        for m in matches:
            s = m.skill
            lines.append(f"## {s.name} (similarity: {m.similarity:.2f}, success rate: {s.success_rate:.0%})")
            lines.append(f"**Version:** {s.meta.version} | **Status:** {s.meta.status}")
            lines.append(f"**Description:** {s.meta.description}")
            lines.append(f"**Tags:** {', '.join(s.meta.tags)}")
            lines.append("")
            lines.append(s.content[:500])
            lines.append("---")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    async def _call_skill_evolve(self, args: Dict) -> Dict:
        self._ensure_skill_engine()
        if self._skill_engine is None:
            return {"content": [{"type": "text", "text": "Skill engine not available"}]}

        name = args.get("name", "")
        new_content = args.get("new_content", "")
        reason = args.get("reason", "MCP-triggered evolution")

        skill = self._skill_engine.get(name)
        if skill is None:
            return {"content": [{"type": "text", "text": f"Skill '{name}' not found"}]}

        old_ver = skill.meta.version
        old_rate = skill.success_rate
        new_ver = skill.evolve(new_content, reason)

        return {"content": [{"type": "text", "text": (
            f"✅ Skill '{name}' evolved:\n"
            f"- Old: v{old_ver} (success rate: {old_rate:.0%})\n"
            f"- New: v{new_ver}\n"
            f"- Reason: {reason}\n"
            f"- Version history now has {len(skill._versions)} versions"
        )}]}

    async def _call_consult_assess(self, args: Dict) -> Dict:
        self._ensure_router()
        self._ensure_consult()

        query = args.get("query", "")

        # Try router classification first
        difficulty_score = 5.0
        industry = "general"

        if self._router and self._router.status.value == "loaded":
            try:
                classification = await self._router.classify(query)
                difficulty_score = classification.difficulty_score
                industry = classification.industry
            except Exception:
                pass

        # Build assessment
        if difficulty_score <= 3:
            level = "简单"
            recommendation = "直接回答即可，不需要专家会诊"
            success_estimate = "95%+"
        elif difficulty_score <= 6:
            level = "中等"
            recommendation = "可以尝试，但建议准备备选方案"
            success_estimate = "70-85%"
        elif difficulty_score <= 7:
            level = "困难"
            recommendation = "⚠️ 强烈建议启动专家会诊（3个模型并行辩论）"
            success_estimate = "50-65%"
        elif difficulty_score <= 8:
            level = "很困难"
            recommendation = "🔴 必须启动专家会诊，单模型几乎不可能解决"
            success_estimate = "30-45%"
        else:
            level = "极困难"
            recommendation = "🔴 即使专家会诊成功率也有限。建议重新表述问题或接受部分解决方案"
            success_estimate = "10-25%"

        return {"content": [{"type": "text", "text": (
            f"# Problem Assessment\n\n"
            f"**Query:** {query}\n"
            f"**Difficulty:** {difficulty_score}/10 ({level})\n"
            f"**Industry:** {industry}\n"
            f"**Success Estimate:** {success_estimate}\n"
            f"**Recommendation:** {recommendation}\n\n"
            f"{'👉 Use dragon.consult.debate to start expert consultation' if difficulty_score >= 7 else '✅ Safe to proceed with standard tools'}"
        )}]}

    async def _call_consult_debate(self, args: Dict) -> Dict:
        self._ensure_consult()

        query = args.get("query", "")
        industry = args.get("industry", "general")

        if self._consult_engine is None:
            return {"content": [{"type": "text", "text": (
                "Expert consultation engine is not available.\n\n"
                "This feature requires API keys for at least 3 models. Set:\n"
                "- DEEPSEEK_API_KEY\n"
                "- OPENAI_API_KEY\n"
                "- ANTHROPIC_API_KEY\n\n"
                "Then restart the MCP server."
            )}]}

        try:
            result = await self._consult_engine.consult(query, industry)
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Consultation failed: {e}"}]}

        lines = [
            f"# Expert Consultation Result\n",
            f"**Query:** {query}",
            f"**Solved:** {'✅ Yes' if result.solved else '❌ No'}",
            f"**Confidence:** {result.confidence:.0%}",
            f"**Models used:** {', '.join(result.panel_used)}",
            f"**Debate rounds:** {result.debate_rounds}",
            f"**Cost:** ${result.cost_usd:.4f}",
            "",
        ]

        if result.solved:
            lines.append("## Solution")
            lines.append(result.solution)
            lines.append("")
        else:
            lines.append(f"## Cannot Solve: {result.cannot_solve_reason}")
            lines.append("")

        if result.minority_opinions:
            lines.append("## Minority Opinions")
            for opinion in result.minority_opinions:
                lines.append(f"- {opinion}")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    async def _call_memory_search(self, args: Dict) -> Dict:
        self._ensure_memory()

        query = args.get("query", "")
        max_entities = args.get("max_entities", 5)

        if self._memory_graph is None or not self._memory_graph._initialized:
            return {"content": [{"type": "text", "text": "Knowledge graph not available"}]}

        ctx = self._memory_graph.get_context(query, max_entities=max_entities)

        if ctx["total_entities"] == 0:
            return {"content": [{"type": "text", "text": f"No knowledge found for: {query}"}]}

        lines = [f"# Knowledge Graph: {query}\n"]
        lines.append(f"Found {ctx['total_entities']} relevant entities and {len(ctx['relations'])} relations.\n")

        for entity in ctx["relevant_entities"]:
            lines.append(f"## {entity['name']} ({entity['type']})")
            lines.append(f"Importance: {entity['importance']:.1f}")
            if entity.get("properties"):
                for k, v in entity["properties"].items():
                    lines.append(f"- {k}: {v}")
            if entity.get("neighbors"):
                lines.append("**Related:**")
                for n in entity["neighbors"][:5]:
                    lines.append(f"- {n['name']} [{n['relation']}]")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    async def _call_memory_graph(self, args: Dict) -> Dict:
        self._ensure_memory()

        query = args.get("query", "")

        if self._memory_graph is None:
            return {"content": [{"type": "text", "text": "Knowledge graph not available"}]}

        result = self._memory_graph.query_graph(query)

        if "error" in result:
            return {"content": [{"type": "text", "text": f"Query error: {result['error']}\n\nSupported: NEIGHBORS <id>, PATH <src> TO <dst>, ALL ENTITIES, ALL RELATIONS"}]}

        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}

    async def _call_builtin_tool(self, tool_name: str, args: Dict) -> Dict:
        self._ensure_tool_registry()
        if self._tool_registry is None:
            return {"content": [{"type": "text", "text": "Tool registry not available"}]}

        try:
            result = await self._tool_registry.call(tool_name, args)
            output = str(result.output) if result.output else result.error
            return {"content": [{"type": "text", "text": output}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Tool error: {e}"}]}

    # ── Resources (Skills as MCP Resources) ────────────────────────

    async def _handle_resources_list(self, params: Dict) -> Dict:
        """List skills as MCP resources."""
        self._ensure_skill_engine()

        resources = []
        if self._skill_engine:
            for skill in self._skill_engine._skills.values():
                resources.append({
                    "uri": f"dragon://skills/{skill.name}",
                    "name": skill.name,
                    "description": f"{skill.meta.description} (v{skill.meta.version}, {skill.success_rate:.0%} success)",
                    "mimeType": "text/markdown",
                })

        return {"resources": resources}

    async def _handle_resources_read(self, params: Dict) -> Dict:
        """Read a skill resource."""
        uri = params.get("uri", "")

        if not uri.startswith("dragon://skills/"):
            raise ValueError(f"Unknown resource URI: {uri}")

        skill_name = uri.replace("dragon://skills/", "")
        self._ensure_skill_engine()

        if self._skill_engine is None:
            raise ValueError("Skill engine not available")

        skill = self._skill_engine.get(skill_name)
        if skill is None:
            raise ValueError(f"Skill not found: {skill_name}")

        return {
            "contents": [{
                "uri": uri,
                "mimeType": "text/markdown",
                "text": (
                    f"# {skill.name} (v{skill.meta.version})\n\n"
                    f"**Success Rate:** {skill.success_rate:.0%} | "
                    f"**Uses:** {skill.total_uses} | "
                    f"**Status:** {skill.meta.status}\n\n"
                    f"{skill.content}"
                ),
            }]
        }

    # ── Prompts (Expert Consultation) ──────────────────────────────

    async def _handle_prompts_list(self, params: Dict) -> Dict:
        """List expert consultation prompts."""
        return {
            "prompts": [
                {
                    "name": "expert-consultation",
                    "description": "Start a multi-model expert debate for complex problems. Three models independently analyze, debate, and synthesize a consensus.",
                    "arguments": [
                        {
                            "name": "query",
                            "description": "The complex problem to solve",
                            "required": True,
                        },
                        {
                            "name": "industry",
                            "description": "Domain: finance, medical, legal, education, or general",
                            "required": False,
                        },
                    ],
                },
                {
                    "name": "skill-evolution",
                    "description": "Review a skill's performance and propose improvements based on execution history.",
                    "arguments": [
                        {
                            "name": "skill_name",
                            "description": "Name of the skill to review",
                            "required": True,
                        },
                    ],
                },
            ]
        }

    async def _handle_prompts_get(self, params: Dict) -> Dict:
        """Get a specific prompt."""
        prompt_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if prompt_name == "expert-consultation":
            query = arguments.get("query", "")
            industry = arguments.get("industry", "general")
            return {
                "messages": [{
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": (
                            f"You are about to start a multi-model expert consultation.\n\n"
                            f"**Problem:** {query}\n"
                            f"**Domain:** {industry}\n\n"
                            f"This will invoke 3 independent models, each analyzing from different angles, "
                            f"then debating to reach a consensus. Use dragon.consult.debate to begin."
                        ),
                    },
                }],
            }

        elif prompt_name == "skill-evolution":
            skill_name = arguments.get("skill_name", "")
            self._ensure_skill_engine()

            if self._skill_engine:
                skill = self._skill_engine.get(skill_name)
                if skill:
                    versions = skill.get_version_history()
                    return {
                        "messages": [{
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": (
                                    f"# Skill Evolution Review: {skill_name}\n\n"
                                    f"**Current version:** {skill.meta.version}\n"
                                    f"**Success rate:** {skill.success_rate:.0%}\n"
                                    f"**Total uses:** {skill.total_uses}\n"
                                    f"**Status:** {skill.meta.status}\n\n"
                                    f"## Version History\n"
                                    f"{json.dumps(versions, indent=2)}\n\n"
                                    f"Review this skill and propose improvements. "
                                    f"Use dragon.skills.evolve to apply changes."
                                ),
                            },
                        }],
                    }

            raise ValueError(f"Skill not found: {skill_name}")

        else:
            raise ValueError(f"Unknown prompt: {prompt_name}")

    # ── Event Loop ─────────────────────────────────────────────────

    async def run(self) -> None:
        """Main event loop — read JSON-RPC requests and dispatch."""
        logger.info("Dragon MCP Server starting (v%s)", self.version)

        while True:
            message = self.transport.receive()
            if message is None:
                continue  # empty line or parse error already handled

            method = message.get("method")
            msg_id = message.get("id")
            params = message.get("params", {})

            # Handle notification (no id)
            if msg_id is None:
                if method == "initialized":
                    self._initialized = True
                    logger.info("MCP client initialized, server ready")
                elif method == "notifications/initialized":
                    self._initialized = True
                continue

            # Handle request
            try:
                handler = self._handlers.get(method)
                if handler is None:
                    self.transport.send_error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
                    continue

                result = await handler(params)
                self.transport.send_response(msg_id, result)

            except ValueError as e:
                self.transport.send_error(msg_id, INVALID_PARAMS, str(e))
            except Exception as e:
                logger.exception("Error handling %s", method)
                self.transport.send_error(
                    msg_id, INTERNAL_ERROR,
                    f"Internal error: {str(e)}",
                    {"traceback": traceback.format_exc()},
                )


# ────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────

def main():
    """Entry point for `python -m dragon.mcp.server`."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    server = DragonMCPServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("MCP server stopped")
    except EOFError:
        logger.info("MCP client disconnected")


if __name__ == "__main__":
    main()
