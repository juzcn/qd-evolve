"""CLI tool registry — discovers and manages CLI tool definitions from yaml files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
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

    def discover(self, cli_dir: str | Path) -> None:
        self._cli_dir = Path(cli_dir)
        self._load()

    def reload(self) -> None:
        """Re-scan yaml files. Called after a new CLI tool is registered."""
        self._load()

    def _load(self) -> None:
        self._tools.clear()
        if self._cli_dir is None or not self._cli_dir.is_dir():
            logger.debug("CLI tools dir {} not found, skipping", self._cli_dir)
            return

        for yaml_file in sorted(self._cli_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or "name" not in data:
                    logger.warning("CLI: invalid yaml {}", yaml_file.name)
                    continue
                tool = CLIToolDef.model_validate(data)
                self._tools[tool.name] = tool
                logger.debug("CLI: discovered tool {}", tool.name)
            except Exception as e:
                logger.error("CLI: failed to load {}: {}", yaml_file.name, e)

    def get_detail(self, name: str) -> dict[str, Any] | None:
        tool = self._tools.get(name)
        if tool is None:
            return None
        return tool.model_dump()

    def list_tools(self) -> list[CLIToolDef]:
        return list(self._tools.values())

    def format_for_prompt(self, active_cli_tools: list[str] | None = None) -> str:
        active = set(active_cli_tools or [])
        if not active:
            return ""
        lines = []
        for tool in self._tools.values():
            if tool.name not in active:
                continue
            line = f"- {tool.name}: {tool.description or tool.command}"
            if tool.examples:
                line += f" (e.g. {tool.examples[0]})"
            lines.append(line)
        return "\n".join(lines)
