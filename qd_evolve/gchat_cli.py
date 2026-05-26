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
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from qd_evolve import __version__
from qd_evolve.cli_utils import AGENT_COLORS
from qd_evolve.core.config import Settings, load_settings, save_settings
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
            if a.is_wechat_human:
                a_type = "WeChat human"
            elif a.is_human:
                a_type = "terminal human"
            else:
                a_type = "AI"
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


async def _display_events_above_prompt(
    event_queue: asyncio.Queue[dict],
    prompt_session: Any,
    member_names: list[str],
    agent_name: str,
) -> None:
    """Print incoming group messages above the prompt, preserving user's partial input."""
    while True:
        event = await event_queue.get()
        if event.get("type") != "group_message":
            continue
        from_name = event.get("from_agent", "?")
        content = event.get("content", "")
        if not content or not content.strip() or from_name == agent_name:
            continue
        _app = getattr(prompt_session, "app", None)
        if _app and _app.is_running:
            _app.renderer.erase()
        idx = member_names.index(from_name) if from_name in member_names else 0
        color = AGENT_COLORS[idx % len(AGENT_COLORS)]
        console.print(f"[bold {color}]{from_name}>[/bold {color}] {content}")
        if _app and _app.is_running:
            _app.invalidate()


async def _wechat_human_display_loop(
    gchat_wechat: Any,
    settings: Settings,
    agent_name: str = "",
) -> None:
    """Terminal display for WeChat human agents: show group messages + slash commands.
    Messages go to/from WeChat, not terminal input."""
    member_names = [a.name for a in settings.agents_config.agents]
    event_queue = gchat_wechat.event_queue
    prompt_session = _make_prompt_session(gchat_wechat._agent.card.name)

    console.print("[dim]WeChat bridge active. Messages are sent/received via WeChat. Type /quit to exit.[/dim]")

    try:
        while True:
            event_task = asyncio.ensure_future(
                _display_events_above_prompt(event_queue, prompt_session, member_names, agent_name),
            )
            input_task = asyncio.ensure_future(_read_input_async(prompt_session))

            try:
                user_input = await input_task
            except (EOFError, KeyboardInterrupt):
                event_task.cancel()
                try:
                    await event_task
                except asyncio.CancelledError:
                    pass
                console.print("\n[dim]Goodbye![/dim]")
                return
            finally:
                if not event_task.done():
                    event_task.cancel()
                    try:
                        await event_task
                    except asyncio.CancelledError:
                        pass

            if user_input.startswith("/"):
                result = await _handle_slash_command(user_input, gchat_wechat._transport, settings)
                if result is None:
                    console.print("[dim]Goodbye![/dim]")
                    return
                if result:
                    console.print(result)
                continue

            if user_input:
                console.print("[dim]Messages are sent via WeChat. Type /help for commands.[/dim]")

    except (KeyboardInterrupt, EOFError):
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
            # Event display runs concurrently — prints above the prompt line
            # without interrupting or discarding the user's partial input.
            event_task = asyncio.ensure_future(
                _display_events_above_prompt(event_queue, prompt_session, member_names, agent_name),
            )
            input_task = asyncio.ensure_future(_read_input_async(prompt_session))

            try:
                user_input = await input_task
            except (EOFError, KeyboardInterrupt):
                event_task.cancel()
                try:
                    await event_task
                except asyncio.CancelledError:
                    pass
                console.print("\n[dim]Goodbye![/dim]")
                return
            finally:
                if not event_task.done():
                    event_task.cancel()
                    try:
                        await event_task
                    except asyncio.CancelledError:
                        pass

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
    from qd_evolve.agent.group_chat_wechat_human import GroupChatWechatHuman
    from qd_evolve.bridge.wechat_clawbot_client import WechatClawbotClient
    from qd_evolve.core.prompts import PromptTemplateManager

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

    # 2. Resolve agent entry
    entry = get_agent_entry(settings, agent)
    if entry is None:
        available = [a.name for a in settings.agents_config.agents]
        console.print(f"[red]Error:[/red] Agent '{agent}' not found. Available: {', '.join(available)}")
        raise SystemExit(1)
    is_human = entry.is_human
    is_wechat = entry.is_wechat_human

    # 3. Init process (AI agents only) + WeChat client init
    wechat_client = None
    if not is_human:
        init_process(settings, agent_name=agent)
    elif is_wechat:
        wechat_client = WechatClawbotClient()

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
        if is_wechat:
            gchat_agent = GroupChatWechatHuman(agent_core, group_transport, member_names, wechat_client)
        else:
            gchat_agent = GroupChatHuman(agent_core, group_transport, member_names)
    else:
        gchat_agent = GroupChatAgent(agent_core, group_transport, member_names, template_mgr)

    # 8. Startup panel
    if is_wechat:
        agent_type = "WeChat Human"
    elif is_human:
        agent_type = "terminal human"
    else:
        agent_type = "AI"
    broker_info = f"{broker_cfg.host}:{broker_cfg.port}"
    if is_wechat:
        hint = "Messages go to/from WeChat. /help for commands, /quit to leave"
    elif is_human:
        hint = "/help for commands, /quit to leave"
    else:
        hint = "Agent is running autonomously. Ctrl+C to stop."
    console.print(Panel(
        f"qd-evolve v{__version__} — Group Chat\n\n"
        f"[bold]Agent:[/bold]     {agent} ({agent_type})\n"
        f"[bold]Broker:[/bold]    {broker_info}\n"
        f"[bold]Members:[/bold]   {', '.join(member_names)}\n\n"
        f"{hint}",
        style="bold green",
    ))

    # 9. Run
    async def _run() -> None:
        if is_wechat:
            if await wechat_client.try_restore_session(entry.wechat_session):
                console.print("[green]WeChat session restored.[/green]")
            else:
                console.print("[dim]Waiting for WeChat login...[/dim]")
                try:
                    login_result = await wechat_client.login()
                except Exception as e:
                    console.print(f"[red]Error:[/red] WeChat login failed — {e}")
                    return
                await wechat_client.start(login_result["bot_token"], login_result.get("baseurl", ""))
                entry.wechat_session = wechat_client.get_session_dict()
                save_settings(settings)
                console.print("[green]WeChat login successful![/green]")

        try:
            await group_transport.connect()
        except Exception as e:
            console.print(f"[red]Error:[/red] Failed to connect to MQTT broker at {broker_info} — {e}")
            if is_wechat:
                await wechat_client.stop()
            return

        try:
            await gchat_agent.start()
        except Exception as e:
            console.print(f"[red]Error:[/red] Failed to start agent — {e}")
            await group_transport.disconnect()
            if is_wechat:
                await wechat_client.stop()
            return

        if is_human:
            if is_wechat:
                await _wechat_human_display_loop(gchat_agent, settings, agent_name=agent)
            else:
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