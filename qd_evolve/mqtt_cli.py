"""MQTT CLI — A2A over MQTT v5 client/server.

CLI is NOT an agent. It connects to remote agent servers via MQTT v5 transport.
Use `qd-evolve mqtt serve --agent <name>` to start an agent server.
Use `qd-evolve mqtt` to connect as a client.
"""

import asyncio
from asyncio import CancelledError
import sys
from pathlib import Path
from typing import Any

# Windows: paho-mqtt requires add_reader/add_writer which ProactorEventLoop doesn't support.
# Must use SelectorEventLoop on Windows for aiomqtt to work.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import typer
from pydantic import ValidationError
from qd_evolve.core.logger import logger
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve.cli_utils import AGENT_COLORS, ReplayInput, TeeWriter
from qd_evolve.core.config import CONFIG_PATH, Settings, load_settings, save_json

from qd_evolve import __version__

mqtt_app = typer.Typer(help="MQTT — A2A over MQTT v5 client/server")
console = Console()


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
    transport: Any,
    agent_entry: Any = None,
) -> str | None:
    from qd_evolve.agent import get_skill_registry, get_cli_registry
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

        # Check agent status for all agents in parallel (on demand)
        async def _check_one(a: Any) -> tuple[str, str]:
            try:
                status = await transport.get_agent_status(a.name)
            except Exception:
                status = "unknown"
            return a.name, status

        tasks = [_check_one(a) for a in agent_list]
        pairs = await asyncio.gather(*tasks)
        results: dict[str, str] = dict(pairs)

        table = Table(title="Available Agents", show_header=True)
        table.add_column("#", style="dim", justify="right")
        table.add_column("Agent", style="cyan")
        table.add_column("Provider/Model", style="bold")
        table.add_column("MQTT", style="dim")
        table.add_column("Status")
        current = settings.agents_config.chat_agent
        broker_cfg = settings.agents_config.mqtt_broker
        for i, a in enumerate(agent_list, 1):
            marker = " *" if a.name == current else ""
            mqtt_info = f"{broker_cfg.host}:{broker_cfg.port}"
            if a.is_human:
                prov_mdl = "human"
            else:
                prov_mdl = f"{a.effective_provider(settings)}/{a.effective_model(settings)}"
            stat = results.get(a.name, "unknown")
            if stat == "online":
                status_str = "[green]online[/green]"
            elif stat == "lwt":
                status_str = "[bold red]lost (LWT)[/bold red]"
            elif stat == "offline":
                status_str = "[red]offline[/red]"
            else:
                status_str = "[dim]unknown[/dim]"
            table.add_row(str(i), a.name + marker, prov_mdl, mqtt_info, status_str)
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
    transport: Any,
) -> None:
    """Async main chat loop — pure MQTT client via MqttTransport (A2A over MQTT v5).

    CLI never creates agents. All communication goes through transport:
    send_stream for chat, resubscribe for heartbeat events, is_online for probes.
    """
    from qd_evolve.core.providers import ProviderRegistry
    from qd_evolve.agent.a2a import Message, Part, TaskState

    providers = ProviderRegistry(settings)

    def _current_agent_name() -> str:
        return settings.agents_config.chat_agent

    def _current_agent_entry() -> Any:
        name = _current_agent_name()
        return next((a for a in settings.agents_config.agents if a.name == name), None)

    all_agent_names = [a.name for a in settings.agents_config.agents]
    hb_idle = settings.heartbeat_idle_seconds
    hb_counts: dict[str, int] = {name: 0 for name in all_agent_names}

    def _agent_color(name: str) -> str:
        idx = all_agent_names.index(name) if name in all_agent_names else 0
        return AGENT_COLORS[idx % len(AGENT_COLORS)]

    # Event queue — heartbeats, presence, task_completed events.
    event_queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
    event_workers: list[asyncio.Task] = []

    async def _discovery_worker() -> None:
        """Monitor $a2a/v1/discovery/+ for agent online/offline/lwt events.

        Deduplicates: only emits presence events on actual status transitions.
        MQTT broker re-delivers retained messages on every subscribe, so
        without dedup every is_online() call would trigger a duplicate
        "agent online" notification.
        """
        disc_queue = await transport.subscribe_discovery()
        last_status: dict[str, str] = {}
        try:
            while True:
                event = await disc_queue.get()
                agent_name = event.get("agent_name", "")
                a2a_status = event.get("status", "unknown")
                if last_status.get(agent_name) == a2a_status:
                    continue
                last_status[agent_name] = a2a_status
                is_online = event.get("online", False)
                if is_online:
                    await event_queue.put(("system", {"type": "agent_online", "name": agent_name}))
                elif a2a_status == "lwt":
                    await event_queue.put(("system", {"type": "agent_lwt", "name": agent_name}))
                else:
                    await event_queue.put(("system", {"type": "agent_offline", "name": agent_name}))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Discovery worker crashed")
        finally:
            transport.unsubscribe_discovery(disc_queue)

    async def _event_stream_worker(name: str) -> None:
        """Stream agent events (heartbeat, iteration, tokens) via resubscribe.

        Retries automatically on disconnect with exponential backoff.
        """
        retry_delay = 2
        while True:
            try:
                async for sr in transport.resubscribe(name):
                    if sr.statusUpdate and sr.statusUpdate.metadata:
                        meta = sr.statusUpdate.metadata
                        logger.debug("MQTT CLI: _event_stream_worker '%s' recv type=%s content_len=%d",
                                   name, meta.get("type", ""), len(str(meta.get("content", ""))))
                        await event_queue.put((name, meta))
                    retry_delay = 2
            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("Event stream worker '%s': lost, retrying in %ds", name, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)

    # Check all agents at startup (parallel, on demand)
    async def _check_all_online() -> None:
        async def _check_one(a: Any) -> tuple[str, str]:
            try:
                status = await transport.get_agent_status(a.name)
            except Exception:
                status = "unknown"
            return a.name, status

        tasks = [_check_one(a) for a in settings.agents_config.agents]
        pairs = await asyncio.gather(*tasks)
        broker_info = f"{settings.agents_config.mqtt_broker.host}:{settings.agents_config.mqtt_broker.port}"
        for name, status in pairs:
            if status == "online":
                status_str = "[green]online[/green]"
            elif status == "lwt":
                status_str = "[bold red]lost (LWT)[/bold red]"
            elif status == "offline":
                status_str = "[red]offline[/red]"
            else:
                status_str = "[dim]unknown[/dim]"
            console.print(f"  {name} MQTT {broker_info} — {status_str}")

    await _check_all_online()

    # Start discovery + event stream workers for all agents.
    logger.info("MQTT CLI: starting workers for %d agents", len(all_agent_names))
    event_workers.append(asyncio.ensure_future(_discovery_worker()))
    for name in all_agent_names:
        event_workers.append(asyncio.ensure_future(_event_stream_worker(name)))
        logger.debug("MQTT CLI: event worker started for '%s'", name)

    input_task = None
    pending_task_id: str | None = None
    quitting = False

    try:
      while True:
        input_task = asyncio.ensure_future(_read_input_async(input_session, hb_counts))

        while True:
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

            # Event arrived from event workers
            if event_wait in done:
                try:
                    agent_name, event = event_wait.result()
                except Exception:
                    continue
                etype = event.get("type", "")
                logger.debug("MQTT CLI: main loop recv event agent=%s type=%s content_len=%d",
                           agent_name, etype, len(str(event.get("content", ""))))

                # ── Presence events ──
                if etype == "agent_online":
                    target = event.get("name", agent_name)
                    logger.info("MQTT CLI: agent '%s' is now online", target)
                    _app = getattr(input_session, "app", None)
                    if _app and _app.is_running:
                        _app.renderer.erase()
                    console.print(f"[bold green]Agent '{target}' is now online[/bold green]")
                    if _app and _app.is_running:
                        _app.invalidate()
                    continue
                elif etype == "agent_offline":
                    target = event.get("name", agent_name)
                    logger.info("MQTT CLI: agent '%s' is now offline (graceful)", target)
                    _app = getattr(input_session, "app", None)
                    if _app and _app.is_running:
                        _app.renderer.erase()
                    console.print(f"[bold yellow]Agent '{target}' is now offline[/bold yellow]")
                    if _app and _app.is_running:
                        _app.invalidate()
                    continue
                elif etype == "agent_lwt":
                    target = event.get("name", agent_name)
                    logger.warning("MQTT CLI: agent '%s' lost connection (LWT)", target)
                    _app = getattr(input_session, "app", None)
                    if _app and _app.is_running:
                        _app.renderer.erase()
                    console.print(f"[bold red]Agent '{target}' lost connection (LWT)[/bold red]")
                    if _app and _app.is_running:
                        _app.invalidate()
                    continue

                # ── Task completed events (push notification from human agent) ──
                # Only handle if this is a response to a task the CLI itself sent
                # (i.e. user chatted with a human agent via send_task).
                # Push notifications for AI agents are handled by the agent itself.
                if etype == "task_completed":
                    content = event.get("content", "")
                    task_id = event.get("task_id", "")
                    if content and task_id == pending_task_id:
                        logger.debug("MQTT CLI: task_completed received (from=%s, task=%s)", agent_name, task_id)
                        input_task.cancel()
                        try:
                            await input_task
                        except (CancelledError, EOFError, KeyboardInterrupt):
                            pass
                        color = _agent_color(agent_name)
                        console.print(f"[bold {color}]{agent_name}>[/bold {color}] {content}")
                        user_input = None
                        break
                    continue

                # ── Completed events (agent finished processing, e.g. push notification response) ──
                if etype == "completed":
                    content = event.get("content", "")
                    if content and content.strip() != ".":
                        color = _agent_color(agent_name)
                        _app = getattr(input_session, "app", None)
                        if _app and _app.is_running:
                            _app.renderer.erase()
                        console.print(f"[bold {color}]{agent_name}>[/bold {color}] {content}")
                        if _app and _app.is_running:
                            _app.invalidate()
                    continue

                # ── Heartbeat events (only when hb_idle > 0) ──
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
                        console.print(f"[bold {color}]{fn}>[/bold {color}] {event.get('content', '')}")
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
        if user_input.startswith("/"):
            result = await _handle_slash_command(user_input, settings, transport, agent_entry=_current_agent_entry())
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                quitting = True
                if input_task and not input_task.done():
                    input_task.cancel()
                break
            if result:
                console.print(result)
            continue

        # Check agent online on demand before sending
        cur_name = _current_agent_name()
        cur_entry = _current_agent_entry()
        if not await transport.is_online(cur_name):
            console.print(f"[bold red]Agent '{cur_name}' is offline.[/bold red] Use [bold]/agents[/bold] to switch.")
            continue

        # Chat via MQTT transport
        is_human_target = cur_entry is not None and cur_entry.is_human
        msg = Message(role="user", parts=[Part(type="text", text=user_input)])
        response = ""
        last_tokens_event: dict | None = None

        if is_human_target:
            # Human agent: send_task (non-blocking), returns input_required
            logger.info("MQTT CLI: send_task -> '%s' (human agent)", _current_agent_name())
            try:
                task = await transport.send_task(_current_agent_name(), msg)
                if task.status.state == TaskState.input_required:
                    console.print(f"[dim]Task sent to {_current_agent_name()}. Waiting for response...[/dim]")
                    pending_task_id = task.id
                    while True:
                        try:
                            agent_name, event = await asyncio.wait_for(event_queue.get(), timeout=300)
                            etype = event.get("type", "")
                            if etype == "task_completed" and event.get("task_id") == pending_task_id:
                                response = event.get("content", "")
                                break
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
            # AI agent: send_stream (blocking with live display)
            logger.info("MQTT CLI: send_stream -> '%s' (%s chars)", _current_agent_name(), len(user_input))
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
                    async for sr in transport.send_stream(_current_agent_name(), msg):
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

        logger.info("MQTT CLI: received response from '%s' (%s chars)", _current_agent_name(), len(response))
        console.print(f"[bold {_agent_color(_current_agent_name())}]{_current_agent_name()}>[/bold {_agent_color(_current_agent_name())}] {response}")

        # Token stats from MQTT event
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

    except KeyboardInterrupt:
        if input_task and not input_task.done():
            input_task.cancel()
            try:
                input_task.result()
            except BaseException:
                pass
        console.print("\n[dim]Goodbye![/dim]")

    logger.info("MQTT CLI: shutting down (stopping %d event workers)", len(event_workers))
    for t in event_workers:
        if not t.done():
            t.cancel()
    if event_workers:
        await asyncio.gather(*event_workers, return_exceptions=True)
    if output_file:
        output_file.close()


# ── Human terminal loop ────────────────────────────────────────────────────

async def _human_terminal_loop(agent_core: Any, settings: Any = None) -> None:
    """Interactive loop: receive tasks, prompt human, submit responses."""
    queue = agent_core.subscribe_events()
    agent_names: dict[str, str] = {}
    if settings:
        for a in settings.agents_config.agents:
            agent_names[a.name] = a.name
    try:
        while True:
            event = await queue.get()
            etype = event.get("type", "")
            if etype == "human_task":
                task_id = event.get("task_id", "")
                task_content = event.get("content", "")
                from_agent = event.get("from_agent", "")
                label = agent_names.get(from_agent, from_agent) if from_agent else task_id[:8]
                console.print(f"\n[bold yellow]{label}:[/bold yellow] {task_content}")
                console.print("[bold cyan]Your response:[/bold cyan] ", end="")
                response = await asyncio.to_thread(input)
                await agent_core.complete_task(task_id, response)
                console.print(f"[dim]Response submitted[/dim]")
            elif etype == "task_completed":
                pass
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        agent_core.unsubscribe_events(queue)




# ── mqtt serve ─────────────────────────────────────────────────────────────

@mqtt_app.command()
def serve(
    agent: str = typer.Option("", "--agent", help="Agent name from config.json to serve"),
) -> None:
    """Start an agent as an MQTT-accessible server (A2A over MQTT v5)."""
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH, load_settings, MqttConfig
    from qd_evolve.agent import create_agent, init_process

    # 1. Config & logging
    setup_logging("WARNING", log_dir=LOG_DIR_PATH)
    try:
        settings = load_settings()
    except ValidationError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise SystemExit(1)
    setup_logging(settings.log.level, log_dir=LOG_DIR_PATH)

    # Inject env_vars
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not agent:
        console.print("[red]Error:[/red] --agent is required. E.g. qd-evolve mqtt serve --agent test")
        raise SystemExit(1)

    entry = next((a for a in settings.agents_config.agents if a.name == agent), None)
    is_human = entry is not None and entry.is_human

    # 2. Per-process init (skills, CLI tools, bridges, registry injection)
    if not is_human:
        init_process(settings, agent_name=agent)

    # 3. Create agent via loader
    try:
        agent_core = create_agent(agent, settings, need_a2a=True, need_mqtt=True)
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # 4. A2A + MQTT setup
    broker_cfg = settings.agents_config.mqtt_broker
    mqtt_transport = None
    if not is_human:
        from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
        from qd_evolve.agent.transport import InprocTransport, TransportRouter
        from qd_evolve.agent.mqtt_transport import MqttTransport

        topology = Topology(settings)
        mqtt_transport = MqttTransport(
            broker_host=broker_cfg.host,
            broker_port=broker_cfg.port,
            mqtt_config=entry.mqtt if entry else MqttConfig(),
            client_name=agent,
        )
        router = TransportRouter(InprocTransport(), mqtt_transport)
        agent_reg = AgentRegistry(topology, current_agent=agent)
        agent_reg.register(agent_core)
        set_agent_registry(agent_reg)

        from qd_evolve.agent.a2a_tools import set_transport
        set_transport(router)

    # 5. Start MQTT agent
    fn = agent
    logger.info("MQTT serve: starting agent '%s' (human=%s) via broker %s:%s",
                agent, is_human, broker_cfg.host, broker_cfg.port)
    if not is_human:
        prov = getattr(agent_core, '_provider_name', '?')
        model = getattr(agent_core, '_model', '?')
        logger.info("MQTT serve: agent '%s' provider=%s/%s", agent, prov, model)
    if is_human:
        console.print(Panel(
            f"Human agent [bold]{fn} ({agent})[/bold] via MQTT {broker_cfg.host}:{broker_cfg.port}\nWaiting for tasks...",
            style="bold yellow",
        ))
    else:
        console.print(Panel(
            f"Serving agent [bold]{fn} ({agent})[/bold] via MQTT {broker_cfg.host}:{broker_cfg.port}\nA2A over MQTT v5",
            style="bold green",
        ))

    async def _run() -> None:
        # Connect MqttTransport for outbound requests
        if mqtt_transport is not None:
            await mqtt_transport.connect()
        await agent_core.start()
        try:
            if is_human:
                agent_core.start_heartbeat_loop(settings.heartbeat_idle_seconds)
                await _human_terminal_loop(agent_core, settings)
            else:
                agent_core.start_heartbeat_loop()
                stop_event = asyncio.Event()
                await stop_event.wait()
        finally:
            # Graceful shutdown: publish a2a-status=offline BEFORE disconnect
            # so Broker sees clean offline, not LWT
            try:
                await agent_core.stop()
            except Exception:
                pass
            if mqtt_transport is not None:
                try:
                    await mqtt_transport.disconnect()
                except Exception:
                    pass

    # Use loop.run_until_complete instead of asyncio.run so that on
    # KeyboardInterrupt we can still run async cleanup (agent_core.stop())
    # before the loop closes. asyncio.run() cancels all tasks immediately
    # on KeyboardInterrupt, preventing any async cleanup from completing.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        # Loop is still alive — run async cleanup to publish
        # a2a-status=offline and send MQTT v5 DISCONNECT (suppresses LWT)
        try:
            loop.run_until_complete(agent_core.stop())
        except Exception:
            pass
        if mqtt_transport is not None:
            try:
                loop.run_until_complete(mqtt_transport.disconnect())
            except Exception:
                pass
        # Cancel remaining tasks so the loop can close cleanly
        for task in asyncio.all_tasks(loop):
            task.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True))
        except Exception:
            pass
        console.print("\n[dim]MQTT agent stopped.[/dim]")
    except Exception as exc:
        from aiomqtt import MqttError
        if isinstance(exc, MqttError):
            console.print(f"\n[bold red]Cannot connect to MQTT broker {broker_cfg.host}:{broker_cfg.port}.[/bold red]")
            console.print("[dim]Start mosquitto first: mosquitto -v[/dim]")
        else:
            raise
    finally:
        loop.close()


# ── mqtt chat (default command) ────────────────────────────────────────────

@mqtt_app.callback(invoke_without_command=True)
def chat(
    ctx: typer.Context,
    replay: Path | None = typer.Option(None, "--replay", help="Replay inputs from file"),
    output: Path | None = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    """MQTT chat client. Connects to remote agents via MQTT v5 broker."""
    if ctx.invoked_subcommand is not None:
        return

    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH
    from qd_evolve.agent.mqtt_transport import MqttTransport

    # 1. Config & logging
    setup_logging("WARNING", log_dir=LOG_DIR_PATH)
    try:
        settings = load_settings()
    except ValidationError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise SystemExit(1)
    setup_logging(settings.log.level, log_dir=LOG_DIR_PATH)

    # Inject env_vars from config into os.environ
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not settings.is_configured:
        console.print("[red]Error:[/red] No API key configured. Edit config.json")
        raise SystemExit(1)

    # MQTT CLI is a pure MQTT client — no tools, skills, or bridges needed.
    # Tools live on the remote agent servers.

    # 2. Chat agent from config (remote only — never created in-process)
    chat_agent_name = settings.agents_config.chat_agent
    chat_agent_entry = next((a for a in settings.agents_config.agents if a.name == chat_agent_name), None)
    if chat_agent_entry is None:
        console.print(f"[red]Error:[/red] Agent '{chat_agent_name}' not found in config")
        raise SystemExit(1)

    # 3. MQTT transport — connect to broker
    broker_cfg = settings.agents_config.mqtt_broker
    mqtt_config = _get_mqtt_config(settings, chat_agent_name)
    transport = MqttTransport(broker_cfg.host, broker_cfg.port, mqtt_config, client_name="cli")

    agents = settings.agents_config.agents

    logger.info("MQTT CLI: %d agents configured, chat='%s', broker=%s:%s",
                len(agents), chat_agent_name, broker_cfg.host, broker_cfg.port)

    # 4. Startup panel
    fn = chat_agent_name

    if len(agents) > 1:
        max_name_len = max(len(a.name) for a in agents)
        agent_lines = []
        for a in agents:
            a_fn = a.name
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
            f"qd-evolve v{__version__} (A2A over MQTT v5)\n\n"
            + "\n".join(agent_lines)
            + f"\n\nChat: {fn} ({chat_agent_name})"
            + f"\nBroker: {broker_cfg.host}:{broker_cfg.port}"
            + f"\n/help for commands, /quit to leave"
        )
    else:
        model_info = escape(f"[{chat_agent_entry.effective_provider(settings)}/{chat_agent_entry.effective_model(settings)}]") if chat_agent_entry else ""
        panel_text = f"qd-evolve v{__version__} (A2A over MQTT v5) {fn} ({chat_agent_name}) {model_info}\nBroker: {broker_cfg.host}:{broker_cfg.port}\n/help for commands, /quit to leave"

    console.print(Panel(
        panel_text,
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
            import qd_evolve.mqtt_cli as _cli_mod
            _cli_mod.console = Console(file=tee, force_terminal=True)
        logger.info("CLI: replay mode: %s inputs from %s", len(inputs), replay)
    else:
        input_session = _make_prompt_session()

    async def _run() -> None:
        try:
            await transport.connect()
        except Exception as e:
            console.print(f"[red]Error:[/red] Failed to connect to MQTT broker at {broker_cfg.host}:{broker_cfg.port} — {e}")
            return
        try:
            await _async_chat_loop(
                input_session, settings, output_file,
                transport=transport,
            )
        finally:
            await transport.disconnect()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Disconnected.[/dim]")


def _get_mqtt_config(settings: object, agent_name: str) -> object:
    """Get MQTT config for an agent, falling back to defaults."""
    from qd_evolve.core.config import MqttConfig
    for a in settings.agents_config.agents:
        if a.name == agent_name:
            return a.mqtt
    return MqttConfig()


from qd_evolve.toolbox_tui import toolbox_app
mqtt_app.add_typer(toolbox_app, name="toolbox")