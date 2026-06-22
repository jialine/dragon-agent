"""
Dragon MCP Server — Expose self-evolving skills + multi-model debate over MCP.

What's unique vs Hermes/OpenClaw MCP:
1. Skills that auto-improve from execution experience
2. Multi-model expert debate as a tool
3. Knowledge graph queries over MCP
4. Semantic skill discovery by meaning, not keywords

Usage::

    python -m dragon.mcp.server

    # Or from Claude Desktop config:
    {
      "mcpServers": {
        "dragon": {
          "command": "python",
          "args": ["-m", "dragon.mcp.server"]
        }
      }
    }
"""
from dragon.mcp.server import DragonMCPServer, main

__all__ = ["DragonMCPServer", "main"]
