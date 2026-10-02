from __future__ import annotations

import httpx
import pytest
import respx

from serpkite import APIError, AsyncSerpKite, SerpKite, verify_webhook

from .helpers import BASE, error_body

# Same vector as SignWebhook in backend/cmd/serp-api.
SECRET = "whsec_test"
BODY = '{"event":"batch.completed"}'
HEADERS = {
    "X-SerpKite-Signature": "v1=30e487bd0bd03db9643edcae3622a5d5cd3ee3f108cd4c9130e7670f518aeed1",
    "X-SerpKite-Timestamp": "1700000000",
}


@respx.mock
def test_idempotency_key_is_sent_and_makes_5xx_retryable(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/batches").mock(
        side_effect=[
            httpx.Response(500, json=error_body("internal", "boom")),
            httpx.Response(202, json={"batches": []}),
        ]
    )
    out = SerpKite().batches.create("search", [{"q": "a"}], idempotency_key="job-42")
    assert out.batches == []
    assert route.call_count == 2
    assert all(c.request.headers["Idempotency-Key"] == "job-42" for c in route.calls)


@respx.mock
def test_without_key_5xx_is_not_retried(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/batches").mock(
        return_value=httpx.Response(500, json=error_body("internal", "boom"))
    )
    with pytest.raises(APIError):
        SerpKite().batches.create("search", [{"q": "a"}])
    assert route.call_count == 1
    assert "Idempotency-Key" not in route.calls[0].request.headers


@respx.mock
async def test_async_idempotency_key(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/batches").mock(return_value=httpx.Response(202, json={"batches": []}))
    async with AsyncSerpKite() as sk:
        await sk.batches.create("search", [{"q": "a"}], idempotency_key="k1")
    assert route.calls[0].request.headers["Idempotency-Key"] == "k1"


def test_invalid_idempotency_key() -> None:
    for bad in ["", "has space", "x" * 256]:
        with pytest.raises(ValueError):
            SerpKite().batches.create("search", [{"q": "a"}], idempotency_key=bad)


def test_verify_webhook() -> None:
    assert verify_webhook(SECRET, BODY, HEADERS, now=1700000060)
    assert verify_webhook(SECRET, BODY.encode(), {k.lower(): v for k, v in HEADERS.items()}, now=1700000000)
    assert verify_webhook(SECRET, BODY, httpx.Headers(HEADERS), now=1700000000)
    assert not verify_webhook(SECRET, BODY + " ", HEADERS, now=1700000000)
    assert not verify_webhook("whsec_other", BODY, HEADERS, now=1700000000)
    assert not verify_webhook(SECRET, BODY, HEADERS, now=1700000301)
    assert not verify_webhook(SECRET, BODY, {"X-SerpKite-Timestamp": "1700000000"}, now=1700000000)
