"""Tests for push notification flow — on_push_notification, _check_pending_task_results,
A2AAgent.heartbeat_check, _delegate_to human rejection, _send_task callback_url,
remote task_id mapping, friendly name lookup."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.server import TaskStore


@pytest.fixture(autouse=True)
def _clean_a2a_state():
    """Ensure a2a_tools module-level state is clean between tests."""
    from qd_evolve.agent import a2a_tools as a2a_module
    a2a_module._task_store.clear()
    a2a_module._transport = None
    yield
    a2a_module._task_store.clear()
    a2a_module._transport = None


# ── on_push_notification ───────────────────────────────────────────

class TestOnPushNotification:
    def test_updates_existing_entry(self):
        from qd_evolve.agent.a2a_tools import on_push_notification
        from qd_evolve.agent import a2a_tools as a2a_module

        a2a_module._task_store["t1"] = {"target": "human", "state": "input_required", "result": None}
        on_push_notification("t1", "completed", "我很好，谢谢")

        assert a2a_module._task_store["t1"]["state"] == "completed"
        assert a2a_module._task_store["t1"]["result"] == "我很好，谢谢"
        a2a_module._task_store.clear()

    def test_ignores_unknown_task_id(self):
        from qd_evolve.agent.a2a_tools import on_push_notification
        from qd_evolve.agent import a2a_tools as a2a_module

        on_push_notification("nonexistent", "completed", "result")
        assert "nonexistent" not in a2a_module._task_store

    def test_updates_failed_state(self):
        from qd_evolve.agent.a2a_tools import on_push_notification
        from qd_evolve.agent import a2a_tools as a2a_module

        a2a_module._task_store["t2"] = {"target": "helper", "state": "submitted", "result": None}
        on_push_notification("t2", "failed", "timeout")

        assert a2a_module._task_store["t2"]["state"] == "failed"
        assert a2a_module._task_store["t2"]["result"] == "timeout"
        a2a_module._task_store.clear()


# ── _check_pending_task_results ────────────────────────────────────

class TestCheckPendingTaskResults:
    def test_finds_completed_tasks(self):
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.a2a_agent import A2AAgent
        from qd_evolve.agent.agent import Agent

        a2a_module._task_store["abc12345xxxx"] = {
            "target": "human", "state": "completed", "result": "我很好"
        }

        mock_agent = MagicMock(spec=Agent)
        mock_agent._template_mgr = None
        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        result = a2a._check_pending_task_results()

        assert "human" in result
        assert "我很好" in result
        a2a_module._task_store.clear()

    def test_empty_when_no_completed(self):
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.a2a_agent import A2AAgent
        from qd_evolve.agent.agent import Agent

        a2a_module._task_store["t1"] = {"target": "human", "state": "input_required", "result": None}

        mock_agent = MagicMock(spec=Agent)
        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        result = a2a._check_pending_task_results()

        assert result == ""
        a2a_module._task_store.clear()

    def test_includes_failed_and_canceled(self):
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.a2a_agent import A2AAgent
        from qd_evolve.agent.agent import Agent

        a2a_module._task_store["t1"] = {"target": "helper", "state": "failed", "result": "error"}
        a2a_module._task_store["t2"] = {"target": "helper", "state": "canceled", "result": "stopped"}

        mock_agent = MagicMock(spec=Agent)
        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        result = a2a._check_pending_task_results()

        assert "helper" in result
        assert "error" in result
        assert "stopped" in result
        a2a_module._task_store.clear()


# ── A2AAgent.heartbeat_check uses a2a-heartbeat template ──────────

class TestA2AHeartbeatCheck:
    def test_uses_a2a_heartbeat_template(self):
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.agent import Agent

        a2a_module._task_store.clear()

        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "heartbeat msg"

        mock_agent = MagicMock(spec=Agent)
        mock_agent._template_mgr = mock_tmpl
        mock_agent.run.return_value = "."

        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        a2a.heartbeat_check(60)

        mock_tmpl.render.assert_called_once()
        call_args = mock_tmpl.render.call_args
        assert call_args[0][0] == "a2a-heartbeat"

    def test_injects_pending_results_into_template(self):
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.agent import Agent

        a2a_module._task_store["t1"] = {"target": "human", "state": "completed", "result": "reply"}

        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "you got a reply"

        mock_agent = MagicMock(spec=Agent)
        mock_agent._template_mgr = mock_tmpl
        mock_agent.run.return_value = "Thanks for the reply"

        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        a2a.heartbeat_check(60)

        mock_tmpl.render.assert_called_once()
        call_args = mock_tmpl.render.call_args
        assert call_args[0][0] == "a2a-heartbeat"
        # heartbeat_check passes idle_seconds and now to template
        call_kwargs = call_args[1]
        assert "idle_seconds" in call_kwargs
        a2a_module._task_store.clear()


# ── A2AAgent.start_heartbeat_loop runs own loop ───────────────────

class TestA2AHeartbeatLoop:
    def test_start_uses_own_heartbeat_check(self):
        """Verify start_heartbeat_loop creates a task that calls A2AAgent.heartbeat_check."""
        from qd_evolve.agent.agent import Agent
        from qd_evolve.core.config import Settings

        mock_agent = MagicMock(spec=Agent)
        mock_agent.settings = MagicMock()
        mock_agent.settings.heartbeat_idle_seconds = 0  # disabled
        mock_agent._hb_task = None

        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        a2a.start_heartbeat_loop()

        # With heartbeat_idle_seconds=0, no task should be created
        assert mock_agent._hb_task is None

    @pytest.mark.asyncio
    async def test_start_creates_async_task_when_enabled(self):
        from qd_evolve.agent.agent import Agent

        mock_agent = MagicMock(spec=Agent)
        mock_agent.settings = MagicMock()
        mock_agent.settings.heartbeat_idle_seconds = 60
        mock_agent._hb_task = None

        a2a = A2AAgent(mock_agent, AgentCard(name="test", description="Test"))
        a2a.start_heartbeat_loop()

        # Should have created an asyncio.Task on the internal agent
        assert mock_agent._hb_task is not None
        # Clean up
        a2a.stop_heartbeat_loop()


# ── _delegate_to rejects human agent ───────────────────────────────

class TestDelegateToHumanRejection:
    def test_rejects_human_agent_inproc(self):
        from qd_evolve.agent.a2a_tools import _delegate_to, set_transport
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.human_agent import HumanAgent
        from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter

        human = HumanAgent(name="human", description="Human agent")

        mock_inproc = MagicMock(spec=InprocTransport)
        mock_http = MagicMock(spec=HttpTransport)
        mock_router = TransportRouter(mock_inproc, mock_http)

        # Make _pick return inproc (agent found locally)
        mock_router._pick = MagicMock(return_value=mock_inproc)
        mock_router._get_registry = MagicMock()

        mock_reg = MagicMock()
        mock_reg.get.return_value = human
        mock_router._get_registry.return_value = mock_reg

        set_transport(mock_router)

        result = _delegate_to("human", "hello")
        assert "Human agents require async communication" in result
        assert "send_task" in result

        a2a_module._transport = None

    def test_rejects_input_required_http(self):
        from qd_evolve.agent.a2a_tools import _delegate_to, set_transport
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter

        mock_inproc = MagicMock(spec=InprocTransport)
        mock_http = MagicMock(spec=HttpTransport)
        mock_router = TransportRouter(mock_inproc, mock_http)

        # Make _pick return http (agent not local)
        mock_router._pick = MagicMock(return_value=mock_http)

        # Mock send_task to return input_required
        async def mock_send(agent, msg):
            return Task(
                id="remote-task",
                status=TaskStatus(state=TaskState.input_required),
            )
        mock_router.send_task = mock_send

        set_transport(mock_router)

        with patch("qd_evolve.agent.a2a_tools._get_current_agent_name", return_value="test-agent"):
            result = _delegate_to("human", "hello")
        assert "Human agent requires async communication" in result
        assert "send_task" in result

        a2a_module._transport = None


# ── _send_task sets callback_url and from_agent ───────────────────

class TestSendTaskMetadata:
    def test_sets_callback_url_and_from_agent(self):
        from qd_evolve.agent.a2a_tools import _send_task, set_transport
        from qd_evolve.agent import a2a_tools as a2a_module

        captured_message = None

        mock_transport = MagicMock()

        async def mock_send(agent, msg):
            nonlocal captured_message
            captured_message = msg
            return Task(
                id="remote-123",
                status=TaskStatus(state=TaskState.input_required),
            )

        mock_transport.send_task = mock_send
        set_transport(mock_transport)

        # Mock registry to provide callback_url and agent name
        with patch("qd_evolve.agent.a2a_tools._get_own_callback_url", return_value="http://localhost:8002"), \
             patch("qd_evolve.agent.a2a_tools._get_current_agent_name", return_value="jack"):
            result = _send_task("human", "hello")

        data = json.loads(result)
        assert data["state"] == "submitted"
        assert data["agent"] == "human"

        # _watch() runs synchronously via asyncio.run() when no loop exists
        assert captured_message is not None
        assert captured_message.metadata.get("callback_url") == "http://localhost:8002"
        assert captured_message.metadata.get("from_agent") == "jack"

        a2a_module._task_store.clear()
        a2a_module._transport = None


# ── Remote task_id mapping in _watch() ────────────────────────────

class TestRemoteTaskIdMapping:
    def test_maps_remote_task_id_to_same_entry(self):
        from qd_evolve.agent.a2a_tools import _send_task, set_transport, on_push_notification
        from qd_evolve.agent import a2a_tools as a2a_module

        mock_transport = MagicMock()

        async def mock_send(agent, msg):
            # Remote server generates its own task_id
            return Task(
                id="remote-abc",
                status=TaskStatus(
                    state=TaskState.input_required,
                    message=make_text_message("agent", "waiting for human"),
                ),
            )

        mock_transport.send_task = mock_send
        set_transport(mock_transport)

        with patch("qd_evolve.agent.a2a_tools._get_own_callback_url", return_value=""), \
             patch("qd_evolve.agent.a2a_tools._get_current_agent_name", return_value=""):
            result = _send_task("human", "hello")

        data = json.loads(result)
        local_task_id = data["task_id"]

        # _watch() runs synchronously via asyncio.run() when no loop exists
        assert local_task_id in a2a_module._task_store
        assert "remote-abc" in a2a_module._task_store
        assert a2a_module._task_store[local_task_id] is a2a_module._task_store["remote-abc"]

        # Now simulate push notification using remote task_id
        on_push_notification("remote-abc", "completed", "我很好")

        # Local task_id should also show completed
        assert a2a_module._task_store[local_task_id]["state"] == "completed"
        assert a2a_module._task_store[local_task_id]["result"] == "我很好"

        a2a_module._task_store.clear()
        a2a_module._transport = None


# ── A2AServer._tasks_push_notification calls on_push_notification ──

class TestServerPushNotification:
    @pytest.mark.asyncio
    async def test_push_notification_updates_task_store(self):
        from qd_evolve.agent import a2a_tools as a2a_module
        from qd_evolve.agent.server import A2AServer

        # Pre-populate _task_store so on_push_notification can find it
        a2a_module._task_store["pn-test-id"] = {
            "target": "human", "state": "input_required", "result": None
        }

        mock_agent = MagicMock()
        mock_agent.card = AgentCard(name="test", description="Test")
        mock_agent.task_store = TaskStore()
        mock_agent._on_task_completed = None
        mock_agent._push_event = MagicMock()
        mock_agent._running = False
        mock_agent._check_pending_task_results.return_value = ""

        server = A2AServer(mock_agent)

        # Patch _forward_to_cli to avoid real HTTP call
        with patch.object(server, "_forward_to_cli", new_callable=AsyncMock):
            task = Task(
                id="pn-test-id",
                status=TaskStatus(
                    state=TaskState.completed,
                    message=make_text_message("agent", "human reply"),
                ),
            )
            params = {"task": task.model_dump()}
            result = await server._tasks_push_notification(params)

        # _task_store should be updated via on_push_notification
        assert a2a_module._task_store["pn-test-id"]["state"] == "completed"
        assert a2a_module._task_store["pn-test-id"]["result"] == "human reply"

        a2a_module._task_store.clear()
