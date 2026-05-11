from __future__ import annotations

import asyncio
import json
import logging

from qd_evolve.logger import logger

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
        },
        "required": ["query"],
    },
    handler=lambda query, search_type="general": _serper_search(query, search_type),
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
        },
        "required": ["url"],
    },
    handler=lambda url: _serper_scrape(url),
)


def _run_async(coro):
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


def _serper_search(query: str, search_type: str = "general") -> str:
    from serper_toolkit.server import (
        serper_general_search,
        serper_image_search,
        serper_news_search,
    )

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

    result = _run_async(coro)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _serper_scrape(url: str) -> str:
    from serper_toolkit.server import serper_scrape as _scrape

    _ensure_started()

    result = _run_async(_scrape(url))
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)
