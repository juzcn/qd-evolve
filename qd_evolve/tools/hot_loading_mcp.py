"""Hot-load an MCP server — spawn process, discover tools, register for immediate use."""

import json
from pathlib import Path

from qd_evolve.core.config import MCPServerConfig
from qd_evolve.tools import get_registry
from tools.bridge._mcp import MCPToolBridge, _expand_env, _extract_servers

_bridges: list[MCPToolBridge] = []


def _hot_loading_mcp(
    name: str,
    config_path: str,
    timeout: int | None = None,
) -> str:
    if timeout is None:
        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT
        timeout = DEFAULT_TOOL_TIMEOUT

    try:
        raw = Path(config_path).read_text(encoding="utf-8")
        config = json.loads(raw)
    except FileNotFoundError:
        return f"Error: config file not found: {config_path}"
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON in {config_path}: {e}"

    servers = _extract_servers(config, name)
    if name not in servers:
        return f"Error: server '{name}' not found in config. Available keys: {', '.join(servers.keys())}"
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
    description="Hot-load an MCP server from a config file — spawn the process, discover its tools, and register them for immediate use.",
    handler=lambda **kwargs: _hot_loading_mcp(
        name=kwargs["name"],
        config_path=kwargs["config_path"],
        timeout=kwargs.get("timeout", None),
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "MCP server name (must match a key in the config file).",
            },
            "config_path": {
                "type": "string",
                "description": (
                    "Path to the MCP server config JSON file (e.g., 'tools/mcp/my-server.json'). "
                    "Common forms:\n"
                    "1. Stdio: "
                    '{"command": "npx", "args": ["-y", "<package>"], '
                    '"env": {"API_KEY": "$ENV_VAR"}}\n'
                    "2. SSE/HTTP: "
                    '{"type": "sse", "url": "http://localhost:8000/sse", '
                    '"headers": {"Authorization": "Bearer $TOKEN"}}\n'
                    "3. Wrapper: "
                    '{"mcpServers": {"<name>": {"command": "..."}}}'
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds for server connection (default 30). Set higher for slow-starting servers (e.g., timeout=300).",
            },
        },
        "required": ["name", "config_path"],
    },
)