"""Persistent memory store — SQLite + sqlite-vec for semantic + keyword search."""

from __future__ import annotations

import sqlite3
from datetime import datetime
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
        self._llm = Llama(model_path=model_path, embedding=True, verbose=False, n_ctx=8192)

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
        self._embedder: Embedder | None = None
        self._db = sqlite3.connect(str(self._db_path))
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

    def _encode(self, text: str) -> np.ndarray:
        if self._embedder is None:
            self._embedder = _create_embedder(self._embedding_model_path)
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
                WHERE v.embedding MATCH ?
                  AND m.session_id != ?
                ORDER BY v.distance
                LIMIT ?
            """, (query_vec, self._session_id, limit)).fetchall()

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

    def close(self) -> None:
        self._db.close()
        logger.info("MemoryStore closed")
