"""Persistent memory store — SQLite + sqlite-vec for semantic + keyword search."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import sqlite_vec
from loguru import logger
from pydantic import BaseModel


class MemoryEntry(BaseModel):
    id: int
    key: str
    session_id: str
    user_msg: str
    assistant_msg: str
    content: str
    accessed_at: str | None = None
    access_count: int = 0
    distance: float | None = None


class RecalledMemoryRegistry:
    """Tracks auto-recalled memory entries to deduplicate across turns."""

    def __init__(self) -> None:
        self._entries: dict[int, MemoryEntry] = {}

    def add(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        """Add entries, returning only those not already present (deduped)."""
        new = [e for e in entries if e.id not in self._entries]
        for e in new:
            self._entries[e.id] = e
        return new

    def format_section(self) -> str:
        """Format all recalled entries as a memory section string."""
        if not self._entries:
            return ""
        lines = []
        for e in self._entries.values():
            lines.append(f"- [{e.key}] user: {e.user_msg} | assistant: {e.assistant_msg}")
        return "\n".join(lines)

    def clear(self) -> None:
        self._entries.clear()


class Embedder(Protocol):
    def encode(self, text: str) -> np.ndarray: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_path: str) -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_path)

    def encode(self, text: str) -> np.ndarray:
        return np.array(self._model.encode(text), dtype=np.float32)


class LlamaCppEmbedder:
    def __init__(self, model_path: str) -> None:
        from llama_cpp import Llama
        self._llm = Llama(model_path=model_path, embedding=True, verbose=False, n_ctx=8192, n_batch=512, n_ubatch=512)

    def encode(self, text: str) -> np.ndarray:
        result = self._llm.create_embedding(text)
        return np.array(result["data"][0]["embedding"], dtype=np.float32)


def _create_embedder(model_path: str) -> Embedder:
    p = Path(model_path)
    if p.suffix.lower() == ".gguf":
        logger.info("Using llama-cpp embedder: {}", model_path)
        return LlamaCppEmbedder(model_path)
    logger.info("Using sentence-transformers embedder: {}", model_path)
    return SentenceTransformerEmbedder(model_path)


class MemoryStore:
    def __init__(self, db_path: str | Path, embedding_model_path: str, embedding_dim: int = 1024) -> None:
        self._db_path = Path(db_path)
        self._embedding_model_path = embedding_model_path
        self._embedding_dim = embedding_dim
        self._session_id = datetime.now().isoformat(timespec="seconds")
        self._db = sqlite3.connect(str(self._db_path))
        self._embedder = _create_embedder(embedding_model_path)
        self._init_db()
        logger.info("MemoryStore initialized: db={}, session_id={}, dim={}", self._db_path, self._session_id, self._embedding_dim)

    def _init_db(self) -> None:
        self._db.enable_load_extension(True)
        sqlite_vec.load(self._db)
        self._db.enable_load_extension(False)

        self._db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                user_msg TEXT NOT NULL,
                assistant_msg TEXT NOT NULL,
                content TEXT NOT NULL,
                accessed_at TEXT,
                access_count INTEGER DEFAULT 0
            )
        """)

        vec_tables = [row[0] for row in self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_vec'"
        ).fetchall()]
        if not vec_tables:
            self._db.execute(f"""
                CREATE VIRTUAL TABLE memory_vec USING vec0(
                    embedding float[{self._embedding_dim}]
                )
            """)

        self._db.commit()

    @property
    def session_id(self) -> str:
        return self._session_id

    def new_session(self) -> str:
        self._session_id = datetime.now().isoformat(timespec="seconds")
        logger.info("New memory session: {}", self._session_id)
        return self._session_id

    def _encode(self, text: str) -> np.ndarray:
        return self._embedder.encode(text)

    def save(self, user_msg: str, assistant_msg: str) -> int:
        key = datetime.now().isoformat(timespec="seconds")
        content = f"user: {user_msg}\nassistant: {assistant_msg}"

        cursor = self._db.execute(
            "INSERT INTO memories (key, session_id, user_msg, assistant_msg, content) VALUES (?, ?, ?, ?, ?)",
            (key, self._session_id, user_msg, assistant_msg, content),
        )
        memory_id = cursor.lastrowid

        embedding = self._encode(content)
        self._db.execute(
            "INSERT INTO memory_vec (rowid, embedding) VALUES (?, ?)",
            (memory_id, embedding),
        )
        self._db.commit()
        logger.debug("Saved memory id={}, key={}", memory_id, key)
        return memory_id

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        results: dict[int, MemoryEntry] = {}

        # Semantic search
        try:
            query_vec = self._encode(query)
            rows = self._db.execute("""
                SELECT m.id, m.key, m.session_id, m.user_msg, m.assistant_msg, m.content,
                       m.accessed_at, m.access_count, v.distance
                FROM memory_vec v
                JOIN memories m ON m.id = v.rowid
                WHERE v.embedding MATCH ? AND k = ?
                  AND m.session_id != ?
                ORDER BY v.distance
                LIMIT ?
            """, (query_vec, limit, self._session_id, limit)).fetchall()

            for row in rows:
                entry = MemoryEntry(
                    id=row[0], key=row[1], session_id=row[2], user_msg=row[3],
                    assistant_msg=row[4], content=row[5], accessed_at=row[6],
                    access_count=row[7], distance=row[8],
                )
                results[entry.id] = entry
        except Exception as e:
            logger.warning("Semantic search failed: {}", e)

        # Keyword search
        like_pattern = f"%{query}%"
        rows = self._db.execute("""
            SELECT id, key, session_id, user_msg, assistant_msg, content, accessed_at, access_count
            FROM memories
            WHERE content LIKE ?
              AND session_id != ?
            ORDER BY key DESC
            LIMIT ?
        """, (like_pattern, self._session_id, limit)).fetchall()

        for row in rows:
            if row[0] not in results:
                entry = MemoryEntry(
                    id=row[0], key=row[1], session_id=row[2], user_msg=row[3],
                    assistant_msg=row[4], content=row[5], accessed_at=row[6],
                    access_count=row[7],
                )
                results[entry.id] = entry

        # Update access stats
        now = datetime.now().isoformat(timespec="seconds")
        for mid in results:
            self._db.execute(
                "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                (now, mid),
            )
        self._db.commit()

        return sorted(results.values(), key=lambda e: e.distance if e.distance is not None else 999)

    def search_by_time(
        self, start: str | None = None, end: str | None = None, limit: int = 20
    ) -> list[MemoryEntry]:
        clauses: list[str] = ["session_id != ?"]
        params: list[Any] = [self._session_id]
        if start:
            clauses.append("key >= ?")
            params.append(start)
        if end:
            clauses.append("key < ?")
            params.append(end)
        params.append(limit)

        where = " AND ".join(clauses)
        rows = self._db.execute(f"""
            SELECT id, key, session_id, user_msg, assistant_msg, content, accessed_at, access_count
            FROM memories
            WHERE {where}
            ORDER BY key DESC
            LIMIT ?
        """, params).fetchall()

        return [
            MemoryEntry(
                id=r[0], key=r[1], session_id=r[2], user_msg=r[3],
                assistant_msg=r[4], content=r[5], accessed_at=r[6], access_count=r[7],
            )
            for r in rows
        ]

    def delete(self, memory_id: int) -> bool:
        self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        try:
            self._db.execute("DELETE FROM memory_vec WHERE rowid = ?", (memory_id,))
        except Exception:
            pass
        self._db.commit()
        return True

    def list_all(self, limit: int = 50) -> list[MemoryEntry]:
        rows = self._db.execute("""
            SELECT id, key, session_id, user_msg, assistant_msg, content, accessed_at, access_count
            FROM memories
            ORDER BY key DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [
            MemoryEntry(
                id=r[0], key=r[1], session_id=r[2], user_msg=r[3],
                assistant_msg=r[4], content=r[5], accessed_at=r[6], access_count=r[7],
            )
            for r in rows
        ]

    def _parse_time_range(self, time_range: str) -> tuple[str | None, str | None]:
        """Parse time_range string to (start, end) ISO timestamps. Returns (None, None) if empty."""
        if not time_range:
            return None, None

        tr = time_range.strip().lower()
        now = datetime.now()

        if tr == "last_session":
            return None, None  # handled separately in recall()
        if tr == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(timespec="seconds"), None
        if tr == "yesterday":
            start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")
        if tr == "this_week":
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(timespec="seconds"), None
        if tr == "last_week":
            this_monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            start = this_monday - timedelta(weeks=1)
            end = this_monday
            return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")
        if tr == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(timespec="seconds"), None
        if tr == "last_month":
            first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = first_this
            start = (first_this - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")

        # last_Nd pattern
        m = re.match(r"last_(\d+)d", tr)
        if m:
            n = int(m.group(1))
            start = (now - timedelta(days=n)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(timespec="seconds"), None

        # YYYY-MM-DD~YYYY-MM-DD
        m = re.match(r"(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})", tr)
        if m:
            start = datetime.strptime(m.group(1), "%Y-%m-%d").isoformat(timespec="seconds")
            end = datetime.strptime(m.group(2), "%Y-%m-%d").replace(hour=23, minute=59, second=59).isoformat(timespec="seconds")
            return start, end

        # YYYY-MM-DD (from date to now)
        m = re.match(r"(\d{4}-\d{2}-\d{2})$", tr)
        if m:
            start = datetime.strptime(m.group(1), "%Y-%m-%d").isoformat(timespec="seconds")
            return start, None

        logger.warning("Unknown time_range format: {}", time_range)
        return None, None

    def recall(
        self,
        query: str | None = None,
        keywords: list[str] | None = None,
        time_range: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        # last_session: find the most recent session that isn't current
        if time_range and time_range.strip().lower() == "last_session":
            row = self._db.execute("""
                SELECT session_id FROM memories
                WHERE session_id != ?
                ORDER BY key DESC LIMIT 1
            """, (self._session_id,)).fetchone()
            if not row:
                return []
            target_session = row[0]
            rows = self._db.execute("""
                SELECT id, key, session_id, user_msg, assistant_msg, content, accessed_at, access_count
                FROM memories
                WHERE session_id = ?
                ORDER BY key ASC
                LIMIT ?
            """, (target_session, limit)).fetchall()
            return [
                MemoryEntry(
                    id=r[0], key=r[1], session_id=r[2], user_msg=r[3],
                    assistant_msg=r[4], content=r[5], accessed_at=r[6], access_count=r[7],
                )
                for r in rows
            ]

        start, end = self._parse_time_range(time_range or "")
        results: dict[int, MemoryEntry] = {}

        # Semantic search
        if query:
            try:
                query_vec = self._encode(query)
                clauses = ["m.session_id != ?"]
                params: list[Any] = [self._session_id]
                if start:
                    clauses.append("m.key >= ?")
                    params.append(start)
                if end:
                    clauses.append("m.key < ?")
                    params.append(end)
                where = " AND ".join(clauses)
                params.extend([query_vec, limit, limit])

                rows = self._db.execute(f"""
                    SELECT m.id, m.key, m.session_id, m.user_msg, m.assistant_msg, m.content,
                           m.accessed_at, m.access_count, v.distance
                    FROM memory_vec v
                    JOIN memories m ON m.id = v.rowid
                    WHERE {where}
                      AND v.embedding MATCH ? AND k = ?
                    ORDER BY v.distance
                    LIMIT ?
                """, params).fetchall()

                for row in rows:
                    entry = MemoryEntry(
                        id=row[0], key=row[1], session_id=row[2], user_msg=row[3],
                        assistant_msg=row[4], content=row[5], accessed_at=row[6],
                        access_count=row[7], distance=row[8],
                    )
                    results[entry.id] = entry
            except Exception as e:
                logger.warning("Semantic search failed: {}", e)

        # Keyword search
        if keywords:
            for kw in keywords:
                clauses = ["session_id != ?", "content LIKE ?"]
                params_kw: list[Any] = [self._session_id, f"%{kw}%"]
                if start:
                    clauses.append("key >= ?")
                    params_kw.append(start)
                if end:
                    clauses.append("key < ?")
                    params_kw.append(end)
                where = " AND ".join(clauses)
                params_kw.append(limit)

                rows = self._db.execute(f"""
                    SELECT id, key, session_id, user_msg, assistant_msg, content, accessed_at, access_count
                    FROM memories
                    WHERE {where}
                    ORDER BY key DESC
                    LIMIT ?
                """, params_kw).fetchall()

                for row in rows:
                    if row[0] not in results:
                        entry = MemoryEntry(
                            id=row[0], key=row[1], session_id=row[2], user_msg=row[3],
                            assistant_msg=row[4], content=row[5], accessed_at=row[6],
                            access_count=row[7],
                        )
                        results[entry.id] = entry

        # No query or keywords: just time-bounded listing
        if not query and not keywords:
            clauses = ["session_id != ?"]
            params_t: list[Any] = [self._session_id]
            if start:
                clauses.append("key >= ?")
                params_t.append(start)
            if end:
                clauses.append("key < ?")
                params_t.append(end)
            where = " AND ".join(clauses)
            params_t.append(limit)

            rows = self._db.execute(f"""
                SELECT id, key, session_id, user_msg, assistant_msg, content, accessed_at, access_count
                FROM memories
                WHERE {where}
                ORDER BY key DESC
                LIMIT ?
            """, params_t).fetchall()

            for row in rows:
                if row[0] not in results:
                    entry = MemoryEntry(
                        id=row[0], key=row[1], session_id=row[2], user_msg=row[3],
                        assistant_msg=row[4], content=row[5], accessed_at=row[6],
                        access_count=row[7],
                    )
                    results[entry.id] = entry

        # Update access stats
        now = datetime.now().isoformat(timespec="seconds")
        for mid in results:
            self._db.execute(
                "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                (now, mid),
            )
        self._db.commit()

        return sorted(results.values(), key=lambda e: e.distance if e.distance is not None else 999)

    def close(self) -> None:
        if isinstance(self._embedder, LlamaCppEmbedder):
            try:
                self._embedder._llm.close()
            except Exception:
                pass
        self._db.close()
        logger.info("MemoryStore closed")
