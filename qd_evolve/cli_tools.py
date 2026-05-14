"""CLI tool registry —discovers and manages CLI tool definitions from yaml files."""


from pathlib import Path
from typing import Any

from qd_evolve.logger import logger
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

    def format_for_prompt(self, loaded: set[str] | None = None) -> str:
        """Format unloaded CLI tools as summary list for the system prompt."""
        if not self._tools:
            return ""
        loaded = loaded or set()
        lines = []
        for tool in self._tools.values():
            if tool.name in loaded or tool.name in self._disabled:
                continue
            line = f"- {tool.name}: {tool.description or tool.command}"
            if tool.examples:
                line += f" (e.g. {tool.examples[0]})"
            lines.append(line)
        return "\n".join(lines)
