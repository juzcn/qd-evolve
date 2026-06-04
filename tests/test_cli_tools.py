"""Tests for qd_evolve.cli_tools — CLIRegistry, YAML discovery."""



from qd_evolve.cli_tools import CLIRegistry, CLIToolDef


class TestCLIToolDef:
    def test_basic_creation(self):
        tool = CLIToolDef(name="pandoc", command="pandoc", description="Universal converter")
        assert tool.name == "pandoc"
        assert tool.command == "pandoc"
        assert tool.help_summary == ""
        assert tool.examples == []

    def test_defaults(self):
        ctd = CLIToolDef(name="git", command="git")
        assert ctd.description == ""
        assert ctd.help_summary == ""
        assert ctd.examples == []

    def test_with_all_fields(self):
        ctd = CLIToolDef(
            name="git", command="git", description="Git version control",
            help_summary="Git CLI tool", examples=["git status", "git log"],
        )
        assert ctd.description == "Git version control"
        assert len(ctd.examples) == 2


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
        assert "- [inactive] pandoc: Universal converter" in result

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

    def test_format_for_prompt_with_status_tags(self, tmp_path):
        cli_dir = tmp_path / "cli"
        cli_dir.mkdir()
        (cli_dir / "pandoc.yaml").write_text(
            "name: pandoc\ncommand: pandoc\ndescription: Universal converter\n",
            encoding="utf-8",
        )

        reg = CLIRegistry()
        reg.discover(cli_dir)
        # preloaded shows one-liner summary only (JSON detail in appendix)
        result = reg.format_for_prompt(preloaded={"pandoc"})
        assert "- [ready] pandoc: Universal converter" in result
        assert '"name"' not in result  # JSON detail moved to appendix
        # loaded only shows summary line
        result2 = reg.format_for_prompt(loaded={"pandoc"})
        assert "- [ready] pandoc: Universal converter" in result2

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