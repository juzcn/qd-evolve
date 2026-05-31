"""Tests for toolbox state management."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qd_evolve.core.toolbox import (
    get_state,
    set_state,
    toggle,
    get_disabled,
    get_preloaded,
    get_disabled_bridges,
        migrate_toolbox_to_config,
    state_mark,
    apply_to_tools,
    apply_to_cli_registry,
    apply_to_skill_registry,
    _load,
    _save,
)


def _make_config(tmp_path: Path, agents: list[dict] | None = None) -> Path:
    """Create a minimal config.json in tmp_path and return its path."""
    data = {
        "max_iterations": 5,
        "tool_output_limit": 2000,
        "default_provider": "test",
        "default_model": "test-model",
        "agents_config": {
            "chat_agent": "default",
            "agents": agents or [{"name": "default"}, {"name": "other"}],
        },
    }
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg


def _make_config_with_toolbox(tmp_path: Path) -> Path:
    """Create config.json with pre-populated toolbox sections."""
    agents = [
        {
            "name": "default",
            "toolbox": {
                "tools": {"fetch": "preload", "run_shell": "disabled"},
                "mcp_servers": {"mcp1": "disabled"},
                "bridge": {"oat:boat": "disabled"},
                "cli": {"git": "preload"},
                "skills": {"find-tools": "disabled"},
            },
        },
        {"name": "other"},
    ]
    return _make_config(tmp_path, agents=agents)


@pytest.fixture
def toolbox_dir(tmp_path, monkeypatch):
    """Provide an isolated config.json and patch CONFIG_PATH."""
    cfg_path = _make_config(tmp_path)
    import qd_evolve.core.toolbox as tb
    monkeypatch.setattr(tb, "CONFIG_PATH", cfg_path)
    return tmp_path


@pytest.fixture
def toolbox_dir_with_data(tmp_path, monkeypatch):
    """Provide config.json with pre-populated toolbox data."""
    cfg_path = _make_config_with_toolbox(tmp_path)
    import qd_evolve.core.toolbox as tb
    monkeypatch.setattr(tb, "CONFIG_PATH", cfg_path)
    return tmp_path


AGENT = "default"


class TestGetState:
    def test_default_enabled(self, toolbox_dir):
        assert get_state("tools", "echo", agent_name=AGENT) == "enabled"

    def test_default_bridge_enabled(self, toolbox_dir):
        assert get_state("bridge", "oat:boat", agent_name=AGENT) == "enabled"

    def test_nonexistent_section(self, toolbox_dir):
        assert get_state("skills", "foo", agent_name=AGENT) == "enabled"

    def test_preloaded_state(self, toolbox_dir_with_data):
        assert get_state("tools", "fetch", agent_name=AGENT) == "preload"

    def test_disabled_state(self, toolbox_dir_with_data):
        assert get_state("tools", "run_shell", agent_name=AGENT) == "disabled"


class TestGetDisabled:
    def test_disabled_tools(self, toolbox_dir_with_data):
        result = get_disabled("tools", agent_name=AGENT)
        assert "run_shell" in result
        assert "fetch" not in result

    def test_no_disabled(self, toolbox_dir):
        result = get_disabled("tools", agent_name=AGENT)
        assert result == set()

    def test_disabled_mcp_servers(self, toolbox_dir_with_data):
        result = get_disabled("mcp_servers", agent_name=AGENT)
        assert "mcp1" in result


class TestGetPreloaded:
    def test_preloaded_tools(self, toolbox_dir_with_data):
        result = get_preloaded("tools", agent_name=AGENT)
        assert "fetch" in result
        assert "run_shell" not in result

    def test_preloaded_cli(self, toolbox_dir_with_data):
        result = get_preloaded("cli", agent_name=AGENT)
        assert "git" in result

    def test_no_preloaded(self, toolbox_dir):
        result = get_preloaded("tools", agent_name=AGENT)
        assert result == set()


class TestSetState:
    def test_set_disabled(self, toolbox_dir):
        set_state("tools", "echo", "disabled", agent_name=AGENT)
        assert get_state("tools", "echo", agent_name=AGENT) == "disabled"

    def test_set_preload(self, toolbox_dir):
        set_state("tools", "echo", "preload", agent_name=AGENT)
        assert get_state("tools", "echo", agent_name=AGENT) == "preload"

    def test_set_enabled_removes_entry(self, toolbox_dir):
        set_state("tools", "echo", "disabled", agent_name=AGENT)
        set_state("tools", "echo", "enabled", agent_name=AGENT)
        # "enabled" is the default, so the entry should be removed
        assert get_state("tools", "echo", agent_name=AGENT) == "enabled"

    def test_invalid_state_returns_false(self, toolbox_dir):
        assert set_state("tools", "echo", "invalid", agent_name=AGENT) is False

    def test_bridge_invalid_preload_returns_false(self, toolbox_dir):
        assert set_state("bridge", "x", "preload", agent_name=AGENT) is False

    def test_bridge_set_enabled(self, toolbox_dir):
        assert set_state("bridge", "x", "disabled", agent_name=AGENT) is True
        assert set_state("bridge", "x", "enabled", agent_name=AGENT) is True

    def test_mcp_set_disabled(self, toolbox_dir):
        assert set_state("mcp_servers", "mcp1", "disabled", agent_name=AGENT) is True


class TestToggle:
    def test_cycle_disabled_enabled_preload(self, toolbox_dir):
        assert toggle("tools", "echo", agent_name=AGENT) == "preload"  # enabled → preload
        assert toggle("tools", "echo", agent_name=AGENT) == "disabled"  # preload → disabled
        assert toggle("tools", "echo", agent_name=AGENT) == "enabled"  # disabled → enabled
        assert toggle("tools", "echo", agent_name=AGENT) == "preload"  # enabled → preload

    def test_bridge_toggle(self, toolbox_dir):
        assert toggle("bridge", "oat:boat", agent_name=AGENT) == "disabled"  # enabled → disabled
        assert toggle("bridge", "oat:boat", agent_name=AGENT) == "enabled"   # disabled → enabled


class TestApplyToTools:
    def test_disabled_tool(self, toolbox_dir_with_data):
        mock_td = MagicMock()
        mock_td.name = "run_shell"
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = [mock_td]

        preload = set()
        apply_to_tools(mock_registry, preload, agent_name=AGENT)
        assert mock_td.enabled is False

    def test_preload_tool(self, toolbox_dir_with_data):
        mock_td = MagicMock()
        mock_td.name = "fetch"
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = [mock_td]

        preload = set()
        apply_to_tools(mock_registry, preload, agent_name=AGENT)
        assert mock_td.enabled is True
        assert "fetch" in preload

    def test_enabled_tool(self, toolbox_dir):
        mock_td = MagicMock()
        mock_td.name = "echo"
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = [mock_td]

        preload = set()
        apply_to_tools(mock_registry, preload, agent_name=AGENT)
        assert mock_td.enabled is True
        assert "echo" not in preload


class TestApplyToCliRegistry:
    def test_preload_cli(self, toolbox_dir_with_data):
        mock_tool = MagicMock()
        mock_tool.name = "git"
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = [mock_tool]
        mock_registry._disabled = set()

        preload = set()
        apply_to_cli_registry(mock_registry, preload, agent_name=AGENT)
        assert "git" not in mock_registry._disabled
        assert "git" in preload

    def test_disabled_cli(self, toolbox_dir_with_data):
        # Create a CLI tool that is disabled
        set_state("cli", "npm", "disabled", agent_name=AGENT)
        mock_tool = MagicMock()
        mock_tool.name = "npm"
        mock_registry = MagicMock()
        mock_registry.list_tools.return_value = [mock_tool]
        mock_registry._disabled = set()

        preload = set()
        apply_to_cli_registry(mock_registry, preload, agent_name=AGENT)
        assert "npm" in mock_registry._disabled


class TestApplyToSkillRegistry:
    def test_disabled_skill(self, toolbox_dir_with_data):
        mock_skill = MagicMock()
        mock_skill.name = "find-tools"
        mock_registry = MagicMock()
        mock_registry.get_all_skills.return_value = [mock_skill]
        mock_registry._disabled = set()

        preload = set()
        apply_to_skill_registry(mock_registry, preload, agent_name=AGENT)
        assert "find-tools" in mock_registry._disabled


class TestGetDisabledBridges:
    def test_includes_bridge_and_mcp(self, toolbox_dir_with_data):
        result = get_disabled_bridges(agent_name=AGENT)
        assert "oat:boat" in result
        assert "mcp:mcp1" in result

    def test_empty_when_none_disabled(self, toolbox_dir):
        result = get_disabled_bridges(agent_name=AGENT)
        assert result == set()


class TestMigrateToolbox:
    def test_migrates_agent_toolbox(self, tmp_path, monkeypatch):
        agent_toolbox = {"tools": {"fetch": "preload"}, "cli": {}, "skills": {}, "mcp_servers": {}, "bridge": {}}
        tb_data = {
            "defaults": {},
            "agents": {"default": agent_toolbox},
        }
        tb_path = tmp_path / "toolbox.json"
        tb_path.write_text(json.dumps(tb_data), encoding="utf-8")

        cfg_path = _make_config(tmp_path, agents=[{"name": "default"}])
        import qd_evolve.core.toolbox as tb
        monkeypatch.setattr(tb, "CONFIG_PATH", cfg_path)
        monkeypatch.setattr(tb, "TOOLBOX_MIGRATION_PATH", tb_path)

        migrate_toolbox_to_config()

        cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
        agent = cfg_data["agents_config"]["agents"][0]
        assert agent["toolbox"]["tools"]["fetch"] == "preload"

    def test_no_toolbox_json_skips(self, tmp_path, monkeypatch):
        cfg_path = _make_config(tmp_path)
        import qd_evolve.core.toolbox as tb
        monkeypatch.setattr(tb, "CONFIG_PATH", cfg_path)
        monkeypatch.setattr(tb, "TOOLBOX_MIGRATION_PATH", tmp_path / "toolbox.json")

        # Should not raise
        migrate_toolbox_to_config()


class TestStateMark:
    def test_enabled(self):
        assert state_mark("enabled") == "[✓]"

    def test_preload(self):
        assert state_mark("preload") == "[P]"

    def test_disabled(self):
        assert state_mark("disabled") == "[✗]"

    def test_unknown(self):
        assert state_mark("unknown") == "[?]"


class TestLoadSave:
    def test_load_with_toolbox_data(self, toolbox_dir_with_data):
        data = _load(AGENT)
        assert data["tools"]["fetch"] == "preload"
        assert data["tools"]["run_shell"] == "disabled"

    def test_load_missing_agent(self, toolbox_dir):
        data = _load("nonexistent_agent")
        assert data == {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}

    def test_save_new_agent(self, toolbox_dir):
        data = {"tools": {"echo": "preload"}, "cli": {}, "skills": {}, "mcp_servers": {}, "bridge": {}}
        _save(data, "brand_new_agent")

        loaded = _load("brand_new_agent")
        assert loaded["tools"]["echo"] == "preload"

    def test_save_updates_existing_agent(self, toolbox_dir):
        data = {"tools": {"new_tool": "disabled"}, "cli": {}, "skills": {}, "mcp_servers": {}, "bridge": {}}
        _save(data, AGENT)

        loaded = _load(AGENT)
        assert loaded["tools"]["new_tool"] == "disabled"

    def test_save_without_config_json(self, tmp_path, monkeypatch):
        import qd_evolve.core.toolbox as tb
        nonexistent = tmp_path / "nonexistent_config.json"
        monkeypatch.setattr(tb, "CONFIG_PATH", nonexistent)

        data = {"tools": {"echo": "preload"}, "cli": {}, "skills": {}, "mcp_servers": {}, "bridge": {}}
        _save(data, "new_agent")

        assert nonexistent.exists()

    def test_set_state_new_section(self, toolbox_dir):
        assert set_state("skills", "myskill", "preload", agent_name=AGENT) is True
        assert get_state("skills", "myskill", agent_name=AGENT) == "preload"

    def test_apply_to_skill_preload(self, toolbox_dir):
        set_state("skills", "myskill", "preload", agent_name=AGENT)
        mock_skill = MagicMock()
        mock_skill.name = "myskill"
        mock_registry = MagicMock()
        mock_registry.get_all_skills.return_value = [mock_skill]
        mock_registry._disabled = set()

        preload = set()
        apply_to_skill_registry(mock_registry, preload, agent_name=AGENT)
        assert "myskill" in preload

    def test_apply_to_skill_enabled(self, toolbox_dir):
        mock_skill = MagicMock()
        mock_skill.name = "enabled_skill"
        mock_registry = MagicMock()
        mock_registry.get_all_skills.return_value = [mock_skill]
        mock_registry._disabled = {"other_disabled"}

        preload = set()
        apply_to_skill_registry(mock_registry, preload, agent_name=AGENT)
        assert "enabled_skill" not in preload
        assert "other_disabled" in mock_registry._disabled  # not cleared

    def test_migrate_no_config_json(self, tmp_path, monkeypatch):
        tb_path = tmp_path / "toolbox.json"
        tb_path.write_text(json.dumps({"defaults": {}, "agents": {}}), encoding="utf-8")

        nonexistent_cfg = tmp_path / "nonexistent_config.json"
        import qd_evolve.core.toolbox as tb
        monkeypatch.setattr(tb, "CONFIG_PATH", nonexistent_cfg)
        monkeypatch.setattr(tb, "TOOLBOX_MIGRATION_PATH", tb_path)

        # Should not raise — just return
        migrate_toolbox_to_config()

    def test_migrate_defaults(self, tmp_path, monkeypatch):
        tb_data = {
            "defaults": {"fetch": "preload", "run_shell": "disabled"},
            "agents": {},
        }
        tb_path = tmp_path / "toolbox.json"
        tb_path.write_text(json.dumps(tb_data), encoding="utf-8")

        cfg_path = _make_config(tmp_path, agents=[{"name": "default"}])
        import qd_evolve.core.toolbox as tb
        monkeypatch.setattr(tb, "CONFIG_PATH", cfg_path)
        monkeypatch.setattr(tb, "TOOLBOX_MIGRATION_PATH", tb_path)

        migrate_toolbox_to_config()

        cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert cfg_data["toolbox_defaults"]["fetch"] == "preload"
        assert cfg_data["toolbox_defaults"]["run_shell"] == "disabled"