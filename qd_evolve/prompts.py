from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateNotFound
from qd_evolve.logger import logger

TEMPLATES_DIR = Path("templates")


class _CombinedLoader(BaseLoader):
    """Load templates from a primary dir, falling back to a secondary dir."""

    def __init__(self, primary: Path, fallback: Path | None = None) -> None:
        self._primary = primary
        self._fallback = fallback

    def get_source(self, environment: Environment, template: str) -> tuple[str, str, callable]:
        for d in [self._primary, self._fallback] if self._fallback else [self._primary]:
            if d is None:
                continue
            p = d / template
            if p.is_file():
                source = p.read_text(encoding="utf-8")
                return source, str(p), lambda: p.stat().st_mtime == p.stat().st_mtime
        raise TemplateNotFound(template)


class PromptTemplateManager:
    """Jinja2-based prompt template management.

    Templates are ``.j2`` files in the ``templates/`` directory.
    Use ``{{ variable }}`` for interpolation and ``{% if ... %}`` for logic.
    """

    def __init__(self, templates_dir: str | Path | None = None) -> None:
        self._dir = Path(templates_dir) if templates_dir else TEMPLATES_DIR
        builtin_dir = Path(__file__).parent / "_templates"
        loader = _CombinedLoader(self._dir, builtin_dir) if self._dir.is_dir() else _CombinedLoader(builtin_dir)
        self._env = Environment(
            loader=loader,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, name: str, **context: Any) -> str:
        """Render a named template with the given context variables."""
        template = self._env.get_template(f"{name}.j2")
        ctx = self._default_context()
        ctx.update(context)
        result = template.render(**ctx)
        logger.debug("Rendered template '%s' (%s chars)", name, len(result))
        return result

    @staticmethod
    def _default_context() -> dict[str, Any]:
        return {"date": date.today().isoformat()}
