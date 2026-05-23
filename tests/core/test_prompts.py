"""Tests for qd_evolve.core.prompts — PromptTemplateManager, _CombinedLoader."""

import pytest
from pathlib import Path

from qd_evolve.core.prompts import PromptTemplateManager, _CombinedLoader


class TestCombinedLoader:
    def test_load_from_primary(self, tmp_path):
        template = tmp_path / "test.j2"
        template.write_text("Hello {{ name }}!", encoding="utf-8")
        loader = _CombinedLoader(tmp_path)
        source, filename, uptodate = loader.get_source(None, "test.j2")
        assert "Hello {{ name }}!" in source

    def test_load_from_fallback(self, tmp_path):
        primary = tmp_path / "primary"
        primary.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (fallback / "test.j2").write_text("Fallback {{ name }}!", encoding="utf-8")
        loader = _CombinedLoader(primary, fallback)
        source, filename, uptodate = loader.get_source(None, "test.j2")
        assert "Fallback" in source

    def test_primary_overrides_fallback(self, tmp_path):
        primary = tmp_path / "primary"
        primary.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (primary / "test.j2").write_text("Primary!", encoding="utf-8")
        (fallback / "test.j2").write_text("Fallback!", encoding="utf-8")
        loader = _CombinedLoader(primary, fallback)
        source, _, _ = loader.get_source(None, "test.j2")
        assert "Primary!" in source

    def test_not_found_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        loader = _CombinedLoader(empty)
        from jinja2 import TemplateNotFound
        with pytest.raises(TemplateNotFound):
            loader.get_source(None, "nonexistent.j2")


class TestPromptTemplateManager:
    def test_render_builtin_default(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="testbot", os_name="Linux", python_cmd="python", cwd="/tmp", skills_dir="skills")
        assert "testbot" in result
        assert "Linux" in result

    def test_render_builtin_heartbeat(self):
        mgr = PromptTemplateManager()
        result = mgr.render("heartbeat", idle_seconds=30, now="2024-01-01 Monday 12:00:00")
        assert "30" in result
        assert "2024-01-01" in result

    def test_render_with_a2a_enabled(self):
        mgr = PromptTemplateManager()
        result = mgr.render("a2a-default", agent_name="bot", os_name="Linux", python_cmd="python",
                            cwd="/tmp", skills_dir="skills", a2a_enabled=True,
                            available_agents="bot, helper")
        assert "Inter-Agent Communication" in result
        assert "bot, helper" in result

    def test_render_with_a2a_disabled(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", os_name="Linux", python_cmd="python",
                            cwd="/tmp", skills_dir="skills")
        assert "Inter-Agent Communication" not in result

    def test_render_with_unloaded_tools(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", os_name="Linux", python_cmd="python",
                            cwd="/tmp", skills_dir="skills", unloaded_tools="- echo: Echo tool\n- fetch: Fetch tool")
        assert "Unloaded Func Tools Summary" in result
        assert "- echo: Echo tool" in result

    def test_render_with_preloaded_skills(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", os_name="Linux", python_cmd="python",
                            cwd="/tmp", skills_dir="skills", preloaded_skills="Skill content here")
        assert "Preloaded Skills SKILL.md" in result

    def test_render_custom_template(self, tmp_path):
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "custom.j2").write_text("Custom: {{ agent_name }}", encoding="utf-8")
        mgr = PromptTemplateManager(templates_dir=str(templates_dir))
        result = mgr.render("custom", agent_name="testbot")
        assert "Custom: testbot" in result

    def test_has_template_builtin(self):
        mgr = PromptTemplateManager()
        assert mgr.has_template("default") is True

    def test_has_template_nonexistent(self):
        mgr = PromptTemplateManager()
        assert mgr.has_template("absolutely_nonexistent_template_xyz") is False

    def test_render_nonexistent_template_raises(self):
        mgr = PromptTemplateManager()
        from jinja2 import TemplateNotFound
        with pytest.raises(TemplateNotFound):
            mgr.render("nonexistent_xyz_123")

    def test_has_template_a2a_default(self):
        mgr = PromptTemplateManager()
        assert mgr.has_template("a2a-default") is True

    def test_default_context_includes_date(self):
        mgr = PromptTemplateManager()
        ctx = mgr._default_context()
        assert "date" in ctx