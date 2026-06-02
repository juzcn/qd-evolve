"""A2A interaction tools — delegate_to, send_task, get_task, cancel_task."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from qd_evolve.agent.a2a import (
    Task,
    TaskState,
    make_text_message,
)
from qd_evolve.core.logger import logger
from qd_evolve.core.registry import get_registry


# ── Module-level state ──────────────────────────────────────────

_transport: Any = None  # TransportRouter instance, set by CLI at startup
_task_store: dict[str, dict[str, Any]] = {}  # task_id → {target, state, result}


def set_transport(transport: Any) -> None:
    """Inject the TransportRouter instance (called by CLI at startup)."""
    global _transport
    _transport = transport


def _get_transport() -> Any:
    if _transport is None:
        raise RuntimeError("A2A transport not initialized — call set_transport() first")
    return _transport


def _get_own_callback_url() -> str:
    """Return callback URL for push notifications.

    For A2AAgent in serve mode: returns the agent's own server URL.
    For CLI mode: returns the CLI's A2A server URL from a2a_cli config.
    """
    from qd_evolve.agent.registry import get_agent_registry
    registry = get_agent_registry()
    current = registry.current_agent
    if current:
        url = registry.get_url(current)
        if url:
            return url
    # Fallback: CLI mode — use a2a_cli server config
    from qd_evolve.core.config import load_settings
    settings = load_settings()
    a2a_cli = settings.agents_config.a2a_cli
    if a2a_cli.server.port:
        return f"http://localhost:{a2a_cli.server.port}"
    return ""


def _get_current_agent_name() -> str:
    """Return the name of the current agent from the registry."""
    from qd_evolve.agent.registry import get_agent_registry
    return get_agent_registry().current_agent


def on_push_notification(task_id: str, state: str, result: str) -> None:
    """Called by A2AServer._tasks_push_notification to update _task_store."""
    entry = _task_store.get(task_id)
    if entry is not None:
        entry["state"] = state
        entry["result"] = result


# ── Tool handlers ──────────────────────────────────────────────


def _delegate_to(agent_name: str, task: str, timeout: int | None = None) -> str:
    """Blocking: call target Agent, wait for result."""
    if timeout is None:
        timeout = 120
    transport = _get_transport()
    message = make_text_message("user", task)

    # For inproc transport, call agent_core.run() directly (synchronous)
    from qd_evolve.agent.transport import InprocTransport
    inproc = transport._pick(agent_name)
    if isinstance(inproc, InprocTransport):
        registry = inproc._get_registry()
        agent_node = registry.get(agent_name)
        if agent_node is None:
            # Try lazy load
            try:
                from qd_evolve.agent.loader import create_agent
                from qd_evolve.core.config import load_settings
                agent_node = create_agent(agent_name, load_settings())
                registry.register(agent_node)
                logger.info("delegate_to: lazy-loaded agent '%s'", agent_name)
            except ValueError as e:
                return f"Error: Agent '{agent_name}' not found in config — {e}"

        # Human agent: reject — must use send_task for async communication
        from qd_evolve.agent.human_agent import HumanAgent
        if isinstance(agent_node, HumanAgent):
            return "Error: Human agents require async communication. Use send_task instead of delegate_to."

        try:
            from qd_evolve.core.config import load_settings
            settings = load_settings()
            entry = next((a for a in settings.agents_config.agents if a.name == agent_name), None)
            prov = entry.effective_provider(settings) if entry else settings.default_provider
            mdl = entry.effective_model(settings) if entry else settings.default_model
            result = agent_node.run(task, provider=prov, model=mdl)
            logger.info("delegate_to: %s completed (%d chars)", agent_name, len(result))
            return result
        except Exception as e:
            logger.warning("delegate_to: %s failed: %s", agent_name, e)
            return f"Error from agent '{agent_name}': {type(e).__name__}: {e}"

    # For remote transport, run send_task
    from_agent = _get_current_agent_name()
    import concurrent.futures
    from qd_evolve.agent.mqtt_transport import MqttTransport
    remote = transport._pick(agent_name)
    if isinstance(remote, MqttTransport) and remote._loop is not None:
        # MQTT client is bound to its original event loop — must use run_coroutine_threadsafe
        future = asyncio.run_coroutine_threadsafe(
            remote.send_task(agent_name, message, from_agent=from_agent), remote._loop
        )
        result_task = future.result(timeout=timeout)
    else:
        # HttpTransport: run async in a new event loop (standard approach)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, transport.send_task(agent_name, message))
            result_task = future.result(timeout=timeout)

    # Human agent returned input_required — reject
    if result_task.status.state == TaskState.input_required:
        return "Error: Human agent requires async communication. Use send_task instead of delegate_to."

    if result_task.status.state == TaskState.completed:
        text = _extract_result_text(result_task)
        logger.info("delegate_to: %s completed (%d chars)", agent_name, len(text))
        return text
    elif result_task.status.state == TaskState.failed:
        text = _extract_result_text(result_task)
        logger.warning("delegate_to: %s failed: %s", agent_name, text)
        return f"Error from agent '{agent_name}': {text}"
    return f"Error: unexpected state {result_task.status.state}"


def _send_task(agent_name: str, task: str) -> str:
    """Non-blocking: send message to target agent, return task_id immediately."""
    task_id = uuid4().hex
    _task_store[task_id] = {"target": agent_name, "state": "submitted", "result": None}

    transport = _get_transport()
    message = make_text_message("user", task)

    from_agent = _get_current_agent_name()

    # For MQTT transport: from_agent is passed via User Property (not callback_url)
    # For HTTP transport: callback_url is set for webhook push notifications
    from qd_evolve.agent.mqtt_transport import MqttTransport
    remote = transport._pick(agent_name)
    if isinstance(remote, MqttTransport):
        # MQTT: from_agent in message metadata + User Property
        if from_agent:
            message.metadata["from_agent"] = from_agent
    else:
        # HTTP: callback_url for webhook push notifications
        callback_url = _get_own_callback_url()
        if callback_url:
            message.metadata["callback_url"] = callback_url
        if from_agent:
            message.metadata["from_agent"] = from_agent

    async def _watch() -> None:
        try:
            # ── Inproc: run AI agent in background (truly async) ──
            from qd_evolve.agent.transport import InprocTransport
            inproc = transport._pick(agent_name)
            if isinstance(inproc, InprocTransport):
                registry = inproc._get_registry()
                agent_node = registry.get(agent_name)
                if agent_node is None:
                    agent_node = inproc._lazy_load(agent_name, registry)
                if agent_node is None:
                    _task_store[task_id]["state"] = "failed"
                    _task_store[task_id]["result"] = f"Agent '{agent_name}' not found"
                    return
                from qd_evolve.agent.human_agent import HumanAgent
                if isinstance(agent_node, HumanAgent):
                    # Human: use send_task which returns input_required
                    result_task = await transport.send_task(agent_name, message)
                    _task_store[task_id]["state"] = "input_required"
                    _task_store[task_id]["result"] = None
                    remote_task_id = result_task.id
                    if remote_task_id and remote_task_id != task_id:
                        _task_store[remote_task_id] = _task_store[task_id]
                    logger.info("send_task: %s (human) returned input_required (task=%s)", agent_name, remote_task_id[:8])
                    return
                # AI agent: fire-and-forget in background thread
                _task_store[task_id]["state"] = "working"
                _task_store[task_id]["result"] = None
                async def _run_bg() -> None:
                    try:
                        result = await asyncio.to_thread(agent_node.run, task)
                        _task_store[task_id]["state"] = "completed"
                        _task_store[task_id]["result"] = result
                    except Exception as e:
                        _task_store[task_id]["state"] = "failed"
                        _task_store[task_id]["result"] = f"{type(e).__name__}: {e}"
                    # Push completion event so the calling agent's heartbeat picks it up
                    agent_node._push_event({
                        "type": "task_completed",
                        "task_id": task_id,
                        "content": _task_store[task_id].get("result", ""),
                        "metadata": {"type": "completed", "from_agent": agent_name},
                    })
                asyncio.ensure_future(_run_bg())
                logger.info("send_task: %s (inproc AI) submitted — task=%s", agent_name, task_id[:8])
                return

            # ── Remote: MQTT or HTTP ──
            if isinstance(remote, MqttTransport) and remote._loop is not None:
                result_task = asyncio.run_coroutine_threadsafe(
                    remote.send_task(agent_name, message, from_agent=from_agent), remote._loop
                ).result(timeout=120)
            else:
                result_task = await transport.send_task(agent_name, message)
            # Human agent returns input_required — result arrives via push notification
            if result_task.status.state == TaskState.input_required:
                _task_store[task_id]["state"] = "input_required"
                _task_store[task_id]["result"] = None
                remote_task_id = result_task.id
                if remote_task_id and remote_task_id != task_id:
                    _task_store[remote_task_id] = _task_store[task_id]
                logger.info("send_task: %s returned input_required (task=%s)", agent_name, remote_task_id[:8])
                return
            _task_store[task_id]["state"] = result_task.status.state.value
            _task_store[task_id]["result"] = _extract_result_text(result_task)
            # Map remote task_id to local entry so push notifications can update it
            remote_task_id = result_task.id
            if remote_task_id and remote_task_id != task_id:
                _task_store[remote_task_id] = _task_store[task_id]
        except Exception as e:
            _task_store[task_id]["state"] = "failed"
            _task_store[task_id]["result"] = f"{type(e).__name__}: {e}"

    try:
        asyncio.get_running_loop()
        asyncio.ensure_future(_watch())
    except RuntimeError:
        asyncio.run(_watch())

    logger.info("send_task: submitted task %s to %s", task_id, agent_name)
    return json.dumps({"task_id": task_id, "state": "submitted", "agent": agent_name})


def _get_task(task_id: str) -> str:
    """Query task status and result."""
    entry = _task_store.get(task_id)
    if entry is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    return json.dumps({
        "task_id": task_id,
        "agent": entry["target"],
        "state": entry["state"],
        "result": entry["result"],
    })


def _cancel_task(task_id: str) -> str:
    """Cancel a pending task."""
    entry = _task_store.get(task_id)
    if entry is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    entry["state"] = "canceled"
    logger.info("cancel_task: canceled task %s", task_id)
    return json.dumps({"task_id": task_id, "state": "canceled"})


def _extract_result_text(task: Task) -> str:
    if task.status.message and task.status.message.parts:
        for part in task.status.message.parts:
            if part.type == "text" and part.text:
                return part.text
    return ""


# ── Register tools ─────────────────────────────────────────────

def register_a2a_tools() -> None:
    """Register A2A interaction tools. Called by init_process."""
    registry = get_registry()
    _register(registry)


def _register(registry: Any) -> None:
    registry.register(
        name="delegate_to",
        description="Call an AI agent and wait for its response. Blocking call — returns the agent's output directly. For AI agents only; use send_task for human agents.",
        handler=lambda **kwargs: _delegate_to(
            kwargs["agent_name"],
            kwargs["task"],
            kwargs.get("timeout", None),
        ),
        input_schema={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "The agent_name of the target AI agent to call (see available agents in system prompt). Cannot be a human agent — use send_task instead.",
                },
                "task": {
                    "type": "string",
                    "description": "The task description or question to send to the target agent",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 120s. Increase for complex tasks that may take minutes.",
                },
            },
            "required": ["agent_name", "task"],
        },
    )

    registry.register(
        name="send_task",
        description="Submit a task to any agent (AI or human) without waiting. Returns a task_id for later status queries via get_task. Use this for human agents and async workflows.",
        handler=_send_task,
        input_schema={
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "The agent_name of the target agent (see available agents in system prompt). Works with both AI and human agents.",
                },
                "task": {
                    "type": "string",
                    "description": "The task description or question to send to the target agent",
                },
            },
            "required": ["agent_name", "task"],
        },
    )

    registry.register(
        name="get_task",
        description="Query the status and result of a previously submitted task (from send_task).",
        handler=_get_task,
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by send_task",
                },
            },
            "required": ["task_id"],
        },
    )

    registry.register(
        name="cancel_task",
        description="Cancel a pending task submitted via send_task.",
        handler=_cancel_task,
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id to cancel",
                },
            },
            "required": ["task_id"],
        },
    )