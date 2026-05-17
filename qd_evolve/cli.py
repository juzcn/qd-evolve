import asyncio
from asyncio import CancelledError
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import typer
from qd_evolve.core.logger import logger
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve.core.config import CONFIG_PATH, SKILLS_DIR, CLI_TOOLS_DIR, DEFAULT_SERVER_PORT, Settings, load_settings, save_json
from qd_evolve.cli_tools import CLIRegistry
from qd_evolve.core.memory import MemoryStore
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


async def _read_input_async(session: "PromptSession | ReplayInput", hb_counts: dict[str, int] | None = None) -> str:
    """Read user input from prompt_toolkit or replay session.

    When hb_counts is a dict, it tracks per-agent silent heartbeat counts.
    An rprompt renders agent heartbeat dots on the right side of the prompt.
    """
    if isinstance(session, ReplayInput):
        result = await asyncio.to_thread(session.prompt)
        return result.strip()
    if hb_counts is not None:
        _agent_colors = ["cyan", "magenta", "yellow", "green", "blue", "red"]
        def _rprompt() -> "FormattedText":
            from prompt_toolkit.formatted_text import FormattedText
            fragments: list[tuple[str, str]] = []
            for i, (name, n) in enumerate(hb_counts.items()):
                color = _agent_colors[i % len(_agent_colors)]
                if fragments:
                    fragments.append(("", "  "))
                if n > 0:
                    fragments.append((f"fg:{color} bold", f"♡ {name}:{n}"))
                else:
                    fragments.append((f"fg:{color}", f"♡ {name}"))
            return FormattedText(fragments) if fragments else FormattedText([("", "")])
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
    memory: MemoryStore | None = None,
    agent_entry: Any = None,
) -> str | None:
    from qd_evolve.agent.loader import get_skill_registry, get_cli_registry
    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()
    name = cmd.lower().strip()
    if name == "/quit":
        return None
    if name == "/reset":
        if agent is not None:
            agent.reset()
            return "Conversation reset."
        return "  Reset not available for remote agents."
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
            if agent is not None:
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
        prov_name = (agent._provider_name if agent else "") or (agent_entry.effective_provider(settings) if agent_entry else settings.default_provider)
        model_name = (agent._model if agent else "") or (agent_entry.effective_model(settings) if agent_entry else settings.default_model)
        lines = [f"  [bold]Provider:[/bold] {prov_name}/{model_name}"]

        if agent is not None:
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
        else:
            lines.append("  [dim](remote agent — local status not available)[/dim]")

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
            if agent is not None:
                agent._provider_name = target.effective_provider(settings)
                agent._model = target.effective_model(settings)
            save_json(settings.model_dump(), CONFIG_PATH)
            prov = target.effective_provider(settings)
            mdl = target.effective_model(settings)
            logger.info("CLI: switched to agent '%s' (%s/%s)", target.name, prov, mdl)
            return f"  Switched to agent '{target.name}' ({prov}/{mdl})"
        return "  Cancelled."
    return None


async def _async_chat_loop(
    input_session: "PromptSession | ReplayInput",
    settings: Settings,
    output_file: Any,
    router: Any,
    chat_agent_name: str,
    agent_core: Any = None,
    a2a_server: Any = None,
    agent_config_server: Any = None,
    agent_entry: Any = None,
    inproc_agents: dict[str, Any] | None = None,
) -> None:
    """Async main chat loop — pure A2A client via transport.

    CLI never calls agent.run() directly. All communication goes through
    the transport router: tasks/send for chat, chat_subscribe for heartbeat.
    For inproc agents, InprocTransport wraps the local AgentCore.
    For HTTP agents, HttpTransport talks to the remote serve process.
    """
    from qd_evolve.agent.loader import get_skill_registry, get_cli_registry, get_bridges

    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()
    bridges = get_bridges()
    providers = ProviderRegistry(settings)
    memory = agent_core.memory if agent_core else None

    # Start A2A HTTP servers for all inproc agents
    if a2a_server and agent_config_server:
        host = agent_config_server.host
        port = agent_config_server.port
        try:
            await a2a_server.start(host=host, port=port)
            console.print(f"[dim]A2A server running on {host}:{port}[/dim]")
        except OSError as e:
            logger.warning("A2A: failed to start server on %s:%s: %s (may already be running)", host, port, e)
            console.print(f"[dim]A2A server on {host}:{port} skipped (port in use)[/dim]")

    # Start A2A servers and heartbeat loops for non-chat inproc agents
    inproc_hb_tasks: list[asyncio.Task] = []
    if inproc_agents:
        from qd_evolve.agent.server import A2AServer
        for name, core in inproc_agents.items():
            entry = next((a for a in settings.agents_config.agents if a.name == name), None)
            if entry:
                server = A2AServer(core, core.card, core.task_store)
                try:
                    await server.start(host=entry.server.host, port=entry.server.port)
                    console.print(f"[dim]A2A server for '{name}' running on {entry.server.host}:{entry.server.port}[/dim]")
                except OSError as e:
                    logger.warning("A2A: failed to start server for '%s' on %s:%s: %s", name, entry.server.host, entry.server.port, e)
                    console.print(f"[dim]A2A server for '{name}' on {entry.server.host}:{entry.server.port} skipped[/dim]")
            # Start heartbeat loop for inproc non-chat agents
            if name != chat_agent_name:
                hb_seconds = settings.heartbeat_idle_seconds
                if hb_seconds > 0:
                    async def _hb_loop(agent_core: Any, seconds: int) -> None:
                        while True:
                            await asyncio.sleep(seconds)
                            try:
                                await asyncio.to_thread(agent_core.heartbeat_check, seconds)
                            except Exception as e:
                                logger.debug("Inproc heartbeat for '%s' failed: %s", agent_core.card.name, e)
                    inproc_hb_tasks.append(asyncio.ensure_future(_hb_loop(core, hb_seconds)))

    hb_idle = settings.heartbeat_idle_seconds
    all_agent_names = [a.name for a in settings.agents_config.agents]
    hb_counts: dict[str, int] = {name: 0 for name in all_agent_names}

    def _handle_event(agent_name: str, event: dict) -> None:
        """Process an event from any agent — update hb_counts, display heartbeat."""
        etype = event.get("type", "")
        if etype == "heartbeat":
            hb_counts[agent_name] = 0
            console.print(f"[bold]Assistant ({agent_name}):[/bold] {event.get('content', '')}")
        elif etype == "heartbeat_silent":
            hb_counts[agent_name] += 1

    # --- Inproc mode: old pattern (callbacks + heartbeat_coro) ---
    if agent_core is not None:
        iteration_lines: list[str] = []
        output_lines: list[str] = []

        def _on_status(text: str) -> None:
            iteration_lines.append(text)
            _refresh()

        def _on_print(text: str) -> None:
            output_lines.append(text)
            _refresh()

        def _refresh() -> None:
            items = [Text(line, style="bold green") for line in iteration_lines]
            items.append(spinner)
            for line in output_lines:
                items.append(Text.from_markup(line, style="dim cyan"))
            live.update(Group(*items))

        agent_core.set_status_callback(_on_status)
        agent_core.set_print_callback(_on_print)

        # Event queue for other agents' heartbeat events
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        event_workers: list[asyncio.Task] = []

        async def _inproc_event_worker(name: str, core: Any) -> None:
            """Subscribe to inproc agent events directly (zero latency).
            Heartbeat events are handled by hb_task for chat_agent, so skip them here."""
            queue = core.subscribe_events()
            try:
                while True:
                    event = await queue.get()
                    if name == chat_agent_name and event.get("type", "") in ("heartbeat", "heartbeat_silent"):
                        continue
                    await event_queue.put((name, event))
            except asyncio.CancelledError:
                pass
            finally:
                core.unsubscribe_events(queue)

        async def _remote_event_worker(name: str) -> None:
            """Subscribe to a remote agent's events via chat/subscribe SSE."""
            retry_delay = 5
            while True:
                try:
                    async for agent_name, event in _event_collector(
                        router.chat_subscribe(name), agent_name=name,
                    ):
                        await event_queue.put((agent_name, event))
                        retry_delay = 5
                except asyncio.CancelledError:
                    return
                except Exception:
                    logger.debug("Event worker for '%s': retrying in %ds", name, retry_delay)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

        if hb_idle > 0:
            # Inproc agents: direct subscribe_events() — zero latency
            for name, core in inproc_agents.items():
                event_workers.append(asyncio.ensure_future(_inproc_event_worker(name, core)))
            # Remote agents: SSE via HttpTransport
            for name in all_agent_names:
                if name not in inproc_agents:
                    event_workers.append(asyncio.ensure_future(_remote_event_worker(name)))

        input_task = hb_task = None

        try:
          while True:
            input_task = asyncio.ensure_future(_read_input_async(input_session, hb_counts))

            while True:
                hb_coro = agent_core.create_heartbeat_coro()
                if hb_coro is None:
                    for k in hb_counts:
                        hb_counts[k] = 0
                    try:
                        user_input = (await input_task).strip()
                    except (EOFError, KeyboardInterrupt):
                        console.print("\n[dim]Goodbye![/dim]")
                        return
                    break

                hb_task = asyncio.ensure_future(hb_coro)
                event_wait = asyncio.ensure_future(event_queue.get())
                try:
                    done, pending = await asyncio.wait(
                        [input_task, hb_task, event_wait], return_when=asyncio.FIRST_COMPLETED,
                    )
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return

                if input_task.done():
                    exc = input_task.exception()
                    if exc is not None and not isinstance(exc, CancelledError):
                        console.print("\n[dim]Goodbye![/dim]")
                        return

                # Event arrived from any agent (inproc or remote)
                if event_wait in done:
                    try:
                        agent_name, event = event_wait.result()
                    except Exception:
                        pass
                    else:
                        _handle_event(agent_name, event)
                    # hb_task / input_task not done yet — re-wait
                    continue

                if hb_task in done:
                    try:
                        response = hb_task.result()
                    except Exception as e:
                        logger.debug("Heartbeat failed: %s", e)
                        await asyncio.sleep(5)
                        event_wait.cancel()
                        try:
                            await event_wait
                        except (CancelledError, Exception):
                            pass
                        continue
                    # Speaking heartbeat — cancel input, display response
                    if response is not None:
                        hb_counts[chat_agent_name] = 0
                        input_task.cancel()
                        try:
                            await input_task
                        except (CancelledError, EOFError, KeyboardInterrupt):
                            pass
                        event_wait.cancel()
                        try:
                            await event_wait
                        except (CancelledError, Exception):
                            pass
                        console.print(f"[bold]Assistant:[/bold] {response}")
                        user_input = None
                        break
                    # Silent heartbeat — increment counter
                    hb_counts[chat_agent_name] += 1
                    event_wait.cancel()
                    try:
                        await event_wait
                    except (CancelledError, Exception):
                        pass
                    continue

                # User input arrived
                for k in hb_counts:
                    hb_counts[k] = 0
                hb_task.cancel()
                try:
                    await hb_task
                except (CancelledError, Exception):
                    pass
                event_wait.cancel()
                try:
                    await event_wait
                except (CancelledError, Exception):
                    pass
                try:
                    user_input = input_task.result().strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return
                break

            if user_input is None:
                continue
            if not user_input:
                continue
            if user_input.startswith("/"):
                result = await _handle_slash_command(user_input, agent_core, settings, memory, agent_entry=agent_entry)
                if result is None:
                    console.print("[dim]Goodbye![/dim]")
                    break
                if result:
                    console.print(result)
                continue

            # Chat — direct agent.run() with Live display (old pattern)
            iteration_lines.clear()
            output_lines.clear()
            spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))
            with Live(Group(spinner), console=console, refresh_per_second=10) as live:
                try:
                    response = await asyncio.to_thread(agent_core.run, user_input)
                except Exception as e:
                    response = f"[red]Error:[/red] {e}"

            console.print(f"[bold]Assistant:[/bold] {response}")

            # Token stats — read directly from agent_core
            try:
                prov = providers.get(agent_core._provider_name)
                model_name = agent_core._model or settings.default_model
                ctx = prov.get_context_window(model_name)
                max_tok = prov.get_max_tokens(model_name)
                last_in = agent_core.last_input_tokens
                last_out = agent_core.last_output_tokens
                pct_ctx = f" ({last_in / ctx * 100:.1f}% of {ctx} context)" if ctx > 0 else ""
                pct_max = f" ({last_out / max_tok * 100:.1f}% of {max_tok} max)" if max_tok > 0 else ""
                console.print(f"[dim]This turn: {last_in} in{pct_ctx} + {last_out} out{pct_max}[/dim]")
                console.print(f"[dim]Cumulative: {agent_core.total_input_tokens + agent_core.total_output_tokens} tokens used[/dim]")
            except (KeyError, ZeroDivisionError):
                pass

            skill_registry.reload()
            cli_registry.reload()
            bridges = BridgeManager.reload(settings, bridges)

        except KeyboardInterrupt:
            if input_task and not input_task.done():
                input_task.cancel()
                try:
                    input_task.result()
                except BaseException:
                    pass
            if hb_task and not hb_task.done():
                hb_task.cancel()
            for t in event_workers:
                if not t.done():
                    t.cancel()
            console.print("\n[dim]Goodbye![/dim]")

    # --- HTTP mode: SSE event stream ---
    else:
        event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        event_workers: list[asyncio.Task] = []

        async def _inproc_event_worker(name: str, core: Any) -> None:
            """Subscribe to inproc agent events directly (zero latency)."""
            queue = core.subscribe_events()
            try:
                while True:
                    event = await queue.get()
                    await event_queue.put((name, event))
            except asyncio.CancelledError:
                pass
            finally:
                core.unsubscribe_events(queue)

        async def _remote_event_worker(name: str) -> None:
            """Subscribe to a remote agent's events via chat/subscribe SSE."""
            retry_delay = 5
            while True:
                try:
                    async for agent_name, event in _event_collector(
                        router.chat_subscribe(name), agent_name=name,
                    ):
                        await event_queue.put((agent_name, event))
                        retry_delay = 5
                except asyncio.CancelledError:
                    return
                except Exception:
                    logger.debug("Event worker for '%s': retrying in %ds", name, retry_delay)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

        if hb_idle > 0:
            # Inproc agents: direct subscribe_events() — zero latency
            for name, core in (inproc_agents or {}).items():
                event_workers.append(asyncio.ensure_future(_inproc_event_worker(name, core)))
            # Remote agents: SSE via HttpTransport
            for name in all_agent_names:
                if name not in (inproc_agents or {}):
                    event_workers.append(asyncio.ensure_future(_remote_event_worker(name)))

        input_task = None

        try:
          while True:
            input_task = asyncio.ensure_future(_read_input_async(input_session, hb_counts))

            while True:
                if hb_idle <= 0:
                    for k in hb_counts:
                        hb_counts[k] = 0
                    try:
                        user_input = (await input_task).strip()
                    except (EOFError, KeyboardInterrupt):
                        console.print("\n[dim]Goodbye![/dim]")
                        return
                    break

                # Wait for either user input or an event
                event_wait = asyncio.ensure_future(event_queue.get())
                try:
                    done, pending = await asyncio.wait(
                        [input_task, event_wait], return_when=asyncio.FIRST_COMPLETED,
                    )
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return

                if input_task.done():
                    exc = input_task.exception()
                    if exc is not None and not isinstance(exc, CancelledError):
                        console.print("\n[dim]Goodbye![/dim]")
                        return

                # Event arrived
                if event_wait in done:
                    try:
                        agent_name, event = event_wait.result()
                    except Exception:
                        continue
                    _handle_event(agent_name, event)
                    etype = event.get("type", "")
                    if etype == "heartbeat" and agent_name == chat_agent_name:
                        input_task.cancel()
                        try:
                            await input_task
                        except (CancelledError, EOFError, KeyboardInterrupt):
                            pass
                        user_input = None
                        break
                    continue

                # User input arrived — cancel event_wait
                event_wait.cancel()
                try:
                    await event_wait
                except (CancelledError, Exception):
                    pass
                try:
                    user_input = input_task.result().strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return
                break

            if user_input is None:
                continue
            if not user_input:
                continue
            if user_input.startswith("/"):
                result = await _handle_slash_command(user_input, agent_core, settings, memory, agent_entry=agent_entry)
                if result is None:
                    console.print("[dim]Goodbye![/dim]")
                    break
                if result:
                    console.print(result)
                continue

            # Chat via transport with SSE iteration display
            from qd_evolve.agent.a2a import Message, Part
            iteration_lines: list[str] = []
            output_lines: list[str] = []
            last_tokens_event: dict | None = None
            spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))

            def _refresh() -> None:
                items = [Text(line, style="bold green") for line in iteration_lines]
                items.append(spinner)
                for line in output_lines:
                    items.append(Text.from_markup(line, style="dim cyan"))
                live.update(Group(*items))

            with Live(Group(spinner), console=console, refresh_per_second=10) as live:
                try:
                    msg = Message(role="user", parts=[Part(type="text", text=user_input)])
                    send_task = asyncio.ensure_future(router.send_task(chat_agent_name, msg))
                    while not send_task.done():
                        try:
                            agent_name, event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        etype = event.get("type", "")
                        if etype == "status":
                            iteration_lines.append(event["text"])
                            _refresh()
                        elif etype == "print":
                            output_lines.append(event["text"])
                            _refresh()
                        elif etype == "iteration":
                            spinner.update(text=Text("Thinking...", style="bold green"))
                            _refresh()
                        elif etype == "tokens":
                            last_tokens_event = event
                    task = send_task.result()
                    response = ""
                    if task.status and task.status.message:
                        for part in task.status.message.parts:
                            if part.type == "text" and part.text:
                                response = part.text
                                break
                    if not response and task.status and task.status.state:
                        response = f"[Task state: {task.status.state}]"
                except Exception as e:
                    response = f"[red]Error:[/red] {e}"

            console.print(f"[bold]Assistant:[/bold] {response}")

            # Token stats — from SSE event
            if last_tokens_event:
                try:
                    last_in = last_tokens_event["input"]
                    last_out = last_tokens_event["output"]
                    total_in = last_tokens_event["total_in"]
                    total_out = last_tokens_event["total_out"]
                    prov = providers.get(settings.default_provider)
                    model_name = settings.default_model
                    ctx = prov.get_context_window(model_name)
                    max_tok = prov.get_max_tokens(model_name)
                    pct_ctx = f" ({last_in / ctx * 100:.1f}% of {ctx} context)" if ctx > 0 else ""
                    pct_max = f" ({last_out / max_tok * 100:.1f}% of {max_tok} max)" if max_tok > 0 else ""
                    console.print(f"[dim]This turn: {last_in} in{pct_ctx} + {last_out} out{pct_max}[/dim]")
                    console.print(f"[dim]Cumulative: {total_in + total_out} tokens used[/dim]")
                except (KeyError, ZeroDivisionError):
                    pass

            skill_registry.reload()
            cli_registry.reload()
            bridges = BridgeManager.reload(settings, bridges)

        except KeyboardInterrupt:
            if input_task and not input_task.done():
                input_task.cancel()
                try:
                    input_task.result()
                except BaseException:
                    pass
            for t in event_workers:
                if not t.done():
                    t.cancel()
            console.print("\n[dim]Goodbye![/dim]")

    for b in bridges:
        try:
            b.disconnect(shutdown=True)
        except Exception:
            logger.debug("shutdown: bridge disconnect failed for %s", b, exc_info=True)
    from qd_evolve.tools.staging import cleanup_staging
    cleanup_staging()
    if memory:
        memory.close()
    if output_file:
        output_file.close()


async def _event_collector(event_iter: Any, agent_name: str = "") -> AsyncIterator[tuple[str, dict]]:
    """Yield (agent_name, event_dict) from a chat_subscribe stream.

    Passes all events through — heartbeat, iteration, status, print, tokens, etc.
    """
    async for event in event_iter:
        yield (agent_name, event)


@app.command()
def serve(
    agent: str = typer.Option("", "--agent", help="Agent name from config.json to serve"),
) -> None:
    """Start an agent as a standalone A2A HTTP server (for cross-process communication)."""
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH, load_settings
    from qd_evolve.agent.loader import init_process, create_agent_core
    from qd_evolve.agent.server import A2AServer

    # 1. Config & logging
    setup_logging("WARNING", log_dir=LOG_DIR_PATH)
    settings = load_settings()
    setup_logging(settings.log.level, log_dir=LOG_DIR_PATH)

    # Inject env_vars
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not agent:
        console.print("[red]Error:[/red] --agent is required. E.g. qd-evolve serve --agent test")
        raise SystemExit(1)

    # 2. Per-process init (skills, CLI tools, bridges, registry injection)
    init_process(settings)

    # 3. Create AgentCore via loader (full init: toolbox, system prompt, memory, etc.)
    agent_core = create_agent_core(agent, settings=settings)

    # 4. A2A setup — register in AgentRegistry + set transport
    from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
    from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter

    topology = Topology(settings)
    router = TransportRouter(InprocTransport(), HttpTransport())
    agent_reg = AgentRegistry(topology, current_agent=agent)
    agent_reg.register(agent_core)
    set_agent_registry(agent_reg)

    from qd_evolve.tools.a2a import set_transport
    set_transport(router)

    # 5. Start A2A server
    server = A2AServer(agent_core, agent_core.card, agent_core.task_store)
    entry = next((a for a in settings.agents_config.agents if a.name == agent), None)
    host = entry.server.host if entry else "0.0.0.0"
    port = entry.server.port if entry else DEFAULT_SERVER_PORT
    console.print(Panel(
        f"Serving agent [bold]{agent}[/bold] on {host}:{port}\nA2A v1.0 JSON-RPC + SSE",
        style="bold green",
    ))

    async def _run() -> None:
        await server.start(host=host, port=port)
        # Start heartbeat loop in background
        hb_seconds = settings.heartbeat_idle_seconds
        if hb_seconds > 0:
            async def _heartbeat_loop() -> None:
                while True:
                    await asyncio.sleep(hb_seconds)
                    try:
                        await asyncio.to_thread(agent_core.heartbeat_check, hb_seconds)
                    except Exception as e:
                        logger.warning("Serve heartbeat failed: %s", e)
            asyncio.ensure_future(_heartbeat_loop())
        # Block until Ctrl+C
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Server stopped.[/dim]")


@app.callback(invoke_without_command=True)
def chat(
    ctx: typer.Context,
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return  # toolbox or another subcommand was invoked
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH
    from qd_evolve.agent.loader import init_process, create_agent_core

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

    # 2. Per-process init (skills, CLI tools, bridges, registry injection)
    init_process(settings)

    # 3. Determine transport for chat agent
    chat_agent_name = settings.agents_config.chat_agent
    topology_transports = settings.agents_config.topology.transports
    is_http = any(
        key.endswith(f"→{chat_agent_name}") and mode == "http"
        for key, mode in topology_transports.items()
    )

    # 4. A2A setup
    from qd_evolve.agent.server import A2AServer
    from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
    from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter

    topology = Topology(settings)
    router = TransportRouter(InprocTransport(), HttpTransport())

    agent_core = None
    a2a_server = None
    a2a_server_cfg = None
    inproc_agents: dict[str, Any] = {}  # name → AgentCore for inproc agents

    # Create all non-chat inproc agents via create_agent_core
    for a in settings.agents_config.agents:
        if a.name == chat_agent_name:
            continue
        try:
            inproc_core = create_agent_core(a.name, settings=settings)
            inproc_agents[a.name] = inproc_core
            logger.info("A2A: inproc agent '%s' — created in CLI process", a.name)
        except Exception as e:
            logger.warning("A2A: failed to create inproc agent '%s': %s", a.name, e)

    if not is_http:
        # Chat agent is inproc: create via create_agent_core
        agent_core = create_agent_core(chat_agent_name, settings=settings)
        inproc_agents[chat_agent_name] = agent_core

        a2a_server = A2AServer(agent_core=agent_core, card=agent_core.card, task_store=agent_core.task_store)
        a2a_server_cfg = next((a.server for a in settings.agents_config.agents if a.name == chat_agent_name), None)
        logger.info("A2A: inproc agent '%s' — server on %s:%d", chat_agent_name,
                     a2a_server_cfg.host, a2a_server_cfg.port)
    else:
        logger.info("A2A: HTTP agent '%s' — CLI is pure client", chat_agent_name)

    # Register all inproc agents in AgentRegistry
    agent_reg = AgentRegistry(topology, current_agent=chat_agent_name if not is_http else "")
    for name, core in inproc_agents.items():
        agent_reg.register(core)
    set_agent_registry(agent_reg)

    from qd_evolve.tools.a2a import set_transport
    set_transport(router)

    # Build startup panel
    agents = settings.agents_config.agents
    chat_agent_entry = next((a for a in agents if a.name == chat_agent_name), None)
    # Determine transport label per agent
    def _transport_label(name: str) -> str:
        for key, mode in topology_transports.items():
            if key.endswith(f"→{name}") and mode == "http":
                port = next((a.server.port for a in agents if a.name == name), DEFAULT_SERVER_PORT)
                return f"HTTP :{port}"
        return "inproc"

    if len(agents) > 1:
        max_name_len = max(len(a.name) for a in agents)
        agent_lines = []
        for a in agents:
            prov = a.effective_provider(settings)
            mdl = a.effective_model(settings)
            name_col = f"{a.name:<{max_name_len}}"
            transport = _transport_label(a.name)
            if a.name == chat_agent_name:
                agent_lines.append(f"  [bold]► {name_col}[/bold]  {prov}/{mdl}  [{transport}]")
            else:
                agent_lines.append(f"    {name_col}  {prov}/{mdl}  [{transport}]")
        chat_transport = "HTTP" if is_http else "inproc"
        panel_text = (
            f"qd-evolve v{__version__}\n\n"
            + "\n".join(agent_lines)
            + f"\n\nChat: {chat_agent_name} (via {chat_transport})"
            + f"\n/help for commands, /quit to leave"
        )
    else:
        agent_label = f" ({chat_agent_name})" if agents else ""
        model_info = escape(f"[{chat_agent_entry.effective_provider(settings)}/{chat_agent_entry.effective_model(settings)}]") if chat_agent_entry else ""
        panel_text = f"qd-evolve v{__version__}{agent_label} {model_info} - /help for commands, /quit to leave"

    console.print(Panel(
        panel_text,
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

    asyncio.run(_async_chat_loop(
        input_session, settings, output_file,
        router=router, chat_agent_name=chat_agent_name,
        agent_core=agent_core, a2a_server=a2a_server,
        agent_config_server=a2a_server_cfg, agent_entry=chat_agent_entry,
        inproc_agents=inproc_agents,
    ))


if __name__ == "__main__":
    app()
