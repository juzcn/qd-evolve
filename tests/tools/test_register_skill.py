"""Tests for qd_evolve.tools.register_skill — register_skill handler."""

from pathlib import Path
from unittest.mock import MagicMock, patch



class TestRegisterSkill:
    def test_register_moves_skill(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "skill"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "skills"
        perm.mkdir(parents=True)

        staged_dir = staging / "my-skill"
        staged_dir.mkdir()
        (staged_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: My skill\n---\nContent", encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_skill._perm_skill_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_skill.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_skill import _register_skill
            result = _register_skill("my-skill")
            assert "registered" in result
            assert (perm / "my-skill" / "SKILL.md").exists()
            assert not staged_dir.exists()

    def test_register_staged_not_found(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "skill"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "skills"
        perm.mkdir(parents=True)

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_skill._perm_skill_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_skill.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_skill import _register_skill
            result = _register_skill("nonexistent")
            assert "not found" in result

    def test_register_already_exists(self, tmp_path, monkeypatch):
        staging = tmp_path / ".qd_evolve" / "staging" / "skill"
        staging.mkdir(parents=True)
        perm = tmp_path / "tools" / "skills"
        perm.mkdir(parents=True)

        staged_dir = staging / "my-skill"
        staged_dir.mkdir()
        (staged_dir / "SKILL.md").write_text("content", encoding="utf-8")

        perm_dir = perm / "my-skill"
        perm_dir.mkdir()
        (perm_dir / "SKILL.md").write_text("existing", encoding="utf-8")

        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        monkeypatch.setattr("qd_evolve.tools.register_skill._perm_skill_dir", lambda: perm)

        mock_registry = MagicMock()
        with patch("qd_evolve.tools.register_skill.get_registry", return_value=mock_registry):
            from qd_evolve.tools.register_skill import _register_skill
            result = _register_skill("my-skill")
            assert "already exists" in result

    def test_perm_skill_dir(self):
        from qd_evolve.tools.register_skill import _perm_skill_dir
        from qd_evolve.core.config import SKILLS_DIR
        result = _perm_skill_dir()
        assert result == Path.cwd() / SKILLS_DIR