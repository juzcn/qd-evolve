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