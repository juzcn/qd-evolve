import asyncio
import json
import logging

from qd_evolve.tools import get_registry

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
    """Run async coroutine in a fresh event loop.

    Uses asyncio.run() which always creates a new loop — never reuses
    a potentially closed loop from get_event_loop(). When called from
    inside an already-running loop, spawns a thread with its own loop.
    """
    if timeout:
        coro = asyncio.wait_for(coro, timeout)
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Already inside a running event loop — use a separate thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except asyncio.TimeoutError:
        return json.dumps({"error": f"Search timed out after {timeout}s"})
    except Exception as e:
        return json.dumps({"error": f"Search failed: {e}"})


def _serper_search(query: str, search_type: str = "general", timeout: int | None = None) -> str:
    from serper_toolkit.server import (
        serper_general_search,
        serper_image_search,
        serper_news_search,
        startup_all,
    )

    if timeout is None:
        timeout = 30

    async def _do():
        await startup_all()
        match search_type:
            case "general":
                return await serper_general_search(query)
            case "images":
                return await serper_image_search(query)
            case "news":
                return await serper_news_search(query)
            case _:
                return {"error": f"Unknown search_type: {search_type}"}

    # Suppress httpx INFO logs leaking to stderr (once)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    result = _run_async(_do(), timeout=timeout)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _serper_scrape(url: str, timeout: int | None = None) -> str:
    from serper_toolkit.server import serper_scrape as _scrape, startup_all

    if timeout is None:
        timeout = 30

    async def _do():
        await startup_all()
        return await _scrape(url)

    result = _run_async(_do(), timeout=timeout)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)
