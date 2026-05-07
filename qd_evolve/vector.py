from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import sqlite_vec
from loguru import logger
from pydantic import BaseModel

from qd_evolve.config import Settings


class VectorItem(BaseModel):
    id: int | None = None
    content: str
    metadata: dict = {}


class VectorStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db_path = Path(settings.db_path)
        self._db: sqlite3.Connection | None = None
        self._embed_model = None

    @property
    def embed_model(self):
        if self._embed_model is None:
            from sentence_transformers import SentenceTransformer
            model_path = self.settings.embedding_model_path
            logger.info("Loading embedding model from {}", model_path)
            self._embed_model = SentenceTransformer(model_path)
            logger.info("Embedding model loaded, dim={}", self._embed_model.get_sentence_embedding_dimension())
        return self._embed_model

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(str(self.db_path))
            self._db.enable_load_extension(True)
            sqlite_vec.load(self._db)
            self._db.enable_load_extension(False)
            self._init_tables()
        return self._db

    def _init_tables(self) -> None:
        dim = self.settings.embedding_dimensions
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0("
            f"  id integer primary key autoincrement,"
            f"  embedding float[{dim}]"
            f")"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS items_meta ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  content TEXT NOT NULL,"
            "  metadata TEXT DEFAULT '{}',"
            "  FOREIGN KEY (id) REFERENCES vec_items(id)"
            ")"
        )
        self.db.commit()

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = self.embed_model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    def add(self, items: list[VectorItem]) -> list[int]:
        texts = [item.content for item in items]
        embeddings = self.embed(texts)
        ids: list[int] = []
        for item, emb in zip(items, embeddings):
            cur = self.db.execute(
                "INSERT INTO items_meta (content, metadata) VALUES (?, ?)",
                (item.content, json.dumps(item.metadata, ensure_ascii=False)),
            )
            row_id = cur.lastrowid
            self.db.execute(
                "INSERT INTO vec_items (id, embedding) VALUES (?, ?)",
                (row_id, sqlite_vec.serialize_float32(emb)),
            )
            ids.append(row_id)
        self.db.commit()
        logger.info("Added {} items to vector store", len(ids))
        return ids

    def search(self, query: str, top_k: int = 5) -> list[tuple[VectorItem, float]]:
        query_emb = self.embed([query])[0]
        results = self.db.execute(
            "SELECT v.id, v.distance, m.content, m.metadata "
            "FROM vec_items v "
            "JOIN items_meta m ON v.id = m.id "
            "WHERE v.embedding MATCH ? "
            "ORDER BY v.distance "
            "LIMIT ?",
            (sqlite_vec.serialize_float32(query_emb), top_k),
        ).fetchall()

        items: list[tuple[VectorItem, float]] = []
        for row_id, distance, content, metadata in results:
            item = VectorItem(
                id=row_id,
                content=content,
                metadata=json.loads(metadata) if metadata else {},
            )
            items.append((item, distance))
        return items

    def delete(self, item_id: int) -> None:
        self.db.execute("DELETE FROM vec_items WHERE id = ?", (item_id,))
        self.db.execute("DELETE FROM items_meta WHERE id = ?", (item_id,))
        self.db.commit()
        logger.info("Deleted item {}", item_id)

    def count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) FROM items_meta").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
