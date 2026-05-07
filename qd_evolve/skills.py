from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from qd_evolve.tools import ToolRegistry, get_registry


class SkillInfo:
    def __init__(self, path: Path, name: str, version: str = "0.0.0") -> None:
        self.path = path
        self.name = name
        self.version = version
        self.skill_md: str = ""
        self.scripts: list[Path] = []


class SkillLoader:
    """Universal skill loader.

    Convention (from SKILL.md):
      python3 scripts/<name>.py '<JSON>'

    Scripts receive arguments as a JSON string in sys.argv[1].
    SKILL.md tells the LLM what parameters to pass.
    No manifest.json needed — tools are inferred from scripts/.
    """

    def __init__(self, skills_dir: str | Path, skill_config: dict[str, str] | None = None, registry: ToolRegistry | None = None) -> None:
        self._dir = Path(skills_dir)
        self._config = skill_config or {}
        self._registry = registry or get_registry()
        self._skills: list[SkillInfo] = []

    def discover(self) -> int:
        if not self._dir.is_dir():
            logger.warning("Skills directory not found: {}", self._dir)
            return 0

        count = 0
        for child in sorted(self._dir.iterdir()):
            if not child.is_dir():
                continue

            scripts_dir = child / "scripts"
            if not scripts_dir.is_dir() or not list(scripts_dir.glob("*.py")):
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

            info = SkillInfo(path=child, name=name, version=version)

            skill_md_path = child / "SKILL.md"
            if skill_md_path.exists():
                info.skill_md = skill_md_path.read_text(encoding="utf-8")

            info.scripts = sorted(scripts_dir.glob("*.py"))

            if info.scripts:
                self._skills.append(info)
                count += 1
                logger.info("Skill discovered: {} v{} ({} scripts)", info.name, info.version, len(info.scripts))

        return count

    def register_tools(self) -> int:
        total = 0
        for skill in self._skills:
            for script_path in skill.scripts:
                tool_name = f"{skill.name}__{script_path.stem}"
                description = skill.skill_md or f"[{skill.name}] Run {script_path.stem}"
                self._registry.register(
                    name=tool_name,
                    description=description,
                    input_schema={"type": "object", "additionalProperties": True},
                    handler=self._make_handler(script_path),
                    category="skill",
                )
                total += 1
                logger.info("Skill tool registered: {}", tool_name)
        return total

    def _make_handler(self, script_path: Path):
        config = self._config

        def handler(**kwargs: Any) -> str:
            env = dict(os.environ)
            env.update(config)

            try:
                args_json = json.dumps(kwargs, ensure_ascii=False)
                cmd = [sys.executable, str(script_path), args_json]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)

                output = result.stdout.strip()
                if result.returncode != 0:
                    err = result.stderr.strip()
                    return json.dumps({"error": err or f"Exit code {result.returncode}", "stdout": output}, ensure_ascii=False)
                return output or "(no output)"
            except subprocess.TimeoutExpired:
                return json.dumps({"error": "Skill script timed out"})
            except Exception as e:
                return json.dumps({"error": str(e)})

        return handler

    def get_system_prompt_addition(self) -> str:
        if not self._skills:
            return ""
        parts = [s.skill_md for s in self._skills if s.skill_md]
        if not parts:
            return ""
        return "\n\n--- Skills ---\n" + "\n\n".join(parts)
