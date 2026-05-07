from __future__ import annotations

import sys
from typing import Optional

from loguru import logger
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.shortcuts import PromptSession
from rich.console import Console
from typer import Typer

from qd_evolve.config import load_settings
from qd_evolve.logger import setup_logging
from qd_evolve.providers import ProviderRegistry
from qd_evolve.tools import get_registry

tools_app = Typer(name="tools", help="Manage the persistent toolbox")
mcp_app = Typer(name="mcp", help="Manage MCP servers in the toolbox")

app = Typer(name="qd-evolve", help="AI agent with tool use")
app.add_typer(tools_app, name="tools")
tools_app.add_typer(mcp_app, name="mcp")
console = Console()


@app.command("chat")
@app.command("run", hidden=True)
def chat_cmd(
    template: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """Start an interactive chat session."""
    _run_chat(template, provider, model)


@tools_app.command("list")
def tools_list() -> None:
    """List all tools from the persistent toolbox."""
    setup_logging()
    settings = load_settings()
    setup_logging("WARNING")

    from qd_evolve.toolbox import ToolBox
    toolbox = ToolBox(settings)
    all_tools = toolbox.load_all()
    toolbox.close()

    if not all_tools:
        console.print("[dim]No tools in toolbox.[/]")
        return

    by_cat: dict[str, list[dict]] = {}
    for t in all_tools:
        by_cat.setdefault(t["category"], []).append(t)

    for cat, tools in sorted(by_cat.items()):
        console.print(f"[bold]{cat}:[/bold]")
        for t in tools:
            status = "" if t["enabled"] else " [dim](disabled)[/]"
            console.print(f"  {t['name']} — {t['description'][:80]}{status}")
        console.print()


@mcp_app.command("add")
def mcp_add(mcp_path: str) -> None:
    """Add an MCP server from a JSON config file."""
    import json as _json
    from pathlib import Path as _Path

    path = _Path(mcp_path)
    if not path.exists():
        console.print(f"[red]Error:[/] File not found: {mcp_path}")
        raise SystemExit(1)

    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]Error:[/] Invalid JSON: {e}")
        raise SystemExit(1)

    # Support both flat {"name": ..., "command": ..., "args": ...}
    # and nested {"mcpServers": {"name": {"command": ..., "args": ...}}}
    names: list[str] = []
    if "mcpServers" in data:
        names = list(data["mcpServers"].keys())
    elif "name" in data:
        names = [data["name"]]
    else:
        console.print("[red]Error:[/] JSON must have a 'name' field or 'mcpServers' key")
        raise SystemExit(1)

    setup_logging()
    settings = load_settings()
    setup_logging("WARNING")

    from qd_evolve.toolbox import ToolBox
    toolbox = ToolBox(settings)
    resolved = str(path.resolve())
    for srv_name in names:
        toolbox.add_mcp_server(srv_name, resolved)
        console.print(f"[green]Added MCP server:[/] {srv_name} ({resolved})")
    toolbox.close()


@mcp_app.command("list")
def mcp_list() -> None:
    """List registered MCP servers."""
    setup_logging()
    settings = load_settings()
    setup_logging("WARNING")

    from qd_evolve.toolbox import ToolBox
    toolbox = ToolBox(settings)
    servers = toolbox.list_mcp_servers()
    toolbox.close()

    if not servers:
        console.print("[dim]No MCP servers registered.[/]")
        return

    for name, config_path in servers:
        console.print(f"  [bold]{name}[/bold] — {config_path}")


class SlashCompleter(WordCompleter):
    def __init__(self) -> None:
        super().__init__(
            words=["/help", "/tools", "/quit", "/clear", "/model", "/provider", "/enable", "/disable"],
            ignore_case=True,
            sentence=True,
        )


def _run_chat(
    template: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    setup_logging()
    settings = load_settings()

    if template:
        from qd_evolve.prompts import PromptManager
        pm = PromptManager(settings)
        t = pm.get(template)
        if t:
            settings.default_system_prompt = t.get("system", settings.default_system_prompt)
    if provider:
        settings.default_provider = provider
    if model:
        settings.default_model = model

    registry = get_registry()

    # Init ToolBox (sqlite-vec persistence)
    from qd_evolve.toolbox import ToolBox
    toolbox = ToolBox(settings)
    registry.set_toolbox(toolbox)

    # Try to set up embeddings for ToolBox
    try:
        from qd_evolve.vector import VectorStore
        vs = VectorStore(settings)
        toolbox.set_embed_fn(vs.embed)
    except Exception:
        logger.debug("Embedding init skipped (model not available)")

    # Discover and register builtin tools
    loaded = registry.discover_tools()

    # Connect MCP servers (from config + from toolbox)
    from qd_evolve.tools._mcp_client import connect_mcp_servers, disconnect_mcp_servers
    from qd_evolve.config import MCPServerConfig
    mcp_configs = list(settings.mcp_servers)
    for s in toolbox.load_mcp_servers():
        mcp_configs.append(MCPServerConfig(name=s["name"], command=s["command"], args=s.get("args", [])))
    mcp_bridges = connect_mcp_servers(mcp_configs)

    # Load skills
    from qd_evolve.skills import SkillLoader
    skill_loader = SkillLoader(settings.skills_dir, settings.skill_config)
    skill_count = skill_loader.discover()
    if skill_count > 0:
        skill_loader.register_tools()
        settings.default_system_prompt += skill_loader.get_system_prompt_addition()

    # Load previously persisted tools not yet in memory
    restored = registry.load_from_toolbox(toolbox)
    if restored:
        logger.info("Restored {} tools from toolbox", restored)

    providers = ProviderRegistry(settings)
    from qd_evolve.agent import Agent
    agent = Agent(settings, registry, providers)

    session = PromptSession(history=InMemoryHistory(), completer=SlashCompleter())

    console.print(
        f"[bold green]qd-evolve agent[/] [{settings.default_provider}/{settings.default_model}]"
        f" — type /help for commands, /quit to leave"
    )

    while True:
        try:
            user_input = session.prompt("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            result = _handle_slash(user_input, settings, registry, providers, agent)
            if result is None:
                break
            if result:
                console.print(result)
            continue

        try:
            response = agent.run(user_input)
            console.print(f"\n[bold cyan]Assistant:[/]\n{response}\n")
        except Exception as e:
            logger.exception("Agent error")
            console.print(f"[red]Error:[/] {e}")

    # Cleanup
    try:
        disconnect_mcp_servers(mcp_bridges)
    except Exception:
        pass
    toolbox.close()


def _handle_slash(
    name: str,
    settings,
    registry,
    providers,
    agent,
) -> str | None:
    if name == "/quit":
        return None

    if name == "/help":
        return (
            "  /help    — Show this help\n"
            "  /tools   — List available tools\n"
            "  /quit    — Exit\n"
            "  /clear   — Clear conversation\n"
            "  /model   — Show current model\n"
            "  /provider — Show current provider\n"
            "  /enable <tool>  — Enable a tool\n"
            "  /disable <tool> — Disable a tool"
        )

    if name == "/tools":
        by_cat = registry.list_by_category()
        lines = []
        for cat, names in by_cat.items():
            lines.append(f"  [bold]{cat}:[/bold]")
            for n in names:
                td = registry.get(n)
                if td and registry.is_enabled(n):
                    lines.append(f"    {n} — {td.description[:80]}")
                elif td:
                    lines.append(f"    {n} — {td.description[:80]} [dim](disabled)[/]")
        return "\n".join(lines) if lines else "  (no tools loaded)"

    if name == "/clear":
        agent.clear()
        return "Conversation cleared."

    if name == "/model":
        return f"  {settings.default_provider}/{settings.default_model}"

    if name == "/provider":
        return f"  {settings.default_provider}"

    if name.startswith("/enable "):
        tool_name = name[8:].strip()
        registry.enable(tool_name)
        return f"  Enabled: {tool_name}"

    if name.startswith("/disable "):
        tool_name = name[9:].strip()
        registry.disable(tool_name)
        return f"  Disabled: {tool_name}"

    return f"  Unknown command: {name}"


def main() -> None:
    """Entry point — default to chat if no subcommand given."""
    if len(sys.argv) <= 1 or sys.argv[1].startswith("-"):
        # No subcommand — inject 'chat' so typer routes to chat_cmd
        sys.argv = [sys.argv[0], "chat"] + sys.argv[1:]
    app()


if __name__ == "__main__":
    main()
