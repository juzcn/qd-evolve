"""Tests for qd_evolve.tools.skill_loader — open_skill handler."""

from unittest.mock import MagicMock, patch



class TestLoadSkill:
    def test_load_existing_skill(self):
        from qd_evolve.skills import SkillInfo
        mock_reg = MagicMock()
        mock_skill = SkillInfo(name="search-tools", content="SKILL.md content here", summary="Search for tools")
        mock_reg.get_skill.return_value = mock_skill
        mock_reg.get_all_skills.return_value = [mock_skill]

        with patch("qd_evolve.tools.skill_loader._skill_registry", mock_reg):
            from qd_evolve.tools.skill_loader import _load_skill
            result = _load_skill("search-tools")
            assert "SKILL.md content here" in result

    def test_load_nonexistent_skill(self):
        mock_reg = MagicMock()
        mock_reg.get_skill.return_value = None
        mock_reg.get_all_skills.return_value = []

        with patch("qd_evolve.tools.skill_loader._skill_registry", mock_reg):
            from qd_evolve.tools.skill_loader import _load_skill
            result = _load_skill("nonexistent")
            assert "not found" in result

    def test_registry_not_initialized(self):
        with patch("qd_evolve.tools.skill_loader._skill_registry", None):
            from qd_evolve.tools.skill_loader import _load_skill
            result = _load_skill("anything")
            assert "not initialized" in result

    def test_set_skill_registry(self):
        from qd_evolve.tools.skill_loader import set_skill_registry
        mock_reg = MagicMock()
        set_skill_registry(mock_reg)
        from qd_evolve.tools import skill_loader as mod
        assert mod._skill_registry == mock_reg
        mod._skill_registry = None

    def test_set_skill_registry_overwrites(self):
        from qd_evolve.tools.skill_loader import set_skill_registry
        from qd_evolve.tools import skill_loader as mod
        old = mod._skill_registry
        reg1 = MagicMock()
        reg2 = MagicMock()
        try:
            set_skill_registry(reg1)
            assert mod._skill_registry is reg1
            set_skill_registry(reg2)
            assert mod._skill_registry is reg2
        finally:
            mod._skill_registry = old

    def test_load_skill_with_rich_content(self):
        from qd_evolve.skills import SkillInfo
        mock_reg = MagicMock()
        rich = "SKILL.md content\n\n## Section\n\n- item 1\n- item 2\n\n```python\nprint('hello')\n```"
        mock_skill = SkillInfo(name="rich-skill", content=rich, summary="Rich content skill")
        mock_reg.get_skill.return_value = mock_skill
        mock_reg.get_all_skills.return_value = [mock_skill]

        with patch("qd_evolve.tools.skill_loader._skill_registry", mock_reg):
            from qd_evolve.tools.skill_loader import _load_skill
            result = _load_skill("rich-skill")
            assert "## Section" in result
            assert "print('hello')" in result

    def test_registry_with_empty_skills(self):
        mock_reg = MagicMock()
        mock_reg.get_skill.return_value = None
        mock_reg.get_all_skills.return_value = []

        with patch("qd_evolve.tools.skill_loader._skill_registry", mock_reg):
            from qd_evolve.tools.skill_loader import _load_skill
            result = _load_skill("nonexistent")
            assert "not found" in result