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
from qd_evolve.utils.cancellation import CancellationToken, CancelledError

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

    # Build type-grouped toolbox sections via shared builder
    skill_registry = cli_registry = None
    try:
        from qd_evolve.agent.loader import build_toolbox_context, get_skill_registry, get_cli_registry
        skill_registry = get_skill_registry()
        cli_registry = get_cli_registry()
    except Exception:
        pass

    toolbox_ctx = build_toolbox_context(
        registry, skill_registry, cli_registry,
        inherited_preload_tools, inherited_preload_skills, inherited_preload_cli,
    )

    template_mgr = PromptTemplateManager()
    system_prompt = template_mgr.render(
        "subagent",
        name=name,
        description=description,
        runtime_context=ctx.get("runtime_context", ""),
        **toolbox_ctx,
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
    object.__setattr__(agent, "name", name)
    object.__setattr__(agent, "_provider_name", parent_agent._provider_name)
    object.__setattr__(agent, "_model", parent_agent._model)

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
    token = CancellationToken()
    _sub_tasks[task_id] = {
        "name": name, "state": "running", "result": None, "consumed": False,
        "_cancel_token": token,
    }

    def _bg_run() -> None:
        try:
            result = sub.run(task, cancel_token=token)
        except CancelledError:
            _sub_tasks[task_id]["state"] = "cancelled"
            _sub_tasks[task_id]["result"] = "Task cancelled."
        except Exception as e:
            _sub_tasks[task_id]["state"] = "error"
            _sub_tasks[task_id]["result"] = f"{type(e).__name__}: {e}"
        else:
            if token.is_cancelled:
                _sub_tasks[task_id]["state"] = "cancelled"
                _sub_tasks[task_id]["result"] = "Task cancelled."
            else:
                _sub_tasks[task_id]["state"] = "done"
                _sub_tasks[task_id]["result"] = result
        finally:
            _sub_complete_event.set()
            # Push result to parent agent asynchronously (non-blocking).
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
    elif state == "cancelled":
        return f"task_id: {task_id}\n  agent: {entry['name']}\n  state: cancelled\n  result:\n{entry['result']}"
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
        if state in ("done", "error", "cancelled"):
            entry["consumed"] = True
            name = entry.get("name", "unknown")
            result = entry.get("result", "")
            if state == "done":
                label = "completed"
            elif state == "cancelled":
                label = "cancelled"
            else:
                label = "failed"
            parts.append(f"[Sub-agent '{name}' {label} task {task_id}]\n{result}")
    return "\n\n".join(parts)


def has_running_sub_agents() -> bool:
    """Return True if any sub-agent task is still running."""
    return any(e.get("state") == "running" for e in _sub_tasks.values())


def _cancel_sub_task(task_id: str) -> str:
    """Cancel a running sub-agent task."""
    entry = _sub_tasks.get(task_id)
    if entry is None:
        return f"Error: task '{task_id}' not found"

    current_state = entry["state"]
    if current_state == "cancelled":
        return f"Task '{task_id}' was already cancelled."
    if current_state in ("done", "error"):
        return f"Task '{task_id}' already finished ({current_state})."

    # Signal the running agent to stop at the next checkpoint
    token = entry.get("_cancel_token")
    if token is not None:
        token.cancel()
        return f"Cancellation requested for task '{task_id}' (was {current_state}). The task will stop at the next checkpoint."
    else:
        # No token — task hasn't started yet, just mark it
        entry["state"] = "cancelled"
        entry["result"] = "Task cancelled before start."
        return f"Task '{task_id}' cancelled before start."


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
    description="Query the result of a sub-agent task by task_id. Returns running/done/error/cancelled state.",
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

_registry.register(
    name="cancel_sub_task",
    description="Cancel a running sub-agent task. The task will stop at the next safe checkpoint and push a 'cancelled' result back.",
    handler=_cancel_sub_task,
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
