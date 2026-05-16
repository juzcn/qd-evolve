"""Toolbox — manage tool state: enabled / preload / disabled.

State is persisted to toolbox.json. Three states per item:
  - "enabled"  — on-demand loading (default)
  - "preload"  — full schema/definition in system prompt
  - "disabled" — hidden from LLM entirely

Bridges only support "enabled"/"disabled" (no preload concept).
"""

import json
from pathlib import Path
from typing import Any

TOOLBOX_PATH = Path("toolbox.json")

VALID_STATES = ("enabled", "preload", "disabled")
BRIDGE_VALID_STATES = ("enabled", "disabled")


def _toolbox_path(agent_name: str | None = None) -> Path:
    """Return toolbox.json path. Per-agent if agent_name given, else global."""
    if agent_name:
        return Path("agents") / agent_name / "toolbox.json"
    return TOOLBOX_PATH


def _load(agent_name: str | None = None) -> dict[str, Any]:
    path = _toolbox_path(agent_name)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}


def _save(data: dict[str, Any], agent_name: str | None = None) -> None:
    _toolbox_path(agent_name).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ── state queries ──────────────────────────────────────────────

def get_state(section: str, name: str, agent_name: str | None = None) -> str:
    """Return state for an item: 'enabled', 'preload', or 'disabled'."""
    data = _load(agent_name)
    return data.get(section, {}).get(name, "enabled")


def get_disabled(section: str, agent_name: str | None = None) -> set[str]:
    """Return names of disabled items in a section."""
    data = _load(agent_name)
    return {k for k, v in data.get(section, {}).items() if v == "disabled"}


def get_preloaded(section: str, agent_name: str | None = None) -> set[str]:
    """Return names of preloaded items in a section."""
    data = _load(agent_name)
    return {k for k, v in data.get(section, {}).items() if v == "preload"}


# ── state mutations ────────────────────────────────────────────

def set_state(section: str, name: str, state: str, agent_name: str | None = None) -> bool:
    """Set state for an item. Returns False if state is invalid."""
    if section in ("mcp_servers", "bridge"):
        if state not in BRIDGE_VALID_STATES:
            return False
    elif state not in VALID_STATES:
        return False

    data = _load(agent_name)
    if section not in data:
        data[section] = {}
    if state == "enabled":
        data[section].pop(name, None)  # remove to keep file clean
    else:
        data[section][name] = state
    _save(data, agent_name)
    return True


def toggle(section: str, name: str, agent_name: str | None = None) -> str:
    """Cycle state. Returns the new state."""
    if section in ("mcp_servers", "bridge"):
        current = get_state(section, name, agent_name)
        new_state = "disabled" if current != "disabled" else "enabled"
    else:
        # disabled → enabled → preload → disabled
        cycle = {"disabled": "enabled", "enabled": "preload", "preload": "disabled"}
        current = get_state(section, name, agent_name)
        new_state = cycle[current]
    set_state(section, name, new_state, agent_name)
    return new_state


# ── apply to registries ────────────────────────────────────────

def apply_to_tools(registry: Any, preload_tools_config: set[str], agent_name: str | None = None) -> None:
    """Apply toolbox state to ToolRegistry. Returns updated preload set."""
    for td in registry.list_tools():
        state = get_state("tools", td.name, agent_name)
        if state == "disabled":
            td.enabled = False
        else:
            td.enabled = True
            if state == "preload":
                preload_tools_config.add(td.name)


def apply_to_cli_registry(registry: Any, preload_cli_config: set[str], agent_name: str | None = None) -> None:
    """Apply toolbox state to CLIRegistry. Returns updated preload set."""
    for tool in registry.list_tools():
        state = get_state("cli", tool.name, agent_name)
        if state == "disabled":
            registry._disabled.add(tool.name)
        else:
            registry._disabled.discard(tool.name)
            if state == "preload":
                preload_cli_config.add(tool.name)


def apply_to_skill_registry(registry: Any, preload_skills_config: set[str], agent_name: str | None = None) -> None:
    """Apply toolbox state to SkillRegistry. Returns updated preload set."""
    for skill in registry.get_all_skills():
        state = get_state("skills", skill.name, agent_name)
        if state == "disabled":
            registry._disabled.add(skill.name)
        else:
            registry._disabled.discard(skill.name)
            if state == "preload":
                preload_skills_config.add(skill.name)


def get_disabled_bridges(agent_name: str | None = None) -> set[str]:
    """Return keys of disabled bridges (format: 'type:name')."""
    disabled = get_disabled("bridge", agent_name)
    for name in get_disabled("mcp_servers", agent_name):
        disabled.add(f"mcp:{name}")
    return disabled


# ── tool defaults ─────────────────────────────────────────────

def get_default(key: str, fallback: Any = None, agent_name: str | None = None) -> Any:
    """Read a global default value from toolbox.json."""
    data = _load(agent_name)
    return data.get("defaults", {}).get(key, fallback)


# ── display helpers ────────────────────────────────────────────

_STATE_MARK = {"enabled": "[✓]", "preload": "[P]", "disabled": "[✗]"}

def state_mark(state: str) -> str:
    return _STATE_MARK.get(state, "[?]")
