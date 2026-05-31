"""Tests for qd_evolve.agent.loader — get_agent_entry, create_agent, _a2a_enabled, singletons."""

import pytest

from qd_evolve.agent.loader import get_agent_entry, _a2a_enabled, get_skill_registry, get_cli_registry
from qd_evolve.core.config import AgentEntry, AgentsConfig, ServerConfig, TopologyConfig


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


class TestA2aEnabled:
    def test_single_agent_disabled(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[AgentEntry(name="default", server=ServerConfig(port=8002))],
        )
        assert _a2a_enabled(minimal_settings) is False

    def test_multi_agent_enabled(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="default",
            agents=[
                AgentEntry(name="default", server=ServerConfig(port=8002)),
                AgentEntry(name="helper", server=ServerConfig(port=8003)),
            ],
        )
        assert _a2a_enabled(minimal_settings) is True

    def test_zero_agents_disabled(self, minimal_settings):
        minimal_settings.agents_config = AgentsConfig(chat_agent="default", agents=[])
        assert _a2a_enabled(minimal_settings) is False


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

    def test_creates_human_agent(self, minimal_settings):
        from qd_evolve.agent.loader import create_agent
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="human",
            agents=[AgentEntry(name="human", provider="human", description="Human agent")],
        )
        agent = create_agent("human", settings=minimal_settings, need_a2a=False)
        from qd_evolve.agent.human_agent import HumanAgent
        assert isinstance(agent, HumanAgent)

    def test_creates_mqtt_human_agent(self, minimal_settings):
        from qd_evolve.agent.loader import create_agent
        from qd_evolve.core.config import MqttBrokerConfig
        minimal_settings.agents_config = AgentsConfig(
            chat_agent="human",
            agents=[AgentEntry(name="human", provider="human", description="Human agent")],
            mqtt_broker=MqttBrokerConfig(host="localhost", port=1883),
        )
        agent = create_agent("human", settings=minimal_settings, need_mqtt=True)
        from qd_evolve.agent.mqtt_human_agent import MqttHumanAgent
        assert isinstance(agent, MqttHumanAgent)


class TestSingletons:
    def test_skill_registry_lazy_init(self):
        from qd_evolve.agent import loader as ldr
        old = ldr._skill_registry
        ldr._skill_registry = None
        reg = get_skill_registry()
        assert reg is not None
        ldr._skill_registry = old

    def test_cli_registry_lazy_init(self):
        from qd_evolve.agent import loader as ldr
        old = ldr._cli_registry
        ldr._cli_registry = None
        reg = get_cli_registry()
        assert reg is not None
        ldr._cli_registry = old

    def test_get_bridges(self):
        from qd_evolve.agent.loader import get_bridges
        result = get_bridges()
        assert isinstance(result, list)