"""Tests for qd_evolve.cli_utils — ReplayInput, TeeWriter, AGENT_COLORS."""

from io import StringIO

import pytest

from qd_evolve.cli_utils import AGENT_COLORS, ReplayInput, TeeWriter


class TestReplayInput:
    def test_prompts_in_order(self):
        ri = ReplayInput(["first", "second", "third"])
        assert ri.prompt() == "first"
        assert ri.prompt() == "second"
        assert ri.prompt() == "third"

    def test_raises_eoferror_on_exhaustion(self):
        ri = ReplayInput(["only"])
        assert ri.prompt() == "only"
        with pytest.raises(EOFError):
            ri.prompt()

    def test_empty_list_raises_immediately(self):
        ri = ReplayInput([])
        with pytest.raises(EOFError):
            ri.prompt()

    def test_prompt_accepts_kwargs(self):
        """prompt() accepts arbitrary kwargs (compatible with prompt_toolkit signature)."""
        ri = ReplayInput(["hello"])
        result = ri.prompt(message="> ", default="world")
        assert result == "hello"

    def test_index_advances_correctly(self):
        ri = ReplayInput(["a", "b", "c", "d"])
        ri.prompt()  # index 0
        ri.prompt()  # index 1
        assert ri.prompt() == "c"
        assert ri.prompt() == "d"
        with pytest.raises(EOFError):
            ri.prompt()


class TestTeeWriter:
    def test_writes_to_multiple_files(self):
        a = StringIO()
        b = StringIO()
        tw = TeeWriter(a, b)
        tw.write("hello")
        assert a.getvalue() == "hello"
        assert b.getvalue() == "hello"

    def test_write_returns_length(self):
        a = StringIO()
        tw = TeeWriter(a)
        result = tw.write("test")
        assert result == 4

    def test_flush_propagates(self):
        a = StringIO()
        b = StringIO()
        tw = TeeWriter(a, b)
        tw.write("data")
        tw.flush()
        # StringIO.flush is a no-op, but we verify it doesn't raise
        assert a.getvalue() == "data"

    def test_isatty_all_true(self):
        class TTY(StringIO):
            def isatty(self): return True

        tw = TeeWriter(TTY(), TTY())
        assert tw.isatty() is True

    def test_isatty_all_false(self):
        a = StringIO()
        b = StringIO()
        tw = TeeWriter(a, b)
        assert tw.isatty() is False

    def test_isatty_mixed(self):
        class TTY(StringIO):
            def isatty(self): return True

        tw = TeeWriter(TTY(), StringIO())
        assert tw.isatty() is True

    def test_no_files(self):
        tw = TeeWriter()
        # Write to zero files should not raise
        result = tw.write("nobody sees this")
        assert result == len("nobody sees this")
        tw.flush()  # should not raise

    def test_file_without_isatty(self):
        """Files without isatty method are treated as not-a-tty."""
        f1 = StringIO()
        tw = TeeWriter(f1)
        assert tw.isatty() is False


class TestAgentColors:
    def test_is_list_of_six(self):
        assert isinstance(AGENT_COLORS, list)
        assert len(AGENT_COLORS) == 6

    def test_all_are_hex_colors(self):
        for c in AGENT_COLORS:
            assert c.startswith("#")
            assert len(c) == 7
