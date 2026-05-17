"""Tests for qd_evolve.tools.skill_loader — load_skill handler."""

from unittest.mock import MagicMock, patch

import pytest


class TestLoadSkill:
    def test_load_existing_skill(self):
        from qd_evolve.skills import SkillInfo
        mock_reg = MagicMock()
        mock_skill = SkillInfo(name="find-tools", content="SKILL.md content here", summary="Search for tools")
        mock_reg.get_skill.return_value = mock_skill
        mock_reg.get_all_skills.return_value = [mock_skill]

        with patch("qd_evolve.tools.skill_loader._skill_registry", mock_reg):
            from qd_evolve.tools.skill_loader import _load_skill
            result = _load_skill("find-tools")
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