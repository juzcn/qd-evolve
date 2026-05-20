"""Tests for toolbox state management."""

import json
from pathlib import Path

import pytest

from qd_evolve.core.toolbox import get_state, set_state, toggle


def _make_config(tmp_path: Path) -> Path:
    """Create a minimal config.json in tmp_path and return its path."""
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "max_iterations": 5,
                "tool_output_limit": 2000,
                "default_provider": "test",
                "default_model": "test-model",
                "agents_config": {
                    "chat_agent": "default",
                    "agents": [{"name": "default"}, {"name": "other"}],
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


@pytest.fixture
def toolbox_dir(tmp_path, monkeypatch):
    """Provide an isolated config.json and patch CONFIG_PATH."""
    cfg_path = _make_config(tmp_path)
    import qd_evolve.core.toolbox as tb

    monkeypatch.setattr(tb, "CONFIG_PATH", cfg_path)
    return tmp_path


class TestGetState:
    def test_default_enabled(self, toolbox_dir):
        assert get_state("tools", "echo") == "enabled"

    def test_default_bridge_enabled(self, toolbox_dir):
        assert get_state("bridge", "oat:boat") == "enabled"

    def test_nonexistent_section(self, toolbox_dir):
        assert get_state("skills", "foo") == "enabled"


class TestSetState:
    def test_set_disabled(self, toolbox_dir):
        set_state("tools", "echo", "disabled")
        assert get_state("tools", "echo") == "disabled"

    def test_set_preload(self, toolbox_dir):
        set_state("tools", "echo", "preload")
        assert get_state("tools", "echo") == "preload"

    def test_set_enabled_removes_entry(self, toolbox_dir):
        set_state("tools", "echo", "disabled")
        set_state("tools", "echo", "enabled")
        # "enabled" is the default, so the entry should be removed
        assert get_state("tools", "echo") == "enabled"


class TestToggle:
    def test_cycle_disabled_enabled_preload(self, toolbox_dir):
        assert toggle("tools", "echo") == "preload"  # enabled → preload
        assert toggle("tools", "echo") == "disabled"  # preload → disabled
        assert toggle("tools", "echo") == "enabled"  # disabled → enabled
        assert toggle("tools", "echo") == "preload"  # enabled → preload

    def test_bridge_toggle(self, toolbox_dir):
        assert toggle("bridge", "oat:boat") == "disabled"  # enabled → disabled
        assert toggle("bridge", "oat:boat") == "enabled"   # disabled → enabled
