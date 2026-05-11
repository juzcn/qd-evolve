"""MCP server exposing basic-open-agent-tools as callable MCP tools.

Run via: python -m qd_evolve.boat_mcp_server --loadout coder
"""

from __future__ import annotations

import argparse
import functools
import inspect
from typing import Any

import basic_open_agent_tools as boat
from mcp.server.fastmcp import FastMCP


def _make_wrapper(fn: Any) -> tuple[Any, str]:
    """Wrap a BOAT function: strip skip_confirm from signature, set it True at call time."""
    sig = inspect.signature(fn)
    new_params = [
        p for name, p in sig.parameters.items()
        if name != "skip_confirm"
    ]
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
            return str(result) if result is not None else "(done)"
        except Exception as exc:
            return f"Error: {exc}"

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    return wrapper, doc


def main() -> None:
    parser = argparse.ArgumentParser(description="BOAT MCP server")
    parser.add_argument("--loadout", default="coder",
                        help="BOAT loadout name (default: coder)")
    args = parser.parse_args()

    name = args.loadout

    # Try three naming patterns (in order)
    loader = (
        getattr(boat, f"load_{name}_loadout", None)          # loadouts: coder, docs, ...
        or getattr(boat, f"load_all_{name}_tools", None)     # per-module: pdf, excel, ..., all
        or getattr(boat, f"load_{name}", None)               # curated bundles: converters, essential, ...
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
