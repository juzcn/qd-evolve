
import json
import logging

import httpx

from qd_evolve.tools import get_registry

# Suppress httpx INFO logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_client = httpx.Client(timeout=30, follow_redirects=True)

registry = get_registry()

registry.register(
    name="fetch",
    description="Fetch a URL and return its content. Supports text and JSON responses.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST"],
                "description": 'HTTP method. Defaults to "GET".',
            },
            "headers": {
                "type": "object",
                "description": "Optional HTTP headers as key-value pairs.",
            },
            "body": {
                "type": "string",
                "description": "Optional request body (for POST).",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 30).",
            },
        },
        "required": ["url"],
    },
    handler=lambda url, method="GET", headers=None, body=None, timeout=30: _fetch(url, method, headers, body, timeout),
)


def _fetch(url: str, method: str = "GET", headers: dict | None = None, body: str | None = None, timeout: int = 30) -> str:
    try:
        content = body if body else None
        resp = _client.request(
            method=method,
            url=url,
            headers=headers,
            content=content,
            timeout=timeout,
        )

        text = resp.text

        return text

    except httpx.HTTPStatusError as e:
        return json.dumps({"error": f"HTTP {e.response.status_code}", "body": e.response.text}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps({"error": f"Request timed out after {timeout}s"})
    except httpx.RequestError as e:
        return json.dumps({"error": str(e)})
