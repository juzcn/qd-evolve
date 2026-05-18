"""Agent loader — process init + agent creation via Agent.from_config()."""

from __future__ import annotations

from typing import Any

from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.agent import Agent
from qd_evolve.core.config import SKILLS_DIR, CLI_TOOLS_DIR, AgentEntry, Settings, load_settings
from qd_evolve.core.logger import logger
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import get_registry


# ── per-process state (initialized once by init_process) ──────────

_skill_registry: Any = None
_cli_registry: Any = None
_bridges: list[Any] = []
_process_initialized: bool = False


def init_process(settings: Settings) -> None:
    """Per-process initialization: skills, CLI tools, bridges, registry injection.

    Must be called once before create_agent() / create_agent_core().
    Safe to call multiple times (subsequent calls are no-ops).
    """
    global _skill_registry, _cli_registry, _bridges, _process_initialized
    if _process_initialized:
        return

    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from tools.bridge import BridgeManager

    # Skills
    _skill_registry = SkillRegistry()
    _skill_registry.discover_skills(SKILLS_DIR)

    # CLI tools
    _cli_registry = CLIRegistry()
    _cli_registry.discover(CLI_TOOLS_DIR)

    # Bridges (MCP + OAT + ...)
    _bridges = BridgeManager.connect_all(settings)

    # Inject registries into loader tools (global singletons)
    from qd_evolve.tools.skill_loader import set_skill_registry
    set_skill_registry(_skill_registry)
    from qd_evolve.tools.cli_loader import set_cli_registry
    set_cli_registry(_cli_registry)
    from qd_evolve.tools.install_skill import set_skill_registry as set_install_skill_registry
    set_install_skill_registry(_skill_registry)
    from qd_evolve.tools.install_mcp import set_staged_bridges
    set_staged_bridges([])

    _process_initialized = True
    logger.info("Loader: process initialized (skills=%d, cli=%d, bridges=%d)",
                len(_skill_registry.get_all_skills()),
                len(_cli_registry.list_tools()),
                len(_bridges))


def get_skill_registry() -> Any:
    return _skill_registry


def get_cli_registry() -> Any:
    return _cli_registry


def get_bridges() -> list[Any]:
    return _bridges


# ── agent entry lookup ────────────────────────────────────────────

def get_agent_entry(settings: Settings, name: str) -> AgentEntry | None:
    """Find an AgentEntry by name from settings.agents_config.agents."""
    for a in settings.agents_config.agents:
        if a.name == name:
            return a
    return None


# ── agent creation (thin orchestration) ───────────────────────────

def create_agent(
    name: str,
    settings: Settings | None = None,
) -> Agent:
    """Create a plain Agent (no A2A identity) from config.json.

    Delegates all initialization to Agent.from_config().
    Used by `qd-evolve chat` for single-agent in-process mode.
    """
    settings = settings or load_settings()
    init_process(settings)

    entry = get_agent_entry(settings, name)
    if entry is None:
        raise ValueError(f"Agent '{name}' not found in config.json agents list")

    registry = get_registry()
    providers = ProviderRegistry(settings)

    return Agent.from_config(entry, settings, registry, providers, _skill_registry, _cli_registry)


def create_agent_core(
    name: str,
    settings: Settings | None = None,
) -> A2AAgent:
    """Create an A2AAgent (with A2A identity) from config.json.

    Delegates all initialization to A2AAgent.from_config().
    Used by `qd-evolve a2a serve` for standalone A2A server mode.
    """
    settings = settings or load_settings()
    init_process(settings)

    entry = get_agent_entry(settings, name)
    if entry is None:
        raise ValueError(f"Agent '{name}' not found in config.json agents list")

    registry = get_registry()
    providers = ProviderRegistry(settings)

    return A2AAgent.from_config(entry, settings, registry, providers, _skill_registry, _cli_registry)
