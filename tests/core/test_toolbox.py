"""Tests for qd_evolve.core.toolbox — state management, toggle, apply, migration."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qd_evolve.core.toolbox import (
    get_state,
    get_disabled,
    get_preloaded,
    set_state,
    toggle,
    state_mark,
    get_default,
    get_disabled_bridges,
    apply_to_tools,
    migrate_toolbox_to_config,
)


def _make_config(toolbox_data: dict | None = None, toolbox_defaults: dict | None = None) -> dict:
    """Build a minimal config.json dict with optional toolbox data per agent."""
    agents = [{"name": "default"}, {"name": "other"}]
    if toolbox_data:
        for agent in agents:
            if agent["name"] in toolbox_data:
                agent["toolbox"] = toolbox_data[agent["name"]]
    cfg = {
        "max_iterations": 5,
        "tool_output_limit": 2000,
        "default_provider": "test",
        "default_model": "test-model",
        "agents_config": {"chat_agent": "default", "agents": agents},
    }
    if toolbox_defaults:
        cfg["toolbox_defaults"] = toolbox_defaults
    return cfg


@pytest.fixture
def toolbox_dir(tmp_path, monkeypatch):
    """Set up a temporary config.json for toolbox testing."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_make_config()), encoding="utf-8")
    monkeypatch.setattr("qd_evolve.core.toolbox.CONFIG_PATH", cfg_path)
    return cfg_path


class TestGetState:
    def test_default_enabled(self, toolbox_dir):
        assert get_state("tools", "echo") == "enabled"

    def test_disabled(self, toolbox_dir):
        tb = {"default": {"tools": {"echo": "disabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        assert get_state("tools", "echo") == "disabled"

    def test_preloaded(self, toolbox_dir):
        tb = {"default": {"tools": {"echo": "preload"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        assert get_state("tools", "echo") == "preload"

    def test_per_agent(self, toolbox_dir):
        tb = {"default": {"tools": {"echo": "disabled"}}, "other": {"tools": {"echo": "preload"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        assert get_state("tools", "echo", agent_name="other") == "preload"


class TestGetDisabled:
    def test_empty(self, toolbox_dir):
        assert get_disabled("tools") == set()

    def test_with_disabled(self, toolbox_dir):
        tb = {"default": {"tools": {"echo": "disabled", "fetch": "enabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        assert get_disabled("tools") == {"echo"}


class TestGetPreloaded:
    def test_empty(self, toolbox_dir):
        assert get_preloaded("tools") == set()

    def test_with_preload(self, toolbox_dir):
        tb = {"default": {"tools": {"echo": "preload", "fetch": "enabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        assert get_preloaded("tools") == {"echo"}


class TestSetState:
    def test_set_disabled(self, toolbox_dir):
        assert set_state("tools", "echo", "disabled") is True
        assert get_state("tools", "echo") == "disabled"

    def test_set_preload(self, toolbox_dir):
        assert set_state("tools", "echo", "preload") is True
        assert get_state("tools", "echo") == "preload"

    def test_set_enabled_removes_entry(self, toolbox_dir):
        set_state("tools", "echo", "disabled")
        set_state("tools", "echo", "enabled")
        assert get_state("tools", "echo") == "enabled"

    def test_invalid_state_rejected(self, toolbox_dir):
        assert set_state("tools", "echo", "invalid") is False

    def test_bridge_only_enabled_disabled(self, toolbox_dir):
        assert set_state("bridge", "oat:boat", "enabled") is True
        assert set_state("bridge", "oat:boat", "disabled") is True
        assert set_state("bridge", "oat:boat", "preload") is False

    def test_mcp_only_enabled_disabled(self, toolbox_dir):
        assert set_state("mcp_servers", "myserver", "enabled") is True
        assert set_state("mcp_servers", "myserver", "preload") is False


class TestToggle:
    def test_cycle_disabled_enabled_preload(self, toolbox_dir):
        assert toggle("tools", "echo") == "preload"  # enabled → preload
        assert toggle("tools", "echo") == "disabled"  # preload → disabled
        assert toggle("tools", "echo") == "enabled"  # disabled → enabled
        assert toggle("tools", "echo") == "preload"  # enabled → preload

    def test_bridge_toggle(self, toolbox_dir):
        assert toggle("bridge", "oat:boat") == "disabled"  # enabled → disabled
        assert toggle("bridge", "oat:boat") == "enabled"   # disabled → enabled


class TestStateMark:
    def test_enabled(self):
        assert state_mark("enabled") == "[✓]"

    def test_preload(self):
        assert state_mark("preload") == "[P]"

    def test_disabled(self):
        assert state_mark("disabled") == "[✗]"

    def test_unknown(self):
        assert state_mark("unknown") == "[?]"


class TestGetDefault:
    def test_existing_key(self, toolbox_dir):
        cfg = _make_config(toolbox_defaults={"timeout": 60})
        toolbox_dir.write_text(json.dumps(cfg), encoding="utf-8")
        assert get_default("timeout") == 60

    def test_missing_key(self, toolbox_dir):
        assert get_default("timeout", 30) == 30

    def test_no_file(self, toolbox_dir):
        toolbox_dir.unlink()
        assert get_default("timeout", 30) == 30


class TestGetDisabledBridges:
    def test_combines_bridge_and_mcp(self, toolbox_dir):
        tb = {"default": {"bridge": {"oat:boat": "disabled"}, "mcp_servers": {"myserver": "disabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        disabled = get_disabled_bridges()
        assert "oat:boat" in disabled
        assert "mcp:myserver" in disabled

    def test_empty(self, toolbox_dir):
        tb = {"default": {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        assert get_disabled_bridges() == set()


class TestApplyToTools:
    def test_disables_tools(self, toolbox_dir, registry):
        registry.register("echo", "Echo", lambda s: s)
        registry.register("fetch", "Fetch", lambda u: u)
        tb = {"default": {"tools": {"echo": "disabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        preload = set()
        apply_to_tools(registry, preload)
        assert registry.get("echo").enabled is False
        assert registry.get("fetch").enabled is True

    def test_preloads_tools(self, toolbox_dir, registry):
        registry.register("echo", "Echo", lambda s: s)
        tb = {"default": {"tools": {"echo": "preload"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")
        preload = set()
        apply_to_tools(registry, preload)
        assert "echo" in preload


class TestLoadAndSave:
    def test_load_no_config_file(self, toolbox_dir, monkeypatch):
        toolbox_dir.unlink()
        from qd_evolve.core.toolbox import _load
        result = _load()
        # Returns a dict with all 5 sections
        assert "tools" in result
        assert "mcp_servers" in result
        assert "bridge" in result
        assert "cli" in result
        assert "skills" in result

    def test_load_agent_not_found(self, toolbox_dir):
        from qd_evolve.core.toolbox import _load
        result = _load("nonexistent_agent")
        # Returns a copy of _EMPTY — check structure, not exact equality
        assert "tools" in result
        assert "mcp_servers" in result
        assert "bridge" in result
        assert "cli" in result
        assert "skills" in result

    def test_save_creates_new_agent(self, toolbox_dir):
        from qd_evolve.core.toolbox import _save, _load
        data = {"tools": {"echo": "preload"}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}
        _save(data, agent_name="new_agent")
        result = _load("new_agent")
        assert result["tools"]["echo"] == "preload"

    def test_save_no_config_file_creates_data(self, toolbox_dir, monkeypatch):
        toolbox_dir.unlink()
        from qd_evolve.core.toolbox import _save
        data = {"tools": {"echo": "preload"}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}
        _save(data, agent_name="new_agent")
        assert toolbox_dir.exists()


class TestApplyToCliRegistry:
    def test_disables_cli_tools(self, toolbox_dir):
        from qd_evolve.core.toolbox import apply_to_cli_registry
        tb = {"default": {"cli": {"pandoc": "disabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")

        mock_registry = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "pandoc"
        mock_registry.list_tools.return_value = [mock_tool]
        mock_registry._disabled = set()

        preload = set()
        apply_to_cli_registry(mock_registry, preload)
        assert "pandoc" in mock_registry._disabled

    def test_preloads_cli_tools(self, toolbox_dir):
        from qd_evolve.core.toolbox import apply_to_cli_registry
        tb = {"default": {"cli": {"pandoc": "preload"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")

        mock_registry = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "pandoc"
        mock_registry.list_tools.return_value = [mock_tool]
        mock_registry._disabled = set()

        preload = set()
        apply_to_cli_registry(mock_registry, preload)
        assert "pandoc" in preload
        assert "pandoc" not in mock_registry._disabled


class TestApplyToSkillRegistry:
    def test_disables_skills(self, toolbox_dir):
        from qd_evolve.core.toolbox import apply_to_skill_registry
        tb = {"default": {"skills": {"find-tools": "disabled"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")

        mock_registry = MagicMock()
        mock_skill = MagicMock()
        mock_skill.name = "find-tools"
        mock_registry.get_all_skills.return_value = [mock_skill]
        mock_registry._disabled = set()

        preload = set()
        apply_to_skill_registry(mock_registry, preload)
        assert "find-tools" in mock_registry._disabled

    def test_preloads_skills(self, toolbox_dir):
        from qd_evolve.core.toolbox import apply_to_skill_registry
        tb = {"default": {"skills": {"find-tools": "preload"}}}
        toolbox_dir.write_text(json.dumps(_make_config(tb)), encoding="utf-8")

        mock_registry = MagicMock()
        mock_skill = MagicMock()
        mock_skill.name = "find-tools"
        mock_registry.get_all_skills.return_value = [mock_skill]
        mock_registry._disabled = set()

        preload = set()
        apply_to_skill_registry(mock_registry, preload)
        assert "find-tools" in preload


class TestSetStateNewSection:
    def test_set_state_creates_new_section(self, toolbox_dir):
        # When section doesn't exist in toolbox data, set_state creates it
        set_state("cli", "pandoc", "preload")
        assert get_state("cli", "pandoc") == "preload"


class TestMigrateToolboxToConfig:
    def test_merges_toolbox_into_config(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.json"
        tb_path = tmp_path / "toolbox.json"

        cfg_data = {
            "max_iterations": 5,
            "tool_output_limit": 2000,
            "agents_config": {
                "agents": [{"name": "default"}],
            },
        }
        cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")

        tb_data = {
            "defaults": {"timeout": 90},
            "agents": {
                "default": {"tools": {"echo": "preload"}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}},
            },
        }
        tb_path.write_text(json.dumps(tb_data), encoding="utf-8")

        monkeypatch.setattr("qd_evolve.core.toolbox.CONFIG_PATH", cfg_path)
        monkeypatch.setattr("qd_evolve.core.toolbox.TOOLBOX_MIGRATION_PATH", tb_path)
        migrate_toolbox_to_config()

        result = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert result["toolbox_defaults"]["timeout"] == 90
        assert result["agents_config"]["agents"][0]["toolbox"]["tools"]["echo"] == "preload"
        assert not tb_path.exists()
        assert (tmp_path / "toolbox.json.bak").exists()

    def test_no_toolbox_json_is_noop(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "config.json"
        cfg_data = {"max_iterations": 5, "tool_output_limit": 2000}
        cfg_path.write_text(json.dumps(cfg_data), encoding="utf-8")
        monkeypatch.setattr("qd_evolve.core.toolbox.CONFIG_PATH", cfg_path)

        migrate_toolbox_to_config()
        # config unchanged, no toolbox.json.bak
        result = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "toolbox_defaults" not in result

    def test_no_config_file_is_noop(self, tmp_path, monkeypatch):
        tb_path = tmp_path / "toolbox.json"
        tb_data = {"defaults": {"timeout": 90}, "agents": {}}
        tb_path.write_text(json.dumps(tb_data), encoding="utf-8")

        cfg_path = tmp_path / "config.json"
        monkeypatch.setattr("qd_evolve.core.toolbox.CONFIG_PATH", cfg_path)
        monkeypatch.setattr("qd_evolve.core.toolbox.TOOLBOX_MIGRATION_PATH", tb_path)
        migrate_toolbox_to_config()
        # toolbox.json still exists (no config to merge into)
        assert tb_path.exists()