"""Tests for qd_evolve.tools.register_func — register_func handler."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestRegisterFunc:
    def test_register_moves_file(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "func"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "func"
        perm.mkdir(parents=True)

        staged_file = staging / "my_tool.py"
        staged_file.write_text("# my tool", encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_func._perm_func_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _register_func
            result = _register_func("my_tool")
            assert "registered" in result
            assert (perm / "my_tool.py").exists()
            assert not staged_file.exists()

    def test_register_staged_not_found(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "func"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "func"
        perm.mkdir(parents=True)

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_func._perm_func_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _register_func
            result = _register_func("nonexistent")
            assert "not found" in result

    def test_register_already_exists(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "func"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "func"
        perm.mkdir(parents=True)

        (staging / "my_tool.py").write_text("# staged", encoding="utf-8")
        (perm / "my_tool.py").write_text("# permanent", encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_func._perm_func_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _register_func
            result = _register_func("my_tool")
            assert "already exists" in result