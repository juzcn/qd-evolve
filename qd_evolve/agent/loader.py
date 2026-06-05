"""Agent loader @centralized initialization and factory.

init_process(settings): per-process setup (SkillRegistry, CLIRegistry, BridgeManager).
create_agent(name, settings): per-agent factory → Agent or A2AAgent.
get_agent_entry(settings, name): lookup AgentEntry from config.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
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

    # Ensure CWD is on sys.path so user tools/ and skills/ are importable.
    # Needed when running via pip entry point which may strip CWD from path.
    import sys as _sys
    _cwd = str(Path.cwd())
    if _cwd not in _sys.path:
        _sys.path.insert(0, _cwd)

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


# ── Toolbox context builder ──────────────────────────────────────────

def build_toolbox_context(
    registry: Any,
    skill_registry: Any,
    cli_registry: Any,
    preload_tools: set[str],
    preload_skills: set[str],
    preload_cli: set[str],
) -> dict[str, str]:
    """Build the 5 template context variables for the toolbox sections.

    Returns a dict with keys: func_tools_section, skills_section,
    cli_tools_section, preloaded_skills_detail, preloaded_cli_detail.
    """
    import json as _json

    func_tools_section = registry.format_tools_summary(preloaded=preload_tools, loaded=set())
    skills_section = skill_registry.format_for_prompt(preloaded=preload_skills, loaded=set()) if skill_registry else ""
    cli_tools_section = cli_registry.format_for_prompt(preloaded=preload_cli, loaded=set()) if cli_registry else ""

    # Preloaded skill details appendix
    preloaded_skills_detail = ""
    if skill_registry:
        parts = [s.content for s in skill_registry.get_all_skills()
                 if s.name in preload_skills and s.content]
        preloaded_skills_detail = "\n\n".join(parts)

    # Preloaded CLI details appendix
    preloaded_cli_detail = ""
    if cli_registry:
        parts = []
        for t in cli_registry.list_tools():
            if t.name in preload_cli:
                detail = cli_registry.get_detail(t.name)
                if detail:
                    parts.append(_json.dumps(detail, ensure_ascii=False))
        preloaded_cli_detail = "\n\n".join(parts)

    # Summary counts — totals only (active/open/enabled counts go stale as
    # tools are activated at runtime; the [ready] tags in the detail list
    # are the authoritative source for activation status).
    total_func = len(registry.list_tools())
    total_skills = len(skill_registry.get_all_skills()) if skill_registry else 0
    total_cli = len(cli_registry.list_tools()) if cli_registry else 0

    def _plural(n: int, word: str) -> str:
        return f"{n} {word}{'s' if n != 1 else ''}"

    parts = [f"{_plural(total_func, 'function')}"]
    if total_skills:
        parts.append(f"{_plural(total_skills, 'skill')}")
    if total_cli:
        parts.append(f"{_plural(total_cli, 'CLI command')}")
    toolbox_summary = " — ".join(parts)

    return {
        "func_tools_section": func_tools_section,
        "skills_section": skills_section,
        "cli_tools_section": cli_tools_section,
        "preloaded_skills_detail": preloaded_skills_detail,
        "preloaded_cli_detail": preloaded_cli_detail,
        "toolbox_summary": toolbox_summary,
    }


# ── Runtime environment collection ───────────────────────────────────

def _collect_runtime_context(env_vars: dict[str, str] | None = None) -> tuple[str, str]:
    """Collect actual runtime environment info at startup.

    Args:
        env_vars: Optional dict of environment variable names to values
                  (e.g. from config.json env_vars). Used to report which
                  services are already configured.

    Returns a markdown list string suitable for injection into the
    system prompt.  Uses only stdlib @no dependency on OAT tools.
    """
    lines: list[str] = []

    # --- OS ---
    system = platform.system()
    release = platform.release()
    version = platform.version()
    machine = platform.machine()
    if system == "Windows":
        os_label = f"Windows {release} (build {version}, {machine})"
    elif system == "Darwin":
        os_label = f"macOS {release} (version {version}, {machine})"
    else:
        os_label = f"{system} {release} ({version}, {machine})"
    lines.append(f"- **OS:** {os_label}")

    # --- Python ---
    exe = sys.executable or "python"
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    lines.append(f"- **Python:** `{Path(exe).name}` {ver} at `{exe}`")

    # --- Virtual environment ---
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        lines.append(f"- **Virtual env:** `{venv}`")
    elif sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        lines.append(f"- **Virtual env:** yes (prefix: `{sys.prefix}`)")

    # --- Current shell ---
    try:
        import shellingham
        shell_name, _ = shellingham.detect_shell()
    except Exception:
        shell_name = os.environ.get("SHELL") or ("cmd" if system == "Windows" else "/bin/sh")
    lines.append(f"- **Current shell:** {shell_name}")

    # --- Package tools ---
    pkg_items: list[str] = []
    uv_path = shutil.which("uv")
    pip_path = shutil.which("pip") or shutil.which("pip3")
    if uv_path:
        pkg_items.append("`uv` [Y] (use `uv pip install <pkg>`)")
    else:
        pkg_items.append("`uv` [N]")
    if pip_path:
        pkg_items.append("`pip` [Y]")
    else:
        pkg_items.append("`pip` [N]")
    lines.append(f"- **Package tools:** {'  '.join(pkg_items)}")

    # --- Working directory ---
    cwd = Path.cwd()
    is_git = (cwd / ".git").is_dir()
    if is_git:
        lines.append(f"- **Working directory:** `{cwd}` (git repository)")
    else:
        lines.append(f"- **Working directory:** `{cwd}`")

    # --- Proxy ---
    proxy_vars = [
        ("HTTP_PROXY", os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")),
        ("HTTPS_PROXY", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
        ("NO_PROXY", os.environ.get("NO_PROXY") or os.environ.get("no_proxy")),
    ]
    active_proxies = [(name, val) for name, val in proxy_vars if val]
    if active_proxies:
        proxy_parts = ", ".join(f"{name}={val}" for name, val in active_proxies)
        lines.append(f"- **Proxy:** {proxy_parts}")

    # --- Configured API keys ---
    if env_vars:
        configured = [k for k, v in env_vars.items() if v and (k.endswith("_API_KEY") or k.endswith("_KEY"))]
        if configured:
            lines.append(f"- **Configured API keys:** {', '.join(configured)}")

    return "\n".join(lines), shell_name


# ── Per-agent factory ──────────────────────────────────────────────

def create_agent(name: str, settings: Settings, *, need_a2a: bool | None = None, need_mqtt: bool = False, need_gchat: bool = False, need_inproc: bool = False) -> Any:
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
    a2a_on = (_a2a_enabled(settings) if need_a2a is None else need_a2a) or need_inproc
    mqtt_on = need_mqtt

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

    # ── Build type-grouped toolbox sections with status tags ───
    skill_registry._preload_skills |= loaded_skill_names
    for s in skill_registry.get_all_skills():
        if s.name in loaded_skill_names:
            s.active = True

    toolbox_ctx = build_toolbox_context(
        registry, skill_registry, cli_registry,
        loaded_tool_names, loaded_skill_names, loaded_cli_names,
    )

    total_tools = len(registry.list_tools())
    logger.debug(
        "Agent [%s]: prompt %d func tools (%d preload), %d skills (%d preload), %d cli tools (%d preload)",
        name, total_tools, len(loaded_tool_names),
        len(skill_registry.get_all_skills()), len(loaded_skill_names),
        len(cli_registry.list_tools()), len(loaded_cli_names),
    )

    # ── Runtime environment detection ──────────────────────────
    runtime_context, current_shell = _collect_runtime_context(settings.env_vars)

    # Map current shell to the appropriate execution tool
    _shell_tool_map = {
        "powershell": "run_powershell",
        "pwsh": "run_powershell",
        "bash": "run_bash",
        "zsh": "run_bash",
        "fish": "run_bash",
    }
    shell_tool = _shell_tool_map.get(current_shell, "run_shell")

    # ── System prompt via template ────────────────────────────
    template_mgr = PromptTemplateManager()

    if need_gchat:
        template_name = "group-default"
    elif need_mqtt:
        template_name = "mqtt-default"
    elif need_inproc:
        template_name = "inproc-default"
    elif a2a_on:
        template_name = "a2a-default"
    else:
        template_name = "default"

    template_context = {
        **toolbox_ctx,
        "runtime_context": runtime_context,
        "shell_tool": shell_tool,
        "agent_name": entry.name,
        "agent_description": entry.description,
        "available_agents": ", ".join(a.name for a in settings.agents_config.agents),
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
    agent.name = name  # for config_manager thread-local context tracking

    # ── Provider/model ────────────────────────────────────────
    agent._provider_name = entry.effective_provider(settings)
    agent._model = entry.effective_model(settings)

    # ── Inject context for config_manager tool ────────────────
    from qd_evolve.tools.config_manager import set_agent_context
    set_agent_context(name, agent, settings)

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
        elif need_inproc:
            card_url = f"inproc://{name}"
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
        hb_template = "heartbeat"
        a2a_agent = A2AAgent(agent, card, TaskStore(), heartbeat_template=hb_template)

        # ── Wrap with MqttAgent if needed ──────────────────────────
        if mqtt_on:
            from qd_evolve.agent.mqtt_agent import MqttAgent
            mqtt_agent = MqttAgent(a2a_agent, settings.agents_config.mqtt_broker.host, settings.agents_config.mqtt_broker.port, entry.mqtt, settings.agents_config.mqtt_broker.will_delay_interval)
            return mqtt_agent

        return a2a_agent

    return agent
