"""Tests for qd_evolve.agent.a2a_tools — delegate_to, send_task, get_task, cancel_task."""

import json
from unittest.mock import MagicMock, patch

import pytest

from qd_evolve.agent.a2a import Task, TaskState, TaskStatus, make_text_message


@pytest.fixture(autouse=True)
def _clean_a2a_state():
    """Ensure a2a_tools module-level state is clean between tests."""
    from qd_evolve.agent import a2a_tools as a2a_module
    a2a_module._task_store.clear()
    a2a_module._transport = None
    yield
    a2a_module._task_store.clear()
    a2a_module._transport = None


class TestGetTask:
    def test_existing_task(self):
        from qd_evolve.agent.a2a_tools import _get_task
        from qd_evolve.agent import a2a_tools as a2a_module
        a2a_module._task_store["t1"] = {"target": "helper", "state": "completed", "result": "done"}

        result = _get_task("t1")
        data = json.loads(result)
        assert data["task_id"] == "t1"
        assert data["state"] == "completed"
        assert data["result"] == "done"
        assert data["agent"] == "helper"

        # Cleanup
        a2a_module._task_store.clear()

    def test_not_found(self):
        from qd_evolve.agent.a2a_tools import _get_task
        result = _get_task("nonexistent")
        data = json.loads(result)
        assert "error" in data


class TestCancelTask:
    def test_cancel_existing(self):
        from qd_evolve.agent.a2a_tools import _cancel_task
        from qd_evolve.agent import a2a_tools as a2a_module
        a2a_module._task_store["t1"] = {"target": "helper", "state": "submitted", "result": None}

        result = _cancel_task("t1")
        data = json.loads(result)
        assert data["state"] == "canceled"
        assert a2a_module._task_store["t1"]["state"] == "canceled"

        # Cleanup
        a2a_module._task_store.clear()

    def test_cancel_not_found(self):
        from qd_evolve.agent.a2a_tools import _cancel_task
        result = _cancel_task("nonexistent")
        data = json.loads(result)
        assert "error" in data


class TestExtractResultText:
    def test_with_text(self):
        from qd_evolve.agent.a2a_tools import _extract_result_text
        task = Task(
            status=TaskStatus(
                state=TaskState.completed,
                message=make_text_message("agent", "result text"),
            ),
        )
        assert _extract_result_text(task) == "result text"

    def test_empty_message(self):
        from qd_evolve.agent.a2a_tools import _extract_result_text
        task = Task(status=TaskStatus(state=TaskState.completed))
        assert _extract_result_text(task) == ""

    def test_no_parts(self):
        from qd_evolve.agent.a2a_tools import _extract_result_text
        from qd_evolve.agent.a2a import Message
        task = Task(
            status=TaskStatus(
                state=TaskState.completed,
                message=Message(role="agent", parts=[]),
            ),
        )
        assert _extract_result_text(task) == ""


class TestSetTransport:
    def test_set_and_get(self):
        from qd_evolve.agent.a2a_tools import set_transport, _get_transport
        mock_transport = MagicMock()
        set_transport(mock_transport)
        assert _get_transport() == mock_transport

    def test_not_initialized_raises(self):
        from qd_evolve.agent.a2a_tools import _get_transport
        # Reset transport to None
        from qd_evolve.agent import a2a_tools as a2a_module
        a2a_module._transport = None
        with pytest.raises(RuntimeError, match="transport not initialized"):
            _get_transport()

    def test_set_transport_module_level(self):
        """Verify set_transport sets the module-level _transport."""
        from qd_evolve.agent.a2a_tools import set_transport
        from qd_evolve.agent import a2a_tools as mod
        old = mod._transport
        try:
            mock = MagicMock()
            set_transport(mock)
            assert mod._transport is mock
        finally:
            mod._transport = old


class TestOnPushNotification:
    def test_updates_existing_entry(self):
        from qd_evolve.agent.a2a_tools import on_push_notification
        from qd_evolve.agent import a2a_tools as mod

        mod._task_store["t1"] = {"target": "a", "state": "working", "result": None}
        on_push_notification("t1", "completed", "All done!")
        assert mod._task_store["t1"]["state"] == "completed"
        assert mod._task_store["t1"]["result"] == "All done!"

    def test_ignores_missing_entry(self):
        from qd_evolve.agent.a2a_tools import on_push_notification
        on_push_notification("nonexistent", "completed", "ok")


class TestCancelTaskExtended:
    def test_already_completed(self):
        from qd_evolve.agent.a2a_tools import _cancel_task
        from qd_evolve.agent import a2a_tools as mod

        mod._task_store["t1"] = {"target": "a", "state": "completed", "result": "ok"}
        result = _cancel_task("t1")
        data = json.loads(result)
        assert "already completed" in data["message"]

    def test_already_failed(self):
        from qd_evolve.agent.a2a_tools import _cancel_task
        from qd_evolve.agent import a2a_tools as mod

        mod._task_store["t1"] = {"target": "a", "state": "failed", "result": "err"}
        result = _cancel_task("t1")
        data = json.loads(result)
        assert "already failed" in data["message"]

    def test_already_canceled(self):
        from qd_evolve.agent.a2a_tools import _cancel_task
        from qd_evolve.agent import a2a_tools as mod

        mod._task_store["t1"] = {"target": "a", "state": "canceled", "result": "x"}
        result = _cancel_task("t1")
        data = json.loads(result)
        assert "already canceled" in data["message"]

    def test_with_cancel_token(self):
        from qd_evolve.agent.a2a_tools import _cancel_task
        from qd_evolve.agent import a2a_tools as mod
        from qd_evolve.utils.cancellation import CancellationToken

        token = CancellationToken()
        mod._task_store["t1"] = {
            "target": "a", "state": "working", "result": None,
            "_cancel_token": token,
        }
        result = _cancel_task("t1")
        data = json.loads(result)
        assert data["state"] == "cancelling"
        assert token.is_cancelled is True

    def test_working_no_token_remote_cancel(self):
        from qd_evolve.agent.a2a_tools import _cancel_task, set_transport
        from qd_evolve.agent import a2a_tools as mod

        mock_transport = MagicMock()
        set_transport(mock_transport)

        mod._task_store["t1"] = {"target": "remote", "state": "working", "result": None}
        result = _cancel_task("t1")
        data = json.loads(result)
        assert data["state"] == "cancelling"
        assert "remote" in data["message"]


class TestExtractResultTextExtended:
    def test_multiple_parts_returns_first(self):
        from qd_evolve.agent.a2a_tools import _extract_result_text
        from qd_evolve.agent.a2a import Message, Part

        task = Task(
            status=TaskStatus(
                state=TaskState.completed,
                message=Message(role="agent", parts=[
                    Part(type="text", text="First"),
                    Part(type="text", text="Second"),
                ]),
            ),
        )
        assert _extract_result_text(task) == "First"

    def test_non_text_part_skipped(self):
        from qd_evolve.agent.a2a_tools import _extract_result_text
        from qd_evolve.agent.a2a import Message, Part

        task = Task(
            status=TaskStatus(
                state=TaskState.completed,
                message=Message(role="agent", parts=[Part(type="file", text=None)]),
            ),
        )
        assert _extract_result_text(task) == ""

    def test_part_with_none_text(self):
        from qd_evolve.agent.a2a_tools import _extract_result_text
        from qd_evolve.agent.a2a import Message, Part

        task = Task(
            status=TaskStatus(
                state=TaskState.completed,
                message=Message(role="agent", parts=[Part(type="text", text=None)]),
            ),
        )
        assert _extract_result_text(task) == ""


class TestOwnCallbackUrlHelpers:
    """Test _get_own_callback_url via mocking the agent registry and settings."""

    def _init_registry(self):
        from qd_evolve.agent.registry import AgentRegistry, set_agent_registry
        import qd_evolve.agent.registry as reg_mod

        self._old_reg = reg_mod._registry
        reg = AgentRegistry(current_agent="")
        set_agent_registry(reg)
        return reg

    def _restore_registry(self):
        from qd_evolve.agent.registry import set_agent_registry
        set_agent_registry(self._old_reg) if self._old_reg is not None else None

    def test_current_agent_with_url(self):
        from qd_evolve.agent.a2a_tools import _get_own_callback_url

        reg = self._init_registry()
        try:
            mock_agent = MagicMock()
            mock_agent.card.name = "test_agent"
            mock_agent.card.url = "http://agent:8000"
            reg.register(mock_agent)
            reg.current_agent = "test_agent"
            result = _get_own_callback_url()
            assert "agent:8000" in result
        finally:
            self._restore_registry()

    def test_fallback_cli_port(self):
        from qd_evolve.agent.a2a_tools import _get_own_callback_url

        reg = self._init_registry()
        reg.current_agent = None
        try:
            with patch("qd_evolve.core.config.load_settings") as mock_load:
                mock_settings = MagicMock()
                mock_settings.agents_config.a2a_cli.server.port = 9000
                mock_load.return_value = mock_settings
                result = _get_own_callback_url()
                assert "localhost:9000" in result
        finally:
            self._restore_registry()

    def test_no_port_returns_empty(self):
        from qd_evolve.agent.a2a_tools import _get_own_callback_url

        reg = self._init_registry()
        reg.current_agent = None
        try:
            with patch("qd_evolve.core.config.load_settings") as mock_load:
                mock_settings = MagicMock()
                mock_settings.agents_config.a2a_cli.server.port = 0
                mock_load.return_value = mock_settings
                result = _get_own_callback_url()
                assert result == ""
        finally:
            self._restore_registry()

    def test_current_agent_name(self):
        from qd_evolve.agent.a2a_tools import _get_current_agent_name

        reg = self._init_registry()
        try:
            reg.current_agent = "my_agent"
            assert _get_current_agent_name() == "my_agent"
        finally:
            self._restore_registry()


class TestSendTask:
    def test_send_task_returns_task_id(self):
        from qd_evolve.agent.a2a_tools import _send_task, set_transport
        from qd_evolve.agent import a2a_tools as a2a_module

        mock_transport = MagicMock()
        async def mock_send(agent, msg):
            return Task(status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", "done")))
        mock_transport.send_task = mock_send
        set_transport(mock_transport)

        with patch("qd_evolve.agent.a2a_tools._get_current_agent_name", return_value="test-agent"), \
             patch("qd_evolve.agent.a2a_tools._get_own_callback_url", return_value=""):
            result = _send_task("helper", "do something")
        data = json.loads(result)
        assert "task_id" in data
        assert data["state"] == "submitted"
        assert data["agent"] == "helper"

        # Cleanup
        a2a_module._task_store.clear()
        a2a_module._transport = None