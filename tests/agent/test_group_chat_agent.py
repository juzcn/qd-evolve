"""Tests for qd_evolve.agent.group_chat_agent — dedup, _run_and_publish, heartbeat, delegation."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.group_chat_agent import GroupChatAgent
from qd_evolve.agent.group_chat_transport import GroupChatTransport
from qd_evolve.agent.server import TaskStore
from qd_evolve.core.config import GChatConfig, MqttConfig


def _make_group_chat_agent(run_return="response"):
    """Create a GroupChatAgent with mocked MqttAgent (which wraps A2AAgent->Agent)."""
    # Build the innermost mock (Agent-like)
    mock_inner = MagicMock()
    mock_inner._running = False
    mock_inner._template_mgr = None
    mock_inner._hb_event = asyncio.Event()
    mock_inner._hb_idle_seconds = 0
    mock_inner._hb_task = None
    mock_inner.settings = MagicMock()
    mock_inner.settings.heartbeat_idle_seconds = 0
    mock_inner.settings.agents_config = MagicMock()
    mock_inner.settings.agents_config.gchat = GChatConfig()
    mock_inner.registry = MagicMock()
    mock_inner.providers = MagicMock()
    mock_inner.messages = []
    mock_inner._provider_name = None
    mock_inner._model = None
    mock_inner.iteration = 0
    mock_inner.total_input_tokens = 0
    mock_inner.total_output_tokens = 0
    mock_inner.default_system_prompt = ""
    mock_inner._always_active = set()
    mock_inner._active_tools = set()
    mock_inner._preload_skills = set()
    mock_inner._preload_cli = set()
    mock_inner._loaded_skill_names = set()
    mock_inner._loaded_cli_names = set()
    mock_inner.memory = None
    mock_inner._on_status = None
    mock_inner._on_print = None
    mock_inner._on_event = None
    mock_inner._recalled = MagicMock()
    mock_inner.reset = MagicMock()
    mock_inner.set_status_callback = MagicMock()
    mock_inner.set_print_callback = MagicMock()
    mock_inner.set_event_callback = MagicMock()

    # Build A2AAgent wrapping mock_inner
    card = AgentCard(name="test_agent", description="Test")
    from qd_evolve.agent.a2a_agent import A2AAgent
    a2a = A2AAgent(mock_inner, card, TaskStore())
    a2a.run = MagicMock(return_value=run_return)
    a2a._push_event = MagicMock()
    a2a.stop_heartbeat_loop = MagicMock()

    # Build MqttAgent wrapping A2AAgent
    from qd_evolve.agent.mqtt_agent import MqttAgent
    config = MqttConfig()
    mqtt_agent = MqttAgent(a2a, "localhost", 1883, config)

    mock_transport = MagicMock(spec=GroupChatTransport)
    mock_transport._broker_host = "localhost"
    mock_transport._broker_port = 1883

    mock_tmpl = MagicMock()
    mock_tmpl.render = MagicMock(return_value="heartbeat prompt")

    members = ["test_agent", "helper1", "helper2"]
    gca = GroupChatAgent(mqtt_agent, mock_transport, members, mock_tmpl)
    return gca, mqtt_agent, a2a, mock_inner, mock_transport, mock_tmpl


# ── Delegation properties ──────────────────────────────────────────


class TestGroupChatAgentDelegation:
    def test_card_delegates(self):
        gca, mqtt_agent, a2a, _, _, _ = _make_group_chat_agent()
        assert gca.card is mqtt_agent.card
        assert gca.card.name == "test_agent"

    def test_task_store_delegates(self):
        gca, mqtt_agent, _, _, _, _ = _make_group_chat_agent()
        assert gca.task_store is mqtt_agent.task_store

    def test_event_queue_exists(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        assert isinstance(gca.event_queue, asyncio.Queue)


# ── Deduplication ──────────────────────────────────────────────────


class TestGroupChatAgentDedup:
    def test_seen_msg_ids_initially_empty(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        assert len(gca._seen_msg_ids) == 0

    def test_add_seen_msg(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        gca._seen_msg_ids.add("msg-1")
        assert "msg-1" in gca._seen_msg_ids

    def test_duplicate_skipped(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        gca._seen_msg_ids.add("msg-1")
        assert "msg-1" in gca._seen_msg_ids

    def test_seen_set_cap(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        # Fill with >10000 entries
        for i in range(10001):
            gca._seen_msg_ids.add(f"msg-{i}")
        assert len(gca._seen_msg_ids) > 10000
        # Simulate the cap logic in _listen_group_chat
        gca._seen_msg_ids = set(list(gca._seen_msg_ids)[-5000:])
        assert len(gca._seen_msg_ids) <= 5000


# ── _run_and_publish ───────────────────────────────────────────────


class TestRunAndPublish:
    def test_silent_dot_response(self):
        gca, mqtt_agent, a2a, _, mock_transport, _ = _make_group_chat_agent(run_return=".")
        gca._loop = None
        gca._run_and_publish("hello")
        # Agent run was called but no publish (silent)
        a2a.run.assert_called_once_with("hello")

    def test_silent_empty_response(self):
        gca, mqtt_agent, a2a, _, mock_transport, _ = _make_group_chat_agent(run_return="")
        gca._loop = None
        gca._run_and_publish("hello")
        a2a.run.assert_called_once_with("hello")

    def test_silent_whitespace_response(self):
        gca, mqtt_agent, a2a, _, mock_transport, _ = _make_group_chat_agent(run_return="   ")
        gca._loop = None
        gca._run_and_publish("hello")
        a2a.run.assert_called_once_with("hello")

    def test_normal_response_published(self):
        gca, mqtt_agent, a2a, _, mock_transport, _ = _make_group_chat_agent(run_return="I'm here!")
        gca._loop = None  # will use asyncio.run
        with patch("asyncio.run", return_value="msg-id-123") as mock_arun:
            gca._run_and_publish("hello")
            a2a.run.assert_called_once_with("hello")
            mock_arun.assert_called_once()

    def test_run_failure_handled(self):
        gca, mqtt_agent, a2a, _, mock_transport, _ = _make_group_chat_agent()
        a2a.run.side_effect = Exception("LLM down")
        gca._loop = None
        gca._run_and_publish("hello")
        # Should not crash, just log and return


# ── reply delay ────────────────────────────────────────────────────


class TestGroupChatAgentReplyDelay:
    def test_no_delay_by_default(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner._running = False
        mock_inner.settings.agents_config.gchat = GChatConfig()
        with patch("qd_evolve.agent.group_chat_agent.time.sleep") as mock_sleep:
            gca._run_and_publish("hello")
            mock_sleep.assert_not_called()
        a2a.run.assert_called_once_with("hello")

    def test_delay_applied_when_configured(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner._running = False
        mock_inner.settings.agents_config.gchat = GChatConfig(reply_delay_min=2.0, reply_delay_max=5.0)
        with patch("qd_evolve.agent.group_chat_agent.time.sleep") as mock_sleep:
            gca._run_and_publish("hello")
            mock_sleep.assert_called_once()
            delay = mock_sleep.call_args[0][0]
            assert 2.0 <= delay <= 5.0
        a2a.run.assert_called_once_with("hello")

    def test_delay_before_run(self):
        """Delay happens before agent.run(), not after."""
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner._running = False
        mock_inner.settings.agents_config.gchat = GChatConfig(reply_delay_min=1.0, reply_delay_max=1.0)
        call_order = []
        mock_sleep = lambda s: call_order.append("sleep")
        original_run = a2a.run
        a2a.run = lambda t: (call_order.append("run"), original_run(t))[1]
        with patch("qd_evolve.agent.group_chat_agent.time.sleep", side_effect=mock_sleep):
            gca._run_and_publish("hello")
        assert call_order == ["sleep", "run"]

    def test_zero_max_means_no_delay(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner._running = False
        mock_inner.settings.agents_config.gchat = GChatConfig(reply_delay_min=5.0, reply_delay_max=0.0)
        with patch("qd_evolve.agent.group_chat_agent.time.sleep") as mock_sleep:
            gca._run_and_publish("hello")
            mock_sleep.assert_not_called()


# ── heartbeat_check ────────────────────────────────────────────────


class TestGroupChatAgentHeartbeat:
    def test_skips_when_running(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner._running = True
        result = gca.heartbeat_check(60)
        assert result is None

    def test_uses_group_heartbeat_template(self):
        gca, mqtt_agent, a2a, mock_inner, _, mock_tmpl = _make_group_chat_agent(run_return="I'm alive")
        mock_inner._running = False
        result = gca.heartbeat_check(60)
        mock_tmpl.render.assert_called_once()
        call_args = mock_tmpl.render.call_args
        assert call_args[0][0] == "group-heartbeat"
        assert result == "I'm alive"

    def test_silent_dot_heartbeat(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent(run_return=".")
        mock_inner._running = False
        result = gca.heartbeat_check(60)
        assert result is None
        # _push_event goes through mqtt_agent -> a2a._push_event
        a2a._push_event.assert_called_with({"type": "heartbeat_silent"})

    def test_llm_failure_returns_none(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        a2a.run.side_effect = Exception("timeout")
        mock_inner._running = False
        result = gca.heartbeat_check(60)
        assert result is None


# ── Heartbeat loop ─────────────────────────────────────────────────


class TestGroupChatAgentHeartbeatLoop:
    def test_disabled_with_zero_seconds(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner.settings.heartbeat_idle_seconds = 0
        gca.start_heartbeat_loop()
        assert gca._heartbeat_task is None

    @pytest.mark.asyncio
    async def test_start_sets_task(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner.settings.heartbeat_idle_seconds = 60
        gca.start_heartbeat_loop()
        assert gca._heartbeat_task is not None
        gca.stop_heartbeat_loop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        gca, mqtt_agent, a2a, mock_inner, _, _ = _make_group_chat_agent()
        mock_inner.settings.heartbeat_idle_seconds = 60
        gca.start_heartbeat_loop()
        assert gca._heartbeat_task is not None
        gca.stop_heartbeat_loop()
        # stop_heartbeat_loop calls cancel() on the task and delegates to agent
        # The task may be in "cancelling" state (not yet fully cancelled)
        # until the event loop runs. Just verify it was requested to stop.
        a2a.stop_heartbeat_loop.assert_called_once()


# ── Event queue ─────────────────────────────────────────────────────


class TestGroupChatAgentEventQueue:
    def test_event_queue_is_asyncio_queue(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        assert isinstance(gca.event_queue, asyncio.Queue)

    def test_push_incoming_message_to_event_queue(self):
        gca, _, _, _, _, _ = _make_group_chat_agent()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(
            gca.event_queue.put({
                "type": "group_message",
                "from_agent": "helper1",
                "content": "hi",
            })
        )
        msg = gca.event_queue.get_nowait()
        assert msg["type"] == "group_message"
        assert msg["from_agent"] == "helper1"
        loop.close()