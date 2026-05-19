"""Tests for qd_evolve.chat_cli — ReplayInput, TeeWriter, SLASH_COMMANDS."""

import pytest

from qd_evolve.chat_cli import ReplayInput, TeeWriter, SLASH_COMMANDS


class TestReplayInput:
    def test_feeds_inputs_in_order(self):
        ri = ReplayInput(["hello", "world"])
        assert ri.prompt() == "hello"
        assert ri.prompt() == "world"

    def test_raises_eof_on_exhaustion(self):
        ri = ReplayInput(["only_one"])
        ri.prompt()
        with pytest.raises(EOFError):
            ri.prompt()

    def test_empty_inputs(self):
        ri = ReplayInput([])
        with pytest.raises(EOFError):
            ri.prompt()

    def test_ignores_kwargs(self):
        ri = ReplayInput(["hello"])
        assert ri.prompt(some_kwarg=True) == "hello"


class TestTeeWriter:
    def test_writes_to_all_files(self):
        import io
        f1 = io.StringIO()
        f2 = io.StringIO()
        tw = TeeWriter(f1, f2)
        tw.write("hello")
        assert f1.getvalue() == "hello"
        assert f2.getvalue() == "hello"

    def test_returns_length(self):
        import io
        f1 = io.StringIO()
        tw = TeeWriter(f1)
        n = tw.write("hello")
        assert n == 5

    def test_flush(self):
        import io
        f1 = io.StringIO()
        tw = TeeWriter(f1)
        tw.write("hello")
        tw.flush()  # should not raise

    def test_isatty_false(self):
        import io
        f1 = io.StringIO()
        tw = TeeWriter(f1)
        assert tw.isatty() is False

    def test_isatty_true_when_any_is_tty(self):
        import io
        class FakeTTY:
            def write(self, text):
                return len(text)
            def flush(self):
                pass
            def isatty(self):
                return True
        tw = TeeWriter(FakeTTY(), io.StringIO())
        assert tw.isatty() is True


class TestSlashCommands:
    def test_all_commands_start_with_slash(self):
        for cmd in SLASH_COMMANDS:
            assert cmd.startswith("/"), f"Command '{cmd}' doesn't start with /"

    def test_known_commands_present(self):
        assert "/quit" in SLASH_COMMANDS
        assert "/help" in SLASH_COMMANDS
        assert "/models" in SLASH_COMMANDS
        assert "/agents" in SLASH_COMMANDS

    def test_all_have_descriptions(self):
        for cmd, desc in SLASH_COMMANDS.items():
            assert len(desc) > 0, f"Command '{cmd}' has empty description"

    def test_dict_is_not_empty(self):
        assert len(SLASH_COMMANDS) > 0