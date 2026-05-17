"""Tests for qd_evolve.skills — SkillRegistry, SkillInfo, _parse_frontmatter."""

import json
from pathlib import Path

import pytest

from qd_evolve.skills import SkillInfo, SkillRegistry, _parse_frontmatter


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\nname: find-tools\ndescription: Search for tools\n---\nSkill content here"
        fm = _parse_frontmatter(content)
        assert fm["name"] == "find-tools"
        assert fm["description"] == "Search for tools"

    def test_no_frontmatter(self):
        content = "Just some text without frontmatter"
        fm = _parse_frontmatter(content)
        assert fm == {}

    def test_missing_closing_delimiter(self):
        content = "---\nname: test\nNo closing delimiter"
        fm = _parse_frontmatter(content)
        assert fm == {}

    def test_empty_frontmatter(self):
        content = "---\n---\nContent"
        fm = _parse_frontmatter(content)
        assert fm == {}

    def test_invalid_yaml(self):
        content = "---\n: invalid yaml : [\n---\nContent"
        fm = _parse_frontmatter(content)
        assert fm == {}

    def test_multiline_description(self):
        content = "---\nname: test\ndescription: A long description\n---\nContent"
        fm = _parse_frontmatter(content)
        assert "name" in fm


class TestSkillInfo:
    def test_defaults(self):
        si = SkillInfo(name="test", content="content")
        assert si.summary == ""
        assert si.version == ""
        assert si.active is False

    def test_format_for_prompt_with_summary(self):
        si = SkillInfo(name="test", content="content", summary="A test skill")
        assert si.format_for_prompt() == "- test: A test skill"

    def test_format_for_prompt_without_summary(self):
        si = SkillInfo(name="test", content="First line of content\nSecond line")
        result = si.format_for_prompt()
        assert "- test:" in result
        assert "First line" in result


class TestSkillRegistry:
    def test_discover_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "find-tools"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: find-tools\ndescription: Search for tools\n---\nContent here",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        skills = reg.get_all_skills()
        assert len(skills) == 1
        assert skills[0].name == "find-tools"

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

    def test_get_skill(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\n---\nContent",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        skill = reg.get_skill("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"

    def test_get_skill_not_found(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        assert reg.get_skill("nonexistent") is None

    def test_get_detail(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        content = "---\nname: test-skill\ndescription: Test\n---\nFull content here"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        detail = reg.get_detail("test-skill")
        assert detail == content

    def test_add_skill(self):
        reg = SkillRegistry()
        skill = SkillInfo(name="hot-skill", content="Hot loaded content", summary="Hot skill")
        reg.add_skill(skill)
        assert reg.get_skill("hot-skill") is not None

    def test_format_for_prompt(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test skill\n---\nContent",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        prompt = reg.format_for_prompt()
        assert "- test-skill:" in prompt

    def test_format_for_prompt_excludes_loaded(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test skill\n---\nContent",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        prompt = reg.format_for_prompt(loaded={"test-skill"})
        assert "- test-skill" not in prompt

    def test_disabled_skill(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\n---\nContent",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir))
        reg._disabled.add("test-skill")
        assert reg.get_skill("test-skill") is None
        assert reg.get_detail("test-skill") is None

    def test_preload_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: Test\n---\nContent",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.discover_skills(str(skills_dir), preload_skills=["test-skill"])
        skills = reg.get_all_skills()
        assert len(skills) == 1
        assert skills[0].active is True