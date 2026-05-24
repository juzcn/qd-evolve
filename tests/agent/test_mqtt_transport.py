"""Tests for qd_evolve.agent.mqtt_transport — pure functions and registry logic."""

import pytest

from qd_evolve.agent.a2a import (
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.agent.mqtt_transport import (
    _discovery_topic,
    _event_topic,
    _new_req_id,
    _request_topic,
    _response_topic,
    _rpc_request,
    _build_tls_params,
    MqttTransport,
)
from qd_evolve.core.config import MqttConfig


# ── Topic helpers ────────────────────────────────────────────────────


class TestDiscoveryTopic:
    def test_format(self):
        assert _discovery_topic("helper") == "$a2a/v1/discovery/helper"

    def test_empty_name(self):
        assert _discovery_topic("") == "$a2a/v1/discovery/"


class TestRequestTopic:
    def test_format(self):
        assert _request_topic("agent1") == "$a2a/v1/request/agent1"


class TestResponseTopic:
    def test_default_wildcard(self):
        assert _response_topic("cli") == "$a2a/v1/response/cli/+"

    def test_specific_req_id(self):
        assert _response_topic("cli", "abc123") == "$a2a/v1/response/cli/abc123"


class TestEventTopic:
    def test_format(self):
        assert _event_topic("agent1") == "$a2a/v1/event/agent1"


# ── JSON-RPC helper ─────────────────────────────────────────────────


class TestRpcRequest:
    def test_format(self):
        rpc = _rpc_request("message/send", {"message": {"role": "user"}}, "r1")
        assert rpc["jsonrpc"] == "2.0"
        assert rpc["method"] == "message/send"
        assert rpc["params"] == {"message": {"role": "user"}}
        assert rpc["id"] == "r1"


# ── _new_req_id ──────────────────────────────────────────────────────


class TestNewReqId:
    def test_unique(self):
        ids = {_new_req_id() for _ in range(100)}
        assert len(ids) == 100

    def test_hex_format(self):
        rid = _new_req_id()
        assert len(rid) == 32
        assert all(c in "0123456789abcdef" for c in rid)


# ── _build_tls_params ────────────────────────────────────────────────


class TestBuildTlsParams:
    def test_none_when_no_tls(self):
        config = MqttConfig()
        assert _build_tls_params(config) is None

    def test_returns_tls_when_ca_certs(self):
        config = MqttConfig(ca_certs="/path/to/ca.pem")
        result = _build_tls_params(config)
        assert result is not None

    def test_returns_tls_when_certfile(self):
        config = MqttConfig(certfile="/path/to/cert.pem", keyfile="/path/to/key.pem")
        result = _build_tls_params(config)
        assert result is not None


# ── _extract_text ────────────────────────────────────────────────────


class TestMqttTransportExtractText:
    def test_extracts_text_part(self):
        msg = Message(role="user", parts=[Part(type="text", text="hello")])
        assert MqttTransport._extract_text(msg) == "hello"

    def test_empty_when_no_text(self):
        msg = Message(role="user", parts=[Part(type="file", file=None)])
        assert MqttTransport._extract_text(msg) == ""

    def test_empty_when_no_parts(self):
        msg = Message(role="user", parts=[])
        assert MqttTransport._extract_text(msg) == ""

    def test_extracts_first_text(self):
        msg = Message(role="user", parts=[
            Part(type="text", text="first"),
            Part(type="text", text="second"),
        ])
        assert MqttTransport._extract_text(msg) == "first"


# ── _extract_task_text ───────────────────────────────────────────────


class TestMqttTransportExtractTaskText:
    def test_extracts_from_task_status(self):
        task = make_task_with_text("hello world")
        assert MqttTransport._extract_task_text(task) == "hello world"

    def test_empty_when_no_message(self):
        task = Task(status=TaskStatus(state=TaskState.working))
        assert MqttTransport._extract_task_text(task) == ""


# ── _error_task ──────────────────────────────────────────────────────


class TestMqttTransportErrorTask:
    def test_failed_state(self):
        task = MqttTransport._error_task("helper", "connection refused")
        assert task.status.state == TaskState.failed
        assert task.metadata["target"] == "helper"

    def test_message_content(self):
        task = MqttTransport._error_task("agent1", "timeout")
        text = MqttTransport._extract_task_text(task)
        assert "timeout" in text


# ── MqttTransport.__init__ ──────────────────────────────────────────


class TestMqttTransportInit:
    def test_initial_state(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "test-cli")
        assert t._broker_host == "localhost"
        assert t._broker_port == 1883
        assert t._client_name == "test-cli"
        assert t._client is None
        assert t._connected is False
        assert t._listener_task is None
        assert t._loop is None
        assert t._pending == {}
        assert t._event_subscribers == {}
        assert t._discovery_subscribers == []
        assert t._online_subscribers == {}
        assert t._last_discovery_status == {}


# ── Event subscriber registry ────────────────────────────────────────


class TestMqttTransportEventSubscribers:
    def test_subscribe(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        q = t.subscribe_agent_events("agent1")
        assert "agent1" in t._event_subscribers
        assert q in t._event_subscribers["agent1"]

    def test_subscribe_multiple(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        q1 = t.subscribe_agent_events("agent1")
        q2 = t.subscribe_agent_events("agent1")
        assert len(t._event_subscribers["agent1"]) == 2

    def test_unsubscribe(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        q = t.subscribe_agent_events("agent1")
        t.unsubscribe_agent_events("agent1", q)
        assert "agent1" not in t._event_subscribers

    def test_unsubscribe_wrong_queue(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        q1 = t.subscribe_agent_events("agent1")
        q2 = t.subscribe_agent_events("agent1")
        t.unsubscribe_agent_events("agent1", q2)
        assert q1 in t._event_subscribers["agent1"]


# ── Discovery subscriber registry ─────────────────────────────────────


class TestMqttTransportDiscoverySubscribers:
    @pytest.mark.asyncio
    async def test_subscribe(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        q = await t.subscribe_discovery()
        assert q in t._discovery_subscribers

    def test_unsubscribe(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        import asyncio
        q = asyncio.Queue()
        t._discovery_subscribers.append(q)
        t.unsubscribe_discovery(q)
        assert q not in t._discovery_subscribers


# ── is_online / get_agent_status without client ─────────────────────


class TestMqttTransportStatusNoClient:
    @pytest.mark.asyncio
    async def test_get_agent_status_unknown(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        assert await t.get_agent_status("agent1") == "unknown"

    @pytest.mark.asyncio
    async def test_is_online_false(self):
        config = MqttConfig()
        t = MqttTransport("localhost", 1883, config, "cli")
        assert await t.is_online("agent1") is False