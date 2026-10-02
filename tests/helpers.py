from __future__ import annotations

from typing import Any

BASE = "https://api.serpkite.com"
KEY = "skt_live_test_123"


def meta(**extra: Any) -> dict[str, Any]:
    return {"request_id": "req_1", "credits_used": 1, "cached": False, "engine": "google", **extra}


def search_body(**extra: Any) -> dict[str, Any]:
    return {
        "request": {"endpoint": "search", "engine": "google", "q": "espresso", "country": "us"},
        "results": [
            {
                "position": 1,
                "title": "Best espresso machines",
                "link": "https://example.com/espresso",
                "domain": "example.com",
                "displayed_link": "example.com > espresso",
                "snippet": "Our picks.",
                "brand_new_field": {"x": 1},
            }
        ],
        "related_searches": [{"query": "espresso grinder"}],
        "meta": meta(),
        **extra,
    }


def list_body(endpoint: str, results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "request": {"endpoint": endpoint, "engine": "google"},
        "results": results,
        "meta": meta(),
        **extra,
    }


def error_body(code: str, message: str = "nope", request_id: str = "req_err") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}
