from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from serpkite import AsyncSerpKite, AuthenticationError, BatchTimeoutError, SerpKite
from serpkite.types import Batch, ErrorResponse, SearchResponse

from .helpers import BASE, KEY, error_body, search_body

BID = "0b5a3a8e-4a7b-4c61-9f3e-1d2c3b4a5f60"


def batch(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": BID,
        "status": status,
        "endpoint": "/v1/search",
        "created_at": "2026-09-29T10:00:00Z",
        "completed_at": None,
        "credits_used": 0,
        "poll_url": f"{BASE}/v1/batches/{BID}",
        "webhook_url": None,
        "webhook_status": None,
        "error": None,
        "result": None,
        **extra,
    }


@respx.mock
def test_batches_create_get_wait(sleeps: list[float]) -> None:
    create = respx.post(f"{BASE}/v1/batches").mock(
        return_value=httpx.Response(
            202, json={"batches": [batch("queued"), error_body("invalid_request", "q is required")]}
        )
    )
    poll = respx.get(f"{BASE}/v1/batches/{BID}").mock(
        side_effect=[
            httpx.Response(200, json=batch("queued")),
            httpx.Response(200, json=batch("running")),
            httpx.Response(
                200,
                json=batch(
                    "done",
                    completed_at="2026-09-29T10:00:05Z",
                    credits_used=0.5,
                    result=search_body(),
                ),
            ),
        ]
    )
    sk = SerpKite()
    job = sk.batches.create(
        endpoint="ai_mode", requests=[{"q": "a"}, {}], webhook_url="https://hooks.example/sk"
    )
    assert json.loads(create.calls.last.request.content) == {
        "endpoint": "ai-mode",
        "requests": [{"q": "a"}, {}],
        "webhook_url": "https://hooks.example/sk",
    }
    first, second = job.batches
    assert isinstance(first, Batch)
    assert isinstance(second, ErrorResponse)
    assert second.error.code == "invalid_request"

    done = sk.batches.wait(str(first.id), poll_interval=1.5)
    assert done.status == "done"
    assert done.credits_used == 0.5
    assert done.result is not None
    assert SearchResponse.model_validate(done.result).results[0].domain == "example.com"
    assert poll.call_count == 3
    assert sleeps == [1.5, 1.5]


@respx.mock
def test_batches_wait_times_out() -> None:
    respx.get(f"{BASE}/v1/batches/{BID}").mock(return_value=httpx.Response(200, json=batch("running")))
    with pytest.raises(BatchTimeoutError) as info:
        SerpKite().batches.wait(BID, timeout=0.0, poll_interval=1)
    assert isinstance(info.value, TimeoutError)
    assert info.value.batch_id == BID


def test_batches_validates_size() -> None:
    sk = SerpKite()
    with pytest.raises(ValueError):
        sk.batches.create(endpoint="search", requests=[])
    with pytest.raises(ValueError):
        sk.batches.create(endpoint="search", requests=[{"q": str(i)} for i in range(101)])


@respx.mock
def test_batches_create_is_not_retried_on_5xx(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/batches").mock(
        return_value=httpx.Response(503, json=error_body("upstream_error"))
    )
    with pytest.raises(Exception):  # noqa: B017
        SerpKite().batches.create(endpoint="search", requests=[{"q": "a"}])
    assert route.call_count == 1


@respx.mock
async def test_async_client_search_markdown_errors_and_retries(sleeps: list[float]) -> None:
    search = respx.post(f"{BASE}/v1/search").mock(
        side_effect=[
            httpx.Response(503, json=error_body("unavailable")),
            httpx.Response(200, json=search_body()),
            httpx.Response(200, text="# md", headers={"content-type": "text/markdown"}),
        ]
    )
    respx.get(f"{BASE}/v1/account").mock(return_value=httpx.Response(401, json=error_body("unauthorized")))

    async with AsyncSerpKite() as sk:
        res = await sk.search("espresso", country="de")
        assert isinstance(res, SearchResponse)
        assert res.results[0].title == "Best espresso machines"
        md = await sk.search("espresso", format="markdown")
        assert md == "# md"
        with pytest.raises(AuthenticationError):
            await sk.account()

    assert search.call_count == 3
    assert search.calls[0].request.headers["authorization"] == f"Bearer {KEY}"
    assert len(sleeps) == 1


@respx.mock
async def test_async_batches_wait(sleeps: list[float]) -> None:
    respx.post(f"{BASE}/v1/batches").mock(
        return_value=httpx.Response(202, json={"batches": [batch("queued")]})
    )
    respx.get(f"{BASE}/v1/batches/{BID}").mock(
        side_effect=[
            httpx.Response(200, json=batch("running")),
            httpx.Response(200, json=batch("failed", error={"code": "upstream_error", "message": "x"})),
        ]
    )
    async with AsyncSerpKite() as sk:
        job = await sk.batches.create(endpoint="search", requests=[{"q": "a"}])
        entry = job.batches[0]
        assert isinstance(entry, Batch)
        done = await sk.batches.wait(str(entry.id), poll_interval=0.25)
    assert done.status == "failed"
    assert done.error is not None
    assert done.error.code == "upstream_error"
    assert sleeps == [0.25]
