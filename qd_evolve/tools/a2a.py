"""A2A interaction tools — delegate_to, send_task, get_task, cancel_task."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from qd_evolve.agent.a2a import (
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
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


# ── Tool handlers ──────────────────────────────────────────────


def _delegate_to(agent: str, task: str) -> str:
    """Blocking: call target Agent, wait for result."""
    transport = _get_transport()
    message = make_text_message("user", task)

    # For inproc transport, call agent_core.run() directly (synchronous)
    from qd_evolve.agent.transport import InprocTransport
    if isinstance(transport._pick(agent), InprocTransport):
        registry = transport._get_registry()
        agent_node = registry.get(agent)
        if agent_node is None:
            # Try lazy load
            try:
                from qd_evolve.agent.loader import create_agent
                from qd_evolve.core.config import load_settings
                agent_node = create_agent(agent, load_settings())
                registry.register(agent_node)
                logger.info("delegate_to: lazy-loaded agent '%s'", agent)
            except ValueError as e:
                return f"Error: Agent '{agent}' not found in config — {e}"

        try:
            from qd_evolve.core.config import load_settings
            settings = load_settings()
            entry = next((a for a in settings.agents_config.agents if a.name == agent), None)
            prov = entry.effective_provider(settings) if entry else settings.default_provider
            mdl = entry.effective_model(settings) if entry else settings.default_model
            result = agent_node.run(task, provider=prov, model=mdl)
            logger.info("delegate_to: %s completed (%d chars)", agent, len(result))
            return result
        except Exception as e:
            logger.warning("delegate_to: %s failed: %s", agent, e)
            return f"Error from agent '{agent}': {type(e).__name__}: {e}"

    # For http transport, run async in a thread pool
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, transport.send_task(agent, message))
        result_task = future.result(timeout=60)

    if result_task.status.state == TaskState.completed:
        text = _extract_result_text(result_task)
        logger.info("delegate_to: %s completed (%d chars)", agent, len(text))
        return text
    elif result_task.status.state == TaskState.failed:
        text = _extract_result_text(result_task)
        logger.warning("delegate_to: %s failed: %s", agent, text)
        return f"Error from agent '{agent}': {text}"
    return f"Error: unexpected state {result_task.status.state}"


def _send_task(agent: str, task: str) -> str:
    """Non-blocking: submit task, return task_id immediately."""
    task_id = uuid4().hex
    _task_store[task_id] = {"target": agent, "state": "submitted", "result": None}

    transport = _get_transport()
    message = make_text_message("user", task)

    async def _watch() -> None:
        try:
            result_task = await transport.send_task(agent, message)
            _task_store[task_id]["state"] = result_task.status.state.value
            _task_store[task_id]["result"] = _extract_result_text(result_task)
        except Exception as e:
            _task_store[task_id]["state"] = "failed"
            _task_store[task_id]["result"] = f"{type(e).__name__}: {e}"

    try:
        loop = asyncio.get_running_loop()
        asyncio.ensure_future(_watch())
    except RuntimeError:
        asyncio.run(_watch())

    logger.info("send_task: submitted task %s to %s", task_id, agent)
    return json.dumps({"task_id": task_id, "state": "submitted", "agent": agent})


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

registry = get_registry()

registry.register(
    name="delegate_to",
    description="Call another Agent and wait for its response. Blocking call — returns the Agent's output directly.",
    handler=_delegate_to,
    input_schema={
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Name of the target Agent to call",
            },
            "task": {
                "type": "string",
                "description": "The task description or question to send to the target Agent",
            },
        },
        "required": ["agent", "task"],
    },
)

registry.register(
    name="send_task",
    description="Submit a task to another Agent without waiting. Returns a task_id for later status queries via get_task.",
    handler=_send_task,
    input_schema={
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Name of the target Agent",
            },
            "task": {
                "type": "string",
                "description": "The task to send",
            },
        },
        "required": ["agent", "task"],
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