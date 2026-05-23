"""Tests for qd_evolve.agent.human_agent — HumanAgent logic."""

import asyncio

import pytest

from qd_evolve.agent.human_agent import HumanAgent
from qd_evolve.agent.a2a import TaskState


@pytest.fixture
def human():
    return HumanAgent(name="human", description="Test human agent")


class TestHumanAgentInit:
    def test_card_name(self, human):
        assert human.card.name == "human"

    def test_card_description(self, human):
        assert human.card.description == "Test human agent"

    def test_card_capabilities(self, human):
        assert human.card.capabilities.streaming is True
        assert human.card.capabilities.push_notifications is True

    def test_task_store(self, human):
        assert human.task_store is not None

    def test_event_subscribers_empty(self, human):
        assert human._event_subscribers == []


class TestHumanAgentRun:
    def test_returns_placeholder(self, human):
        result = human.run("hello")
        assert "send_task" in result

    def test_pushes_human_task_event(self, human):
        q = human.subscribe_events()
        human.run("hello")
        event = q.get_nowait()
        assert event["type"] == "human_task"
        assert event["content"] == "hello"
        human.unsubscribe_events(q)


class TestHumanAgentEvents:
    def test_subscribe_and_push(self, human):
        q = human.subscribe_events()
        human._push_event({"type": "test", "data": "hello"})
        event = q.get_nowait()
        assert event["type"] == "test"
        human.unsubscribe_events(q)

    def test_unsubscribe(self, human):
        q = human.subscribe_events()
        human.unsubscribe_events(q)
        human._push_event({"type": "test"})
        assert q.empty()

    def test_multiple_subscribers(self, human):
        q1 = human.subscribe_events()
        q2 = human.subscribe_events()
        human._push_event({"type": "ping"})
        assert q1.get_nowait()["type"] == "ping"
        assert q2.get_nowait()["type"] == "ping"
        human.unsubscribe_events(q1)
        human.unsubscribe_events(q2)


class TestHumanAgentReceiveTask:
    def test_creates_input_required_task(self, human):
        human.receive_task("task-1", "What is 2+2?", from_agent="math_agent")
        task = human.task_store.get("task-1")
        assert task is not None
        assert task.status.state == TaskState.input_required

    def test_stores_callback_url(self, human):
        human.receive_task("task-2", "hello", callback_url="http://localhost:8080/callback")
        task = human.task_store.get("task-2")
        assert task.metadata["callback_url"] == "http://localhost:8080/callback"

    def test_pushes_human_task_event(self, human):
        q = human.subscribe_events()
        human.receive_task("task-3", "hello", from_agent="agent1")
        event = q.get_nowait()
        assert event["type"] == "human_task"
        assert event["task_id"] == "task-3"
        assert event["content"] == "hello"
        assert event["from_agent"] == "agent1"
        human.unsubscribe_events(q)


class TestHumanAgentCompleteTask:
    def test_completes_task(self, human):
        human.receive_task("task-4", "What is 2+2?")
        human.complete_task("task-4", "4")
        task = human.task_store.get("task-4")
        assert task.status.state == TaskState.completed

    def test_pushes_task_completed_event(self, human):
        q = human.subscribe_events()
        human.receive_task("task-5", "hello")
        human.complete_task("task-5", "world")
        # First event is from receive_task, second from complete_task
        q.get_nowait()  # skip receive_task event
        event = q.get_nowait()
        assert event["type"] == "task_completed"
        assert event["task_id"] == "task-5"
        assert event["content"] == "world"
        human.unsubscribe_events(q)

    def test_missing_task_no_error(self, human):
        # Should not raise, just log warning
        human.complete_task("nonexistent", "response")


class TestHumanAgentHeartbeat:
    def test_start_with_zero_seconds(self, human):
        human.start_heartbeat_loop(idle_seconds=0)
        assert human._hb_task is None

    def test_start_sets_task(self, human):
        human.start_heartbeat_loop(idle_seconds=60)
        assert human._hb_task is not None
        # Clean up
        human.stop_heartbeat_loop()

    def test_stop_without_start(self, human):
        # Should not raise
        human.stop_heartbeat_loop()
