from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from qd_evolve.logger import logger

from qd_evolve.config import MCPServerConfig
from qd_evolve.tools import ToolRegistry, get_registry

MCP_DIR = Path.cwd() / "tools" / "mcp"


def discover_mcp_servers() -> list[MCPServerConfig]:
    """Scan tools/mcp/*.json for MCP server configs and return parsed list."""
    configs: list[MCPServerConfig] = []
    if not MCP_DIR.exists():
        logger.debug("MCP dir %s not found, skipping", MCP_DIR)
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
    # Format 1: {"mcpServers": {"name": {...}}}
    if "mcpServers" in data:
        return data["mcpServers"]

    # Format 2: {"mcp": {"servers": {"name": {...}}}}
    if "mcp" in data and isinstance(data["mcp"], dict):
        if "servers" in data["mcp"]:
            return data["mcp"]["servers"]

    # Format 3: bare {"command": "...", "args": [...]} —use filename as name
    if "command" in data:
        return {fallback_name: data}

    return {}


class MCPToolBridge:
    def __init__(self, config: MCPServerConfig, registry: ToolRegistry | None = None) -> None:
        self._config = config
        self._registry = registry or get_registry()
        self._session: Any = None
        self._stdio_context: Any = None
        self._session_context: Any = None
        self._tool_names: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
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
            # Keep the event loop running for tool calls
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

        server_params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
            env=self._config.env or None,
        )

        logger.info("MCP: connecting to %s (%s)", self._config.name, self._config.command)

        self._stdio_context = stdio_client(server_params)
        self._read_stream, self._write_stream = await self._stdio_context.__aenter__()

        self._session_context = ClientSession(self._read_stream, self._write_stream)
        self._session = await self._session_context.__aenter__()

        await self._session.initialize()

        result = await self._session.list_tools()
        for tool in result.tools:
            prefixed_name = f"{self._config.name}__{tool.name}"
            self._registry.register(
                name=prefixed_name,
                description=f"[{self._config.name}] {tool.description or tool.name}",
                input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict) else {"type": "object", "properties": {}},
                handler=self._make_handler(tool.name),
            )
            self._tool_names.append(prefixed_name)
            logger.debug("MCP: registered tool %s -> %s", tool.name, prefixed_name)

    def _make_handler(self, tool_name: str):
        def handler(**kwargs: Any) -> str:
            if not self._loop or not self._session:
                return json.dumps({"error": "MCP session not connected"})
            future = asyncio.run_coroutine_threadsafe(
                self._call_tool(tool_name, kwargs), self._loop
            )
            return future.result(timeout=60)
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
        if not self._loop:
            return
        # Unregister tools from ToolRegistry
        registry = get_registry()
        for tool_name in self._tool_names:
            registry.unregister(tool_name)
        self._tool_names.clear()
        # Stop the event loop and disconnect
        try:
            future = asyncio.run_coroutine_threadsafe(self._async_disconnect(), self._loop)
            future.result(timeout=10)
        except Exception as e:
            logger.error("MCP: error disconnecting %s: %s", self._config.name, e)
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
        logger.info("MCP: disconnected from %s", self._config.name)


def connect_mcp_servers(configs: list[MCPServerConfig]) -> list[MCPToolBridge]:
    from qd_evolve.toolbox import get_disabled_mcp_servers
    disabled = get_disabled_mcp_servers()
    bridges: list[MCPToolBridge] = []
    for config in configs:
        if config.name in disabled:
            logger.info("MCP: skipping disabled server %s", config.name)
            continue
        try:
            bridge = MCPToolBridge(config)
            bridge.connect()
            bridges.append(bridge)
        except Exception as e:
            logger.error("MCP: failed to connect to %s: %s", config.name, e)
    return bridges


def reload_mcp_servers(
    configs: list[MCPServerConfig],
    existing_bridges: list[MCPToolBridge],
) -> list[MCPToolBridge]:
    """Connect new MCP servers, disconnect removed ones. Keep existing ones unchanged."""
    current_names = {c.name for c in configs}
    kept_bridges: list[MCPToolBridge] = []

    # Disconnect servers that are no longer in configs
    for bridge in existing_bridges:
        if bridge._config.name in current_names:
            kept_bridges.append(bridge)
        else:
            logger.info("MCP: disconnecting removed server %s", bridge._config.name)
            bridge.disconnect()

    # Connect new servers
    connected_names = {b._config.name for b in kept_bridges}
    for config in configs:
        if config.name in connected_names:
            continue
        try:
            bridge = MCPToolBridge(config)
            bridge.connect()
            kept_bridges.append(bridge)
            logger.info("MCP: connected new server %s", config.name)
        except Exception as e:
            logger.error("MCP: failed to connect to %s: %s", config.name, e)

    return kept_bridges
