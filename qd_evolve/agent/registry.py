"""Agent registry — manages known agents and their topology."""

from __future__ import annotations

from typing import Any

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.core.config import Settings, load_settings
from qd_evolve.core.logger import logger


class Topology:
    """Agent topology — relationships and transport config from config.json."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or load_settings()
        a = settings.agents_config
        t = a.topology
        self.default_mode: str = t.default_mode
        self.default_transport: str = t.default_transport
        self.transports: dict[str, str] = t.transports
        self.relations: list[dict[str, str]] = t.relations
        # Build agents map from settings.agents_config.agents (url derived from server config)
        self.agents: dict[str, dict[str, Any]] = {}
        for entry in a.agents:
            port = entry.server.get("port", 8001)
            self.agents[entry.name] = {"url": f"http://localhost:{port}"}

    def get_transport(self, from_agent: str, to_agent: str) -> str:
        """Get transport mode between two agents."""
        key = f"{from_agent}→{to_agent}"
        if key in self.transports:
            return self.transports[key]
        # Auto-detect: different hosts → http
        from_url = self.agents.get(from_agent, {}).get("url", "")
        to_url = self.agents.get(to_agent, {}).get("url", "")
        if from_url and to_url:
            from_host = from_url.split("//", 1)[-1].split("/")[0].split(":")[0]
            to_host = to_url.split("//", 1)[-1].split("/")[0].split(":")[0]
            if from_host != to_host:
                return "http"
        return self.default_transport

    def get_relation(self, from_agent: str, to_agent: str) -> str:
        """Get relationship mode between two agents."""
        for r in self.relations:
            if r["from"] == from_agent and r["to"] == to_agent:
                return r.get("mode", self.default_mode)
        return self.default_mode


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
        return self.topology.agents.get(name, {}).get("url", "http://localhost:8001")

    def get_transport(self, target: str) -> str:
        """Get transport mode for reaching a target agent."""
        from_agent = self.current_agent or next(iter(self._agents), "")
        if from_agent:
            return self.topology.get_transport(from_agent, target)
        return self.topology.default_transport

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