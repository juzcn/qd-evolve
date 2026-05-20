"""MQTT CLI — A2A over MQTT client/server/broker.

Commands:
  qd-evolve mqtt           Chat client (connects to agents via MQTT broker)
  qd-evolve mqtt serve     Start an agent as an MQTT-accessible server
  qd-evolve mqtt broker    Start embedded MQTT broker
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

import typer

mqtt_app = typer.Typer(help="MQTT — A2A over MQTT client/server/broker")


@mqtt_app.callback(invoke_without_command=True)
def chat(
    ctx: typer.Context,
    replay: Optional[str] = typer.Option(None, "--replay", help="Replay file for automated testing"),
    output: Optional[str] = typer.Option(None, "--output", help="Capture output to file"),
) -> None:
    """MQTT chat client. Connects to agents via MQTT broker."""
    if ctx.invoked_subcommand is not None:
        return

    from qd_evolve.core.config import load_settings
    from qd_evolve.agent.loader import init_process, create_agent
    from qd_evolve.agent.mqtt_transport import MqttTransport
    from qd_evolve.agent.mqtt_broker import ensure_broker

    settings = load_settings()
    init_process(settings)

    # Auto-start embedded broker if configured
    broker_cfg = settings.agents_config.mqtt_broker
    try:
        asyncio.run(ensure_broker(broker_cfg))
    except ImportError:
        typer.echo("Warning: amqtt not installed, embedded broker unavailable. Use external broker.", err=True)

    # Determine chat agent
    chat_agent_name = settings.agents_config.chat_agent
    agent = create_agent(chat_agent_name, settings, need_a2a=True, need_mqtt=True)

    # Connect MQTT transport
    mqtt_config = _get_mqtt_config(settings, chat_agent_name)
    transport = MqttTransport(mqtt_config, client_name="cli")

    async def _run() -> None:
        try:
            await transport.connect()
        except Exception as e:
            typer.echo(f"Failed to connect to MQTT broker: {e}", err=True)
            sys.exit(1)

        try:
            await _mqtt_chat_loop(agent, transport, settings, replay, output)
        finally:
            await transport.disconnect()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


async def _mqtt_chat_loop(
    agent: object,
    transport: MqttTransport,
    settings: object,
    replay: str | None,
    output: str | None,
) -> None:
    """Main MQTT chat loop — mirrors a2a_cli.py chat loop pattern."""
    from qd_evolve.agent.a2a import make_text_message
    from qd_evolve.agent.mqtt_transport import MqttTransport as MT

    assert isinstance(transport, MT)

    # For now, use a simple synchronous chat loop
    # (full Rich Live display integration follows the same pattern as a2a_cli.py)
    typer.echo("MQTT chat ready. Type your message (Ctrl+C to quit).")

    replay_lines = []
    if replay:
        from pathlib import Path
        replay_lines = Path(replay).read_text(encoding="utf-8").splitlines()

    output_file = None
    if output:
        from pathlib import Path
        output_file = Path(output).open("w", encoding="utf-8")

    line_idx = 0
    while True:
        try:
            if replay_lines:
                if line_idx >= len(replay_lines):
                    break
                user_input = replay_lines[line_idx]
                line_idx += 1
                typer.echo(f"You> {user_input}")
            else:
                user_input = input("You> ")
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input.strip():
            continue
        if user_input.strip().lower() in ("/quit", "/exit"):
            break

        # Send via MQTT transport to the chat agent
        try:
            message = make_text_message("user", user_input)
            result = await transport.send_task(agent.card.name, message)
            response_text = ""
            if result.status.message:
                for part in result.status.message.parts:
                    if part.type == "text" and part.text:
                        response_text += part.text
            typer.echo(f"Agent> {response_text}")
            if output_file:
                output_file.write(f"You: {user_input}\nAgent: {response_text}\n\n")
        except Exception as e:
            typer.echo(f"Error: {e}", err=True)

    if output_file:
        output_file.close()


@mqtt_app.command()
def serve(
    agent: str = typer.Option(..., "--agent", help="Agent name from config.json"),
) -> None:
    """Start an agent as an MQTT-accessible server."""
    from qd_evolve.core.config import load_settings
    from qd_evolve.agent.loader import init_process, create_agent
    from qd_evolve.agent.mqtt_broker import ensure_broker

    settings = load_settings()
    init_process(settings)

    # Auto-start embedded broker if configured
    broker_cfg = settings.agents_config.mqtt_broker
    try:
        asyncio.run(ensure_broker(broker_cfg))
    except ImportError:
        typer.echo("Warning: amqtt not installed, embedded broker unavailable. Use external broker.", err=True)

    mqtt_agent = create_agent(agent, settings, need_a2a=True, need_mqtt=True)

    async def _run() -> None:
        typer.echo(f"Starting MQTT agent '{agent}'...")
        await mqtt_agent.start()
        typer.echo(f"MQTT agent '{agent}' running. Press Ctrl+C to stop.")
        try:
            # Block until cancelled
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await mqtt_agent.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        typer.echo(f"\nMQTT agent '{agent}' stopped.")


@mqtt_app.command()
def broker() -> None:
    """Start embedded MQTT broker."""
    from qd_evolve.core.config import load_settings
    from qd_evolve.agent.mqtt_broker import ensure_broker, shutdown_broker

    settings = load_settings()
    broker_cfg = settings.agents_config.mqtt_broker

    async def _run() -> None:
        try:
            b = await ensure_broker(broker_cfg)
            typer.echo(f"MQTT broker running on {broker_cfg.host}:{broker_cfg.port}. Press Ctrl+C to stop.")
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
        except ImportError:
            typer.echo("Error: amqtt not installed. Run: pip install amqtt", err=True)
            sys.exit(1)
        finally:
            await shutdown_broker()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        typer.echo("\nMQTT broker stopped.")


def _get_mqtt_config(settings: object, agent_name: str) -> object:
    """Get MQTT config for an agent, falling back to defaults."""
    from qd_evolve.core.config import MqttConfig
    for a in settings.agents_config.agents:
        if a.name == agent_name:
            return a.mqtt
    return MqttConfig()


from qd_evolve.toolbox_tui import toolbox_app
mqtt_app.add_typer(toolbox_app, name="toolbox")
