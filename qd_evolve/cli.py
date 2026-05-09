from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

import typer
from loguru import logger
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from qd_evolve.config import Settings, load_settings
from qd_evolve.prompts import PromptTemplateManager
from qd_evolve.providers import ProviderRegistry
from qd_evolve.skills import SkillRegistry
from qd_evolve.tools import get_registry
from qd_evolve.tools._mcp_client import connect_mcp_servers, discover_mcp_servers

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("qd-evolve")
except Exception:
    __version__ = "0.1.0"

app = typer.Typer(help="qd-evolve AI agent")
console = Console()

SLASH_COMMANDS = {
    "/quit": "Quit the session",
    "/reset": "Reset conversation history",
    "/help": "Show available commands",
    "/tools": "List available tools",
    "/skills": "List loaded skills",
    "/config": "Show current configuration",
    "/loglevel": "Set log level (e.g. /loglevel DEBUG)",
    "/models": "Pick a model to switch to",
}


def _detect_python_cmd() -> str:
    """Detect a working python command by actually running it."""
    import subprocess
    for cmd in (sys.executable, "python3", "python"):
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return cmd
        except Exception:
            continue
    return "python"


def _read_input() -> str:
    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.completion import WordCompleter

        completer = WordCompleter(list(SLASH_COMMANDS.keys()), ignore_case=True, sentence=True)
        return pt_prompt("You> ", completer=completer).strip()
    except ImportError:
        return console.input("[bold cyan]You>[/bold cyan] ").strip()


def _handle_slash_command(
    cmd: str,
    agent: Any,
    settings: Settings,
    providers: ProviderRegistry,
    skill_registry: SkillRegistry,
) -> str | None:
    name = cmd.lower().strip()
    if name == "/quit":
        return None
    if name == "/reset":
        agent.messages.clear()
        return "Conversation reset."
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
        by_category = registry.list_by_category()
        if not by_category:
            return "  (no tools loaded)"
        lines = []
        for cat, tool_names in by_category.items():
            lines.append(f"  [bold]{cat}:[/bold]")
            for n in tool_names:
                td = registry.get(n)
                desc = (td.description or "")[:80] if td else ""
                lines.append(f"    [cyan]{n}[/cyan] — {desc}")
        return "\n".join(lines)
    if name == "/skills":
        skills = skill_registry.list_skills()
        if not skills:
            return "  (no skills loaded)"
        lines = []
        for s in skills:
            lines.append(f"  [bold]{s.name}[/bold] v{s.version}")
        return "\n".join(lines)
    if name == "/config":
        lines = [
            f"  Provider: {settings.default_provider}",
            f"  Model: {settings.default_model}",
            f"  Log level: {settings.log_level}",
        ]
        return "\n".join(lines)
    if name == "/loglevel":
        parts = cmd.strip().split(maxsplit=1)
        if len(parts) < 2:
            return "  Usage: /loglevel <DEBUG|INFO|WARNING|ERROR>"
        level = parts[1].upper()
        logger.remove()
        from qd_evolve.logger import setup_logging
        setup_logging(level)
        return f"  Log level set to {level}"
    if name == "/models":
        table = Table(title="Available Models", show_header=True)
        table.add_column("#", style="dim")
        table.add_column("Provider", style="bold")
        table.add_column("Model ID", style="bold cyan")
        table.add_column("Name")
        all_models: list[tuple[str, str, str]] = []
        for p in settings.providers:
            for m in p.models:
                all_models.append((p.name, m.id, m.name or m.id))
        for i, (prov_name, mid, mname) in enumerate(all_models, 1):
            table.add_row(str(i), prov_name, mid, mname)
        console.print(table)
        choice = console.input("[bold]Switch to #:[/bold] ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(all_models):
            prov_name, mid, _ = all_models[int(choice) - 1]
            settings.default_provider = prov_name
            settings.default_model = mid
            return f"  Switched to {prov_name}/{mid} (restart to apply)"
        return "  Cancelled."
    return None


@app.command()
def chat(
    provider: str = typer.Option("", "--provider", "-p", help="Provider name override"),
    model: str = typer.Option("", "--model", "-m", help="Model name override"),
) -> None:
    from qd_evolve.agent import Agent
    from qd_evolve.logger import setup_logging

    # 1. Config & logging
    setup_logging("WARNING")
    settings = load_settings()
    setup_logging(settings.log_level)

    # Inject env_vars from config into os.environ
    import os
    for key, value in settings.env_vars.items():
        os.environ[key] = value

    if not settings.is_configured:
        console.print("[red]Error:[/red] No API key configured. Edit qd-evolve.json")
        raise SystemExit(1)

    # 2. Builtin tools
    registry = get_registry()

    # 3. Skills
    skill_registry = SkillRegistry()
    skill_registry.discover_skills(settings.skills_dir)

    # 4. MCP servers
    mcp_configs = discover_mcp_servers()
    connect_mcp_servers(mcp_configs)

    # 5. Inject skill_registry into skill_loader tool
    from qd_evolve.tools.skill_loader import set_skill_registry
    set_skill_registry(skill_registry)

    # 6. System prompt via Jinja2 template
    python_cmd = _detect_python_cmd()
    template_mgr = PromptTemplateManager()
    skill_addition = skill_registry.format_for_prompt()
    tools_summary = registry.format_tools_summary()
    system_prompt = template_mgr.render(
        "default",
        skills=skill_addition,
        tools_summary=tools_summary,
        os_name=platform.system(),
        python_cmd=python_cmd,
        cwd=str(Path.cwd()),
        skills_dir=str(Path(settings.skills_dir).resolve()),
    )

    # 6. Provider
    providers = ProviderRegistry(settings)
    settings.default_system_prompt = system_prompt

    agent = Agent(settings=settings, registry=registry, providers=providers)

    model_info = escape(f"[{settings.default_provider}/{settings.default_model}]")
    console.print(Panel(
        f"qd-evolve v{__version__} {model_info} - /help for commands, /quit to leave",
        style="bold green",
    ))

    while True:
        try:
            user_input = _read_input()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
            continue
        if user_input.startswith("/"):
            result = _handle_slash_command(user_input, agent, settings, providers, skill_registry)
            if result is None:
                console.print("[dim]Goodbye![/dim]")
                break
            if result:
                console.print(result)
            continue

        spinner = Spinner("dots", text=Text("Thinking...", style="bold green"))
        def _on_status(text: str) -> None:
            spinner.update(text=Text(text, style="bold green"))
        agent.set_status_callback(_on_status)
        with Live(spinner, console=console, transient=True):
            try:
                response = agent.run(user_input)
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                continue
        console.print(f"[bold]Assistant:[/bold] {response}")
        prov = providers.get()
        model_name = agent._model or settings.default_model
        ctx = prov.get_context_window(model_name)
        max_tok = prov.get_max_tokens(model_name)
        last_in = agent.last_input_tokens
        last_out = agent.last_output_tokens
        pct_ctx = f" ({last_in / ctx * 100:.1f}% of {ctx} context)" if ctx > 0 else ""
        pct_max = f" ({last_out / max_tok * 100:.1f}% of {max_tok} max)" if max_tok > 0 else ""
        console.print(f"[dim]This turn: {last_in} in{pct_ctx} + {last_out} out{pct_max}[/dim]")
        console.print(f"[dim]Cumulative: {agent.total_input_tokens + agent.total_output_tokens} tokens used[/dim]")


if __name__ == "__main__":
    app()
