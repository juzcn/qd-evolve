from __future__ import annotations

import os

from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from typer import Typer

from qd_evolve.agent import Agent
from qd_evolve.config import (
    ModelConfig,
    ProviderConfig,
    Settings,
    load_settings,
)
from qd_evolve.logger import setup_logging
from qd_evolve.prompts import TemplateStore
from qd_evolve.providers import ProviderRegistry
from qd_evolve.tools import get_registry

app = Typer(name="qd-evolve", help="AI agent with tool use", invoke_without_command=True)
console = Console()

SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/reset": "Reset conversation history",
    "/help": "Show available commands",
    "/tools": "List available tools",
    "/config": "Show current configuration",
    "/loglevel": "Set log level (e.g. /loglevel DEBUG)",
}


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for cmd, desc in SLASH_COMMANDS.items():
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display_meta=desc)


def _resolve_api_key(settings: Settings) -> None:
    if not settings.providers:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            settings.providers.append(
                ProviderConfig(
                    name="anthropic",
                    api_keys=[key],
                    models=[ModelConfig(name=settings.default_model)],
                )
            )


def _handle_slash_command(cmd: str, agent: Agent, settings: Settings) -> str | None:
    parts = cmd.strip("/").split(maxsplit=1)
    name = "/" + parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    if name == "/quit":
        return "EXIT"
    if name == "/reset":
        agent.reset()
        return "Conversation reset."
    if name == "/help":
        lines = [f"  [bold]{k}[/bold] — {v}" for k, v in SLASH_COMMANDS.items()]
        return "\n".join(lines)
    if name == "/tools":
        registry = get_registry()
        lines = [f"  [bold]{n}[/bold] — {registry.get(n).description}" for n in registry.list_names()]
        return "\n".join(lines)
    if name == "/config":
        _resolve_api_key(settings)
        lines = [
            f"  default_provider: {settings.default_provider}",
            f"  default_model: {settings.default_model}",
            f"  log_level: {settings.log_level}",
            f"  api_key: {'***configured***' if settings.is_configured else '[red]NOT SET[/red]'}",
        ]
        return "\n".join(lines)
    if name == "/loglevel":
        if not arg:
            return f"Current log level: {settings.log_level}"
        settings.log_level = arg.upper()
        setup_logging(settings.log_level)
        return f"Log level set to: {settings.log_level}"

    return f"Unknown command: {name}. Type /help for available commands."


@app.callback(invoke_without_command=True)
def main(
    template: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Start an interactive chat session. All config via qd-evolve.json."""
    # Init logging first so no DEBUG leaks to stderr
    setup_logging()
    settings = load_settings()
    setup_logging(settings.log_level)

    import qd_evolve.tools.file_rw  # noqa: F401 — registers tools
    import qd_evolve.tools.shell  # noqa: F401 — registers tools

    _resolve_api_key(settings)

    if not settings.is_configured:
        console.print("[red]Error:[/red] No API key configured. Edit qd-evolve.json or set ANTHROPIC_API_KEY env.")
        raise SystemExit(1)

    system_prompt = settings.default_system_prompt
    if template:
        store = TemplateStore()
        try:
            tpl = store.load(template)
            system_prompt = tpl.system
            console.print(f"[dim]Using template: {template}[/]")
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Template '{template}' not found.")
            raise SystemExit(1)

    registry = get_registry()
    providers = ProviderRegistry(settings)
    agent = Agent(settings, registry, providers)

    session = PromptSession(history=InMemoryHistory(), completer=SlashCompleter())

    console.print(Panel("qd-evolve agent — type [bold]/help[/] for commands, [bold]/quit[/] to leave", style="blue"))

    while True:
        try:
            user_input = session.prompt("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            result = _handle_slash_command(user_input, agent, settings)
            if result == "EXIT":
                console.print("[dim]Goodbye![/]")
                break
            console.print(result)
            continue

        try:
            with console.status("[bold blue]Thinking..."):
                response = agent.run(user_input, system=system_prompt, provider=provider, model=model)
            console.print(Panel(Markdown(response), title="Assistant", border_style="cyan"))
            prov = agent.providers.get(agent._provider_name)
            model_name = agent._model or settings.default_model
            ctx = prov.get_context_window(model_name)
            max_tok = prov.get_max_tokens(model_name)
            pct_ctx = f" ({agent.total_tokens / ctx * 100:.1f}% of {ctx} context)" if ctx > 0 else ""
            pct_max = f" ({agent.total_output_tokens / max_tok * 100:.1f}% of {max_tok} max)" if max_tok > 0 else ""
            console.print(f"[dim]Tokens: {agent.total_input_tokens} in + {agent.total_output_tokens} out = {agent.total_tokens} total{pct_ctx} | output{pct_max}[/]")
        except Exception as e:
            logger.exception("Agent error")
            console.print(f"[red]Error:[/red] {e}")