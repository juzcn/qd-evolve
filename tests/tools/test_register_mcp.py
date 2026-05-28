"""Tests for qd_evolve.tools.register_mcp — register_mcp handler."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import json
import pytest


class TestRegisterMcp:
    def test_register_moves_config(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "mcp"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "mcp"
        perm.mkdir(parents=True)

        staged_file = staging / "myserver.json"
        staged_file.write_text(json.dumps({"mcpServers": {"myserver": {"command": "node"}}}), encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_mcp._perm_mcp_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_mcp.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_mcp import _register_mcp
            result = _register_mcp("myserver")
            assert "registered" in result
            assert (perm / "myserver.json").exists()
            assert not staged_file.exists()

    def test_register_staged_not_found(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "mcp"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "mcp"
        perm.mkdir(parents=True)

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_mcp._perm_mcp_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_mcp.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_mcp import _register_mcp
            result = _register_mcp("nonexistent")
            assert "not found" in result

    def test_register_already_exists(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "mcp"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "mcp"
        perm.mkdir(parents=True)

        (staging / "myserver.json").write_text("{}", encoding="utf-8")
        (perm / "myserver.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_mcp._perm_mcp_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_mcp.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_mcp import _register_mcp
            result = _register_mcp("myserver")
            assert "already exists" in result

    def test_perm_mcp_dir(self):
        from qd_evolve.tools.register_mcp import _perm_mcp_dir
        from qd_evolve.core.config import MCP_DIR
        result = _perm_mcp_dir()
        assert result == Path.cwd() / MCP_DIR