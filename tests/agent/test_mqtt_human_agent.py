"""Tests for qd_evolve.agent.mqtt_human_agent — delegation, _extract_text, receive_task, complete_task."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from qd_evolve.agent.a2a import Message, Part, TaskState
from qd_evolve.agent.human_agent import HumanAgent
from qd_evolve.agent.mqtt_human_agent import MqttHumanAgent
from qd_evolve.core.config import MqttConfig


def _make_mqtt_human():
    """Create an MqttHumanAgent wrapping a real HumanAgent."""
    human = HumanAgent(name="human_user", description="Human agent")
    config = MqttConfig()
    mqtt_human = MqttHumanAgent(human, "localhost", 1883, config)
    # Mock the MQTT client to avoid real connections
    mqtt_human._client = MagicMock()
    mqtt_human._client.publish = AsyncMock()
    mqtt_human._connected = False
    return mqtt_human, human


# ── _extract_text ──────────────────────────────────────────────────


class TestMqttHumanExtractText:
    def test_extracts_text(self):
        msg = Message(role="user", parts=[Part(type="text", text="hello")])
        assert MqttHumanAgent._extract_text(msg) == "hello"

    def test_empty_when_no_text(self):
        msg = Message(role="user", parts=[Part(type="file", file=None)])
        assert MqttHumanAgent._extract_text(msg) == ""

    def test_empty_when_no_parts(self):
        msg = Message(role="user", parts=[])
        assert MqttHumanAgent._extract_text(msg) == ""


# ── Delegation properties ──────────────────────────────────────────


class TestMqttHumanDelegation:
    def test_card_delegates(self):
        mqtt_human, human = _make_mqtt_human()
        assert mqtt_human.card is human.card
        assert mqtt_human.card.name == "human_user"

    def test_task_store_delegates(self):
        mqtt_human, human = _make_mqtt_human()
        assert mqtt_human.task_store is human.task_store

    def test_run_delegates(self):
        mqtt_human, human = _make_mqtt_human()
        result = mqtt_human.run("hello")
        # HumanAgent.run returns a prompt message
        assert isinstance(result, str)

    def test_subscribe_events_delegates(self):
        mqtt_human, human = _make_mqtt_human()
        q = mqtt_human.subscribe_events()
        assert isinstance(q, asyncio.Queue)

    def test_unsubscribe_events_delegates(self):
        mqtt_human, human = _make_mqtt_human()
        q = mqtt_human.subscribe_events()
        mqtt_human.unsubscribe_events(q)
        # Queue removed from subscribers


# ── receive_task ───────────────────────────────────────────────────


class TestMqttHumanReceiveTask:
    def test_stores_from_agent_in_metadata(self):
        mqtt_human, human = _make_mqtt_human()
        mqtt_human.receive_task("task-1", "hello", from_agent="agent1")
        task = human.task_store.get("task-1")
        assert task is not None
        assert task.metadata.get("from_agent") == "agent1"

    def test_task_is_input_required(self):
        mqtt_human, human = _make_mqtt_human()
        mqtt_human.receive_task("task-2", "question?", from_agent="helper")
        task = human.task_store.get("task-2")
        assert task is not None
        assert task.status.state == TaskState.input_required

    def test_receive_task_without_from_agent(self):
        mqtt_human, human = _make_mqtt_human()
        mqtt_human.receive_task("task-3", "hello")
        task = human.task_store.get("task-3")
        assert task is not None
        # No from_agent in metadata
        assert task.metadata.get("from_agent") is None


# ── complete_task ──────────────────────────────────────────────────


class TestMqttHumanCompleteTask:
    def test_completes_task_sync(self):
        mqtt_human, human = _make_mqtt_human()
        mqtt_human.receive_task("task-4", "hello", from_agent="agent1")
        # Direct sync call to HumanAgent.complete_task
        human.complete_task("task-4", "world")
        task = human.task_store.get("task-4")
        assert task is not None
        assert task.status.state == TaskState.completed

    @pytest.mark.asyncio
    async def test_complete_task_async_publishes(self):
        mqtt_human, human = _make_mqtt_human()
        mqtt_human.receive_task("task-5", "hello", from_agent="agent1")
        await mqtt_human.complete_task("task-5", "world")

        task = human.task_store.get("task-5")
        assert task is not None
        assert task.status.state == TaskState.completed
        mqtt_human._client.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_task_no_publish_without_from_agent(self):
        mqtt_human, human = _make_mqtt_human()
        mqtt_human.receive_task("task-6", "hello")  # no from_agent
        await mqtt_human.complete_task("task-6", "world")

        task = human.task_store.get("task-6")
        assert task is not None
        assert task.status.state == TaskState.completed
        mqtt_human._client.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_task_no_publish_for_missing_task(self):
        mqtt_human, human = _make_mqtt_human()
        await mqtt_human.complete_task("nonexistent", "response")
        mqtt_human._client.publish.assert_not_called()