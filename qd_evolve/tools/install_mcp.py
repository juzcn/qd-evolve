"""Install and hot-load an MCP server."""

import json
import shutil
import subprocess
import sys

from qd_evolve.core.config import MCPServerConfig
from qd_evolve.tools import get_registry
from qd_evolve.tools.staging import ensure_staging_dirs, staging_mcp_dir
from tools.bridge._mcp import MCPToolBridge, _expand_env, _extract_servers

_staged_bridges: list[MCPToolBridge] = []


def set_staged_bridges(bridges: list[MCPToolBridge]) -> None:
    global _staged_bridges
    _staged_bridges = bridges


def _install_mcp(
    name: str,
    config: dict,
    pip_packages: list[str] | None = None,
) -> str:
    if pip_packages:
        try:
            uv = shutil.which("uv")
            if uv:
                subprocess.check_call(
                    [uv, "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", *pip_packages],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except subprocess.CalledProcessError as e:
            return f"Error: package install failed for {pip_packages} (exit code {e.returncode}). The packages may not exist or be incompatible."

    ensure_staging_dirs()

    # Write config to staging
    staging_file = staging_mcp_dir() / f"{name}.json"
    servers = _extract_servers(config, name)
    staging_data = {"mcpServers": servers}
    staging_file.write_text(json.dumps(staging_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Build MCPServerConfig from the dict
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
        _staged_bridges.append(bridge)
        tool_names = bridge.tool_names
        return f"MCP server '{name}' installed and hot-loaded. Discovered tools: {', '.join(tool_names)}"
    except BaseException as e:
        return f"Error: MCP server '{name}' connect failed: {e}"


registry = get_registry()
registry.register(
    name="install_mcp",
    description="Install and hot-load an MCP server. The server's tools are immediately usable after installation.",
    handler=_install_mcp,
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
        },
        "required": ["name", "config"],
    },
)