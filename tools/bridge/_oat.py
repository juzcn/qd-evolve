"""OAT bridge 鈥?in-process Open-Agent-Tools (boat + coat).

Reads tools/bridge/oat.json for config. Imports packages directly,
wraps functions as ToolRegistry handlers with no subprocess overhead.
"""


import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qd_evolve.logger import logger
from qd_evolve.tools import ToolRegistry, get_registry
from qd_evolve.utils.adk_output import make_handler
from qd_evolve.utils.adk_schema import google_adk_to_openai_schema
from tools.bridge import BridgeManager

OAT_CONFIG = Path(__file__).parent / "oat.json"


@dataclass
class OATBridgeConfig:
    name: str
    package: str
    loadout: str


# 鈹€鈹€ discovery 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def discover_oat_bridges(_settings: Any = None) -> list[OATBridgeConfig]:
    """Read tools/bridge/oat.json for OAT bridge configs."""
    if not OAT_CONFIG.exists():
        logger.debug("OAT: config not found at %s, skipping", OAT_CONFIG)
        return []

    try:
        data = json.loads(OAT_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("OAT: failed to parse %s", OAT_CONFIG)
        return []

    configs: list[OATBridgeConfig] = []
    for name, entry in data.items():
        configs.append(OATBridgeConfig(
            name=name,
            package=entry.get("package", ""),
            loadout=entry.get("loadout", "all"),
        ))
        logger.debug("OAT: discovered '%s' 鈫?%s/%s", name, entry.get("package"), entry.get("loadout"))
    return configs


# 鈹€鈹€ OATBridge 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

class OATBridge:
    """Wraps one OAT package loadout, registers tools in-process."""

    def __init__(self, config: OATBridgeConfig, registry: ToolRegistry | None = None) -> None:
        self.config = config
        self._registry = registry or get_registry()
        self.tool_names: list[str] = []
        self._pkg: Any = None

    def connect(self) -> None:
        """Import package, load tools, register as in-process handlers."""
        try:
            self._pkg = importlib.import_module(self.config.package)
        except ImportError:
            logger.warning("OAT: package '%s' not installed, skipping", self.config.package)
            return

        # Resolve loader
        loader = self._find_loader()
        if loader is None:
            logger.error("OAT: unknown loadout '%s' in package '%s'",
                         self.config.loadout, self.config.package)
            return

        tools = loader()
        logger.info("OAT: [%s] %s:%s 鈫?%d tools", self.config.name,
                    self.config.package, self.config.loadout, len(tools))

        for fn in tools:
            try:
                # First non-empty docstring line as description
                desc = ""
                if fn.__doc__:
                    for line in fn.__doc__.strip().splitlines():
                        line = line.strip()
                        if line and not line.startswith("Args:") and not line.startswith("Returns:"):
                            desc = line[:200]
                            break

                json_schema = google_adk_to_openai_schema(fn)
                handler = make_handler(fn)

                tool_name = fn.__name__
                self._registry.register(
                    name=tool_name,
                    description=f"[{self.config.name}] {desc or tool_name}",
                    input_schema=json_schema,
                    handler=handler,
                )
                self.tool_names.append(tool_name)
                logger.debug("OAT: registered %s", tool_name)
            except Exception:
                logger.exception("OAT: failed to register %s", fn.__name__)

    def _find_loader(self) -> Any:
        """Find the loadout function in the package."""
        name = self.config.loadout

        # "all" 鈫?load_all_tools
        if name == "all":
            return getattr(self._pkg, "load_all_tools", None)

        # Try three naming patterns (in order)
        loader = (
            getattr(self._pkg, f"load_{name}_loadout", None)
            or getattr(self._pkg, f"load_all_{name}_tools", None)
            or getattr(self._pkg, f"load_{name}", None)
        )

        if loader is None:
            # Log available loaders
            available = sorted(
                a.removeprefix("load_").removesuffix("_loadout")
                for a in dir(self._pkg) if a.startswith("load_") and a.endswith("_loadout")
            )
            available += sorted(
                a.removeprefix("load_all_").removesuffix("_tools")
                for a in dir(self._pkg) if a.startswith("load_all_") and a.endswith("_tools")
            )
            available += sorted(
                a.removeprefix("load_")
                for a in dir(self._pkg)
                if a.startswith("load_") and not a.endswith("_loadout")
                and not a.startswith("load_all_") and a != "load_all_tools"
            )
            logger.error("OAT: unknown loadout '%s'. Available: %s",
                         name, ", ".join(sorted(set(available))))

        return loader

    def disconnect(self, shutdown: bool = False) -> None:
        """Unregister all tools from ToolRegistry. If shutdown, skip registry cleanup."""
        count = len(self.tool_names)
        if not shutdown:
            registry = get_registry()
            for tool_name in self.tool_names:
                registry.unregister(tool_name)
        self.tool_names.clear()
        logger.info("OAT: disconnected %s (%d tools)", self.config.name, count)


# 鈹€鈹€ Bridge protocol functions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _connect_oat_bridges(configs: list[OATBridgeConfig]) -> list[OATBridge]:
    bridges: list[OATBridge] = []
    for config in configs:
        try:
            bridge = OATBridge(config)
            bridge.connect()
            bridges.append(bridge)
        except Exception:
            logger.exception("OAT: failed to connect %s", config.name)
    return bridges


def _disconnect_oat_bridges(bridges: list[OATBridge]) -> None:
    for bridge in bridges:
        try:
            bridge.disconnect()
        except Exception:
            logger.exception("OAT: disconnect error for %s", bridge.config.name)


# 鈹€鈹€ Register with BridgeManager 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

BridgeManager.register(
    name="oat",
    discover=discover_oat_bridges,
    connect=_connect_oat_bridges,
    disconnect=_disconnect_oat_bridges,
)

logger.debug("OAT bridge registered")
