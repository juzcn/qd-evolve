from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., str],
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )
        logger.debug("Registered tool: {}", name)

    def tool(self, name: str, description: str, input_schema: dict[str, Any]):
        def decorator(fn: Callable[..., str]) -> Callable[..., str]:
            self.register(name, description, input_schema, fn)
            return fn
        return decorator

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise KeyError(f"Tool not found: {name}")
        return self._tools[name]

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, **kwargs: Any) -> str:
        td = self.get(name)
        logger.info("Executing tool: {} with args: {}", name, list(kwargs.keys()))
        try:
            result = td.handler(**kwargs)
            logger.debug("Tool {} completed", name)
            return result
        except Exception as e:
            logger.error("Tool {} failed: {}", name, e)
            return f"Error executing {name}: {e}"


_global_registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _global_registry
