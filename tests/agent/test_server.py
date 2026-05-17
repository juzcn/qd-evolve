"""Tests for qd_evolve.agent.server — TaskStore, A2AServer."""

import asyncio

import pytest

from qd_evolve.agent.a2a import (
    AgentCard,
    AgentCapabilities,
    AgentExtension,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
    make_text_message,
    make_task_with_text,
)
from qd_evolve.agent.server import A2AServer, TaskStore


class TestTaskStore:
    def test_put_and_get(self):
        store = TaskStore()
        task = make_task_with_text("hello")
        store.put(task)
        assert store.get(task.id) is not None
        assert store.get(task.id).id == task.id

    def test_get_not_found(self):
        store = TaskStore()
        assert store.get("nonexistent") is None

    def test_update_state(self):
        store = TaskStore()
        task = make_task_with_text("hello")
        store.put(task)
        updated = store.update_state(task.id, TaskState.completed, make_text_message("agent", "done"))
        assert updated is not None
        assert updated.status.state == TaskState.completed

    def test_update_state_not_found(self):
        store = TaskStore()
        result = store.update_state("nonexistent", TaskState.completed)
        assert result is None

    def test_overwrite_on_put(self):
        store = TaskStore()
        task = make_task_with_text("hello")
        store.put(task)
        task.status = TaskStatus(state=TaskState.working)
        store.put(task)
        assert store.get(task.id).status.state == TaskState.working


class TestA2AServer:
    def test_init(self):
        mock_agent = type("MockAgent", (), {"card": AgentCard(name="test", description="Test")})()
        server = A2AServer(mock_agent, AgentCard(name="test", description="Test"))
        assert server.card.name == "test"

    def test_extract_text(self):
        msg = make_text_message("user", "hello")
        text = A2AServer._extract_text(msg)
        assert text == "hello"

    def test_extract_text_empty(self):
        msg = Message(role="user", parts=[])
        text = A2AServer._extract_text(msg)
        assert text == ""

    def test_error_response(self):
        err = A2AServer._error(-32601, "Method not found", req_id=1)
        assert err["jsonrpc"] == "2.0"
        assert err["error"]["code"] == -32601
        assert err["id"] == 1

    def test_error_parse_error(self):
        err = A2AServer._error(-32700, "Parse error")
        assert err["error"]["code"] == -32700


class TestA2AServerRPC:
    """Test JSON-RPC request handling via aiohttp test client."""

    @pytest.fixture
    def server_app(self):
        """Create an aiohttp app with A2AServer routes."""
        from aiohttp import web
        from unittest.mock import MagicMock

        mock_agent = MagicMock()
        mock_agent.card = AgentCard(name="test", description="Test agent")
        mock_agent.run.return_value = "Hello from agent"
        mock_agent.subscribe_events.return_value = asyncio.Queue()
        mock_agent.task_store = TaskStore()
        mock_agent._provider_name = "test"
        mock_agent._model = "test-model"
        mock_agent._always_active = {"echo"}
        mock_agent._active_tools = {"echo", "search"}
        mock_agent._preload_skills = set()
        mock_agent._loaded_skill_names = set()
        mock_agent._preload_cli = set()
        mock_agent._loaded_cli_names = set()

        server = A2AServer(mock_agent, mock_agent.card)
        app = web.Application()
        app.router.add_get("/.well-known/agent.json", server._handle_agent_card)
        app.router.add_post("/", server._handle_rpc)
        return app, server

    @pytest.mark.asyncio
    async def test_agent_card_endpoint(self, server_app, aiohttp_client):
        app, _ = server_app
        client = await aiohttp_client(app)
        resp = await client.get("/.well-known/agent.json")
        assert resp.status == 200
        data = await resp.json()
        assert data["name"] == "test"

    @pytest.mark.asyncio
    async def test_tasks_get_not_found(self, server_app, aiohttp_client):
        app, _ = server_app
        client = await aiohttp_client(app)
        payload = {"jsonrpc": "2.0", "method": "tasks/get", "params": {"id": "nonexistent"}, "id": 1}
        resp = await client.post("/", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"]["state"] == "failed"

    @pytest.mark.asyncio
    async def test_unknown_method(self, server_app, aiohttp_client):
        app, _ = server_app
        client = await aiohttp_client(app)
        payload = {"jsonrpc": "2.0", "method": "unknown/method", "params": {}, "id": 1}
        resp = await client.post("/", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_message_send(self, server_app, aiohttp_client):
        app, server = server_app
        client = await aiohttp_client(app)
        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}},
            "id": 2,
        }
        resp = await client.post("/", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_tasks_cancel(self, server_app, aiohttp_client):
        app, server = server_app
        # First create a task
        task = make_task_with_text("hello")
        server.task_store.put(task)
        client = await aiohttp_client(app)
        payload = {
            "jsonrpc": "2.0",
            "method": "tasks/cancel",
            "params": {"id": task.id},
            "id": 3,
        }
        resp = await client.post("/", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"]["state"] == "canceled"

    @pytest.mark.asyncio
    async def test_get_extended_agent_card(self, server_app, aiohttp_client):
        app, _ = server_app
        client = await aiohttp_client(app)
        payload = {"jsonrpc": "2.0", "method": "agent/getExtendedAgentCard", "params": {}, "id": 4}
        resp = await client.post("/", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert "result" in data
        card = data["result"]
        assert card["capabilities"]["extended_agent_card"] is True
        exts = card.get("extensions", [])
        status_exts = [e for e in exts if e["uri"] == "x-qd-evolve-status"]
        assert len(status_exts) == 1
        assert "provider" in status_exts[0]["params"]

    @pytest.mark.asyncio
    async def test_agent_card_has_extended_flag(self, server_app, aiohttp_client):
        app, _ = server_app
        client = await aiohttp_client(app)
        resp = await client.get("/.well-known/agent.json")
        assert resp.status == 200
        data = await resp.json()
        assert data["capabilities"]["extended_agent_card"] is True


class TestGetExtendedAgentCardUnit:
    def test_builds_extended_card(self):
        from unittest.mock import MagicMock

        mock_agent = MagicMock()
        mock_agent.card = AgentCard(name="helper", description="Helper agent")
        mock_agent._provider_name = "openai"
        mock_agent._model = "gpt-4"
        mock_agent._always_active = {"echo", "fetch"}
        mock_agent._active_tools = {"echo", "fetch", "search"}
        mock_agent._preload_skills = {"coding"}
        mock_agent._loaded_skill_names = {"coding", "debugging"}
        mock_agent._preload_cli = {"git"}
        mock_agent._loaded_cli_names = {"git", "docker"}

        server = A2AServer(mock_agent, mock_agent.card)
        card = server._get_extended_agent_card()

        assert card.capabilities.extended_agent_card is True
        assert len(card.extensions) == 1
        ext = card.extensions[0]
        assert ext.uri == "x-qd-evolve-status"
        assert ext.params["provider"] == "openai"
        assert ext.params["model"] == "gpt-4"
        assert "echo" in ext.params["preload_tools"]
        assert "search" in ext.params["loaded_tools"]
        assert "coding" in ext.params["preload_skills"]
        assert "debugging" in ext.params["loaded_skills"]
        assert "git" in ext.params["preload_cli"]
        assert "docker" in ext.params["loaded_cli"]