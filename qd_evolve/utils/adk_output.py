"""Output normalizer for Google ADK tool functions.

Converts any return value to a string safe for LLM consumption.
Provides make_handler factory for wrapping boat/coat functions.
"""

from __future__ import annotations

import functools
import inspect
import json
from typing import Any, Callable


def normalize_output(result: Any) -> str:
    """Convert any tool return value to a string for LLM display."""
    if result is None:
        return "(done)"
    if isinstance(result, str):
        return result
    if isinstance(result, (int, float, bool)):
        return str(result)
    if isinstance(result, dict):
        return json.dumps(_normalize_dict(result), ensure_ascii=False)
    if isinstance(result, list):
        return json.dumps(
            [_normalize_dict(item) if isinstance(item, dict) else item for item in result],
            ensure_ascii=False,
        )
    return str(result)


def _normalize_dict(d: dict) -> dict:
    """Recursively convert all values to JSON-safe primitives (str only)."""
    out: dict = {}
    for k, v in d.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (int, float, bool)):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _normalize_dict(v)
        elif isinstance(v, list):
            out[k] = [_normalize_dict(i) if isinstance(i, dict) else str(i) for i in v]
        else:
            out[k] = str(v)
    return out


def make_handler(fn: Callable) -> Callable[..., str]:
    """Wrap a boat/coat function as a ToolRegistry-compatible handler.

    Strips skip_confirm, forces it to True. Normalizes the return value.
    """
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def handler(**kwargs: Any) -> str:
        if "skip_confirm" in sig.parameters:
            kwargs["skip_confirm"] = True
        try:
            result = fn(**kwargs)
            return normalize_output(result)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    return handler
