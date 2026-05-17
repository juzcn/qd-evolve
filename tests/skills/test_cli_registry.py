"""Tests for qd_evolve.cli_tools — CLIRegistry, CLIToolDef."""

from pathlib import Path

import yaml

import pytest

from qd_evolve.cli_tools import CLIRegistry, CLIToolDef


class TestCLIToolDef:
    def test_defaults(self):
        ctd = CLIToolDef(name="git", command="git")
        assert ctd.description == ""
        assert ctd.help_summary == ""
        assert ctd.examples == []

    def test_with_all_fields(self):
        ctd = CLIToolDef(
            name="git",
            command="git",
            description="Git version control",
            help_summary="Git CLI tool",
            examples=["git status", "git log"],
        )
        assert ctd.description == "Git version control"
        assert len(ctd.examples) == 2


class TestCLIRegistry:
    def test_discover_tools(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {
            "name": "git",
            "command": "git",
            "description": "Git CLI",
            "help_summary": "Git version control",
            "examples": ["git status"],
        }
        (cli_dir / "git.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        tools = reg.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "git"

    def test_discover_empty_dir(self, tmp_path):
        cli_dir = tmp_path / "empty_cli"
        cli_dir.mkdir()

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        assert reg.list_tools() == []

    def test_discover_nonexistent_dir(self, tmp_path):
        reg = CLIRegistry()
        reg.discover(str(tmp_path / "nonexistent"))
        assert reg.list_tools() == []

    def test_get_detail(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {"name": "git", "command": "git", "description": "Git CLI"}
        (cli_dir / "git.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        detail = reg.get_detail("git")
        assert detail is not None
        assert detail["name"] == "git"

    def test_get_detail_not_found(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        assert reg.get_detail("nonexistent") is None

    def test_format_for_prompt(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {"name": "git", "command": "git", "description": "Git CLI", "examples": ["git status"]}
        (cli_dir / "git.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        prompt = reg.format_for_prompt()
        assert "- git:" in prompt
        assert "git status" in prompt

    def test_format_for_prompt_excludes_loaded(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {"name": "git", "command": "git", "description": "Git CLI"}
        (cli_dir / "git.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        prompt = reg.format_for_prompt(loaded={"git"})
        assert "- git" not in prompt

    def test_disabled_tool(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {"name": "git", "command": "git", "description": "Git CLI"}
        (cli_dir / "git.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        reg._disabled.add("git")
        assert reg.get_detail("git") is None
        assert reg.list_tools() == []

    def test_invalid_yaml_skipped(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "bad.yaml").write_text("not a yaml dict", encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        assert reg.list_tools() == []

    def test_yaml_without_name_skipped(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {"command": "git"}  # missing name
        (cli_dir / "no_name.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        assert reg.list_tools() == []

    def test_reload(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        yaml_content = {"name": "git", "command": "git", "description": "Git CLI"}
        (cli_dir / "git.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

        reg = CLIRegistry()
        reg.discover(str(cli_dir))
        assert len(reg.list_tools()) == 1

        # Add new tool and reload
        yaml_content2 = {"name": "npm", "command": "npm", "description": "NPM CLI"}
        (cli_dir / "npm.yaml").write_text(yaml.dump(yaml_content2), encoding="utf-8")
        reg.reload()
        assert len(reg.list_tools()) == 2