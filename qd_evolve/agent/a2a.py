"""A2A (Agent-to-Agent) protocol data models — follows A2A v1.0 spec."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskState(str, Enum):
    submitted = "submitted"
    working = "working"
    input_required = "input-required"
    completed = "completed"
    canceled = "canceled"
    failed = "failed"


class AgentCapabilities(BaseModel):
    streaming: bool = False
    push_notifications: bool = False


class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = []
    examples: list[str] = []
    input_modes: list[str] = []
    output_modes: list[str] = []


class AuthInfo(BaseModel):
    scheme: str = ""
    credential: str = ""


class AgentCard(BaseModel):
    name: str
    description: str
    url: str = ""
    version: str = "1.0"
    capabilities: AgentCapabilities = AgentCapabilities()
    skills: list[AgentSkill] = []
    default_input_modes: list[str] = Field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text"])
    authentication: list[AuthInfo] = []


class FileContent(BaseModel):
    name: str = ""
    mime_type: str = "application/octet-stream"
    bytes: str | None = None
    uri: str | None = None


class Part(BaseModel):
    type: str = "text"
    text: str | None = None
    file: FileContent | None = None
    data: dict[str, Any] | None = None


class Message(BaseModel):
    role: str  # "user" | "agent"
    parts: list[Part] = []
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStatus(BaseModel):
    state: TaskState = TaskState.submitted
    message: Message | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class Artifact(BaseModel):
    name: str = ""
    description: str = ""
    parts: list[Part] = []
    index: int = 0
    append: bool = False
    last_chunk: bool = True


class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    status: TaskStatus = TaskStatus()
    history: list[Message] = []
    artifacts: list[Artifact] = []
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStatusUpdateEvent(BaseModel):
    id: str
    status: TaskStatus
    final: bool = False


class TaskArtifactUpdateEvent(BaseModel):
    id: str
    artifact: Artifact


def make_text_message(role: str, text: str) -> Message:
    return Message(role=role, parts=[Part(type="text", text=text)])


def make_task_with_text(task_text: str, existing_task_id: str | None = None) -> Task:
    task_id = existing_task_id or uuid4().hex
    return Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.submitted,
            message=make_text_message("user", task_text),
        ),
    )