"""
Panda MCP — JSON-RPC 2.0 Protocol Types and Transport
======================================================

Implements the Model Context Protocol (MCP) specification over stdio transport.
MCP uses JSON-RPC 2.0 as the message format.

Reference: https://spec.modelcontextprotocol.io/
"""
from __future__ import annotations

import json
import sys
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger("panda.mcp")


# ────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 Types
# ────────────────────────────────────────────────────────────────────

@dataclass
class JSONRPCRequest:
    jsonrpc: str = "2.0"
    id: Union[int, str, None] = None
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JSONRPCResponse:
    jsonrpc: str = "2.0"
    id: Union[int, str, None] = None
    result: Any = None
    error: Optional[JSONRPCError] = None


@dataclass
class JSONRPCError:
    code: int
    message: str
    data: Any = None


# JSON-RPC 2.0 standard error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# ────────────────────────────────────────────────────────────────────
# MCP Protocol Types
# ────────────────────────────────────────────────────────────────────

MCP_VERSION = "2024-11-05"

@dataclass
class ServerCapabilities:
    tools: Optional[Dict] = None
    resources: Optional[Dict] = None
    prompts: Optional[Dict] = None


@dataclass
class Implementation:
    name: str
    version: str


@dataclass
class InitializeResult:
    protocolVersion: str
    capabilities: ServerCapabilities
    serverInfo: Implementation


@dataclass
class MCPTool:
    name: str
    description: str
    inputSchema: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResource:
    uri: str
    name: str
    description: str = ""
    mimeType: str = "text/markdown"


@dataclass
class MCPPrompt:
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# Transport — stdio (stdin/stdout)
# ────────────────────────────────────────────────────────────────────


class StdioTransport:
    """JSON-RPC 2.0 transport over stdin/stdout.

    Reads JSON-RPC requests from stdin, writes responses to stdout.
    Stderr is reserved for logging (not protocol messages).
    """

    def send(self, message: Dict[str, Any]) -> None:
        """Send a JSON-RPC message to stdout."""
        data = json.dumps(message, ensure_ascii=False)
        sys.stdout.write(data + "\n")
        sys.stdout.flush()
        logger.debug("→ %s", data[:200])

    def receive(self) -> Optional[Dict[str, Any]]:
        """Read a JSON-RPC message from stdin. Blocks until a line is available."""
        try:
            line = sys.stdin.readline()
            if not line:
                return None  # EOF
            line = line.strip()
            if not line:
                return None
            logger.debug("← %s", line[:200])
            return json.loads(line)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON-RPC message: %s", e)
            self.send({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": PARSE_ERROR, "message": f"Parse error: {e}"},
            })
            return None
        except EOFError:
            return None

    def send_response(self, id: Union[int, str], result: Any) -> None:
        self.send({"jsonrpc": "2.0", "id": id, "result": result})

    def send_error(self, id: Union[int, str, None], code: int, message: str, data: Any = None) -> None:
        error = {"code": code, "message": message}
        if data:
            error["data"] = data
        self.send({"jsonrpc": "2.0", "id": id, "error": error})

    def send_notification(self, method: str, params: Dict[str, Any] = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self.send(msg)
