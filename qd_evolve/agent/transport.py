"""A2A transport layer — inproc (direct call) and http (JSON-RPC) transports."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Protocol, runtime_checkable

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    Part,
    StreamResponse,
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
    async def send_stream(self, target: str, message: Message) -> AsyncIterator[StreamResponse]: ...
    async def get_task(self, target: str, task_id: str) -> Task: ...
    async def cancel_task(self, target: str, task_id: str) -> Task: ...
    async def get_agent_card(self, target: str) -> AgentCard: ...
    async def get_extended_agent_card(self, target: str) -> AgentCard: ...
    async def resubscribe(self, target: str, task_id: str = "") -> AsyncIterator[StreamResponse]: ...


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
            # Try to lazy-load the agent from config
            agent_node = self._lazy_load(target, registry)
            if agent_node is None:
                return self._error_task(target, f"Agent '{target}' not found in registry")

        task_text = self._extract_text(message)
        task = make_task_with_text(task_text)
        task.status.state = TaskState.working

        try:
            result = await asyncio.to_thread(agent_node.run, task_text)
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

    async def send_stream(self, target: str, message: Message) -> AsyncIterator[StreamResponse]:
        """SSE stream: run agent, yield StreamResponse events with intermediate updates in metadata."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            agent_node = self._lazy_load(target, registry)
            if agent_node is None:
                yield StreamResponse(task=self._error_task(target, f"Agent '{target}' not found in registry"))
                return

        task_text = self._extract_text(message)
        task = make_task_with_text(task_text)
        task.status.state = TaskState.working

        # First event: Task
        yield StreamResponse(task=task)

        # Subscribe to agent events
        event_queue = agent_node.subscribe_events()

        # Background execution
        run_task = asyncio.ensure_future(asyncio.to_thread(agent_node.run, task_text))

        try:
            while not run_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    from qd_evolve.agent.a2a import TaskStatusUpdateEvent
                    yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.session_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata=event,
                    ))
                except asyncio.TimeoutError:
                    yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.session_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata={"type": "ping"},
                    ))
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            agent_node.unsubscribe_events(event_queue)

        # Final event
        try:
            result = run_task.result()
            final_state = TaskState.completed
        except Exception as e:
            result = f"{type(e).__name__}: {e}"
            final_state = TaskState.failed

        from qd_evolve.agent.a2a import TaskStatusUpdateEvent
        yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
            task_id=task.id,
            context_id=task.session_id,
            status=TaskStatus(state=final_state, message=make_text_message("agent", result)),
            final=True,
        ))

    async def get_task(self, target: str, task_id: str) -> Task:
        """Query task status from in-process task store."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            agent_node = self._lazy_load(target, registry)
            if agent_node is None:
                return self._error_task(target, f"Agent '{target}' not found")
        return agent_node.task_store.get(task_id, self._error_task(target, f"Task '{task_id}' not found"))

    async def cancel_task(self, target: str, task_id: str) -> Task:
        """Cancel a task — marks it as canceled in the task store."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            agent_node = self._lazy_load(target, registry)
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
            agent_node = self._lazy_load(target, registry)
            if agent_node is None:
                return AgentCard(name=target, description=f"Agent '{target}' not found")
        return agent_node.card

    async def get_extended_agent_card(self, target: str) -> AgentCard:
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            agent_node = self._lazy_load(target, registry)
            if agent_node is None:
                return AgentCard(name=target, description=f"Agent '{target}' not found")
        from qd_evolve.agent.a2a import AgentCapabilities, AgentExtension
        from qd_evolve.agent.server import A2AServer
        server = A2AServer(agent_node)
        return server._get_extended_agent_card()

    async def resubscribe(self, target: str, task_id: str = "") -> AsyncIterator[StreamResponse]:
        """Subscribe to agent events from a local agent via asyncio.Queue."""
        registry = self._get_registry()
        agent_node = registry.get(target)
        if agent_node is None:
            agent_node = self._lazy_load(target, registry)
            if agent_node is None:
                return

        queue = agent_node.subscribe_events()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    from qd_evolve.agent.a2a import TaskStatusUpdateEvent
                    yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata=event,
                    ))
                except asyncio.TimeoutError:
                    yield StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata={"type": "ping"},
                    ))
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            agent_node.unsubscribe_events(queue)

    def _lazy_load(self, target: str, registry: Any) -> Any | None:
        """Try to create and register an Agent from config on demand."""
        try:
            from qd_evolve.agent.loader import create_agent
            from qd_evolve.core.config import load_settings
            agent_core = create_agent(target, load_settings())
            registry.register(agent_core)
            logger.info("InprocTransport: lazy-loaded agent '%s'", target)
            return agent_core
        except Exception as e:
            logger.warning("InprocTransport: failed to lazy-load agent '%s': %s", target, e)
            return None

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

    def _get_callback_url(self) -> str:
        """Return this agent's own server URL for webhook callbacks."""
        registry = self._get_registry()
        if registry.current_agent:
            return registry.get_url(registry.current_agent)
        return ""

    async def send_task(self, target: str, message: Message) -> Task:
        """Blocking: HTTP POST message/send."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            # Include callback_url so remote agent can push results via webhook
            msg_dict = message.model_dump()
            callback_url = self._get_callback_url()
            if callback_url and "metadata" not in msg_dict:
                msg_dict["metadata"] = {}
            if callback_url:
                msg_dict["metadata"]["callback_url"] = callback_url
            payload = self._rpc("message/send", {"message": msg_dict})
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=payload)
                data = await resp.json()
            return Task.model_validate(data.get("result", {}))
        except Exception as e:
            logger.error("HttpTransport: send_task to '%s' failed: %s", target, e)
            return self._error_task(target, f"{type(e).__name__}: {e}")

    async def send_stream(self, target: str, message: Message) -> AsyncIterator[StreamResponse]:
        """SSE stream: HTTP POST message/stream, parse StreamResponse."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            msg_dict = message.model_dump()
            callback_url = self._get_callback_url()
            if callback_url and "metadata" not in msg_dict:
                msg_dict["metadata"] = {}
            if callback_url:
                msg_dict["metadata"]["callback_url"] = callback_url
            payload = self._rpc("message/stream", {"message": msg_dict})
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    async for line in resp.content:
                        if line.startswith(b"data:"):
                            try:
                                rpc_data = json.loads(line[5:].strip())
                                result = rpc_data.get("result", {})
                                yield StreamResponse.model_validate(result)
                            except (json.JSONDecodeError, Exception):
                                pass
        except Exception as e:
            logger.error("HttpTransport: send_stream to '%s' failed: %s", target, e)
            yield StreamResponse(task=self._error_task(target, f"{type(e).__name__}: {e}"))

    async def get_task(self, target: str, task_id: str) -> Task:
        """Query task status via HTTP."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            payload = self._rpc("tasks/get", {"id": task_id})
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=payload)
                data = await resp.json()
            return Task.model_validate(data.get("result", {}))
        except Exception as e:
            logger.error("HttpTransport: get_task from '%s' failed: %s", target, e)
            return self._error_task(target, f"{type(e).__name__}: {e}")

    async def cancel_task(self, target: str, task_id: str) -> Task:
        """Cancel task via HTTP."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            payload = self._rpc("tasks/cancel", {"id": task_id})
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=payload)
                data = await resp.json()
            return Task.model_validate(data.get("result", {}))
        except Exception as e:
            logger.error("HttpTransport: cancel_task on '%s' failed: %s", target, e)
            return self._error_task(target, f"{type(e).__name__}: {e}")

    async def get_agent_card(self, target: str) -> AgentCard:
        """Discover remote Agent's capabilities."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            async with aiohttp.ClientSession() as session:
                resp = await session.get(f"{url}/.well-known/agent.json")
                data = await resp.json()
            return AgentCard.model_validate(data)
        except Exception as e:
            logger.error("HttpTransport: get_agent_card for '%s' failed: %s", target, e)
            return AgentCard(name=target, description=f"Error: {type(e).__name__}: {e}")

    async def get_extended_agent_card(self, target: str) -> AgentCard:
        """Get extended AgentCard with runtime status from remote agent."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            payload = self._rpc("agent/getExtendedAgentCard", {})
            async with aiohttp.ClientSession() as session:
                resp = await session.post(url, json=payload)
                data = await resp.json()
            return AgentCard.model_validate(data.get("result", {}))
        except Exception as e:
            logger.error("HttpTransport: get_extended_agent_card for '%s' failed: %s", target, e)
            return AgentCard(name=target, description=f"Error: {type(e).__name__}: {e}")

    async def is_online(self, target: str) -> bool:
        """Check if a remote agent server is reachable via /.well-known/agent.json."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                async with session.get(f"{url}/.well-known/agent.json") as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def resubscribe(self, target: str, task_id: str = "") -> AsyncIterator[StreamResponse]:
        """SSE stream: HTTP POST tasks/resubscribe, parse StreamResponse."""
        import aiohttp

        try:
            url = self._get_registry().get_url(target)
            payload = self._rpc("tasks/resubscribe", {"taskId": task_id})
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    async for line in resp.content:
                        if line.startswith(b"data:"):
                            try:
                                rpc_data = json.loads(line[5:].strip())
                                result = rpc_data.get("result", {})
                                yield StreamResponse.model_validate(result)
                            except (json.JSONDecodeError, Exception):
                                pass
        except Exception as e:
            logger.debug("HttpTransport: resubscribe for '%s' failed: %s", target, e)

    @staticmethod
    def _error_task(target: str, error: str) -> Task:
        return Task(
            status=TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", error),
            ),
            metadata={"target": target},
        )

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

    def __init__(self, inproc: InprocTransport | None, http: HttpTransport) -> None:
        self._inproc = inproc
        self._http = http
        self._registry: Any = None

    def _get_registry(self) -> Any:
        if self._registry is None:
            from qd_evolve.agent.registry import get_agent_registry
            self._registry = get_agent_registry()
        return self._registry

    def _pick(self, target: str) -> InprocTransport | HttpTransport:
        if self._inproc is None:
            return self._http
        # If the agent is registered locally (in-process), use inproc; otherwise http
        if self._get_registry().get(target) is not None:
            return self._inproc
        return self._http

    async def send_task(self, target: str, message: Message) -> Task:
        return await self._pick(target).send_task(target, message)

    async def send_stream(self, target: str, message: Message) -> AsyncIterator[StreamResponse]:
        transport = self._pick(target)
        async for sr in transport.send_stream(target, message):
            yield sr

    async def get_task(self, target: str, task_id: str) -> Task:
        return await self._pick(target).get_task(target, task_id)

    async def cancel_task(self, target: str, task_id: str) -> Task:
        return await self._pick(target).cancel_task(target, task_id)

    async def get_agent_card(self, target: str) -> AgentCard:
        return await self._pick(target).get_agent_card(target)

    async def get_extended_agent_card(self, target: str) -> AgentCard:
        return await self._pick(target).get_extended_agent_card(target)

    async def resubscribe(self, target: str, task_id: str = "") -> AsyncIterator[StreamResponse]:
        transport = self._pick(target)
        async for sr in transport.resubscribe(target, task_id):
            yield sr

    async def is_online(self, target: str) -> bool:
        return await self._http.is_online(target)


def _new_id() -> str:
    from uuid import uuid4
    return uuid4().hex