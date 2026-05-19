"""AgentProtocol — minimal interface for any A2A participant."""

from __future__ import annotations

import asyncio
from typing import Protocol

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.server import TaskStore


class AgentProtocol(Protocol):
    """Minimal interface for an A2A participant — AI agent or human.

    InprocTransport, A2AServer, and AgentRegistry depend only on this
    interface.  Concrete implementations: A2AAgent (wraps Agent), HumanAgent.
    """

    card: AgentCard
    task_store: TaskStore

    def run(self, message: str, **kwargs) -> str:
        """Process a message and return a response.

        For AI agents: calls LLM, executes tools, returns final text.
        For human agents: returns immediately with placeholder (use send_task instead).
        """
        ...

    def subscribe_events(self) -> asyncio.Queue:
        """Subscribe to agent events (iteration, status, heartbeat, etc.)."""
        ...

    def unsubscribe_events(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from agent events."""
        ...
