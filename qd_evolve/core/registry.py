"""Tool registry — discovers, registers, and manages callable tools."""

import importlib
import importlib.util
import sys as _sys
from pathlib import Path
from typing import Any, Callable

from qd_evolve.core.logger import logger
from pydantic import BaseModel

# Ensure CWD is on sys.path so user tools/ and skills/ are importable.
# Needed when running via pip entry point which may strip CWD from path.
_cwd = str(Path.cwd())
if _cwd not in _sys.path:
    _sys.path.insert(0, _cwd)


class ToolDef(BaseModel):
    name: str
    description: str
    handler: Callable[..., str]
    input_schema: dict[str, Any] = {}
    enabled: bool = True


# source_name → bridge_type ("oat" | "mcp"). Bridges populate this when
# they register tools so format_tools_summary() can group them correctly.
_source_bridge_types: dict[str, str] = {}


def set_source_bridge_type(source_name: str, bridge_type: str) -> None:
    """Called by bridges to declare which type a tool source belongs to."""
    _source_bridge_types[source_name] = bridge_type


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

        from qd_evolve.core.config import DEFAULT_TOOL_TIMEOUT, REGISTRY_TIMEOUT_BUFFER
        import contextvars
        import threading

        tool_timeout = kwargs.get("timeout", None)
        registry_timeout = (tool_timeout + REGISTRY_TIMEOUT_BUFFER) if tool_timeout else DEFAULT_TOOL_TIMEOUT

        result: dict = {}
        def _target() -> None:
            try:
                result["value"] = td.handler(**kwargs)
            except Exception as e:
                result["error"] = e

        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(_target), daemon=True)
        try:
            t.start()
            t.join(timeout=registry_timeout)
        except RuntimeError:
            return f"Error: Tool '{tool_name}' unavailable — interpreter is shutting down"

        if t.is_alive():
            return f"Error: Tool '{tool_name}' timed out after {registry_timeout}s"

        if "error" in result:
            e = result["error"]
            if isinstance(e, ImportError):
                raise e
            import concurrent.futures
            if isinstance(e, concurrent.futures.TimeoutError):
                return f"Error: Tool '{tool_name}' timed out after {registry_timeout}s"
            msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.error("Tools: tool '%s' error: %s", tool_name, msg)
            return f"Error executing tool '{tool_name}': {msg}"

        return result.get("value", "")

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

    def format_tools_summary(self, preloaded: set[str] | None = None, loaded: set[str] | None = None) -> str:
        """Format all func tools grouped by source with status tags.

        Builtins (no source tag) first, then MCP services, then OAT sources.
        Each line is tagged [preloaded], [loaded], or [unloaded].
        Within each group, items are sorted by status (preloaded → loaded →
        unloaded), then alphabetically.
        """
        import re

        preloaded = preloaded or set()
        loaded = loaded or set()

        def _tag(name: str) -> str:
            if name in preloaded:
                return "[preloaded]"
            if name in loaded:
                return "[loaded]"
            return "[unloaded]"

        def _status_sort_key(item: tuple[str, str, str]) -> tuple[int, str]:
            """Sort by status tag order, then name."""
            tag, name = item[2], item[0]
            if tag == "[preloaded]":
                return (0, name)
            if tag == "[loaded]":
                return (1, name)
            return (2, name)

        _src_pat = re.compile(r"^\[([^\]]+)\]\s*")

        builtins: list[tuple[str, str, str]] = []
        # source_name → list of (name, desc, tag)
        oat_sources: dict[str, list[tuple[str, str, str]]] = {}
        mcp_sources: dict[str, list[tuple[str, str, str]]] = {}

        for td in self._tools.values():
            if not td.enabled:
                continue
            tag = _tag(td.name)
            m = _src_pat.match(td.description)
            if m:
                src = m.group(1)
                desc = td.description[m.end():].strip()
                bridge_type = _source_bridge_types.get(src, "mcp")
                if bridge_type == "oat":
                    oat_sources.setdefault(src, []).append((td.name, desc, tag))
                else:
                    mcp_sources.setdefault(src, []).append((td.name, desc, tag))
            else:
                builtins.append((td.name, td.description, tag))

        sections: list[str] = []

        if builtins:
            builtins.sort(key=_status_sort_key)
            sections.append(f"### Builtins ({len(builtins)} tools)")
            for name, desc, tag in builtins:
                sections.append(f"- {tag} {name}: {desc}")

        if mcp_sources:
            if sections:
                sections.append("")
            sections.append(f"### MCP Services ({sum(len(t) for t in mcp_sources.values())} tools)")
            for src in sorted(mcp_sources):
                tools = mcp_sources[src]
                tools.sort(key=_status_sort_key)
                sections.append("")
                sections.append(f"#### {src} ({len(tools)} tools)")
                for name, desc, tag in tools:
                    sections.append(f"- {tag} {name}: {desc}")

        for src in sorted(oat_sources):
            tools = oat_sources[src]
            tools.sort(key=_status_sort_key)
            if sections:
                sections.append("")
            sections.append(f"### {src} ({len(tools)} tools)")
            for name, desc, tag in tools:
                sections.append(f"- {tag} {name}: {desc}")

        return "\n".join(sections)

    def discover_tools(self) -> None:
        """Auto-discover tools from qd_evolve/tools/ (system) and tools/func/ (user)."""
        # System tools — bundled with qd_evolve
        tools_dir = Path(__file__).resolve().parent.parent / "tools"
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "__init__.py":
                continue
            module_name = f"qd_evolve.tools.{py_file.stem}"
            try:
                importlib.import_module(module_name)
            except Exception:
                logger.exception("Tools: failed to load tool module %s", module_name)

        # User func tools — tools/func/*.py (add/delete files to add/remove tools)
        from qd_evolve.core.config import FUNC_TOOLS_DIR
        func_dir = Path(FUNC_TOOLS_DIR)
        if func_dir.is_dir():
            for py_file in sorted(func_dir.glob("*.py")):
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"tools.func.{py_file.stem}", py_file,
                    )
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        logger.debug("Tools: loaded func tool: %s", py_file.stem)
                except Exception:
                    logger.exception("Tools: failed to load func tool %s", py_file.name)

# Module-level singleton
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _registry.discover_tools()
    return _registry


def decode_output(data: bytes, fallback_enc: str) -> str:
    """Decode subprocess output bytes, trying multiple encodings.

    Tries UTF-8 first (modern tools), then the locale encoding (cmd.exe output),
    then common legacy encodings for the platform. Uses replacement errors only
    as a last resort — never on the first attempts, to avoid garbled text.
    """
    if not data:
        return ""

    # Build an ordered list of encodings to try (no duplicates).
    # Encoding x fails with UnicodeDecodeError → next encoding.
    # Encoding x fails with LookupError → skip encoding.
    candidates: list[str] = ["utf-8"]
    if fallback_enc and fallback_enc.lower() not in ("utf-8", "cp65001"):
        candidates.append(fallback_enc)

    # Common legacy encodings — ordered by likelihood on CJK Windows systems.
    # These handle cmd.exe output when the locale encoding is also UTF-8.
    import platform
    if platform.system() == "Windows":
        candidates.extend(["gbk", "gb2312", "gb18030", "shift_jis", "euc_kr"])
    candidates.extend(["cp1252", "latin-1"])

    for enc in candidates:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # Absolute last resort: UTF-8 with replacement characters.
    return data.decode("utf-8", errors="replace")