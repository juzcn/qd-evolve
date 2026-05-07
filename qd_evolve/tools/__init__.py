from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    handler: Callable


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._disabled: set[str] = set()
        self._embed_fn: Callable[[list[str]], list[list[float]]] | None = None
        self._tool_embeddings: dict[str, list[float]] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )
        logger.info("Registered tool: {}", name)

    def tool(self, name: str, description: str, input_schema: dict):
        def decorator(fn: Callable) -> Callable:
            self.register(name, description, input_schema, fn)
            return fn
        return decorator

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict]:
        return [
            {
                "name": td.name,
                "description": td.description,
                "input_schema": td.input_schema,
            }
            for td in self._tools.values()
            if td.name not in self._disabled
        ]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def list_all(self) -> list[tuple[str, str, bool]]:
        return [
            (td.name, td.description, td.name not in self._disabled)
            for td in self._tools.values()
        ]

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        self._disabled.add(name)

    def is_enabled(self, name: str) -> bool:
        return name not in self._disabled

    def execute(self, name: str, **kwargs) -> str:
        td = self._tools.get(name)
        if td is None:
            raise ValueError(f"Unknown tool: {name}")
        if name in self._disabled:
            raise ValueError(f"Tool {name} is disabled")
        logger.info("Tool call: {} | args: {}", name, kwargs)
        result = td.handler(**kwargs)
        logger.info("Tool result: {} | output: {}", name, str(result)[:500])
        return result

    def discover_tools(self) -> list[str]:
        tools_dir = Path(__file__).parent
        loaded = []
        for py in sorted(tools_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            module_name = f"qd_evolve.tools.{py.stem}"
            try:
                importlib.import_module(module_name)
                loaded.append(py.stem)
                logger.info("Discovered tool module: {}", py.stem)
            except Exception:
                logger.exception("Failed to load tool module: {}", py.stem)
        return loaded

    def set_embed_fn(self, fn: Callable[[list[str]], list[list[float]]]) -> None:
        self._embed_fn = fn

    def build_tool_embeddings(self) -> None:
        if not self._embed_fn or not self._tools:
            return
        texts = [f"{td.name}: {td.description}" for td in self._tools.values()]
        embeddings = self._embed_fn(texts)
        self._tool_embeddings = {
            td.name: emb for td, emb in zip(self._tools.values(), embeddings)
        }
        logger.info("Built embeddings for {} tools", len(self._tool_embeddings))

    def search_tools(self, query: str, top_k: int = 5) -> list[tuple[str, str, float]]:
        results: dict[str, tuple[str, float]] = {}

        # 1. Exact name match
        for name, td in self._tools.items():
            if name == query:
                results[name] = (td.description, 1.0)
            elif name.startswith(query):
                results[name] = (td.description, 0.9)

        # 2. Keyword match
        q_lower = query.lower()
        for name, td in self._tools.items():
            if name in results:
                continue
            if q_lower in td.description.lower() or q_lower in name.lower():
                results[name] = (td.description, 0.8)

        # 3. Semantic match
        if self._tool_embeddings and self._embed_fn:
            query_emb = self._embed_fn([query])[0]
            for name, emb in self._tool_embeddings.items():
                if name in results:
                    continue
                sim = _cosine_similarity(query_emb, emb)
                if sim > 0.3:
                    results[name] = (self._tools[name].description, sim)

        sorted_results = sorted(results.items(), key=lambda x: x[1][1], reverse=True)
        return [(name, desc, score) for name, (desc, score) in sorted_results[:top_k]]


_global_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _global_registry
