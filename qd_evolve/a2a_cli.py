"""A2A CLI — pure HTTP client for remote A2A agent servers.

CLI is NOT an agent. It connects to remote agent servers via TransportRouter.
Use `qd-evolve a2a serve --agent <name>` to start an agent server.
Use `qd-evolve a2a chat` to connect as a client.
"""

import asyncio
from asyncio import CancelledError
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

from qd_evolve.core.config import CONFIG_PATH, Settings, load_settings, save_json

from qd_evolve import __version__

a2a_app = typer.Typer(help="A2A — remote agent HTTP client/server", invoke_without_command=True)
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


AGENT_COLORS = ["#ff6b6b", "#51cf66", "#74c0fc", "#ffd43b", "#da77f2", "#63e6be"]

SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/tools": "List available tools",
    "/skills": "List available skills",
    "/models": "Pick a model to switch to",
    "/cli": "List registered CLI tools",
    "/status": "Show runtime status",
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

    import sys
    import os
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
    agent_entry: Any = None,
) -> str | None:
    from qd_evolve.agent import A2AAgent, init_process, get_skill_registry, get_cli_registry
    from qd_evolve.core.registry import get_registry
    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()
    name = cmd.lower().strip()
    if name == "/quit":
        return None
    if name == "/reset":
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
            if agent_entry:
                agent_entry.provider = prov_name
                agent_entry.model = mname
            save_json(settings.model_dump(), CONFIG_PATH)
            logger.info("CLI: switched model to %s/%s and saved config", prov_name, mname)
            return f"  Switched to {prov_name}/{mname}"
        return "  Cancelled."
    if name == "/status":
        prov_name = (agent_entry.effective_provider(settings) if agent_entry else settings.default_provider)
        model_name = (agent_entry.effective_model(settings) if agent_entry else settings.default_model)
        lines = [f"  [bold]Provider:[/bold] {prov_name}/{model_name}"]
        lines.append("  [dim](remote agent — local status not available)[/dim]")
        return "\n".join(lines)
    if name == "/memory":
        return "  Memory not available for remote agents."
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
    router: Any,
) -> None:
    """Async main chat loop — pure A2A HTTP client via TransportRouter.

    CLI never creates agents. All communication goes through router:
    send_stream for chat, resubscribe for heartbeat events, is_online for probes.
    """
    from qd_evolve.agent import A2AAgent, init_process, get_skill_registry, get_cli_registry, get_bridges
    from qd_evolve.core.providers import ProviderRegistry
    from qd_evolve.agent.a2a import Message, Part, TaskState
    from tools.bridge import BridgeManager

    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()
    bridges = get_bridges()
    providers = ProviderRegistry(settings)

    def _current_agent_name() -> str:
        return settings.agents_config.chat_agent

    def _current_agent_entry() -> Any:
        name = _current_agent_name()
        return next((a for a in settings.agents_config.agents if a.name == name), None)

    all_agent_names = [a.name for a in settings.agents_config.agents]
    all_friendly_names = {a.name: a.effective_friendly_name() for a in settings.agents_config.agents}
    hb_idle = settings.heartbeat_idle_seconds
    hb_counts: dict[str, int] = {all_friendly_names[name]: 0 for name in all_agent_names}

    def _agent_color(name: str) -> str:
        idx = all_agent_names.index(name) if name in all_agent_names else 0
        return AGENT_COLORS[idx % len(AGENT_COLORS)]

    # Probe all agents for online status via router
    online_status: dict[str, bool] = {}
    for a in settings.agents_config.agents:
        ok = await router.is_online(a.name)
        online_status[a.name] = ok
        status_str = "online" if ok else "offline"
        console.print(f"  [dim]{_friendly_name(settings, a.name)} ({a.name}) HTTP :{a.server.port} — {status_str}[/dim]")
        if not ok:
            logger.warning("Agent '%s' (HTTP :%s) is offline", a.name, a.server.port)

    chat_name = _current_agent_name()
    if not online_status.get(chat_name, False):
        console.print(f"[bold yellow]Warning:[/bold yellow] {_friendly_name(settings, chat_name)} is offline. Use [bold]/agents[/bold] to switch.")

    def _is_current_agent_online() -> bool:
        return online_status.get(_current_agent_name(), False)

    # Event workers for heartbeat via router.resubscribe
    event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
    event_workers: list[asyncio.Task] = []

    async def _remote_event_worker(name: str) -> None:
        """Subscribe to a remote agent's events via router.resubscribe."""
        retry_delay = 5
        while True:
            try:
                async for sr in router.resubscribe(name):
                    if sr.statusUpdate and sr.statusUpdate.metadata:
                        await event_queue.put((name, sr.statusUpdate.metadata))
                    retry_delay = 5
            except asyncio.CancelledError:
                return
            except Exception:
                logger.warning("Event worker for '%s': offline, retrying in %ds", name, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    if hb_idle > 0:
        for name in all_agent_names:
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

            # Event arrived from any agent
            if event_wait in done:
                try:
                    agent_name, event = event_wait.result()
                except Exception:
                    continue
                fn = all_friendly_names.get(agent_name, agent_name)
                etype = event.get("type", "")
                if etype == "heartbeat":
                    hb_counts[fn] = 0
                    color = _agent_color(agent_name)
                    _app = getattr(input_session, "app", None)
                    if _app and _app.is_running:
                        _app.renderer.erase()
                    console.print(f"[bold {color}]{fn}>[/bold {color}] {event.get('content', '')}")
                    if _app and _app.is_running:
                        _app.invalidate()
                    if agent_name == _current_agent_name():
                        input_task.cancel()
                        try:
                            await input_task
                        except (CancelledError, EOFError, KeyboardInterrupt):
                            pass
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
        if user_input.startswith("/"):
            result = await _handle_slash_command(user_input, settings, router, agent_entry=_current_agent_entry())
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                break
            if result:
                console.print(result)
            continue

        # Check if current agent is online
        cur_name = _current_agent_name()
        cur_entry = _current_agent_entry()
        if cur_entry and (cur_name not in online_status or not online_status.get(cur_name, False)):
            online_status[cur_name] = await router.is_online(cur_name)
            if not online_status[cur_name]:
                logger.warning("Agent '%s' (HTTP :%s) is offline", cur_name, cur_entry.server.port)
                console.print(f"  [dim]Agent '{cur_name}' HTTP :{cur_entry.server.port} — offline[/dim]")
        if not _is_current_agent_online():
            console.print(f"[bold red]Agent '{cur_name}' is offline.[/bold red] Use [bold]/agents[/bold] to switch to an online agent.")
            continue

        # Chat via router
        cur_entry = _current_agent_entry()
        is_human_target = cur_entry is not None and cur_entry.is_human
        msg = Message(role="user", parts=[Part(type="text", text=user_input)])
        response = ""

        if is_human_target:
            # Human agent: send_task (non-blocking), returns input_required
            # Human responds asynchronously via webhook callback
            try:
                task = await router.send_task(_current_agent_name(), msg)
                if task.status.state == TaskState.input_required:
                    console.print(f"[dim]Task sent to {_friendly_name(settings, _current_agent_name())}. Waiting for response...[/dim]")
                    # Wait for webhook callback via event queue
                    pending_task_id = task.id
                    while True:
                        try:
                            agent_name, event = await asyncio.wait_for(event_queue.get(), timeout=300)
                            etype = event.get("type", "")
                            if etype == "task_completed" and event.get("task_id") == pending_task_id:
                                response = event.get("content", "")
                                break
                            # Heartbeat or other events from other agents — ignore
                        except asyncio.TimeoutError:
                            console.print("[dim]Still waiting...[/dim]")
                else:
                    # Task completed immediately (shouldn't happen for human)
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
            # AI agent: send_stream (blocking with live display)
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
                                if sr.statusUpdate.status.state == TaskState.failed:
                                    response = "[bold red]Agent offline[/bold red] — use /agents to switch"
                                else:
                                    response = f"[Task state: {sr.statusUpdate.status.state}]"
                except Exception as e:
                    if isinstance(e, (OSError,)):
                        response = "[bold red]Agent offline[/bold red] — use /agents to switch"
                    else:
                        response = f"[red]Error:[/red] {e}"

        console.print(f"[bold {_agent_color(_current_agent_name())}]{_friendly_name(settings, _current_agent_name())}>[/bold {_agent_color(_current_agent_name())}] {response}")

        # Token stats from SSE event
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
    if output_file:
        output_file.close()


# ── Human terminal loop ────────────────────────────────────────────────────

async def _human_terminal_loop(agent_core: Any, friendly_name: str) -> None:
    """Interactive loop: receive tasks, prompt human, submit responses."""
    queue = agent_core.subscribe_events()
    try:
        while True:
            event = await queue.get()
            etype = event.get("type", "")
            if etype == "human_task":
                task_id = event.get("task_id", "")
                task_content = event.get("content", "")
                console.print(f"\n[bold yellow]Task {task_id}:[/bold yellow] {task_content}")
                console.print("[bold cyan]Your response:[/bold cyan] ", end="")
                response = await asyncio.to_thread(input)
                agent_core.complete_task(task_id, response)
                console.print(f"[dim]Response submitted for task {task_id}[/dim]")
            elif etype == "task_completed":
                # Already handled by complete_task caller; ignore echo
                pass
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        agent_core.unsubscribe_events(queue)


# ── a2a serve ─────────────────────────────────────────────────────────────

@a2a_app.command()
def serve(
    agent: str = typer.Option("", "--agent", help="Agent name from config.json to serve"),
) -> None:
    """Start an agent as a standalone A2A HTTP server (for cross-process communication)."""
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH, load_settings
    from qd_evolve.agent import create_agent, init_process
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
        console.print("[red]Error:[/red] --agent is required. E.g. qd-evolve a2a serve --agent test")
        raise SystemExit(1)

    # 2. Per-process init (skills, CLI tools, bridges, registry injection)
    init_process(settings)

    # 3. Create agent via loader
    try:
        agent_core = create_agent(agent, settings=settings)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    entry = next((a for a in settings.agents_config.agents if a.name == agent), None)
    is_human = entry is not None and entry.is_human

    # 4. A2A setup — register in AgentRegistry + set transport (AI agents only)
    if not is_human:
        from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
        from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter

        topology = Topology(settings)
        router = TransportRouter(InprocTransport(), HttpTransport())
        agent_reg = AgentRegistry(topology, current_agent=agent)
        agent_reg.register(agent_core)
        set_agent_registry(agent_reg)

        from qd_evolve.tools.a2a import set_transport
        set_transport(router)

    # 5. Start A2A server — bind 0.0.0.0 to accept all interfaces, display connect address
    server = A2AServer(agent_core)
    from qd_evolve.core.config import DEFAULT_BIND_HOST, DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
    bind_host = DEFAULT_BIND_HOST
    connect_host = entry.server.host if entry else DEFAULT_SERVER_HOST
    port = entry.server.port if entry else DEFAULT_SERVER_PORT
    fn = _friendly_name(settings, agent)
    if is_human:
        console.print(Panel(
            f"Human agent [bold]{fn} ({agent})[/bold] on {bind_host}:{port} (connect: {connect_host}:{port})\nWaiting for tasks...",
            style="bold yellow",
        ))
    else:
        console.print(Panel(
            f"Serving agent [bold]{fn} ({agent})[/bold] on {bind_host}:{port} (connect: {connect_host}:{port})\nA2A v1.0 JSON-RPC + SSE",
            style="bold green",
        ))

    async def _run() -> None:
        try:
            await server.start(host=bind_host, port=port)
        except OSError as e:
            console.print(f"[red]Error:[/red] Cannot bind to {bind_host}:{port} — {e}")
            console.print("[dim]Another process may be using this port. Kill it or change the port in config.json.[/dim]")
            return
        if is_human:
            await _human_terminal_loop(agent_core, fn)
        else:
            agent_core.start_heartbeat_loop()
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


# ── a2a chat (default command) ────────────────────────────────────────────

@a2a_app.callback()
def chat(
    ctx: typer.Context,
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    """A2A chat client + server. Connects to remote agents and accepts webhook callbacks."""
    if ctx.invoked_subcommand is not None:
        return
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH
    from qd_evolve.agent.loader import init_process, create_agent

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

    # 3. Create chat agent via loader (full A2A agent with server)
    chat_agent_name = settings.agents_config.chat_agent
    chat_agent_entry = next((a for a in settings.agents_config.agents if a.name == chat_agent_name), None)
    if chat_agent_entry is None:
        console.print(f"[red]Error:[/red] Agent '{chat_agent_name}' not found in config")
        raise SystemExit(1)

    try:
        agent_core = create_agent(chat_agent_name, settings=settings)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # 4. A2A setup — register agent + full transport (inproc + http)
    from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
    from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter

    topology = Topology(settings)
    router = TransportRouter(InprocTransport(), HttpTransport())
    agent_reg = AgentRegistry(topology, current_agent=chat_agent_name)
    agent_reg.register(agent_core)
    set_agent_registry(agent_reg)

    from qd_evolve.tools.a2a import set_transport
    set_transport(router)

    # 5. Start A2A server for this agent (accepts webhook callbacks)
    from qd_evolve.agent.server import A2AServer
    from qd_evolve.core.config import DEFAULT_BIND_HOST, DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

    async def _on_webhook(event: dict) -> None:
        """Push webhook callback events to the CLI event queue."""
        await event_queue.put(("webhook", event))

    server = A2AServer(agent_core, on_task_completed=_on_webhook)
    bind_host = DEFAULT_BIND_HOST
    a2a_cli_cfg = settings.agents_config.a2a_cli
    connect_host = a2a_cli_cfg.server.host or DEFAULT_SERVER_HOST
    port = a2a_cli_cfg.server.port or DEFAULT_SERVER_PORT

    # 6. Startup panel
    agents = settings.agents_config.agents
    fn = _friendly_name(settings, chat_agent_name)

    if len(agents) > 1:
        max_name_len = max(len(a.effective_friendly_name()) for a in agents)
        agent_lines = []
        for a in agents:
            a_fn = a.effective_friendly_name()
            name_col = f"{a_fn:<{max_name_len}}"
            if a.is_human:
                info = "human"
            else:
                info = f"{a.effective_provider(settings)}/{a.effective_model(settings)}"
            if a.name == chat_agent_name:
                agent_lines.append(f"  [bold]► {name_col}[/bold]  {info}")
            else:
                agent_lines.append(f"    {name_col}  {info}")
        panel_text = (
            f"qd-evolve v{__version__} (A2A client+server)\n\n"
            + "\n".join(agent_lines)
            + f"\n\nChat: {fn} ({chat_agent_name})"
            + f"\nServer: {bind_host}:{port}"
            + f"\n/help for commands, /quit to leave"
        )
    else:
        model_info = escape(f"[{chat_agent_entry.effective_provider(settings)}/{chat_agent_entry.effective_model(settings)}]") if chat_agent_entry else ""
        panel_text = f"qd-evolve v{__version__} (A2A client+server) {fn} ({chat_agent_name}) {model_info}\nServer: {bind_host}:{port}\n/help for commands, /quit to leave"

    console.print(Panel(
        panel_text,
        style="bold green",
    ))

    # 7. Replay mode setup
    output_file = None
    if replay:
        lines = replay.read_text(encoding="utf-8").splitlines()
        inputs = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        input_session = ReplayInput(inputs)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output_file = open(output, "w", encoding="utf-8")
            tee = TeeWriter(sys.stdout, output_file)
            import qd_evolve.a2a_cli as _cli_mod
            _cli_mod.console = Console(file=tee, force_terminal=True)
        logger.info("CLI: replay mode: %s inputs from %s", len(inputs), replay)
    else:
        input_session = _make_prompt_session()

    async def _run() -> None:
        try:
            await server.start(host=bind_host, port=port)
        except OSError as e:
            console.print(f"[red]Error:[/red] Cannot bind to {bind_host}:{port} — {e}")
            console.print("[dim]Another process may be using this port. Kill it or change the port in config.json.[/dim]")
            return
        if chat_agent_entry.is_human:
            agent_core.start_heartbeat_loop(settings.heartbeat_idle_seconds)
        else:
            agent_core.start_heartbeat_loop()
        await _async_chat_loop(
            input_session, settings, output_file,
            router=router,
        )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Server stopped.[/dim]")


if __name__ == "__main__":
    a2a_app()

from qd_evolve.toolbox_tui import toolbox_app
a2a_app.add_typer(toolbox_app, name="toolbox")
