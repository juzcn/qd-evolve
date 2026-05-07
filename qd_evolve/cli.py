from __future__ import annotations

import asyncio
import os

from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
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
    "/models": "Pick a model to switch to",
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
                    api_key=key,
                    models=[ModelConfig(name=settings.default_model)],
                )
            )


def _handle_slash_command(cmd: str, agent: Agent, settings: Settings, providers: ProviderRegistry, session: PromptSession) -> str | None:
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
        by_cat = registry.list_by_category()
        lines = []
        for cat, names in by_cat.items():
            lines.append(f"  [bold]{cat}:[/bold]")
            for n in names:
                td = registry.get(n)
                if td and registry.is_enabled(n):
                    lines.append(f"    {n} — {td.description[:80]}")
        return "\n".join(lines) if lines else "  (no tools loaded)"
    if name == "/config":
        _resolve_api_key(settings)
        lines = [
            f"  default_provider: {settings.default_provider}",
            f"  default_model: {settings.default_model}",
            f"  log_level: {settings.log_level}",
            f"  api_key: {'***configured***' if settings.is_configured else '[red]NOT SET[/red]'}",
        ]
        return "\n".join(lines)
    if name == "/models":
        all_models = providers.list_all_models()
        entries: list[tuple[str, str]] = []  # (provider, model)
        lines = []
        idx = 1
        for prov_name, model_names in all_models.items():
            for m in model_names:
                marker = " [dim](current)[/]" if prov_name == settings.default_provider and m == settings.default_model else ""
                lines.append(f"  [bold]{idx}[/bold]. {prov_name}/{m}{marker}")
                entries.append((prov_name, m))
                idx += 1
        lines.append("")
        lines.append("Enter number to switch, or press Enter to cancel:")
        console.print("\n".join(lines))
        try:
            choice = session.prompt("Model> ").strip()
        except (EOFError, KeyboardInterrupt):
            return "Cancelled."
        if not choice:
            return "Cancelled."
        try:
            n = int(choice)
            if n < 1 or n > len(entries):
                return f"Invalid selection: {n}"
        except ValueError:
            return f"Invalid input: {choice}"
        prov_name, model_name = entries[n - 1]
        settings.default_provider = prov_name
        settings.default_model = model_name
        agent._provider_name = prov_name
        agent._model = model_name
        return f"Switched to [bold]{prov_name}/{model_name}[/bold]"
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

    registry = get_registry()
    loaded = registry.discover_tools()

    from qd_evolve.tools._mcp_client import connect_mcp_servers, disconnect_mcp_servers
    mcp_bridges = connect_mcp_servers(settings.mcp_servers)

    from qd_evolve.skills import SkillLoader
    skill_loader = SkillLoader(settings.skills_dir, settings.skill_config)
    skill_count = skill_loader.discover()
    if skill_count > 0:
        skill_loader.register_tools()
        settings.default_system_prompt += skill_loader.get_system_prompt_addition()

    _resolve_api_key(settings)

    if settings.serper_api_key:
        os.environ["SERPER_API_KEY"] = settings.serper_api_key

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

    try:
        from qd_evolve.vector import VectorStore
        vs = VectorStore(settings)
        registry.set_embed_fn(vs.embed)
        registry.build_tool_embeddings()
    except Exception:
        logger.debug("Embedding init skipped (model not available)")

    session = PromptSession(history=InMemoryHistory(), completer=SlashCompleter())

    model_info = escape(f"[{settings.default_provider}/{settings.default_model}]")
    console.print(Panel(f"qd-evolve agent {model_info} — type [bold]/help[/] for commands, [bold]/quit[/] to leave", style="blue"))

    while True:
        try:
            user_input = session.prompt("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            result = _handle_slash_command(user_input, agent, settings, providers, session)
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

    disconnect_mcp_servers(mcp_bridges)