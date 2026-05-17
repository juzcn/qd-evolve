"""Tests for qd_evolve.core.toolbox — state management, toggle, apply."""

import json
from pathlib import Path
from unittest.mock import patch

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
)


@pytest.fixture
def toolbox_dir(tmp_path, monkeypatch):
    """Set up a temporary toolbox.json for testing."""
    tb_path = tmp_path / "toolbox.json"
    monkeypatch.setattr("qd_evolve.core.toolbox.TOOLBOX_PATH", tb_path)
    return tb_path


class TestGetState:
    def test_default_enabled(self, toolbox_dir):
        assert get_state("tools", "echo") == "enabled"

    def test_disabled(self, toolbox_dir):
        data = {"agents": {"default": {"tools": {"echo": "disabled"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        assert get_state("tools", "echo") == "disabled"

    def test_preloaded(self, toolbox_dir):
        data = {"agents": {"default": {"tools": {"echo": "preload"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        assert get_state("tools", "echo") == "preload"

    def test_per_agent(self, toolbox_dir):
        data = {"agents": {"default": {"tools": {"echo": "disabled"}}, "other": {"tools": {"echo": "preload"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        assert get_state("tools", "echo", agent_name="other") == "preload"


class TestGetDisabled:
    def test_empty(self, toolbox_dir):
        assert get_disabled("tools") == set()

    def test_with_disabled(self, toolbox_dir):
        data = {"agents": {"default": {"tools": {"echo": "disabled", "fetch": "enabled"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        assert get_disabled("tools") == {"echo"}


class TestGetPreloaded:
    def test_empty(self, toolbox_dir):
        assert get_preloaded("tools") == set()

    def test_with_preload(self, toolbox_dir):
        data = {"agents": {"default": {"tools": {"echo": "preload", "fetch": "enabled"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
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
        assert toggle("tools", "echo") == "enabled"  # default → enabled → preload
        assert toggle("tools", "echo") == "preload"
        assert toggle("tools", "echo") == "disabled"
        assert toggle("tools", "echo") == "enabled"

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
        data = {"defaults": {"timeout": 60}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        assert get_default("timeout") == 60

    def test_missing_key(self, toolbox_dir):
        assert get_default("timeout", 30) == 30

    def test_no_file(self, toolbox_dir):
        assert get_default("timeout", 30) == 30


class TestGetDisabledBridges:
    def test_combines_bridge_and_mcp(self, toolbox_dir):
        data = {"agents": {"default": {"bridge": {"oat:boat": "disabled"}, "mcp_servers": {"myserver": "disabled"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        disabled = get_disabled_bridges()
        assert "oat:boat" in disabled
        assert "mcp:myserver" in disabled

    def test_empty(self, toolbox_dir):
        # Write empty toolbox.json to ensure no disabled bridges
        toolbox_dir.write_text(json.dumps({"agents": {"default": {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}}}), encoding="utf-8")
        assert get_disabled_bridges() == set()


class TestApplyToTools:
    def test_disables_tools(self, toolbox_dir, registry):
        registry.register("echo", "Echo", lambda s: s)
        registry.register("fetch", "Fetch", lambda u: u)
        data = {"agents": {"default": {"tools": {"echo": "disabled"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        preload = set()
        apply_to_tools(registry, preload)
        assert registry.get("echo").enabled is False
        assert registry.get("fetch").enabled is True

    def test_preloads_tools(self, toolbox_dir, registry):
        registry.register("echo", "Echo", lambda s: s)
        data = {"agents": {"default": {"tools": {"echo": "preload"}}}}
        toolbox_dir.write_text(json.dumps(data), encoding="utf-8")
        preload = set()
        apply_to_tools(registry, preload)
        assert "echo" in preload