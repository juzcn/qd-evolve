"""Tests for qd_evolve.tools.staging — staging area path functions."""



from qd_evolve.tools.staging import (
    staging_func_dir,
    staging_mcp_dir,
    staging_skill_dir,
    ensure_staging_dirs,
    cleanup_staging,
)


class TestStagingDirs:
    def test_staging_func_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        d = staging_func_dir()
        assert d.name == "func"
        assert d.exists()

    def test_staging_mcp_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        d = staging_mcp_dir()
        assert d.name == "mcp"
        assert d.exists()

    def test_staging_skill_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        d = staging_skill_dir()
        assert d.name == "skill"
        assert d.exists()

    def test_ensure_staging_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        ensure_staging_dirs()
        assert (tmp_path / ".qd_evolve" / "staging" / "func").exists()
        assert (tmp_path / ".qd_evolve" / "staging" / "mcp").exists()
        assert (tmp_path / ".qd_evolve" / "staging" / "skill").exists()

    def test_cleanup_staging(self, tmp_path, monkeypatch):
        base = tmp_path / ".qd_evolve" / "staging"
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: base)
        ensure_staging_dirs()
        assert base.exists()
        cleanup_staging()
        assert not base.exists()

    def test_cleanup_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("qd_evolve.tools.staging._staging_base", lambda: tmp_path / ".qd_evolve" / "staging")
        cleanup_staging()  # should not raise