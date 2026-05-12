"""MCP bridge — external process tools via stdin/stdout transport.

Scans tools/mcp/*.json for MCP server configs, spawns subprocesses,
discovers tools via MCP list_tools, and registers them in ToolRegistry.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from qd_evolve.config import MCPServerConfig
from qd_evolve.logger import logger
from qd_evolve.tools import ToolRegistry, get_registry
from tools.bridge import BridgeManager

MCP_DIR = Path.cwd() / "tools" / "mcp"


# ── discovery ────────────────────────────────────────────────────

def discover_mcp_servers(_settings: Any = None) -> list[MCPServerConfig]:
    """Scan tools/mcp/*.json for MCP server configs."""
    configs: list[MCPServerConfig] = []
    if not MCP_DIR.exists():
        logger.debug("MCP: dir %s not found, skipping", MCP_DIR)
        return configs

    for json_file in sorted(MCP_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            servers = _extract_servers(data, json_file.stem)
            for name, srv in servers.items():
                config = MCPServerConfig(
                    name=name,
                    command=srv.get("command", ""),
                    args=srv.get("args", []),
                    env=srv.get("env", {}),
                )
                configs.append(config)
                logger.info("MCP: discovered server '%s' from %s", name, json_file.name)
        except Exception:
            logger.exception("MCP: failed to parse %s", json_file.name)
    return configs


def _extract_servers(data: dict, fallback_name: str) -> dict[str, dict]:
    """Extract server entries from various JSON formats."""
    if "mcpServers" in data:
        return data["mcpServers"]
    if "mcp" in data and isinstance(data["mcp"], dict):
        if "servers" in data["mcp"]:
            return data["mcp"]["servers"]
    if "command" in data:
        return {fallback_name: data}
    return {}


# ── MCPToolBridge ─────────────────────────────────────────────────

class MCPToolBridge:
    """Manages one MCP server — spawns subprocess, discovers tools, dispatches calls."""

    def __init__(self, config: MCPServerConfig, registry: ToolRegistry | None = None) -> None:
        self.config = config
        self._registry = registry or get_registry()
        self._session: Any = None
        self._stdio_context: Any = None
        self._session_context: Any = None
        self.tool_names: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
        """Start the subprocess, initialize MCP session, register tools."""
        self._loop = asyncio.new_event_loop()
        connected = threading.Event()
        error: list[Exception] = []

        def _run():
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._async_connect())
            except Exception as e:
                error.append(e)
            finally:
                connected.set()
            if not error:
                self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        connected.wait(timeout=60)

        if error:
            raise error[0]

    async def _async_connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        import os as _os
        merged_env = {**_os.environ, "PYTHONIOENCODING": "utf-8", **self.config.env}
        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=merged_env,
        )

        logger.info("MCP: connecting to %s (%s)", self.config.name, self.config.command)

        self._stdio_context = stdio_client(server_params)
        self._read_stream, self._write_stream = await self._stdio_context.__aenter__()

        self._session_context = ClientSession(self._read_stream, self._write_stream)
        self._session = await self._session_context.__aenter__()

        await self._session.initialize()

        result = await self._session.list_tools()
        for tool in result.tools:
            self._registry.register(
                name=tool.name,
                description=f"[{self.config.name}] {tool.description or tool.name}",
                input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict)
                else {"type": "object", "properties": {}},
                handler=self._make_handler(tool.name),
            )
            self.tool_names.append(tool.name)
            logger.debug("MCP: registered tool %s", tool.name)

    def _make_handler(self, tool_name: str):
        def handler(**kwargs: Any) -> str:
            if not self._loop or not self._session:
                return json.dumps({"error": "MCP session not connected"})
            future = asyncio.run_coroutine_threadsafe(
                self._call_tool(tool_name, kwargs), self._loop
            )
            try:
                return future.result(timeout=120)
            except TimeoutError:
                return json.dumps({"error": f"MCP tool '{tool_name}' timed out after 120s"})
            except Exception as exc:
                logger.exception("MCP: tool '%s' failed", tool_name)
                return json.dumps({"error": f"MCP tool '{tool_name}' failed: {type(exc).__name__}: {exc}"})
        return handler

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        result = await self._session.call_tool(tool_name, arguments)
        parts = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            else:
                parts.append(str(content))
        return "\n".join(parts)

    def disconnect(self) -> None:
        """Disconnect from MCP server and unregister tools."""
        if not self._loop:
            return
        # Unregister tools
        registry = get_registry()
        for tool_name in self.tool_names:
            registry.unregister(tool_name)
        self.tool_names.clear()
        # Stop the event loop
        try:
            future = asyncio.run_coroutine_threadsafe(self._async_disconnect(), self._loop)
            future.result(timeout=10)
        except Exception as e:
            logger.error("MCP: error disconnecting %s: %s", self.config.name, e)
        self._loop.call_soon_threadsafe(self._loop.stop)

    async def _async_disconnect(self) -> None:
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if self._stdio_context:
                await self._stdio_context.__aexit__(None, None, None)
        except Exception:
            pass
        logger.info("MCP: disconnected from %s", self.config.name)


# ── Bridge protocol functions ─────────────────────────────────────

def _connect_mcp_servers(configs: list[MCPServerConfig]) -> list[MCPToolBridge]:
    bridges: list[MCPToolBridge] = []
    for config in configs:
        try:
            bridge = MCPToolBridge(config)
            bridge.connect()
            bridges.append(bridge)
        except Exception as e:
            logger.exception("MCP: failed to connect to %s: %s", config.name, e)
    return bridges


def _disconnect_mcp_servers(bridges: list[MCPToolBridge]) -> None:
    for bridge in bridges:
        try:
            bridge.disconnect()
        except Exception:
            logger.exception("MCP: disconnect error for %s", bridge.config.name)


# ── Register with BridgeManager ───────────────────────────────────

BridgeManager.register(
    name="mcp",
    discover=discover_mcp_servers,
    connect=_connect_mcp_servers,
    disconnect=_disconnect_mcp_servers,
)

logger.debug("MCP bridge registered")
