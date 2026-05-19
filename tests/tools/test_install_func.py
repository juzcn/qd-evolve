"""Tests for qd_evolve.tools.install_func — install_func handler."""

import json
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

    def test_pip_install_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.install_func.get_registry", return_value=mock_registry):
            with patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip")):
                from qd_evolve.tools.install_func import _install_func
                result = _install_func(
                    name="my_tool",
                    description="desc",
                    input_schema={},
                    python_code="    return ''",
                    pip_packages=["nonexistent-package"],
                )
                assert "Error" in result
                assert "package install failed" in result

    def test_pip_packages_none_skips_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.install_func.get_registry", return_value=mock_registry):
            with patch("subprocess.check_call") as mock_call:
                from qd_evolve.tools.install_func import _install_func
                result = _install_func(
                    name="my_tool",
                    description="desc",
                    input_schema={},
                    python_code="    return ''",
                )
                mock_call.assert_not_called()
                assert "installed" in result

    def test_deps_file_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.install_func.get_registry", return_value=mock_registry):
            with patch("subprocess.check_call"):
                from qd_evolve.tools.install_func import _install_func
                result = _install_func(
                    name="my_tool",
                    description="desc",
                    input_schema={},
                    python_code="    return ''",
                    pip_packages=["requests>=2.0"],
                )
                assert "installed" in result
                deps_file = tmp_path / ".qd_evolve" / "staging" / "func" / "my_tool_deps.json"
                assert deps_file.exists()
                deps = json.loads(deps_file.read_text(encoding="utf-8"))
                assert deps["pip_packages"] == ["requests>=2.0"]

    def test_spec_creation_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        from qd_evolve.tools.staging import ensure_staging_dirs
        ensure_staging_dirs()

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.install_func.get_registry", return_value=mock_registry):
            with patch("importlib.util.spec_from_file_location", return_value=None):
                from qd_evolve.tools.install_func import _install_func
                result = _install_func(
                    name="my_tool",
                    description="desc",
                    input_schema={},
                    python_code="    return ''",
                )
                assert "Error" in result
                assert "could not create module spec" in result