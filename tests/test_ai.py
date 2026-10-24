"""AI surfaces: /v1/answer (+ streaming), include_answer, extract summary/output_schema and
research tasks."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from serpkite import APIError, AsyncSerpKite, SerpKite, SerpKiteError, TaskTimeoutError, parse_webhook
from serpkite.types import (
    AnswerResponse,
    AnswerStreamDelta,
    AnswerStreamDone,
    AnswerStreamEvent,
    AnswerStreamResults,
    CrawlProgress,
    ResearchProgress,
    ResearchResult,
    ResearchTask,
    TaskCompletedEvent,
    TaskCreated,
)

from .helpers import BASE, error_body, meta, search_body

TID = "01926a3e-7b2c-7c4e-9f1a-2b3c4d5e6f70"
CITATION = {"index": 1, "title": "RAG", "link": "https://e.com/rag", "domain": "e.com"}


def sent(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)


def answer_json(**extra: Any) -> dict[str, Any]:
    return {
        "request": {"endpoint": "answer", "engine": "google", "q": "what is rag"},
        "answer": "Retrieval-augmented generation [1].",
        "citations": [CITATION],
        "results": [CITATION],
        "meta": meta(
            credits_used=8,
            credits={"search": 4, "answer": 4},
            llm={"provider": "anthropic", "model": "claude-haiku-5-5", "input_tokens": 900},
        ),
        **extra,
    }


def sse(*events: tuple[str, dict[str, Any]], crlf: bool = False) -> httpx.Response:
    nl = "\r\n" if crlf else "\n"
    text = ": keep-alive" + nl + nl
    for name, data in events:
        text += f"event: {name}{nl}data: {json.dumps(data)}{nl}{nl}"
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/event-stream"})


RESULTS = ("results", {"request": {"endpoint": "answer", "engine": "google"}, "results": [CITATION]})
DONE = ("done", {"answer": "A [1].", "citations": [CITATION], "meta": meta(credits_used=5)})


# ── answer ─────────────────────────────────────────────────────────────────


@respx.mock
def test_answer_posts_and_types_the_response() -> None:
    route = respx.post(f"{BASE}/v1/answer").mock(return_value=httpx.Response(200, json=answer_json()))
    res = SerpKite().answer("what is rag", depth="standard", num_sources=3, include_domains=["e.com"])
    assert isinstance(res, AnswerResponse)
    assert sent(route) == {
        "q": "what is rag",
        "depth": "standard",
        "num_sources": 3,
        "include_domains": ["e.com"],
    }
    assert res.answer == "Retrieval-augmented generation [1]."
    assert res.citations[0].index == 1
    assert res.meta.credits is not None and res.meta.credits.answer == 4
    assert res.meta.llm is not None and res.meta.llm.provider == "anthropic"


@respx.mock
def test_answer_output_schema_from_a_pydantic_model() -> None:
    class Building(BaseModel):
        name: str
        height_m: float

    route = respx.post(f"{BASE}/v1/answer").mock(
        return_value=httpx.Response(
            200, json=answer_json(answer=None, output={"name": "Burj", "height_m": 828})
        )
    )
    res = SerpKite().answer("tallest building", output_schema=Building)
    body = sent(route)
    assert body["output_schema"]["properties"]["height_m"]["type"] == "number"
    assert res.answer is None and res.output == {"name": "Burj", "height_m": 828}


@respx.mock
def test_answer_ai_unavailable() -> None:
    respx.post(f"{BASE}/v1/answer").mock(
        return_value=httpx.Response(
            503, json=error_body("ai_unavailable", "AI is off"), headers={"Retry-After": "0"}
        )
    )
    with pytest.raises(APIError) as info:
        SerpKite(max_retries=0).answer("x")
    assert info.value.status == 503 and info.value.code == "ai_unavailable"


@respx.mock
def test_answer_not_retried_on_5xx_and_waits_for_the_search() -> None:
    # A 503 after the search still charged it: a retry would bill the search again.
    route = respx.post(f"{BASE}/v1/answer").mock(
        return_value=httpx.Response(503, json=error_body("ai_unavailable"), headers={"Retry-After": "0"})
    )
    with pytest.raises(APIError):
        SerpKite(timeout=10).answer("x")
    assert route.call_count == 1
    assert route.calls[0].request.extensions["timeout"]["read"] == 100.0


@respx.mock
def test_answer_stream_yields_typed_events() -> None:
    route = respx.post(f"{BASE}/v1/answer").mock(
        return_value=sse(RESULTS, ("delta", {"text": "A "}), ("delta", {"text": "[1]."}), DONE, crlf=True)
    )
    events = list(SerpKite().answer_stream("what is rag", depth="fast"))
    assert sent(route) == {"q": "what is rag", "depth": "fast", "stream": True}
    assert "text/event-stream" in route.calls[0].request.headers["accept"]
    assert [type(e) for e in events] == [
        AnswerStreamResults,
        AnswerStreamDelta,
        AnswerStreamDelta,
        AnswerStreamDone,
    ]
    assert "".join(e.text for e in events if isinstance(e, AnswerStreamDelta)) == "A [1]."
    done = events[-1]
    assert isinstance(done, AnswerStreamDone) and done.meta.credits_used == 5


@respx.mock
def test_answer_stream_skips_unknown_events_unparsed() -> None:
    body = (
        "event: ping\ndata: keep-alive\n\n"
        'event: done\ndata: {"answer":"ok","citations":[],'
        '"meta":{"request_id":"r","credits_used":1,"cached":false}}\n\n'
    )
    respx.post(f"{BASE}/v1/answer").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    events = list(SerpKite().answer_stream("x"))
    assert len(events) == 1 and isinstance(events[0], AnswerStreamDone) and events[0].answer == "ok"


@respx.mock
def test_answer_stream_error_event_raises() -> None:
    err = {"error": {"code": "ai_unavailable", "message": "answer generation failed", "request_id": "req_9"}}
    respx.post(f"{BASE}/v1/answer").mock(return_value=sse(RESULTS, ("error", err)))
    seen: list[AnswerStreamEvent] = []
    with pytest.raises(APIError) as info:
        for ev in SerpKite().answer_stream("x"):
            seen.append(ev)
    assert len(seen) == 1 and isinstance(seen[0], AnswerStreamResults)
    assert info.value.code == "ai_unavailable" and info.value.request_id == "req_9"


@respx.mock
def test_answer_stream_truncated_raises() -> None:
    respx.post(f"{BASE}/v1/answer").mock(return_value=sse(RESULTS, ("delta", {"text": "A"})))
    with pytest.raises(SerpKiteError) as info:
        list(SerpKite().answer_stream("x"))
    assert info.value.code == "invalid_response"


@respx.mock
def test_answer_stream_http_error_and_retry(sleeps: list[float]) -> None:
    bad = respx.post(f"{BASE}/v1/answer").mock(
        return_value=httpx.Response(400, json=error_body("invalid_request", "bad depth"))
    )
    with pytest.raises(SerpKiteError) as info:
        list(SerpKite().answer_stream("x"))
    assert info.value.status == 400 and bad.call_count == 1

    respx.post(f"{BASE}/v1/answer").mock(
        side_effect=[httpx.Response(503, json=error_body("ai_unavailable")), sse(DONE)]
    )
    events = list(SerpKite().answer_stream("x"))
    assert len(events) == 1 and isinstance(events[0], AnswerStreamDone)
    assert len(sleeps) == 1


@respx.mock
def test_answer_stream_json_fallback() -> None:
    respx.post(f"{BASE}/v1/answer").mock(
        return_value=httpx.Response(
            200, json=answer_json(answer=None, citations=[], meta=meta(warnings=["none"]))
        )
    )
    events = list(SerpKite().answer_stream("zzz"))
    assert isinstance(events[0], AnswerStreamResults)
    done = events[1]
    assert isinstance(done, AnswerStreamDone) and done.answer == "" and done.meta.warnings == ["none"]


@respx.mock
async def test_async_answer_and_stream() -> None:
    respx.post(f"{BASE}/v1/answer").mock(
        side_effect=[httpx.Response(200, json=answer_json()), sse(RESULTS, ("delta", {"text": "A"}), DONE)]
    )
    async with AsyncSerpKite() as sk:
        res = await sk.answer("what is rag")
        assert res.citations[0].link == "https://e.com/rag"
        kinds = [type(ev) async for ev in sk.answer_stream("what is rag")]
    assert kinds == [AnswerStreamResults, AnswerStreamDelta, AnswerStreamDone]


# ── include_answer and extract ─────────────────────────────────────────────


@respx.mock
def test_search_include_answer() -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(
            200,
            json=search_body(
                answer={"text": "Espresso [1].", "citations": [CITATION]},
                meta=meta(llm={"provider": "openai", "model": "gpt-6-luna"}),
            ),
        )
    )
    res = SerpKite().search("espresso", include_answer=True)
    assert sent(route) == {"q": "espresso", "include_answer": True}
    assert res.answer is not None and res.answer.citations[0].index == 1
    assert res.meta.llm is not None and res.meta.llm.model == "gpt-6-luna"

    respx.post(f"{BASE}/v1/news").mock(
        return_value=httpx.Response(
            200,
            json={
                "request": {"endpoint": "news", "engine": "google"},
                "results": [],
                "meta": meta(warnings=["answer failed"]),
            },
        )
    )
    news = SerpKite().news("espresso", include_answer=True)
    assert news.answer is None and news.meta.warnings == ["answer failed"]


@respx.mock
def test_extract_summary_and_output_schema() -> None:
    body = {
        "request": {"endpoint": "extract", "urls": ["https://a.com", "https://b.com"]},
        "results": [
            {"url": "https://a.com", "cached": False, "summary": "About A.", "output": {"price": 10}},
            {
                "url": "https://b.com",
                "cached": False,
                "summary": None,
                "summary_error": "failed",
                "output_error": "x",
            },
        ],
        "failed": [],
        "meta": {
            "request_id": "r",
            "credits_used": 7,
            "succeeded": 2,
            "failed": 0,
            "llm": {"provider": "anthropic"},
        },
    }
    route = respx.post(f"{BASE}/v1/extract").mock(return_value=httpx.Response(200, json=body))
    schema = {"type": "object", "properties": {"price": {"type": "number"}}}
    res = SerpKite(timeout=10).extract(
        ["https://a.com", "https://b.com"], summary=True, output_schema=schema, prompt="USD"
    )
    assert sent(route) == {
        "urls": ["https://a.com", "https://b.com"],
        "summary": True,
        "output_schema": schema,
        "prompt": "USD",
    }
    # The HTTP timeout covers the server's 75 s AI deadline + 15 s.
    assert route.calls[0].request.extensions["timeout"]["read"] == 90.0
    assert res.results[0].summary == "About A." and res.results[0].output == {"price": 10}
    assert res.results[1].summary_error == "failed"
    assert res.meta.llm is not None and res.meta.llm.provider == "anthropic"


# ── research ───────────────────────────────────────────────────────────────


def research_task(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": TID,
        "kind": "research",
        "status": status,
        "created_at": "2026-10-24T12:00:00Z",
        "credits_reserved": 75,
        "credits_used": 0,
        **extra,
    }


REPORT = {
    "input": "compare vector databases",
    "effort": "standard",
    "report": "# Report\n\npgvector [1].",
    "citations": [CITATION],
    "sources": [{**CITATION, "query": "vector databases"}],
    "queries": ["vector databases"],
    "stats": {"searches": 4, "sources": 9, "rounds": 2},
}

CREATED = {
    "id": TID,
    "kind": "research",
    "status": "queued",
    "created_at": "2026-10-24T12:00:00Z",
    "poll_url": f"{BASE}/v1/research/{TID}",
    "credits_reserved": 75,
    "webhook_url": None,
}


@respx.mock
def test_research_create_wait_and_cancel(sleeps: list[float]) -> None:
    create = respx.post(f"{BASE}/v1/research").mock(return_value=httpx.Response(202, json=CREATED))
    get = respx.get(f"{BASE}/v1/research/{TID}").mock(
        side_effect=[
            httpx.Response(
                200, json=research_task("running", progress={"round": 1, "rounds": 2, "step": "searching"})
            ),
            httpx.Response(200, json=research_task("running")),
            httpx.Response(200, json=research_task("completed", credits_used=75, result=REPORT)),
        ]
    )
    cancel = respx.delete(f"{BASE}/v1/research/{TID}").mock(
        return_value=httpx.Response(200, json={"id": TID, "status": "canceled"})
    )
    sk = SerpKite()
    t = sk.research(
        "compare vector databases",
        effort="standard",
        include_domains="github.com, postgresql.org",
        exclude_domains=("pinterest.com",),
        webhook_url="https://example.com/hook",
    )
    assert isinstance(t, TaskCreated) and t.kind == "research"
    assert sent(create) == {
        "input": "compare vector databases",
        "effort": "standard",
        "include_domains": ["github.com", "postgresql.org"],
        "exclude_domains": ["pinterest.com"],
        "webhook_url": "https://example.com/hook",
    }
    running = sk.get_research(t.id)
    assert isinstance(running, ResearchTask) and isinstance(running.progress, ResearchProgress)
    assert running.progress.step == "searching"
    done = sk.wait_research(t.id)
    assert done.status == "completed" and isinstance(done.result, ResearchResult)
    assert done.result.sources[0].query == "vector databases"
    assert get.call_count == 3 and sleeps == [3.0]
    assert sk.cancel_research(TID).status == "canceled" and cancel.call_count == 1


@respx.mock
def test_research_not_retried_on_5xx_and_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.post(f"{BASE}/v1/research").mock(
        return_value=httpx.Response(503, json=error_body("unavailable"))
    )
    with pytest.raises(APIError):
        SerpKite().research("x")
    assert route.call_count == 1
    with pytest.raises(ValueError):
        SerpKite().research("")

    respx.get(f"{BASE}/v1/research/{TID}").mock(
        return_value=httpx.Response(200, json=research_task("running"))
    )
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr("serpkite._client.time.monotonic", lambda: float(next(clock)))
    with pytest.raises(TaskTimeoutError):
        SerpKite().wait_research(TID, timeout=250)


@respx.mock
async def test_async_research() -> None:
    respx.post(f"{BASE}/v1/research").mock(return_value=httpx.Response(202, json=CREATED))
    respx.get(f"{BASE}/v1/research/{TID}").mock(
        return_value=httpx.Response(
            200, json=research_task("failed", error={"code": "ai_unavailable", "message": "x"})
        )
    )
    async with AsyncSerpKite() as sk:
        t = await sk.research("x", effort="lite")
        done = await sk.wait_research(t.id)
    assert done.status == "failed" and done.error is not None and done.error.code == "ai_unavailable"


def test_research_webhook_and_progress_aliases() -> None:
    ev = parse_webhook(
        {
            "event": "research.completed",
            "id": TID,
            "kind": "research",
            "status": "completed",
            "created_at": "2026-10-24T12:00:00Z",
            "credits_used": 50,
            "result": REPORT,
        },
        {"X-SerpKite-Event": "research.completed"},
    )
    assert isinstance(ev, TaskCompletedEvent) and ev.kind == "research"
    assert ResearchResult.model_validate(ev.result).report.startswith("# Report")
    # The generated models number nested objects; the aliases must point at the right ones.
    assert "pages_done" in CrawlProgress.model_fields and "round" in ResearchProgress.model_fields
