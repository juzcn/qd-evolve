"""Chat CLI — single in-process agent, no A2A."""

import asyncio
from asyncio import CancelledError
from pathlib import Path
from typing import Any

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve import __version__
from qd_evolve.core.config import CONFIG_PATH, Settings, load_settings, save_json
from qd_evolve.core.logger import logger
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import get_registry

app = typer.Typer(help="qd-evolve AI agent")
console = Console()


def _friendly_name(settings: Settings, name: str) -> str:
    """Resolve friendly_name for an agent, falling back to name."""
    entry = next((a for a in settings.agents_config.agents if a.name == name), None)
    return entry.effective_friendly_name() if entry else name


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


def _make_prompt_session() -> "PromptSession":
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import FormattedText

    completer = WordCompleter(
        list(SLASH_COMMANDS.keys()),
        ignore_case=True,
        sentence=True,
        meta_dict=SLASH_COMMANDS,
    )
    prompt_msg = FormattedText([("fg:#74c0fc bold", "You> "), ("", "")])

    import os
    import sys
    try:
        return PromptSession(prompt_msg, completer=completer)
    except Exception:
        if os.name == "nt" and os.environ.get("TERM"):
            from prompt_toolkit.output.vt100 import Vt100_Output
            from prompt_toolkit.input.vt100 import Vt100Input
            output = Vt100_Output.from_pty(sys.stdout)
            input_stream = Vt100Input(sys.stdin)
            return PromptSession(prompt_msg, completer=completer, output=output, input=input_stream)
        raise


async def _read_input_async(session: "PromptSession | ReplayInput", hb: list[int] | None = None) -> str:
    """Read user input from prompt_toolkit or replay session.

    When hb is provided, shows heartbeat count as rprompt on the right side of You>.
    hb is a mutable [count] list so the rprompt sees live updates.
    """
    if isinstance(session, ReplayInput):
        result = await asyncio.to_thread(session.prompt)
        return result.strip()
    if hb is not None:
        def _rprompt() -> "FormattedText":
            from prompt_toolkit.formatted_text import FormattedText
            count = hb[0]
            if count <= 0:
                return FormattedText([])
            return FormattedText([(f"fg:#ff6b6b bold", f"♡ {count}")])
        try:
            result = await session.prompt_async(rprompt=_rprompt, refresh_interval=1)
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
    memory: Any = None,
    agent_entry: Any = None,
) -> str | None:
    from qd_evolve.agent import Agent, init_process, get_skill_registry, get_cli_registry
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
        table.add_column("Name", style="bold green")
        table.add_column("Provider/Model", style="bold")
        table.add_column("Server", style="dim")
        current = settings.agents_config.chat_agent
        for i, a in enumerate(agent_list, 1):
            marker = " *" if a.name == current else ""
            srv = f"{a.server.host}:{a.server.port}"
            if a.is_human:
                prov_mdl = "human"
            else:
                prov_mdl = f"{a.effective_provider(settings)}/{a.effective_model(settings)}"
            table.add_row(str(i), a.name + marker, a.effective_friendly_name(), prov_mdl, srv)
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
    agent_core: Any,
    agent_name: str,
) -> None:
    """Async main chat loop — single in-process agent, no A2A."""
    from qd_evolve.agent import Agent, A2AAgent, create_agent, init_process, get_skill_registry, get_cli_registry, get_bridges
    from tools.bridge import BridgeManager

    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()
    bridges = get_bridges()
    providers = ProviderRegistry(settings)

    hb_idle = settings.heartbeat_idle_seconds
    hb: list[int] = [0]

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

    event_queue: asyncio.Queue[dict] = asyncio.Queue()

    if hb_idle > 0:
        agent_core.start_heartbeat_loop()

        def _on_agent_event(event: dict) -> None:
            try:
                event_queue.put_nowait(event)
            except Exception:
                pass
        agent_core.set_event_callback(_on_agent_event)

    input_task = None

    try:
      while True:
        input_task = asyncio.ensure_future(_read_input_async(input_session, hb))

        while True:
            if hb_idle <= 0:
                hb[0] = 0
                try:
                    user_input = (await input_task).strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return
                break

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
                if exc is not None and not isinstance(exc, (CancelledError, EOFError)):
                    logger.warning("Input task failed: %s", exc)
                    console.print("\n[dim]Goodbye![/dim]")
                    return

            # Heartbeat event arrived
            if event_wait in done:
                try:
                    event = event_wait.result()
                except Exception:
                    continue
                etype = event.get("type", "")
                if etype == "heartbeat":
                    hb[0] = 0
                    _app = getattr(input_session, "app", None)
                    if _app and _app.is_running:
                        _app.renderer.erase()
                    console.print(f"[bold #ff6b6b]{_friendly_name(settings, agent_name)}>[/bold #ff6b6b] {event.get('content', '')}")
                    if _app and _app.is_running:
                        _app.invalidate()
                    input_task.cancel()
                    try:
                        await input_task
                    except (CancelledError, EOFError, KeyboardInterrupt):
                        pass
                    user_input = None
                    break
                elif etype == "heartbeat_silent":
                    hb[0] += 1
                    continue
                continue

            # User input arrived
            hb[0] = 0
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
            agent_entry = next((a for a in settings.agents_config.agents if a.name == agent_name), None)
            result = await _handle_slash_command(user_input, agent_core, settings, agent_core.memory, agent_entry=agent_entry)
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                break
            if result:
                console.print(result)
            continue

        # Chat — direct agent.run() with Live display
        agent_core.touch_heartbeat()
        agent_core.set_status_callback(_on_status)
        agent_core.set_print_callback(_on_print)
        iteration_lines.clear()
        output_lines.clear()
        spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))
        with Live(Group(spinner), console=console, refresh_per_second=10) as live:
            try:
                response = await asyncio.to_thread(agent_core.run, user_input)
            except Exception as e:
                response = f"[red]Error:[/red] {e}"

        console.print(f"[bold #ff6b6b]{_friendly_name(settings, agent_name)}>[/bold #ff6b6b] {response}")

        # Token stats
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
        except Exception:
            logger.debug("token stats display failed", exc_info=True)

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
        agent_core.stop_heartbeat_loop()
        console.print("\n[dim]Goodbye![/dim]")

    for b in bridges:
        try:
            b.disconnect(shutdown=True)
        except Exception:
            logger.debug("shutdown: bridge disconnect failed for %s", b, exc_info=True)
    from qd_evolve.tools.staging import cleanup_staging
    cleanup_staging()
    if agent_core.memory:
        agent_core.memory.close()
    if output_file:
        output_file.close()


@app.command()
def chat(
    agent: str = typer.Option(..., "--agent", help="Agent name from config.json to load in-process"),
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    """Start an interactive chat session with a single in-process agent."""
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH
    from qd_evolve.agent import Agent, create_agent, init_process

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

    # 3. Load the specified agent in-process (pure Agent, no A2A)
    chat_agent_name = agent
    chat_agent_entry = next((a for a in settings.agents_config.agents if a.name == chat_agent_name), None)
    if chat_agent_entry is None:
        available = [a.name for a in settings.agents_config.agents]
        console.print(f"[red]Error:[/red] Agent '{chat_agent_name}' not found. Available: {', '.join(available)}")
        raise SystemExit(1)

    agent_core = create_agent(chat_agent_name, settings=settings, need_a2a=False)

    # 4. Startup panel
    prov_name = chat_agent_entry.effective_provider(settings)
    model_name = chat_agent_entry.effective_model(settings)
    console.print(Panel(
        f"qd-evolve v{__version__}\n\n"
        f"[bold]Agent:[/bold]     {_friendly_name(settings, chat_agent_name)} ({chat_agent_name})\n"
        f"[bold]Provider:[/bold]  {prov_name}/{model_name}\n"
        f"[bold]Transport:[/bold] inproc (chat mode)\n\n"
        f"/help for commands, /quit to leave",
        style="bold green",
    ))

    # 5. Replay mode setup
    output_file = None
    if replay:
        lines = replay.read_text(encoding="utf-8").splitlines()
        inputs = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        input_session = ReplayInput(inputs)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output_file = open(output, "w", encoding="utf-8")
            tee = TeeWriter(sys.stdout, output_file)
            import qd_evolve.chat_cli as _cli_mod
            _cli_mod.console = Console(file=tee, force_terminal=True)
        logger.info("CLI: replay mode: %s inputs from %s", len(inputs), replay)
    else:
        input_session = _make_prompt_session()

    asyncio.run(_async_chat_loop(
        input_session, settings, output_file,
        agent_core=agent_core, agent_name=chat_agent_name,
    ))


if __name__ == "__main__":
    app()

from qd_evolve.a2a_cli import a2a_app
app.add_typer(a2a_app, name="a2a")

from qd_evolve.mqtt_cli import mqtt_app
app.add_typer(mqtt_app, name="mqtt")

from qd_evolve.toolbox_tui import toolbox_app
app.add_typer(toolbox_app, name="toolbox")