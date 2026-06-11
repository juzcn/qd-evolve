"""Tests for qd_evolve.agent.a2a_agent — A2AAgent delegation, init, event subscribers."""

import asyncio
from unittest.mock import MagicMock

import pytest

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.agent import Agent
from qd_evolve.agent.server import TaskStore


@pytest.fixture
def a2a_agent(agent_core):
    """A2AAgent wrapping a real minimal Agent."""
    card = AgentCard(name="test-agent", description="Test agent for A2A")
    return A2AAgent(agent_core, card)


@pytest.fixture
def a2a_agent_mock():
    """A2AAgent wrapping a MagicMock Agent — for delegation tests.
    Plain MagicMock (no spec) so all attributes auto-create — we're testing
    delegation, not the Agent class contract."""
    mock = MagicMock()
    card = AgentCard(name="mock-agent", description="Mock agent")
    return A2AAgent(mock, card)


class TestA2AAgentInit:
    def test_stores_card(self, a2a_agent):
        assert a2a_agent.card.name == "test-agent"
        assert a2a_agent.card.description == "Test agent for A2A"

    def test_creates_default_task_store(self, a2a_agent_mock):
        assert isinstance(a2a_agent_mock.task_store, TaskStore)

    def test_accepts_custom_task_store(self, agent_core):
        ts = TaskStore()
        card = AgentCard(name="test", description="Test")
        a2a = A2AAgent(agent_core, card, task_store=ts)
        assert a2a.task_store is ts

    def test_default_heartbeat_template(self, a2a_agent):
        assert a2a_agent._heartbeat_template == "heartbeat"

    def test_custom_heartbeat_template(self, agent_core):
        card = AgentCard(name="test", description="Test")
        a2a = A2AAgent(agent_core, card, heartbeat_template="custom-hb")
        assert a2a._heartbeat_template == "custom-hb"

    def test_hooks_agent_on_event_to_push_event(self, a2a_agent_mock):
        # After init, agent._on_event should be set to a2a_agent._push_event
        assert a2a_agent_mock.agent._on_event == a2a_agent_mock._push_event

    def test_running_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._running = True
        assert a2a_agent_mock._running is True
        a2a_agent_mock.agent._running = False
        assert a2a_agent_mock._running is False


class TestA2AAgentDelegation:
    """Verify A2AAgent delegates methods and attributes to wrapped Agent."""

    def test_run_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent.run.return_value = "response"
        result = a2a_agent_mock.run("hello")
        a2a_agent_mock.agent.run.assert_called_once_with("hello")
        assert result == "response"

    def test_reset_delegates(self, a2a_agent_mock):
        a2a_agent_mock.reset()
        a2a_agent_mock.agent.reset.assert_called_once()

    def test_set_status_callback_delegates(self, a2a_agent_mock):
        cb = lambda s: None
        a2a_agent_mock.set_status_callback(cb)
        a2a_agent_mock.agent.set_status_callback.assert_called_once_with(cb)

    def test_set_print_callback_delegates(self, a2a_agent_mock):
        cb = lambda s: None
        a2a_agent_mock.set_print_callback(cb)
        a2a_agent_mock.agent.set_print_callback.assert_called_once_with(cb)

    def test_set_event_callback_delegates(self, a2a_agent_mock):
        cb = lambda e: None
        a2a_agent_mock.set_event_callback(cb)
        a2a_agent_mock.agent.set_event_callback.assert_called_once_with(cb)

    def test_stop_heartbeat_loop_delegates(self, a2a_agent_mock):
        a2a_agent_mock.stop_heartbeat_loop()
        a2a_agent_mock.agent.stop_heartbeat_loop.assert_called_once()

    # ── Property delegation ──────────────────────────────────────────

    def test_settings_delegates(self, a2a_agent_mock):
        assert a2a_agent_mock.settings is a2a_agent_mock.agent.settings

    def test_registry_delegates(self, a2a_agent_mock):
        assert a2a_agent_mock.registry is a2a_agent_mock.agent.registry

    def test_providers_delegates(self, a2a_agent_mock):
        assert a2a_agent_mock.providers is a2a_agent_mock.agent.providers

    def test_memory_delegates(self, a2a_agent_mock):
        assert a2a_agent_mock.memory is a2a_agent_mock.agent.memory

    def test_messages_getter_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent.messages = [{"role": "user", "content": "hi"}]
        assert a2a_agent_mock.messages == [{"role": "user", "content": "hi"}]

    def test_messages_setter_delegates(self, a2a_agent_mock):
        a2a_agent_mock.messages = [{"role": "assistant", "content": "hello"}]
        assert a2a_agent_mock.agent.messages == [{"role": "assistant", "content": "hello"}]

    def test_provider_name_delegates(self, a2a_agent_mock):
        a2a_agent_mock._provider_name = "test"
        assert a2a_agent_mock.agent._provider_name == "test"
        assert a2a_agent_mock._provider_name == "test"

    def test_model_delegates(self, a2a_agent_mock):
        a2a_agent_mock._model = "gpt-4"
        assert a2a_agent_mock.agent._model == "gpt-4"
        assert a2a_agent_mock._model == "gpt-4"

    def test_always_active_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._always_active = {"echo", "fetch"}
        assert a2a_agent_mock._always_active == {"echo", "fetch"}

    def test_active_tools_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._active_tools = {"run_shell"}
        assert a2a_agent_mock._active_tools == {"run_shell"}

    def test_preload_skills_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._preload_skills = {"search"}
        assert a2a_agent_mock._preload_skills == {"search"}

    def test_preload_cli_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._preload_cli = {"git"}
        assert a2a_agent_mock._preload_cli == {"git"}

    def test_loaded_skill_names_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._loaded_skill_names = {"s1"}
        assert a2a_agent_mock._loaded_skill_names == {"s1"}

    def test_loaded_cli_names_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._loaded_cli_names = {"c1"}
        assert a2a_agent_mock._loaded_cli_names == {"c1"}

    def test_token_properties_delegate(self, a2a_agent_mock):
        a2a_agent_mock.agent.last_input_tokens = 10
        a2a_agent_mock.agent.last_output_tokens = 20
        a2a_agent_mock.agent.total_input_tokens = 100
        a2a_agent_mock.agent.total_output_tokens = 200
        a2a_agent_mock.agent.total_tokens = 300

        assert a2a_agent_mock.last_input_tokens == 10
        assert a2a_agent_mock.last_output_tokens == 20
        assert a2a_agent_mock.total_input_tokens == 100
        assert a2a_agent_mock.total_output_tokens == 200
        assert a2a_agent_mock.total_tokens == 300

    def test_iteration_getter_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent.iteration = 5
        assert a2a_agent_mock.iteration == 5

    def test_iteration_setter_delegates(self, a2a_agent_mock):
        a2a_agent_mock.iteration = 10
        assert a2a_agent_mock.agent.iteration == 10

    def test_default_system_prompt_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent.default_system_prompt = "You are helpful."
        assert a2a_agent_mock.default_system_prompt == "You are helpful."

        a2a_agent_mock.default_system_prompt = "New prompt"
        assert a2a_agent_mock.agent.default_system_prompt == "New prompt"

    def test___track_tokens_methods_delegate(self, a2a_agent_mock):
        """_track_tokens_* methods update token counters on the underlying agent."""
        usage = MagicMock()
        usage.input_tokens = 100
        usage.output_tokens = 50
        a2a_agent_mock.agent.total_input_tokens = 0
        a2a_agent_mock.agent.total_output_tokens = 0

        a2a_agent_mock._track_tokens_anthropic(usage)
        assert a2a_agent_mock.agent.last_input_tokens == 100
        assert a2a_agent_mock.agent.last_output_tokens == 50

        usage.prompt_tokens = 200
        usage.completion_tokens = 75
        a2a_agent_mock._track_tokens_openai_completion(usage)
        assert a2a_agent_mock.agent.last_input_tokens == 200
        assert a2a_agent_mock.agent.last_output_tokens == 75

        usage2 = MagicMock()
        usage2.input_tokens = 300
        usage2.output_tokens = 100
        a2a_agent_mock._track_tokens_openai_response(usage2)
        assert a2a_agent_mock.agent.last_input_tokens == 300
        assert a2a_agent_mock.agent.last_output_tokens == 100

    def test__recalled_delegates(self, a2a_agent_mock):
        a2a_agent_mock.agent._recalled = "some-value"
        assert a2a_agent_mock._recalled == "some-value"

    def test_hb_task_property_delegates(self, a2a_agent_mock):
        task = MagicMock(spec=asyncio.Task)
        a2a_agent_mock._hb_task = task
        assert a2a_agent_mock.agent._hb_task is task


class TestA2AEventSubscribers:
    def test_subscribe_events_returns_queue(self, a2a_agent):
        q = a2a_agent.subscribe_events()
        assert isinstance(q, asyncio.Queue)

    def test_multiple_subscribers_get_same_event(self, a2a_agent):
        q1 = a2a_agent.subscribe_events()
        q2 = a2a_agent.subscribe_events()

        event = {"type": "iteration", "iteration": 0}
        a2a_agent._push_event(event)

        assert q1.get_nowait() == event
        assert q2.get_nowait() == event

    def test_unsubscribe_removes_subscriber(self, a2a_agent):
        q1 = a2a_agent.subscribe_events()
        q2 = a2a_agent.subscribe_events()

        a2a_agent.unsubscribe_events(q1)
        event = {"type": "done"}
        a2a_agent._push_event(event)

        # q1 should not receive the event (it was removed)
        assert q1.empty()
        # q2 should receive it
        assert q2.get_nowait() == event

    def test_unsubscribe_nonexistent_is_noop(self, a2a_agent):
        q = asyncio.Queue()
        a2a_agent.unsubscribe_events(q)  # should not raise

    def test_push_event_full_queue_is_silent(self, a2a_agent):
        """push_event catches exceptions from full queues gracefully."""
        q = a2a_agent.subscribe_events()
        # Fill the queue to its maxsize (0 = unlimited for asyncio.Queue)
        # We simulate a failing put_nowait by mocking
        mock_q = MagicMock(spec=asyncio.Queue)
        mock_q.put_nowait.side_effect = asyncio.QueueFull()
        a2a_agent._event_subscribers = [mock_q]

        # Should not raise
        a2a_agent._push_event({"type": "test"})
        mock_q.put_nowait.assert_called_once()

    def test_subscribe_heartbeat_is_alias(self, a2a_agent):
        """subscribe_heartbeat is a backward-compat alias for subscribe_events."""
        q = a2a_agent.subscribe_heartbeat()
        assert isinstance(q, asyncio.Queue)
        assert q in a2a_agent._event_subscribers

    def test_unsubscribe_heartbeat_is_alias(self, a2a_agent):
        """unsubscribe_heartbeat is a backward-compat alias for unsubscribe_events."""
        q = a2a_agent.subscribe_events()
        a2a_agent.unsubscribe_heartbeat(q)
        assert q not in a2a_agent._event_subscribers

    def test_no_subscribers_push_does_not_raise(self, a2a_agent):
        """Pushing events with no subscribers should be a no-op."""
        a2a_agent._event_subscribers = []
        a2a_agent._push_event({"type": "lonely"})  # should not raise
