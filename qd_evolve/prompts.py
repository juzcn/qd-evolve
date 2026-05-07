from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from qd_evolve.config import load_json, save_json

TEMPLATES_DIR = Path("templates")


class PromptTemplate(BaseModel):
    name: str
    system: str
    description: str = ""


class TemplateStore:
    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else TEMPLATES_DIR

    def _path(self, name: str) -> Path:
        return self.directory / f"{name}.json"

    def list_templates(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem for p in self.directory.glob("*.json"))

    def load(self, name: str) -> PromptTemplate:
        p = self._path(name)
        if not p.exists():
            raise FileNotFoundError(f"Template not found: {name}")
        data = load_json(p)
        return PromptTemplate.model_validate(data)

    def save(self, template: PromptTemplate) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        p = self._path(template.name)
        save_json(template.model_dump(), p)
        logger.info("Saved template: {}", template.name)

    def delete(self, name: str) -> None:
        p = self._path(name)
        if not p.exists():
            raise FileNotFoundError(f"Template not found: {name}")
        p.unlink()
        logger.info("Deleted template: {}", name)
