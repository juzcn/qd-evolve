"""Agent loader — creates Agent instances from agents/<name>/agent.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import Settings, load_settings
from qd_evolve.core.logger import logger
from qd_evolve.core.memory import MemoryStore
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import ToolRegistry, get_registry


AGENTS_DIR = Path("agents")


class AgentConfig(BaseModel):
    """Runtime config for an Agent — fields beyond the A2A spec AgentCard."""

    provider: str = ""
    model: str = ""
    system_prompt_template: str = "default"
    a2a_tools: list[str] = Field(default_factory=list)
    memory: dict[str, Any] | None = None
    server: dict[str, Any] = Field(default_factory=lambda: {"host": "0.0.0.0", "port": 8001})


def load_agent_config(agent_dir: Path) -> tuple[AgentCard, AgentConfig]:
    """Load AgentCard and AgentConfig from agent.json."""
    card_path = agent_dir / "agent.json"
    if not card_path.is_file():
        raise FileNotFoundError(f"No agent.json in {agent_dir}")
    data = json.loads(card_path.read_text(encoding="utf-8"))

    # Split data: A2A fields → AgentCard, runtime fields → AgentConfig
    a2a_fields = {"name", "description", "url", "version", "capabilities", "skills",
                  "default_input_modes", "default_output_modes", "authentication"}
    card_data = {k: v for k, v in data.items() if k in a2a_fields}
    config_data = {k: v for k, v in data.items() if k not in a2a_fields}

    card = AgentCard.model_validate(card_data)
    config = AgentConfig.model_validate(config_data)
    return card, config


def create_agent(
    name: str,
    settings: Settings | None = None,
    tool_registry: ToolRegistry | None = None,
) -> Any:
    """Create a fully configured Agent from agents/<name>/agent.json."""
    from qd_evolve.agents.agent import Agent

    agent_dir = AGENTS_DIR / name
    if not agent_dir.is_dir():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    card, config = load_agent_config(agent_dir)
    settings = settings or load_settings()
    tool_registry = tool_registry or get_registry()

    # Provider
    provider_name = config.provider or settings.default_provider
    providers = ProviderRegistry(settings)

    # Memory (optional, per-agent db)
    memory: MemoryStore | None = None
    if config.memory and config.memory.get("db"):
        from qd_evolve.core.config import EmbeddingsBackend
        backend_name = settings.memory_search.default_embeddings_backend
        backend = settings.embeddings_backends.get(backend_name) if backend_name else None
        if backend:
            memory = MemoryStore(
                config.memory["db"], backend,
                list_all_limit=settings.memory_search.list_all_limit,
            )

    # System prompt via template
    from qd_evolve.core.prompts import PromptTemplateManager
    template_name = config.system_prompt_template

    # Build preload sets from agent's own toolbox.json
    from qd_evolve.core.toolbox import get_preloaded
    preload_tools: set[str] = get_preloaded("tools", agent_name=name)
    preload_skills: set[str] = get_preloaded("skills", agent_name=name)
    preload_cli: set[str] = get_preloaded("cli", agent_name=name)

    # Register A2A tools if configured
    if config.a2a_tools:
        from qd_evolve.agent.delegate import set_transport
        from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter
        inproc = InprocTransport()
        http = HttpTransport()
        router = TransportRouter(inproc, http)
        set_transport(router)

    # Build system prompt
    import platform
    from pathlib import Path as P
    template_mgr = PromptTemplateManager()
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

    return Agent(
        card=card,
        agent_core=agent_core,
        memory=memory,
        tool_registry=tool_registry,
        task_store=TaskStore(),
    )


def discover_agents() -> list[str]:
    """List all agent names found in agents/ directory."""
    if not AGENTS_DIR.is_dir():
        return []
    return sorted(
        d.name for d in AGENTS_DIR.iterdir()
        if d.is_dir() and (d / "agent.json").is_file()
    )