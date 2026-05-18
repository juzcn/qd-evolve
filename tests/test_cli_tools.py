"""Tests for qd_evolve.cli_tools — CLIRegistry, YAML discovery."""

from pathlib import Path

import pytest

from qd_evolve.cli_tools import CLIRegistry, CLIToolDef


class TestCLIToolDef:
    def test_basic_creation(self):
        tool = CLIToolDef(name="pandoc", command="pandoc", description="Universal converter")
        assert tool.name == "pandoc"
        assert tool.command == "pandoc"
        assert tool.help_summary == ""
        assert tool.examples == []


class TestCLIRegistry:
    def test_discover_finds_yaml(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\nhelp_summary: |\n  Usage: pandoc [OPTIONS]\nexamples:\n  - 'pandoc input.md -o output.pdf'\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        tools = reg.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "pandoc"

    def test_discover_skips_invalid_yaml(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "bad.yaml").write_text("not a dict", encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(cli_dir)
        assert len(reg.list_tools()) == 0

    def test_discover_skips_no_name(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "noname.yaml").write_text(
            "command: test\ndescription: No name\n", encoding="utf-8"
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        assert len(reg.list_tools()) == 0

    def test_get_detail(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        detail = reg.get_detail("pandoc")
        assert detail is not None
        assert detail["name"] == "pandoc"

    def test_get_detail_not_found(self):
        reg = CLIRegistry()
        assert reg.get_detail("nonexistent") is None

    def test_get_detail_disabled(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        reg._disabled.add("pandoc")
        assert reg.get_detail("pandoc") is None

    def test_format_for_prompt(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\nexamples:\n  - 'pandoc input.md -o output.pdf'\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        result = reg.format_for_prompt()
        assert "pandoc" in result
        assert "Universal converter" in result

    def test_format_for_prompt_empty(self):
        reg = CLIRegistry()
        result = reg.format_for_prompt()
        assert result == ""

    def test_format_for_prompt_excludes_disabled(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        reg._disabled.add("pandoc")
        result = reg.format_for_prompt()
        assert "pandoc" not in result

    def test_format_for_prompt_excludes_loaded(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        result = reg.format_for_prompt(loaded={"pandoc"})
        assert "pandoc" not in result

    def test_list_tools_excludes_disabled(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        reg._disabled.add("pandoc")
        assert len(reg.list_tools()) == 0

    def test_reload(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        assert len(reg.list_tools()) == 1

        # Add another and reload
        (cli_dir / "git.yaml").write_text(
            "name: git\ncommand: git\ndescription: Git CLI\n",
            encoding="utf-8",
        )
        reg.reload()
        assert len(reg.list_tools()) == 2

    def test_discover_no_dir(self, tmp_path):
        reg = CLIRegistry()
        reg.discover(tmp_path / "nonexistent")
        assert len(reg.list_tools()) == 0