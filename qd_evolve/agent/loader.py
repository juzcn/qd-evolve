"""Agent loader — creates AgentCore instances from config.json agents list."""

from __future__ import annotations

from typing import Any

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import DEFAULT_MEMORY_DB, DEFAULT_SERVER_PORT, AgentEntry, Settings, load_settings
from qd_evolve.core.logger import logger
from qd_evolve.core.memory import MemoryStore
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import get_registry


def get_agent_entry(settings: Settings, name: str) -> AgentEntry | None:
    """Find an AgentEntry by name from settings.agents_config.agents."""
    for a in settings.agents_config.agents:
        if a.name == name:
            return a
    return None


def create_agent_core(
    name: str,
    settings: Settings | None = None,
) -> Any:
    """Create a fully configured AgentCore from config.json agents list."""
    from qd_evolve.agent.agent import Agent as AgentCore

    settings = settings or load_settings()
    entry = get_agent_entry(settings, name)
    if entry is None:
        raise ValueError(f"Agent '{name}' not found in config.json agents list")

    # Provider — use agent-specific overrides or defaults
    providers = ProviderRegistry(settings)

    # Memory — per-agent db from config
    memory_db = entry.memory_db or DEFAULT_MEMORY_DB
    backend_name = settings.memory_search.embeddings_backend
    backend = settings.embeddings_backends.get(backend_name) if backend_name else None
    if backend is None:
        logger.warning("Loader: no embeddings backend for agent '%s', skipping memory", name)
        memory = None
    else:
        memory = MemoryStore(memory_db, backend,
                             list_all_limit=settings.memory_search.list_all_limit)

    # System prompt via template
    from qd_evolve.core.prompts import PromptTemplateManager
    import platform
    from pathlib import Path
    template_mgr = PromptTemplateManager()
    template_name = entry.system_prompt_template or "default"
    system_prompt = template_mgr.render(
        template_name,
        unpreloaded_skills="",
        unpreloaded_cli="",
        unloaded_tools="",
        preloaded_skills="",
        preloaded_cli="",
        os_name=platform.system(),
        python_cmd="python",
        cwd=str(Path.cwd()),
        skills_dir="tools/skills",
        agent_name=name,
        a2a_tools=", ".join(entry.a2a_tools) if entry.a2a_tools else "",
        available_agents=", ".join(a.name for a in settings.agents_config.agents),
        agent_relations=", ".join(
            f"{r['from']}→{r['to']} ({r.get('mode', 'peer')})"
            for r in settings.agents_config.topology.relations
        ) if settings.agents_config.topology.relations else "",
    )

    # Build preload sets from agent's own toolbox.json
    from qd_evolve.core.toolbox import get_preloaded
    preload_tools: set[str] = get_preloaded("tools", agent_name=name)
    preload_skills: set[str] = get_preloaded("skills", agent_name=name)
    preload_cli: set[str] = get_preloaded("cli", agent_name=name)

    # Create AgentCore
    agent_core = AgentCore(
        settings=settings,
        registry=get_registry(),
        providers=providers,
        memory=memory,
        default_system_prompt=system_prompt,
        preload_tools=preload_tools,
        preload_skills=preload_skills,
        preload_cli=preload_cli,
        template_mgr=template_mgr,
    )

    # Build AgentCard from entry
    card = AgentCard(
        name=entry.name,
        description=entry.description,
        url=f"http://localhost:{entry.server.port}",
        capabilities=AgentCapabilities(streaming=True),
    )

    # Attach card + task_store to agent_core for registry
    task_store = TaskStore()
    agent_core.card = card
    agent_core.task_store = task_store

    return agent_core