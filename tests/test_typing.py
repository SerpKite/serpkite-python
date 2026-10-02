"""Static checks (run by mypy) that the overloads pick the right return types."""

from __future__ import annotations

from typing import Any

import httpx
import respx
from typing_extensions import assert_type

from serpkite import AsyncSerpKite, SerpKite
from serpkite.types import LensResponse, NewsResponse, ReviewsResponse, SearchResponse, WebpageResponse

from .helpers import BASE, list_body, search_body


@respx.mock
async def test_overload_return_types() -> None:
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json=search_body()))
    respx.post(f"{BASE}/v1/news").mock(return_value=httpx.Response(200, json=list_body("news", [])))
    respx.post(f"{BASE}/v1/lens").mock(return_value=httpx.Response(200, json=list_body("lens", [])))
    respx.post(f"{BASE}/v1/webpage").mock(
        return_value=httpx.Response(200, text="# x", headers={"content-type": "text/markdown"})
    )
    sk = SerpKite()
    assert_type(sk.search("q"), SearchResponse)
    assert_type(sk.search("q", format="json", country="us"), SearchResponse)
    assert_type(sk.news("q", time="day"), NewsResponse)
    assert_type(sk.lens("https://img.example/a.png"), LensResponse)
    assert_type(sk.webpage("https://example.com", format="markdown"), str)

    ask = AsyncSerpKite()
    assert_type(await ask.search("q"), SearchResponse)
    assert_type(await ask.news("q"), NewsResponse)
    await ask.close()


def _type_only_checks(sk: SerpKite) -> None:
    """Never called: mypy checks the inferred types."""
    assert_type(sk.search("q", format="markdown"), str)
    assert_type(sk.search("q", format="compact"), dict[str, Any])
    assert_type(sk.search("q", fields="results.title"), dict[str, Any])
    assert_type(sk.search("q", format="markdown", fields="results.title"), str)
    assert_type(sk.reviews(place_id="x"), ReviewsResponse)
    assert_type(sk.webpage("https://example.com"), WebpageResponse)
    assert_type(sk.search("q", engine="auto"), SearchResponse)
    assert_type(sk.news("q", engine=["google", "brave"]), NewsResponse)
