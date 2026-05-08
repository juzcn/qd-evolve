from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class SkillInfo:
    def __init__(self, path: Path, name: str, version: str = "0.0.0") -> None:
        self.path = path
        self.name = name
        self.version = version
        self.skill_md: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "path": str(self.path)}


class SkillRegistry:
    """Discovers SKILL.md files and injects them into the system prompt.

    Skills are NOT tool calls — the LLM reads SKILL.md instructions and
    uses other callable tools (e.g. run_shell) to execute them.
    """

    def __init__(self, skills_dir: str | Path) -> None:
        self._dir = Path(skills_dir)
        self._skills: list[SkillInfo] = []

    def discover(self) -> int:
        if not self._dir.is_dir():
            logger.warning("Skills directory not found: {}", self._dir)
            return 0

        count = 0
        for child in sorted(self._dir.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue

            skill_md_path = child / "SKILL.md"
            if not skill_md_path.is_file():
                continue

            name, version = child.name, "0.0.0"
            meta_path = child / "_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    name = meta.get("slug", name)
                    version = meta.get("version", version)
                except Exception:
                    pass

            if name == child.name:
                name = self._parse_frontmatter_name(skill_md_path) or name

            info = SkillInfo(path=child, name=name, version=version)
            info.skill_md = skill_md_path.read_text(encoding="utf-8")
            self._skills.append(info)
            count += 1
            logger.info("Skill discovered: {} v{}", info.name, info.version)

        return count

    def list_skills(self) -> list[SkillInfo]:
        return list(self._skills)

    def format_for_prompt(self) -> str:
        """Return skill descriptions (without frontmatter) for system prompt injection."""
        if not self._skills:
            return ""
        parts = [self._strip_frontmatter(s.skill_md) for s in self._skills if s.skill_md]
        return "\n\n".join(parts) if parts else ""

    def get_system_prompt_addition(self) -> str:
        """Return all SKILL.md contents (without frontmatter) for injection into the system prompt."""
        if not self._skills:
            return ""
        parts = [self._strip_frontmatter(s.skill_md) for s in self._skills if s.skill_md]
        if not parts:
            return ""
        return "\n\n--- Skills ---\n" + "\n\n".join(parts)

    @staticmethod
    def _parse_frontmatter_name(skill_md_path: Path) -> str | None:
        try:
            text = skill_md_path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                return None
            end = text.find("---", 3)
            if end < 0:
                return None
            for line in text[3:end].splitlines():
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return None

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        """Remove YAML frontmatter (--- ... ---) from SKILL.md content."""
        if not text.startswith("---"):
            return text
        end = text.find("---", 3)
        if end < 0:
            return text
        return text[end + 3:].lstrip("\n")
