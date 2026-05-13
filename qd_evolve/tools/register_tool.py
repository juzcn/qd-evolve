"""Dynamic tool registration — lets the LLM create and register tools at runtime."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from qd_evolve.logger import logger
from qd_evolve.tools import get_registry

registry = get_registry()


def _parse_params(params_desc: dict[str, str]) -> dict[str, Any]:
    """Convert LLM-provided parameter descriptions into JSON Schema."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, desc in (params_desc or {}).items():
        desc_lower = desc.lower()
        json_type = "string"
        if any(t in desc_lower for t in ("int", "integer")):
            json_type = "integer"
        elif any(t in desc_lower for t in ("number", "float")):
            json_type = "number"
        elif "bool" in desc_lower:
            json_type = "boolean"
        properties[name] = {"type": json_type, "description": desc}
        if "required" in desc_lower:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def _make_shell_handler(template: str) -> Any:
    def handler(**kwargs: Any) -> str:
        cmd = template.format(**{k: str(v) for k, v in kwargs.items()})
        import locale
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True)
        except OSError as e:
            return f"Command failed: {type(e).__name__}: {e}"
        enc = locale.getpreferredencoding(False)
        out = _decode(result.stdout, enc)
        if result.stderr:
            out += f"\nSTDERR:\n{_decode(result.stderr, enc)}"
        if result.returncode != 0:
            out += f"\nExit code: {result.returncode}"
        return out.strip() or "(no output)"
    return handler


def _make_python_handler(func_body: str, param_names: list[str]) -> Any:
    """exec the LLM-provided function body inside a wrapper that injects kwargs as locals."""
    pulls = "\n".join(f"    {p} = __kwargs.pop('{p}', None)" for p in param_names)
    lines = func_body.split("\n")
    body = "\n".join(f"    {line}" for line in lines)
    code = f"def _handler(**__kwargs):\n{pulls}\n{body}"
    ns: dict[str, Any] = {"__builtins__": __builtins__}
    try:
        exec(code, ns)
    except Exception as e:
        raise ValueError(f"Failed to compile handler body: {e}") from e
    fn = ns.get("_handler")
    if not callable(fn):
        raise ValueError("handler body did not produce a callable")
    return fn


def _make_http_handler(url_template: str, method: str) -> Any:
    import httpx
    client = httpx.Client(timeout=30, follow_redirects=True)

    def handler(**kwargs: Any) -> str:
        url = url_template.format(**{k: str(v) for k, v in kwargs.items()})
        try:
            resp = client.request(method=method, url=url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as e:
            return json.dumps({"error": f"HTTP {e.response.status_code}", "body": e.response.text[:2000]}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)
    return handler


def _decode(data: bytes | None, fallback_enc: str) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback_enc, errors="replace")


def register_tool(
    name: str,
    description: str,
    handler_type: str,
    handler_content: str,
    parameters: dict[str, str] | None = None,
    method: str = "GET",
) -> str:
    """Register a new tool into the current session."""
    params = parameters or {}
    param_names = list(params.keys())
    input_schema = _parse_params(params)

    handler_content = handler_content.strip()
    actual_method = method.upper()

    if handler_type == "http":
        prefixes = ("GET ", "POST ", "PUT ", "PATCH ", "DELETE ", "HEAD ")
        for pfx in prefixes:
            if handler_content.upper().startswith(pfx):
                actual_method = handler_content[: len(pfx)].strip().upper()
                handler_content = handler_content[len(pfx):].strip()
                break

    try:
        if handler_type == "shell":
            handler = _make_shell_handler(handler_content)
        elif handler_type == "python":
            handler = _make_python_handler(handler_content, param_names)
        elif handler_type == "http":
            handler = _make_http_handler(handler_content, actual_method)
        else:
            return f"Error: unknown handler_type '{handler_type}'. Must be 'shell', 'python', or 'http'."
    except Exception as e:
        logger.exception("register_tool: failed to create handler for '%s'", name)
        return f"Error creating handler for '{name}': {e}"

    registry.register(name=name, description=description, handler=handler, input_schema=input_schema)

    type_label = handler_type if handler_type != "http" else f"http {actual_method}"
    return (
        f"Tool '{name}' registered successfully (type: {type_label}). "
        f"It is available on the next turn for the remainder of this session. "
        f"To persist permanently, write its definition to a file — "
        f"tools/cli/<name>.yaml for CLI commands, "
        f"tools/mcp/<name>.json for MCP servers, "
        f"or register it as a builtin for Python logic."
    )


registry.register(
    name="register_tool",
    description=(
        "Register a new reusable tool for the current session. "
        "Use when you discover a useful command, API, or code snippet that should become a named tool with parameters. "
        "handler_type: 'shell' for command templates, 'python' for Python code, 'http' for URL endpoints."
    ),
    handler=register_tool,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Unique tool name in snake_case, e.g. 'ping_host'",
            },
            "description": {
                "type": "string",
                "description": "One-line description of what the tool does",
            },
            "handler_type": {
                "type": "string",
                "enum": ["shell", "python", "http"],
                "description": (
                    "'shell' — command template with {param} placeholders. "
                    "'python' — function body, with parameter values available as local variables. "
                    "'http' — URL template with {param} placeholders"
                ),
            },
            "handler_content": {
                "type": "string",
                "description": (
                    "For 'shell': command template, e.g. 'ping -c {count} {host}'. "
                    "For 'python': function body using param names as variables, return a string. "
                    "For 'http': URL template, optionally prefixed with HTTP method, e.g. 'POST https://api.example.com/{endpoint}'"
                ),
            },
            "parameters": {
                "type": "object",
                "description": "Parameter descriptions as {'name': 'type info'}. Types: string, int/integer, number/float, bool. Add 'required' for mandatory params. E.g. {'host': 'string, required', 'count': 'int, default 4'}",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                "description": "HTTP method for 'http' type (default GET). Ignored if handler_content already starts with a method prefix.",
            },
        },
        "required": ["name", "description", "handler_type", "handler_content"],
    },
)
