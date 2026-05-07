from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec
from loguru import logger

from qd_evolve.config import Settings


class ToolBox:
    """Persistent tool store backed by sqlite-vec.

    Stores tool metadata + embeddings for hybrid search
    (name exact → keyword → semantic).
    """

    def __init__(self, settings: Settings) -> None:
        self._db_path = Path(settings.db_path)
        self._dim = settings.embedding_dimensions
        self._db: sqlite3.Connection | None = None
        self._embed_fn: Any = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self._db = sqlite3.connect(str(self._db_path))
            self._db.enable_load_extension(True)
            sqlite_vec.load(self._db)
            self._db.enable_load_extension(False)
            self._init_tables()
        return self._db

    def _init_tables(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS tools ("
            "  name TEXT PRIMARY KEY,"
            "  description TEXT NOT NULL,"
            "  input_schema TEXT NOT NULL,"
            "  category TEXT NOT NULL DEFAULT 'builtin',"
            "  enabled INTEGER NOT NULL DEFAULT 1"
            ")"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS mcp_servers ("
            "  name TEXT PRIMARY KEY,"
            "  config_path TEXT NOT NULL"
            ")"
        )
        self.db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_tools USING vec0("
            f"  name TEXT PRIMARY KEY,"
            f"  embedding float[{self._dim}]"
            f")"
        )
        self.db.commit()

    def set_embed_fn(self, fn: Any) -> None:
        self._embed_fn = fn

    def save_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        category: str = "builtin",
    ) -> None:
        schema_json = json.dumps(input_schema, ensure_ascii=False)
        self.db.execute(
            "INSERT OR REPLACE INTO tools (name, description, input_schema, category, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            (name, description, schema_json, category),
        )
        if self._embed_fn:
            try:
                emb = self._embed_fn([f"{name}: {description}"])[0]
                self.db.execute(
                    "INSERT OR REPLACE INTO vec_tools (name, embedding) VALUES (?, ?)",
                    (name, sqlite_vec.serialize_float32(emb)),
                )
            except Exception as e:
                logger.debug("ToolBox: embedding failed for {}: {}", name, e)
        self.db.commit()

    def remove_tool(self, name: str) -> None:
        self.db.execute("DELETE FROM tools WHERE name = ?", (name,))
        try:
            self.db.execute("DELETE FROM vec_tools WHERE name = ?", (name,))
        except Exception:
            pass
        self.db.commit()

    def load_all(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT name, description, input_schema, category, enabled FROM tools"
        ).fetchall()
        results = []
        for name, desc, schema_json, category, enabled in rows:
            results.append({
                "name": name,
                "description": desc,
                "input_schema": json.loads(schema_json),
                "category": category,
                "enabled": bool(enabled),
            })
        return results

    def set_enabled(self, name: str, enabled: bool) -> None:
        self.db.execute(
            "UPDATE tools SET enabled = ? WHERE name = ?",
            (1 if enabled else 0, name),
        )
        self.db.commit()

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, str, str, float]]:
        """Hybrid search: name exact → keyword → semantic.

        Returns list of (name, description, category, score).
        """
        results: dict[str, tuple[str, str, float]] = {}
        q_lower = query.lower()

        # 1. Exact name match
        for row in self.db.execute("SELECT name, description, category FROM tools").fetchall():
            name, desc, cat = row
            if name == query:
                results[name] = (desc, cat, 1.0)
            elif name.startswith(query):
                results[name] = (desc, cat, 0.9)

        # 2. Keyword match
        for row in self.db.execute("SELECT name, description, category FROM tools").fetchall():
            name, desc, cat = row
            if name in results:
                continue
            if q_lower in name.lower() or q_lower in desc.lower():
                results[name] = (desc, cat, 0.8)

        # 3. Semantic match
        if self._embed_fn:
            try:
                query_emb = self._embed_fn([query])[0]
                rows = self.db.execute(
                    "SELECT v.name, v.distance, t.description, t.category "
                    "FROM vec_tools v "
                    "JOIN tools t ON v.name = t.name "
                    "WHERE v.embedding MATCH ? AND k = ? "
                    "ORDER BY v.distance",
                    (sqlite_vec.serialize_float32(query_emb), top_k),
                ).fetchall()
                for name, distance, desc, cat in rows:
                    if name in results:
                        continue
                    score = max(0.0, 1.0 - distance)
                    if score > 0.3:
                        results[name] = (desc, cat, score)
            except Exception as e:
                logger.debug("ToolBox: semantic search failed: {}", e)

        sorted_results = sorted(results.items(), key=lambda x: x[1][2], reverse=True)
        return [(name, desc, cat, score) for name, (desc, cat, score) in sorted_results[:top_k]]

    # --- MCP server persistence ---

    def add_mcp_server(self, name: str, config_path: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO mcp_servers (name, config_path) VALUES (?, ?)",
            (name, config_path),
        )
        self.db.commit()

    def remove_mcp_server(self, name: str) -> None:
        self.db.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
        self.db.commit()

    def list_mcp_servers(self) -> list[tuple[str, str]]:
        """Return (name, config_path) pairs."""
        rows = self.db.execute("SELECT name, config_path FROM mcp_servers").fetchall()
        return [(name, path) for name, path in rows]

    def load_mcp_servers(self) -> list[dict]:
        """Load MCP server configs by reading their JSON files."""
        rows = self.db.execute("SELECT name, config_path FROM mcp_servers").fetchall()
        results = []
        for name, config_path in rows:
            try:
                data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("MCP config file unreadable: {} — {}", config_path, e)
                continue
            # Support nested {"mcpServers": {...}} format
            if "mcpServers" in data:
                srv = data["mcpServers"].get(name, {})
            else:
                srv = data
            results.append({
                "name": name,
                "command": srv.get("command", ""),
                "args": srv.get("args", []),
                "env": srv.get("env", {}),
            })
        return results

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None
