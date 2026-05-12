"""Google ADK tool function → OpenAI JSON Schema converter.

Boat and coat follow the Google ADK convention:
- All parameters are JSON-serializable types (str, int, float, bool, list, dict)
- No default parameter values
- skip_confirm parameter should be stripped
- Return type is dict[str, str] or list[dict[str, str]]
"""

from __future__ import annotations

import inspect
import types
import typing
from typing import Any


def google_adk_to_openai_schema(fn: Any) -> dict[str, Any]:
    """Convert a Google ADK tool function signature to OpenAI JSON Schema.

    Uses inspect.signature to derive parameter types. Strips skip_confirm.
    """
    sig = inspect.signature(fn)
    props: dict[str, Any] = {}
    required: list[str] = []

    for name, p in sig.parameters.items():
        if name == "skip_confirm":
            continue

        ann = p.annotation if p.annotation is not inspect.Parameter.empty else Any
        props[name] = _annotation_to_json_type(ann)
        if p.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def _annotation_to_json_type(ann: Any) -> dict[str, Any]:
    """Recursively map a Python type annotation to a JSON Schema type object."""
    # Handle typing.Annotated (Python 3.13+)
    origin = typing.get_origin(ann)
    if origin is not None:
        origin_str = getattr(origin, "__name__", str(origin))
        args = typing.get_args(ann)

        if origin_str == "Union" or origin is types.UnionType:
            # Union[X, None] / X | None
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                schema = _annotation_to_json_type(non_none[0])
            else:
                schema = {}
            if any(a is type(None) for a in args):
                if schema:
                    schema["nullable"] = True
            return schema

        if origin_str in ("list", "List"):
            items_schema: dict[str, Any] = {}
            if args:
                items_schema = _annotation_to_json_type(args[0])
            return {"type": "array", "items": items_schema}

        if origin_str in ("dict", "Dict"):
            value_schema: dict[str, Any] = {}
            if len(args) >= 2:
                value_schema = _annotation_to_json_type(args[1])
            return {"type": "object", "additionalProperties": value_schema}

        if origin_str == "Optional":
            inner = _annotation_to_json_type(args[0])
            if inner:
                inner["nullable"] = True
            return inner

    # Direct types
    if ann is str:
        return {"type": "string"}
    if ann is int:
        return {"type": "integer"}
    if ann is float:
        return {"type": "number"}
    if ann is bool:
        return {"type": "boolean"}
    if ann is Any:
        return {}

    # Named types — check by string for forward references
    ann_str = getattr(ann, "__name__", str(ann))
    if ann_str == "str":
        return {"type": "string"}
    if ann_str == "int":
        return {"type": "integer"}
    if ann_str == "float":
        return {"type": "number"}
    if ann_str == "bool":
        return {"type": "boolean"}

    return {}
