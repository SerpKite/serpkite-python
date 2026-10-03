from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

import serpkite
from serpkite import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    InsufficientCreditsError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    SerpKite,
    SerpKiteError,
)
from serpkite.types import (
    Account,
    AccountKey,
    NewsResponse,
    OrganicResult,
    RankResponse,
    ReviewsResponse,
    RouteStep,
    SearchResponse,
    WebpageResponse,
)

from .helpers import BASE, KEY, error_body, list_body, meta, search_body


def sent_json(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)


@respx.mock
def test_search_sends_bearer_and_parses_model() -> None:
    route = respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json=search_body()))
    sk = SerpKite()
    res = sk.search("espresso", country="us", num=10, time="week")

    assert isinstance(res, SearchResponse)
    assert isinstance(res.results[0], OrganicResult)
    assert res.results[0].title == "Best espresso machines"
    assert res.results[0].displayed_link == "example.com > espresso"
    assert res.meta.credits_used == 1
    # Unknown fields are kept, not rejected.
    assert res.results[0].model_extra == {"brand_new_field": {"x": 1}}

    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["user-agent"].startswith("serpkite-python/")
    assert "x-api-key" not in req.headers
    assert sent_json(route) == {"q": "espresso", "country": "us", "num": 10, "time": "week"}


@respx.mock
def test_none_params_are_dropped() -> None:
    route = respx.post(f"{BASE}/v1/news").mock(return_value=httpx.Response(200, json=list_body("news", [])))
    res = SerpKite().news("ai", country=None)
    assert isinstance(res, NewsResponse)
    assert sent_json(route) == {"q": "ai"}


@respx.mock
def test_markdown_returns_str() -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(
            200,
            text="# espresso\n\n1. [Best](https://example.com)",
            headers={"content-type": "text/markdown"},
        )
    )
    md = SerpKite().search("espresso", format="markdown")
    assert isinstance(md, str)
    assert md.startswith("# espresso")
    assert sent_json(route) == {"q": "espresso", "format": "markdown"}


@respx.mock
def test_compact_and_fields_return_dict() -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"title": "t", "link": "l"}], "meta": meta()}),
            httpx.Response(200, json={"results": [{"title": "t"}], "request": {}, "meta": meta()}),
        ]
    )
    sk = SerpKite()
    compact = sk.search("q", format="compact")
    assert compact["results"][0]["title"] == "t"
    projected = sk.search("q", fields="results.title")
    assert projected["results"] == [{"title": "t"}]
    assert sent_json(route, 0) == {"q": "q", "format": "compact"}
    assert sent_json(route, 1) == {"q": "q", "fields": "results.title"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("search", "/v1/search"),
        ("images", "/v1/images"),
        ("videos", "/v1/videos"),
        ("news", "/v1/news"),
        ("maps", "/v1/maps"),
        ("places", "/v1/places"),
        ("shopping", "/v1/shopping"),
        ("scholar", "/v1/scholar"),
        ("patents", "/v1/patents"),
        ("autocomplete", "/v1/autocomplete"),
    ],
)
@respx.mock
def test_query_vertical_paths(method: str, path: str) -> None:
    body = search_body() if method == "search" else list_body(method, [])
    route = respx.post(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=body))
    res = getattr(SerpKite(), method)("coffee")
    assert route.called
    assert res.request.engine == "google"


@respx.mock
def test_engine_list_and_route() -> None:
    body = search_body(
        request={"endpoint": "search", "engine": ["google", "brave"], "q": "espresso"},
        meta=meta(
            engine="brave",
            route=[
                {"provider": "google", "outcome": "blocked", "ms": 812},
                {"provider": "brave", "outcome": "ok", "ms": 431},
            ],
        ),
    )
    route = respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json=body))
    sk = SerpKite()
    res = sk.search("espresso", engine=("google", "brave"))
    assert sent_json(route) == {"q": "espresso", "engine": ["google", "brave"]}
    assert res.meta.engine == "brave"
    assert res.meta.route is not None
    assert isinstance(res.meta.route[0], RouteStep)
    assert [(s.provider, s.outcome, s.ms) for s in res.meta.route] == [
        ("google", "blocked", 812),
        ("brave", "ok", 431),
    ]
    assert res.request.engine == ["google", "brave"]

    sk.search("espresso", engine="auto")
    assert sent_json(route) == {"q": "espresso", "engine": "auto"}


@respx.mock
def test_consensus_sources() -> None:
    body = search_body(
        meta=meta(
            engine="consensus",
            route=[
                {"provider": "google", "outcome": "ok", "ms": 900},
                {"provider": "brave", "outcome": "canceled", "ms": 120},
            ],
        )
    )
    body["results"][0]["sources"] = ["google", "wikipedia"]
    route = respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json=body))
    res = SerpKite().search("espresso", engine="consensus")
    assert sent_json(route) == {"q": "espresso", "engine": "consensus"}
    assert res.meta.engine == "consensus"
    assert res.results[0].sources == ["google", "wikipedia"]
    assert [s.outcome for s in res.meta.route or []] == ["ok", "canceled"]


@respx.mock
def test_route_absent_on_cache_hit() -> None:
    respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(200, json=search_body(meta=meta(cached=True)))
    )
    assert SerpKite().search("espresso", max_age=3600).meta.route is None


@respx.mock
def test_reviews_requires_an_id_and_pages() -> None:
    body = list_body("reviews", [{"rating": 5, "snippet": "Great"}], next_page_token="tok2")
    route = respx.post(f"{BASE}/v1/reviews").mock(return_value=httpx.Response(200, json=body))
    sk = SerpKite()
    with pytest.raises(ValueError):
        sk.reviews(sort="newest")
    res = sk.reviews(place_id="ChIJ123", sort="newest", page_token="tok1")
    assert isinstance(res, ReviewsResponse)
    assert res.next_page_token == "tok2"
    assert sent_json(route) == {"place_id": "ChIJ123", "sort": "newest", "page_token": "tok1"}


@respx.mock
def test_webpage_rank_account() -> None:
    web = respx.post(f"{BASE}/v1/webpage").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "request": {"endpoint": "webpage", "engine": "http"},
                    "url": "https://example.com/",
                    "status_code": 200,
                    "markdown": "# Example",
                    "metadata": {"title": "Example"},
                    "meta": meta(),
                },
            ),
            httpx.Response(200, text="# Example", headers={"content-type": "text/markdown; charset=utf-8"}),
        ]
    )
    rank = respx.post(f"{BASE}/v1/rank").mock(
        return_value=httpx.Response(
            200,
            json={
                "request": {"endpoint": "rank", "engine": "google"},
                "domain": "example.com",
                "position": None,
                "matches": [],
                "checked": 100,
                "meta": meta(credits_used=7),
            },
        )
    )
    acct = respx.get(f"{BASE}/v1/account").mock(
        return_value=httpx.Response(
            200,
            json={
                "balance": 1234.5,
                "rate_limit_rps": 20,
                "plan": "paid",
                "key": {"id": "k1", "name": "prod", "credit_limit": None, "credits_used_month": 3},
                "monthly_spend_cap": None,
                "month": {"credits": 12.5, "requests": 10},
            },
        )
    )
    sk = SerpKite()

    page = sk.webpage("https://example.com", include_html=True)
    assert isinstance(page, WebpageResponse)
    assert page.metadata.title == "Example"
    assert sent_json(web, 0) == {"url": "https://example.com", "include_html": True}
    assert sk.webpage("https://example.com", format="markdown") == "# Example"
    assert sent_json(web, 1) == {"url": "https://example.com", "format": "markdown"}

    r = sk.rank("espresso", "example.com", num=50)
    assert isinstance(r, RankResponse)
    assert r.position is None
    assert sent_json(rank) == {"q": "espresso", "domain": "example.com", "num": 50}

    a = sk.account()
    assert isinstance(a, Account)
    assert isinstance(a.key, AccountKey)
    assert a.balance == 1234.5
    assert acct.calls.last.request.content == b""


@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        (400, "invalid_request", BadRequestError),
        (401, "unauthorized", AuthenticationError),
        (402, "insufficient_credits", InsufficientCreditsError),
        (403, "spend_cap_reached", PermissionDeniedError),
        (404, "not_found", NotFoundError),
        (429, "daily_limit_reached", RateLimitError),
        (503, "upstream_blocked", APIError),
        (503, "upstream_timeout", APIError),
    ],
)
@respx.mock
def test_error_mapping(status: int, code: str, cls: type[SerpKiteError]) -> None:
    respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(status, json=error_body(code, "bad thing", "req_42"))
    )
    with pytest.raises(cls) as info:
        SerpKite(max_retries=0).search("x")
    err = info.value
    assert isinstance(err, SerpKiteError)
    assert err.status == status
    assert err.code == code
    assert err.message == "bad thing"
    assert err.request_id == "req_42"
    assert "req_42" in str(err)


@respx.mock
def test_error_without_json_body() -> None:
    respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(503, text="upstream down", headers={"x-request-id": "rid"})
    )
    with pytest.raises(APIError) as info:
        SerpKite(max_retries=0).search("x")
    assert info.value.message == "upstream down"
    assert info.value.request_id == "rid"


@respx.mock
def test_retries_on_429_and_5xx_honoring_retry_after(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited"), headers={"retry-after": "3"}),
            httpx.Response(503, json=error_body("upstream_blocked"), headers={"retry-after": "5"}),
            httpx.Response(503, json=error_body("upstream_error")),
            httpx.Response(200, json=search_body()),
        ]
    )
    res = SerpKite(max_retries=3).search("x")
    assert isinstance(res, SearchResponse)
    assert route.call_count == 4
    assert sleeps[0] == 3.0
    assert sleeps[1] == 5.0
    assert 0 < sleeps[2] <= 4.0  # backoff with jitter


@respx.mock
def test_retries_legacy_502_and_504(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        side_effect=[
            httpx.Response(502, text="error code: 502"),
            httpx.Response(504, text="error code: 504"),
            httpx.Response(200, json=search_body()),
        ]
    )
    assert isinstance(SerpKite(max_retries=2).search("x"), SearchResponse)
    assert route.call_count == 3


@respx.mock
def test_retries_give_up_and_raise(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(503, json=error_body("unavailable"))
    )
    with pytest.raises(APIError):
        SerpKite(max_retries=2).search("x")
    assert route.call_count == 3
    assert len(sleeps) == 2


@respx.mock
def test_no_retry_on_daily_cap_or_4xx(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(429, json=error_body("daily_limit_reached"))
    )
    with pytest.raises(RateLimitError):
        SerpKite().search("x")
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_retries_connection_errors(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json=search_body())]
    )
    assert isinstance(SerpKite().search("x"), SearchResponse)
    assert route.call_count == 2

    respx.post(f"{BASE}/v1/news").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(APIConnectionError):
        SerpKite(max_retries=1).news("x")


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPKITE_API_KEY")
    with pytest.raises(SerpKiteError, match="SERPKITE_API_KEY"):
        SerpKite()


@respx.mock
def test_options_base_url_and_injected_client() -> None:
    route = respx.post("http://localhost:8080/v1/search").mock(
        return_value=httpx.Response(200, json=search_body())
    )
    http = httpx.Client()
    with SerpKite(api_key="skt_live_other", base_url="http://localhost:8080/", http_client=http) as sk:
        sk.search("x")
        assert sk.base_url == "http://localhost:8080"
    assert route.calls.last.request.headers["authorization"] == "Bearer skt_live_other"
    assert not http.is_closed  # the caller owns it
    http.close()


def test_env_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPKITE_BASE_URL", "https://staging.example")
    assert SerpKite().base_url == "https://staging.example"


def test_version() -> None:
    assert serpkite.__version__ == "0.2.0"


@respx.mock
def test_low_level_request() -> None:
    route = respx.post(f"{BASE}/v1/news").mock(
        side_effect=[
            httpx.Response(200, json=list_body("news", [])),
            httpx.Response(200, text="**a**", headers={"content-type": "text/markdown"}),
        ]
    )
    sk = SerpKite()
    assert sk.request("news", {"q": "x", "country": None})["request"]["endpoint"] == "news"
    assert sk.request("/news/", {"q": "x", "format": "markdown"}) == "**a**"
    assert sent_json(route, 0) == {"q": "x"}
