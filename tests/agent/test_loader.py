"""Tests for qd_evolve.agent.loader — get_agent_entry, create_agent."""

import pytest

from qd_evolve.agent.loader import get_agent_entry
from qd_evolve.core.config import AgentEntry, AgentsConfig, ServerConfig, Settings, TopologyConfig


class TestGetAgentEntry:
    def test_found(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default", description="Default agent", server=ServerConfig(port=8002))],
        )
        entry = get_agent_entry(minimal_settings, "default")
        assert entry is not None
        assert entry.name == "default"

    def test_not_found(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default", server=ServerConfig(port=8002))],
        )
        entry = get_agent_entry(minimal_settings, "nonexistent")
        assert entry is None

    def test_multiple_agents(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[
                AgentEntry(name="default", description="Default", server=ServerConfig(port=8002)),
                AgentEntry(name="helper", description="Helper", provider="test", model="test-model", server=ServerConfig(port=8003)),
            ],
            topology=TopologyConfig(relations=[{"from": "default", "to": "helper", "mode": "peer"}]),
        )
        entry = get_agent_entry(minimal_settings, "helper")
        assert entry is not None
        assert entry.description == "Helper"


class TestCreateAgent:
    def test_raises_for_unknown_agent(self, minimal_settings):
        from qd_evolve.agent.loader import create_agent
        with pytest.raises(ValueError, match="Agent 'nonexistent' not found"):
            create_agent("nonexistent", settings=minimal_settings)

    def test_a2a_disabled_single_agent(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default", server=ServerConfig(port=8002))],
        )
        entry = get_agent_entry(minimal_settings, "default")
        assert entry is not None
        assert len(minimal_settings.agents_config.agents) <= 1