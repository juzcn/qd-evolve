"""Hot-load an MCP server — spawn process, discover tools, register for immediate use."""

import shutil
import subprocess
import sys

from qd_evolve.core.config import MCPServerConfig
from qd_evolve.tools import get_registry
from tools.bridge._mcp import MCPToolBridge, _expand_env, _extract_servers

_bridges: list[MCPToolBridge] = []


def _hot_loading_mcp(
    name: str,
    config: dict,
    pip_packages: list[str] | None = None,
    timeout: int | None = None,
) -> str:
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT
        timeout = DEFAULT_TOOL_TIMEOUT
    if pip_packages:
        try:
            uv = shutil.which("uv")
            if uv:
                subprocess.check_call(
                    [uv, "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                )
            else:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                )
        except subprocess.CalledProcessError as e:
            return f"Error: package install failed for {pip_packages} (exit code {e.returncode}). The packages may not exist or be incompatible."
        except subprocess.TimeoutExpired:
            return f"Error: package install timed out after {timeout}s"

    servers = _extract_servers(config, name)
    srv = servers[name]
    mcp_config = MCPServerConfig(
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

    # Hot-load: create bridge and connect
    try:
        bridge = MCPToolBridge(mcp_config)
        bridge.connect()
        _bridges.append(bridge)
        tool_names = bridge.tool_names
        return f"MCP server '{name}' hot-loaded. Discovered tools: {', '.join(tool_names)}"
    except BaseException as e:
        return f"Error: MCP server '{name}' connect failed: {e}"


registry = get_registry()
registry.register(
    name="hot_loading_mcp",
    description="Hot-load an MCP server — spawn the process, discover its tools, and register them for immediate use.",
    handler=lambda **kwargs: _hot_loading_mcp(
        kwargs["name"],
        kwargs["config"],
        kwargs.get("pip_packages", None),
        kwargs.get("timeout", None),
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "MCP server name",
            },
            "config": {
                "type": "object",
                "description": "MCP server config dict (same format as tools/mcp/*.json). Must include mcpServers key or bare command/args.",
            },
            "pip_packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional pip packages to install (for stdio servers)",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Set higher for servers that need package installs (e.g., timeout=300).",
            },
        },
        "required": ["name", "config"],
    },
)