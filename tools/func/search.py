
import asyncio
import json
import logging

from qd_evolve.tools import get_registry

_started = False


def _ensure_started() -> None:
    global _started
    if _started:
        return
    from serper_toolkit.server import startup_all
    asyncio.run(startup_all())
    _started = True
    # Suppress httpx INFO logs leaking to stderr
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


registry = get_registry()

registry.register(
    name="serper_search",
    description="Search the web using Serper API. Supports general, image, and news search.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string.",
            },
            "search_type": {
                "type": "string",
                "enum": ["general", "images", "news"],
                "description": 'Type of search. Defaults to "general".',
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default 30s.",
            },
        },
        "required": ["query"],
    },
    handler=lambda **kwargs: _serper_search(
        kwargs["query"],
        kwargs.get("search_type", "general"),
        kwargs.get("timeout", None),
    ),
)

registry.register(
    name="serper_scrape",
    description="Scrape a webpage and return its content.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the webpage to scrape.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default 30s.",
            },
        },
        "required": ["url"],
    },
    handler=lambda **kwargs: _serper_scrape(
        kwargs["url"],
        kwargs.get("timeout", None),
    ),
)


def _run_async(coro, timeout: int | None = None):
    if timeout:
        coro = asyncio.wait_for(coro, timeout)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
    except asyncio.TimeoutError:
        return json.dumps({"error": f"Search timed out after {timeout}s"})


def _serper_search(query: str, search_type: str = "general", timeout: int | None = None) -> str:
    from serper_toolkit.server import (
        serper_general_search,
        serper_image_search,
        serper_news_search,
    )

    if timeout is None:
        timeout = 30

    _ensure_started()

    match search_type:
        case "general":
            coro = serper_general_search(query)
        case "images":
            coro = serper_image_search(query)
        case "news":
            coro = serper_news_search(query)
        case _:
            return json.dumps({"error": f"Unknown search_type: {search_type}"})

    result = _run_async(coro, timeout=timeout)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _serper_scrape(url: str, timeout: int | None = None) -> str:
    from serper_toolkit.server import serper_scrape as _scrape

    if timeout is None:
        timeout = 30

    _ensure_started()

    result = _run_async(_scrape(url), timeout=timeout)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)
