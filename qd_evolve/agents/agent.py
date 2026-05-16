"""Agent class — one fully isolated Agent instance with A2A AgentCard."""

from __future__ import annotations

from typing import Any

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.memory import MemoryStore
from qd_evolve.core.registry import ToolRegistry


class Agent:
    """A fully isolated Agent instance.

    Each Agent has:
    - card: A2A AgentCard (identity + capabilities)
    - agent_core: AgentCore instance (independent messages, system prompt)
    - memory: independent MemoryStore (independent db file)
    - tool_registry: shared ToolRegistry reference
    - task_store: A2A TaskStore for in-process task tracking
    """

    def __init__(
        self,
        card: AgentCard,
        agent_core: Any,
        memory: MemoryStore | None = None,
        tool_registry: ToolRegistry | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        self.card = card
        self.agent_core = agent_core
        self.memory = memory
        self.tool_registry = tool_registry
        self.task_store = task_store or TaskStore()