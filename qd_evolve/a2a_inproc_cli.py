"""A2A in-process CLI — all AI agents loaded in-process, no network.

CLI loads every configured AI agent in-process via TransportRouter(InprocTransport()).
No serve subcommand — everything runs locally with zero network overhead.
Use `qd-evolve a2a-inproc` to start.
"""

from __future__ import annotations

import asyncio
from asyncio import CancelledError
from pathlib import Path
import sys
from typing import Any, TYPE_CHECKING

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve import __version__
from qd_evolve.cli_utils import AGENT_COLORS, ReplayInput, TeeWriter
from pydantic import ValidationError
from qd_evolve.core.config import CONFIG_PATH, Settings, load_settings, save_json
from qd_evolve.core.logger import logger

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import FormattedText

a2a_inproc_app = typer.Typer(help="A2A in-process — multi-agent chat, no network server", invoke_without_command=True)
console = Console()


SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/tools": "List available tools",
    "/skills": "List available skills",
    "/models": "Pick a model to switch to",
    "/cli": "List registered CLI tools",
    "/status": "Show runtime status (loaded tools, skills, CLI)",
    "/memory": "List saved memories",
    "/reset": "Reset conversation history",
    "/agents": "Switch agent or show agent list",
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
    try:
        return PromptSession(prompt_msg, completer=completer)
    except Exception:
        if os.name == "nt" and os.environ.get("TERM"):
            from prompt_toolkit.output.vt100 import Vt100_Output  # type: ignore[import-untyped]
            from prompt_toolkit.input.vt100 import Vt100Input  # type: ignore[import-untyped]
            output = Vt100_Output.from_pty(sys.stdout)
            input_stream = Vt100Input(sys.stdin)
            return PromptSession(prompt_msg, completer=completer, output=output, input=input_stream)
        raise


async def _read_input_async(session: "PromptSession | ReplayInput", hb_counts: dict[str, int] | None = None) -> str:
    """Read user input from prompt_toolkit or replay session."""
    if isinstance(session, ReplayInput):
        result = await asyncio.to_thread(session.prompt)
        return result.strip()
    if hb_counts is not None:
        def _bottom_toolbar() -> "FormattedText":
            from prompt_toolkit.formatted_text import FormattedText
            fragments: list[tuple[str, str]] = []
            for i, (name, n) in enumerate(hb_counts.items()):
                color = AGENT_COLORS[i % len(AGENT_COLORS)]
                if n > 0:
                    fragments.append((f"fg:{color} bold", f" ♡ {name}:{n} "))
                else:
                    fragments.append((f"fg:{color}", f" ♡ {name} "))
            return FormattedText(fragments) if fragments else FormattedText([("", "")])
        try:
            result = await session.prompt_async(
                bottom_toolbar=_bottom_toolbar,
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
    settings: Settings,
    router: Any,
    agent_core: Any = None,
    agent_entry: Any = None,
    agent_map: dict[str, Any] | None = None,
) -> str | None:
    from qd_evolve.agent import get_skill_registry, get_cli_registry
    from qd_evolve.core.registry import get_registry
    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()
    name = cmd.lower().strip()
    if name == "/quit":
        return None
    if name == "/reset":
        if agent_core is not None:
            agent_core.reset()
            return "Conversation reset."
        return "  Reset not available."
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
            if agent_entry:
                agent_entry.provider = prov_name
                agent_entry.model = mname
            save_json(settings.model_dump(), CONFIG_PATH)
            logger.info("CLI: switched model to %s/%s and saved config", prov_name, mname)
            return f"  Switched to {prov_name}/{mname}"
        return "  Cancelled."
    if name == "/status":
        if agent_core is None:
            prov_name = (agent_entry.effective_provider(settings) if agent_entry else settings.default_provider)
            model_name = (agent_entry.effective_model(settings) if agent_entry else settings.default_model)
            return f"  [bold]Provider:[/bold] {prov_name}/{model_name}\n  [dim](no agent loaded)[/dim]"
        prov_name = agent_core._provider_name or settings.default_provider
        model_name = agent_core._model or settings.default_model
        lines = [f"  [bold]Provider:[/bold] {prov_name}/{model_name}"]
        preload_tools = sorted(agent_core._always_active)
        loaded_tools = sorted(agent_core._active_tools - agent_core._always_active)
        if preload_tools:
            lines.append(f"  [bold]Tool (preload):[/bold] {', '.join(preload_tools)}")
        if loaded_tools:
            lines.append(f"  [bold]Tool (loaded):[/bold] {', '.join(loaded_tools)}")
        preload_skills = sorted(agent_core._preload_skills)
        loaded_skills = sorted(s for s in agent_core._loaded_skill_names if s not in agent_core._preload_skills)
        if preload_skills:
            lines.append(f"  [bold]Skill (preload):[/bold] {', '.join(preload_skills)}")
        if loaded_skills:
            lines.append(f"  [bold]Skill (loaded):[/bold] {', '.join(loaded_skills)}")
        preload_cli = sorted(agent_core._preload_cli)
        loaded_cli = sorted(c for c in agent_core._loaded_cli_names if c not in agent_core._preload_cli)
        if preload_cli:
            lines.append(f"  [bold]CLI (preload):[/bold] {', '.join(preload_cli)}")
        if loaded_cli:
            lines.append(f"  [bold]CLI (loaded):[/bold] {', '.join(loaded_cli)}")
        return "\n".join(lines)
    if name == "/memory":
        if agent_core is None or agent_core.memory is None:
            return "  Memory store not initialized"
        entries = agent_core.memory.list_all()
        if not entries:
            return "  (no memories saved)"
        for e in entries:
            console.print(f"[bold cyan]#{e.id}[/bold cyan] [dim]{e.key}[/dim]")
            console.print(f"  [dim]session:[/dim] {e.session_id}  [dim]access:[/dim] {e.accessed_at or '-'}  [dim]count:[/dim] {e.access_count}")
            console.print(f"  {e.content}")
            console.print()
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
        table.add_column("#", style="dim", justify="right")
        table.add_column("Agent", style="cyan")
        table.add_column("Provider/Model", style="bold")
        table.add_column("Status")
        current = settings.agents_config.chat_agent
        for i, a in enumerate(agent_list, 1):
            marker = " *" if a.name == current else ""
            if a.is_human:
                prov_mdl = "human"
            else:
                prov_mdl = f"{a.effective_provider(settings)}/{a.effective_model(settings)}"
            # All inproc agents are always online
            if a.is_human and (agent_map is None or a.name not in agent_map):
                status_str = "[dim]not loaded[/dim]"
            else:
                status_str = "[green]inproc[/green]"
            table.add_row(str(i), a.name + marker, prov_mdl, status_str)
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
            if target.is_human:
                info = "human"
            else:
                info = f"{target.effective_provider(settings)}/{target.effective_model(settings)}"
            logger.info("CLI: switched to agent '%s' (%s)", target.name, info)
            return f"  Switched to agent '{target.name}' ({info})"
        return "  Cancelled."
    return None


async def _async_chat_loop(
    input_session: "PromptSession | ReplayInput",
    settings: Settings,
    output_file: Any,
    router: Any,
    event_queue: "asyncio.Queue[tuple[str, dict]]",
    agent_map: dict[str, Any],
) -> None:
    """Async main chat loop — all agents in-process via InprocTransport."""
    from qd_evolve.core.providers import ProviderRegistry
    from qd_evolve.agent.a2a import Message, Part, TaskState

    providers = ProviderRegistry(settings)

    def _current_agent_name() -> str:
        return settings.agents_config.chat_agent

    def _current_agent_entry() -> Any:
        name = _current_agent_name()
        return next((a for a in settings.agents_config.agents if a.name == name), None)

    # Only AI agents loaded in-process — human agents not relevant
    all_agent_names = sorted(agent_map.keys())
    hb_idle = settings.heartbeat_idle_seconds
    hb_counts: dict[str, int] = {name: 0 for name in all_agent_names}

    def _agent_color(name: str) -> str:
        idx = all_agent_names.index(name) if name in all_agent_names else 0
        return AGENT_COLORS[idx % len(AGENT_COLORS)]

    # All inproc agents are online by definition
    logger.info("A2A Inproc CLI: all %d agents loaded in-process", len(agent_map))

    # Event workers for inproc agent heartbeat via resubscribe
    event_workers: list[asyncio.Task] = []

    async def _inproc_event_worker(name: str) -> None:
        """Subscribe to an inproc agent's event queue via router.resubscribe."""
        while True:
            try:
                async for sr in router.resubscribe(name):
                    if sr.statusUpdate and sr.statusUpdate.metadata:
                        await event_queue.put((name, sr.statusUpdate.metadata))
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("Inproc event worker for '%s': retrying", name)
                await asyncio.sleep(2)

    # Start heartbeat loops + event workers for all AI agents
    for name, ag in agent_map.items():
        if hasattr(ag, 'start_heartbeat_loop'):
            ag.start_heartbeat_loop()
        event_workers.append(asyncio.ensure_future(_inproc_event_worker(name)))
        logger.debug("A2A Inproc CLI: heartbeat + event worker started for '%s'", name)

    input_task = None

    try:
      while True:
        input_task = asyncio.ensure_future(_read_input_async(input_session, hb_counts))

        while True:
            if hb_idle <= 0:
                hb_counts[_current_agent_name()] = 0
                try:
                    user_input = (await input_task).strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return
                break

            event_wait = asyncio.ensure_future(event_queue.get())
            logger.debug("A2A Inproc CLI: waiting for input or event, queue_size=%d", event_queue.qsize())
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

            # Event arrived from inproc event worker
            if event_wait in done:
                try:
                    agent_name, event = event_wait.result()
                except Exception:
                    continue
                etype = event.get("type", "")

                if hb_idle > 0:
                    fn = agent_name
                    if etype == "heartbeat":
                        hb_counts[fn] = 0
                        is_current = agent_name == _current_agent_name()
                        if is_current:
                            input_task.cancel()
                            try:
                                await input_task
                            except (CancelledError, EOFError, KeyboardInterrupt):
                                pass
                        color = _agent_color(agent_name)
                        content = event.get("content", "")
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                        console.print(f"[dim](heartbeat)[/dim] [bold {color}]{fn}>[/bold {color}] {content}")
                        if is_current:
                            user_input = None
                            break
                    elif etype == "heartbeat_silent":
                        hb_counts[fn] += 1
                    continue

            # User input arrived
            for k in hb_counts:
                hb_counts[k] = 0
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

        # Get current agent for slash commands
        cur_name = _current_agent_name()
        cur_agent = agent_map.get(cur_name)
        cur_entry = _current_agent_entry()

        if user_input.startswith("/"):
            result = await _handle_slash_command(
                user_input, settings, router,
                agent_core=cur_agent.agent if cur_agent and hasattr(cur_agent, 'agent') else cur_agent,
                agent_entry=cur_entry,
                agent_map=agent_map,
            )
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                break
            if result:
                console.print(result)
            continue

        # Chat via router.send_stream
        cur_entry = _current_agent_entry()
        is_human_target = cur_entry is not None and cur_entry.is_human
        msg = Message(role="user", parts=[Part(type="text", text=user_input)])
        response = ""
        last_tokens_event: dict | None = None

        if is_human_target:
            logger.info("A2A Inproc CLI: send_task -> '%s' (human agent)", _current_agent_name())
            try:
                task = await router.send_task(_current_agent_name(), msg)
                if task.status.state == TaskState.input_required:
                    console.print(f"[dim]Task sent to {_current_agent_name()}. Waiting for response...[/dim]")
                    try:
                        agent_name, event = await asyncio.wait_for(event_queue.get(), timeout=300)
                        etype = event.get("type", "")
                        if etype == "task_completed":
                            response = event.get("content", "")
                    except asyncio.TimeoutError:
                        console.print("[dim]Still waiting...[/dim]")
                else:
                    if task.status.message:
                        for part in task.status.message.parts:
                            if part.type == "text" and part.text:
                                response = part.text
                                break
                    if not response:
                        response = f"[Task state: {task.status.state}]"
            except Exception as e:
                response = f"[red]Error:[/red] {e}"
        else:
            # AI agent: send_stream with live display
            logger.info("A2A Inproc CLI: send_stream -> '%s' (%s chars)", _current_agent_name(), len(user_input))
            iteration_lines: list[str] = []
            output_lines: list[str] = []
            spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))

            def _refresh() -> None:
                items: list[Text | Spinner] = [Text(line, style="bold green") for line in iteration_lines]
                items.append(spinner)
                for line in output_lines:
                    items.append(Text.from_markup(line, style="dim cyan"))
                live.update(Group(*items))

            with Live(Group(spinner), console=console, refresh_per_second=10) as live:
                try:
                    async for sr in router.send_stream(_current_agent_name(), msg):
                        if sr.task:
                            pass
                        elif sr.statusUpdate and sr.statusUpdate.metadata:
                            event = sr.statusUpdate.metadata
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
                        elif sr.statusUpdate and sr.statusUpdate.final:
                            if sr.statusUpdate.status and sr.statusUpdate.status.message:
                                for part in sr.statusUpdate.status.message.parts:
                                    if part.type == "text" and part.text:
                                        response = part.text
                                        break
                            if not response and sr.statusUpdate.status:
                                response = f"[Task state: {sr.statusUpdate.status.state}]"
                except Exception as e:
                    response = f"[red]Error:[/red] {e}"

        logger.info("A2A Inproc CLI: received response from '%s' (%s chars)", _current_agent_name(), len(response))
        console.print(f"[bold {_agent_color(_current_agent_name())}]{_current_agent_name()}>[/bold {_agent_color(_current_agent_name())}] {response}")

        # Token stats
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
            except Exception:
                logger.debug("token stats display failed", exc_info=True)

        from qd_evolve.agent import get_skill_registry, get_cli_registry
        get_skill_registry().reload()
        get_cli_registry().reload()

    except KeyboardInterrupt:
        if input_task and not input_task.done():
            input_task.cancel()
            try:
                input_task.result()
            except BaseException:
                pass
        console.print("\n[dim]Goodbye![/dim]")

    logger.info("A2A Inproc CLI: shutting down (stopping %d event workers)", len(event_workers))
    for ag in agent_map.values():
        if hasattr(ag, 'stop_heartbeat_loop'):
            ag.stop_heartbeat_loop()
    for t in event_workers:
        if not t.done():
            t.cancel()
    if event_workers:
        try:
            await asyncio.wait_for(asyncio.gather(*event_workers, return_exceptions=True), timeout=3)
        except asyncio.TimeoutError:
            pass
    if output_file:
        output_file.close()


@a2a_inproc_app.callback()
def chat(
    ctx: typer.Context,
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    """A2A in-process chat — all AI agents loaded locally, no network."""
    if ctx.invoked_subcommand is not None:
        return
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH
    from qd_evolve.agent import create_agent, init_process

    # 1. Config & logging
    setup_logging("WARNING", log_dir=LOG_DIR_PATH)
    try:
        settings = load_settings()
    except ValidationError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise SystemExit(1)
    setup_logging(settings.log_level, log_dir=LOG_DIR_PATH)

    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not settings.is_configured:
        console.print("[red]Error:[/red] No API key configured. Edit config.json")
        raise SystemExit(1)

    # 2. Per-process init (skills, CLI tools, bridges)
    chat_agent_name = settings.agents_config.chat_agent
    init_process(settings, agent_name=chat_agent_name)

    # 3. Load all AI agents in-process
    from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
    from qd_evolve.agent.transport import InprocTransport, TransportRouter
    from qd_evolve.agent.a2a_tools import set_transport, register_a2a_tools

    topology = Topology(settings)
    router = TransportRouter(InprocTransport(), None)
    agent_reg = AgentRegistry(topology, current_agent=chat_agent_name)
    agent_map: dict[str, Any] = {}

    for a in settings.agents_config.agents:
        if a.is_human:
            logger.info("A2A Inproc CLI: skipping human agent '%s'", a.name)
            continue
        try:
            ag = create_agent(a.name, settings=settings, need_inproc=True)
            agent_reg.register(ag)
            agent_map[a.name] = ag
            logger.info("A2A Inproc CLI: loaded agent '%s' (%s/%s)",
                        a.name, a.effective_provider(settings), a.effective_model(settings))
        except Exception as e:
            logger.warning("A2A Inproc CLI: failed to load agent '%s': %s", a.name, e)
            console.print(f"[yellow]Warning:[/yellow] Failed to load agent '{a.name}': {e}")

    if not agent_map:
        console.print("[red]Error:[/red] No AI agents could be loaded")
        raise SystemExit(1)

    set_agent_registry(agent_reg)
    register_a2a_tools()
    set_transport(router)

    # 4. Event queue for heartbeat and task events
    event_queue: "asyncio.Queue[tuple[str, dict]]" = asyncio.Queue()

    # 5. Startup panel — AI agents only (human agents not loaded in-process)
    if len(agent_map) > 1:
        max_name_len = max(len(a.name) for a in settings.agents_config.agents if not a.is_human)
        agent_lines = []
        for a in settings.agents_config.agents:
            if a.is_human:
                continue
            name_col = f"{a.name:<{max_name_len}}"
            if a.name in agent_map:
                info = f"{a.effective_provider(settings)}/{a.effective_model(settings)}"
            else:
                info = "[red]failed to load[/red]"
            if a.name == chat_agent_name:
                agent_lines.append(f"  [bold]► {name_col}[/bold]  {info}")
            else:
                agent_lines.append(f"    {name_col}  {info}")
        panel_text = (
            f"qd-evolve v{__version__} (A2A in-process)\n\n"
            + "\n".join(agent_lines)
            + f"\n\nChat: {chat_agent_name}"
            + f"\n/help for commands, /quit to leave"
        )
    else:
        panel_text = f"qd-evolve v{__version__} (A2A in-process) {chat_agent_name}\n/help for commands, /quit to leave"

    console.print(Panel(panel_text, style="bold green"))

    # 6. Replay mode setup
    output_file = None
    if replay:
        lines = replay.read_text(encoding="utf-8").splitlines()
        inputs = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        input_session = ReplayInput(inputs)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output_file = open(output, "w", encoding="utf-8")
            tee = TeeWriter(sys.stdout, output_file)
            import qd_evolve.a2a_inproc_cli as _cli_mod
            _cli_mod.console = Console(file=tee, force_terminal=True)  # type: ignore[arg-type]
        logger.info("CLI: replay mode: %s inputs from %s", len(inputs), replay)
    else:
        input_session = _make_prompt_session()

    try:
        asyncio.run(_async_chat_loop(
            input_session, settings, output_file,
            router=router,
            event_queue=event_queue,
            agent_map=agent_map,
        ))
    except KeyboardInterrupt:
        console.print("\n[dim]Goodbye![/dim]")

    # 7. Cleanup
    from qd_evolve.agent.loader import get_bridges
    bridges = get_bridges()
    logger.info("A2A Inproc CLI: shutting down (bridges=%d, agents=%d)", len(bridges), len(agent_map))
    for b in bridges:
        try:
            b.disconnect(shutdown=True)
        except Exception:
            logger.debug("shutdown: bridge disconnect failed for %s", b, exc_info=True)
    for ag in agent_map.values():
        try:
            if hasattr(ag, 'agent') and ag.agent.memory:
                ag.agent.memory.close()
            elif hasattr(ag, 'memory') and ag.memory:
                ag.memory.close()
        except Exception:
            pass
    if output_file:
        output_file.close()


if __name__ == "__main__":
    a2a_inproc_app()
