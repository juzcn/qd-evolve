"""Tests for qd_evolve.skills — SkillRegistry, SKILL.md parsing."""



from qd_evolve.skills import SkillInfo, SkillRegistry, _parse_frontmatter


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        content = "---\nname: find-tools\ndescription: Search tools\n---\n# Find Tools\nContent here"
        result = _parse_frontmatter(content)
        assert result["name"] == "find-tools"
        assert result["description"] == "Search tools"

    def test_no_frontmatter(self):
        content = "# Just a skill\nNo frontmatter here"
        result = _parse_frontmatter(content)
        assert result == {}

    def test_empty_frontmatter(self):
        content = "---\n---\nBody text"
        result = _parse_frontmatter(content)
        assert result == {}

    def test_multiline_frontmatter(self):
        content = "---\nname: find-tools\ndescription: Search\ntags: search,tools\n---\nBody"
        result = _parse_frontmatter(content)
        assert result["name"] == "find-tools"
        assert result["description"] == "Search"
        assert result["tags"] == "search,tools"

    def test_no_closing_delimiter(self):
        content = "---\ndescription: test\nNo closing"
        result = _parse_frontmatter(content)
        assert result == {}

    def test_invalid_yaml(self):
        content = "---\n: invalid yaml : [\n---\nContent"
        fm = _parse_frontmatter(content)
        assert fm == {}


class TestSkillInfo:
    def test_defaults(self):
        si = SkillInfo(name="test", content="content")
        assert si.summary == ""
        assert si.version == ""
        assert si.active is False

    def test_format_for_prompt_with_summary(self):
        info = SkillInfo(name="find-tools", content="full content", summary="Search tools")
        assert info.format_for_prompt() == "- find-tools: Search tools"

    def test_format_for_prompt_without_summary(self):
        info = SkillInfo(name="find-tools", content="first line\nsecond line", summary="")
        result = info.format_for_prompt()
        assert "find-tools" in result
        assert "first line" in result


class TestSkillRegistry:
    def test_discover_skills_finds_skills(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "find-tools").mkdir()
        (skill_dir / "find-tools" / "SKILL.md").write_text(
            "---\nname: find-tools\ndescription: Search tools\n---\n# Find Tools\nContent", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        skills = reg.get_all_skills()
        assert len(skills) == 1
        assert skills[0].name == "find-tools"
        assert skills[0].summary == "Search tools"

    def test_discover_skills_skips_no_skill_md(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "empty-skill").mkdir()
        # No SKILL.md

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        assert len(reg.get_all_skills()) == 0

    def test_discover_skills_skips_missing_name(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "bad-skill").mkdir()
        (skill_dir / "bad-skill" / "SKILL.md").write_text(
            "---\ndescription: No name\n---\nContent", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        assert len(reg.get_all_skills()) == 0

    def test_discover_empty_dir(self, tmp_path):
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        assert reg.get_all_skills() == []

    def test_discover_nonexistent_dir(self, tmp_path):
        reg = SkillRegistry()
        reg.discover_skills(str(tmp_path / "nonexistent"))
        assert reg.get_all_skills() == []

    def test_get_detail(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "my-skill").mkdir()
        (skill_dir / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: My skill\n---\n# My Skill\nFull content here", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        detail = reg.get_detail("my-skill")
        assert detail is not None
        assert "Full content here" in detail

    def test_get_detail_not_found(self):
        reg = SkillRegistry()
        assert reg.get_detail("nonexistent") is None

    def test_get_skill(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\n---\nContent", encoding="utf-8",
        )
        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        skill = reg.get_skill("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"

    def test_get_skill_not_found(self):
        reg = SkillRegistry()
        assert reg.get_skill("nonexistent") is None

    def test_add_skill(self):
        reg = SkillRegistry()
        skill = SkillInfo(name="new-skill", summary="New desc", content="Full content")
        reg.add_skill(skill)
        skills = reg.get_all_skills()
        assert any(s.name == "new-skill" for s in skills)
        assert reg.get_detail("new-skill") == "Full content"

    def test_format_for_prompt(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "find-tools").mkdir()
        (skill_dir / "find-tools" / "SKILL.md").write_text(
            "---\nname: find-tools\ndescription: Search tools\n---\n# Find Tools", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        result = reg.format_for_prompt()
        assert "find-tools" in result
        assert "Search tools" in result

    def test_format_for_prompt_empty(self):
        reg = SkillRegistry()
        result = reg.format_for_prompt()
        assert result == ""

    def test_format_for_prompt_excludes_loaded(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test skill\n---\nContent", encoding="utf-8",
        )
        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        prompt = reg.format_for_prompt(loaded={"test-skill"})
        assert "- test-skill" not in prompt

    def test_disabled_skill(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "my-skill").mkdir()
        (skill_dir / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: My skill\n---\nContent", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        reg._disabled.add("my-skill")
        skills = reg.get_all_skills()
        assert len(skills) == 0
        assert reg.get_skill("my-skill") is None
        assert reg.get_detail("my-skill") is None

    def test_format_for_prompt_excludes_disabled(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "my-skill").mkdir()
        (skill_dir / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: My skill\n---\nContent", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        reg._disabled.add("my-skill")
        result = reg.format_for_prompt()
        assert "my-skill" not in result

    def test_preload_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\n---\nContent", encoding="utf-8",
        )
        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir), preload_skills=["test-skill"])
        skills = reg.get_all_skills()
        assert len(skills) == 1
        assert skills[0].active is True

    def test_reload(self, tmp_path):
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        (skill_dir / "my-skill").mkdir()
        (skill_dir / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: My skill\n---\nContent", encoding="utf-8"
        )

        reg = SkillRegistry()
        reg.discover_skills(skill_dir)
        assert len(reg.get_all_skills()) == 1

        # Add another skill and reload
        (skill_dir / "new-skill").mkdir()
        (skill_dir / "new-skill" / "SKILL.md").write_text(
            "---\nname: new-skill\ndescription: New skill\n---\nContent", encoding="utf-8"
        )
        reg.reload()
        assert len(reg.get_all_skills()) == 2