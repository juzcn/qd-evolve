"""Toolbox — manage tool state: enabled / preload / disabled.

All state is in config.json under agents_config.agents[].toolbox sections.
Every operation requires an agent_name.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from qd_evolve.core.config import CONFIG_PATH

VALID_STATES = ("enabled", "preload", "disabled")
BRIDGE_VALID_STATES = ("enabled", "disabled")

TOOLBOX_MIGRATION_PATH = Path("toolbox.json")

_EMPTY = {"tools": {}, "mcp_servers": {}, "bridge": {}, "cli": {}, "skills": {}}


def _load(agent_name: str) -> dict[str, Any]:
    if CONFIG_PATH.is_file():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        agents = data.get("agents_config", {}).get("agents", [])
        for agent in agents:
            if agent.get("name") == agent_name:
                return agent.get("toolbox", deepcopy(_EMPTY))
    return deepcopy(_EMPTY)


def _save(section_data: dict[str, Any], agent_name: str) -> None:
    if CONFIG_PATH.is_file():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        data = {"agents_config": {"agents": []}}

    agents = data.get("agents_config", {}).get("agents", [])
    found = False
    for i, agent in enumerate(agents):
        if agent.get("name") == agent_name:
            agents[i]["toolbox"] = section_data
            found = True
            break
    if not found:
        agents.append({"name": agent_name, "toolbox": section_data})
        data["agents_config"]["agents"] = agents

    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ── state queries ──────────────────────────────────────────────

def get_state(section: str, name: str, agent_name: str) -> str:
    data = _load(agent_name)
    return data.get(section, {}).get(name, "enabled")


def get_disabled(section: str, agent_name: str) -> set[str]:
    data = _load(agent_name)
    return {k for k, v in data.get(section, {}).items() if v == "disabled"}


def get_preloaded(section: str, agent_name: str) -> set[str]:
    data = _load(agent_name)
    return {k for k, v in data.get(section, {}).items() if v == "preload"}


# ── state mutations ────────────────────────────────────────────

def set_state(section: str, name: str, state: str, agent_name: str) -> bool:
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


def toggle(section: str, name: str, agent_name: str) -> str:
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

def apply_to_tools(registry: Any, preload_tools_config: set[str], agent_name: str) -> None:
    for td in registry.list_tools():
        state = get_state("tools", td.name, agent_name)
        if state == "disabled":
            td.enabled = False
        else:
            td.enabled = True
            if state == "preload":
                preload_tools_config.add(td.name)


def apply_to_cli_registry(registry: Any, preload_cli_config: set[str], agent_name: str) -> None:
    for tool in registry.list_tools():
        state = get_state("cli", tool.name, agent_name)
        if state == "disabled":
            registry._disabled.add(tool.name)
        else:
            registry._disabled.discard(tool.name)
            if state == "preload":
                preload_cli_config.add(tool.name)


def apply_to_skill_registry(registry: Any, preload_skills_config: set[str], agent_name: str) -> None:
    for skill in registry.get_all_skills():
        state = get_state("skills", skill.name, agent_name)
        if state == "disabled":
            registry._disabled.add(skill.name)
        else:
            registry._disabled.discard(skill.name)
            if state == "preload":
                preload_skills_config.add(skill.name)


def get_disabled_bridges(agent_name: str) -> set[str]:
    disabled = get_disabled("bridge", agent_name)
    for name in get_disabled("mcp_servers", agent_name):
        disabled.add(f"mcp:{name}")
    return disabled


# ── migration ──────────────────────────────────────────────────

def migrate_toolbox_to_config() -> None:
    """One-time migration: merge toolbox.json data into config.json."""
    toolbox_path = TOOLBOX_MIGRATION_PATH
    if not toolbox_path.is_file():
        return

    tb_data = json.loads(toolbox_path.read_text(encoding="utf-8"))
    if not CONFIG_PATH.is_file():
        return

    cfg_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    # Merge defaults → toolbox_defaults
    defaults = tb_data.get("defaults", {})
    if defaults:
        cfg_data.setdefault("toolbox_defaults", {})
        for key, value in defaults.items():
            cfg_data["toolbox_defaults"][key] = value

    # Merge per-agent toolbox data
    tb_agents = tb_data.get("agents", {})
    agents = cfg_data.get("agents_config", {}).get("agents", [])
    for agent in agents:
        agent_name = agent.get("name")
        if agent_name in tb_agents:
            agent["toolbox"] = tb_agents[agent_name]

    CONFIG_PATH.write_text(
        json.dumps(cfg_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    toolbox_path.rename(toolbox_path.with_suffix(".json.bak"))


# ── display helpers ────────────────────────────────────────────

_STATE_MARK = {"enabled": "[✓]", "preload": "[P]", "disabled": "[✗]"}

def state_mark(state: str) -> str:
    return _STATE_MARK.get(state, "[?]")
