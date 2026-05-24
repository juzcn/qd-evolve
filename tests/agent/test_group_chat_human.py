"""Tests for qd_evolve.agent.group_chat_human — _listen_group_chat, publish_human_input, delegation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qd_evolve.agent.a2a import AgentCard
from qd_evolve.agent.group_chat_human import GroupChatHuman
from qd_evolve.agent.group_chat_transport import GroupChatTransport
from qd_evolve.core.config import MqttConfig


def _make_group_chat_human():
    """Create a GroupChatHuman with mocked MqttHumanAgent and transport."""
    mock_human = MagicMock()
    mock_human.card = AgentCard(name="human_user", description="Human")
    mock_human.start = AsyncMock()
    mock_human.stop = AsyncMock()

    mock_transport = MagicMock(spec=GroupChatTransport)
    mock_transport.subscribe_all_group_chat = MagicMock(return_value=asyncio.Queue())
    mock_transport.unsubscribe_all_group_chat = MagicMock()
    mock_transport.publish_group_chat = AsyncMock(return_value="msg-id-123")

    members = ["human_user", "agent1", "agent2"]
    gch = GroupChatHuman(mock_human, mock_transport, members)
    return gch, mock_human, mock_transport


# ── Delegation properties ──────────────────────────────────────────


class TestGroupChatHumanDelegation:
    def test_card_delegates(self):
        gch, mock_human, _ = _make_group_chat_human()
        assert gch.card is mock_human.card
        assert gch.card.name == "human_user"

    def test_event_queue_exists(self):
        gch, _, _ = _make_group_chat_human()
        assert isinstance(gch.event_queue, asyncio.Queue)


# ── publish_human_input ────────────────────────────────────────────


class TestPublishHumanInput:
    @pytest.mark.asyncio
    async def test_calls_transport_publish(self):
        gch, _, mock_transport = _make_group_chat_human()
        result = await gch.publish_human_input("hello @agent1")
        mock_transport.publish_group_chat.assert_called_once()
        call_args = mock_transport.publish_group_chat.call_args
        assert call_args[0][0] == "human_user"  # from_agent
        assert call_args[0][1] == "hello @agent1"  # content
        assert result == "msg-id-123"

    @pytest.mark.asyncio
    async def test_mentions_parsed(self):
        gch, _, mock_transport = _make_group_chat_human()
        await gch.publish_human_input("@agent1 check this")
        call_args = mock_transport.publish_group_chat.call_args
        mentions = call_args[0][2]  # mentions arg
        assert "agent1" in mentions

    @pytest.mark.asyncio
    async def test_at_all_mentions(self):
        gch, _, mock_transport = _make_group_chat_human()
        await gch.publish_human_input("@all meeting now")
        call_args = mock_transport.publish_group_chat.call_args
        mentions = call_args[0][2]
        assert "all" in mentions

    @pytest.mark.asyncio
    async def test_no_mentions(self):
        gch, _, mock_transport = _make_group_chat_human()
        await gch.publish_human_input("just chatting")
        call_args = mock_transport.publish_group_chat.call_args
        mentions = call_args[0][2]
        assert mentions == []


# ── _listen_group_chat ────────────────────────────────────────────


class TestListenGroupChat:
    @pytest.mark.asyncio
    async def test_skips_own_messages(self):
        gch, mock_human, mock_transport = _make_group_chat_human()
        gch._global_queue = asyncio.Queue()
        # Push own message
        await gch._global_queue.put({
            "from_agent": "human_user",
            "content": "my own message",
            "msg_id": "m1",
        })
        # Push other agent's message
        await gch._global_queue.put({
            "from_agent": "agent1",
            "content": "hello from agent1",
            "msg_id": "m2",
        })

        # Start listener and collect events
        gch._group_listener_task = asyncio.create_task(gch._listen_group_chat())

        # Wait for the agent1 message to appear in event_queue
        event = await asyncio.wait_for(gch.event_queue.get(), timeout=2.0)
        assert event["type"] == "group_message"
        assert event["from_agent"] == "agent1"

        # Cancel listener
        gch._group_listener_task.cancel()
        try:
            await gch._group_listener_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_includes_mentions(self):
        gch, _, _ = _make_group_chat_human()
        gch._global_queue = asyncio.Queue()
        await gch._global_queue.put({
            "from_agent": "agent1",
            "content": "@human_user hello",
            "msg_id": "m1",
            "mentions": ["human_user"],
        })

        gch._group_listener_task = asyncio.create_task(gch._listen_group_chat())
        event = await asyncio.wait_for(gch.event_queue.get(), timeout=2.0)
        assert event["mentions"] == ["human_user"]

        gch._group_listener_task.cancel()
        try:
            await gch._group_listener_task
        except asyncio.CancelledError:
            pass


# ── Lifecycle ──────────────────────────────────────────────────────


class TestGroupChatHumanLifecycle:
    @pytest.mark.asyncio
    async def test_start_calls_agent_start(self):
        gch, mock_human, mock_transport = _make_group_chat_human()
        await gch.start()
        mock_human.start.assert_called_once()
        mock_transport.subscribe_all_group_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_calls_agent_stop(self):
        gch, mock_human, mock_transport = _make_group_chat_human()
        await gch.start()
        await gch.stop()
        mock_human.stop.assert_called_once()
        mock_transport.unsubscribe_all_group_chat.assert_called_once()