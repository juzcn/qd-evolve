"""MCP bridge 鈥?external process tools via stdin/stdout transport.

Scans tools/mcp/*.json for MCP server configs, spawns subprocesses,
discovers tools via MCP list_tools, and registers them in ToolRegistry.
"""


import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import os as _os
import re

from qd_evolve.config import MCPServerConfig
from qd_evolve.logger import logger
from qd_evolve.tools import ToolRegistry, get_registry
from tools.bridge import BridgeManager

MCP_DIR = Path.cwd() / "tools" / "mcp"


def _expand_env(value: str) -> str:
    """Expand $VAR / ${VAR} references in a string from os.environ."""

    def _replace(match: re.Match) -> str:
        var = match.group(1) or match.group(2)
        return _os.environ.get(var, match.group(0))

    return re.sub(r"\$\{(\w+)\}|\$(\w+)", _replace, value)


# 鈹€鈹€ discovery 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def discover_mcp_servers(_settings: Any = None) -> list[MCPServerConfig]:
    """Scan tools/mcp/*.json and .qd-evolve/staging/mcp/*.json for MCP server configs."""
    configs: list[MCPServerConfig] = []
    scan_dirs = [MCP_DIR]

    from qd_evolve.tools.staging import staging_mcp_dir
    staging = staging_mcp_dir()
    if staging.is_dir():
        scan_dirs.append(staging)

    for mcp_dir in scan_dirs:
        if not mcp_dir.exists():
            logger.debug("MCP: dir %s not found, skipping", mcp_dir)
            continue

        for json_file in sorted(mcp_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                servers = _extract_servers(data, json_file.stem)
                for name, srv in servers.items():
                    config = MCPServerConfig(
                        name=name,
                        command=_expand_env(srv.get("command", "")),
                        args=[_expand_env(a) for a in srv.get("args", [])],
                        env=srv.get("env", {}),
                        type=srv.get("type", "stdio"),
                        url=_expand_env(srv.get("url", "")),
                        headers={k: _expand_env(v) for k, v in srv.get("headers", {}).items()},
                        timeout=float(srv.get("timeout", 30)),
                        sse_read_timeout=float(srv.get("sse_read_timeout", 300)),
                        terminate_on_close=bool(srv.get("terminate_on_close", True)),
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


# 鈹€鈹€ MCPToolBridge 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

class MCPToolBridge:
    """Manages one MCP server 鈥?spawns subprocess, discovers tools, dispatches calls."""

    def __init__(self, config: MCPServerConfig, registry: ToolRegistry | None = None) -> None:
        self.config = config
        self._registry = registry or get_registry()
        self._session: Any = None
        self._transport_ctx: Any = None
        self._session_context: Any = None
        self.tool_names: list[str] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def connect(self) -> None:
        """Start the subprocess, initialize MCP session, register tools."""
        self._loop = asyncio.new_event_loop()
        connected = threading.Event()
        error_ref: list[BaseException] = []

        def _run():
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._async_connect())
            except BaseException as e:
                error_ref.append(e)
                logger.error("MCP: connect failed for %s: %s", self.config.name, e)
            finally:
                connected.set()
            if not error_ref:
                self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        connected.wait(timeout=60)

        if error_ref:
            raise error_ref[0]

    async def _async_connect(self) -> None:
        from mcp import ClientSession

        transport = self.config.type
        if transport == "stdio":
            await self._connect_stdio()
        elif transport == "sse":
            await self._connect_sse()
        elif transport in ("http", "streamable-http"):
            await self._connect_streamable_http()
        elif transport in ("ws", "websocket"):
            await self._connect_websocket()
        else:
            raise ValueError(f"Unsupported MCP transport: {transport}")

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

    async def _connect_stdio(self) -> None:
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        merged_env = {**_os.environ, "PYTHONIOENCODING": "utf-8", **self.config.env}
        server_params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=merged_env,
        )

        logger.info("MCP: connecting to %s via stdio (%s)", self.config.name, self.config.command)

        self._transport_ctx = stdio_client(server_params)
        self._read_stream, self._write_stream = await self._transport_ctx.__aenter__()

    async def _connect_sse(self) -> None:
        from mcp.client.sse import sse_client

        url = self.config.url
        headers = self.config.headers if self.config.headers else None

        logger.info("MCP: connecting to %s via SSE (%s)", self.config.name, url)

        self._transport_ctx = sse_client(
            url, headers=headers,
            timeout=self.config.timeout,
            sse_read_timeout=self.config.sse_read_timeout,
        )
        self._read_stream, self._write_stream = await self._transport_ctx.__aenter__()

    async def _connect_streamable_http(self) -> None:
        from mcp.client.streamable_http import streamablehttp_client

        url = self.config.url
        headers = self.config.headers if self.config.headers else None

        logger.info("MCP: connecting to %s via StreamableHTTP (%s)", self.config.name, url)

        self._transport_ctx = streamablehttp_client(
            url, headers=headers,
            timeout=self.config.timeout,
            sse_read_timeout=self.config.sse_read_timeout,
            terminate_on_close=self.config.terminate_on_close,
        )
        self._read_stream, self._write_stream, _ = await self._transport_ctx.__aenter__()

    async def _connect_websocket(self) -> None:
        from mcp.client.websocket import websocket_client

        url = self.config.url

        logger.info("MCP: connecting to %s via WebSocket (%s)", self.config.name, url)

        self._transport_ctx = websocket_client(url)
        self._read_stream, self._write_stream = await self._transport_ctx.__aenter__()

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

    def disconnect(self, shutdown: bool = False) -> None:
        """Disconnect from MCP server. If shutdown, skip registry cleanup."""
        if not self._loop:
            return
        if not shutdown:
            registry = get_registry()
            for tool_name in self.tool_names:
                registry.unregister(tool_name)
            self.tool_names.clear()
        # Stop the event loop
        try:
            if self._loop.is_running():
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
            if self._transport_ctx:
                await self._transport_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        logger.info("MCP: disconnected from %s", self.config.name)


# 鈹€鈹€ Bridge protocol functions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _connect_mcp_servers(configs: list[MCPServerConfig]) -> list[MCPToolBridge]:
    bridges: list[MCPToolBridge] = []
    for config in configs:
        try:
            bridge = MCPToolBridge(config)
            bridge.connect()
            bridges.append(bridge)
        except BaseException as e:
            logger.error("MCP: failed to connect to %s: %s", config.name, e)
    return bridges


def _disconnect_mcp_servers(bridges: list[MCPToolBridge]) -> None:
    for bridge in bridges:
        try:
            bridge.disconnect()
        except Exception:
            logger.exception("MCP: disconnect error for %s", bridge.config.name)


# 鈹€鈹€ Register with BridgeManager 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

BridgeManager.register(
    name="mcp",
    discover=discover_mcp_servers,
    connect=_connect_mcp_servers,
    disconnect=_disconnect_mcp_servers,
)

logger.debug("MCP bridge registered")
