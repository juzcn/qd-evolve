"""Tests for qd_evolve.tools.cli_loader — load_cli handler."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestLoadCli:
    def test_load_existing_cli(self):
        from qd_evolve.cli_tools import CLIToolDef
        mock_reg = MagicMock()
        mock_tool = CLIToolDef(name="git", command="git", description="Git CLI")
        mock_reg.get_detail.return_value = mock_tool.model_dump()
        mock_reg.list_tools.return_value = [mock_tool]

        with patch("qd_evolve.tools.cli_loader._cli_registry", mock_reg):
            from qd_evolve.tools.cli_loader import _load_cli
            result = _load_cli("git")
            data = json.loads(result)
            assert data["name"] == "git"

    def test_load_nonexistent_cli(self):
        mock_reg = MagicMock()
        mock_reg.get_detail.return_value = None
        mock_reg.list_tools.return_value = []

        with patch("qd_evolve.tools.cli_loader._cli_registry", mock_reg):
            from qd_evolve.tools.cli_loader import _load_cli
            result = _load_cli("nonexistent")
            assert "not found" in result

    def test_registry_not_initialized(self):
        with patch("qd_evolve.tools.cli_loader._cli_registry", None):
            from qd_evolve.tools.cli_loader import _load_cli
            result = _load_cli("anything")
            assert "not initialized" in result