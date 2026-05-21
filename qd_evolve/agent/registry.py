"""Agent registry — local in-process agent lookup."""

from __future__ import annotations

from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.human_agent import HumanAgent

DEFAULT_SERVER_PORT = 8000

# ── Singleton ──────────────────────────────────────────────────────
_registry: AgentRegistry | None = None


def set_agent_registry(reg: AgentRegistry) -> None:
    global _registry
    _registry = reg


def get_agent_registry() -> AgentRegistry:
    if _registry is None:
        raise RuntimeError("AgentRegistry not initialized")
    return _registry


class Topology:
    """Agent relationship topology."""

    def __init__(self, settings=None):
        if settings is not None:
            self.relations = [
                dict(r) for r in settings.agents_config.topology.relations
            ]
            self.agents: dict[str, dict] = {}
            for a in settings.agents_config.agents:
                self.agents[a.name] = {
                    "name": a.name,
                    "description": a.description,
                    "url": f"{a.server.host}:{a.server.port}",
                }
        else:
            self.relations = []
            self.agents = {}

    def get(self, name: str) -> dict | None:
        """Get relation by agent name."""
        for r in self.relations:
            if r["from"] == name or r["to"] == name:
                return r
        return None


class AgentRegistry:
    """Registry of local in-process agents."""

    def __init__(self, topology: Topology | None = None, current_agent: str = "") -> None:
        self._agents: dict[str, A2AAgent | HumanAgent] = {}
        self.topology = topology or Topology()
        self._current_agent = current_agent

    def register(self, agent: A2AAgent | HumanAgent) -> None:
        """Register a local agent."""
        name = agent.card.name
        self._agents[name] = agent

    def get(self, name: str) -> A2AAgent | HumanAgent | None:
        """Look up agent by name in local registry."""
        return self._agents.get(name)

    def get_url(self, name: str) -> str | None:
        """Look up agent URL by name in local registry."""
        agent = self._agents.get(name)
        if agent is not None and hasattr(agent, "card"):
            return f"http://{agent.card.url}"
        # Try topology
        info = self.topology.agents.get(name)
        if info:
            return f"http://{info['url']}"
        return None

    @property
    def current_agent(self) -> str:
        return self._current_agent

    @current_agent.setter
    def current_agent(self, name: str) -> None:
        self._current_agent = name

    @property
    def agents(self) -> dict[str, A2AAgent | HumanAgent]:
        return self._agents

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __len__(self) -> int:
        return len(self._agents)
