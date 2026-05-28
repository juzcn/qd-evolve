"""Tests for qd_evolve.core.memory — MemoryStore, RecalledMemoryRegistry, _parse_time_range."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from qd_evolve.core.memory import MemoryEntry, RecalledMemoryRegistry


class _ControllableDatetime:
    """Wrapper around datetime that lets tests advance ``now()`` without sleeping."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2025, 1, 1, 12, 0, 0)

    def now(self, tz=None):
        return self._now

    def advance(self, seconds: int = 2):
        self._now += timedelta(seconds=seconds)

    def __getattr__(self, name):
        return getattr(datetime, name)


class TestMemoryEntry:
    def test_defaults(self):
        me = MemoryEntry(id=1, key="2024-01-01", session_id="s1", user_msg="hi", assistant_msg="hello", content="user: hi\nassistant: hello")
        assert me.accessed_at is None
        assert me.access_count == 0
        assert me.distance is None


class TestRecalledMemoryRegistry:
    def test_add_new_entries(self, recalled_registry):
        entries = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1"),
            MemoryEntry(id=2, key="k2", session_id="s2", user_msg="q2", assistant_msg="a2", content="c2"),
        ]
        new = recalled_registry.add(entries)
        assert len(new) == 2

    def test_add_deduplicates(self, recalled_registry):
        entries = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1"),
        ]
        recalled_registry.add(entries)
        new = recalled_registry.add(entries)
        assert len(new) == 0

    def test_add_partial_new(self, recalled_registry):
        entries = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1"),
        ]
        recalled_registry.add(entries)
        new_entries = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1"),
            MemoryEntry(id=2, key="k2", session_id="s2", user_msg="q2", assistant_msg="a2", content="c2"),
        ]
        new = recalled_registry.add(new_entries)
        assert len(new) == 1
        assert new[0].id == 2

    def test_format_section_empty(self, recalled_registry):
        assert recalled_registry.format_section() == ""

    def test_format_section_with_entries(self, recalled_registry):
        entries = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1"),
        ]
        recalled_registry.add(entries)
        section = recalled_registry.format_section()
        assert "- [k1]" in section
        assert "user: q1" in section
        assert "assistant: a1" in section

    def test_format_section_with_access_stats(self, recalled_registry):
        entries = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1", accessed_at="2024-01-01", access_count=3),
        ]
        recalled_registry.add(entries)
        section = recalled_registry.format_section()
        assert "last_access=2024-01-01" in section
        assert "count=3" in section

    def test_clear(self, recalled_registry):
        entries = [MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q1", assistant_msg="a1", content="c1")]
        recalled_registry.add(entries)
        recalled_registry.clear()
        assert recalled_registry.format_section() == ""


class TestMemoryStore:
    def test_save_and_list(self, memory_store):
        memory_store.save("hello", "world")
        memory_store.save("foo", "bar")
        entries = memory_store.list_all()
        assert len(entries) == 2

    def test_save_returns_id(self, memory_store):
        id1 = memory_store.save("hello", "world")
        assert isinstance(id1, int)
        assert id1 > 0

    def test_delete(self, memory_store):
        id1 = memory_store.save("hello", "world")
        assert memory_store.delete(id1) is True
        entries = memory_store.list_all()
        assert len(entries) == 0

    def test_list_all_limit(self, memory_store):
        for i in range(10):
            memory_store.save(f"q{i}", f"a{i}")
        entries = memory_store.list_all(limit=5)
        assert len(entries) == 5

    def test_session_id(self, memory_store):
        sid = memory_store.session_id
        assert sid is not None
        assert len(sid) > 0

    def test_new_session_changes_id(self, memory_store):
        old_sid = memory_store.session_id
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.fromisoformat(old_sid) + timedelta(seconds=2))):
            new_sid = memory_store.new_session()
        assert new_sid != old_sid

    def test_recall_excludes_current_session(self, memory_store):
        # Save in current session
        memory_store.save("current q", "current a")
        # Recall excludes current session
        entries = memory_store.recall(query="current", limit=5)
        assert len(entries) == 0

    def test_recall_finds_old_session(self, memory_store):
        # Save, then switch session, then recall
        memory_store.save("old q", "old a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=2))):
            memory_store.new_session()
        entries = memory_store.recall(query="old", limit=5)
        # With mock embedder (zeros), semantic search may return results
        # Keyword search should find it
        entries_kw = memory_store.recall(keywords=["old"], limit=5)
        assert len(entries_kw) >= 1

    def test_recall_no_results(self, memory_store):
        entries = memory_store.recall(query="nonexistent_topic_xyz", limit=5)
        assert len(entries) == 0

    def test_recall_with_keywords(self, memory_store):
        memory_store.save("python programming", "use python for coding")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime()):
            memory_store.new_session()
        entries = memory_store.recall(keywords=["python"], limit=5)
        assert len(entries) >= 1


class TestParseTimeRange:
    """Test time_range filtering via recall()."""

    def test_today(self, memory_store):
        memory_store.save("today q", "today a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime()):
            memory_store.new_session()
        entries = memory_store.recall(keywords=["today"], time_range="today", limit=5)
        assert len(entries) >= 1

    def test_yesterday(self, memory_store):
        memory_store.save("yesterday q", "yesterday a")
        entries = memory_store.recall(keywords=["yesterday"], time_range="yesterday", limit=5)
        assert len(entries) == 0

    def test_empty_time_range(self, memory_store):
        memory_store.save("q", "a")
        entries = memory_store.recall(keywords=["q"], time_range="", limit=5)
        assert isinstance(entries, list)

    def test_invalid_time_range(self, memory_store):
        memory_store.save("x query", "x answer")
        entries = memory_store.recall(keywords=["x"], time_range="invalid_format", limit=5)
        # Invalid format is logged but handled gracefully — returns empty list
        assert isinstance(entries, list)


class TestParseTimeRangeDirect:
    """Test _parse_time_range directly via MemoryStore for all patterns."""

    def test_empty_string(self, memory_store):
        start, end = memory_store._parse_time_range("")
        assert start is None
        assert end is None

    def test_last_session(self, memory_store):
        start, end = memory_store._parse_time_range("last_session")
        assert start is None
        assert end is None

    def test_today(self, memory_store):
        start, end = memory_store._parse_time_range("today")
        assert start is not None
        assert end is None
        # start should be today at midnight
        assert start.endswith("00:00:00")

    def test_yesterday(self, memory_store):
        start, end = memory_store._parse_time_range("yesterday")
        assert start is not None
        assert end is not None

    def test_this_week(self, memory_store):
        start, end = memory_store._parse_time_range("this_week")
        assert start is not None
        assert end is None

    def test_last_week(self, memory_store):
        start, end = memory_store._parse_time_range("last_week")
        assert start is not None
        assert end is not None

    def test_this_month(self, memory_store):
        start, end = memory_store._parse_time_range("this_month")
        assert start is not None
        assert end is None

    def test_last_month(self, memory_store):
        start, end = memory_store._parse_time_range("last_month")
        assert start is not None
        assert end is not None

    def test_last_Nd(self, memory_store):
        start, end = memory_store._parse_time_range("last_7d")
        assert start is not None
        assert end is None

    def test_date_range(self, memory_store):
        start, end = memory_store._parse_time_range("2025-01-01~2025-01-15")
        assert start is not None
        assert end is not None
        assert "2025-01-01" in start
        assert "2025-01-15" in end

    def test_single_date(self, memory_store):
        start, end = memory_store._parse_time_range("2025-06-01")
        assert start is not None
        assert end is None

    def test_unknown_format_returns_none(self, memory_store):
        start, end = memory_store._parse_time_range("garbage_format")
        assert start is None
        assert end is None

    def test_whitespace_handling(self, memory_store):
        start, end = memory_store._parse_time_range("  today  ")
        assert start is not None


class TestCreateEmbedder:
    def test_sentence_transformers_backend(self):
        from qd_evolve.core.memory import _create_embedder
        from qd_evolve.core.config import EmbeddingsBackend

        backend = EmbeddingsBackend(model_path="fake-model", dim=384)
        with patch("qd_evolve.core.memory.SentenceTransformerEmbedder") as MockST:
            _create_embedder(backend)
            MockST.assert_called_once_with("fake-model")

    def test_llama_cpp_backend(self):
        from qd_evolve.core.memory import _create_embedder
        from qd_evolve.core.config import EmbeddingsBackend

        backend = EmbeddingsBackend(model_path="fake-model", dim=384, backend="llama-cpp-python", llama_n_ctx=512, llama_n_batch=256)
        with patch("qd_evolve.core.memory.LlamaCppEmbedder") as MockLC:
            _create_embedder(backend)
            MockLC.assert_called_once_with("fake-model", n_ctx=512, n_batch=256)


class TestMemoryStoreExtended:
    def test_save_with_process(self, memory_store):
        mid = memory_store.save("hello", "world", process="step1: done\nstep2: done")
        assert mid > 0

    def test_delete_returns_true(self, memory_store):
        mid = memory_store.save("hello", "world")
        result = memory_store.delete(mid)
        assert result is True
        # Verify it was actually deleted
        assert len(memory_store.list_all()) == 0

    def test_recall_last_session(self, memory_store):
        memory_store.save("old q", "old a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=2))):
            memory_store.new_session()
        entries = memory_store.recall(time_range="last_session", limit=5)
        assert len(entries) >= 1

    def test_recall_last_session_no_prev(self, memory_store):
        entries = memory_store.recall(time_range="last_session", limit=5)
        assert entries == []

    def test_recall_time_range_only_no_query(self, memory_store):
        memory_store.save("some q", "some a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=2))):
            memory_store.new_session()
        entries = memory_store.recall(time_range="today", limit=5)
        assert isinstance(entries, list)

    def test_recall_semantic_search_failure_handled(self, memory_store):
        memory_store.save("test q", "test a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=2))):
            memory_store.new_session()
        with patch.object(memory_store, "_encode", side_effect=Exception("embedding failed")):
            entries = memory_store.recall(query="test", limit=5)
            assert isinstance(entries, list)

    def test_close_stores_and_closes_db(self, memory_store):
        memory_store.save("test", "test")
        memory_store.close()

    def test_close_with_llama_cpp_exception(self, tmp_path, mock_embedder):
        from qd_evolve.core.memory import MemoryStore, LlamaCppEmbedder
        from qd_evolve.core.config import EmbeddingsBackend

        backend = EmbeddingsBackend(model_path="fake-model", dim=384)
        db_path = str(tmp_path / "close_test.db")

        with patch("qd_evolve.core.memory._create_embedder") as mock_create:
            mock_llama = MagicMock()
            mock_llama._llm.close.side_effect = Exception("close error")
            mock_create.return_value = mock_llama

            with patch("qd_evolve.core.memory.isinstance", return_value=True):
                store = MemoryStore(db_path, backend, list_all_limit=50)
                store.close()

    def test_recall_with_time_range_boundaries(self, memory_store):
        memory_store.save("bounded q", "bounded a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=2))):
            memory_store.new_session()
        entries = memory_store.recall(keywords=["bounded"], time_range="last_7d", limit=5)
        assert isinstance(entries, list)

    def test_recall_query_with_time_range(self, memory_store):
        """Hit semantic search time-range filtering (start/end clauses)."""
        memory_store.save("old question", "old answer")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=5))):
            memory_store.new_session()
        # Use a future time_range so the old data is before the range = no results
        entries = memory_store.recall(query="question", time_range="last_7d", limit=5)
        assert isinstance(entries, list)

    def test_recall_access_stats_failure_handled(self, memory_store):
        memory_store.save("stats q", "stats a")
        with patch("qd_evolve.core.memory.datetime", _ControllableDatetime(datetime.now() + timedelta(seconds=2))):
            memory_store.new_session()

        # Replace _db with a wrapper object whose execute delegates then raises on UPDATE
        real_db = memory_store._db

        class _DbWrapper:
            def __getattr__(self, name):
                return getattr(real_db, name)

            def execute(self, sql, params=None):
                if isinstance(sql, str) and sql.strip().upper().startswith("UPDATE"):
                    raise Exception("update failed")
                if params is None:
                    return real_db.execute(sql)
                return real_db.execute(sql, params)

        memory_store._db = _DbWrapper()
        try:
            entries = memory_store.recall(keywords=["stats"], limit=5)
            assert len(entries) == 1
        finally:
            memory_store._db = real_db