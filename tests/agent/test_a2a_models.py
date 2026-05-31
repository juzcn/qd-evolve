"""Tests for qd_evolve.agent.a2a — A2A protocol pydantic models and helpers."""


from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    AgentExtension,
    AgentProvider,
    AgentSkill,
    Artifact,
    FileContent,
    Message,
    Part,
    StreamResponse,
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


class TestAgentProvider:
    def test_defaults(self):
        p = AgentProvider()
        assert p.url == ""
        assert p.name == ""
        assert p.organization == ""

    def test_with_values(self):
        p = AgentProvider(name="openai", url="https://api.openai.com", organization="OpenAI")
        assert p.name == "openai"
        assert p.organization == "OpenAI"


class TestAgentExtension:
    def test_minimal(self):
        e = AgentExtension(uri="x-test")
        assert e.uri == "x-test"
        assert e.description == ""
        assert e.params == {}

    def test_with_params(self):
        e = AgentExtension(uri="x-qd-evolve-status", description="Runtime status", params={"provider": "test", "model": "gpt-4"})
        assert e.params["provider"] == "test"
        assert e.params["model"] == "gpt-4"


class TestAgentCard:
    def test_defaults(self):
        card = AgentCard(name="test", description="Test agent")
        assert card.url == ""
        assert card.version == "1.0"
        assert card.capabilities.streaming is False
        assert card.skills == []
        assert card.provider.name == ""
        assert card.extensions == []

    def test_with_skills(self):
        card = AgentCard(
            name="test",
            description="Test",
            skills=[AgentSkill(id="s1", name="skill1", description="A skill")],
        )
        assert len(card.skills) == 1
        assert card.skills[0].name == "skill1"

    def test_with_provider(self):
        card = AgentCard(
            name="test",
            description="Test",
            provider=AgentProvider(name="openai", url="https://api.openai.com"),
        )
        assert card.provider.name == "openai"
        assert card.provider.url == "https://api.openai.com"

    def test_with_extensions(self):
        card = AgentCard(
            name="test",
            description="Test",
            extensions=[AgentExtension(uri="x-qd-evolve-status", params={"provider": "test"})],
        )
        assert len(card.extensions) == 1
        assert card.extensions[0].uri == "x-qd-evolve-status"
        assert card.extensions[0].params["provider"] == "test"

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
        assert cap.extended_agent_card is False

    def test_streaming_enabled(self):
        cap = AgentCapabilities(streaming=True)
        assert cap.streaming is True

    def test_extended_agent_card_enabled(self):
        cap = AgentCapabilities(extended_agent_card=True)
        assert cap.extended_agent_card is True


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
        event = TaskStatusUpdateEvent(task_id="t1", status=TaskStatus(state=TaskState.working))
        assert event.task_id == "t1"
        assert event.final is False
        assert event.metadata == {}

    def test_final_event(self):
        event = TaskStatusUpdateEvent(task_id="t1", status=TaskStatus(state=TaskState.completed), final=True)
        assert event.final is True

    def test_with_metadata(self):
        event = TaskStatusUpdateEvent(
            task_id="t1", context_id="ctx1",
            status=TaskStatus(state=TaskState.working),
            metadata={"type": "iteration", "num": 1},
        )
        assert event.metadata["type"] == "iteration"
        assert event.context_id == "ctx1"


class TestStreamResponse:
    def test_with_task(self):
        task = Task(id="t1")
        sr = StreamResponse(task=task)
        assert sr.task is not None
        assert sr.task.id == "t1"
        assert sr.statusUpdate is None

    def test_with_status_update(self):
        event = TaskStatusUpdateEvent(task_id="t1", status=TaskStatus(state=TaskState.working))
        sr = StreamResponse(statusUpdate=event)
        assert sr.statusUpdate is not None
        assert sr.task is None

    def test_serialization(self):
        event = TaskStatusUpdateEvent(
            task_id="t1", context_id="ctx1",
            status=TaskStatus(state=TaskState.working),
            metadata={"type": "status", "text": "Tool: echo(hi)"},
        )
        sr = StreamResponse(statusUpdate=event)
        data = sr.model_dump()
        assert data["statusUpdate"]["metadata"]["type"] == "status"

    def test_empty(self):
        sr = StreamResponse()
        assert sr.task is None
        assert sr.message is None
        assert sr.statusUpdate is None
        assert sr.artifactUpdate is None


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