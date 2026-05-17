"""Tests for qd_evolve.agent.a2a — A2A protocol pydantic models and helpers."""

import pytest

from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Artifact,
    AuthInfo,
    FileContent,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    make_text_message,
    make_task_with_text,
)


class TestTaskState:
    def test_all_states(self):
        assert TaskState.submitted == "submitted"
        assert TaskState.working == "working"
        assert TaskState.input_required == "input-required"
        assert TaskState.completed == "completed"
        assert TaskState.canceled == "canceled"
        assert TaskState.failed == "failed"

    def test_is_string_enum(self):
        assert isinstance(TaskState.completed, str)


class TestAgentCard:
    def test_defaults(self):
        card = AgentCard(name="test", description="Test agent")
        assert card.url == ""
        assert card.version == "1.0"
        assert card.capabilities.streaming is False
        assert card.skills == []

    def test_with_skills(self):
        card = AgentCard(
            name="test",
            description="Test",
            skills=[AgentSkill(id="s1", name="skill1", description="A skill")],
        )
        assert len(card.skills) == 1
        assert card.skills[0].name == "skill1"

    def test_serialization(self):
        card = AgentCard(name="test", description="Test", url="http://localhost:8001")
        data = card.model_dump()
        assert data["name"] == "test"
        assert data["url"] == "http://localhost:8001"

    def test_deserialization(self):
        data = {"name": "test", "description": "Test"}
        card = AgentCard.model_validate(data)
        assert card.name == "test"


class TestAgentCapabilities:
    def test_defaults(self):
        cap = AgentCapabilities()
        assert cap.streaming is False
        assert cap.push_notifications is False

    def test_streaming_enabled(self):
        cap = AgentCapabilities(streaming=True)
        assert cap.streaming is True


class TestAgentSkill:
    def test_defaults(self):
        skill = AgentSkill(id="s1", name="skill1", description="A skill")
        assert skill.tags == []
        assert skill.examples == []
        assert skill.input_modes == []
        assert skill.output_modes == []


class TestPart:
    def test_text_part(self):
        part = Part(type="text", text="hello")
        assert part.type == "text"
        assert part.text == "hello"
        assert part.file is None
        assert part.data is None

    def test_file_part(self):
        part = Part(type="file", file=FileContent(name="doc.pdf", mime_type="application/pdf"))
        assert part.file.name == "doc.pdf"

    def test_data_part(self):
        part = Part(type="data", data={"key": "value"})
        assert part.data["key"] == "value"

    def test_default_type(self):
        part = Part()
        assert part.type == "text"


class TestMessage:
    def test_text_message(self):
        msg = Message(role="user", parts=[Part(type="text", text="hello")])
        assert msg.role == "user"
        assert len(msg.parts) == 1
        assert msg.parts[0].text == "hello"

    def test_metadata(self):
        msg = Message(role="user", parts=[], metadata={"source": "test"})
        assert msg.metadata["source"] == "test"

    def test_default_metadata(self):
        msg = Message(role="user", parts=[])
        assert msg.metadata == {}


class TestTaskStatus:
    def test_defaults(self):
        ts = TaskStatus()
        assert ts.state == TaskState.submitted
        assert ts.message is None
        assert ts.timestamp is not None

    def test_with_message(self):
        ts = TaskStatus(state=TaskState.completed, message=make_text_message("agent", "done"))
        assert ts.state == TaskState.completed
        assert ts.message.parts[0].text == "done"


class TestTask:
    def test_defaults(self):
        task = Task()
        assert task.id is not None
        assert len(task.id) > 0
        assert task.session_id is not None
        assert task.status.state == TaskState.submitted
        assert task.history == []
        assert task.artifacts == []
        assert task.metadata == {}

    def test_with_existing_id(self):
        task = Task(id="custom-id")
        assert task.id == "custom-id"

    def test_serialization_roundtrip(self):
        task = Task(id="t1", status=TaskStatus(state=TaskState.completed, message=make_text_message("agent", "result")))
        data = task.model_dump()
        restored = Task.model_validate(data)
        assert restored.id == "t1"
        assert restored.status.state == TaskState.completed


class TestTaskStatusUpdateEvent:
    def test_defaults(self):
        event = TaskStatusUpdateEvent(id="t1", status=TaskStatus(state=TaskState.working))
        assert event.id == "t1"
        assert event.final is False

    def test_final_event(self):
        event = TaskStatusUpdateEvent(id="t1", status=TaskStatus(state=TaskState.completed), final=True)
        assert event.final is True


class TestArtifact:
    def test_defaults(self):
        art = Artifact()
        assert art.name == ""
        assert art.index == 0
        assert art.append is False
        assert art.last_chunk is True


class TestFileContent:
    def test_defaults(self):
        fc = FileContent()
        assert fc.name == ""
        assert fc.mime_type == "application/octet-stream"
        assert fc.bytes is None
        assert fc.uri is None


class TestMakeTextMessage:
    def test_creates_message(self):
        msg = make_text_message("user", "hello world")
        assert msg.role == "user"
        assert len(msg.parts) == 1
        assert msg.parts[0].type == "text"
        assert msg.parts[0].text == "hello world"

    def test_agent_role(self):
        msg = make_text_message("agent", "response")
        assert msg.role == "agent"


class TestMakeTaskWithText:
    def test_creates_task(self):
        task = make_task_with_text("do something")
        assert task.status.state == TaskState.submitted
        assert task.status.message is not None
        assert task.status.message.parts[0].text == "do something"

    def test_with_existing_id(self):
        task = make_task_with_text("do something", existing_task_id="custom-id")
        assert task.id == "custom-id"

    def test_generates_unique_id(self):
        task1 = make_task_with_text("task 1")
        task2 = make_task_with_text("task 2")
        assert task1.id != task2.id