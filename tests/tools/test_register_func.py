"""Tests for qd_evolve.tools.register_func — register_func handler."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch



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

    def test_register_moves_deps_file(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "func"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "func"
        perm.mkdir(parents=True)

        (staging / "my_tool.py").write_text("# my tool", encoding="utf-8")
        deps = {"pip_packages": ["requests>=2.0"]}
        (staging / "my_tool_deps.json").write_text(json.dumps(deps), encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_func._perm_func_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.register_func._add_to_pyproject") as mock_add:
                from qd_evolve.tools.register_func import _register_func
                result = _register_func("my_tool")
                assert "registered" in result
                mock_add.assert_called_once_with(["requests>=2.0"])
                assert not (staging / "my_tool_deps.json").exists()

    def test_register_invalid_deps_json(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "func"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "func"
        perm.mkdir(parents=True)

        (staging / "my_tool.py").write_text("# my tool", encoding="utf-8")
        (staging / "my_tool_deps.json").write_text("invalid json", encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_func._perm_func_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _register_func
            result = _register_func("my_tool")
            assert "registered" in result
            # deps file still cleaned up even on parse error
            assert not (staging / "my_tool_deps.json").exists()


class TestAddToPyproject:
    def test_adds_new_dependency(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\ndependencies = ["click>=8.0"]\n', encoding="utf-8")

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _add_to_pyproject
            _add_to_pyproject(["requests>=2.0"])

        import tomlkit
        doc = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        deps = [str(d) for d in doc["project"]["dependencies"]]
        assert "requests>=2.0" in deps
        assert "click>=8.0" in deps

    def test_skips_existing_dependency(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\ndependencies = ["requests>=2.0"]\n', encoding="utf-8")

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _add_to_pyproject
            _add_to_pyproject(["requests>=2.33"])

        import tomlkit
        doc = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        assert len(doc["project"]["dependencies"]) == 1

    def test_no_pyproject(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _add_to_pyproject
            _add_to_pyproject(["requests>=2.0"])  # should not crash

    def test_no_dependencies_section(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nname = "test"\n', encoding="utf-8")

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _add_to_pyproject
            _add_to_pyproject(["requests>=2.0"])  # should not crash

    def test_invalid_toml_handled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("invalid toml {{{", encoding="utf-8")

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_func.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_func import _add_to_pyproject
            _add_to_pyproject(["requests>=2.0"])  # should not crash

    def test_perm_func_dir(self):
        from qd_evolve.tools.register_func import _perm_func_dir
        from qd_evolve.core.config import FUNC_TOOLS_DIR
        result = _perm_func_dir()
        assert result == Path.cwd() / FUNC_TOOLS_DIR