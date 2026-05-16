"""A2A transport layer — inproc (direct call) and http (JSON-RPC) transports."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Protocol, runtime_checkable

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.core.logger import logger


@runtime_checkable
class A2ATransport(Protocol):
    """A2A transport interface — implementations: InprocTransport, HttpTransport."""

    async def send_task(self, target: str, message: Message) -> Task: ...
    async def send_subscribe(self, target: str, message: Message) -> AsyncIterator[Task]: ...
    async def get_task(self, target: str, task_id: str) -> Task: ...
    async def cancel_task(self, target: str, task_id: str) -> Task: ...
    async def get_agent_card(self, target: str) -> AgentCard: ...


class InprocTransport:
    """In-process transport — direct AgentCore.run() call, zero network latency."""

    def __init__(self) -> None:
        # Lazy reference to AgentRegistry to avoid circular imports
        self._registry: Any = None

    def _get_registry(self) -> Any:
        if self._registry is None:
            from qd_evolve.agent.registry import get_agent_registry
            self._registry = get_agent_registry()
        return self._registry

    async def send_task(self, target: str, message: Message) -> Task:
        """Blocking: call target Agent's run() directly via thread pool."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            return self._error_task(target, f"Agent '{target}' not found in registry")

        task_text = self._extract_text(message)
        task = make_task_with_text(task_text)
        task.status.state = TaskState.working

        try:
            result = await asyncio.to_thread(agent_node.agent_core.run, task_text)
            task.status = TaskStatus(
                state=TaskState.completed,
                message=make_text_message("agent", result),
            )
        except Exception as e:
            task.status = TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", f"{type(e).__name__}: {e}"),
            )
        return task

    async def send_subscribe(self, target: str, message: Message) -> AsyncIterator[Task]:
        """Non-blocking: run in background, yield status updates via queue."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            yield self._error_task(target, f"Agent '{target}' not found in registry")
            return

        task_text = self._extract_text(message)
        task_id = _new_id()
        queue: asyncio.Queue[Task] = asyncio.Queue()

        # Submit initial state
        task = make_task_with_text(task_text, existing_task_id=task_id)
        await queue.put(task)

        # Background execution
        async def _run() -> None:
            try:
                result = await asyncio.to_thread(agent_node.agent_core.run, task_text)
                await queue.put(Task(
                    id=task_id,
                    status=TaskStatus(
                        state=TaskState.completed,
                        message=make_text_message("agent", result),
                    ),
                ))
            except Exception as e:
                await queue.put(Task(
                    id=task_id,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=make_text_message("agent", f"{type(e).__name__}: {e}"),
                    ),
                ))

        asyncio.ensure_future(_run())

        # Stream status updates
        while True:
            update = await queue.get()
            yield update
            if update.status.state in (TaskState.completed, TaskState.failed, TaskState.canceled):
                break

    async def get_task(self, target: str, task_id: str) -> Task:
        """Query task status from in-process task store."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            return self._error_task(target, f"Agent '{target}' not found")
        return agent_node.task_store.get(task_id, self._error_task(target, f"Task '{task_id}' not found"))

    async def cancel_task(self, target: str, task_id: str) -> Task:
        """Cancel a task — marks it as canceled in the task store."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            return self._error_task(target, f"Agent '{target}' not found")
        task = agent_node.task_store.get(task_id)
        if task is None:
            return self._error_task(target, f"Task '{task_id}' not found")
        task.status.state = TaskState.canceled
        return task

    async def get_agent_card(self, target: str) -> AgentCard:
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            return AgentCard(name=target, description=f"Agent '{target}' not found")
        return agent_node.card

    @staticmethod
    def _extract_text(message: Message) -> str:
        for part in message.parts:
            if part.type == "text" and part.text:
                return part.text
        return ""

    @staticmethod
    def _error_task(target: str, error: str) -> Task:
        return Task(
            status=TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", error),
            ),
            metadata={"target": target},
        )


class HttpTransport:
    """HTTP transport — aiohttp JSON-RPC calls to remote Agent servers."""

    def __init__(self) -> None:
        self._registry: Any = None

    def _get_registry(self) -> Any:
        if self._registry is None:
            from qd_evolve.agent.registry import get_agent_registry
            self._registry = get_agent_registry()
        return self._registry

    async def send_task(self, target: str, message: Message) -> Task:
        """Blocking: HTTP POST tasks/send."""
        import aiohttp

        url = self._get_registry().get_url(target)
        payload = self._rpc("tasks/send", {"message": message.model_dump()})
        async with aiohttp.ClientSession() as session:
            resp = await session.post(url, json=payload)
            data = await resp.json()
        return Task.model_validate(data.get("result", {}))

    async def send_subscribe(self, target: str, message: Message) -> AsyncIterator[Task]:
        """Non-blocking: HTTP POST tasks/sendSubscribe, SSE stream."""
        import aiohttp

        url = self._get_registry().get_url(target)
        payload = self._rpc("tasks/sendSubscribe", {"message": message.model_dump()})
        async with aiohttp.ClientSession() as session:
            resp = await session.post(url, json=payload)
            async for line in resp.content:
                if line.startswith(b"data:"):
                    yield Task.model_validate(json.loads(line[5:].strip()))

    async def get_task(self, target: str, task_id: str) -> Task:
        """Query task status via HTTP."""
        import aiohttp

        url = self._get_registry().get_url(target)
        payload = self._rpc("tasks/get", {"id": task_id})
        async with aiohttp.ClientSession() as session:
            resp = await session.post(url, json=payload)
            data = await resp.json()
        return Task.model_validate(data.get("result", {}))

    async def cancel_task(self, target: str, task_id: str) -> Task:
        """Cancel task via HTTP."""
        import aiohttp

        url = self._get_registry().get_url(target)
        payload = self._rpc("tasks/cancel", {"id": task_id})
        async with aiohttp.ClientSession() as session:
            resp = await session.post(url, json=payload)
            data = await resp.json()
        return Task.model_validate(data.get("result", {}))

    async def get_agent_card(self, target: str) -> AgentCard:
        """Discover remote Agent's capabilities."""
        import aiohttp

        url = self._get_registry().get_url(target)
        async with aiohttp.ClientSession() as session:
            resp = await session.get(f"{url}/.well-known/agent.json")
            data = await resp.json()
        return AgentCard.model_validate(data)

    @staticmethod
    def _rpc(method: str, params: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }


class TransportRouter:
    """Route A2A calls to inproc or http transport based on topology config."""

    def __init__(self, inproc: InprocTransport, http: HttpTransport) -> None:
        self._inproc = inproc
        self._http = http
        self._registry: Any = None

    def _get_registry(self) -> Any:
        if self._registry is None:
            from qd_evolve.agent.registry import get_agent_registry
            self._registry = get_agent_registry()
        return self._registry

    def _pick(self, target: str) -> InprocTransport | HttpTransport:
        transport = self._get_registry().get_transport(target)
        if transport == "inproc":
            return self._inproc
        return self._http

    async def send_task(self, target: str, message: Message) -> Task:
        return await self._pick(target).send_task(target, message)

    async def send_subscribe(self, target: str, message: Message) -> AsyncIterator[Task]:
        # Can't directly return from async generator — delegate
        transport = self._pick(target)
        async for update in transport.send_subscribe(target, message):
            yield update

    async def get_task(self, target: str, task_id: str) -> Task:
        return await self._pick(target).get_task(target, task_id)

    async def cancel_task(self, target: str, task_id: str) -> Task:
        return await self._pick(target).cancel_task(target, task_id)

    async def get_agent_card(self, target: str) -> AgentCard:
        return await self._pick(target).get_agent_card(target)


def _new_id() -> str:
    from uuid import uuid4
    return uuid4().hex