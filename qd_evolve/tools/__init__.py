from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from qd_evolve.toolbox import ToolBox


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    handler: Callable
    category: str = "builtin"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._disabled: set[str] = set()
        self._toolbox: ToolBox | None = None

    def set_toolbox(self, toolbox: ToolBox) -> None:
        self._toolbox = toolbox

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
        category: str = "builtin",
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            category=category,
        )
        logger.info("Registered tool: {} [{}]", name, category)
        if self._toolbox:
            self._toolbox.save_tool(name, description, input_schema, category)

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

    def list_by_category(self) -> dict[str, list[str]]:
        cats: dict[str, list[str]] = {}
        for td in self._tools.values():
            cats.setdefault(td.category, []).append(td.name)
        return cats

    def enable(self, name: str) -> None:
        self._disabled.discard(name)
        if self._toolbox:
            self._toolbox.set_enabled(name, True)

    def disable(self, name: str) -> None:
        self._disabled.add(name)
        if self._toolbox:
            self._toolbox.set_enabled(name, False)

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

    def load_from_toolbox(self, toolbox: ToolBox) -> int:
        """Load tools from ToolBox that aren't already in memory.

        Returns count of newly loaded tools.
        """
        count = 0
        for tool_data in toolbox.load_all():
            name = tool_data["name"]
            if name in self._tools:
                # Already registered this session — sync enabled state
                if not tool_data["enabled"]:
                    self._disabled.add(name)
                continue
            # Tool not in memory — register with a placeholder handler
            # that returns an error (e.g. MCP server not running)
            self._tools[name] = ToolDefinition(
                name=name,
                description=tool_data["description"],
                input_schema=tool_data["input_schema"],
                handler=_unavailable_handler(name),
                category=tool_data["category"],
            )
            if not tool_data["enabled"]:
                self._disabled.add(name)
            count += 1
            logger.info("Loaded tool from toolbox: {} [{}]", name, tool_data["category"])
        return count

    def search_tools(self, query: str, top_k: int = 5) -> list[tuple[str, str, float]]:
        if self._toolbox:
            results = self._toolbox.search(query, top_k)
            return [(name, desc, score) for name, desc, _cat, score in results]

        # Fallback: in-memory search (no embeddings)
        results: dict[str, tuple[str, float]] = {}
        q_lower = query.lower()
        for name, td in self._tools.items():
            if name == query:
                results[name] = (td.description, 1.0)
            elif name.startswith(query):
                results[name] = (td.description, 0.9)
            elif q_lower in td.description.lower() or q_lower in name.lower():
                results[name] = (td.description, 0.8)
        sorted_results = sorted(results.items(), key=lambda x: x[1][1], reverse=True)
        return [(name, desc, score) for name, (desc, score) in sorted_results[:top_k]]


def _unavailable_handler(tool_name: str):
    def handler(**kwargs: Any) -> str:
        return f"Tool '{tool_name}' is not available in this session (source offline)."
    return handler


_global_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _global_registry
