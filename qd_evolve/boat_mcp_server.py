"""MCP server exposing basic-open-agent-tools as callable MCP tools.

Run via: python -m qd_evolve.boat_mcp_server --loadout coder
"""

from __future__ import annotations

import argparse
import functools
import inspect
import json
from typing import Any

import basic_open_agent_tools as boat
from qd_evolve.logger import logger
from mcp.server.fastmcp import FastMCP


_DEFAULTS: dict[type, Any] = {bool: False}


def _sanitize(obj: Any) -> Any:
    """Replace None with '' in dict values to survive pydantic string validation."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj if obj is not None else ""


def _make_wrapper(fn: Any) -> tuple[Any, str]:
    """Wrap a BOAT function: strip skip_confirm, add defaults for optional params."""
    sig = inspect.signature(fn)
    new_params = []
    has_default = False
    for name, p in sig.parameters.items():
        if name == "skip_confirm":
            continue
        # If non-string param has no default, give it one (bool/int/float)
        if p.default is inspect.Parameter.empty and p.annotation in _DEFAULTS:
            new_params.append(p.replace(default=_DEFAULTS[p.annotation]))
            has_default = True
        elif p.default is not inspect.Parameter.empty:
            new_params.append(p)
            has_default = True
        elif has_default and p.default is inspect.Parameter.empty:
            # Follows a parameter with a default — must not be required
            new_params.append(p.replace(default=None))
            has_default = True
        else:
            new_params.append(p)
    new_sig = sig.replace(parameters=new_params)

    # First non-empty line of docstring as description
    doc = ""
    if fn.__doc__:
        for line in fn.__doc__.strip().splitlines():
            line = line.strip()
            if line and not line.startswith("Args:") and not line.startswith("Returns:"):
                doc = line[:200]
                break

    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> str:
        if "skip_confirm" in sig.parameters:
            kwargs["skip_confirm"] = True
        try:
            result = fn(**kwargs)
            if result is None:
                return "(done)"
            if isinstance(result, (dict, list)):
                return _sanitize(result)
            return str(result)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    return wrapper, doc


def main() -> None:
    parser = argparse.ArgumentParser(description="BOAT MCP server")
    parser.add_argument("--loadout", default="coder",
                        help="BOAT loadout name (default: coder)")
    args = parser.parse_args()

    name = args.loadout

    # Special case: "all" → load_all_tools
    if name == "all":
        loader = boat.load_all_tools
    else:
        # Try three naming patterns (in order)
        loader = (
            getattr(boat, f"load_{name}_loadout", None)          # loadouts: coder, docs, ...
            or getattr(boat, f"load_all_{name}_tools", None)     # per-module: pdf, excel, ...
            or getattr(boat, f"load_{name}", None)               # curated bundles: converters, ...
        )

    if loader is None:
        available = sorted(
            a.removeprefix("load_").removesuffix("_loadout")
            for a in dir(boat) if a.startswith("load_") and a.endswith("_loadout")
        )
        available += sorted(
            a.removeprefix("load_all_").removesuffix("_tools")
            for a in dir(boat) if a.startswith("load_all_") and a.endswith("_tools")
        )
        available += sorted(
            a.removeprefix("load_")
            for a in dir(boat)
            if a.startswith("load_") and not a.endswith("_loadout")
            and not a.startswith("load_all_") and a != "load_all_tools"
        )
        logger.error("BOAT: unknown loadout '%s'. Available: %s", name, ', '.join(sorted(set(available))))
        print(f"Error: unknown loadout '{name}'. Available: {', '.join(sorted(set(available)))}",
              flush=True)
        raise SystemExit(1)

    tools = loader()
    mcp = FastMCP("boat")

    for fn in tools:
        wrapper, desc = _make_wrapper(fn)
        mcp.add_tool(wrapper, name=fn.__name__, description=desc)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
