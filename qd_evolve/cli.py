from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import typer
from loguru import logger
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve.config import Settings, load_settings
from qd_evolve.cli_tools import CLIRegistry
from qd_evolve.memory import MemoryStore
from qd_evolve.prompts import PromptTemplateManager
from qd_evolve.providers import ProviderRegistry
from qd_evolve.skills import SkillRegistry
from qd_evolve.tools import get_registry
from qd_evolve.tools._mcp_client import connect_mcp_servers, discover_mcp_servers, reload_mcp_servers

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("qd-evolve")
except Exception:
    __version__ = "0.1.0"

app = typer.Typer(help="qd-evolve AI agent")
console = Console()

SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/reset": "Reset conversation history",
    "/tools": "List available tools",
    "/skills": "List available skills",
    "/models": "Pick a model to switch to",
    "/memory": "List saved memories",
    "/cli": "List registered CLI tools",
    "/status": "Show runtime status (loaded tools, skills, CLI)",
    "/help": "Show available commands",
}


def _detect_python_cmd() -> str:
    """Detect a working python command by actually running it."""
    import subprocess
    for cmd in (sys.executable, "python3", "python"):
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return cmd
        except Exception:
            continue
    return "python"


def _make_prompt_session() -> "PromptSession":
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter

    completer = WordCompleter(
        list(SLASH_COMMANDS.keys()),
        ignore_case=True,
        sentence=True,
        meta_dict=SLASH_COMMANDS,
    )
    return PromptSession("You> ", completer=completer)


def _read_input(session: "PromptSession") -> str:
    try:
        return session.prompt().strip()
    except ImportError:
        return console.input("[bold cyan]You>[/bold cyan] ").strip()


def _handle_slash_command(
    cmd: str,
    agent: Any,
    settings: Settings,
    providers: ProviderRegistry,
    skill_registry: SkillRegistry,
    cli_registry: CLIRegistry,
    memory: MemoryStore | None = None,
) -> str | None:
    name = cmd.lower().strip()
    if name == "/quit":
        return None
    if name == "/reset":
        agent.reset()
        return "Conversation reset."
    if name == "/help":
        table = Table(title="Commands", show_header=False)
        table.add_column("Command", style="bold cyan")
        table.add_column("Description")
        for c, d in SLASH_COMMANDS.items():
            table.add_row(c, d)
        console.print(table)
        return ""
    if name == "/tools":
        registry = get_registry()
        tools = registry.list_tools()
        if not tools:
            return "  (no tools loaded)"
        lines = []
        for td in tools:
            desc = (td.description or "")[:80]
            lines.append(f"  [cyan]{td.name}[/cyan] — {desc}")
        return "\n".join(lines)
    if name == "/skills":
        skill_registry.reload()
        skills = skill_registry.get_all_skills()
        if not skills:
            return "  (no skills loaded)"
        lines = []
        for s in skills:
            lines.append(f"  [bold]{s.name}[/bold]{' v'+s.version if s.version else ''} — {s.summary[:60] if s.summary else ''}")
        return "\n".join(lines)
    if name == "/models":
        table = Table(title="Available Models", show_header=True)
        table.add_column("#", style="dim")
        table.add_column("Provider", style="bold")
        table.add_column("Model", style="bold cyan")
        all_models: list[tuple[str, str]] = []
        for p in settings.providers:
            for m in p.models:
                all_models.append((p.name, m.name))
        for i, (prov_name, mname) in enumerate(all_models, 1):
            table.add_row(str(i), prov_name, mname)
        console.print(table)
        try:
            from prompt_toolkit import PromptSession
            model_session = PromptSession("Switch to #: ")
            choice = model_session.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            return "  Cancelled."
        if choice.isdigit() and 1 <= int(choice) <= len(all_models):
            prov_name, mname = all_models[int(choice) - 1]
            settings.default_provider = prov_name
            settings.default_model = mname
            return f"  Switched to {prov_name}/{mname} (restart to apply)"
        return "  Cancelled."
    if name == "/status":
        table = Table(title="Runtime Status", show_header=True)
        table.add_column("Category", style="bold")
        table.add_column("Item", style="cyan")
        table.add_column("State", style="dim")

        # Provider / Model
        prov_name = agent._provider_name or settings.default_provider
        model_name = agent._model or settings.default_model
        table.add_row("Provider", f"{prov_name}/{model_name}", "current")

        # Preloaded tools
        for t in sorted(agent._always_active):
            table.add_row("Tool (preload)", t, "active")

        # Runtime activated tools
        for t in sorted(agent._active_tools - agent._always_active):
            table.add_row("Tool (loaded)", t, "active")

        # Loaded skills
        for s in sorted(agent._loaded_skills):
            table.add_row("Skill (loaded)", s, "injected")

        # Preload skills (from config, not yet loaded into agent)
        for s in settings.preload_skills:
            if s not in agent._loaded_skills:
                table.add_row("Skill (preload)", s, "active")

        # Loaded CLI tools
        for c in sorted(agent._loaded_cli):
            table.add_row("CLI (loaded)", c, "injected")

        # Preload CLI tools (from config, not yet loaded into agent)
        for c in settings.preload_cli:
            if c not in agent._loaded_cli:
                table.add_row("CLI (preload)", c, "active")

        # Token stats
        table.add_row("Tokens", f"in={agent.total_input_tokens} out={agent.total_output_tokens}", "cumulative")

        console.print(table)
        return ""
    if name == "/memory":
        if memory is None:
            return "  Memory store not initialized"
        entries = memory.list_all()
        if not entries:
            return "  (no memories saved)"
        table = Table(title="Memories", show_header=True)
        table.add_column("#", style="dim")
        table.add_column("Key", style="bold")
        table.add_column("Session", style="dim")
        table.add_column("User", style="cyan")
        table.add_column("Assistant")
        for e in entries:
            table.add_row(str(e.id), e.key, e.session_id, e.user_msg, e.assistant_msg)
        console.print(table)
        return ""
    if name == "/cli":
        cli_registry.reload()
        tools = cli_registry.list_tools()
        if not tools:
            return "  (no CLI tools registered)"
        lines = []
        for t in tools:
            desc = t.description or t.command
            lines.append(f"  [cyan]{t.name}[/cyan] — {desc}")
        return "\n".join(lines)
    return None


@app.command()
def chat() -> None:
    from qd_evolve.agent import Agent
    from qd_evolve.logger import setup_logging

    # 1. Config & logging
    setup_logging("WARNING")
    settings = load_settings()
    setup_logging(settings.log_level)

    # Inject env_vars from config into os.environ
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not settings.is_configured:
        console.print("[red]Error:[/red] No API key configured. Edit qd-evolve.json")
        raise SystemExit(1)

    # 2. Builtin tools
    registry = get_registry()

    # 3. Skills
    skill_registry = SkillRegistry()
    skill_registry.discover_skills(settings.skills_dir, preload_skills=settings.preload_skills)

    # 4. CLI tools
    cli_registry = CLIRegistry()
    cli_registry.discover(settings.cli_tools_dir)

    # 5. MCP servers
    mcp_configs = discover_mcp_servers()
    mcp_bridges = connect_mcp_servers(mcp_configs)

    # 6. Inject registries into loader tools
    from qd_evolve.tools.skill_loader import set_skill_registry
    set_skill_registry(skill_registry)
    from qd_evolve.tools.cli_loader import set_cli_registry
    set_cli_registry(cli_registry)

    # 7. System prompt via Jinja2 template
    python_cmd = _detect_python_cmd()
    template_mgr = PromptTemplateManager()
    skill_addition = skill_registry.format_for_prompt()
    active_skills_content = skill_registry.get_active_skills_content()
    cli_tools_summary = cli_registry.format_for_prompt()
    tools_summary = registry.format_tools_summary()

    # Build loaded content for active skills/CLI/tools
    import json as _json
    loaded_skill_names: set[str] = set()
    loaded_cli_names: set[str] = set()
    loaded_tool_names: set[str] = set(settings.preload_tools)

    active_skills_parts = []
    for s in skill_registry.get_all_skills():
        if s.active and s.content:
            active_skills_parts.append(f"### {s.name}\n{s.content}")
            loaded_skill_names.add(s.name)
    active_skills_content = "\n".join(active_skills_parts)

    active_cli_parts = []
    for t in cli_registry.list_tools():
        if t.name in settings.preload_cli:
            detail = cli_registry.get_detail(t.name)
            if detail:
                active_cli_parts.append(_json.dumps(detail, ensure_ascii=False))
                loaded_cli_names.add(t.name)
    active_cli_content = "\n".join(active_cli_parts)

    # Unloaded summaries exclude already-loaded items
    unloaded_skills = skill_registry.format_for_prompt(loaded=loaded_skill_names)
    unloaded_cli = cli_registry.format_for_prompt(loaded=loaded_cli_names)
    unloaded_tools = registry.format_tools_summary(loaded=loaded_tool_names)

    # Build loaded tool schemas for active tools
    loaded_tool_parts = []
    for tool_name in settings.preload_tools:
        detail = registry.get_detail(tool_name)
        if detail:
            loaded_tool_parts.append(_json.dumps(detail, ensure_ascii=False))
    active_tools_content = "\n".join(loaded_tool_parts)

    system_prompt = template_mgr.render(
        "default",
        unloaded_skills=unloaded_skills,
        unloaded_cli=unloaded_cli,
        unloaded_tools=unloaded_tools,
        loaded_skills=active_skills_content,
        loaded_cli=active_cli_content,
        os_name=platform.system(),
        python_cmd=python_cmd,
        cwd=str(Path.cwd()),
        skills_dir=str(Path(settings.skills_dir).resolve()),
    )

    # 8. Provider
    providers = ProviderRegistry(settings)

    # 9. Memory
    backend_name = settings.memory_search.default_embeddings_backend
    backend = settings.embeddings_backends.get(backend_name)
    if backend is None:
        console.print(f"[red]Error:[/red] Embeddings backend '{backend_name}' not found in config")
        raise SystemExit(1)
    memory = MemoryStore(settings.memory_db, backend)

    # 10. Inject memory store into recall_memory tool
    from qd_evolve.tools.recall_memory import set_memory_store
    set_memory_store(memory)

    agent = Agent(settings=settings, registry=registry, providers=providers, memory=memory, default_system_prompt=system_prompt)

    # Initialize agent's loaded_skills/loaded_cli with active content for on-demand append
    for s in skill_registry.get_all_skills():
        if s.active and s.content:
            agent._loaded_skills[s.name] = s.content
    for t in cli_registry.list_tools():
        if t.name in settings.preload_cli:
            detail = cli_registry.get_detail(t.name)
            if detail:
                agent._loaded_cli[t.name] = _json.dumps(detail, ensure_ascii=False)

    model_info = escape(f"[{settings.default_provider}/{settings.default_model}]")
    console.print(Panel(
        f"qd-evolve v{__version__} {model_info} - /help for commands, /quit to leave",
        style="bold green",
    ))

    input_session = _make_prompt_session()

    while True:
        try:
            user_input = _read_input(input_session)
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
            continue
        if user_input.startswith("/"):
            result = _handle_slash_command(user_input, agent, settings, providers, skill_registry, cli_registry, memory)
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                break
            if result:
                console.print(result)
            continue

        spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))
        def _on_status(text: str) -> None:
            spinner.update(text=Text(text, style="bold green"))
        agent.set_status_callback(_on_status)
        with Live(spinner, console=console, transient=True):
            try:
                response = agent.run(user_input)

                # Reload registries to pick up any new tools/skills/cli added during this turn
                skill_registry.reload()
                cli_registry.reload()
                mcp_bridges = reload_mcp_servers(discover_mcp_servers(), mcp_bridges)
            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted.[/dim]")
                continue
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                continue
        console.print(f"[bold]Assistant:[/bold] {response}")
        prov = providers.get()
        model_name = agent._model or settings.default_model
        ctx = prov.get_context_window(model_name)
        max_tok = prov.get_max_tokens(model_name)
        last_in = agent.last_input_tokens
        last_out = agent.last_output_tokens
        pct_ctx = f" ({last_in / ctx * 100:.1f}% of {ctx} context)" if ctx > 0 else ""
        pct_max = f" ({last_out / max_tok * 100:.1f}% of {max_tok} max)" if max_tok > 0 else ""
        console.print(f"[dim]This turn: {last_in} in{pct_ctx} + {last_out} out{pct_max}[/dim]")
        console.print(f"[dim]Cumulative: {agent.total_input_tokens + agent.total_output_tokens} tokens used[/dim]")

    memory.close()


if __name__ == "__main__":
    app()
