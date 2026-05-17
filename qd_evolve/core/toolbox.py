"""Toolbox — manage tool state: enabled / preload / disabled.

All state is in toolbox.json under agents.<name> sections.
Every operation requires an agent_name (defaults to "default").

Layout:
{
  "agents": {
    "default": {
      "tools": {"fetch": "disabled", ...},
      "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {},
      "defaults": {"timeout": 60}
    },
    "test": {
      "tools": {}, ...
    }
  }
}
"""

import json
from pathlib import Path
from typing import Any

TOOLBOX_PATH = Path("toolbox.json")

VALID_STATES = ("enabled", "preload", "disabled")
BRIDGE_VALID_STATES = ("enabled", "disabled")

_EMPTY = {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}


def _load(agent_name: str | None = None) -> dict[str, Any]:
    name = agent_name or "default"
    if TOOLBOX_PATH.is_file():
        data = json.loads(TOOLBOX_PATH.read_text(encoding="utf-8"))
        return data.get("agents", {}).get(name, dict(_EMPTY))
    return dict(_EMPTY)


def _save(section_data: dict[str, Any], agent_name: str | None = None) -> None:
    name = agent_name or "default"
    if TOOLBOX_PATH.is_file():
        data = json.loads(TOOLBOX_PATH.read_text(encoding="utf-8"))
    else:
        data = {"agents": {}}
    if "agents" not in data:
        data["agents"] = {}
    data["agents"][name] = section_data
    TOOLBOX_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ── state queries ──────────────────────────────────────────────

def get_state(section: str, name: str, agent_name: str | None = None) -> str:
    data = _load(agent_name)
    return data.get(section, {}).get(name, "enabled")


def get_disabled(section: str, agent_name: str | None = None) -> set[str]:
    data = _load(agent_name)
    return {k for k, v in data.get(section, {}).items() if v == "disabled"}


def get_preloaded(section: str, agent_name: str | None = None) -> set[str]:
    data = _load(agent_name)
    return {k for k, v in data.get(section, {}).items() if v == "preload"}


# ── state mutations ────────────────────────────────────────────

def set_state(section: str, name: str, state: str, agent_name: str | None = None) -> bool:
    if section in ("mcp_servers", "bridge"):
        if state not in BRIDGE_VALID_STATES:
            return False
    elif state not in VALID_STATES:
        return False

    data = _load(agent_name)
    if section not in data:
        data[section] = {}
    if state == "enabled":
        data[section].pop(name, None)
    else:
        data[section][name] = state
    _save(data, agent_name)
    return True


def toggle(section: str, name: str, agent_name: str | None = None) -> str:
    if section in ("mcp_servers", "bridge"):
        current = get_state(section, name, agent_name)
        new_state = "disabled" if current != "disabled" else "enabled"
    else:
        cycle = {"disabled": "enabled", "enabled": "preload", "preload": "disabled"}
        current = get_state(section, name, agent_name)
        new_state = cycle[current]
    set_state(section, name, new_state, agent_name)
    return new_state


# ── apply to registries ────────────────────────────────────────

def apply_to_tools(registry: Any, preload_tools_config: set[str], agent_name: str | None = None) -> None:
    for td in registry.list_tools():
        state = get_state("tools", td.name, agent_name)
        if state == "disabled":
            td.enabled = False
        else:
            td.enabled = True
            if state == "preload":
                preload_tools_config.add(td.name)


def apply_to_cli_registry(registry: Any, preload_cli_config: set[str], agent_name: str | None = None) -> None:
    for tool in registry.list_tools():
        state = get_state("cli", tool.name, agent_name)
        if state == "disabled":
            registry._disabled.add(tool.name)
        else:
            registry._disabled.discard(tool.name)
            if state == "preload":
                preload_cli_config.add(tool.name)


def apply_to_skill_registry(registry: Any, preload_skills_config: set[str], agent_name: str | None = None) -> None:
    for skill in registry.get_all_skills():
        state = get_state("skills", skill.name, agent_name)
        if state == "disabled":
            registry._disabled.add(skill.name)
        else:
            registry._disabled.discard(skill.name)
            if state == "preload":
                preload_skills_config.add(skill.name)


def get_disabled_bridges(agent_name: str | None = None) -> set[str]:
    disabled = get_disabled("bridge", agent_name)
    for name in get_disabled("mcp_servers", agent_name):
        disabled.add(f"mcp:{name}")
    return disabled


# ── tool defaults ─────────────────────────────────────────────

def get_default(key: str, fallback: Any = None) -> Any:
    """Read a global default value from toolbox.json (not per-agent)."""
    if TOOLBOX_PATH.is_file():
        data = json.loads(TOOLBOX_PATH.read_text(encoding="utf-8"))
        return data.get("defaults", {}).get(key, fallback)
    return fallback


# ── display helpers ────────────────────────────────────────────

_STATE_MARK = {"enabled": "[✓]", "preload": "[P]", "disabled": "[✗]"}

def state_mark(state: str) -> str:
    return _STATE_MARK.get(state, "[?]")