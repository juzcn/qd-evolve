"""Tool registry —discovers, registers, and manages callable tools."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable

from qd_evolve.logger import logger
from pydantic import BaseModel


class ToolDef(BaseModel):
    name: str
    description: str
    handler: Callable[..., str]
    input_schema: dict[str, Any] = {}
    enabled: bool = True


class ToolRegistry:
    """Registry of callable tools for the agent loop."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[..., str],
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        if name in self._tools:
            logger.warning("Tools: tool name collision, overwriting: %s", name)
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            handler=handler,
            input_schema=input_schema or {"type": "object", "properties": {}},
        )
        logger.debug("Tools: registered tool: %s", name)

    def call(self, tool_name: str, **kwargs: Any) -> str:
        td = self._tools.get(tool_name)
        if td is None:
            return f"Error: Tool '{tool_name}' not found"
        if not td.enabled:
            return f"Error: Tool '{tool_name}' is disabled"

        from qd_evolve.toolbox import get_default
        timeout = get_default("timeout", 0)

        try:
            if timeout > 0:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(td.handler, **kwargs)
                    return future.result(timeout=timeout)
            return td.handler(**kwargs)
        except Exception as e:
            if isinstance(e, ImportError):
                raise
            import concurrent.futures
            if isinstance(e, concurrent.futures.TimeoutError):
                return f"Error: Tool '{tool_name}' timed out after {timeout}s"
            msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.exception("Tools: tool '%s' error: %s", tool_name, msg)
            return f"Error executing tool '{tool_name}': {msg}"

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_detail(self, name: str) -> dict[str, Any] | None:
        """Return full tool definition (name, description, input_schema) or None."""
        td = self._tools.get(name)
        if td is None:
            return None
        return {
            "name": td.name,
            "description": td.description,
            "input_schema": td.input_schema,
        }

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            logger.debug("Tools: unregistered tool: %s", name)

    def definitions(self, api_format: str = "openai", active_tools: set[str] | None = None) -> list[dict[str, Any]]:
        """Build tool definitions for API calls.

        Args:
            api_format: "openai", "openai-response", or "anthropic"
            active_tools: Set of tool names to include.
                         If None, all tools are included (backward compat).
                         If provided, only active tools are included in API definitions.
        """
        result = []
        for td in self._tools.values():
            if not td.enabled:
                continue
            if active_tools is not None and td.name not in active_tools:
                continue
            if api_format == "anthropic":
                defn: dict[str, Any] = {
                    "name": td.name,
                    "description": td.description,
                    "input_schema": td.input_schema,
                }
                result.append(defn)
            elif api_format == "openai-response":
                defn = {
                    "type": "function",
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.input_schema,
                }
                result.append(defn)
            else:  # openai-completions
                func: dict[str, Any] = {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.input_schema,
                }
                result.append({"type": "function", "function": func})
        return result

    def format_tools_summary(self, loaded: set[str] | None = None) -> str:
        """Format unloaded tools as a summary list for the system prompt."""
        loaded = loaded or set()
        lines = []
        for td in self._tools.values():
            if not td.enabled:
                continue
            if td.name in loaded:
                continue
            lines.append(f"- {td.name}: {td.description}")
        return "\n".join(lines)

    def discover_tools(self) -> None:
        """Auto-discover tools from .py files in this directory."""
        tools_dir = Path(__file__).parent
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "__init__.py":
                continue
            module_name = f"qd_evolve.tools.{py_file.stem}"
            try:
                importlib.import_module(module_name)
            except Exception as e:
                logger.exception("Tools: failed to load tool module %s", module_name)


# Module-level singleton
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _registry.discover_tools()
    return _registry
