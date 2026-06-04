"""CLI tool registry —discovers and manages CLI tool definitions from yaml files."""


import json
from pathlib import Path
from typing import Any

from qd_evolve.core.logger import logger
from pydantic import BaseModel
import yaml


class CLIToolDef(BaseModel):
    name: str
    command: str
    description: str = ""
    help_summary: str = ""
    examples: list[str] = []


class CLIRegistry:
    """Discovers CLI tool definitions from tools/cli/*.yaml files."""

    def __init__(self) -> None:
        self._tools: dict[str, CLIToolDef] = {}
        self._cli_dir: Path | None = None
        self._disabled: set[str] = set()

    def discover(self, cli_dir: str | Path) -> None:
        self._cli_dir = Path(cli_dir)
        self._load()

    def reload(self) -> None:
        """Re-scan yaml files. Called after a new CLI tool is registered."""
        self._load()

    def _load(self) -> None:
        self._tools.clear()
        if self._cli_dir is None or not self._cli_dir.is_dir():
            logger.debug("CLI: tools dir %s not found, skipping", self._cli_dir)
            return

        for yaml_file in sorted(self._cli_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "name" not in data:
                    logger.warning("CLI: invalid yaml %s", yaml_file.name)
                    continue
                tool = CLIToolDef.model_validate(data)
                self._tools[tool.name] = tool
                logger.debug("CLI: discovered tool %s", tool.name)
            except Exception as e:
                logger.error("CLI: failed to load %s: %s", yaml_file.name, e)

    def get_detail(self, name: str) -> dict[str, Any] | None:
        if name in self._disabled:
            return None
        tool = self._tools.get(name)
        if tool is None:
            return None
        return tool.model_dump()

    def list_tools(self) -> list[CLIToolDef]:
        return [t for t in self._tools.values() if t.name not in self._disabled]

    def format_for_prompt(self, preloaded: set[str] | None = None, loaded: set[str] | None = None) -> str:
        """Format all CLI tools with status tags for the system prompt.

        Tags: [preloaded] (startup), [loaded] (runtime), [unloaded].
        All items are one-liner summaries — preloaded full detail is rendered
        separately in the Preloaded CLI Details appendix.
        Items are sorted by status (preloaded → loaded → unloaded), then alphabetically.
        """
        if not self._tools:
            return ""
        preloaded = preloaded or set()
        loaded = loaded or set()

        def _sort_key(tool: CLIToolDef) -> tuple[int, str]:
            if tool.name in preloaded:
                return (0, tool.name)
            if tool.name in loaded:
                return (1, tool.name)
            return (2, tool.name)

        lines = []
        for tool in sorted(self._tools.values(), key=_sort_key):
            if tool.name in self._disabled:
                continue
            if tool.name in preloaded:
                lines.append(f"- [preloaded] {tool.name}: {tool.description or tool.command}")
            elif tool.name in loaded:
                line = f"- [loaded] {tool.name}: {tool.description or tool.command}"
                if tool.examples:
                    line += f" (e.g. {tool.examples[0]})"
                lines.append(line)
            else:
                line = f"- [unloaded] {tool.name}: {tool.description or tool.command}"
                if tool.examples:
                    line += f" (e.g. {tool.examples[0]})"
                lines.append(line)
        return "\n".join(lines)
