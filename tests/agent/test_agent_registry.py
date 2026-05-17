"""Tests for qd_evolve.agent.registry — AgentRegistry, Topology."""

import pytest

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.registry import AgentRegistry, Topology
from qd_evolve.core.config import (
    AgentEntry,
    AgentsConfig,
    ModelConfig,
    ProviderConfig,
    ServerConfig,
    Settings,
    TopologyConfig,
)


def _make_settings(agents, relations=None):
    """Helper to create Settings with specific agents."""
    return Settings(
        max_iterations=5,
        tool_output_limit=2000,
        providers=[ProviderConfig(name="test", api_key="sk-test", models=[ModelConfig(name="test-model", max_tokens=100, context_window=4000)])],
        default_provider="test",
        default_model="test-model",
        agents_config=AgentsConfig(
            chat_agent=agents[0].name,
            agents=agents,
            topology=TopologyConfig(relations=relations or []),
        ),
    )


class TestTopology:
    def test_get_transport_both_inproc(self):
        settings = _make_settings([
            AgentEntry(name="default", transport="inproc"),
            AgentEntry(name="helper", transport="inproc"),
        ])
        topo = Topology(settings)
        assert topo.get_transport("default", "helper") == "inproc"

    def test_get_transport_one_http(self):
        settings = _make_settings([
            AgentEntry(name="default", transport="inproc"),
            AgentEntry(name="remote", transport="http", server=ServerConfig(port=9000)),
        ])
        topo = Topology(settings)
        assert topo.get_transport("default", "remote") == "http"

    def test_get_transport_unknown_agent(self):
        settings = _make_settings([AgentEntry(name="default")])
        topo = Topology(settings)
        assert topo.get_transport("default", "unknown") == "inproc"

    def test_get_relation_peer(self):
        settings = _make_settings(
            [AgentEntry(name="default"), AgentEntry(name="helper")],
            relations=[{"from": "default", "to": "helper", "mode": "peer"}],
        )
        topo = Topology(settings)
        assert topo.get_relation("default", "helper") == "peer"

    def test_get_relation_default_peer(self):
        settings = _make_settings(
            [AgentEntry(name="default"), AgentEntry(name="helper")],
        )
        topo = Topology(settings)
        assert topo.get_relation("default", "helper") == "peer"

    def test_agents_url_map(self):
        settings = _make_settings([
            AgentEntry(name="default", server=ServerConfig(port=8001)),
            AgentEntry(name="remote", transport="http", server=ServerConfig(port=9000)),
        ])
        topo = Topology(settings)
        assert topo.agents["default"]["url"] == "http://localhost:8001"
        assert topo.agents["remote"]["url"] == "http://localhost:9000"


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        mock_agent = type("MockAgent", (), {"card": AgentCard(name="test", description="Test")})()
        reg.register(mock_agent)
        assert reg.get("test") is not None

    def test_get_not_found(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_list_names(self):
        reg = AgentRegistry()
        a1 = type("MockAgent", (), {"card": AgentCard(name="a1", description="A1")})()
        a2 = type("MockAgent", (), {"card": AgentCard(name="a2", description="A2")})()
        reg.register(a1)
        reg.register(a2)
        names = reg.list_names()
        assert "a1" in names
        assert "a2" in names

    def test_get_card(self):
        reg = AgentRegistry()
        mock_agent = type("MockAgent", (), {"card": AgentCard(name="test", description="Test")})()
        reg.register(mock_agent)
        card = reg.get_card("test")
        assert card is not None
        assert card.name == "test"

    def test_get_card_not_found(self):
        reg = AgentRegistry()
        assert reg.get_card("nonexistent") is None

    def test_get_url(self):
        settings = _make_settings([
            AgentEntry(name="default", server=ServerConfig(port=8001)),
        ])
        topo = Topology(settings)
        reg = AgentRegistry(topology=topo)
        url = reg.get_url("default")
        assert url == "http://localhost:8001"

    def test_get_url_unknown_agent(self):
        reg = AgentRegistry()
        url = reg.get_url("unknown")
        assert "localhost" in url