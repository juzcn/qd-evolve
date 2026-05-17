"""Tests for qd_evolve.agent.transport — InprocTransport, HttpTransport, TransportRouter."""

import pytest

from qd_evolve.agent.a2a import (
    AgentCard,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
)
from qd_evolve.agent.transport import InprocTransport, HttpTransport, TransportRouter, _new_id


class TestInprocTransportPureFunctions:
    def test_extract_text(self):
        msg = make_text_message("user", "hello world")
        text = InprocTransport._extract_text(msg)
        assert text == "hello world"

    def test_extract_text_empty(self):
        msg = Message(role="user", parts=[Part(type="text", text=None)])
        text = InprocTransport._extract_text(msg)
        assert text == ""

    def test_extract_text_file_part(self):
        msg = Message(role="user", parts=[Part(type="file", file=None)])
        text = InprocTransport._extract_text(msg)
        assert text == ""

    def test_error_task(self):
        task = InprocTransport._error_task("target", "not found")
        assert task.status.state == TaskState.failed
        assert task.metadata["target"] == "target"


class TestHttpTransportPureFunctions:
    def test_error_task(self):
        task = HttpTransport._error_task("target", "connection failed")
        assert task.status.state == TaskState.failed
        assert task.metadata["target"] == "target"

    def test_rpc_format(self):
        payload = HttpTransport._rpc("tasks/send", {"message": {"role": "user"}})
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "tasks/send"
        assert payload["params"]["message"]["role"] == "user"
        assert payload["id"] == 1


class TestTransportRouter:
    def test_pick_inproc(self):
        inproc = InprocTransport()
        http = HttpTransport()
        router = TransportRouter(inproc, http)

        # Mock the registry to return "inproc" transport
        from unittest.mock import MagicMock
        mock_reg = MagicMock()
        mock_reg.get_transport.return_value = "inproc"
        router._registry = mock_reg

        transport = router._pick("helper")
        assert transport is inproc

    def test_pick_http(self):
        inproc = InprocTransport()
        http = HttpTransport()
        router = TransportRouter(inproc, http)

        from unittest.mock import MagicMock
        mock_reg = MagicMock()
        mock_reg.get_transport.return_value = "http"
        router._registry = mock_reg

        transport = router._pick("remote")
        assert transport is http


class TestNewId:
    def test_generates_unique_ids(self):
        id1 = _new_id()
        id2 = _new_id()
        assert id1 != id2
        assert len(id1) == 32  # uuid4().hex is 32 chars