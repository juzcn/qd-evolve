"""Agent loader — creates AgentCore instances from config.json agents list."""

from __future__ import annotations

import json as _json
import platform
from pathlib import Path
from typing import Any

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import DEFAULT_MEMORY_DB, DEFAULT_SERVER_PORT, SKILLS_DIR, CLI_TOOLS_DIR, AgentEntry, Settings, load_settings
from qd_evolve.core.logger import logger
from qd_evolve.core.memory import MemoryStore
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import get_registry
from qd_evolve.core.prompts import PromptTemplateManager


# ── per-process state (initialized once by init_process) ──────────

_skill_registry: Any = None
_cli_registry: Any = None
_bridges: list[Any] = []
_process_initialized: bool = False


def init_process(settings: Settings) -> None:
    """Per-process initialization: skills, CLI tools, bridges, registry injection.

    Must be called once before create_agent_core(). Safe to call multiple times
    (subsequent calls are no-ops).
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


# ── per-agent initialization ──────────────────────────────────────

def create_agent_core(
    name: str,
    settings: Settings | None = None,
) -> Any:
    """Create a fully configured AgentCore from config.json agents list.

    Performs complete per-agent initialization: toolbox state, preload sets,
    system prompt rendering with proper skill/CLI/tool summaries, memory,
    provider, A2A tools, and AgentCard/TaskStore.
    """
    from qd_evolve.agent.agent import Agent as AgentCore

    settings = settings or load_settings()

    # Ensure process-level init has happened
    init_process(settings)

    entry = get_agent_entry(settings, name)
    if entry is None:
        raise ValueError(f"Agent '{name}' not found in config.json agents list")

    registry = get_registry()

    # ── Toolbox state (per-agent) ─────────────────────────────────
    from qd_evolve.core.toolbox import (
        apply_to_tools, apply_to_cli_registry, apply_to_skill_registry,
        get_preloaded,
    )

    loaded_tool_names: set[str] = get_preloaded("tools", agent_name=name)
    loaded_skill_names: set[str] = get_preloaded("skills", agent_name=name)
    loaded_cli_names: set[str] = get_preloaded("cli", agent_name=name)

    apply_to_tools(registry, loaded_tool_names, agent_name=name)
    apply_to_cli_registry(_cli_registry, loaded_cli_names, agent_name=name)
    apply_to_skill_registry(_skill_registry, loaded_skill_names, agent_name=name)

    # Inject preload set into tool_loader (for load_func to know what's loaded)
    from qd_evolve.tools.tool_loader import set_preload_tools
    set_preload_tools(loaded_tool_names)

    # ── Build preload content for system prompt ───────────────────
    # Merge toolbox preloads into registry state
    _skill_registry._preload_skills |= loaded_skill_names
    for s in _skill_registry.get_all_skills():
        if s.name in loaded_skill_names:
            s.active = True

    active_skills_parts = []
    for s in _skill_registry.get_all_skills():
        if s.active and s.content:
            active_skills_parts.append(s.content)
            loaded_skill_names.add(s.name)
    active_skills_content = "\n".join(active_skills_parts)

    active_cli_parts = []
    for t in _cli_registry.list_tools():
        if t.name in loaded_cli_names:
            detail = _cli_registry.get_detail(t.name)
            if detail:
                active_cli_parts.append(_json.dumps(detail, ensure_ascii=False))
                loaded_cli_names.add(t.name)
    active_cli_content = "\n".join(active_cli_parts)

    # Unloaded summaries exclude already-loaded items
    unloaded_skills = _skill_registry.format_for_prompt(loaded=loaded_skill_names)
    unloaded_cli = _cli_registry.format_for_prompt(loaded=loaded_cli_names)
    unloaded_tools = registry.format_tools_summary(loaded=loaded_tool_names)

    # Log prompt composition
    total_tools = len(registry.list_tools())
    unloaded_count = sum(1 for l in (unloaded_tools or "").splitlines() if l.startswith("- "))
    unloaded_skill_count = sum(1 for l in (unloaded_skills or "").splitlines() if l.startswith("- "))
    unloaded_cli_count = sum(1 for l in (unloaded_cli or "").splitlines() if l.startswith("- "))
    logger.debug(
        "Loader [%s]: prompt %d tools (%d preload, %d unloaded), %d unloaded skills, %d unloaded cli",
        name, total_tools, len(loaded_tool_names), unloaded_count,
        unloaded_skill_count, unloaded_cli_count,
    )

    # ── System prompt via template ────────────────────────────────
    template_mgr = PromptTemplateManager()
    template_name = entry.system_prompt_template or "default"
    system_prompt = template_mgr.render(
        template_name,
        unpreloaded_skills=unloaded_skills,
        unpreloaded_cli=unloaded_cli,
        unloaded_tools=unloaded_tools,
        preloaded_skills=active_skills_content,
        preloaded_cli=active_cli_content,
        os_name=platform.system(),
        python_cmd="python",
        cwd=str(Path.cwd()),
        skills_dir=SKILLS_DIR,
        agent_name=name,
        a2a_enabled=len(settings.agents_config.agents) > 1,
        available_agents=", ".join(a.name for a in settings.agents_config.agents),
        agent_relations=", ".join(
            f"{r['from']}→{r['to']} ({r.get('mode', 'peer')})"
            for r in settings.agents_config.topology.relations
        ) if settings.agents_config.topology.relations else "",
    )
    logger.debug("Loader [%s]: system prompt (%d chars)", name, len(system_prompt))

    # ── Provider ──────────────────────────────────────────────────
    providers = ProviderRegistry(settings)

    # ── Memory ────────────────────────────────────────────────────
    memory_db = entry.memory_db
    if memory_db:
        backend_name = settings.memory_search.embeddings_backend
        backend = settings.embeddings_backends.get(backend_name) if backend_name else None
        if backend is None:
            logger.warning("Loader [%s]: no embeddings backend, skipping memory", name)
            memory = None
        else:
            memory = MemoryStore(memory_db, backend,
                                 list_all_limit=settings.memory_search.list_all_limit)
            # Inject memory store into recall_memory tool
            from qd_evolve.tools.recall_memory import set_memory_store, set_default_limit
            set_memory_store(memory)
            set_default_limit(settings.memory_search.recall_memory_limit)
    else:
        memory = None
        logger.info("Loader [%s]: memory disabled (memory_db is empty/null)", name)
        # Disable recall_memory tool so LLM won't attempt to call it
        recall_td = registry.get("recall_memory")
        if recall_td:
            recall_td.enabled = False

    # ── A2A tools ─────────────────────────────────────────────────
    if len(settings.agents_config.agents) <= 1:
        for tname in ("delegate_to", "send_task", "get_task", "cancel_task"):
            td = registry.get(tname)
            if td:
                td.enabled = False
        logger.info("Loader [%s]: A2A disabled (only 1 agent configured)", name)

    # ── Create AgentCore ──────────────────────────────────────────
    agent_core = AgentCore(
        settings=settings,
        registry=registry,
        providers=providers,
        memory=memory,
        default_system_prompt=system_prompt,
        preload_tools=loaded_tool_names,
        preload_skills=loaded_skill_names,
        preload_cli=loaded_cli_names,
        template_mgr=template_mgr,
    )

    # ── AgentCard + TaskStore ─────────────────────────────────────
    card = AgentCard(
        name=entry.name,
        description=entry.description,
        url=f"http://localhost:{entry.server.port}",
        capabilities=AgentCapabilities(streaming=True),
    )
    task_store = TaskStore()
    agent_core.card = card
    agent_core.task_store = task_store

    # ── Provider/model from agent entry ───────────────────────────
    agent_core._provider_name = entry.effective_provider(settings)
    agent_core._model = entry.effective_model(settings)

    return agent_core
