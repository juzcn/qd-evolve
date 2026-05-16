"""Agent loader — creates Agent instances from config.json agents list."""

from __future__ import annotations

from typing import Any

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import AgentEntry, Settings, load_settings
from qd_evolve.core.logger import logger
from qd_evolve.core.memory import MemoryStore
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import ToolRegistry, get_registry


def get_agent_entry(settings: Settings, name: str) -> AgentEntry | None:
    """Find an AgentEntry by name from settings.agents_config.agents."""
    for a in settings.agents_config.agents:
        if a.name == name:
            return a
    return None


def create_agent(
    name: str,
    settings: Settings | None = None,
    tool_registry: ToolRegistry | None = None,
) -> Any:
    """Create a fully configured Agent from config.json agents list."""
    from qd_evolve.agent.agent import Agent

    settings = settings or load_settings()
    tool_registry = tool_registry or get_registry()
    entry = get_agent_entry(settings, name)
    if entry is None:
        raise ValueError(f"Agent '{name}' not found in config.json agents list")

    # Provider — use agent-specific overrides or defaults
    provider_name = entry.provider or settings.default_provider
    model_name = entry.model or settings.default_model
    providers = ProviderRegistry(settings)

    # Memory (optional, per-agent db)
    memory: MemoryStore | None = None
    if entry.memory and entry.memory.get("db"):
        backend_name = settings.memory_search.default_embeddings_backend
        backend = settings.embeddings_backends.get(backend_name) if backend_name else None
        if backend:
            memory = MemoryStore(
                entry.memory["db"], backend,
                list_all_limit=settings.memory_search.list_all_limit,
            )

    # System prompt via template
    from qd_evolve.core.prompts import PromptTemplateManager
    import platform
    from pathlib import Path as P
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
        cwd=str(P.cwd()),
        skills_dir="tools/skills",
    )

    # Build preload sets from agent's own toolbox.json
    from qd_evolve.core.toolbox import get_preloaded
    preload_tools: set[str] = get_preloaded("tools", agent_name=name)
    preload_skills: set[str] = get_preloaded("skills", agent_name=name)
    preload_cli: set[str] = get_preloaded("cli", agent_name=name)

    # Register A2A tools if configured
    if entry.a2a_tools:
        from qd_evolve.tools.a2a import set_transport
        from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter
        inproc = InprocTransport()
        http = HttpTransport()
        router = TransportRouter(inproc, http)
        set_transport(router)

    # Create AgentCore
    from qd_evolve.agent.agent import Agent as AgentCore
    agent_core = AgentCore(
        settings=settings,
        registry=tool_registry,
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
        url=f"http://localhost:{entry.server.get('port', 8001)}",
        capabilities=AgentCapabilities(streaming=True),
    )

    return Agent(
        card=card,
        agent_core=agent_core,
        memory=memory,
        tool_registry=tool_registry,
        task_store=TaskStore(),
    )