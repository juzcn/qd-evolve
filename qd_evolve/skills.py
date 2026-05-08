from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from qd_evolve.tools import get_registry


class SkillInfo:
    def __init__(self, path: Path, name: str, version: str = "0.0.0") -> None:
        self.path = path
        self.name = name
        self.version = version
        self.skill_md: str = ""


class SkillLoader:
    """Skill loader — discovers SKILL.md files and registers them as non-callable tools.

    Skills are prompt-only tools: the LLM sees the tool definition (with SKILL.md
    as the description) and follows the instructions within, using other callable
    tools to execute. The skill tool itself cannot be invoked.
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

    def register_tools(self) -> int:
        """Register all discovered skills as non-callable tools."""
        registry = get_registry()
        for skill in self._skills:
            registry.register(
                name=skill.name,
                description=skill.skill_md,
                input_schema={"type": "object", "additionalProperties": True},
                handler=None,
                category="skill",
                is_callable=False,
            )
            logger.info("Skill registered (non-callable): {}", skill.name)
        return len(self._skills)

    def get_system_prompt_addition(self) -> str:
        """Return all SKILL.md contents for injection into the system prompt."""
        if not self._skills:
            return ""
        parts = [s.skill_md for s in self._skills if s.skill_md]
        if not parts:
            return ""
        return "\n\n--- Skills ---\n" + "\n\n".join(parts)
