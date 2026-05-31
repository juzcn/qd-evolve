"""Tests for qd_evolve.agent.agent — Agent logic (compress, inject, activate, events)."""

from unittest.mock import patch

import pytest

from qd_evolve.agent.agent import Agent
from qd_evolve.agent.a2a_agent import A2AAgent
from qd_evolve.agent.a2a import AgentCard, AgentCapabilities
from qd_evolve.agent.server import TaskStore


@pytest.fixture
def a2a_agent(agent_core):
    """A2AAgent wrapping agent_core — for event subscriber tests."""
    card = AgentCard(name="test", description="Test agent", capabilities=AgentCapabilities(streaming=True))
    return A2AAgent(agent_core, card, TaskStore())


class TestAgentInit:
    def test_initial_state(self, agent_core):
        assert agent_core.messages == []
        assert agent_core.iteration == 0
        assert agent_core.total_input_tokens == 0
        assert agent_core.total_output_tokens == 0

    def test_total_tokens(self, agent_core):
        assert agent_core.total_tokens == 0
        agent_core.total_input_tokens = 10
        agent_core.total_output_tokens = 5
        assert agent_core.total_tokens == 15


class TestEventSubscribers:
    def test_subscribe_and_push(self, a2a_agent):
        q = a2a_agent.subscribe_events()
        a2a_agent._push_event({"type": "test", "data": "hello"})
        event = q.get_nowait()
        assert event["type"] == "test"
        assert event["data"] == "hello"
        a2a_agent.unsubscribe_events(q)

    def test_unsubscribe(self, a2a_agent):
        q = a2a_agent.subscribe_events()
        a2a_agent.unsubscribe_events(q)
        a2a_agent._push_event({"type": "test"})
        assert q.empty()

    def test_heartbeat_compat_aliases(self, a2a_agent):
        q = a2a_agent.subscribe_heartbeat()
        a2a_agent._push_event({"type": "heartbeat", "content": "ping"})
        event = q.get_nowait()
        assert event["type"] == "heartbeat"
        a2a_agent.unsubscribe_heartbeat(q)


class TestCallbacks:
    def test_status_callback(self, agent_core):
        received = []
        agent_core.set_status_callback(lambda msg: received.append(msg))
        agent_core.iteration = 1
        agent_core._update_status("working")
        assert len(received) == 1
        assert "[#1]" in received[0]

    def test_print_callback(self, agent_core):
        received = []
        agent_core.set_print_callback(lambda msg: received.append(msg))
        agent_core._print("reasoning text")
        assert len(received) == 1
        assert received[0] == "reasoning text"

    def test_status_pushes_event(self, a2a_agent):
        q = a2a_agent.subscribe_events()
        a2a_agent.iteration = 1
        a2a_agent._update_status("working")
        event = q.get_nowait()
        assert event["type"] == "status"
        a2a_agent.unsubscribe_events(q)

    def test_print_pushes_event(self, a2a_agent):
        q = a2a_agent.subscribe_events()
        a2a_agent._print("output text")
        event = q.get_nowait()
        assert event["type"] == "print"
        assert event["text"] == "output text"
        a2a_agent.unsubscribe_events(q)


class TestActivateTool:
    def test_activate_func_tool(self, agent_core):
        agent_core._activate_tool("echo", {"s": "hello"}, "hello")
        assert "echo" in agent_core._active_tools

    def test_activate_load_func(self, agent_core):
        agent_core._activate_tool("load_func", {"name": "fetch"}, "schema...")
        assert "load_func" in agent_core._active_tools
        assert "fetch" in agent_core._active_tools

    def test_activate_load_skill(self, agent_core):
        agent_core._activate_tool("load_skill", {"name": "find-tools"}, "skill content...")
        assert "find-tools" in agent_core._loaded_skill_names

    def test_activate_load_cli(self, agent_core):
        agent_core._activate_tool("load_cli", {"name": "git"}, "cli usage...")
        assert "git" in agent_core._loaded_cli_names


class TestRemoveFromUnloaded:
    def test_removes_matching_line(self):
        text = "### Unloaded Skills Summary\n- find-tools: Search tools\n- other: Other\n## Next Section"
        result = Agent._remove_from_unloaded(text, "### Unloaded Skills Summary", {"find-tools"})
        assert "- find-tools" not in result
        assert "- other: Other" in result

    def test_removes_empty_section(self):
        text = "### Unloaded Skills Summary\n- find-tools: Search tools\n## Next Section"
        result = Agent._remove_from_unloaded(text, "### Unloaded Skills Summary", {"find-tools"})
        assert "### Unloaded Skills Summary" not in result

    def test_no_matching_header(self):
        text = "Some text without the header"
        result = Agent._remove_from_unloaded(text, "### Unloaded Skills Summary", {"find-tools"})
        assert result == text

    def test_multiple_names(self):
        text = "### Unloaded Func Tools Summary\n- echo: Echo\n- fetch: Fetch\n- run_shell: Shell\n## Next"
        result = Agent._remove_from_unloaded(text, "### Unloaded Func Tools Summary", {"echo", "fetch"})
        assert "- echo" not in result
        assert "- fetch" not in result
        assert "- run_shell: Shell" in result


class TestInjectLoadedContent:
    def test_removes_loaded_skills(self, agent_core):
        agent_core._loaded_skill_names = {"find-tools"}
        prompt = "### Unloaded Skills Summary\n- find-tools: Search tools\n- other: Other\n## Next Section"
        result = agent_core._inject_loaded_content(prompt)
        assert "- find-tools" not in result
        assert "- other: Other" in result

    def test_removes_loaded_cli(self, agent_core):
        agent_core._loaded_cli_names = {"git"}
        prompt = "### Unloaded CLI Tools Summary\n- git: Git CLI\n- npm: NPM CLI\n## Next Section"
        result = agent_core._inject_loaded_content(prompt)
        assert "- git" not in result
        assert "- npm: NPM CLI" in result

    def test_removes_active_tools(self, agent_core):
        agent_core._active_tools = {"echo"}
        prompt = "### Unloaded Func Tools Summary\n- echo: Echo\n- fetch: Fetch\n## Next Section"
        result = agent_core._inject_loaded_content(prompt)
        assert "- echo" not in result
        assert "- fetch: Fetch" in result

    def test_no_changes_when_nothing_loaded(self, agent_core):
        prompt = "### Unloaded Skills Summary\n- find-tools: Search tools\n## Next Section"
        result = agent_core._inject_loaded_content(prompt)
        assert result == prompt


class TestCompressMessages:
    def test_no_compress_below_threshold(self, agent_core):
        agent_core.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        agent_core.last_input_tokens = 10  # well below 70% of 4000
        agent_core._compress_messages()
        assert len(agent_core.messages) == 2

    def test_compress_above_threshold(self, agent_core):
        # Fill messages to trigger compression
        for i in range(20):
            agent_core.messages.append({"role": "user", "content": f"question {i} " * 100})
            agent_core.messages.append({"role": "assistant", "content": f"answer {i} " * 100})
        agent_core.last_input_tokens = 3500  # above 70% of 4000
        agent_core._compress_messages()
        assert len(agent_core.messages) < 40

    def test_no_compress_zero_context_window(self, agent_core):
        # Mock the Provider to return context_window=0 — compression skipped
        from unittest.mock import MagicMock
        mock_prov = MagicMock()
        mock_prov.get_context_window.return_value = 0
        agent_core.last_input_tokens = 999999
        count_before = len(agent_core.messages)
        with patch.object(agent_core.providers, "get", return_value=mock_prov):
            agent_core._compress_messages()
        assert len(agent_core.messages) == count_before


class TestExtractText:
    def test_extracts_text_blocks(self):
        class TextBlock:
            type = "text"
            text = "hello"

        class ToolBlock:
            type = "tool_use"
            name = "echo"

        result = Agent._extract_text([TextBlock(), ToolBlock()])
        assert result == "hello"

    def test_empty_content(self):
        result = Agent._extract_text([])
        assert result == ""


class TestTrunc:
    def test_no_truncation(self, agent_core):
        agent_core._log_limit = 0
        assert agent_core._trunc("hello") == "hello"

    def test_truncation(self, agent_core):
        agent_core._log_limit = 5
        assert agent_core._trunc("hello world") == "hello..."

    def test_tail_truncation(self, agent_core):
        agent_core._log_limit = 5
        assert agent_core._trunc("hello world", tail=True) == "...world"

    def test_short_text_no_truncation(self, agent_core):
        agent_core._log_limit = 100
        assert agent_core._trunc("hello") == "hello"


class TestReset:
    def test_reset_clears_state(self, agent_core):
        agent_core.messages = [{"role": "user", "content": "hello"}]
        agent_core.total_input_tokens = 100
        agent_core.total_output_tokens = 50
        agent_core._recalled.add([
            type("Entry", (), {"id": 1, "key": "k", "session_id": "s", "user_msg": "q", "assistant_msg": "a", "content": "c"})()
        ])
        agent_core.reset()
        assert agent_core.messages == []
        assert agent_core.total_input_tokens == 0
        assert agent_core.total_output_tokens == 0


class TestTrackTokens:
    def test_track_tokens_anthropic(self, agent_core):
        class Usage:
            input_tokens = 500
            output_tokens = 100
        agent_core._track_tokens_anthropic(Usage())
        assert agent_core.last_input_tokens == 500
        assert agent_core.last_output_tokens == 100
        assert agent_core.total_input_tokens == 500
        assert agent_core.total_output_tokens == 100
        assert agent_core.total_tokens == 600

    def test_track_tokens_openai_completion(self, agent_core):
        class Usage:
            prompt_tokens = 800
            completion_tokens = 200
        agent_core._track_tokens_openai_completion(Usage())
        assert agent_core.last_input_tokens == 800
        assert agent_core.last_output_tokens == 200
        assert agent_core.total_input_tokens == 800
        assert agent_core.total_output_tokens == 200

    def test_track_tokens_openai_response(self, agent_core):
        class Usage:
            input_tokens = 300
            output_tokens = 50
        agent_core._track_tokens_openai_response(Usage())
        assert agent_core.last_input_tokens == 300
        assert agent_core.last_output_tokens == 50
        assert agent_core.total_input_tokens == 300
        assert agent_core.total_output_tokens == 50

    def test_track_tokens_cumulative(self, agent_core):
        class Usage1:
            prompt_tokens = 100
            completion_tokens = 20
        class Usage2:
            prompt_tokens = 200
            completion_tokens = 30
        agent_core._track_tokens_openai_completion(Usage1())
        agent_core._track_tokens_openai_completion(Usage2())
        assert agent_core.last_input_tokens == 200
        assert agent_core.last_output_tokens == 30
        assert agent_core.total_input_tokens == 300
        assert agent_core.total_output_tokens == 50
        assert agent_core.total_tokens == 350

    def test_track_tokens_pushes_event(self, a2a_agent):
        class Usage:
            prompt_tokens = 100
            completion_tokens = 20
        q = a2a_agent.subscribe_events()
        a2a_agent._track_tokens_openai_completion(Usage())
        event = q.get_nowait()
        assert event["type"] == "tokens"
        assert event["input"] == 100
        assert event["output"] == 20
        assert event["total_in"] == 100
        assert event["total_out"] == 20
        a2a_agent.unsubscribe_events(q)

    def test_track_tokens_anthropic_pushes_event(self, a2a_agent):
        class Usage:
            input_tokens = 50
            output_tokens = 10
        q = a2a_agent.subscribe_events()
        a2a_agent._track_tokens_anthropic(Usage())
        event = q.get_nowait()
        assert event["type"] == "tokens"
        assert event["input"] == 50
        assert event["output"] == 10
        a2a_agent.unsubscribe_events(q)

    def test_track_tokens_response_pushes_event(self, a2a_agent):
        class Usage:
            input_tokens = 70
            output_tokens = 15
        q = a2a_agent.subscribe_events()
        a2a_agent._track_tokens_openai_response(Usage())
        event = q.get_nowait()
        assert event["type"] == "tokens"
        assert event["input"] == 70
        assert event["output"] == 15
        a2a_agent.unsubscribe_events(q)