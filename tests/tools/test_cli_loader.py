"""Tests for qd_evolve.tools.cli_loader — load_cli handler."""

import json
from unittest.mock import MagicMock, patch



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

    def test_set_cli_registry(self):
        from qd_evolve.tools.cli_loader import set_cli_registry
        mock_reg = MagicMock()
        set_cli_registry(mock_reg)
        from qd_evolve.tools import cli_loader as mod
        assert mod._cli_registry == mock_reg
        mod._cli_registry = None

    def test_set_cli_registry_overwrites(self):
        from qd_evolve.tools.cli_loader import set_cli_registry
        from qd_evolve.tools import cli_loader as mod
        old = mod._cli_registry
        reg1 = MagicMock()
        reg2 = MagicMock()
        try:
            set_cli_registry(reg1)
            assert mod._cli_registry is reg1
            set_cli_registry(reg2)
            assert mod._cli_registry is reg2
        finally:
            mod._cli_registry = old

    def test_load_returns_valid_json(self):
        from qd_evolve.cli_tools import CLIToolDef
        mock_reg = MagicMock()
        mock_tool = CLIToolDef(
            name="git", command="git", description="Git version control",
            help_summary="git --help output", examples=["git log"],
        )
        mock_reg.get_detail.return_value = mock_tool.model_dump()
        mock_reg.list_tools.return_value = [mock_tool]

        with patch("qd_evolve.tools.cli_loader._cli_registry", mock_reg):
            from qd_evolve.tools.cli_loader import _load_cli
            result = _load_cli("git")
            data = json.loads(result)
            assert data["name"] == "git"
            assert data["command"] == "git"
            assert "description" in data

    def test_registry_has_empty_tools_list(self):
        mock_reg = MagicMock()
        mock_reg.get_detail.return_value = None
        mock_reg.list_tools.return_value = []

        with patch("qd_evolve.tools.cli_loader._cli_registry", mock_reg):
            from qd_evolve.tools.cli_loader import _load_cli
            result = _load_cli("nonexistent")
            assert "not found" in result