"""Tests for qd_evolve.agent.group_chat_transport — _parse_mentions, _group_chat_topic, subscribe/unsubscribe."""

import asyncio
from unittest.mock import MagicMock

import pytest

from qd_evolve.agent.group_chat_transport import (
    GroupChatTransport,
    _group_chat_topic,
    _parse_mentions,
)
from qd_evolve.core.config import MqttConfig


# ── _group_chat_topic ────────────────────────────────────────────────


class TestGroupChatTopic:
    def test_format(self):
        assert _group_chat_topic("agent1") == "$a2a/v1/group/agent1/chat"

    def test_different_names(self):
        assert _group_chat_topic("alice") != _group_chat_topic("bob")


# ── _parse_mentions ──────────────────────────────────────────────────


class TestParseMentions:
    def test_at_all_precedence(self):
        assert _parse_mentions("@all hey @bob", ["bob", "alice"]) == ["all"]

    def test_specific_mention(self):
        assert _parse_mentions("@bob what do you think?", ["bob", "alice"]) == ["bob"]

    def test_multiple_mentions(self):
        result = _parse_mentions("@bob @alice discuss", ["bob", "alice", "carol"])
        assert "bob" in result
        assert "alice" in result
        assert "carol" not in result

    def test_no_mentions(self):
        assert _parse_mentions("hello everyone", ["bob", "alice"]) == []

    def test_at_all_only(self):
        assert _parse_mentions("@all", ["bob"]) == ["all"]

    def test_mention_not_in_members(self):
        assert _parse_mentions("@unknown hello", ["bob", "alice"]) == []


# ── GroupChatTransport subscribe/unsubscribe ────────────────────────


class TestGroupChatTransportSubscribe:
    def test_subscribe_group_chat(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = t.subscribe_group_chat("agent1")
        assert "agent1" in t._group_subscribers
        assert q in t._group_subscribers["agent1"]

    def test_subscribe_all_group_chat(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = t.subscribe_all_group_chat()
        assert q in t._global_subscribers

    def test_unsubscribe_group_chat(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = t.subscribe_group_chat("agent1")
        t.unsubscribe_group_chat("agent1", q)
        assert "agent1" not in t._group_subscribers

    def test_unsubscribe_all_group_chat(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = t.subscribe_all_group_chat()
        t.unsubscribe_all_group_chat(q)
        assert q not in t._global_subscribers

    def test_subscribe_multiple_per_agent(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        t.subscribe_group_chat("agent1")
        t.subscribe_group_chat("agent1")
        assert len(t._group_subscribers["agent1"]) == 2

    def test_unsubscribe_nonexistent_agent(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = asyncio.Queue()
        # Should not raise
        t.unsubscribe_group_chat("nonexistent", q)

    def test_unsubscribe_wrong_queue(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q1 = t.subscribe_group_chat("agent1")
        q2 = asyncio.Queue()
        t.unsubscribe_group_chat("agent1", q2)
        assert q1 in t._group_subscribers["agent1"]


# ── GroupChatTransport initial state ────────────────────────────────


class TestGroupChatTransportInit:
    def test_initial_state(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        assert t._broker_host == "localhost"
        assert t._broker_port == 1883
        assert t._client_name == "test"
        assert t._group_client is None
        assert t._connected is False
        assert t._listener_task is None
        assert t._group_subscribers == {}
        assert t._global_subscribers == []


# ── GroupChatTransport dispatch ──────────────────────────────────────


class TestGroupChatTransportDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_to_agent_subscriber(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = t.subscribe_group_chat("agent1")
        msg = {"from_agent": "agent1", "content": "hello", "msg_id": "abc"}
        await q.put(msg)
        assert q.get_nowait() == msg

    @pytest.mark.asyncio
    async def test_dispatch_to_global_subscriber(self):
        mock_transport = MagicMock()
        config = MqttConfig()
        t = GroupChatTransport(mock_transport, "localhost", 1883, config, "test")
        q = t.subscribe_all_group_chat()
        msg = {"from_agent": "agent1", "content": "hello", "msg_id": "abc"}
        await q.put(msg)
        assert q.get_nowait() == msg