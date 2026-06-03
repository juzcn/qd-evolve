"""Sub-agent manager — in-memory sub-agent lifecycle management.

Sub-agents are bare Agent instances that share the parent process and die with it.
Each sub-agent has a single-task model: busy sub-agents reject new tasks.
Use multiple sub-agents for parallelism.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from qd_evolve.core.logger import logger
from qd_evolve.tools.config_manager import (
    _agent_contexts,
    _current_agent_var,
    _require_context,
)

# ── Sub-agent storage — in-memory only ────────────────────────────

_sub_agents: dict[str, Any] = {}  # name → Agent
_sub_tasks: dict[str, dict[str, Any]] = {}  # task_id → {name, state, result}
_sub_complete_event = threading.Event()  # signaled when any sub-agent task completes


# ── Handlers ──────────────────────────────────────────────────────


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
        "sub_agent_manager: sub-agent '%s' created by '%s' (provider=%s, model=%s)",
        name, parent_name, agent._provider_name, agent._model,
    )
    return (
        f"Sub-agent '{name}' created.\n"
        f"  provider: {agent._provider_name}\n"
        f"  model: {agent._model}"
    )


def _run_sub_agent(name: str, task: str, reset: bool = False) -> str:
    """Run a task on a sub-agent asynchronously. Returns immediately.

    Returns a task_id. Use get_sub_result(task_id) to check status.
    If the sub-agent is busy, returns an error.

    By default (reset=False), the sub-agent keeps its conversation history
    between calls — use for multi-turn tasks that build on previous context.
    Set reset=True to clear history and start fresh.
    """
    parent_name, parent_agent, _ = _require_context()

    sub = _sub_agents.get(name)
    if sub is None:
        available = ", ".join(_sub_agents.keys()) if _sub_agents else "(none)"
        return f"Error: sub-agent '{name}' not found. Available: {available}"

    if sub._running:
        return f"Error: sub-agent '{name}' is busy"

    if reset:
        sub.reset()
        logger.info("sub_agent_manager: sub-agent '%s' context reset", name)

    task_id = uuid4().hex[:12]
    _sub_tasks[task_id] = {"name": name, "state": "running", "result": None, "consumed": False}

    def _bg_run() -> None:
        sub_name = getattr(sub, "name", "")
        tok = _current_agent_var.set(sub_name) if sub_name else None
        try:
            with sub._run_lock:
                sub._running = True
                try:
                    result = sub._run_inner(task)
                finally:
                    sub._running = False
            _sub_tasks[task_id]["state"] = "done"
            _sub_tasks[task_id]["result"] = result
        except Exception as e:
            _sub_tasks[task_id]["state"] = "error"
            _sub_tasks[task_id]["result"] = f"{type(e).__name__}: {e}"
        finally:
            if tok is not None:
                _current_agent_var.reset(tok)
            _sub_complete_event.set()

        # Push result to parent agent asynchronously (non-blocking).
        # If parent is idle, feed the result through parent._run_inner and
        # fire _on_event so the chat loop displays it. If parent is busy,
        # the result stays in _sub_tasks — _run_inner injection picks it up.
        _try_push_to_parent(parent_agent, parent_name)

    thread = threading.Thread(target=_bg_run, daemon=True)
    thread.start()

    logger.info("sub_agent_manager: sub-agent '%s' started task %s", name, task_id)
    return (
        f"Task submitted to '{name}'. Results will be delivered when ready.\n"
        f"  task_id: {task_id}\n"
        f"  state: running"
    )


def _try_push_to_parent(parent_agent: Any, parent_name: str) -> None:
    """Try to feed completed sub-agent results to the parent agent immediately.

    If the parent agent is idle (lock available), collect pending sub results,
    run them through parent._run_inner, and push the response via _on_event.
    If the parent is busy, do nothing — _run_inner injection will pick up
    the result at the start of the next LLM iteration.
    """
    if not parent_agent._on_event:
        return
    if not parent_agent._run_lock.acquire(blocking=False):
        return  # parent is busy, result will be injected mid-iteration

    parent_tok = _current_agent_var.set(parent_name) if parent_name else None
    try:
        parent_agent._running = True
        try:
            sub = collect_sub_results()
            if sub:
                final = parent_agent._run_inner(sub)
                parent_agent._on_event({"type": "sub_agent_result", "content": final})
        except Exception:
            logger.debug("_try_push_to_parent: failed to push sub result", exc_info=True)
        finally:
            parent_agent._running = False
    finally:
        parent_agent._run_lock.release()
        if parent_tok is not None:
            _current_agent_var.reset(parent_tok)


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


def collect_sub_results() -> str:
    """Collect completed sub-agent results that haven't been consumed yet.

    Called by Agent._run_inner before each LLM request, and by chat loops
    when a sub_agent_completed event fires. Returns a formatted string suitable
    for injection as a user message, or empty string if nothing pending.
    """
    parts: list[str] = []
    for task_id, entry in list(_sub_tasks.items()):
        if entry.get("consumed", False):
            continue
        state = entry.get("state", "")
        if state in ("done", "error"):
            entry["consumed"] = True
            name = entry.get("name", "unknown")
            result = entry.get("result", "")
            label = "completed" if state == "done" else "failed"
            parts.append(f"[Sub-agent '{name}' {label} task {task_id}]\n{result}")
    return "\n\n".join(parts)


def has_running_sub_agents() -> bool:
    """Return True if any sub-agent task is still running."""
    return any(e.get("state") == "running" for e in _sub_tasks.values())


# ── Register tools ────────────────────────────────────────────────

from qd_evolve.tools import get_registry

_registry = get_registry()

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
    description="Run a task on a sub-agent asynchronously. Returns immediately with a task_id. Results arrive automatically when ready. The sub-agent keeps conversation history between calls by default — use reset=True to start fresh.",
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
            "reset": {
                "type": "boolean",
                "description": "Clear the sub-agent's conversation history before this task. Default false (keeps history for multi-turn work). Set true for a clean slate.",
                "default": False,
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
