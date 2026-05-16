import asyncio
from asyncio import CancelledError
import platform
import sys
from pathlib import Path
from typing import Any

import typer
from qd_evolve.core.logger import logger
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve.core.config import CONFIG_PATH, SKILLS_DIR, CLI_TOOLS_DIR, DEFAULT_MEMORY_DB, DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT, Settings, load_settings, save_json
from qd_evolve.cli_tools import CLIRegistry
from qd_evolve.core.memory import MemoryStore
from qd_evolve.core.prompts import PromptTemplateManager
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.skills import SkillRegistry
from qd_evolve.core.registry import get_registry
from qd_evolve.core.toolbox import state_mark
from tools.bridge import BridgeManager

from qd_evolve import __version__

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
    "/agents": "List discovered agents",
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
    agent: str = typer.Option("", "--agent", help="Per-agent toolbox config (from config.json agents list)"),
) -> None:
    """Interactive tool manager — enable/disable/preload tools, MCP, CLI, skills.

    Opens a Textual TUI by default. Use --no-tui for interactive shell.
    --toggle <name> for quick non-interactive toggle.
    --agent <name> to manage a specific agent's toolbox.
    """
    from qd_evolve.core.toolbox import toggle as tb_toggle
    an = agent or None

    # Quick toggle mode
    if toggle:
        section = _resolve_section(toggle)
        name = _resolve_name(toggle)
        new_state = tb_toggle(section, name, agent_name=an)
        console.print(f"[bold]{toggle}[/bold] → [cyan]{new_state}[/cyan]")
        return

    if tui:
        from qd_evolve.toolbox_tui import _build_data, ToolboxApp
        console.print("Loading tools...", end="\r")
        data, bridges, bridge_entries = _build_data(connect_bridges=True, agent_name=an)
        console.print(f"Loaded {sum(len(v) for v in data.values())} items across {len(data)} categories")
        ToolboxApp(data, bridges, bridge_entries, agent_name=an).run()
    else:
        _toolbox_interactive(an)


def _toolbox_interactive(agent_name: str | None = None) -> None:
    """Interactive toolbox shell."""
    from qd_evolve.core.toolbox import (
        get_state, set_state, toggle as tb_toggle,
    )

    label = f" (agent: {agent_name})" if agent_name else ""
    console.print(f"[bold]Toolbox[/bold]{label} — manage tool state (enabled / preload / disabled)")
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
            _toolbox_list(args, agent_name=agent_name)
        elif action == "toggle":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                new = tb_toggle(section, name, agent_name=agent_name)
                console.print(f"  {args[0]} → [cyan]{new}[/cyan]")
            else:
                console.print("  Usage: toggle <name>")
        elif action == "enable":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                set_state(section, name, "enabled", agent_name=agent_name)
                console.print(f"  {args[0]} → [green]enabled[/green]")
            else:
                console.print("  Usage: enable <name>")
        elif action == "disable":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                set_state(section, name, "disabled", agent_name=agent_name)
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
                    set_state(section, name, "preload", agent_name=agent_name)
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


def _toolbox_list(args: list[str], agent_name: str | None = None) -> None:
    from qd_evolve.core.toolbox import get_state, get_disabled_bridges
    from qd_evolve.core.registry import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.core.config import SKILLS_DIR, CLI_TOOLS_DIR, load_settings
    from tools.bridge import BridgeManager

    settings = load_settings()
    PAGE_SIZE = settings.ui.page_size
    section_arg = args[0].lower() if args else "all"

    # Build data
    registry = get_registry()
    builtin: list[tuple[str, str, str]] = []
    bridge_tools: dict[str, list[tuple[str, str, str]]] = {}
    for td in registry.list_tools():
        state = get_state("tools", td.name, agent_name=agent_name)
        desc = td.description or ""
        if desc.startswith("[") and "]" in desc:
            bracket_end = desc.index("]")
            server = desc[1:bracket_end]
            bridge_tools.setdefault(server, []).append((td.name, desc, state))
        else:
            builtin.append((td.name, desc, state))

    # Skills
    sr = SkillRegistry()
    sr.discover_skills(SKILLS_DIR)
    skills_data: list[tuple[str, str, str]] = []
    for s in sr._skills.values():
        skills_data.append((s.name, s.summary or "", get_state("skills", s.name, agent_name=agent_name)))

    # CLI
    cr = CLIRegistry()
    cr.discover(CLI_TOOLS_DIR)
    cli_data: list[tuple[str, str, str]] = []
    for t in cr._tools.values():
        cli_data.append((t.name, t.description or t.command, get_state("cli", t.name, agent_name=agent_name)))

    # Bridge entries
    bridge_entries = BridgeManager.list_all(settings)
    disabled_bridges = get_disabled_bridges(agent_name=agent_name)

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

    def _print_bridge_entries() -> None:
        if not bridge_entries:
            return
        console.print("\n[bold]Bridges[/bold]")
        for be in bridge_entries:
            tools = bridge_tools.get(be.name, [])
            srv_state = "disabled" if f"{be.bridge_type}:{be.name}" in disabled_bridges else "enabled"
            mark = state_mark(srv_state)
            style = "dim" if srv_state == "disabled" else ""
            total = len(tools)
            active = sum(1 for _, _, s in tools if s != "disabled")
            summary = f"{active}/{total} tools" if total else "tools visible during chat"
            console.print(f"  {mark} [cyan]{be.bridge_type}:{be.name}[/cyan] {style}—{summary}")
            if total > 0:
                console.print(f"    [dim]ls {be.name} to expand[/dim]")

    page = 0
    if len(args) > 1 and args[1].isdigit():
        page = int(args[1]) - 1

    if section_arg in ("all", "tools"):
        _print_items("Builtin Tools", builtin, page)
    if section_arg in ("all", "cli"):
        _print_items("CLI Tools", cli_data, page)
    if section_arg in ("all", "skills"):
        _print_items("Skills", skills_data, page)
    if section_arg in ("all", "bridge"):
        _print_bridge_entries()
    # Expand a specific bridge's tools
    for be in bridge_entries:
        if section_arg == be.name and be.name in bridge_tools:
            _print_items(f"Bridge: {be.name} ({be.bridge_type})", bridge_tools[be.name], page)
            break


def _resolve_section(name: str) -> str:
    if name.startswith("mcp:") or name.startswith("oat:") or ":" in name:
        return name.split(":")[0] + "_bridge" if not name.startswith("mcp:") and not name.startswith("oat:") else "bridge"
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

    # On Windows with TERM=xterm-256color (VS Code/Git Bash), prompt_toolkit
    # tries Win32Output which fails. Fall back to Vt100_Output + Vt100Input.
    import sys
    import os
    try:
        return PromptSession("You> ", completer=completer)
    except Exception:
        if os.name == "nt" and os.environ.get("TERM"):
            from prompt_toolkit.output.vt100 import Vt100_Output
            from prompt_toolkit.input.vt100 import Vt100Input
            output = Vt100_Output.from_pty(sys.stdout)
            input_stream = Vt100Input(sys.stdin)
            return PromptSession("You> ", completer=completer, output=output, input=input_stream)
        raise


async def _read_input_async(session: "PromptSession | ReplayInput", dot_counter: list[int] | None = None) -> str:
    """Read user input from prompt_toolkit or replay session.

    When dot_counter is a list, its [0] element tracks silent heartbeat count.
    A bottom_toolbar renders accumulated dots without disturbing the prompt area.
    """
    if isinstance(session, ReplayInput):
        result = await asyncio.to_thread(session.prompt)
        return result.strip()
    if dot_counter is not None:
        def _rprompt() -> str:
            n = dot_counter[0]
            return f"♡ {n}" if n else ""
        try:
            result = await session.prompt_async(
                rprompt=_rprompt,
                refresh_interval=1,
            )
        except KeyboardInterrupt:
            raise EOFError
    else:
        try:
            result = await session.prompt_async()
        except KeyboardInterrupt:
            raise EOFError
    return result.strip()


async def _handle_slash_command(
    cmd: str,
    agent: Any,
    settings: Settings,
    skill_registry: SkillRegistry,
    cli_registry: CLIRegistry,
    memory: MemoryStore | None = None,
    agent_entry: Any = None,
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
            choice = (await model_session.prompt_async()).strip()
        except (EOFError, KeyboardInterrupt):
            return "  Cancelled."
        if choice.isdigit() and 1 <= int(choice) <= len(all_models):
            prov_name, mname = all_models[int(choice) - 1]
            agent._provider_name = prov_name
            agent._model = mname
            if agent_entry:
                agent_entry.provider = prov_name
                agent_entry.model = mname
            save_json(settings.model_dump(), CONFIG_PATH)
            logger.info("CLI: switched model to %s/%s and saved config", prov_name, mname)
            return f"  Switched to {prov_name}/{mname}"
        return "  Cancelled."
    if name == "/status":
        prov_name = agent._provider_name or (agent_entry.effective_provider(settings) if agent_entry else settings.default_provider)
        model_name = agent._model or (agent_entry.effective_model(settings) if agent_entry else settings.default_model)
        lines = [f"  [bold]Provider:[/bold] {prov_name}/{model_name}"]

        preload_tools = sorted(agent._always_active)
        loaded_tools = sorted(agent._active_tools - agent._always_active)
        if preload_tools:
            lines.append(f"  [bold]Tool (preload):[/bold] {', '.join(preload_tools)}")
        if loaded_tools:
            lines.append(f"  [bold]Tool (loaded):[/bold] {', '.join(loaded_tools)}")

        preload_skills = sorted(agent._preload_skills)
        loaded_skills = sorted(s for s in agent._loaded_skill_names if s not in agent._preload_skills)
        if preload_skills:
            lines.append(f"  [bold]Skill (preload):[/bold] {', '.join(preload_skills)}")
        if loaded_skills:
            lines.append(f"  [bold]Skill (loaded):[/bold] {', '.join(loaded_skills)}")

        preload_cli = sorted(agent._preload_cli)
        loaded_cli = sorted(c for c in agent._loaded_cli_names if c not in agent._preload_cli)
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
    if name == "/agents":
        agent_list = settings.agents_config.agents
        if not agent_list:
            return "  (no agents configured in config.json)"
        table = Table(title="Available Agents", show_header=True)
        table.add_column("#", style="dim")
        table.add_column("Agent", style="bold cyan")
        table.add_column("Provider/Model", style="bold")
        table.add_column("Server", style="dim")
        current = settings.agents_config.chat_agent
        for i, a in enumerate(agent_list, 1):
            marker = " *" if a.name == current else ""
            prov = a.effective_provider(settings)
            mdl = a.effective_model(settings)
            srv = f"{a.server.host}:{a.server.port}"
            table.add_row(str(i), a.name + marker, f"{prov}/{mdl}", srv)
        console.print(table)
        try:
            from prompt_toolkit import PromptSession
            agent_session = PromptSession("Switch to #: ")
            choice = (await agent_session.prompt_async()).strip()
        except (EOFError, KeyboardInterrupt):
            return "  Cancelled."
        if choice.isdigit() and 1 <= int(choice) <= len(agent_list):
            target = agent_list[int(choice) - 1]
            settings.agents_config.chat_agent = target.name
            agent._provider_name = target.effective_provider(settings)
            agent._model = target.effective_model(settings)
            save_json(settings.model_dump(), CONFIG_PATH)
            logger.info("CLI: switched to agent '%s' (%s/%s)", target.name, agent._provider_name, agent._model)
            return f"  Switched to agent '{target.name}' ({agent._provider_name}/{agent._model})"
        return "  Cancelled."
    return None


async def _async_chat_loop(
    input_session: "PromptSession | ReplayInput",
    agent: Any,
    settings: Settings,
    skill_registry: SkillRegistry,
    cli_registry: CLIRegistry,
    memory: MemoryStore,
    template_mgr: PromptTemplateManager,
    bridges: list[Any],
    staged_bridges: list[Any],
    providers: ProviderRegistry,
    output_file: Any,
    a2a_server: Any = None,
    agent_config_server: Any = None,
    agent_entry: Any = None,
) -> None:
    """Async main chat loop — event-driven: each event is processed independently."""

    # Start A2A HTTP server
    if a2a_server and agent_config_server:
        host = agent_config_server.host
        port = agent_config_server.port
        await a2a_server.start(host=host, port=port)
        console.print(f"[dim]A2A server running on {host}:{port}[/dim]")

    dot_counter = [0]  # mutable counter for silent heartbeats → toolbar dots
    input_task = hb_task = None

    try:
      while True:
        input_task = asyncio.ensure_future(_read_input_async(input_session, dot_counter))

        while True:
            hb_coro = agent.create_heartbeat_coro()
            if hb_coro is None:
                dot_counter[0] = 0
                try:
                    user_input = (await input_task).strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return
                break

            hb_task = asyncio.ensure_future(hb_coro)
            try:
                done, pending = await asyncio.wait(
                    [input_task, hb_task], return_when=asyncio.FIRST_COMPLETED,
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye![/dim]")
                return

            # If input_task died unexpectedly (not cancelled), exit
            if input_task.done():
                exc = input_task.exception()
                if exc is not None and not isinstance(exc, CancelledError):
                    console.print("\n[dim]Goodbye![/dim]")
                    return

            if hb_task in done:
                try:
                    response = hb_task.result()
                except Exception as e:
                    logger.warning("Heartbeat task failed: %s", e)
                    continue
                if response is not None:
                    # Case 3: speaking heartbeat → display as assistant message
                    dot_counter[0] = 0
                    input_task.cancel()
                    try:
                        await input_task
                    except (CancelledError, EOFError, KeyboardInterrupt):
                        pass
                    console.print(response)
                    break  # exit inner loop → recreate input_task
                else:
                    # Case 2: silent heartbeat → increment dot counter
                    dot_counter[0] += 1
                continue

            # User input arrived
            dot_counter[0] = 0
            hb_task.cancel()
            try:
                await hb_task
            except (CancelledError, Exception):
                pass
            try:
                user_input = input_task.result().strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye![/dim]")
                return
            break  # exit inner loop

        if user_input is None:
            continue  # heartbeat spoke, restart fresh
        if not user_input:
            continue
        if user_input.startswith("/"):
            result = await _handle_slash_command(user_input, agent, settings, skill_registry, cli_registry, memory, agent_entry=agent_entry)
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                break
            if result:
                console.print(result)
            continue

        spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))
        iteration_lines: list[str] = []
        output_lines: list[str] = []
        def _on_status(text: str) -> None:
            iteration_lines.append(text)
            _refresh()
        def _refresh() -> None:
            items = []
            for line in iteration_lines:
                items.append(Text(line, style="bold green"))
            items.append(spinner)
            for line in output_lines:
                items.append(Text.from_markup(line, style="dim cyan"))
            live.update(Group(*items))
        agent.set_status_callback(_on_status)
        agent.set_print_callback(lambda text: (output_lines.append(text), _refresh()))
        with Live(Group(spinner), console=console, refresh_per_second=settings.ui.refresh_per_second) as live:
            try:
                response = agent.run(user_input,
                    provider=agent_entry.effective_provider(settings) if agent_entry else settings.default_provider,
                    model=agent_entry.effective_model(settings) if agent_entry else settings.default_model)
                skill_registry.reload()
                cli_registry.reload()
                bridges = BridgeManager.reload(settings, bridges)
            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted.[/dim]")
                continue
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                continue
        console.print(f"[bold]Assistant:[/bold] {response}")
        prov = providers.get(agent._provider_name)
        model_name = agent._model or (agent_entry.effective_model(settings) if agent_entry else settings.default_model)
        ctx = prov.get_context_window(model_name)
        max_tok = prov.get_max_tokens(model_name)
        last_in = agent.last_input_tokens
        last_out = agent.last_output_tokens
        pct_ctx = f" ({last_in / ctx * 100:.1f}% of {ctx} context)" if ctx > 0 else ""
        pct_max = f" ({last_out / max_tok * 100:.1f}% of {max_tok} max)" if max_tok > 0 else ""
        console.print(f"[dim]This turn: {last_in} in{pct_ctx} + {last_out} out{pct_max}[/dim]")
        console.print(f"[dim]Cumulative: {agent.total_input_tokens + agent.total_output_tokens} tokens used[/dim]")

    except KeyboardInterrupt:
        for t in (input_task, hb_task):
            if t is None:
                continue
            if not t.done():
                t.cancel()
            try:
                t.result()
            except BaseException:
                pass
        console.print("\n[dim]Goodbye![/dim]")

    for b in bridges + staged_bridges:
        try:
            b.disconnect(shutdown=True)
        except Exception:
            logger.debug("shutdown: bridge disconnect failed for %s", b, exc_info=True)
    from qd_evolve.tools.staging import cleanup_staging
    cleanup_staging()
    memory.close()
    if output_file:
        output_file.close()


@app.callback(invoke_without_command=True)
def chat(
    ctx: typer.Context,
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return  # toolbox or another subcommand was invoked
    from qd_evolve.agent.agent import Agent
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH

    # 1. Config & logging
    setup_logging("WARNING", log_dir=LOG_DIR_PATH)
    settings = load_settings()
    setup_logging(settings.log.level, log_dir=LOG_DIR_PATH)

    # Inject env_vars from config into os.environ
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not settings.is_configured:
        console.print("[red]Error:[/red] No API key configured. Edit config.json")
        raise SystemExit(1)

    # 2. Builtin tools
    registry = get_registry()

    # 3. Skills
    skill_registry = SkillRegistry()
    skill_registry.discover_skills(SKILLS_DIR)

    # 4. CLI tools
    cli_registry = CLIRegistry()
    cli_registry.discover(CLI_TOOLS_DIR)

    # 5. Bridges (MCP + OAT + ...)
    bridges = BridgeManager.connect_all(settings)

    # 5b. Apply toolbox state from toolbox.json (per-agent if configured)
    from qd_evolve.core.toolbox import (
        apply_to_tools, apply_to_cli_registry, apply_to_skill_registry,
        get_preloaded,
    )
    agent_name = settings.agents_config.chat_agent
    loaded_tool_names: set[str] = get_preloaded("tools", agent_name=agent_name)
    loaded_skill_names: set[str] = get_preloaded("skills", agent_name=agent_name)
    loaded_cli_names: set[str] = get_preloaded("cli", agent_name=agent_name)
    apply_to_tools(registry, loaded_tool_names, agent_name=agent_name)
    apply_to_cli_registry(cli_registry, loaded_cli_names, agent_name=agent_name)
    apply_to_skill_registry(skill_registry, loaded_skill_names, agent_name=agent_name)

    # 6. Inject registries into loader tools
    from qd_evolve.tools.skill_loader import set_skill_registry
    set_skill_registry(skill_registry)
    from qd_evolve.tools.tool_loader import set_preload_tools
    set_preload_tools(loaded_tool_names)
    from qd_evolve.tools.cli_loader import set_cli_registry
    set_cli_registry(cli_registry)
    from qd_evolve.tools.install_skill import set_skill_registry as set_install_skill_registry
    set_install_skill_registry(skill_registry)
    from qd_evolve.tools.install_mcp import set_staged_bridges
    staged_bridges: list[Any] = []
    set_staged_bridges(staged_bridges)

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

    # Summarise system prompt composition
    total_tools = len(registry.list_tools())
    unloaded_count = sum(1 for l in (unloaded_tools or "").splitlines() if l.startswith("- "))
    unloaded_skill_count = sum(1 for l in (unloaded_skills or "").splitlines() if l.startswith("- "))
    unloaded_cli_count = sum(1 for l in (unloaded_cli or "").splitlines() if l.startswith("- "))
    logger.debug(
        "Prompt: %d tools total (%d preload, %d unloaded), %d unloaded skills, %d unloaded cli",
        total_tools, len(loaded_tool_names), unloaded_count,
        unloaded_skill_count, unloaded_cli_count,
    )

    # 8. Find agent entry (used by memory, system prompt, A2A setup)
    agent_entry = None
    for a in settings.agents_config.agents:
        if a.name == settings.agents_config.chat_agent:
            agent_entry = a
            break

    # 9. System prompt via Jinja2 template
    system_prompt = template_mgr.render(
        "default",
        unpreloaded_skills=unloaded_skills,
        unpreloaded_cli=unloaded_cli,
        unloaded_tools=unloaded_tools,
        preloaded_skills=active_skills_content,
        preloaded_cli=active_cli_content,
        os_name=platform.system(),
        python_cmd=python_cmd,
        cwd=str(Path.cwd()),
        skills_dir=SKILLS_DIR,
        agent_name=settings.agents_config.chat_agent,
        a2a_tools=", ".join(agent_entry.a2a_tools) if agent_entry and agent_entry.a2a_tools else "",
        available_agents=", ".join(a.name for a in settings.agents_config.agents),
        agent_relations=", ".join(f"{r['from']}→{r['to']} ({r.get('mode', 'peer')})" for r in settings.agents_config.topology.relations) if settings.agents_config.topology.relations else "",
    )
    logger.debug("Agent: system prompt (%d chars)\n%s", len(system_prompt), system_prompt)

    # 10. Provider
    providers = ProviderRegistry(settings)

    # 11. Memory — use per-agent db from config
    memory_db = agent_entry.memory_db if agent_entry else DEFAULT_MEMORY_DB

    backend_name = settings.memory_search.embeddings_backend
    backend = settings.embeddings_backends.get(backend_name) if backend_name else None
    if backend is None:
        if not backend_name:
            console.print("[red]Error:[/red] No embeddings backend configured. Set memory_search.default_embeddings_backend and embeddings_backends in config.json")
        else:
            console.print(f"[red]Error:[/red] Embeddings backend '{backend_name}' not found in config.json")
        raise SystemExit(1)
    memory = MemoryStore(memory_db, backend,
                         list_all_limit=settings.memory_search.list_all_limit)

    # 12. Inject memory store and defaults into recall_memory tool
    from qd_evolve.tools.recall_memory import set_memory_store, set_default_limit
    set_memory_store(memory)
    set_default_limit(settings.memory_search.recall_memory_limit)

    agent_core = Agent(settings=settings, registry=registry, providers=providers, memory=memory,
                       default_system_prompt=system_prompt,
                       preload_tools=loaded_tool_names,
                       preload_skills=loaded_skill_names,
                       preload_cli=loaded_cli_names,
                       template_mgr=template_mgr)

    # 13. Multi-agent setup — always initialize registry, topology, transport, A2A server
    from qd_evolve.agent.server import A2AServer, TaskStore
    from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
    from qd_evolve.agent.a2a import AgentCard, AgentCapabilities

    # Build AgentCard from config
    card = AgentCard(
        name=settings.agents_config.chat_agent,
        description=agent_entry.description if agent_entry else "",
        url=f"http://localhost:{agent_entry.server.port}" if agent_entry else f"http://localhost:{DEFAULT_SERVER_PORT}",
        capabilities=AgentCapabilities(streaming=True),
    )

    # Attach card + task_store to agent_core for registry
    task_store = TaskStore()
    agent_core.card = card
    agent_core.task_store = task_store

    # Load topology and register current agent
    topology = Topology(settings)
    agent_reg = AgentRegistry(topology, current_agent=card.name)
    agent_reg.register(agent_core)
    set_agent_registry(agent_reg)

    # Set up transport router (always, so delegate_to works if called)
    from qd_evolve.tools.a2a import set_transport
    from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter
    router = TransportRouter(InprocTransport(), HttpTransport())
    set_transport(router)
    logger.info("A2A: registry + transport initialized for agent '%s'", card.name)

    # Always start A2A HTTP server
    a2a_server = None
    if agent_entry and agent_entry.server:
        a2a_server = A2AServer(
            agent_core=agent_core,
            card=card,
            task_store=task_store,
        )
        a2a_host = agent_entry.server.host
        a2a_port = agent_entry.server.port
        logger.info("A2A: server configured on %s:%d", a2a_host, a2a_port)

    agent = agent_core  # CLI uses AgentCore directly for chat
    agent._provider_name = agent_entry.effective_provider(settings) if agent_entry else settings.default_provider
    agent._model = agent_entry.effective_model(settings) if agent_entry else settings.default_model

    model_info = escape(f"[{agent_entry.effective_provider(settings)}/{agent_entry.effective_model(settings)}]")
    agent_label = f" ({settings.agents_config.chat_agent})" if settings.agents_config.agents else ""
    console.print(Panel(
        f"qd-evolve v{__version__}{agent_label} {model_info} - /help for commands, /quit to leave",
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

    _a2a_server_cfg = agent_entry.server
    asyncio.run(_async_chat_loop(
        input_session, agent, settings, skill_registry, cli_registry,
        memory, template_mgr, bridges, staged_bridges, providers, output_file,
        a2a_server=a2a_server, agent_config_server=_a2a_server_cfg,
        agent_entry=agent_entry,
    ))


if __name__ == "__main__":
    app()
