"""Tests for qd_evolve.agent.registry — AgentRegistry, Topology."""

from unittest.mock import MagicMock

import pytest

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.registry import AgentRegistry, Topology
from qd_evolve.core.config import (
    AgentEntry,
    AgentsConfig,
    ServerConfig,
    Settings,
    TopologyConfig,
)


def _make_settings(agents: list[AgentEntry], relations: list[dict] | None = None) -> Settings:
    """Build a Settings with the given agents and topology."""
    return Settings(
        max_iterations=5,
        tool_output_limit=2000,
        providers=[],
        default_provider="test",
        default_model="test-model",
        agents_config=AgentsConfig(
            chat_agent=agents[0].name if agents else "default",
            agents=agents,
            topology=TopologyConfig(relations=relations or []),
        ),
    )


class TestTopology:
    def test_get_relation_peer(self):
        agents = [
            AgentEntry(name="a", server=ServerConfig(port=8002)),
            AgentEntry(name="b", server=ServerConfig(port=8003)),
        ]
        settings = _make_settings(agents, relations=[{"from": "a", "to": "b", "mode": "peer"}])
        topo = Topology(settings)
        assert topo.get_relation("a", "b") == "peer"

    def test_get_relation_default_peer(self):
        agents = [AgentEntry(name="a", server=ServerConfig(port=8002))]
        settings = _make_settings(agents)
        topo = Topology(settings)
        assert topo.get_relation("a", "b") == "peer"

    def test_agents_map(self):
        agents = [
            AgentEntry(name="a", server=ServerConfig(host="127.0.0.1", port=9000)),
        ]
        settings = _make_settings(agents)
        topo = Topology(settings)
        assert topo.agents["a"]["url"] == "http://localhost:9000"


class TestAgentRegistry:
    def _make_mock_agent(self, name: str) -> MagicMock:
        """Create a mock agent with .card attribute."""
        agent = MagicMock()
        agent.card = AgentCard(name=name, description=f"{name} agent")
        return agent

    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = self._make_mock_agent("test")
        reg.register(agent)
        assert reg.get("test") == agent

    def test_get_not_found(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_names(self):
        reg = AgentRegistry()
        reg.register(self._make_mock_agent("a"))
        reg.register(self._make_mock_agent("b"))
        assert set(reg.list_names()) == {"a", "b"}

    def test_get_url(self):
        agents = [AgentEntry(name="test", server=ServerConfig(host="127.0.0.1", port=9000))]
        settings = _make_settings(agents)
        topo = Topology(settings)
        reg = AgentRegistry(topology=topo)
        assert reg.get_url("test") == "http://localhost:9000"

    def test_get_url_default_port(self):
        reg = AgentRegistry()
        # Unknown agent gets default port
        url = reg.get_url("nonexistent")
        assert "localhost" in url

    def test_get_card(self):
        reg = AgentRegistry()
        agent = self._make_mock_agent("test")
        reg.register(agent)
        card = reg.get_card("test")
        assert card is not None
        assert card.name == "test"

    def test_get_card_not_found(self):
        reg = AgentRegistry()
        assert reg.get_card("nonexistent") is None


class TestModuleSingleton:
    def test_set_and_get_agent_registry(self):
        from qd_evolve.agent.registry import set_agent_registry, get_agent_registry
        reg = AgentRegistry()
        set_agent_registry(reg)
        assert get_agent_registry() == reg

    def test_get_creates_default(self):
        from qd_evolve.agent.registry import get_agent_registry, _registry
        from qd_evolve.tools import a2a as a2a_module
        # Reset to None
        import qd_evolve.agent.registry as mod
        mod._registry = None
        reg = get_agent_registry()
        assert reg is not None
        # Cleanup
        mod._registry = None