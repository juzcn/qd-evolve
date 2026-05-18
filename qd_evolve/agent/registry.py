"""Agent registry — manages known agents and their topology."""

from __future__ import annotations

from typing import Any

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.core.config import DEFAULT_SERVER_PORT, Settings, load_settings
from qd_evolve.core.logger import logger


class Topology:
    """Agent topology — relationships between agents."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or load_settings()
        self.relations: list[dict[str, str]] = settings.agents_config.topology.relations
        # Build agents map: name → {url}
        self.agents: dict[str, dict[str, Any]] = {}
        for entry in settings.agents_config.agents:
            self.agents[entry.name] = {
                "url": f"http://localhost:{entry.server.port}",
            }

    def get_relation(self, from_agent: str, to_agent: str) -> str:
        """Get relationship mode between two agents. Default: peer."""
        for r in self.relations:
            if r["from"] == from_agent and r["to"] == to_agent:
                return r.get("mode", "peer")
        return "peer"


class AgentRegistry:
    """Registry of all Agent instances — manages identity, URLs, and topology."""

    def __init__(self, topology: Topology | None = None, current_agent: str = "") -> None:
        self._agents: dict[str, Any] = {}
        self.topology = topology or Topology()
        self.current_agent = current_agent

    def register(self, agent: Any) -> None:
        """Register an Agent instance."""
        self._agents[agent.card.name] = agent
        logger.debug("Registry: registered agent '%s'", agent.card.name)

    def get(self, name: str) -> Any | None:
        """Get an Agent by name."""
        return self._agents.get(name)

    def list_names(self) -> list[str]:
        """List all registered agent names."""
        return list(self._agents.keys())

    def get_url(self, name: str) -> str:
        """Get URL for an agent from topology."""
        return self.topology.agents.get(name, {}).get("url", f"http://localhost:{DEFAULT_SERVER_PORT}")

    def get_card(self, name: str) -> AgentCard | None:
        """Get AgentCard for a named agent."""
        a = self.get(name)
        return a.card if a else None


# Module-level singleton
_registry: AgentRegistry | None = None


def set_agent_registry(registry: AgentRegistry) -> None:
    global _registry
    _registry = registry


def get_agent_registry() -> AgentRegistry:
    if _registry is None:
        set_agent_registry(AgentRegistry())
    return _registry