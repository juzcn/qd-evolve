"""Tests for qd_evolve.agent.agent — Agent run logic, memory, heartbeat, formatting."""

import asyncio
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from qd_evolve.agent.agent import Agent
from qd_evolve.core.config import (
    AgentEntry, EmbeddingsBackend, MemorySearchConfig,
    ModelConfig, ProviderConfig, Settings,
)
from qd_evolve.core.providers import ProviderRegistry
from qd_evolve.core.registry import ToolRegistry


@pytest.fixture
def agent_core(minimal_settings, registry_with_echo, providers):
    """Agent with echo tool — no LLM calls."""
    agent = Agent(
        settings=minimal_settings,
        registry=registry_with_echo,
        providers=providers,
        default_system_prompt="You are a test agent.",
    )
    agent._provider_name = "test"
    agent._model = "test-model"
    return agent


def _make_mock_prov(text="response", raise_on_call=None, reasoning=False):
    """Build a mock provider that returns a simple text response."""
    mock_prov = MagicMock()

    mock_client = MagicMock()
    if raise_on_call:
        mock_client.chat.completions.create.side_effect = raise_on_call
    else:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = text
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
        mock_client.chat.completions.create.return_value = mock_response

    mock_prov.create_client.return_value = mock_client
    mock_prov.get_api_type.return_value = "openai_completion"
    mock_prov.get_max_tokens.return_value = 100
    mock_prov.get_context_window.return_value = 4000
    mock_prov.get_reasoning.return_value = reasoning
    return mock_prov


# ── _create_memory ────────────────────────────────────────────────────


class TestCreateMemory:
    def test_with_memory_db_and_backend(self, minimal_settings, registry_with_echo, providers):
        entry = AgentEntry(
            name="test",
            memory_db="memory.db",
            provider="test",
            model="test-model",
        )
        minimal_settings.embeddings_backends = {
            "default": EmbeddingsBackend(model_path="fake", dim=384),
        }
        minimal_settings.memory_search = MemorySearchConfig(embeddings_backend="default")

        with patch("qd_evolve.agent.agent.MemoryStore") as MockStore, \
             patch("qd_evolve.tools.recall_memory.set_memory_store"), \
             patch("qd_evolve.tools.recall_memory.set_default_limit"):
            MockStore.return_value = MagicMock()
            result = Agent._create_memory(entry, minimal_settings, registry_with_echo)
            assert result is not None
            MockStore.assert_called_once()

    def test_with_memory_db_no_backend(self, minimal_settings, registry_with_echo, providers):
        entry = AgentEntry(name="test", memory_db="memory.db", provider="test", model="test-model")
        minimal_settings.embeddings_backends = {}
        minimal_settings.memory_search = MemorySearchConfig(embeddings_backend="default")

        result = Agent._create_memory(entry, minimal_settings, registry_with_echo)
        assert result is None

    def test_with_empty_memory_db(self, minimal_settings, registry_with_echo, providers):
        entry = AgentEntry(name="test", memory_db="", provider="test", model="test-model")
        result = Agent._create_memory(entry, minimal_settings, registry_with_echo)
        assert result is None

    def test_with_null_memory_db(self, minimal_settings, registry_with_echo, providers):
        entry = AgentEntry(name="test", memory_db=None, provider="test", model="test-model")
        result = Agent._create_memory(entry, minimal_settings, registry_with_echo)
        assert result is None

    def test_memory_disabled_disables_recall_tool(self, minimal_settings, registry_with_echo, providers):
        registry_with_echo.register(
            "recall_memory", "Recall memory", lambda: "",
            {"type": "object", "properties": {}, "required": []},
        )
        entry = AgentEntry(name="test", memory_db="", provider="test", model="test-model")

        Agent._create_memory(entry, minimal_settings, registry_with_echo)
        recall_td = registry_with_echo.get("recall_memory")
        assert recall_td.enabled is False


# ── run() — API error handling ────────────────────────────────────────


class TestRunApiError:
    def test_api_error_returns_error_string(self, agent_core):
        events = []
        agent_core.set_event_callback(lambda e: events.append(e))

        mock_prov = _make_mock_prov(raise_on_call=ConnectionError("timeout"))

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            result = agent_core.run("hello")

        assert "API error" in result
        assert "ConnectionError" in result
        assert any(e["type"] == "error" for e in events)

    def test_api_error_removes_user_message(self, agent_core):
        mock_prov = _make_mock_prov(raise_on_call=ConnectionError("timeout"))

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            agent_core.run("hello")

        assert agent_core.messages == []

    def test_api_error_sets_running_false(self, agent_core):
        mock_prov = _make_mock_prov(raise_on_call=ConnectionError("timeout"))

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            agent_core.run("hello")

        assert agent_core._running is False


# ── run() — provider/model resolution ─────────────────────────────────


class TestRunProviderModelResolution:
    def test_uses_config_defaults_when_not_set(self, agent_core):
        agent_core._provider_name = None
        agent_core._model = None

        mock_prov = _make_mock_prov()

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            result = agent_core.run("hello")

        assert agent_core._provider_name == "test"
        assert agent_core._model == "test-model"

    def test_explicit_provider_model_override(self, agent_core):
        mock_prov = _make_mock_prov()

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            agent_core.run("hello", provider="test", model="test-model")

        assert agent_core._provider_name == "test"


# ── _auto_recall ──────────────────────────────────────────────────────


class TestAutoRecall:
    def test_no_memory_returns_unchanged(self, agent_core):
        agent_core.memory = None
        result = agent_core._auto_recall("hello", "original prompt")
        assert result == "original prompt"

    def test_auto_recall_disabled(self, agent_core):
        agent_core.settings.memory_search.auto_recall = False
        mock_memory = MagicMock()
        agent_core.memory = mock_memory
        result = agent_core._auto_recall("hello", "original prompt")
        assert result == "original prompt"
        mock_memory.recall.assert_not_called()

    def test_auto_recall_injects_memory(self, agent_core):
        agent_core.settings.memory_search.auto_recall = True
        mock_memory = MagicMock()
        entry = type("Entry", (), {
            "id": 1, "key": "k", "session_id": "s",
            "user_msg": "question", "assistant_msg": "answer",
            "content": "content", "accessed_at": None, "access_count": 0,
            "distance": 0.5,
        })()
        mock_memory.recall.return_value = [entry]
        agent_core.memory = mock_memory

        prompt = "## Section 1\nSome text\n## Section 2\nMore text"
        result = agent_core._auto_recall("hello", prompt)
        assert "Relevant Past Conversations" in result

    def test_auto_recall_failure_returns_unchanged(self, agent_core):
        agent_core.settings.memory_search.auto_recall = True
        mock_memory = MagicMock()
        mock_memory.recall.side_effect = Exception("db error")
        agent_core.memory = mock_memory

        prompt = "original prompt"
        result = agent_core._auto_recall("hello", prompt)
        assert result == "original prompt"


# ── _format_messages_log ──────────────────────────────────────────────


class TestFormatMessagesLog:
    def test_user_message(self, agent_core):
        agent_core.messages = [{"role": "user", "content": "hello"}]
        result = agent_core._format_messages_log()
        assert "[user] hello" in result

    def test_assistant_text(self, agent_core):
        agent_core.messages = [{"role": "assistant", "content": "world"}]
        result = agent_core._format_messages_log()
        assert "[assistant] world" in result

    def test_assistant_tool_calls(self, agent_core):
        agent_core.messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {"name": "echo", "arguments": '{"s": "hi"}'},
            }],
        }]
        result = agent_core._format_messages_log()
        assert "echo" in result

    def test_tool_message(self, agent_core):
        agent_core.messages = [{
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": "tool result",
        }]
        result = agent_core._format_messages_log()
        assert "tc-1" in result
        assert "tool result" in result

    def test_assistant_content_list(self, agent_core):
        agent_core.messages = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_use", "name": "echo", "input": {"s": "hi"}},
            ],
        }]
        result = agent_core._format_messages_log()
        assert "hello" in result
        assert "echo" in result


# ── _format_completion_log ────────────────────────────────────────────


class TestFormatCompletionLog:
    def test_anthropic_format(self, agent_core):
        agent_core._api_type = "anthropic"
        response = MagicMock()
        response.content = [MagicMock(type="text", text="hello")]
        response.stop_reason = "end_turn"

        result = agent_core._format_completion_log(response)
        assert "hello" in result
        assert "stop_reason=end_turn" in result

    def test_openai_completion_format(self, agent_core):
        agent_core._api_type = "openai_completion"
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "hi there"
        response.choices[0].message.tool_calls = None
        response.choices[0].finish_reason = "stop"

        result = agent_core._format_completion_log(response)
        assert "hi there" in result
        assert "finish_reason=stop" in result

    def test_openai_completion_with_tool_calls(self, agent_core):
        agent_core._api_type = "openai_completion"
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = None
        tc = MagicMock()
        tc.function.name = "echo"
        tc.function.arguments = '{"s": "hi"}'
        response.choices[0].message.tool_calls = [tc]
        response.choices[0].finish_reason = "tool_calls"

        result = agent_core._format_completion_log(response)
        assert "echo" in result
        assert "finish_reason=tool_calls" in result

    def test_empty_choices(self, agent_core):
        agent_core._api_type = "openai_completion"
        response = MagicMock()
        response.choices = []

        result = agent_core._format_completion_log(response)
        # Should handle gracefully


# ── run() — completed event ───────────────────────────────────────────


class TestRunCompletedEvent:
    def test_completed_event_on_success(self, agent_core):
        events = []
        agent_core.set_event_callback(lambda e: events.append(e))

        mock_prov = _make_mock_prov("response text")

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            agent_core.run("hello")

        assert any(e["type"] == "completed" for e in events)
        assert any(e["type"] == "iteration" for e in events)

    def test_memory_save_on_success(self, agent_core):
        mock_memory = MagicMock()
        agent_core.memory = mock_memory

        mock_prov = _make_mock_prov("response")

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            agent_core.run("hello")

        mock_memory.save.assert_called_once_with("hello", "response")

    def test_memory_save_failure_handled(self, agent_core):
        mock_memory = MagicMock()
        mock_memory.save.side_effect = Exception("disk full")
        agent_core.memory = mock_memory

        mock_prov = _make_mock_prov("response")

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            # Should not crash
            result = agent_core.run("hello")
        assert result == "response"


# ── unsupported api_type ──────────────────────────────────────────────


class TestUnsupportedApiType:
    def test_raises_for_unknown_api_type(self, agent_core):
        mock_prov = _make_mock_prov()
        mock_prov.get_api_type.return_value = "unknown_api"

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            result = agent_core.run("hello")

        assert "API error" in result
        assert "ValueError" in result


# ── heartbeat ──────────────────────────────────────────────────────────


class TestHeartbeatCheck:
    def test_skips_when_running(self, agent_core):
        agent_core._running = True
        result = agent_core.heartbeat_check(30)
        assert result is None

    def test_heartbeat_with_template(self, agent_core):
        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "heartbeat message"
        agent_core._template_mgr = mock_tmpl

        mock_prov = _make_mock_prov("I'm here")

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            result = agent_core.heartbeat_check(30)

        assert result == "I'm here"

    def test_heartbeat_silent_dot(self, agent_core):
        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "heartbeat message"
        agent_core._template_mgr = mock_tmpl
        events = []
        agent_core.set_event_callback(lambda e: events.append(e))

        mock_prov = _make_mock_prov(".")

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            result = agent_core.heartbeat_check(30)

        assert result == "."
        assert any(e["type"] == "heartbeat_silent" for e in events)

    def test_heartbeat_llm_failure_returns_none(self, agent_core):
        mock_tmpl = MagicMock()
        mock_tmpl.render.return_value = "heartbeat message"
        agent_core._template_mgr = mock_tmpl

        with patch.object(agent_core, "run", side_effect=ConnectionError("down")):
            result = agent_core.heartbeat_check(30)

        assert result is None

    def test_heartbeat_no_template(self, agent_core):
        agent_core._template_mgr = None

        mock_prov = _make_mock_prov("here")

        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            result = agent_core.heartbeat_check(30)

        assert result == "here"


# ── set_event_callback ────────────────────────────────────────────────


class TestSetEventCallback:
    def test_event_callback_receives_events(self, agent_core):
        received = []
        agent_core.set_event_callback(lambda e: received.append(e))
        agent_core._update_status("working")
        assert len(received) == 1
        assert received[0]["type"] == "status"