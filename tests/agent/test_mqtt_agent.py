"""Tests for qd_evolve.agent.mqtt_agent — _extract_text, delegation, heartbeat_check, heartbeat loop."""

import asyncio
from unittest.mock import MagicMock

import pytest

from qd_evolve.agent.a2a import AgentCard, Message, Part
from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.mqtt_agent import MqttAgent
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import MqttConfig


def _make_mqtt_agent(run_return="response"):
    """Create an MqttAgent wrapping a mocked A2AAgent with a real MagicMock Agent."""
    # Use plain MagicMock (no spec) so registry/providers/etc. auto-create
    mock_inner = MagicMock()
    mock_inner._running = False
    mock_inner._template_mgr = None
    mock_inner._hb_event = asyncio.Event()
    mock_inner._hb_idle_seconds = 0
    mock_inner._hb_task = None
    mock_inner.settings = MagicMock()
    mock_inner.settings.heartbeat_idle_seconds = 60
    mock_inner.registry = MagicMock()
    mock_inner.providers = MagicMock()
    mock_inner.memory = None
    mock_inner.messages = []
    mock_inner._provider_name = None
    mock_inner._model = None
    mock_inner._always_active = set()
    mock_inner._active_tools = set()
    mock_inner._preload_skills = set()
    mock_inner._preload_cli = set()
    mock_inner._loaded_skill_names = set()
    mock_inner._loaded_cli_names = set()
    mock_inner.last_input_tokens = 0
    mock_inner.last_output_tokens = 0
    mock_inner.total_input_tokens = 0
    mock_inner.total_output_tokens = 0
    mock_inner.iteration = 0
    mock_inner.default_system_prompt = ""
    mock_inner._on_status = None
    mock_inner._on_print = None
    mock_inner._on_event = None
    mock_inner._recalled = MagicMock()

    card = AgentCard(name="mqtt_agent", description="MQTT Agent")
    a2a = A2AAgent(mock_inner, card, TaskStore())
    a2a.run = MagicMock(return_value=run_return)
    # Keep a2a._push_event as MagicMock for assertion (A2AAgent hooks it in __init__)
    a2a._push_event = MagicMock()
    a2a.stop_heartbeat_loop = MagicMock()

    config = MqttConfig()
    mqtt_agent = MqttAgent(a2a, "localhost", 1883, config)
    return mqtt_agent, a2a, mock_inner


# ── _extract_text ──────────────────────────────────────────────────


class TestMqttAgentExtractText:
    def test_extracts_text(self):
        msg = Message(role="user", parts=[Part(type="text", text="hello")])
        assert MqttAgent._extract_text(msg) == "hello"

    def test_empty_when_no_text(self):
        msg = Message(role="user", parts=[Part(type="file", file=None)])
        assert MqttAgent._extract_text(msg) == ""

    def test_empty_when_no_parts(self):
        msg = Message(role="user", parts=[])
        assert MqttAgent._extract_text(msg) == ""


# ── Delegation properties ──────────────────────────────────────────


class TestMqttAgentDelegation:
    def test_card_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        assert mqtt.card is a2a.card
        assert mqtt.card.name == "mqtt_agent"

    def test_task_store_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        assert mqtt.task_store is a2a.task_store

    def test_run_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        result = mqtt.run("hello")
        a2a.run.assert_called_once_with("hello")  # type: ignore
        assert result == "response"

    def test_subscribe_events_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        q = mqtt.subscribe_events()
        assert isinstance(q, asyncio.Queue)

    def test_unsubscribe_events_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        q = mqtt.subscribe_events()
        mqtt.unsubscribe_events(q)

    def test_push_event_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        mqtt._push_event({"type": "test"})
        a2a._push_event.assert_called_once_with({"type": "test"})  # type: ignore

    def test_settings_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        assert mqtt.settings is a2a.settings

    def test_registry_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        assert mqtt.registry is a2a.registry

    def test_providers_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        assert mqtt.providers is a2a.providers

    def test_running_via_agent(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner._running = True
        assert mqtt.agent._running is True


# ── heartbeat_check ────────────────────────────────────────────────


class TestMqttAgentHeartbeatCheck:
    def test_skips_when_running(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner._running = True
        result = mqtt.heartbeat_check(60)
        assert result is None

    def test_uses_mqtt_heartbeat_template(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent(run_return="I'm here")
        mock_inner._running = False
        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "mqtt heartbeat msg"
        mock_inner._template_mgr = mock_tmpl
        mqtt.heartbeat_check(60)
        mock_tmpl.render.assert_called_once()
        call_args = mock_tmpl.render.call_args
        assert call_args[0][0] == "heartbeat"
        assert call_args[1]["idle_seconds"] == 60
        assert call_args[1]["mqtt_broker_host"] == "localhost"
        assert call_args[1]["mqtt_broker_port"] == 1883

    def test_no_template_fallback(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent(run_return="alive")
        mock_inner._running = False
        mock_inner._template_mgr = None
        result = mqtt.heartbeat_check(60)
        assert result == "alive"

    def test_silent_dot_heartbeat(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent(run_return=".")
        mock_inner._running = False
        result = mqtt.heartbeat_check(60)
        assert result == "."
        # _push_event delegates to a2a._push_event
        a2a._push_event.assert_called_with({"type": "heartbeat_silent"})  # type: ignore

    def test_normal_heartbeat(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent(run_return="I'm alive")
        mock_inner._running = False
        result = mqtt.heartbeat_check(60)
        assert result == "I'm alive"
        a2a._push_event.assert_called_with({"type": "heartbeat", "content": "I'm alive"})  # type: ignore

    def test_llm_failure_returns_none(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        a2a.run.side_effect = Exception("connection lost")  # type: ignore
        mock_inner._running = False
        result = mqtt.heartbeat_check(60)
        assert result is None


# ── Heartbeat loop ─────────────────────────────────────────────────


class TestMqttAgentHeartbeatLoop:
    def test_disabled_with_zero_seconds(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner.settings.heartbeat_idle_seconds = 0
        mqtt.start_heartbeat_loop()
        assert mock_inner._hb_task is None

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner.settings.heartbeat_idle_seconds = 60
        mqtt.start_heartbeat_loop()
        assert mock_inner._hb_task is not None
        mqtt.stop_heartbeat_loop()

    def test_stop_delegates(self):
        mqtt, a2a, _ = _make_mqtt_agent()
        mqtt.stop_heartbeat_loop()
        a2a.stop_heartbeat_loop.assert_called_once()  # type: ignore


# ── Agent attribute pass-through ────────────────────────────────────


class TestMqttAgentAttributePassThrough:
    def test_messages_property(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner.messages = [{"role": "user", "content": "hi"}]
        assert mqtt.messages == [{"role": "user", "content": "hi"}]

    def test_messages_setter(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mqtt.messages = [{"role": "user", "content": "test"}]
        assert mock_inner.messages == [{"role": "user", "content": "test"}]

    def test_provider_name_property(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner._provider_name = "test_provider"
        assert mqtt._provider_name == "test_provider"

    def test_provider_name_setter(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mqtt._provider_name = "new_provider"
        assert mock_inner._provider_name == "new_provider"

    def test_model_property(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner._model = "test_model"
        assert mqtt._model == "test_model"

    def test_model_setter(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mqtt._model = "new_model"
        assert mock_inner._model == "new_model"

    def test_iteration_property(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner.iteration = 5
        assert mqtt.iteration == 5

    def test_iteration_setter(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mqtt.iteration = 10
        assert mock_inner.iteration == 10

    def test_total_tokens(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner.total_input_tokens = 100
        mock_inner.total_output_tokens = 50
        # total_tokens is a computed property on Agent, but A2AAgent delegates
        # so we need to check the raw values
        assert mqtt.total_input_tokens == 100
        assert mqtt.total_output_tokens == 50

    def test_default_system_prompt(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mock_inner.default_system_prompt = "test prompt"
        assert mqtt.default_system_prompt == "test prompt"

    def test_reset_delegates(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        mqtt.reset()
        mock_inner.reset.assert_called_once()

    def test_set_callbacks_delegates(self):
        mqtt, a2a, mock_inner = _make_mqtt_agent()
        cb = lambda x: None
        mqtt.set_status_callback(cb)
        mqtt.set_print_callback(cb)
        mqtt.set_event_callback(cb)
        mock_inner.set_status_callback.assert_called_once_with(cb)
        mock_inner.set_print_callback.assert_called_once_with(cb)
        mock_inner.set_event_callback.assert_called_once_with(cb)