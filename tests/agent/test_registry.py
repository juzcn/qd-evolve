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
    def test_get_relation(self):
        agents = [
            AgentEntry(name="a", server=ServerConfig(port=8002)),
            AgentEntry(name="b", server=ServerConfig(port=8003)),
        ]
        settings = _make_settings(agents, relations=[{"from": "a", "to": "b", "mode": "peer"}])
        topo = Topology(settings)
        r = topo.get("a")
        assert r is not None
        assert r["mode"] == "peer"

    def test_get_relation_not_found(self):
        agents = [AgentEntry(name="a", server=ServerConfig(port=8002))]
        settings = _make_settings(agents)
        topo = Topology(settings)
        assert topo.get("nonexistent") is None

    def test_agents_url_map(self):
        agents = [
            AgentEntry(name="a", server=ServerConfig(host="127.0.0.1", port=9000)),
            AgentEntry(name="b", server=ServerConfig(port=8002)),
        ]
        settings = _make_settings(agents)
        topo = Topology(settings)
        assert "9000" in topo.agents["a"]["url"]
        assert "8002" in topo.agents["b"]["url"]


class TestAgentRegistry:
    def _make_mock_agent(self, name: str) -> MagicMock:
        agent = MagicMock()
        agent.card = AgentCard(name=name, description=f"{name} agent", url="127.0.0.1:8002")
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
        assert set(reg.agents.keys()) == {"a", "b"}

    def test_get_url_registered_agent(self):
        reg = AgentRegistry()
        agent = self._make_mock_agent("test")
        reg.register(agent)
        url = reg.get_url("test")
        assert url is not None
        assert "8002" in url

    def test_get_url_from_topology(self):
        agents = [AgentEntry(name="test", server=ServerConfig(host="127.0.0.1", port=9000))]
        settings = _make_settings(agents)
        topo = Topology(settings)
        reg = AgentRegistry(topology=topo)
        url = reg.get_url("test")
        assert url is not None
        assert "9000" in url

    def test_get_url_unknown_agent(self):
        reg = AgentRegistry()
        url = reg.get_url("unknown")
        assert url is None

    def test_get_card(self):
        reg = AgentRegistry()
        agent = self._make_mock_agent("test")
        reg.register(agent)
        got = reg.get("test")
        assert got is not None
        card = got.card
        assert card is not None
        assert card.name == "test"

    def test_get_card_not_found(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None


class TestModuleSingleton:
    def test_set_and_get_agent_registry(self):
        from qd_evolve.agent.registry import set_agent_registry, get_agent_registry
        reg = AgentRegistry()
        set_agent_registry(reg)
        assert get_agent_registry() == reg

    def test_get_raises_when_not_initialized(self):
        from qd_evolve.agent.registry import get_agent_registry
        import qd_evolve.agent.registry as mod
        old = mod._registry
        mod._registry = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                get_agent_registry()
        finally:
            mod._registry = old
