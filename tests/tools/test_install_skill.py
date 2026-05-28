"""Tests for qd_evolve.tools.install_skill — install_skill handler."""

from unittest.mock import MagicMock, patch

import pytest


class TestInstallSkill:
    def test_registry_not_initialized(self):
        from qd_evolve.tools import install_skill as mod
        mod._skill_registry = None
        with patch("qd_evolve.tools.install_skill.get_registry", return_value=MagicMock()):
            from qd_evolve.tools.install_skill import _install_skill
            result = _install_skill(name="test", github_url="https://github.com/test/skill")
            assert "Error" in result
            assert "skill registry not initialized" in result

    def test_git_clone_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "git")):
                    from qd_evolve.tools.install_skill import _install_skill
                    result = _install_skill(name="test", github_url="https://github.com/nonexistent/repo")
                    assert "Error" in result
                    assert "git clone failed" in result

    def test_skill_md_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        # Create a fake repo dir without SKILL.md
        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()

        # Mock tempfile.TemporaryDirectory to return our empty dir instead of cloning
        class _FakeTempDir:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return str(fake_repo)
            def __exit__(self, *args):
                pass

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call"):
                    with patch("tempfile.TemporaryDirectory", new=_FakeTempDir):
                        from qd_evolve.tools.install_skill import _install_skill
                        result = _install_skill(name="test", github_url="https://github.com/test/skill")
                        assert "Error" in result
                        assert "SKILL.md not found" in result

    def test_pip_install_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call", side_effect=__import__("subprocess").CalledProcessError(1, "pip")):
                    from qd_evolve.tools.install_skill import _install_skill
                    result = _install_skill(
                        name="test",
                        github_url="https://github.com/test/skill",
                        pip_packages=["nonexistent"],
                    )
                    assert "Error" in result
                    assert "package install failed" in result

    def test_set_skill_registry(self):
        from qd_evolve.tools.install_skill import set_skill_registry
        mock_reg = MagicMock()
        set_skill_registry(mock_reg)
        from qd_evolve.tools import install_skill as mod
        assert mod._skill_registry == mock_reg
        mod._skill_registry = None

    def test_successful_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        (fake_repo / "SKILL.md").write_text("---\nname: test-skill\ndescription: A test skill\n---\n\n# Content", encoding="utf-8")
        (fake_repo / "helper.py").write_text("print('hello')", encoding="utf-8")

        class _FakeTempDir:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return str(fake_repo)
            def __exit__(self, *args):
                pass

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call"):
                    with patch("tempfile.TemporaryDirectory", new=_FakeTempDir):
                        from qd_evolve.tools.install_skill import _install_skill
                        result = _install_skill(name="test", github_url="https://github.com/test/skill")
                        assert "installed" in result
                        mock_skill_registry.add_skill.assert_called_once()

    def test_successful_install_with_subdir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        sub = fake_repo / "subdir"
        sub.mkdir()
        (sub / "SKILL.md").write_text("---\nname: sub-skill\n---\n\n# Sub content", encoding="utf-8")

        class _FakeTempDir:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return str(fake_repo)
            def __exit__(self, *args):
                pass

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call"):
                    with patch("tempfile.TemporaryDirectory", new=_FakeTempDir):
                        from qd_evolve.tools.install_skill import _install_skill
                        result = _install_skill(name="test", github_url="https://github.com/test/skill", subdir="subdir")
                        assert "installed" in result

    def test_pip_install_fallback_pip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("shutil.which", return_value=None):
                    with patch("subprocess.check_call") as mock_call:
                        from qd_evolve.tools.install_skill import _install_skill
                        _install_skill(name="test", github_url="https://github.com/test/skill", pip_packages=["requests"])
                        mock_call.assert_called()

    def test_install_with_subdirectory_in_repo(self, tmp_path, monkeypatch):
        """Test copying a repo with subdirectories (hits copytree path)."""
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        (fake_repo / "SKILL.md").write_text("---\nname: dir-skill\n---\n\n# Content", encoding="utf-8")
        subdir = fake_repo / "scripts"
        subdir.mkdir()
        (subdir / "helper.py").write_text("print('hello')", encoding="utf-8")

        class _FakeTempDir:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return str(fake_repo)
            def __exit__(self, *args):
                pass

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call"):
                    with patch("tempfile.TemporaryDirectory", new=_FakeTempDir):
                        from qd_evolve.tools.install_skill import _install_skill
                        result = _install_skill(name="dir-test", github_url="https://github.com/test/skill")
                        assert "installed" in result

    def test_existing_staging_dir_removed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")

        from qd_evolve.tools.staging import ensure_staging_dirs, staging_skill_dir
        ensure_staging_dirs()
        existing = staging_skill_dir() / "test"
        existing.mkdir(parents=True)
        (existing / "old_file.txt").write_text("old", encoding="utf-8")

        mock_registry = MagicMock()
        mock_skill_registry = MagicMock()

        fake_repo = tmp_path / "fake_repo"
        fake_repo.mkdir()
        (fake_repo / "SKILL.md").write_text("---\nname: test-skill\n---\n\n# Content", encoding="utf-8")

        class _FakeTempDir:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return str(fake_repo)
            def __exit__(self, *args):
                pass

        with patch("qd_evolve.tools.install_skill.get_registry", return_value=mock_registry):
            with patch("qd_evolve.tools.install_skill._skill_registry", mock_skill_registry):
                with patch("subprocess.check_call"):
                    with patch("tempfile.TemporaryDirectory", new=_FakeTempDir):
                        from qd_evolve.tools.install_skill import _install_skill
                        result = _install_skill(name="test", github_url="https://github.com/test/skill")
                        assert "installed" in result