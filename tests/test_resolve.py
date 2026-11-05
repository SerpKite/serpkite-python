from __future__ import annotations

import json

import httpx
import respx

from serpkite import AsyncSerpKite, SerpKite
from serpkite.types import ResolveResponse

from .helpers import BASE

GOTO = "https://www.google.com/goto?url=CAES1"

BODY = {
    "results": [
        {
            "link": GOTO,
            "status": "resolved",
            "destination": "https://example.com/page",
            "domain": "example.com",
        },
        {"link": GOTO + "2", "status": "pending"},
    ],
    "retry_after": 12,
    "meta": {"request_id": "r", "credits_used": 0},
}


@respx.mock
def test_resolve() -> None:
    route = respx.post(f"{BASE}/v1/resolve").mock(return_value=httpx.Response(200, json=BODY))
    res = SerpKite().resolve([GOTO, GOTO + "2"])
    assert isinstance(res, ResolveResponse)
    assert res.results[0].destination == "https://example.com/page"
    assert res.results[1].status == "pending" and res.retry_after == 12
    assert json.loads(route.calls.last.request.content) == {"links": [GOTO, GOTO + "2"]}


@respx.mock
async def test_resolve_async() -> None:
    respx.post(f"{BASE}/v1/resolve").mock(return_value=httpx.Response(200, json=BODY))
    async with AsyncSerpKite() as sk:
        res = await sk.resolve((GOTO,))
    assert res.results[0].domain == "example.com"
