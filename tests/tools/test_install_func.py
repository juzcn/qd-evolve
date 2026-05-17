"""Tests for qd_evolve.tools.install_func — install_func handler."""

from unittest.mock import MagicMock, patch

import pytest


class TestInstallFunc:
    def test_install_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()

        with patch("qd_evolve.tools.install_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.install_func import _install_func
            result = _install_func(
                name="my_tool",
                description="My custom tool",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                python_code="    return kwargs.get('x', '')",
            )
            assert "installed and hot-loaded" in result

    def test_registry_not_initialized(self):
        mock_registry = MagicMock()
        with patch("qd_evolve.tools.install_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.install_func import _install_func
            result = _install_func(
                name="my_tool",
                description="desc",
                input_schema={},
                python_code="    return ''",
            )
            assert "installed" in result