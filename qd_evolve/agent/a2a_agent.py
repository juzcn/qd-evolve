"""A2A Agent — extends Agent with A2A protocol identity and event subscriber fan-out."""

from __future__ import annotations

import asyncio
from typing import Any

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.agent import Agent
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.logger import logger


class A2AAgent(Agent):
    """Agent with A2A identity (AgentCard, TaskStore) and event subscriber fan-out.

    Extends Agent by:
    - Adding card (AgentCard) and task_store (TaskStore) for A2A identity
    - Adding _event_subscribers for multi-subscriber event fan-out
    - Overriding _update_status / _print to also push events to subscribers
    - Adding subscribe_events / unsubscribe_events for A2A observability
    """

    def __init__(self, card: AgentCard, task_store: TaskStore | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.card = card
        self.task_store = task_store or TaskStore()
        self._event_subscribers: list[asyncio.Queue] = []

        # Hook our _push_event into the base Agent's event callback
        self._on_event = self._push_event

    # ── Event subscriber mechanism (A2A observability) ──────────────

    def subscribe_events(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._event_subscribers.append(q)
        return q

    def unsubscribe_events(self, q: asyncio.Queue) -> None:
        if q in self._event_subscribers:
            self._event_subscribers.remove(q)

    # Backward compat aliases
    def subscribe_heartbeat(self) -> asyncio.Queue:
        return self.subscribe_events()

    def unsubscribe_heartbeat(self, q: asyncio.Queue) -> None:
        self.unsubscribe_events(q)

    def _push_event(self, event: dict) -> None:
        for q in self._event_subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                pass
