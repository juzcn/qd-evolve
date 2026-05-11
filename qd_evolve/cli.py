from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any

import typer
from qd_evolve.logger import logger
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve.config import CONFIG_PATH, Settings, load_settings, save_json
from qd_evolve.cli_tools import CLIRegistry
from qd_evolve.memory import MemoryStore
from qd_evolve.prompts import PromptTemplateManager
from qd_evolve.providers import ProviderRegistry
from qd_evolve.skills import SkillRegistry
from qd_evolve.tools import get_registry
from qd_evolve.toolbox import state_mark
from qd_evolve.tools._mcp_client import connect_mcp_servers, discover_mcp_servers, reload_mcp_servers

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("qd-evolve")
except Exception:
    __version__ = "0.1.0"

app = typer.Typer(help="qd-evolve AI agent")
console = Console()


class ReplayInput:
    """Feeds pre-recorded inputs instead of reading from prompt_toolkit."""

    def __init__(self, inputs: list[str]) -> None:
        self._inputs = list(inputs)
        self._index = 0

    def prompt(self, **kwargs: Any) -> str:
        if self._index >= len(self._inputs):
            raise EOFError
        line = self._inputs[self._index]
        self._index += 1
        return line


class TeeWriter:
    """Writes to multiple file-like objects simultaneously."""

    def __init__(self, *files: Any) -> None:
        self._files = files

    def write(self, text: str) -> int:
        for f in self._files:
            f.write(text)
        return len(text)

    def flush(self) -> None:
        for f in self._files:
            f.flush()

    def isatty(self) -> bool:
        return any(getattr(f, "isatty", lambda: False)() for f in self._files)

SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/tools": "List available tools",
    "/skills": "List available skills",
    "/models": "Pick a model to switch to",
    "/cli": "List registered CLI tools",
    "/status": "Show runtime status (loaded tools, skills, CLI)",
    "/memory": "List saved memories",
    "/reset": "Reset conversation history",
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


@app.command()
def toolbox(
    toggle: str = typer.Option("", "--toggle", "-t", help="Quick toggle: --toggle <name>"),
    tui: bool = typer.Option(True, "--tui/--no-tui", help="Use Textual TUI (default: on)"),
) -> None:
    """Interactive tool manager — enable/disable/preload tools, MCP, CLI, skills.

    Opens a Textual TUI by default. Use --no-tui for interactive shell.
    --toggle <name> for quick non-interactive toggle.
    """
    from qd_evolve.toolbox import toggle as tb_toggle

    # Quick toggle mode
    if toggle:
        section = _resolve_section(toggle)
        name = _resolve_name(toggle)
        new_state = tb_toggle(section, name)
        console.print(f"[bold]{toggle}[/bold] → [cyan]{new_state}[/cyan]")
        return

    if tui:
        from qd_evolve.toolbox_tui import _build_data, ToolboxApp
        console.print("Loading tools...", end="\r")
        data, bridges = _build_data(connect_mcp=True)
        console.print(f"Loaded {sum(len(v) for v in data.values())} items across {len(data)} categories")
        ToolboxApp(data, bridges).run()
    else:
        _toolbox_interactive()


def _toolbox_interactive() -> None:
    """Interactive toolbox shell."""
    from qd_evolve.toolbox import (
        get_state, set_state, toggle as tb_toggle, get_disabled_mcp_servers,
    )
    from qd_evolve.tools import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.config import load_settings

    PAGE_SIZE = 20

    console.print("[bold]Toolbox[/bold] — manage tool state (enabled / preload / disabled)")
    console.print("Type [cyan]help[/cyan] for commands, [cyan]quit[/cyan] to exit\n")

    while True:
        try:
            cmd = console.input("[bold cyan]toolbox>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if action in ("q", "quit", "exit"):
            break
        elif action == "help":
            _toolbox_help()
        elif action in ("ls", "list", "show"):
            _toolbox_list(args)
        elif action == "toggle":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                new = tb_toggle(section, name)
                console.print(f"  {args[0]} → [cyan]{new}[/cyan]")
            else:
                console.print("  Usage: toggle <name>")
        elif action == "enable":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                set_state(section, name, "enabled")
                console.print(f"  {args[0]} → [green]enabled[/green]")
            else:
                console.print("  Usage: enable <name>")
        elif action == "disable":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                set_state(section, name, "disabled")
                console.print(f"  {args[0]} → [red]disabled[/red]")
            else:
                console.print("  Usage: disable <name>")
        elif action == "preload":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                if section == "mcp_servers":
                    console.print("  MCP servers don't support preload")
                else:
                    set_state(section, name, "preload")
                    console.print(f"  {args[0]} → [yellow]preload[/yellow]")
            else:
                console.print("  Usage: preload <name>")
        else:
            console.print(f"  Unknown: {action}. Type [cyan]help[/cyan]")


def _toolbox_help() -> None:
    console.print("""
  [bold]Commands:[/bold]
    [cyan]ls[/cyan] [section]     List tools (tools, mcp, cli, skills, all)
    [cyan]toggle[/cyan] <name>    Cycle state (enabled→preload→disabled→enabled)
    [cyan]enable[/cyan] <name>    Set item to enabled (on-demand loading)
    [cyan]disable[/cyan] <name>   Hide item from the LLM
    [cyan]preload[/cyan] <name>   Load full definition into system prompt
    [cyan]quit[/cyan]             Exit

  [bold]Name prefixes:[/bold]
    builtin/MCP tools: just the name (e.g. [cyan]fetch[/cyan], [cyan]boat__write_file[/cyan])
    MCP servers:       [cyan]mcp:boat[/cyan]
    CLI tools:          [cyan]pandoc[/cyan]
    Skills:             [cyan]baidu-search[/cyan]

  [bold]States:[/bold] [✓] enabled  [P] preload  [✗] disabled
""")


def _toolbox_list(args: list[str]) -> None:
    from qd_evolve.toolbox import get_state, get_disabled_mcp_servers
    from qd_evolve.tools import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.config import load_settings
    from qd_evolve.tools._mcp_client import discover_mcp_servers

    PAGE_SIZE = 20
    settings = load_settings()
    section_arg = args[0].lower() if args else "all"

    # Build data
    registry = get_registry()
    builtin: list[tuple[str, str, str]] = []
    mcp_tools: dict[str, list[tuple[str, str, str]]] = {}
    for td in registry.list_tools():
        state = get_state("tools", td.name)
        if "__" in td.name:
            server = td.name.split("__")[0]
            mcp_tools.setdefault(server, []).append((td.name, td.description or "", state))
        else:
            builtin.append((td.name, td.description or "", state))

    # Skills
    sr = SkillRegistry()
    sr.discover_skills(settings.skills_dir)
    skills_data: list[tuple[str, str, str]] = []
    for s in sr._skills.values():
        skills_data.append((s.name, s.summary or "", get_state("skills", s.name)))

    # CLI
    cr = CLIRegistry()
    cr.discover(settings.cli_tools_dir)
    cli_data: list[tuple[str, str, str]] = []
    for t in cr._tools.values():
        cli_data.append((t.name, t.description or t.command, get_state("cli", t.name)))

    # MCP servers
    mcp_configs = discover_mcp_servers()
    disabled_srv = get_disabled_mcp_servers()

    def _print_items(title: str, items: list[tuple[str, str, str]], page: int = 0) -> None:
        if not items:
            return
        start = page * PAGE_SIZE
        chunk = items[start:start + PAGE_SIZE]
        total_pages = (len(items) - 1) // PAGE_SIZE + 1
        active_n = sum(1 for _, _, s in items if s != "disabled")
        console.print(f"\n[bold]{title}[/bold] ({active_n}/{len(items)} active{', page %s/%s' % (page + 1, total_pages) if total_pages > 1 else ''})")
        for name, desc, state in chunk:
            mark = state_mark(state)
            style = "dim" if state == "disabled" else ""
            console.print(f"  {mark} [cyan]{name}[/cyan] {style}—{desc[:70]}")
        if total_pages > 1 and page < total_pages - 1:
            console.print(f"  [dim]... ls {section_arg} page {page + 2} for more[/dim]")

    def _print_servers() -> None:
        if not mcp_configs:
            return
        console.print("\n[bold]MCP Servers[/bold]")
        for cfg in mcp_configs:
            tools = mcp_tools.get(cfg.name, [])
            srv_state = "disabled" if cfg.name in disabled_srv else "enabled"
            mark = state_mark(srv_state)
            style = "dim" if srv_state == "disabled" else ""
            total = len(tools)
            active = sum(1 for _, _, s in tools if s != "disabled")
            summary = f"{active}/{total} tools" if total else "tools visible during chat"
            console.print(f"  {mark} [cyan]mcp:{cfg.name}[/cyan] {style}—{summary}")
            if total > 0:
                console.print(f"    [dim]ls mcp:{cfg.name} to expand[/dim]")

    page = 0
    if len(args) > 1 and args[1].isdigit():
        page = int(args[1]) - 1

    if section_arg in ("all", "tools"):
        _print_items("Builtin Tools", builtin, page)
    if section_arg in ("all", "cli"):
        _print_items("CLI Tools", cli_data, page)
    if section_arg in ("all", "skills"):
        _print_items("Skills", skills_data, page)
    if section_arg in ("all", "mcp"):
        _print_servers()
    if section_arg.startswith("mcp:") and section_arg != "mcp":
        server = section_arg.split(":", 1)[1]
        if server in mcp_tools:
            _print_items(f"MCP: {server}", mcp_tools[server], page)
        else:
            console.print(f"  MCP server '{server}' has no tools registered (run chat first)")


def _resolve_section(name: str) -> str:
    if name.startswith("mcp:"):
        return "mcp_servers"
    return "tools"


def _resolve_name(name: str) -> str:
    if name.startswith("mcp:"):
        return name.split(":", 1)[1]
    return name


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
            lines.append(f"  [cyan]{td.name}[/cyan] —{desc}")
        return "\n".join(lines)
    if name == "/skills":
        skill_registry.reload()
        skills = skill_registry.get_all_skills()
        if not skills:
            return "  (no skills loaded)"
        lines = []
        for s in skills:
            lines.append(f"  [bold]{s.name}[/bold]{' v'+s.version if s.version else ''} —{s.summary[:60] if s.summary else ''}")
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
            save_json(settings.model_dump(), CONFIG_PATH)
            logger.info("CLI: switched default model to %s/%s and saved config", prov_name, mname)
            return f"  Switched to {prov_name}/{mname}"
        return "  Cancelled."
    if name == "/status":
        prov_name = agent._provider_name or settings.default_provider
        model_name = agent._model or settings.default_model
        lines = [f"  [bold]Provider:[/bold] {prov_name}/{model_name}"]

        preload_tools = sorted(agent._always_active)
        loaded_tools = sorted(agent._active_tools - agent._always_active)
        if preload_tools:
            lines.append(f"  [bold]Tool (preload):[/bold] {', '.join(preload_tools)}")
        if loaded_tools:
            lines.append(f"  [bold]Tool (loaded):[/bold] {', '.join(loaded_tools)}")

        preload_skills = sorted(agent._preload_skills)
        loaded_skills = sorted(s for s in agent._loaded_skills if s not in agent._preload_skills)
        if preload_skills:
            lines.append(f"  [bold]Skill (preload):[/bold] {', '.join(preload_skills)}")
        if loaded_skills:
            lines.append(f"  [bold]Skill (loaded):[/bold] {', '.join(loaded_skills)}")

        preload_cli = sorted(agent._preload_cli)
        loaded_cli = sorted(c for c in agent._loaded_cli if c not in agent._preload_cli)
        if preload_cli:
            lines.append(f"  [bold]CLI (preload):[/bold] {', '.join(preload_cli)}")
        if loaded_cli:
            lines.append(f"  [bold]CLI (loaded):[/bold] {', '.join(loaded_cli)}")

        return "\n".join(lines)
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
        table.add_column("Last Access", style="dim")
        table.add_column("AC", style="dim", justify="right")
        table.add_column("User", style="cyan")
        table.add_column("Assistant")
        for e in entries:
            table.add_row(str(e.id), e.key, e.session_id, e.accessed_at or "-", str(e.access_count), e.user_msg, e.assistant_msg)
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
            lines.append(f"  [cyan]{t.name}[/cyan] —{desc}")
        return "\n".join(lines)
    return None


@app.callback(invoke_without_command=True)
def chat(
    ctx: typer.Context,
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return  # toolbox or another subcommand was invoked
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
    skill_registry.discover_skills(settings.skills_dir)

    # 4. CLI tools
    cli_registry = CLIRegistry()
    cli_registry.discover(settings.cli_tools_dir)

    # 5. MCP servers
    mcp_configs = discover_mcp_servers()
    mcp_bridges = connect_mcp_servers(mcp_configs)

    # 5b. Apply toolbox state from toolbox.json
    from qd_evolve.toolbox import (
        apply_to_tools, apply_to_cli_registry, apply_to_skill_registry,
        get_preloaded,
    )
    loaded_tool_names: set[str] = get_preloaded("tools")
    loaded_skill_names: set[str] = get_preloaded("skills")
    loaded_cli_names: set[str] = get_preloaded("cli")
    apply_to_tools(registry, loaded_tool_names)
    apply_to_cli_registry(cli_registry, loaded_cli_names)
    apply_to_skill_registry(skill_registry, loaded_skill_names)

    # 6. Inject registries into loader tools
    from qd_evolve.tools.skill_loader import set_skill_registry
    set_skill_registry(skill_registry)
    from qd_evolve.tools.tool_loader import set_preload_tools
    set_preload_tools(loaded_tool_names)
    from qd_evolve.tools.cli_loader import set_cli_registry
    set_cli_registry(cli_registry, loaded_cli_names)

    # 7. System prompt via Jinja2 template
    python_cmd = _detect_python_cmd()
    template_mgr = PromptTemplateManager()

    # Build loaded content for preload skills/CLI/tools
    import json as _json
    # Merge toolbox preloads into registry state
    skill_registry._preload_skills |= loaded_skill_names
    for s in skill_registry._skills.values():
        if s.name in loaded_skill_names:
            s.active = True

    active_skills_parts = []
    for s in skill_registry.get_all_skills():
        if s.active and s.content:
            active_skills_parts.append(s.content)
            loaded_skill_names.add(s.name)
    active_skills_content = "\n".join(active_skills_parts)

    active_cli_parts = []
    for t in cli_registry.list_tools():
        if t.name in loaded_cli_names:
            detail = cli_registry.get_detail(t.name)
            if detail:
                active_cli_parts.append(_json.dumps(detail, ensure_ascii=False))
                loaded_cli_names.add(t.name)
    active_cli_content = "\n".join(active_cli_parts)

    # Unloaded summaries exclude already-loaded items
    unloaded_skills = skill_registry.format_for_prompt(loaded=loaded_skill_names)
    unloaded_cli = cli_registry.format_for_prompt(loaded=loaded_cli_names)
    unloaded_tools = registry.format_tools_summary(loaded=loaded_tool_names)

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
        skills_dir=settings.skills_dir,
    )

    # 8. Provider
    providers = ProviderRegistry(settings)

    # 9. Memory
    backend_name = settings.memory_search.default_embeddings_backend
    backend = settings.embeddings_backends.get(backend_name)
    if backend is None:
        console.print(f"[red]Error:[/red] Embeddings backend '{backend_name}' not found in config")
        raise SystemExit(1)
    memory = MemoryStore(settings.memory_db, backend,
                         search_by_time_limit=settings.memory_search.search_by_time_limit,
                         list_all_limit=settings.memory_search.list_all_limit)

    # 10. Inject memory store and defaults into recall_memory tool
    from qd_evolve.tools.recall_memory import set_memory_store, set_default_limit, set_browse_min_limit
    set_memory_store(memory)
    set_default_limit(settings.memory_search.recall_memory_limit)
    set_browse_min_limit(settings.memory_search.search_by_time_limit)

    agent = Agent(settings=settings, registry=registry, providers=providers, memory=memory,
                  default_system_prompt=system_prompt,
                  preload_tools=loaded_tool_names,
                  preload_skills=loaded_skill_names,
                  preload_cli=loaded_cli_names)

    # Initialize agent's loaded_skills/loaded_cli with active content for on-demand append
    for s in skill_registry.get_all_skills():
        if s.active and s.content:
            agent._loaded_skills[s.name] = s.content
    for t in cli_registry.list_tools():
        if t.name in loaded_cli_names:
            detail = cli_registry.get_detail(t.name)
            if detail:
                agent._loaded_cli[t.name] = _json.dumps(detail, ensure_ascii=False)

    model_info = escape(f"[{settings.default_provider}/{settings.default_model}]")
    console.print(Panel(
        f"qd-evolve v{__version__} {model_info} - /help for commands, /quit to leave",
        style="bold green",
    ))

    # Replay mode setup
    output_file = None
    if replay:
        lines = replay.read_text(encoding="utf-8").splitlines()
        inputs = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        input_session = ReplayInput(inputs)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output_file = open(output, "w", encoding="utf-8")
            tee = TeeWriter(sys.stdout, output_file)
            import qd_evolve.cli as _cli_mod
            _cli_mod.console = Console(file=tee, force_terminal=True)
        logger.info("CLI: replay mode: %s inputs from %s", len(inputs), replay)
    else:
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
            result = _handle_slash_command(user_input, agent, settings, skill_registry, cli_registry, memory)
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
    if output_file:
        output_file.close()


if __name__ == "__main__":
    app()
