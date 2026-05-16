"""AgentRegistry — manages all Agent instances, topology, and transport routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agents.agent import Agent
from qd_evolve.core.logger import logger


class Topology:
    """Agent relationship topology loaded from topology.json."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.default_mode: str = "peer"
        self.default_transport: str = "inproc"
        self.agents: dict[str, dict[str, str]] = {}  # name → {url, ...}
        self.transports: dict[str, str] = {}  # "A→B" → "inproc" | "http"
        self.relations: list[dict[str, str]] = []

        if path:
            self.load(path)

    def load(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            logger.debug("Topology: no topology file at %s", p)
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        self.default_mode = data.get("default_mode", "peer")
        self.default_transport = data.get("default_transport", "inproc")
        self.agents = data.get("agents", {})
        self.transports = data.get("transports", {})
        self.relations = data.get("relations", [])
        logger.info("Topology: loaded from %s — %d agents, %d relations", p, len(self.agents), len(self.relations))

    def get_transport(self, from_agent: str, to_agent: str) -> str:
        """Determine transport for a given agent pair."""
        key = f"{from_agent}→{to_agent}"
        if key in self.transports:
            return self.transports[key]
        # Auto-detect: different hosts → http
        from_url = self.agents.get(from_agent, {}).get("url", "")
        to_url = self.agents.get(to_agent, {}).get("url", "")
        if from_url and to_url:
            from_host = _extract_host(from_url)
            to_host = _extract_host(to_url)
            if from_host != to_host:
                return "http"
        return self.default_transport

    def get_mode(self, from_agent: str, to_agent: str) -> str:
        """Determine relationship mode for a given agent pair."""
        for rel in self.relations:
            if rel["from"] == from_agent and rel["to"] == to_agent:
                return rel.get("mode", self.default_mode)
        return self.default_mode

    def get_peers(self, agent_name: str) -> list[str]:
        """Get agents that have peer relationship with the given agent."""
        peers = []
        for rel in self.relations:
            if rel.get("mode", self.default_mode) == "peer":
                if rel["from"] == agent_name:
                    peers.append(rel["to"])
                elif rel["to"] == agent_name:
                    peers.append(rel["from"])
        return peers

    def get_workers(self, agent_name: str) -> list[str]:
        """Get workers for a master agent."""
        return [
            rel["to"] for rel in self.relations
            if rel["from"] == agent_name and rel.get("mode") == "master-worker"
        ]

    def get_master(self, agent_name: str) -> str | None:
        """Get master for a worker agent."""
        for rel in self.relations:
            if rel["to"] == agent_name and rel.get("mode") == "master-worker":
                return rel["from"]
        return None


class AgentRegistry:
    """Registry of all Agent instances — manages identity, URLs, and topology."""

    def __init__(self, topology: Topology | None = None) -> None:
        self._agents: dict[str, Agent] = {}
        self.topology = topology or Topology()

    def register(self, agent: Agent) -> None:
        self._agents[agent.card.name] = agent
        logger.info("AgentRegistry: registered agent '%s'", agent.card.name)

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def get_url(self, name: str) -> str:
        """Get HTTP URL for an agent (from topology or AgentCard)."""
        agent = self._agents.get(name)
        if agent and agent.card.url:
            return agent.card.url
        return self.topology.agents.get(name, {}).get("url", f"http://localhost:8001")

    def get_transport(self, target: str) -> str:
        """Get transport mode for reaching a target agent."""
        # Find the "current" agent — first registered, or use topology
        current = next(iter(self._agents), None)
        if current:
            return self.topology.get_transport(current, target)
        return self.topology.default_transport

    def list_cards(self) -> list[AgentCard]:
        return [a.card for a in self._agents.values()]

    def list_names(self) -> list[str]:
        return list(self._agents.keys())

    def get_peers(self, agent_name: str) -> list[Agent]:
        peer_names = self.topology.get_peers(agent_name)
        return [self._agents[n] for n in peer_names if n in self._agents]

    def get_workers(self, agent_name: str) -> list[Agent]:
        worker_names = self.topology.get_workers(agent_name)
        return [self._agents[n] for n in worker_names if n in self._agents]

    def get_master(self, agent_name: str) -> Agent | None:
        master_name = self.topology.get_master(agent_name)
        if master_name and master_name in self._agents:
            return self._agents[master_name]
        return None


# ── Module-level singleton ──────────────────────────────────────

_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry


def set_agent_registry(registry: AgentRegistry) -> None:
    global _registry
    _registry = registry


def _extract_host(url: str) -> str:
    """Extract host from URL for cross-machine detection."""
    try:
        # Simple extraction: http://host:port → host
        parts = url.split("//", 1)[-1].split("/")[0].split(":")
        return parts[0]
    except (IndexError, ValueError):
        return url