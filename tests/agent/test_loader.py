"""Tests for qd_evolve.agent.loader — get_agent_entry, create_agent_core."""

import pytest

from qd_evolve.agent.loader import get_agent_entry
from qd_evolve.core.config import AgentEntry, AgentsConfig, Settings, TopologyConfig


class TestGetAgentEntry:
    def test_found(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default", description="Default agent")],
        )
        entry = get_agent_entry(minimal_settings, "default")
        assert entry is not None
        assert entry.name == "default"

    def test_not_found(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default")],
        )
        entry = get_agent_entry(minimal_settings, "nonexistent")
        assert entry is None

    def test_multiple_agents(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[
                AgentEntry(name="default", description="Default"),
                AgentEntry(name="helper", description="Helper", provider="test", model="test-model"),
            ],
            topology=TopologyConfig(relations=[{"from": "default", "to": "helper", "mode": "peer"}]),
        )
        entry = get_agent_entry(minimal_settings, "helper")
        assert entry is not None
        assert entry.description == "Helper"


class TestCreateAgentCore:
    def test_raises_for_unknown_agent(self, minimal_settings):
        from unittest.mock import patch
        with patch("qd_evolve.agent.loader.load_settings", return_value=minimal_settings):
            from qd_evolve.agent.loader import create_agent_core
            with pytest.raises(ValueError, match="Agent 'nonexistent' not found"):
                create_agent_core("nonexistent", settings=minimal_settings)

    def test_a2a_disabled_single_agent(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default")],
        )
        entry = get_agent_entry(minimal_settings, "default")
        assert entry is not None
        assert len(minimal_settings.agents_config.agents) <= 1