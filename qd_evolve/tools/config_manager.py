"""Config manager tool — self-configuration and in-memory sub-agent management.

Sub-agents are bare Agent instances stored in a module-level dict. They share
the parent process and die with it. Each sub-agent has a single-task model:
busy sub-agents reject new tasks. Use multiple sub-agents for parallelism.
"""

from __future__ import annotations

import contextvars
import threading
from typing import Any
from uuid import uuid4

from qd_evolve.core.logger import logger

# ── Agent contexts — set by create_agent(), keyed by name ──────────

_agent_contexts: dict[str, tuple[Any, Any]] = {}  # name → (agent, settings)

# ContextVar auto-inherits to child threads (e.g., ToolRegistry.call() spawn).
# Unlike threading.local, ContextVar copies to Thread.start() children.
_current_agent_var: contextvars.ContextVar[str] = contextvars.ContextVar("current_agent", default="")


def set_agent_context(name: str, agent: Any, settings: Any) -> None:
    """Register an agent's context. Called by create_agent() in loader.py."""
    _agent_contexts[name] = (agent, settings)


def set_current_agent(name: str) -> None:
    """Set the current executing agent (inherits to child threads via ContextVar)."""
    _current_agent_var.set(name)


def clear_current_agent() -> None:
    """Clear the current agent (called after agent.run() completes)."""
    _current_agent_var.set("")


def _require_context() -> tuple[str, Any, Any]:
    name = _current_agent_var.get()
    if not name or name not in _agent_contexts:
        raise RuntimeError(f"config_manager: no agent context — current='{name}', known={list(_agent_contexts.keys())}")
    agent, settings = _agent_contexts[name]
    return name, agent, settings


# ── Sub-agent storage — in-memory only ────────────────────────────

_sub_agents: dict[str, Any] = {}  # name → Agent
_sub_tasks: dict[str, dict[str, Any]] = {}  # task_id → {name, state, result}


# ── Handlers ──────────────────────────────────────────────────────


def _list_providers() -> str:
    """List available LLM providers and their models."""
    _, _, settings = _require_context()
    lines: list[str] = []
    for p in settings.providers:
        models = [m.name for m in p.models]
        mark = " [default]" if p.name == settings.default_provider else ""
        lines.append(f"- {p.name}{mark}: {', '.join(models)}")
    return "\n".join(lines) if lines else "No providers configured."


def _get_my_config() -> str:
    """Show the current agent's configuration."""
    name, agent, settings = _require_context()

    entry = None
    for a in settings.agents_config.agents:
        if a.name == name:
            entry = a
            break

    if entry is None:
        return f"Error: agent '{name}' not found in config"

    provider = agent._provider_name or settings.default_provider
    model = agent._model or settings.default_model

    return (
        f"name: {name}\n"
        f"description: {entry.description}\n"
        f"provider: {provider}\n"
        f"model: {model}"
    )


def _update_my_config(
    provider: str = "",
    model: str = "",
    description: str = "",
) -> str:
    """Update the current agent's provider, model, or description.

    Only specified fields are changed. Provider/model are validated against
    the configured providers list. Changes are persisted to config.json and
    applied to the running agent immediately.
    """
    from qd_evolve.core.config import load_settings, save_settings

    name, agent, _ = _require_context()
    settings = load_settings()

    entry = None
    for a in settings.agents_config.agents:
        if a.name == name:
            entry = a
            break

    if entry is None:
        return f"Error: agent '{name}' not found in config"

    changes: list[str] = []

    # ── Provider ──
    if provider:
        p = settings.get_provider(provider)
        if p is None:
            available = ", ".join(p.name for p in settings.providers)
            return f"Error: provider '{provider}' not found. Available: {available}"
        entry.provider = provider
        agent._provider_name = provider
        changes.append(f"provider: → {provider}")

    # ── Model ──
    if model:
        prov_name = provider or entry.effective_provider(settings)
        prov = settings.get_provider(prov_name)
        if prov is None:
            return f"Error: provider '{prov_name}' not found"
        model_names = [m.name for m in prov.models]
        if model not in model_names:
            return f"Error: model '{model}' not found in provider '{prov_name}'. Available: {', '.join(model_names)}"
        entry.model = model
        agent._model = model
        changes.append(f"model: → {model}")

    # ── Description ──
    if description:
        entry.description = description
        changes.append(f"description: → {description}")

    if not changes:
        return "No changes made."

    save_settings(settings)
    return f"Updated config for '{name}':\n" + "\n".join(f"  {c}" for c in changes)


def _create_sub_agent(name: str, description: str = "") -> str:
    """Create an in-memory sub-agent. Dies with the parent process.

    Sub-agents inherit the parent's provider/model, all currently-loaded
    tools/skills/CLI, and runtime context. No persistent memory or server.
    """
    parent_name, parent_agent, settings = _require_context()

    if not name or not name.strip():
        return "Error: name is required"
    name = name.strip()

    # Check conflicts
    if name in _sub_agents:
        return f"Error: sub-agent '{name}' already exists"
    for a in settings.agents_config.agents:
        if a.name == name:
            return f"Error: '{name}' is a configured agent, cannot reuse"

    from qd_evolve.agent.agent import Agent
    from qd_evolve.core.prompts import PromptTemplateManager
    from qd_evolve.core.registry import get_registry as get_tool_registry

    # Inherit parent's preload-sets only (not accumulated runtime state).
    # Sub-agent loads additional tools on demand via load_func, just like
    # a freshly-created agent — avoiding context bloat from preload.
    inherited_preload_tools = set(parent_agent._always_active)
    inherited_preload_skills = set(parent_agent._preload_skills)
    inherited_preload_cli = set(parent_agent._preload_cli)
    ctx = parent_agent._template_context or {}
    registry = get_tool_registry()

    # Rebuild skill/CLI preload content for the sub-agent's system prompt
    skill_registry = cli_registry = None
    try:
        from qd_evolve.agent.loader import get_skill_registry, get_cli_registry
        skill_registry = get_skill_registry()
        cli_registry = get_cli_registry()
    except Exception:
        pass

    preloaded_skills_content = ""
    if skill_registry:
        for s in skill_registry.get_all_skills():
            if s.name in inherited_preload_skills and s.content:
                preloaded_skills_content += s.content + "\n"

    preloaded_cli_content = ""
    if cli_registry:
        import json
        for t in cli_registry.list_tools():
            if t.name in inherited_preload_cli:
                detail = cli_registry.get_detail(t.name)
                if detail:
                    preloaded_cli_content += json.dumps(detail, ensure_ascii=False) + "\n"

    unloaded_tools = registry.format_tools_summary(loaded=inherited_preload_tools)
    unloaded_skills = skill_registry.format_for_prompt(loaded=inherited_preload_skills) if skill_registry else ""
    unloaded_cli = cli_registry.format_for_prompt(loaded=inherited_preload_cli) if cli_registry else ""

    template_mgr = PromptTemplateManager()
    system_prompt = template_mgr.render(
        "subagent",
        name=name,
        description=description,
        runtime_context=ctx.get("runtime_context", ""),
        unpreloaded_skills=unloaded_skills,
        unpreloaded_cli=unloaded_cli,
        unloaded_tools=unloaded_tools,
        preloaded_skills=preloaded_skills_content,
        preloaded_cli=preloaded_cli_content,
        shell_tool=ctx.get("shell_tool", "run_shell"),
    )

    agent = Agent(
        settings=settings,
        registry=parent_agent.registry,
        providers=parent_agent.providers,
        memory=None,
        default_system_prompt=system_prompt,
        preload_tools=inherited_preload_tools,
        preload_skills=inherited_preload_skills,
        preload_cli=inherited_preload_cli,
    )
    agent.name = name
    agent._provider_name = parent_agent._provider_name
    agent._model = parent_agent._model

    # Register context so sub-agent doesn't crash if its LLM calls config_manager tools
    _agent_contexts[name] = (agent, settings)

    _sub_agents[name] = agent
    logger.info(
        "config_manager: sub-agent '%s' created by '%s' (provider=%s, model=%s)",
        name, parent_name, agent._provider_name, agent._model,
    )
    return (
        f"Sub-agent '{name}' created.\n"
        f"  provider: {agent._provider_name}\n"
        f"  model: {agent._model}"
    )


def _run_sub_agent(name: str, task: str) -> str:
    """Run a task on a sub-agent asynchronously. Returns immediately.

    Returns a task_id. Use get_sub_result(task_id) to check status.
    If the sub-agent is busy, returns an error.
    """
    _, _, _ = _require_context()

    agent = _sub_agents.get(name)
    if agent is None:
        available = ", ".join(_sub_agents.keys()) if _sub_agents else "(none)"
        return f"Error: sub-agent '{name}' not found. Available: {available}"

    if agent._running:
        return f"Error: sub-agent '{name}' is busy"

    task_id = uuid4().hex[:12]
    _sub_tasks[task_id] = {"name": name, "state": "running", "result": None}

    def _bg_run() -> None:
        try:
            result = agent.run(task)
            _sub_tasks[task_id]["state"] = "done"
            _sub_tasks[task_id]["result"] = result
        except Exception as e:
            _sub_tasks[task_id]["state"] = "error"
            _sub_tasks[task_id]["result"] = f"{type(e).__name__}: {e}"

    thread = threading.Thread(target=_bg_run, daemon=True)
    thread.start()

    logger.info("config_manager: sub-agent '%s' started task %s", name, task_id)
    return f"Task submitted to '{name}'.\n  task_id: {task_id}\n  state: running"


def _get_sub_result(task_id: str) -> str:
    """Query the result of a sub-agent task."""
    entry = _sub_tasks.get(task_id)
    if entry is None:
        return f"Error: task '{task_id}' not found"

    state = entry["state"]
    if state == "running":
        return f"task_id: {task_id}\n  agent: {entry['name']}\n  state: running"
    elif state == "done":
        return f"task_id: {task_id}\n  agent: {entry['name']}\n  state: done\n  result:\n{entry['result']}"
    else:
        return f"task_id: {task_id}\n  agent: {entry['name']}\n  state: error\n  error:\n{entry['result']}"


# ── Register tools ────────────────────────────────────────────────

from qd_evolve.tools import get_registry

_registry = get_registry()

_registry.register(
    name="list_providers",
    description="List available LLM providers and their models.",
    handler=_list_providers,
    input_schema={"type": "object", "properties": {}, "required": []},
)

_registry.register(
    name="get_my_config",
    description="Show the current agent's configuration (name, description, provider, model).",
    handler=_get_my_config,
    input_schema={"type": "object", "properties": {}, "required": []},
)

_registry.register(
    name="update_my_config",
    description="Update the current agent's provider, model, or description. Only specify fields to change. Validates provider/model against available options.",
    handler=_update_my_config,
    input_schema={
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "description": "Provider name to switch to (empty = no change). Use list_providers to see options.",
            },
            "model": {
                "type": "string",
                "description": "Model name to switch to (empty = no change). Must belong to the provider.",
            },
            "description": {
                "type": "string",
                "description": "New description for this agent (empty = no change).",
            },
        },
        "required": [],
    },
)

_registry.register(
    name="create_sub_agent",
    description="Create an in-memory sub-agent that inherits the parent's provider/model. Sub-agents live only in the current process and process tasks via run_sub_agent. Create multiple sub-agents for parallel work.",
    handler=_create_sub_agent,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Unique name for the sub-agent.",
            },
            "description": {
                "type": "string",
                "description": "What this sub-agent does (e.g., 'handles data processing tasks').",
            },
        },
        "required": ["name"],
    },
)

_registry.register(
    name="run_sub_agent",
    description="Run a task on a sub-agent asynchronously. Returns immediately with a task_id. Use get_sub_result to check status. If the sub-agent is busy, returns an error — create another sub-agent for parallel work.",
    handler=_run_sub_agent,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the sub-agent to run.",
            },
            "task": {
                "type": "string",
                "description": "The task to assign to the sub-agent.",
            },
        },
        "required": ["name", "task"],
    },
)

_registry.register(
    name="get_sub_result",
    description="Query the result of a sub-agent task by task_id. Returns running/done/error state.",
    handler=_get_sub_result,
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by run_sub_agent.",
            },
        },
        "required": ["task_id"],
    },
)
