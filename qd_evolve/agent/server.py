"""A2A HTTP server — aiohttp JSON-RPC endpoint for cross-machine Agent communication."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web

from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    AgentExtension,
    Message,
    Part,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.core.config import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
from qd_evolve.core.logger import logger


class TaskStore:
    """In-memory task state store for the A2A server."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def put(self, task: Task) -> None:
        self._tasks[task.id] = task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def update_state(self, task_id: str, state: TaskState, message: Message | None = None) -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = TaskStatus(state=state, message=message)
        return task


class A2AServer:
    """A2A JSON-RPC server — one per Agent process.

    Handles:
      - POST /              → JSON-RPC (message/send, message/stream, tasks/get, tasks/cancel, tasks/resubscribe)
      - GET  /.well-known/agent.json → AgentCard discovery
    """

    def __init__(self, a2a_agent: Any, *, on_task_completed: Any = None) -> None:
        """Accept an A2AAgent instance (composition wrapper around Agent).

        The A2AAgent provides: .run(), .subscribe_events(), .unsubscribe_events(),
        .card, .task_store, and all delegated Agent attributes.

        Args:
            on_task_completed: Optional async callback(event_dict) invoked when
                a webhook callback (tasks/pushNotification) completes a task.
                Used by CLI to display results.
        """
        self.agent_core = a2a_agent
        self.card = a2a_agent.card
        self.task_store = a2a_agent.task_store
        self._on_task_completed = on_task_completed

    async def start(self, host: str = DEFAULT_SERVER_HOST, port: int = DEFAULT_SERVER_PORT) -> None:
        app = web.Application()
        app.router.add_get("/.well-known/agent.json", self._handle_agent_card)
        app.router.add_post("/", self._handle_rpc)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info("A2A server: listening on %s:%d", host, port)

    async def _handle_agent_card(self, request: web.Request) -> web.Response:
        card = self.card.model_dump()
        card["capabilities"]["extended_agent_card"] = True
        return web.json_response(card)

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(self._error(-32700, "Parse error"), status=400)

        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")

        try:
            if method == "message/send":
                result = await self._message_send(params)
            elif method == "message/stream":
                return await self._message_stream(params, request, req_id)
            elif method == "tasks/get":
                result = await self._tasks_get(params)
            elif method == "tasks/cancel":
                result = await self._tasks_cancel(params)
            elif method == "tasks/resubscribe":
                return await self._tasks_resubscribe(params, request, req_id)
            elif method == "tasks/pushNotification":
                result = await self._tasks_push_notification(params)
            elif method == "agent/getExtendedAgentCard":
                result = self._get_extended_agent_card()
            else:
                return web.json_response(self._error(-32601, "Method not found", req_id))

        except Exception as e:
            logger.exception("A2A server: RPC error: %s", e)
            return web.json_response(self._error(-32603, str(e), req_id))

        return web.json_response({"jsonrpc": "2.0", "result": result.model_dump(), "id": req_id})

    async def _message_send(self, params: dict) -> Task:
        """Create task, run agent, return completed task.

        For human agents: return Task(input_required) immediately —
        human responds asynchronously via complete_task().
        """
        message_data = params.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

        # Human agent: async mode — return input_required, don't block
        from qd_evolve.agent.human_agent import HumanAgent
        if isinstance(self.agent_core, HumanAgent):
            callback_url = ""
            if message.metadata:
                callback_url = message.metadata.get("callback_url", "")
            task = make_task_with_text(task_text)
            self.agent_core.receive_task(
                task_id=task.id,
                content=task_text,
                callback_url=callback_url,
            )
            # receive_task stores the task; retrieve it
            return self.task_store.get(task.id) or task

        # AI agent: blocking run
        task = make_task_with_text(task_text)
        task.status.state = TaskState.working
        self.task_store.put(task)

        try:
            result = await asyncio.to_thread(self.agent_core.run, task_text)
            task.status = TaskStatus(
                state=TaskState.completed,
                message=make_text_message("agent", result),
            )
        except Exception as e:
            logger.exception("A2A server: agent run failed: %s", e)
            task.status = TaskStatus(
                state=TaskState.failed,
                message=make_text_message("agent", f"{type(e).__name__}: {e}"),
            )
        self.task_store.put(task)
        return task

    async def _message_stream(self, params: dict, request: web.Request, req_id: Any) -> web.StreamResponse:
        """SSE stream: send message, stream TaskStatusUpdateEvent with intermediate events in metadata."""
        message_data = params.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

        task = make_task_with_text(task_text)
        self.task_store.put(task)

        resp = web.StreamResponse()
        resp.content_type = "text/event-stream"
        resp.charset = "utf-8"
        await resp.prepare(request)

        # First event: Task object
        sr = StreamResponse(task=task)
        await resp.write(f"data: {json.dumps({'jsonrpc': '2.0', 'result': sr.model_dump(), 'id': req_id}, ensure_ascii=False)}\n\n".encode())

        # Human agent: return input_required immediately, no blocking run
        from qd_evolve.agent.human_agent import HumanAgent
        if isinstance(self.agent_core, HumanAgent):
            callback_url = ""
            if message.metadata:
                callback_url = message.metadata.get("callback_url", "")
            self.agent_core.receive_task(task_id=task.id, content=task_text, callback_url=callback_url)
            task = self.task_store.get(task.id) or task
            sr = StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                task_id=task.id,
                context_id=task.session_id,
                status=TaskStatus(state=TaskState.input_required, message=make_text_message("agent", "Waiting for human input")),
                final=True,
            ))
            await resp.write(f"data: {json.dumps({'jsonrpc': '2.0', 'result': sr.model_dump(), 'id': req_id}, ensure_ascii=False)}\n\n".encode())
            await resp.write_eof()
            return resp

        # Subscribe to agent events for intermediate updates
        event_queue = self.agent_core.subscribe_events()

        # Background execution
        run_task = asyncio.ensure_future(asyncio.to_thread(self.agent_core.run, task_text))

        try:
            while not run_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    # Push intermediate events as TaskStatusUpdateEvent with metadata
                    sr = StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.session_id,
                        status=TaskStatus(state=TaskState.working),
                        metadata=event,
                    ))
                    await resp.write(f"data: {json.dumps({'jsonrpc': '2.0', 'result': sr.model_dump(), 'id': req_id}, ensure_ascii=False)}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionError, asyncio.CancelledError):
            pass

        # Get result
        try:
            result = run_task.result()
        except Exception as e:
            logger.exception("A2A server: agent run failed: %s", e)
            result = f"{type(e).__name__}: {e}"

        # Final event: completed TaskStatusUpdateEvent
        final_state = TaskState.completed if not run_task.exception() else TaskState.failed
        sr = StreamResponse(statusUpdate=TaskStatusUpdateEvent(
            task_id=task.id,
            context_id=task.session_id,
            status=TaskStatus(state=final_state, message=make_text_message("agent", result)),
            final=True,
        ))
        await resp.write(f"data: {json.dumps({'jsonrpc': '2.0', 'result': sr.model_dump(), 'id': req_id}, ensure_ascii=False)}\n\n".encode())

        task.status = TaskStatus(state=final_state, message=make_text_message("agent", result))
        self.task_store.put(task)

        self.agent_core.unsubscribe_events(event_queue)
        await resp.write_eof()
        return resp

    async def _tasks_get(self, params: dict) -> Task:
        task_id = params.get("id", "")
        task = self.task_store.get(task_id)
        if task is None:
            return Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", f"Task '{task_id}' not found")))
        return task

    async def _tasks_cancel(self, params: dict) -> Task:
        task_id = params.get("id", "")
        task = self.task_store.update_state(task_id, TaskState.canceled)
        if task is None:
            return Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", f"Task '{task_id}' not found")))
        return task

    async def _tasks_push_notification(self, params: dict) -> Task:
        """Webhook callback: receive completed task from remote agent.

        Updates local task store and pushes event so CLI/subscribers
        see the result. This is how human agent callbacks reach the
        calling AI agent.
        """
        task_data = params.get("task", {})
        if not task_data:
            return Task(status=TaskStatus(state=TaskState.failed, message=make_text_message("agent", "No task data in pushNotification")))
        task = Task.model_validate(task_data)
        self.task_store.put(task)
        logger.info("A2A server: pushNotification received for task '%s' (state=%s)", task.id, task.status.state)
        # Push event so CLI and subscribers see the result
        event = {
            "type": "task_completed",
            "task_id": task.id,
            "content": self._extract_text(task.status.message) if task.status.message else "",
        }
        self.agent_core._push_event(event)
        if self._on_task_completed:
            try:
                await self._on_task_completed(event)
            except Exception:
                pass
        return task

    @staticmethod
    def _extract_text(message: Message) -> str:
        for part in message.parts:
            if part.type == "text" and part.text:
                return part.text
        return ""

    async def _tasks_resubscribe(self, params: dict, request: web.Request, req_id: Any) -> web.StreamResponse:
        """SSE stream: subscribe to agent events for an existing or new task."""
        task_id = params.get("taskId", params.get("id", ""))
        task = self.task_store.get(task_id) if task_id else None

        resp = web.StreamResponse()
        resp.content_type = "text/event-stream"
        resp.charset = "utf-8"
        await resp.prepare(request)

        # If task exists, send it first
        if task:
            sr = StreamResponse(task=task)
            await resp.write(f"data: {json.dumps({'jsonrpc': '2.0', 'result': sr.model_dump(), 'id': req_id}, ensure_ascii=False)}\n\n".encode())

        # Subscribe to agent events
        queue = self.agent_core.subscribe_events()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    sr = StreamResponse(statusUpdate=TaskStatusUpdateEvent(
                        task_id=task_id or "",
                        context_id=(task.session_id if task else ""),
                        status=TaskStatus(state=TaskState.working),
                        metadata=event,
                    ))
                    await resp.write(f"data: {json.dumps({'jsonrpc': '2.0', 'result': sr.model_dump(), 'id': req_id}, ensure_ascii=False)}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.agent_core.unsubscribe_events(queue)
        return resp

    def _get_extended_agent_card(self) -> AgentCard:
        """Return extended AgentCard with runtime status in extensions."""
        a = self.agent_core
        # Human agent: minimal status, no provider/model/tools
        from qd_evolve.agent.human_agent import HumanAgent
        if isinstance(a, HumanAgent):
            status_ext = AgentExtension(
                uri="x-qd-evolve-status",
                description="Runtime status of the agent",
                params={"type": "human"},
            )
            card = self.card.model_copy(update={
                "capabilities": AgentCapabilities(
                    streaming=self.card.capabilities.streaming,
                    push_notifications=True,
                    extended_agent_card=True,
                ),
                "extensions": [status_ext],
            })
            return card
        status_ext = AgentExtension(
            uri="x-qd-evolve-status",
            description="Runtime status of the agent",
            params={
                "provider": a._provider_name or "",
                "model": a._model or "",
                "preload_tools": sorted(a._always_active),
                "loaded_tools": sorted(a._active_tools - a._always_active),
                "preload_skills": sorted(a._preload_skills),
                "loaded_skills": sorted(s for s in a._loaded_skill_names if s not in a._preload_skills),
                "preload_cli": sorted(a._preload_cli),
                "loaded_cli": sorted(c for c in a._loaded_cli_names if c not in a._preload_cli),
            },
        )
        card = self.card.model_copy(update={
            "capabilities": AgentCapabilities(
                streaming=self.card.capabilities.streaming,
                push_notifications=self.card.capabilities.push_notifications,
                extended_agent_card=True,
            ),
            "extensions": [status_ext],
        })
        return card

    @staticmethod
    def _error(code: int, message: str, req_id: Any = None) -> dict:
        return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}