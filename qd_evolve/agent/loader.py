"""Agent loader — centralized initialization and factory.

init_process(settings): per-process setup (SkillRegistry, CLIRegistry, BridgeManager).
create_agent(name, settings): per-agent factory → Agent or A2AAgent.
get_agent_entry(settings, name): lookup AgentEntry from config.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from qd_evolve.core.config import SKILLS_DIR, CLI_TOOLS_DIR, AgentEntry, Settings
from qd_evolve.core.logger import logger
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.prompts import PromptTemplateManager
from qd_evolve.core.registry import get_registry


# ── Module-level singletons ────────────────────────────────────────

_skill_registry: Any = None
_cli_registry: Any = None
_bridges: list[Any] = []


def get_skill_registry() -> Any:
    global _skill_registry
    if _skill_registry is None:
        from qd_evolve.skills import SkillRegistry
        _skill_registry = SkillRegistry()
    return _skill_registry


def get_cli_registry() -> Any:
    global _cli_registry
    if _cli_registry is None:
        from qd_evolve.cli_tools import CLIRegistry
        _cli_registry = CLIRegistry()
    return _cli_registry


def get_bridges() -> list[Any]:
    return _bridges


# ── Per-process initialization ─────────────────────────────────────

def init_process(settings: Settings, agent_name: str = "") -> None:
    """Per-process setup: SkillRegistry, CLIRegistry, BridgeManager.connect_all,
    registry injection into loader tools."""
    global _skill_registry, _cli_registry, _bridges

    # Skill registry
    from qd_evolve.skills import SkillRegistry
    if _skill_registry is None:
        _skill_registry = SkillRegistry()
    _skill_registry.discover_skills(SKILLS_DIR)

    # CLI registry
    from qd_evolve.cli_tools import CLIRegistry
    if _cli_registry is None:
        _cli_registry = CLIRegistry()
    _cli_registry.discover(CLI_TOOLS_DIR)

    # Inject registries into tool modules
    from qd_evolve.tools.skill_loader import set_skill_registry
    set_skill_registry(_skill_registry)
    from qd_evolve.tools.install_skill import set_skill_registry as set_skill_registry2
    set_skill_registry2(_skill_registry)
    from qd_evolve.tools.cli_loader import set_cli_registry
    set_cli_registry(_cli_registry)

    # Bridges
    from tools.bridge import BridgeManager
    _bridges = BridgeManager.connect_all(settings, agent_name=agent_name)

    logger.debug("init_process: skills=%d, cli=%d, bridges=%d",
                 len(_skill_registry.get_all_skills()),
                 len(_cli_registry.list_tools()),
                 len(_bridges))


# ── Agent entry lookup ─────────────────────────────────────────────

def get_agent_entry(settings: Settings, name: str) -> AgentEntry | None:
    """Lookup AgentEntry from config by name."""
    for a in settings.agents_config.agents:
        if a.name == name:
            return a
    return None


# ── A2A enabled check ──────────────────────────────────────────────

def _a2a_enabled(settings: Settings) -> bool:
    """A2A tools and system prompt section are auto-enabled when >1 agent."""
    return len(settings.agents_config.agents) > 1


# ── Per-agent factory ──────────────────────────────────────────────

def create_agent(name: str, settings: Settings, *, need_a2a: bool | None = None, need_mqtt: bool = False, need_gchat: bool = False) -> Any:
    """Create a fully initialized Agent (or A2AAgent / MqttAgent) from name + settings.

    Resolves entry, registries, and providers internally.
    Handles: memory creation, system prompt rendering, preload execution,
    provider/model resolution, A2A tool toggling.

    Args:
        name: Agent name from config.json agents list.
        settings: Settings instance.
        need_a2a: If True, always wrap with A2AAgent. If None, auto-detect
                  from agent count. If False, never wrap.
        need_mqtt: If True, wrap with MqttAgent (which wraps A2AAgent).
    """
    from qd_evolve.agent.agent import Agent

    # Resolve entry
    entry = get_agent_entry(settings, name)
    if entry is None:
        raise ValueError(f"Agent '{name}' not found in config.json agents list")

    # ── Human agent: short-circuit ────────────────────────────
    if entry.is_human:
        from qd_evolve.agent.human_agent import HumanAgent
        human = HumanAgent(
            name=entry.name,
            description=entry.description,
        )
        if need_mqtt:
            from qd_evolve.agent.mqtt_human_agent import MqttHumanAgent
            broker = settings.agents_config.mqtt_broker
            return MqttHumanAgent(human, broker.host, broker.port, entry.mqtt, broker.will_delay_interval)
        return human

    # Resolve process-level singletons
    registry = get_registry()
    providers = ProviderRegistry(settings)
    skill_registry = get_skill_registry()
    cli_registry = get_cli_registry()

    # A2A mode: auto-detect (>1 agent) or explicit override
    a2a_on = _a2a_enabled(settings) if need_a2a is None else need_a2a

    # ── Toolbox state (per-agent) ─────────────────────────────
    from qd_evolve.core.toolbox import (
        apply_to_tools, apply_to_cli_registry, apply_to_skill_registry,
        get_preloaded,
    )

    loaded_tool_names: set[str] = get_preloaded("tools", agent_name=name)
    loaded_skill_names: set[str] = get_preloaded("skills", agent_name=name)
    loaded_cli_names: set[str] = get_preloaded("cli", agent_name=name)

    apply_to_tools(registry, loaded_tool_names, agent_name=name)
    apply_to_cli_registry(cli_registry, loaded_cli_names, agent_name=name)
    apply_to_skill_registry(skill_registry, loaded_skill_names, agent_name=name)

    # A2A tools: only register when running as A2A agent (not in group chat)
    if a2a_on and not need_gchat:
        from qd_evolve.agent.a2a_tools import register_a2a_tools
        register_a2a_tools()
        logger.debug("Agent [%s]: A2A tools registered (delegate_to, send_task, get_task, cancel_task)", name)

    from qd_evolve.tools.tool_loader import set_preload_tools
    set_preload_tools(loaded_tool_names)

    # ── Build preload content for system prompt ───────────────
    skill_registry._preload_skills |= loaded_skill_names
    for s in skill_registry.get_all_skills():
        if s.name in loaded_skill_names:
            s.active = True

    active_skills_parts = []
    for s in skill_registry.get_all_skills():
        if s.active and s.content:
            active_skills_parts.append(s.content)
            loaded_skill_names.add(s.name)
    active_skills_content = "\n".join(active_skills_parts)

    active_cli_parts = []
    for t in cli_registry.list_tools():
        if t.name in loaded_cli_names:
            detail = cli_registry.get_detail(t.name)
            if detail:
                active_cli_parts.append(json.dumps(detail, ensure_ascii=False))
                loaded_cli_names.add(t.name)
    active_cli_content = "\n".join(active_cli_parts)

    unloaded_skills = skill_registry.format_for_prompt(loaded=loaded_skill_names)
    unloaded_cli = cli_registry.format_for_prompt(loaded=loaded_cli_names)
    unloaded_tools = registry.format_tools_summary(loaded=loaded_tool_names)

    total_tools = len(registry.list_tools())
    logger.debug(
        "Agent [%s]: prompt %d tools (%d preload), %d unloaded skills, %d unloaded cli",
        name, total_tools, len(loaded_tool_names),
        sum(1 for l in (unloaded_skills or "").splitlines() if l.startswith("- ")),
        sum(1 for l in (unloaded_cli or "").splitlines() if l.startswith("- ")),
    )

    # ── System prompt via template ────────────────────────────
    template_mgr = PromptTemplateManager()

    if need_gchat:
        template_name = "group-default"
    elif need_mqtt:
        template_name = "mqtt-default"
    elif a2a_on:
        template_name = "a2a-default"
    else:
        template_name = "default"

    template_context = {
        "unpreloaded_skills": unloaded_skills,
        "unpreloaded_cli": unloaded_cli,
        "unloaded_tools": unloaded_tools,
        "preloaded_skills": active_skills_content,
        "preloaded_cli": active_cli_content,
        "os_name": platform.system(),
        "python_cmd": "python",
        "cwd": str(Path.cwd()),
        "skills_dir": SKILLS_DIR,
        "agent_name": entry.name,
        "available_agents": ", ".join(a.name for a in settings.agents_config.agents),
        "agent_relations": ", ".join(
            f"{r['from']}→{r['to']} ({r.get('mode', 'peer')})"
            for r in settings.agents_config.topology.relations
        ) if settings.agents_config.topology.relations else "",
        "has_human_agent": any(a.is_human for a in settings.agents_config.agents),
        "human_agent_names": ", ".join(a.name for a in settings.agents_config.agents if a.is_human),
        "mqtt_broker_host": settings.agents_config.mqtt_broker.host,
        "mqtt_broker_port": settings.agents_config.mqtt_broker.port,
        "group_members": ", ".join(a.name for a in settings.agents_config.agents),
    }
    system_prompt = template_mgr.render(template_name, **template_context)
    logger.debug("Agent [%s]: system prompt assembled (%d chars), template=%s\n%s", name, len(system_prompt), template_name, system_prompt)

    # ── Memory ────────────────────────────────────────────────
    memory = Agent._create_memory(entry, settings, registry)

    # ── Create Agent instance ─────────────────────────────────
    agent = Agent(
        settings=settings,
        registry=registry,
        providers=providers,
        memory=memory,
        default_system_prompt=system_prompt,
        preload_tools=loaded_tool_names,
        preload_skills=loaded_skill_names,
        preload_cli=loaded_cli_names,
        template_mgr=template_mgr,
        template_name=template_name,
        template_context=template_context,
    )

    # ── Provider/model ────────────────────────────────────────
    agent._provider_name = entry.effective_provider(settings)
    agent._model = entry.effective_model(settings)

    # ── Wrap with A2AAgent if needed ──────────────────────────
    if a2a_on:
        from qd_evolve.agent.a2a_agent import A2AAgent
        from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
        from qd_evolve.agent.server import TaskStore

        if mqtt_on:
            broker = settings.agents_config.mqtt_broker
            if not broker.host:
                raise ValueError("mqtt_broker.host is required for MQTT mode")
            if not broker.port:
                raise ValueError("mqtt_broker.port is required for MQTT mode")
            card_url = f"mqtt://{broker.host}:{broker.port}"
        else:
            if not entry.server.host:
                raise ValueError(f"Agent '{name}': server.host is required for A2A mode")
            if not entry.server.port:
                raise ValueError(f"Agent '{name}': server.port is required for A2A mode")
            card_url = f"http://{entry.server.host}:{entry.server.port}"
        card = AgentCard(
            name=name,
            description=entry.description,
            url=card_url,
            capabilities=AgentCapabilities(streaming=True),
        )
        a2a_agent = A2AAgent(agent, card, TaskStore())

        # ── Wrap with MqttAgent if needed ──────────────────────────
        if mqtt_on:
            from qd_evolve.agent.mqtt_agent import MqttAgent
            mqtt_agent = MqttAgent(a2a_agent, settings.agents_config.mqtt_broker.host, settings.agents_config.mqtt_broker.port, entry.mqtt, settings.agents_config.mqtt_broker.will_delay_interval)
            return mqtt_agent

        return a2a_agent

    return agent
