"""HumanAgent — A2A agent backed by a human at a terminal.

Implements AgentProtocol directly (no Agent, no A2AAgent wrapper).
Human agents receive tasks asynchronously via send_task, return
input_required state immediately, and push completed results
via webhook callback when the human responds.
"""

from __future__ import annotations

import asyncio

from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
)
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.logger import logger


class HumanAgent:
    """A2A agent whose "inference engine" is a human at a terminal.

    Implements AgentProtocol: card, task_store, run(), subscribe_events().
    No system prompt, no tools, no memory, no LLM.
    Supports heartbeat — pushes periodic "online" events so other
    agents know the human is present.

    Communication pattern:
    - AI agent calls send_task("human", ...) → returns Task(input_required)
    - Human terminal receives task → displays to human
    - Human types response → complete_task() updates to Task(completed)
    - Webhook callback pushes result to calling agent
    """

    def __init__(
        self,
        name: str,
        description: str = "",
    ) -> None:
        self.card = AgentCard(
            name=name,
            description=description,
            capabilities=AgentCapabilities(
                streaming=True,
                push_notifications=True,
            ),
        )
        self.task_store = TaskStore()
        self._event_subscribers: list[asyncio.Queue] = []
        self._hb_task: asyncio.Task | None = None

    # ── AgentProtocol interface ──────────────────────────────────────

    def run(self, message: str, **kwargs) -> str:
        """For message/send — returns placeholder immediately.

        Human agents should be called via send_task (async), not
        delegate_to (blocking). This method returns a hint message.
        """
        self._push_event({"type": "human_task", "content": message})
        logger.info("HumanAgent [%s]: received message via run() — use send_task instead", self.card.name)
        return "[Human agent: use send_task for async interaction, not delegate_to]"

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subscribers:
            self._event_subscribers.remove(q)

    def start_heartbeat_loop(self, idle_seconds: int) -> None:
        """Start heartbeat loop — pushes periodic 'online' events."""
        if idle_seconds <= 0:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(idle_seconds)
                self._push_event({"type": "heartbeat_silent"})

        self._hb_task = asyncio.ensure_future(_loop())

    def stop_heartbeat_loop(self) -> None:
        if self._hb_task and not self._hb_task.done():
            self._hb_task.cancel()

    # ── Human-specific methods ───────────────────────────────────────

    def complete_task(self, task_id: str, response: str) -> None:
        """Called by CLI when human submits a response.

        Updates task state to completed and pushes event for
        webhook callback / SSE subscribers.
        """
        task = self.task_store.get(task_id)
        if task is None:
            logger.warning("HumanAgent [%s]: complete_task — task '%s' not found", self.card.name, task_id)
            return
        task.status = TaskStatus(
            state=TaskState.completed,
            message=make_text_message("agent", response),
        )
        self.task_store.put(task)
        logger.info("HumanAgent [%s]: task '%s' completed (%d chars)", self.card.name, task_id, len(response))
        self._push_event({
            "type": "task_completed",
            "task_id": task_id,
            "content": response,
        })
        # Fire webhook callback if callback_url stored in task metadata
        callback_url = task.metadata.get("callback_url", "")
        if callback_url:
            self._fire_webhook(task, callback_url)

    def receive_task(self, task_id: str, content: str, callback_url: str = "", from_agent: str = "") -> None:
        """Called by A2AServer when a new task arrives via message/send.

        Creates task in input_required state, stores callback URL,
        and pushes event so the human terminal loop sees it.
        """
        from qd_evolve.agent.a2a import make_task_with_text
        task = make_task_with_text(content, existing_task_id=task_id)
        task.status.state = TaskState.input_required
        task.metadata["callback_url"] = callback_url
        self.task_store.put(task)
        logger.info("HumanAgent [%s]: received task '%s' from '%s' (%d chars)", self.card.name, task_id, from_agent, len(content))
        self._push_event({
            "type": "human_task",
            "task_id": task_id,
            "content": content,
            "callback_url": callback_url,
            "from_agent": from_agent,
        })

    # ── Event fan-out (same pattern as A2AAgent) ────────────────────

    def _push_event(self, event: dict) -> None:
        for q in self._event_subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def _fire_webhook(self, task: Task, callback_url: str) -> None:
        """POST completed task result to calling agent's webhook URL."""
        import aiohttp
        payload = {
            "jsonrpc": "2.0",
            "method": "tasks/pushNotification",
            "params": {"task": task.model_dump()},
        }
        try:
            import asyncio
            async def _post() -> None:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(callback_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)):
                            pass
                except Exception:
                    logger.debug("HumanAgent [%s]: webhook POST failed for %s", self.card.name, callback_url, exc_info=True)
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(_post())
            except RuntimeError:
                asyncio.run(_post())
        except Exception:
            logger.warning("HumanAgent [%s]: webhook callback failed for %s", self.card.name, callback_url, exc_info=True)