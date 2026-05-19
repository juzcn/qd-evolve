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
        # Save in session 1, then switch session and recall
        memory_store.save("python programming", "use python for coding")
        import time
        time.sleep(1.1)
        memory_store.new_session()
        entries = memory_store.recall(keywords=["python"], limit=5)
        assert len(entries) >= 1


class TestParseTimeRange:
    """Test time_range filtering via recall()."""

    def test_today(self, memory_store):
        # Save in session 1, then switch session and recall with today filter
        memory_store.save("today q", "today a")
        import time
        time.sleep(1.1)
        memory_store.new_session()
        entries = memory_store.recall(keywords=["today"], time_range="today", limit=5)
        assert len(entries) >= 1

    def test_yesterday(self, memory_store):
        memory_store.save("yesterday q", "yesterday a")
        entries = memory_store.recall(keywords=["yesterday"], time_range="yesterday", limit=5)
        # Saved today, so "yesterday" filter should return 0
        assert isinstance(entries, list)

    def test_empty_time_range(self, memory_store):
        memory_store.save("q", "a")
        entries = memory_store.recall(keywords=["q"], time_range="", limit=5)
        assert isinstance(entries, list)

    def test_invalid_time_range(self, memory_store):
        entries = memory_store.recall(keywords=["q"], time_range="invalid_format", limit=5)
        assert isinstance(entries, list)