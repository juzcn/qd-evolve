from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any, Callable

from loguru import logger


class ToolDef:
    __slots__ = ("name", "description", "input_schema", "handler", "category", "is_callable")

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable[..., str] | None = None,
        category: str = "builtin",
        is_callable: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.category = category
        self.is_callable = is_callable


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._disabled: set[str] = set()

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable[..., str] | None = None,
        category: str = "builtin",
        is_callable: bool = True,
    ) -> None:
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            category=category,
            is_callable=is_callable,
        )
        logger.debug("Tool registered: {} [{}] callable={}", name, category, is_callable)

    def tool(self, name: str | None = None, description: str = "", category: str = "builtin", is_callable: bool = True):
        def decorator(fn: Callable[..., str]) -> Callable[..., str]:
            tname = name or fn.__name__
            self.register(
                name=tname,
                description=description or fn.__doc__ or "",
                input_schema={"type": "object", "additionalProperties": True},
                handler=fn,
                category=category,
                is_callable=is_callable,
            )
            return fn
        return decorator

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        self._disabled.add(name)

    def is_enabled(self, name: str) -> bool:
        return name not in self._disabled

    def definitions(self, callable_only: bool = True, api_format: str = "openai") -> list[dict]:
        """Return tool definitions for the API.

        callable_only=True: only tools the LLM can call (sent to API).
        callable_only=False: all tools including prompt-only skills.
        api_format: "openai" for OpenAI/Responses API, "anthropic" for Messages API.
        """
        result = []
        for name, td in self._tools.items():
            if name in self._disabled:
                continue
            if callable_only and not td.is_callable:
                continue
            if api_format == "anthropic":
                result.append({
                    "name": td.name,
                    "description": td.description,
                    "input_schema": td.input_schema,
                })
            else:
                result.append({
                    "type": "function",
                    "function": {
                        "name": td.name,
                        "description": td.description,
                        "parameters": td.input_schema,
                    },
                })
        return result

    def list_by_category(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name, td in self._tools.items():
            if name in self._disabled:
                continue
            result.setdefault(td.category, []).append(name)
        return result

    def call(self, name: str, **kwargs: Any) -> str:
        td = self._tools.get(name)
        if td is None:
            return f"Error: unknown tool '{name}'"
        if not td.is_callable or td.handler is None:
            return f"Error: tool '{name}' is not callable"
        return td.handler(**kwargs)

    def discover_tools(self) -> None:
        tools_dir = Path(__file__).parent
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"qd_evolve.tools.{py_file.stem}"
            try:
                importlib.import_module(module_name)
            except Exception:
                logger.exception("Failed to import tool module: {}", module_name)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
