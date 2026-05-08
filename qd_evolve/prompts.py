from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateNotFound
from loguru import logger

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

    def list_templates(self) -> list[str]:
        """Return available template names (without .j2 extension)."""
        names: list[str] = []
        for d in [self._dir] if self._dir.is_dir() else []:
            for f in sorted(d.glob("*.j2")):
                names.append(f.stem)
        return names

    def render(self, name: str, **context: Any) -> str:
        """Render a named template with the given context variables."""
        template = self._env.get_template(f"{name}.j2")
        ctx = self._default_context()
        ctx.update(context)
        result = template.render(**ctx)
        logger.debug("Rendered template '{}' ({} chars)", name, len(result))
        return result

    def render_string(self, template_str: str, **context: Any) -> str:
        """Render a Jinja2 template string directly."""
        template = self._env.from_string(template_str)
        ctx = self._default_context()
        ctx.update(context)
        return template.render(**ctx)

    def load(self, name: str) -> str | None:
        """Return the raw template source for a named template."""
        try:
            source, _, _ = self._env.loader.get_source(self._env, f"{name}.j2")
            return source
        except TemplateNotFound:
            return None

    def save(self, name: str, content: str) -> Path:
        """Save a template string as a .j2 file."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{name}.j2"
        path.write_text(content, encoding="utf-8")
        logger.info("Saved template '{}' to {}", name, path)
        return path

    def delete(self, name: str) -> bool:
        """Delete a template file. Returns True if deleted."""
        path = self._dir / f"{name}.j2"
        if path.is_file():
            path.unlink()
            logger.info("Deleted template '{}'", name)
            return True
        return False

    @staticmethod
    def _default_context() -> dict[str, Any]:
        return {"date": date.today().isoformat()}
