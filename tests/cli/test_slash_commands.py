"""Tests for SLASH_COMMANDS validation — ensures consistency and completeness."""


from qd_evolve.chat_cli import SLASH_COMMANDS


class TestSlashCommandsValidation:
    def test_required_commands_exist(self):
        required = ["/quit", "/help", "/models", "/agents", "/tools", "/skills", "/reset", "/status"]
        for cmd in required:
            assert cmd in SLASH_COMMANDS, f"Required command '{cmd}' missing"

    def test_no_duplicate_commands(self):
        keys = list(SLASH_COMMANDS.keys())
        assert len(keys) == len(set(keys))

    def test_command_format_consistent(self):
        for cmd in SLASH_COMMANDS:
            # All commands should be lowercase with single /
            assert cmd == cmd.lower()
            assert cmd.count("/") == 1
            assert not cmd.startswith("//")