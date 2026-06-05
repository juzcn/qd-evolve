"""Tests for qd_evolve.tools.sub_agent_manager — sub-agent lifecycle management.

Tests pure functions (collect_sub_results, has_running_sub_agents,
_get_sub_result, _cancel_sub_task) and validation logic of
_create_sub_agent / _run_sub_agent in isolation.
"""

from unittest.mock import MagicMock, patch

import pytest

from qd_evolve.utils.cancellation import CancellationToken


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_sub_agent_state():
    """Reset module-level globals between tests."""
    import qd_evolve.tools.sub_agent_manager as mod

    old_agents = mod._sub_agents.copy()
    old_tasks = mod._sub_tasks.copy()
    mod._sub_agents.clear()
    mod._sub_tasks.clear()
    mod._sub_complete_event.clear()

    yield

    mod._sub_agents = old_agents
    mod._sub_tasks = old_tasks


# ── _get_sub_result ───────────────────────────────────────────────


class TestGetSubResult:
    def test_task_not_found(self):
        from qd_evolve.tools.sub_agent_manager import _get_sub_result
        result = _get_sub_result("nonexistent_id")
        assert "not found" in result

    def test_running_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _get_sub_result

        _sub_tasks["task1"] = {
            "name": "helper", "state": "running", "result": None, "consumed": False,
        }
        result = _get_sub_result("task1")
        assert "running" in result
        assert "helper" in result

    def test_done_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _get_sub_result

        _sub_tasks["task1"] = {
            "name": "helper", "state": "done", "result": "All done!", "consumed": False,
        }
        result = _get_sub_result("task1")
        assert "done" in result
        assert "All done!" in result

    def test_cancelled_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _get_sub_result

        _sub_tasks["task1"] = {
            "name": "helper", "state": "cancelled", "result": "Cancelled.", "consumed": False,
        }
        result = _get_sub_result("task1")
        assert "cancelled" in result
        assert "Cancelled." in result

    def test_error_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _get_sub_result

        _sub_tasks["task1"] = {
            "name": "helper", "state": "error", "result": "ValueError: boom!", "consumed": False,
        }
        result = _get_sub_result("task1")
        assert "error" in result
        assert "ValueError: boom!" in result


# ── collect_sub_results ───────────────────────────────────────────


class TestCollectSubResults:
    def test_empty(self):
        from qd_evolve.tools.sub_agent_manager import collect_sub_results
        assert collect_sub_results() == ""

    def test_collects_done_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, collect_sub_results

        _sub_tasks["t1"] = {
            "name": "helper", "state": "done", "result": "Task result", "consumed": False,
        }
        result = collect_sub_results()
        assert "helper" in result
        assert "completed" in result
        assert "Task result" in result
        assert _sub_tasks["t1"]["consumed"] is True

    def test_collects_error_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, collect_sub_results

        _sub_tasks["t1"] = {
            "name": "helper", "state": "error", "result": "err", "consumed": False,
        }
        result = collect_sub_results()
        assert "failed" in result
        assert _sub_tasks["t1"]["consumed"] is True

    def test_collects_cancelled_task(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, collect_sub_results

        _sub_tasks["t1"] = {
            "name": "helper", "state": "cancelled", "result": "cancelled", "consumed": False,
        }
        result = collect_sub_results()
        assert "cancelled" in result
        assert _sub_tasks["t1"]["consumed"] is True

    def test_skips_running_tasks(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, collect_sub_results

        _sub_tasks["t1"] = {
            "name": "helper", "state": "running", "result": None, "consumed": False,
        }
        result = collect_sub_results()
        assert result == ""
        assert not _sub_tasks["t1"].get("consumed")

    def test_skips_already_consumed(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, collect_sub_results

        _sub_tasks["t1"] = {
            "name": "helper", "state": "done", "result": "old", "consumed": True,
        }
        result = collect_sub_results()
        assert result == ""

    def test_multiple_tasks(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, collect_sub_results

        _sub_tasks["t1"] = {
            "name": "a", "state": "done", "result": "r1", "consumed": False,
        }
        _sub_tasks["t2"] = {
            "name": "b", "state": "error", "result": "e2", "consumed": False,
        }
        result = collect_sub_results()
        assert "a" in result
        assert "b" in result
        assert "r1" in result
        assert "e2" in result


# ── has_running_sub_agents ────────────────────────────────────────


class TestHasRunningSubAgents:
    def test_empty(self):
        from qd_evolve.tools.sub_agent_manager import has_running_sub_agents
        assert has_running_sub_agents() is False

    def test_has_running(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, has_running_sub_agents

        _sub_tasks["t1"] = {
            "name": "helper", "state": "running", "result": None, "consumed": False,
        }
        assert has_running_sub_agents() is True

    def test_all_done(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, has_running_sub_agents

        _sub_tasks["t1"] = {
            "name": "helper", "state": "done", "result": "ok", "consumed": False,
        }
        _sub_tasks["t2"] = {
            "name": "helper2", "state": "error", "result": "err", "consumed": False,
        }
        assert has_running_sub_agents() is False

    def test_mixed(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, has_running_sub_agents

        _sub_tasks["t1"] = {
            "name": "a", "state": "done", "result": "ok", "consumed": False,
        }
        _sub_tasks["t2"] = {
            "name": "b", "state": "running", "result": None, "consumed": False,
        }
        assert has_running_sub_agents() is True


# ── _cancel_sub_task ──────────────────────────────────────────────


class TestCancelSubTask:
    def test_task_not_found(self):
        from qd_evolve.tools.sub_agent_manager import _cancel_sub_task
        result = _cancel_sub_task("nonexistent")
        assert "not found" in result

    def test_already_cancelled(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _cancel_sub_task

        _sub_tasks["t1"] = {
            "name": "helper", "state": "cancelled", "result": "...", "consumed": False,
        }
        result = _cancel_sub_task("t1")
        assert "already cancelled" in result

    def test_already_done(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _cancel_sub_task

        _sub_tasks["t1"] = {
            "name": "helper", "state": "done", "result": "ok", "consumed": False,
        }
        result = _cancel_sub_task("t1")
        assert "already finished" in result
        assert "done" in result

    def test_already_error(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _cancel_sub_task

        _sub_tasks["t1"] = {
            "name": "helper", "state": "error", "result": "err", "consumed": False,
        }
        result = _cancel_sub_task("t1")
        assert "already finished" in result
        assert "error" in result

    def test_running_with_token(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _cancel_sub_task

        token = CancellationToken()
        _sub_tasks["t1"] = {
            "name": "helper", "state": "running", "result": None, "consumed": False,
            "_cancel_token": token,
        }
        result = _cancel_sub_task("t1")
        assert "requested" in result
        assert token.is_cancelled is True

    def test_running_without_token(self):
        from qd_evolve.tools.sub_agent_manager import _sub_tasks, _cancel_sub_task

        _sub_tasks["t1"] = {
            "name": "helper", "state": "running", "result": None, "consumed": False,
        }
        result = _cancel_sub_task("t1")
        assert "cancelled before start" in result
        assert _sub_tasks["t1"]["state"] == "cancelled"


# ── _create_sub_agent validation ──────────────────────────────────


class TestCreateSubAgentValidation:
    """Test validation logic in _create_sub_agent without creating a real agent."""

    def test_no_context_raises(self):
        from qd_evolve.tools.sub_agent_manager import _create_sub_agent

        import qd_evolve.tools.sub_agent_manager as sub_mod
        import qd_evolve.tools.config_manager as cfg_mod

        old_var = cfg_mod._current_agent_var.get()
        old_ctx = cfg_mod._agent_contexts.copy()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                _create_sub_agent("helper")
        finally:
            cfg_mod._current_agent_var.set(old_var)
            cfg_mod._agent_contexts.update(old_ctx)

    def test_empty_name(self):
        """_create_sub_agent with empty name returns error."""

        import qd_evolve.tools.config_manager as cfg_mod
        from qd_evolve.core.config import AgentEntry, ModelConfig, ProviderConfig, Settings

        settings = Settings(
            max_iterations=5,
            tool_output_limit=2000,
            providers=[
                ProviderConfig(name="test", api_key="sk",
                    models=[ModelConfig(name="m", max_tokens=100, context_window=4000)]),
            ],
            default_provider="test",
            default_model="m",
            agents_config={  # type: ignore
                "chat_agent": "p",
                "agents": [AgentEntry(name="p", description="P", provider="test", model="m")],
            },
        )
        parent = MagicMock()
        parent._always_active = set()
        parent._preload_skills = set()
        parent._preload_cli = set()
        parent._template_context = {}
        parent.registry = MagicMock()
        parent.providers = MagicMock()
        parent._provider_name = "test"
        parent._model = "m"

        cfg_mod._agent_contexts["p"] = (parent, settings)
        token = cfg_mod._current_agent_var.set("p")
        try:
            from qd_evolve.tools.sub_agent_manager import _create_sub_agent, _sub_agents
            result = _create_sub_agent("")
            assert "name is required" in result
            result2 = _create_sub_agent("   ")
            assert "name is required" in result2
        finally:
            cfg_mod._agent_contexts.clear()
            _sub_agents.clear()
            cfg_mod._current_agent_var.reset(token)


# ── _run_sub_agent validation ─────────────────────────────────────


class TestRunSubAgentValidation:
    """Test validation logic in _run_sub_agent without actually running."""

    def test_no_context_raises(self):
        from qd_evolve.tools.sub_agent_manager import _run_sub_agent

        import qd_evolve.tools.config_manager as cfg_mod

        old_var = cfg_mod._current_agent_var.get()
        old_ctx = cfg_mod._agent_contexts.copy()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                _run_sub_agent("helper", "do something")
        finally:
            cfg_mod._current_agent_var.set(old_var)
            cfg_mod._agent_contexts.update(old_ctx)

    def test_sub_agent_not_found(self):
        import qd_evolve.tools.config_manager as cfg_mod
        from qd_evolve.core.config import AgentEntry, ModelConfig, ProviderConfig, Settings

        settings = Settings(
            max_iterations=5,
            tool_output_limit=2000,
            providers=[
                ProviderConfig(name="test", api_key="sk",
                    models=[ModelConfig(name="m", max_tokens=100, context_window=4000)]),
            ],
            default_provider="test",
            default_model="m",
            agents_config={  # type: ignore
                "chat_agent": "p",
                "agents": [AgentEntry(name="p", description="P", provider="test", model="m")],
            },
        )
        parent = MagicMock()
        parent._provider_name = "test"
        parent._model = "m"

        cfg_mod._agent_contexts["p"] = (parent, settings)
        token = cfg_mod._current_agent_var.set("p")
        try:
            from qd_evolve.tools.sub_agent_manager import _run_sub_agent
            result = _run_sub_agent("nonexistent", "do something")
            assert "not found" in result
            assert "(none)" in result
        finally:
            cfg_mod._agent_contexts.clear()
            cfg_mod._current_agent_var.reset(token)

    def test_sub_agent_is_busy(self):
        import qd_evolve.tools.config_manager as cfg_mod
        from qd_evolve.tools.sub_agent_manager import _run_sub_agent, _sub_agents

        mock_sub = MagicMock()
        mock_sub._running = True
        _sub_agents["helper"] = mock_sub

        mock_parent = MagicMock()
        settings = MagicMock()
        cfg_mod._agent_contexts["parent"] = (mock_parent, settings)
        token = cfg_mod._current_agent_var.set("parent")
        try:
            result = _run_sub_agent("helper", "do something")
            assert "busy" in result
        finally:
            cfg_mod._agent_contexts.clear()
            cfg_mod._current_agent_var.reset(token)
            _sub_agents.clear()


# ── _try_push_to_parent ───────────────────────────────────────────


class TestTryPushToParent:
    def test_no_on_event_returns_early(self):
        from qd_evolve.tools.sub_agent_manager import _try_push_to_parent

        parent = MagicMock()
        parent._on_event = None
        _try_push_to_parent(parent, "p")

    def test_lock_busy_returns_early(self):
        from qd_evolve.tools.sub_agent_manager import _try_push_to_parent

        parent = MagicMock()
        parent._on_event = MagicMock()
        parent._run_lock.acquire.return_value = False
        _try_push_to_parent(parent, "p")
        parent._run_lock.acquire.assert_called_once_with(blocking=False)
