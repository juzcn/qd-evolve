"""A2A HTTP server — aiohttp JSON-RPC endpoint for cross-machine Agent communication."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    Part,
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
      - POST /              → JSON-RPC (tasks/send, tasks/sendSubscribe, tasks/get, tasks/cancel)
      - GET  /.well-known/agent.json → AgentCard discovery
    """

    def __init__(self, agent_core: Any, card: AgentCard, task_store: TaskStore | None = None) -> None:
        self.agent_core = agent_core
        self.card = card
        self.task_store = task_store or TaskStore()

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
        return web.json_response(self.card.model_dump())

    async def _handle_rpc(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(self._error(-32700, "Parse error"), status=400)

        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")

        try:
            if method == "tasks/send":
                result = await self._tasks_send(params)
            elif method == "tasks/sendSubscribe":
                return await self._tasks_send_subscribe(params)
            elif method == "tasks/get":
                result = await self._tasks_get(params)
            elif method == "tasks/cancel":
                result = await self._tasks_cancel(params)
            else:
                return web.json_response(self._error(-32601, "Method not found", req_id))

        except Exception as e:
            logger.exception("A2A server: RPC error: %s", e)
            return web.json_response(self._error(-32603, str(e), req_id))

        return web.json_response({"jsonrpc": "2.0", "result": result.model_dump(), "id": req_id})

    async def _tasks_send(self, params: dict) -> Task:
        """Blocking: create task, run agent, return completed task."""
        message_data = params.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

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

    async def _tasks_send_subscribe(self, params: dict) -> web.StreamResponse:
        """Non-blocking: SSE stream of task status updates."""
        message_data = params.get("message", {})
        message = Message.model_validate(message_data) if message_data else make_text_message("user", "")
        task_text = self._extract_text(message)

        task = make_task_with_text(task_text)
        self.task_store.put(task)

        resp = web.StreamResponse()
        resp.content_type = "text/event-stream"
        resp.charset = "utf-8"
        await resp.prepare(self._request)

        # Submit working state
        event = TaskStatusUpdateEvent(id=task.id, status=TaskStatus(state=TaskState.working))
        await resp.write(f"data: {event.model_dump_json()}\n\n".encode())

        # Background execution
        try:
            result = await asyncio.to_thread(self.agent_core.run, task_text)
        except Exception as e:
            logger.exception("A2A server: agent run failed: %s", e)
            result = f"{type(e).__name__}: {e}"

        # Completed state
        completed = TaskStatusUpdateEvent(
            id=task.id,
            status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", result)),
            final=True,
        )
        await resp.write(f"data: {completed.model_dump_json()}\n\n".encode())

        task.status = TaskStatus(state=TaskState.completed, message=make_text_message("agent", result))
        self.task_store.put(task)
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

    @staticmethod
    def _extract_text(message: Message) -> str:
        for part in message.parts:
            if part.type == "text" and part.text:
                return part.text
        return ""

    @staticmethod
    def _error(code: int, message: str, req_id: Any = None) -> dict:
        return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}