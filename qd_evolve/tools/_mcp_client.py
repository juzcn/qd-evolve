from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from loguru import logger

from qd_evolve.config import MCPServerConfig
from qd_evolve.tools import ToolRegistry, get_registry


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

        logger.info("MCP: connecting to {} ({})", self._config.name, self._config.command)

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
                category="mcp",
            )
            self._tool_names.append(prefixed_name)
            logger.info("MCP: registered tool {} -> {}", tool.name, prefixed_name)

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
        try:
            future = asyncio.run_coroutine_threadsafe(self._async_disconnect(), self._loop)
            future.result(timeout=10)
        except Exception as e:
            logger.error("MCP: error disconnecting {}: {}", self._config.name, e)
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
        logger.info("MCP: disconnected from {}", self._config.name)


def connect_mcp_servers(configs: list[MCPServerConfig]) -> list[MCPToolBridge]:
    bridges: list[MCPToolBridge] = []
    for config in configs:
        try:
            bridge = MCPToolBridge(config)
            bridge.connect()
            bridges.append(bridge)
        except Exception as e:
            logger.error("MCP: failed to connect to {}: {}", config.name, e)
    return bridges


def disconnect_mcp_servers(bridges: list[MCPToolBridge]) -> None:
    for bridge in bridges:
        bridge.disconnect()
