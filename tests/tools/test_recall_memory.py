"""Tests for qd_evolve.tools.recall_memory — recall_memory handler."""

from unittest.mock import MagicMock, patch


from qd_evolve.core.memory import MemoryEntry


class TestRecallMemory:
    def test_recall_with_query(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = [
            MemoryEntry(id=1, key="2024-01-01", session_id="s1", user_msg="hello", assistant_msg="world", content="c1"),
        ]

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(query="hello")
            assert "Found 1 memories" in result
            assert "hello" in result

    def test_recall_no_results(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = []

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(query="nonexistent")
            assert "No memories found" in result

    def test_recall_store_not_initialized(self):
        with patch("qd_evolve.tools.recall_memory._memory_store", None):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(query="hello")
            assert "not initialized" in result

    def test_recall_with_distance(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q", assistant_msg="a", content="c", distance=0.15),
        ]

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(query="q")
            assert "relevance" in result

    def test_recall_with_keywords(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="python", assistant_msg="use python", content="c"),
        ]

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(keywords=["python"])
            assert "python" in result

    def test_recall_with_time_range(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = []

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(time_range="today")
            assert "No memories found" in result

    def test_recall_custom_limit(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = []

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            _recall_memory(query="q", limit=10)
            mock_store.recall.assert_called_once_with(query="q", keywords=None, time_range=None, limit=10)

    def test_recall_with_access_stats(self):
        mock_store = MagicMock()
        mock_store.recall.return_value = [
            MemoryEntry(id=1, key="k1", session_id="s1", user_msg="q", assistant_msg="a", content="c", accessed_at="2024-01-01", access_count=5),
        ]

        with patch("qd_evolve.tools.recall_memory._memory_store", mock_store):
            from qd_evolve.tools.recall_memory import _recall_memory
            result = _recall_memory(query="q")
            assert "last_access" in result
            assert "access_count" in result

    def test_set_memory_store(self):
        from qd_evolve.tools.recall_memory import set_memory_store
        mock_store = MagicMock()
        set_memory_store(mock_store)
        from qd_evolve.tools import recall_memory as mod
        assert mod._memory_store == mock_store
        mod._memory_store = None

    def test_set_default_limit(self):
        from qd_evolve.tools.recall_memory import set_default_limit
        set_default_limit(20)
        from qd_evolve.tools import recall_memory as mod
        assert mod._default_limit == 20
        mod._default_limit = 5