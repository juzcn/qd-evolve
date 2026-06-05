"""Tests for qd_evolve.tools.config_manager — agent self-configuration handlers."""

from unittest.mock import MagicMock, patch

import pytest

from qd_evolve.core.config import AgentEntry, ModelConfig, ProviderConfig, Settings


# ── Helpers ──────────────────────────────────────────────────────


def _make_settings(agent_name: str, provider: str = "test", model: str = "test-model",
                   include_agent_in_config: bool = True):
    """Build Settings with an agent entry optionally in agents_config."""
    agents_list = []
    if include_agent_in_config:
        agents_list.append(AgentEntry(name=agent_name, description="Test agent",
                                       provider=provider, model=model))
    agents_list.append(AgentEntry(name="other_agent", description="Other agent",
                                   provider=provider, model=model))

    return Settings(
        max_iterations=5,
        tool_output_limit=2000,
        providers=[
            ProviderConfig(
                name=provider,
                api_key="sk-test-key",
                models=[
                    ModelConfig(name=model, max_tokens=100, context_window=4000),
                    ModelConfig(name="other-model", max_tokens=200, context_window=8000),
                ],
            ),
        ],
        default_provider=provider,
        default_model=model,
        agents_config={  # type: ignore
            "chat_agent": agent_name,
            "agents": agents_list,
        },
    )


def _setup_context(name: str, provider: str = "test", model: str = "test-model",
                   include_agent_in_config: bool = True):
    """Set up agent context and return (agent, settings, token, cfg_mod)."""
    import qd_evolve.tools.config_manager as cfg_mod

    settings = _make_settings(name, provider, model,
                               include_agent_in_config=include_agent_in_config)

    mock_agent = MagicMock()
    mock_agent._provider_name = provider
    mock_agent._model = model

    cfg_mod._agent_contexts[name] = (mock_agent, settings)
    token = cfg_mod._current_agent_var.set(name)

    return mock_agent, settings, token, cfg_mod


def _cleanup(token, cfg_mod):
    cfg_mod._current_agent_var.reset(token)
    cfg_mod._agent_contexts.clear()


# ── Tests: _list_providers ────────────────────────────────────────


class TestListProviders:
    def test_lists_providers_and_models(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            result = mod._list_providers()
            assert "test" in result
            assert "test-model" in result
            assert "other-model" in result
            assert "[default]" in result
        finally:
            _cleanup(token, mod)

    def test_no_context_raises(self):
        import qd_evolve.tools.config_manager as cfg_mod

        old_ctx = cfg_mod._agent_contexts.copy()
        old_var = cfg_mod._current_agent_var.get()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                cfg_mod._list_providers()
        finally:
            cfg_mod._agent_contexts.update(old_ctx)
            cfg_mod._current_agent_var.set(old_var)


# ── Tests: _get_my_config ─────────────────────────────────────────


class TestGetMyConfig:
    def test_returns_agent_config(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            result = mod._get_my_config()
            assert "name: test_agent" in result
            assert "provider: test" in result
            assert "model: test-model" in result
            assert "description: Test agent" in result
        finally:
            _cleanup(token, mod)

    def test_no_context_raises(self):
        import qd_evolve.tools.config_manager as cfg_mod

        old_ctx = cfg_mod._agent_contexts.copy()
        old_var = cfg_mod._current_agent_var.get()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                cfg_mod._get_my_config()
        finally:
            cfg_mod._agent_contexts.update(old_ctx)
            cfg_mod._current_agent_var.set(old_var)


# ── Tests: _update_my_config ──────────────────────────────────────


class TestUpdateMyConfig:
    def test_no_changes(self):
        """_update_my_config with empty args returns 'No changes made'."""
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config()
                assert "No changes" in result
        finally:
            _cleanup(token, mod)

    def test_update_description_only(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(description="New description")
                assert "description:" in result
        finally:
            _cleanup(token, mod)

    def test_update_provider_only(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(provider="test")
                assert "provider:" in result
                assert agent._provider_name == "test"
        finally:
            _cleanup(token, mod)

    def test_update_model_only(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(model="other-model")
                assert "model:" in result
                assert agent._model == "other-model"
        finally:
            _cleanup(token, mod)

    def test_update_multiple_fields(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(provider="test", model="other-model", description="Updated")
                assert "provider:" in result
                assert "model:" in result
                assert "description:" in result
        finally:
            _cleanup(token, mod)

    def test_provider_not_found(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(provider="nonexistent")
                assert "not found" in result
        finally:
            _cleanup(token, mod)

    def test_model_not_found(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(model="missing-model")
                assert "not found" in result
        finally:
            _cleanup(token, mod)

    def test_agent_not_in_config(self):
        agent, settings, token, mod = _setup_context(
            "orphan_agent", include_agent_in_config=False,
        )
        try:
            with patch("qd_evolve.core.config.load_settings", return_value=settings), \
                 patch("qd_evolve.core.config.save_settings"):
                result = mod._update_my_config(description="test")
                assert "not found" in result
        finally:
            _cleanup(token, mod)

    def test_no_context_raises(self):
        import qd_evolve.tools.config_manager as cfg_mod

        old_ctx = cfg_mod._agent_contexts.copy()
        old_var = cfg_mod._current_agent_var.get()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                with patch("qd_evolve.core.config.load_settings"), \
                     patch("qd_evolve.core.config.save_settings"):
                    cfg_mod._update_my_config()
        finally:
            cfg_mod._agent_contexts.update(old_ctx)
            cfg_mod._current_agent_var.set(old_var)


# ── Tests: set_agent_context ───────────────────────────────────────


class TestSetAgentContext:
    def test_registers_context(self):
        import qd_evolve.tools.config_manager as cfg_mod

        old_ctx = cfg_mod._agent_contexts.copy()
        cfg_mod._agent_contexts.clear()
        try:
            mock_agent = MagicMock()
            mock_settings = MagicMock()
            cfg_mod.set_agent_context("my_agent", mock_agent, mock_settings)

            assert "my_agent" in cfg_mod._agent_contexts
            assert cfg_mod._agent_contexts["my_agent"] == (mock_agent, mock_settings)
        finally:
            cfg_mod._agent_contexts.clear()
            cfg_mod._agent_contexts.update(old_ctx)


# ── Tests: _require_context ────────────────────────────────────────


class TestRequireContext:
    def test_with_valid_context(self):
        agent, settings, token, mod = _setup_context("test_agent")
        try:
            name, a, s = mod._require_context()
            assert name == "test_agent"
            assert a is agent
            assert s is settings
        finally:
            _cleanup(token, mod)

    def test_empty_context_var_raises(self):
        import qd_evolve.tools.config_manager as cfg_mod

        old_ctx = cfg_mod._agent_contexts.copy()
        old_var = cfg_mod._current_agent_var.get()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                cfg_mod._require_context()
        finally:
            cfg_mod._agent_contexts.update(old_ctx)
            cfg_mod._current_agent_var.set(old_var)

    def test_context_var_unknown_name_raises(self):
        import qd_evolve.tools.config_manager as cfg_mod

        old_ctx = cfg_mod._agent_contexts.copy()
        old_var = cfg_mod._current_agent_var.get()
        cfg_mod._agent_contexts.clear()
        cfg_mod._current_agent_var.set("ghost_name")
        try:
            with pytest.raises(RuntimeError, match="no agent context"):
                cfg_mod._require_context()
        finally:
            cfg_mod._agent_contexts.update(old_ctx)
            cfg_mod._current_agent_var.set(old_var)
