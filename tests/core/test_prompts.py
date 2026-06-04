"""Tests for qd_evolve.core.prompts — PromptTemplateManager, _CombinedLoader."""

import pytest

from qd_evolve.core.prompts import PromptTemplateManager, _CombinedLoader


class TestCombinedLoader:
    def test_load_from_primary(self, tmp_path):
        template = tmp_path / "test.j2"
        template.write_text("Hello {{ name }}!", encoding="utf-8")
        loader = _CombinedLoader(tmp_path)
        source, filename, uptodate = loader.get_source(None, "test.j2")  # type: ignore
        assert "Hello {{ name }}!" in source

    def test_load_from_fallback(self, tmp_path):
        primary = tmp_path / "primary"
        primary.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (fallback / "test.j2").write_text("Fallback {{ name }}!", encoding="utf-8")
        loader = _CombinedLoader(primary, fallback)
        source, filename, uptodate = loader.get_source(None, "test.j2")  # type: ignore
        assert "Fallback" in source

    def test_primary_overrides_fallback(self, tmp_path):
        primary = tmp_path / "primary"
        primary.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        (primary / "test.j2").write_text("Primary!", encoding="utf-8")
        (fallback / "test.j2").write_text("Fallback!", encoding="utf-8")
        loader = _CombinedLoader(primary, fallback)
        source, _, _ = loader.get_source(None, "test.j2")  # type: ignore
        assert "Primary!" in source

    def test_not_found_raises(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        loader = _CombinedLoader(empty)
        from jinja2 import TemplateNotFound
        with pytest.raises(TemplateNotFound):
            loader.get_source(None, "nonexistent.j2")  # type: ignore

    def test_fallback_none_skipped(self, tmp_path):
        primary = tmp_path / "primary"
        primary.mkdir()
        (primary / "test.j2").write_text("Primary content", encoding="utf-8")
        # Pass None primary to exercise the "d is None" continue path
        loader = _CombinedLoader(None, primary)  # type: ignore
        source, _, _ = loader.get_source(None, "test.j2")  # type: ignore
        assert "Primary content" in source


class TestPromptTemplateManager:
    def test_render_builtin_default(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="testbot", runtime_context="- **OS:** Linux")
        assert "testbot" in result
        assert "Linux" in result

    def test_render_builtin_heartbeat(self):
        mgr = PromptTemplateManager()
        result = mgr.render("heartbeat", idle_seconds=30, now="2024-01-01 Monday 12:00:00")
        assert "30" in result
        assert "2024-01-01" in result

    def test_render_with_a2a_enabled(self):
        mgr = PromptTemplateManager()
        result = mgr.render("a2a-default", agent_name="bot", runtime_context="- **OS:** Linux",
                            a2a_enabled=True, available_agents="bot, helper")
        assert "Inter-Agent Communication" in result
        assert "bot, helper" in result

    def test_render_with_a2a_disabled(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux")
        assert "Inter-Agent Communication" not in result

    def test_render_with_func_tools_section(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            func_tools_section="- [unloaded] echo: Echo tool\n- [unloaded] fetch: Fetch tool")
        assert "Func Tools" in result
        assert "- [unloaded] echo: Echo tool" in result
        assert "Unloaded Func Tools Summary" not in result  # old header gone

    def test_render_with_skills_section(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            skills_section="- [preloaded] search: Search tools\n  # Skill content...")
        assert "Skills" in result
        assert "- [preloaded] search" in result
        assert "Preloaded Skills SKILL.md" not in result  # old header gone

    def test_render_with_cli_tools_section(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            cli_tools_section="- [preloaded] git: Git CLI\n  {\"name\": \"git\"}")
        assert "CLI Tools" in result
        assert "- [preloaded] git" in result

    def test_render_empty_sections_omitted(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            func_tools_section="- [preloaded] echo: Echo")
        assert "### Skills Summary" not in result
        assert "### CLI Tools" not in result

    def test_section_ordering_skills_first(self):
        """Skills section should appear before CLI Tools and Func Tools."""
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            skills_section="- [unloaded] s: Skill",
                            cli_tools_section="- [unloaded] c: CLI",
                            func_tools_section="- [unloaded] f: Func")
        skills_pos = result.index("### Skills Summary")
        cli_pos = result.index("### CLI Tools Summary")
        func_pos = result.index("### Func Tools Summary")
        assert skills_pos < cli_pos < func_pos

    def test_section_header_line(self):
        """Each type section should have a 'status  name — description' header line."""
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            skills_section="- [unloaded] s: Skill")
        assert "status  name — description" in result

    def test_status_tag_legend(self):
        mgr = PromptTemplateManager()
        result = mgr.render("default", agent_name="bot", runtime_context="- **OS:** Linux",
                            func_tools_section="- [unloaded] test: Test")
        assert "[preloaded]" in result  # legend explains all three tags
        assert "[loaded]" in result
        assert "[unloaded]" in result

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