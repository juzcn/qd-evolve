"""Toolbox — manage tool state: enabled / preload / disabled.

State is persisted to toolbox.json. Three states per item:
  - "enabled"  — on-demand loading (default)
  - "preload"  — full schema/definition in system prompt
  - "disabled" — hidden from LLM entirely

Bridges only support "enabled"/"disabled" (no preload concept).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TOOLBOX_PATH = Path("toolbox.json")

VALID_STATES = ("enabled", "preload", "disabled")
BRIDGE_VALID_STATES = ("enabled", "disabled")


def _load() -> dict[str, Any]:
    if TOOLBOX_PATH.is_file():
        return json.loads(TOOLBOX_PATH.read_text(encoding="utf-8"))
    return {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}


def _save(data: dict[str, Any]) -> None:
    TOOLBOX_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── state queries ──────────────────────────────────────────────

def get_state(section: str, name: str) -> str:
    """Return state for an item: 'enabled', 'preload', or 'disabled'."""
    data = _load()
    return data.get(section, {}).get(name, "enabled")


def get_disabled(section: str) -> set[str]:
    """Return names of disabled items in a section."""
    data = _load()
    return {k for k, v in data.get(section, {}).items() if v == "disabled"}


def get_preloaded(section: str) -> set[str]:
    """Return names of preloaded items in a section."""
    data = _load()
    return {k for k, v in data.get(section, {}).items() if v == "preload"}


# ── state mutations ────────────────────────────────────────────

def set_state(section: str, name: str, state: str) -> bool:
    """Set state for an item. Returns False if state is invalid."""
    if section in ("mcp_servers", "bridge"):
        if state not in BRIDGE_VALID_STATES:
            return False
    elif state not in VALID_STATES:
        return False

    data = _load()
    if section not in data:
        data[section] = {}
    if state == "enabled":
        data[section].pop(name, None)  # remove to keep file clean
    else:
        data[section][name] = state
    _save(data)
    return True


def toggle(section: str, name: str) -> str:
    """Cycle state. Returns the new state."""
    if section in ("mcp_servers", "bridge"):
        current = get_state(section, name)
        new_state = "disabled" if current != "disabled" else "enabled"
    else:
        # disabled → enabled → preload → disabled
        cycle = {"disabled": "enabled", "enabled": "preload", "preload": "disabled"}
        current = get_state(section, name)
        new_state = cycle[current]
    set_state(section, name, new_state)
    return new_state


# ── apply to registries ────────────────────────────────────────

def apply_to_tools(registry: Any, preload_tools_config: set[str]) -> None:
    """Apply toolbox state to ToolRegistry. Returns updated preload set."""
    for td in registry.list_tools():
        state = get_state("tools", td.name)
        if state == "disabled":
            td.enabled = False
        else:
            td.enabled = True
            if state == "preload":
                preload_tools_config.add(td.name)


def apply_to_cli_registry(registry: Any, preload_cli_config: set[str]) -> None:
    """Apply toolbox state to CLIRegistry. Returns updated preload set."""
    for tool in registry.list_tools():
        state = get_state("cli", tool.name)
        if state == "disabled":
            registry._disabled.add(tool.name)
        else:
            registry._disabled.discard(tool.name)
            if state == "preload":
                preload_cli_config.add(tool.name)


def apply_to_skill_registry(registry: Any, preload_skills_config: set[str]) -> None:
    """Apply toolbox state to SkillRegistry. Returns updated preload set."""
    for skill in registry.get_all_skills():
        state = get_state("skills", skill.name)
        if state == "disabled":
            registry._disabled.add(skill.name)
        else:
            registry._disabled.discard(skill.name)
            if state == "preload":
                preload_skills_config.add(skill.name)


def get_disabled_mcp_servers() -> set[str]:
    """Return names of disabled MCP servers. (legacy)"""
    return get_disabled("mcp_servers")


def get_disabled_bridges() -> set[str]:
    """Return keys of disabled bridges (format: 'type:name')."""
    # Returns entries from "bridge" section and legacy "mcp_servers" section
    disabled = get_disabled("bridge")
    # Also check legacy mcp_servers section
    for name in get_disabled("mcp_servers"):
        disabled.add(f"mcp:{name}")
    return disabled


# ── tool defaults ─────────────────────────────────────────────

def get_default(key: str, fallback: Any = None) -> Any:
    """Read a global default value from toolbox.json."""
    data = _load()
    return data.get("defaults", {}).get(key, fallback)


# ── display helpers ────────────────────────────────────────────

_STATE_MARK = {"enabled": "[✓]", "preload": "[P]", "disabled": "[✗]"}

def state_mark(state: str) -> str:
    return _STATE_MARK.get(state, "[?]")
