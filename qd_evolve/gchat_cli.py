"""Group chat CLI — `qd-evolve gchat --agent <name>`.

All configured agents form a single group.  AI agents run a background
group-message loop; human agents get an interactive terminal that shows
incoming group messages and publishes keyboard input to the group.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

# Windows: aiomqtt requires SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import typer
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from qd_evolve import __version__
from qd_evolve.cli_utils import AGENT_COLORS
from qd_evolve.core.config import Settings, load_settings
from qd_evolve.core.logger import logger

gchat_app = typer.Typer(help="Group chat — 微信群式多 agent 群聊")
console = Console()

SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/agents": "Show group members and online status",
    "/help": "Show available commands",
}


def _make_prompt_session(agent_name: str) -> Any:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import FormattedText

    completer = WordCompleter(
        list(SLASH_COMMANDS.keys()),
        ignore_case=True,
        sentence=True,
        meta_dict=SLASH_COMMANDS,
    )
    prompt_msg = FormattedText([("fg:#74c0fc bold", f"{agent_name}> "), ("", "")])

    try:
        return PromptSession(prompt_msg, completer=completer)
    except Exception:
        if sys.platform == "win32" and sys.environ.get("TERM"):
            from prompt_toolkit.output.vt100 import Vt100_Output
            from prompt_toolkit.input.vt100 import Vt100Input
            output = Vt100_Output.from_pty(sys.stdout)
            input_stream = Vt100Input(sys.stdin)
            return PromptSession(prompt_msg, completer=completer, output=output, input=input_stream)
        raise


async def _read_input_async(session: Any) -> str:
    try:
        result = await session.prompt_async()
    except KeyboardInterrupt:
        raise EOFError
    return result.strip()


async def _handle_slash_command(cmd: str, transport: Any, settings: Settings) -> str | None:
    name = cmd.lower().strip()
    if name == "/quit":
        return None
    if name == "/help":
        from rich.table import Table
        table = Table(title="Commands", show_header=False)
        table.add_column("Command", style="bold cyan")
        table.add_column("Description")
        for c, d in SLASH_COMMANDS.items():
            table.add_row(c, d)
        console.print(table)
        return ""
    if name == "/agents":
        agents = settings.agents_config.agents
        table = Table(title="Group Members", show_header=True)
        table.add_column("#", style="dim", justify="right")
        table.add_column("Agent", style="cyan")
        table.add_column("Type", style="bold")
        table.add_column("Status")
        for i, a in enumerate(agents, 1):
            a_type = "human" if a.is_human else "AI"
            try:
                status = await transport.get_agent_status(a.name)
            except Exception:
                status = "unknown"
            if status == "online":
                status_str = "[green]online[/green]"
            elif status in ("lwt", "offline"):
                status_str = "[red]offline[/red]"
            else:
                status_str = "[dim]unknown[/dim]"
            table.add_row(str(i), a.name, a_type, status_str)
        console.print(table)
        return ""
    return None


async def _ai_group_display_loop(
    gchat_agent: Any,
    settings: Settings,
) -> None:
    """Display group messages for AI agents (no input, just shows activity)."""
    member_names = [a.name for a in settings.agents_config.agents]
    event_queue = gchat_agent.event_queue

    try:
        while True:
            event = await event_queue.get()
            if event.get("type") == "group_message":
                from_name = event.get("from_agent", "?")
                content = event.get("content", "")
                if not content or not content.strip():
                    continue
                idx = member_names.index(from_name) if from_name in member_names else 0
                color = AGENT_COLORS[idx % len(AGENT_COLORS)]
                console.print(f"[bold {color}]{from_name}>[/bold {color}] {content}")
    except asyncio.CancelledError:
        pass


async def _human_group_terminal_loop(
    gchat_human: Any,
    transport: Any,
    settings: Settings,
    agent_name: str = "",
) -> None:
    """Interactive terminal for human agents: display group messages + keyboard input."""
    member_names = [a.name for a in settings.agents_config.agents]
    event_queue = gchat_human.event_queue
    prompt_session = _make_prompt_session(gchat_human._agent.card.name)

    try:
        while True:
            input_task = asyncio.ensure_future(_read_input_async(prompt_session))
            event_task = asyncio.ensure_future(event_queue.get())

            try:
                done, pending = await asyncio.wait(
                    [input_task, event_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye![/dim]")
                return

            # Group message arrived → display
            if event_task in done:
                # Gracefully exit prompt_toolkit so the input line is properly cleaned up
                try:
                    from prompt_toolkit.application import get_app
                    _app = get_app()
                    if _app is not None and _app.is_running:
                        _app.exit(result="")
                except Exception:
                    pass

                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, EOFError, KeyboardInterrupt):
                        pass

                try:
                    event = event_task.result()
                except Exception:
                    continue

                if event.get("type") == "group_message":
                    from_name = event.get("from_agent", "?")
                    content = event.get("content", "")
                    if not content or not content.strip() or from_name == agent_name:
                        continue
                    idx = member_names.index(from_name) if from_name in member_names else 0
                    color = AGENT_COLORS[idx % len(AGENT_COLORS)]
                    console.print(f"[bold {color}]{from_name}>[/bold {color}] {content}")
                continue

            # User input arrived
            if input_task in done:
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

                try:
                    user_input = input_task.result()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye![/dim]")
                    return

                if user_input.startswith("/"):
                    result = await _handle_slash_command(user_input, transport, settings)
                    if result is None:
                        console.print("[dim]Goodbye![/dim]")
                        return
                    if result:
                        console.print(result)
                    continue

                if not user_input:
                    continue

                await gchat_human.publish_human_input(user_input)

    except (KeyboardInterrupt, EOFError):
        pass


@gchat_app.callback(invoke_without_command=True)
def gchat(
    ctx: typer.Context,
    agent: str = typer.Option(..., "--agent", help="Agent name from config.json to join the group"),
) -> None:
    """Join the group chat as the specified agent."""
    from qd_evolve.core.logger import setup_logging
    from qd_evolve.core.config import LOG_DIR as LOG_DIR_PATH, MqttConfig
    from qd_evolve.agent import create_agent, init_process
    from qd_evolve.agent.loader import get_agent_entry
    from qd_evolve.agent.mqtt_transport import MqttTransport
    from qd_evolve.agent.group_chat_transport import GroupChatTransport
    from qd_evolve.agent.group_chat_agent import GroupChatAgent
    from qd_evolve.agent.group_chat_human import GroupChatHuman
    from qd_evolve.core.prompts import PromptTemplateManager

    # 1. Config & logging
    setup_logging("WARNING", log_dir=LOG_DIR_PATH)
    settings = load_settings()
    setup_logging(settings.log.level, log_dir=LOG_DIR_PATH)

    # Inject env_vars
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    # 2. Resolve agent entry
    entry = get_agent_entry(settings, agent)
    if entry is None:
        available = [a.name for a in settings.agents_config.agents]
        console.print(f"[red]Error:[/red] Agent '{agent}' not found. Available: {', '.join(available)}")
        raise SystemExit(1)
    is_human = entry.is_human

    # 3. Init process (AI agents only)
    if not is_human:
        init_process(settings, agent_name=agent)

    # 4. Create agent with gchat mode
    agent_core = create_agent(agent, settings, need_a2a=True, need_mqtt=True, need_gchat=True)

    # 5. Create MqttTransport + GroupChatTransport
    broker_cfg = settings.agents_config.mqtt_broker
    mqtt_config = entry.mqtt
    mqtt_transport = MqttTransport(
        broker_host=broker_cfg.host,
        broker_port=broker_cfg.port,
        mqtt_config=mqtt_config,
        client_name=agent,
    )
    group_transport = GroupChatTransport(
        mqtt_transport=mqtt_transport,
        broker_host=broker_cfg.host,
        broker_port=broker_cfg.port,
        mqtt_config=mqtt_config,
        client_name=f"{agent}-group",
    )

    # 6. Setup TransportRouter + AgentRegistry (AI agents only)
    if not is_human:
        from qd_evolve.agent.registry import AgentRegistry, Topology, set_agent_registry
        from qd_evolve.agent.transport import InprocTransport, TransportRouter

        topology = Topology(settings)
        router = TransportRouter(InprocTransport(), mqtt_transport)
        agent_reg = AgentRegistry(topology, current_agent=agent)
        agent_reg.register(agent_core)
        set_agent_registry(agent_reg)

        from qd_evolve.agent.a2a_tools import set_transport
        set_transport(router)

    # 7. Create wrapper
    member_names = [a.name for a in settings.agents_config.agents]
    template_mgr = PromptTemplateManager()

    if is_human:
        gchat_agent = GroupChatHuman(agent_core, group_transport, member_names)
    else:
        gchat_agent = GroupChatAgent(agent_core, group_transport, member_names, template_mgr)

    # 8. Startup panel
    agent_type = "human" if is_human else "AI"
    broker_info = f"{broker_cfg.host}:{broker_cfg.port}"
    console.print(Panel(
        f"qd-evolve v{__version__} — Group Chat\n\n"
        f"[bold]Agent:[/bold]     {agent} ({agent_type})\n"
        f"[bold]Broker:[/bold]    {broker_info}\n"
        f"[bold]Members:[/bold]   {', '.join(member_names)}\n\n"
        f"/help for commands, /quit to leave",
        style="bold green",
    ))

    # 9. Run
    async def _run() -> None:
        try:
            await group_transport.connect()
        except Exception as e:
            console.print(f"[red]Error:[/red] Failed to connect to MQTT broker at {broker_info} — {e}")
            return

        try:
            await gchat_agent.start()
        except Exception as e:
            console.print(f"[red]Error:[/red] Failed to start agent — {e}")
            await group_transport.disconnect()
            return

        if is_human:
            await _human_group_terminal_loop(gchat_agent, group_transport, settings, agent_name=agent)
        else:
            gchat_agent.start_heartbeat_loop()
            await _ai_group_display_loop(gchat_agent, settings)
    #endregion _run

    # Use loop.run_until_complete (like mqtt serve) so KeyboardInterrupt
    # can still run async cleanup before the loop closes.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        # Loop is still alive — run async cleanup, then cancel remaining tasks
        try:
            loop.run_until_complete(gchat_agent.stop())
        except Exception:
            pass
        try:
            loop.run_until_complete(group_transport.disconnect())
        except Exception:
            pass
        for task in asyncio.all_tasks(loop):
            task.cancel()
        try:
            loop.run_until_complete(
                asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True)
            )
        except Exception:
            pass
        console.print("\n[dim]Disconnected.[/dim]")
    else:
        # Normal exit (user typed /quit or EOF) — clean up gracefully
        try:
            loop.run_until_complete(gchat_agent.stop())
        except Exception:
            pass
        try:
            loop.run_until_complete(group_transport.disconnect())
        except Exception:
            pass
        for task in asyncio.all_tasks(loop):
            task.cancel()
        try:
            loop.run_until_complete(
                asyncio.gather(*asyncio.all_tasks(loop), return_exceptions=True)
            )
        except Exception:
            pass
    finally:
        loop.close()