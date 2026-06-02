import json
import logging

from qd_evolve.tools import get_registry

logger = logging.getLogger(__name__)

registry = get_registry()

registry.register(
    name="ddg_search",
    description="Search the web using DuckDuckGo. No API key required. Supports text, image, and news search.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string.",
            },
            "search_type": {
                "type": "string",
                "enum": ["text", "images", "news"],
                "description": 'Type of search. "text" for general web, "images" for image search, "news" for news. Defaults to "text".',
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return. Default 10, max 30.",
            },
            "region": {
                "type": "string",
                "description": 'Region code (e.g. "cn-zh", "us-en", "wt-wt"). Defaults to "wt-wt" (no region).',
            },
            "safesearch": {
                "type": "string",
                "enum": ["strict", "moderate", "off"],
                "description": 'Safe search level. Defaults to "moderate".',
            },
            "timelimit": {
                "type": "string",
                "enum": ["d", "w", "m", "y"],
                "description": 'Time filter: "d" (day), "w" (week), "m" (month), "y" (year).',
            },
        },
        "required": ["query"],
    },
    handler=lambda **kwargs: _ddg_search(
        kwargs["query"],
        kwargs.get("search_type", "text"),
        kwargs.get("max_results", 10),
        kwargs.get("region", "wt-wt"),
        kwargs.get("safesearch", "moderate"),
        kwargs.get("timelimit", None),
    ),
)

registry.register(
    name="ddg_news",
    description="Search news articles via DuckDuckGo. No API key required. Shortcut for ddg_search with search_type='news'.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The news search query string.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return. Default 10, max 30.",
            },
            "region": {
                "type": "string",
                "description": 'Region code (e.g. "cn-zh", "us-en"). Defaults to "wt-wt".',
            },
            "safesearch": {
                "type": "string",
                "enum": ["strict", "moderate", "off"],
                "description": 'Safe search level. Defaults to "moderate".',
            },
            "timelimit": {
                "type": "string",
                "enum": ["d", "w", "m", "y"],
                "description": 'Time filter: "d" (day), "w" (week), "m" (month), "y" (year).',
            },
        },
        "required": ["query"],
    },
    handler=lambda **kwargs: _ddg_search(
        kwargs["query"],
        "news",
        kwargs.get("max_results", 10),
        kwargs.get("region", "wt-wt"),
        kwargs.get("safesearch", "moderate"),
        kwargs.get("timelimit", None),
    ),
)


def _ddg_search(
    query: str,
    search_type: str = "text",
    max_results: int = 10,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
) -> str:
    from ddgs import DDGS

    max_results = min(max(max_results, 1), 30)

    try:
        with DDGS() as ddgs:
            match search_type:
                case "text":
                    results = list(ddgs.text(
                        query,
                        region=region,
                        safesearch=safesearch,
                        timelimit=timelimit,
                        max_results=max_results,
                    ))
                case "images":
                    results = list(ddgs.images(
                        query,
                        region=region,
                        safesearch=safesearch,
                        timelimit=timelimit,
                        max_results=max_results,
                    ))
                case "news":
                    results = list(ddgs.news(
                        query,
                        region=region,
                        safesearch=safesearch,
                        timelimit=timelimit,
                        max_results=max_results,
                    ))
                case _:
                    return json.dumps({"error": f"Unknown search_type: {search_type}"})

        return json.dumps(results, ensure_ascii=False)

    except Exception as e:
        logger.error(f"DDG search error: {e}")
        return json.dumps({"error": str(e)})
