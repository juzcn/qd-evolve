"""Bridge Protocol — generic mechanism for integrating external tool sources.

A bridge is a named spec with three functions:
    discover(settings) -> list[Config]
    connect(config, registry) -> Bridge
    disconnect(bridge, registry) -> None

Bridges self-register via BridgeManager.register(). cli.py only talks to
BridgeManager — adding a new bridge never touches cli.py or toolbox.py.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from qd_evolve.logger import logger

BRIDGE_DIR = Path(__file__).parent


# ── Protocol ─────────────────────────────────────────────────────

class Bridge(Protocol):
    """Each bridge instance holds its config and tracks registered tool names."""

    config: Any
    tool_names: list[str]

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...


# ── BridgeManager ─────────────────────────────────────────────────

DiscoverFn = Callable[[Any], list[Any]]
ConnectFn = Callable[[list[Any]], list[Bridge]]
DisconnectFn = Callable[[list[Bridge]], None]


@dataclass
class BridgeSpec:
    name: str
    discover: DiscoverFn
    connect: ConnectFn | None = None
    disconnect: DisconnectFn | None = None


@dataclass
class BridgeEntry:
    """Lightweight summary for toolbox listing."""
    bridge_type: str
    name: str
    description: str = ""
    tool_names: list[str] = field(default_factory=list)


class _BridgeManager:
    _types: dict[str, BridgeSpec] = {}
    _loaded: bool = False

    @classmethod
    def register(cls, name: str, discover: DiscoverFn,
                 connect: ConnectFn, disconnect: DisconnectFn) -> None:
        cls._types[name] = BridgeSpec(
            name=name, discover=discover,
            connect=connect, disconnect=disconnect,
        )
        logger.debug("BridgeManager: registered bridge type '%s'", name)

    @classmethod
    def _ensure_loaded(cls) -> None:
        """Discover bridge modules in tools/bridge/_*.py."""
        if cls._loaded:
            return
        for py_file in sorted(BRIDGE_DIR.glob("_*.py")):
            mod_name = py_file.stem  # _mcp, _oat
            importlib.import_module(f"tools.bridge.{mod_name}")
            logger.debug("BridgeManager: loaded module %s", mod_name)
        cls._loaded = True

    @classmethod
    def connect_all(cls, settings: Any) -> list[Bridge]:
        """Discover and connect all registered bridge types. Returns flat list."""
        cls._ensure_loaded()
        from qd_evolve.toolbox import get_disabled_bridges

        disabled = get_disabled_bridges()
        all_bridges: list[Bridge] = []

        for bt_name, spec in cls._types.items():
            try:
                configs = spec.discover(settings)
                # Filter disabled
                enabled_configs = [c for c in configs
                                   if f"{bt_name}:{getattr(c, 'name', '')}" not in disabled]
                if len(enabled_configs) < len(configs):
                    logger.info("BridgeManager: [%s] %d configs, %d enabled",
                                bt_name, len(configs), len(enabled_configs))
                if spec.connect:
                    bridges = spec.connect(enabled_configs)
                    for b in bridges:
                        b.bridge_type = bt_name
                    all_bridges.extend(bridges)
                    logger.info("BridgeManager: [%s] %d bridges connected", bt_name, len(bridges))
            except Exception:
                logger.exception("BridgeManager: [%s] connect failed", bt_name)

        return all_bridges

    @classmethod
    def reload(cls, settings: Any, existing: list[Bridge]) -> list[Bridge]:
        """Re-discover and reconcile. Disconnects removed, connects new."""
        cls._ensure_loaded()

        # Build map of existing bridges by type+name for comparison
        existing_map: dict[tuple[str, str], list[Bridge]] = {}
        for b in existing:
            bt = getattr(b, "bridge_type", b.__class__.__name__)
            cfg_name = getattr(b.config, "name", "")
            existing_map.setdefault((bt, cfg_name), []).append(b)

        new_bridges: list[Bridge] = []

        for bt_name, spec in cls._types.items():
            if not spec.discover:
                continue
            try:
                configs = spec.discover(settings)
                current_names = {getattr(c, "name", "") for c in configs}

                # Keep bridges whose config still exists
                for (e_bt, e_name), e_bridges in list(existing_map.items()):
                    if e_bt != bt_name:
                        continue  # different bridge type
                    if e_name in current_names:
                        new_bridges.extend(e_bridges)
                        logger.debug("BridgeManager: [%s] keeping %s", bt_name, e_name)
                    else:
                        if spec.disconnect:
                            try:
                                spec.disconnect(e_bridges)
                            except Exception:
                                logger.exception("BridgeManager: [%s] disconnect failed", bt_name)

                # Connect new configs
                connected_names = {getattr(b.config, "name", "")
                                   for b in new_bridges if getattr(b, "bridge_type", "") == bt_name}
                new_configs = [c for c in configs if getattr(c, "name", "") not in connected_names]
                if new_configs and spec.connect:
                    new = spec.connect(new_configs)
                    new_bridges.extend(new)
                    logger.info("BridgeManager: [%s] %d new bridges connected", bt_name, len(new))
            except Exception:
                logger.exception("BridgeManager: [%s] reload failed", bt_name)

        # Disconnect removed bridges from types that no longer exist
        for (e_bt, e_name), e_bridges in existing_map.items():
            if e_bt not in cls._types:
                for b in e_bridges:
                    try:
                        b.disconnect()
                    except Exception:
                        pass

        return new_bridges

    @classmethod
    def list_all(cls, settings: Any) -> list[BridgeEntry]:
        """Return all bridge configs for toolbox listing."""
        cls._ensure_loaded()
        entries: list[BridgeEntry] = []

        for bt_name, spec in cls._types.items():
            try:
                configs = spec.discover(settings)
                for cfg in configs:
                    cfg_name = getattr(cfg, "name", "")
                    desc = getattr(cfg, "command", "") or getattr(cfg, "loadout", "") or ""
                    entries.append(BridgeEntry(
                        bridge_type=bt_name,
                        name=cfg_name,
                        description=str(desc),
                    ))
            except Exception:
                logger.exception("BridgeManager: [%s] list_all failed", bt_name)

        return entries


# Re-export BridgeManager as a module alias for clean imports
BridgeManager = _BridgeManager
