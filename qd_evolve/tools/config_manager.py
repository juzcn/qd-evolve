"""Config manager tool — agent self-configuration (provider/model/description).

Also provides agent context primitives used by sub_agent_manager and Agent.run().
"""

from __future__ import annotations

import contextvars
from typing import Any

from qd_evolve.core.logger import logger

# ── Agent contexts — set by create_agent(), keyed by name ──────────

_agent_contexts: dict[str, tuple[Any, Any]] = {}  # name → (agent, settings)

# ContextVar auto-inherits to child threads (e.g., ToolRegistry.call() spawn).
# Unlike threading.local, ContextVar copies to Thread.start() children.
_current_agent_var: contextvars.ContextVar[str] = contextvars.ContextVar("current_agent", default="")


def set_agent_context(name: str, agent: Any, settings: Any) -> None:
    """Register an agent's context. Called by create_agent() in loader.py."""
    _agent_contexts[name] = (agent, settings)


def _require_context() -> tuple[str, Any, Any]:
    name = _current_agent_var.get()
    if not name or name not in _agent_contexts:
        raise RuntimeError(f"config_manager: no agent context — current='{name}', known={list(_agent_contexts.keys())}")
    agent, settings = _agent_contexts[name]
    return name, agent, settings



# ── Handlers ──────────────────────────────────────────────────────


def _list_providers() -> str:
    """List available LLM providers and their models."""
    _, _, settings = _require_context()
    lines: list[str] = []
    for p in settings.providers:
        models = [m.name for m in p.models]
        mark = " [default]" if p.name == settings.default_provider else ""
        lines.append(f"- {p.name}{mark}: {', '.join(models)}")
    return "\n".join(lines) if lines else "No providers configured."


def _get_my_config() -> str:
    """Show the current agent's configuration."""
    name, agent, settings = _require_context()

    entry = None
    for a in settings.agents_config.agents:
        if a.name == name:
            entry = a
            break

    if entry is None:
        return f"Error: agent '{name}' not found in config"

    provider = agent._provider_name or settings.default_provider
    model = agent._model or settings.default_model

    return (
        f"name: {name}\n"
        f"description: {entry.description}\n"
        f"provider: {provider}\n"
        f"model: {model}"
    )


def _update_my_config(
    provider: str = "",
    model: str = "",
    description: str = "",
) -> str:
    """Update the current agent's provider, model, or description.

    Only specified fields are changed. Provider/model are validated against
    the configured providers list. Changes are persisted to config.json and
    applied to the running agent immediately.
    """
    from qd_evolve.core.config import load_settings, save_settings

    name, agent, _ = _require_context()
    settings = load_settings()

    entry = None
    for a in settings.agents_config.agents:
        if a.name == name:
            entry = a
            break

    if entry is None:
        return f"Error: agent '{name}' not found in config"

    changes: list[str] = []

    # ── Provider ──
    if provider:
        p = settings.get_provider(provider)
        if p is None:
            available = ", ".join(p.name for p in settings.providers)
            return f"Error: provider '{provider}' not found. Available: {available}"
        entry.provider = provider
        agent._provider_name = provider
        changes.append(f"provider: → {provider}")

    # ── Model ──
    if model:
        prov_name = provider or entry.effective_provider(settings)
        prov = settings.get_provider(prov_name)
        if prov is None:
            return f"Error: provider '{prov_name}' not found"
        model_names = [m.name for m in prov.models]
        if model not in model_names:
            return f"Error: model '{model}' not found in provider '{prov_name}'. Available: {', '.join(model_names)}"
        entry.model = model
        agent._model = model
        changes.append(f"model: → {model}")

    # ── Description ──
    if description:
        entry.description = description
        changes.append(f"description: → {description}")

    if not changes:
        return "No changes made."

    save_settings(settings)
    return f"Updated config for '{name}':\n" + "\n".join(f"  {c}" for c in changes)

# ── Register tools ────────────────────────────────────────────────

from qd_evolve.tools import get_registry

_registry = get_registry()

_registry.register(
    name="list_providers",
    description="List available LLM providers and their models.",
    handler=_list_providers,
    input_schema={"type": "object", "properties": {}, "required": []},
)

_registry.register(
    name="get_my_config",
    description="Show the current agent's configuration (name, description, provider, model).",
    handler=_get_my_config,
    input_schema={"type": "object", "properties": {}, "required": []},
)

_registry.register(
    name="update_my_config",
    description="Update the current agent's provider, model, or description. Only specify fields to change. Validates provider/model against available options.",
    handler=_update_my_config,
    input_schema={
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "description": "Provider name to switch to (empty = no change). Use list_providers to see options.",
            },
            "model": {
                "type": "string",
                "description": "Model name to switch to (empty = no change). Must belong to the provider.",
            },
            "description": {
                "type": "string",
                "description": "New description for this agent (empty = no change).",
            },
        },
        "required": [],
    },
)

