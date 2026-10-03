"""Crawl tasks, monitors and their webhooks."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
import respx

import serpkite._client as client_mod
from serpkite import (
    UNSET,
    APIError,
    AsyncSerpKite,
    NotFoundError,
    SerpKite,
    TaskTimeoutError,
    parse_webhook,
    verify_webhook,
)
from serpkite.types import (
    Batch,
    CrawlTask,
    Monitor,
    MonitorPageChange,
    MonitorResultsEvent,
    MonitorRun,
    MonitorRunList,
    TaskCompletedEvent,
    TaskCreated,
)

from .helpers import BASE

TID = "01926a3e-7b2c-7c4e-9f1a-2b3c4d5e6f70"


def sent(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)


def task(kind: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": TID,
        "kind": kind,
        "status": status,
        "created_at": "2026-10-03T12:00:00Z",
        "started_at": None,
        "completed_at": None,
        "credits_reserved": 50,
        "credits_used": 0,
        "webhook_status": None,
        "progress": None,
        "result": None,
        "error": None,
        **extra,
    }


def created(kind: str) -> dict[str, Any]:
    return {
        "id": TID,
        "kind": kind,
        "status": "queued",
        "created_at": "2026-10-03T12:00:00Z",
        "poll_url": f"{BASE}/v1/{kind}/{TID}",
        "credits_reserved": 25,
        "webhook_url": None,
    }


def monitor(**extra: Any) -> dict[str, Any]:
    return {
        "id": TID,
        "name": "agents news",
        "endpoint": "news",
        "request": {"q": "ai agents"},
        "interval_seconds": 3600,
        "webhook_url": "https://example.com/hook",
        "active": True,
        "next_run_at": "2026-10-03T13:00:00Z",
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_new_results": 0,
        "runs": 0,
        "consecutive_failures": 0,
        "credits_used": 0,
        "created_at": "2026-10-03T12:00:00Z",
        **extra,
    }


# ── crawl ──────────────────────────────────────────────────────────────────


@respx.mock
def test_crawl_wait_backs_off(sleeps: list[float]) -> None:
    create = respx.post(f"{BASE}/v1/crawl").mock(return_value=httpx.Response(202, json=created("crawl")))
    get = respx.get(f"{BASE}/v1/crawl/{TID}").mock(
        side_effect=[
            httpx.Response(200, json=task("crawl", "queued")),
            httpx.Response(200, json=task("crawl", "running", progress={"pages_done": 2, "limit": 5})),
            httpx.Response(
                200,
                json=task(
                    "crawl",
                    "completed",
                    credits_used=2,
                    result={
                        "url": "https://d.com/",
                        "pages": [{"url": "https://d.com/", "depth": 0, "markdown": "# D"}],
                        "failed": [],
                        "stats": {"pages": 1},
                    },
                ),
            ),
        ]
    )
    sk = SerpKite()
    t = sk.crawl("https://d.com/", limit=5, exclude_paths=("/b$",), include_subdomains=False)
    assert isinstance(t, TaskCreated) and t.kind == "crawl"
    assert sent(create) == {"url": "https://d.com/", "limit": 5, "exclude_paths": ["/b$"]}
    done = sk.wait_for_crawl(t.id, poll_interval=2.0)  # the UUID from the response, as is
    assert isinstance(done, CrawlTask) and done.status == "completed"
    assert done.result is not None and done.result.pages[0].markdown == "# D"
    assert get.call_count == 3
    assert sleeps == [2.0, 3.0]


@respx.mock
def test_crawl_create_not_retried_on_5xx(sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/crawl").mock(
        return_value=httpx.Response(503, json={"error": {"code": "unavailable", "message": "down"}})
    )
    with pytest.raises(APIError):
        SerpKite().crawl("https://d.com/")
    assert route.call_count == 1


@respx.mock
def test_crawl_sitemap_query_and_stats() -> None:
    create = respx.post(f"{BASE}/v1/crawl").mock(return_value=httpx.Response(202, json=created("crawl")))
    respx.get(f"{BASE}/v1/crawl/{TID}").mock(
        return_value=httpx.Response(
            200,
            json=task(
                "crawl",
                "completed",
                progress={"pages_done": 1, "pages_discovered": 40, "limit": 1000},
                result={
                    "url": "https://d.com/",
                    "pages": [{"url": "https://d.com/", "depth": 0, "markdown": "# D"}],
                    "failed": [],
                    "stats": {
                        "pages": 1,
                        "discovered": 40,
                        "sitemap_urls": 30,
                        "robots": "found",
                        "robots_blocked": 2,
                        "crawl_delay_ms": 500,
                        "stopped": "limit",
                    },
                },
            ),
        )
    )
    sk = SerpKite()
    sk.crawl(
        "https://d.com/",
        limit=1000,
        max_depth=10,
        sitemap="only",
        query="pricing plans",
        ignore_query_parameters=True,
    )
    assert sent(create) == {
        "url": "https://d.com/",
        "limit": 1000,
        "max_depth": 10,
        "sitemap": "only",
        "query": "pricing plans",
        "ignore_query_parameters": True,
    }
    sk.crawl("https://d.com/", ignore_query_parameters=False)
    assert sent(create) == {"url": "https://d.com/"}
    t = sk.get_crawl(TID)
    assert t.progress is not None and t.progress.pages_discovered == 40
    stats = t.result.stats if t.result else None
    assert stats is not None and stats.stopped == "limit" and stats.robots == "found"
    assert stats.sitemap_urls == 30 and stats.crawl_delay_ms == 500


@respx.mock
def test_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.get(f"{BASE}/v1/crawl/{TID}").mock(
        return_value=httpx.Response(200, json=task("crawl", "running"))
    )
    now = [1000.0]
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(client_mod, "_sleep", fake_sleep)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    with pytest.raises(TaskTimeoutError) as exc:
        SerpKite().wait_for_crawl(TID, timeout=6, poll_interval=2)
    assert exc.value.task_id == TID
    # Polls at 0, 2, 5 and 6: the last sleep is clamped to the time left, and the task is polled
    # once more at the deadline instead of giving up a poll early.
    assert slept == [2, 3, 1]
    assert route.call_count == 4


@respx.mock
async def test_async_crawl(sleeps: list[float]) -> None:
    respx.post(f"{BASE}/v1/crawl").mock(return_value=httpx.Response(202, json=created("crawl")))
    respx.get(f"{BASE}/v1/crawl/{TID}").mock(
        side_effect=[
            httpx.Response(200, json=task("crawl", "running")),
            httpx.Response(200, json=task("crawl", "failed", error={"code": "no_pages", "message": "x"})),
        ]
    )
    respx.delete(f"{BASE}/v1/crawl/{TID}").mock(
        return_value=httpx.Response(
            409, json={"error": {"code": "invalid_request", "message": "already finished"}}
        )
    )
    async with AsyncSerpKite() as sk:
        t = await sk.crawl("https://d.com/")
        done = await sk.wait_for_crawl(t.id, poll_interval=1.0)
        assert done.status == "failed" and done.error is not None and done.error.code == "no_pages"
        assert sleeps == [1.0]
        with pytest.raises(Exception) as exc:
            await sk.cancel_crawl(TID)
        assert getattr(exc.value, "status", None) == 409


# ── monitors ───────────────────────────────────────────────────────────────


@respx.mock
def test_monitors_crud() -> None:
    create = respx.post(f"{BASE}/v1/monitors").mock(return_value=httpx.Response(201, json=monitor()))
    respx.get(f"{BASE}/v1/monitors").mock(return_value=httpx.Response(200, json={"results": [monitor()]}))
    respx.get(f"{BASE}/v1/monitors/{TID}").mock(return_value=httpx.Response(200, json=monitor()))
    patch = respx.patch(f"{BASE}/v1/monitors/{TID}").mock(
        return_value=httpx.Response(200, json=monitor(active=False, interval_seconds=86400))
    )
    run = respx.post(f"{BASE}/v1/monitors/{TID}/run").mock(return_value=httpx.Response(202, json=monitor()))
    delete = respx.delete(f"{BASE}/v1/monitors/{TID}").mock(
        side_effect=[
            httpx.Response(204),
            httpx.Response(404, json={"error": {"code": "not_found", "message": "monitor not found"}}),
        ]
    )
    sk = SerpKite()
    m = sk.monitors.create(
        "ai agents",
        endpoint="news",
        interval="hourly",
        webhook_url="https://example.com/hook",
        name="agents news",
        country="us",
    )
    assert isinstance(m, Monitor) and m.interval_seconds == 3600
    assert sent(create) == {
        "q": "ai agents",
        "webhook_url": "https://example.com/hook",
        "endpoint": "news",
        "name": "agents news",
        "interval": "hourly",
        "country": "us",
    }
    assert len(sk.monitors.list().results) == 1
    assert sk.monitors.get(TID).name == "agents news"
    u = sk.monitors.update(TID, active=False, interval="daily")
    assert u.active is False and sent(patch) == {"interval": "daily", "active": False}
    sk.monitors.run(TID)
    assert run.called
    sk.monitors.delete(TID)
    with pytest.raises(NotFoundError):
        sk.monitors.delete(TID)
    assert delete.call_count == 2
    with pytest.raises(ValueError):
        sk.monitors.update(TID)


def run_json(**extra: Any) -> dict[str, Any]:
    return {
        "id": TID,
        "status": "ok",
        "error": None,
        "new_results": 1,
        "results": [{"title": "x", "link": "https://e.com"}],
        "credits_used": 1,
        "webhook_status": "none",
        "created_at": "2026-10-03T12:00:00Z",
        **extra,
    }


@respx.mock
def test_poll_monitor_runs_and_search_update() -> None:
    create = respx.post(f"{BASE}/v1/monitors").mock(
        return_value=httpx.Response(201, json=monitor(webhook_url=None, active=False))
    )
    patch = respx.patch(f"{BASE}/v1/monitors/{TID}").mock(return_value=httpx.Response(200, json=monitor()))
    runs = respx.get(f"{BASE}/v1/monitors/{TID}/runs").mock(
        return_value=httpx.Response(200, json={"results": [run_json()], "next_before": TID})
    )
    sk = SerpKite()
    m = sk.monitors.create("ai agents", active=False)
    assert m.webhook_url is None and m.consecutive_failures == 0
    assert sent(create) == {"q": "ai agents", "active": False}

    sk.monitors.update(TID, q="ai agent frameworks", endpoint="search", num=20, include_domains="github.com")
    assert sent(patch) == {
        "q": "ai agent frameworks",
        "endpoint": "search",
        "num": 20,
        "include_domains": "github.com",
    }
    sk.monitors.update(TID, webhook_url="")
    assert sent(patch) == {"webhook_url": ""}

    page = sk.monitors.runs(TID)
    assert isinstance(page, MonitorRunList) and str(page.next_before) == TID
    r = page.results[0]
    assert isinstance(r, MonitorRun) and r.status == "ok" and r.results and r.results[0]["title"] == "x"
    assert runs.calls[-1].request.url.query == b""
    sk.monitors.runs(TID, limit=50, before=TID)
    assert dict(runs.calls[-1].request.url.params) == {"limit": "50", "before": TID}


@respx.mock
def test_webpage_monitor_and_metadata() -> None:
    page_monitor = monitor(
        endpoint="webpage",
        request={"url": "https://example.com/pricing"},
        metadata={"team": "growth"},
    )
    create = respx.post(f"{BASE}/v1/monitors").mock(return_value=httpx.Response(201, json=page_monitor))
    patch = respx.patch(f"{BASE}/v1/monitors/{TID}").mock(return_value=httpx.Response(200, json=monitor()))
    respx.get(f"{BASE}/v1/monitors/{TID}/runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    run_json(
                        results=[
                            {
                                "url": "https://example.com/pricing",
                                "title": "Pricing",
                                "change": "changed",
                                "content_hash": "abc",
                                "markdown": "# Pricing",
                            }
                        ]
                    )
                ],
                "next_before": None,
            },
        )
    )
    sk = SerpKite()
    m = sk.monitors.create(
        endpoint="webpage", url="https://example.com/pricing", country="de", metadata={"team": "growth"}
    )
    assert m.request.url == "https://example.com/pricing" and m.metadata == {"team": "growth"}
    assert sent(create) == {
        "url": "https://example.com/pricing",
        "endpoint": "webpage",
        "metadata": {"team": "growth"},
        "country": "de",
    }
    with pytest.raises(ValueError):
        sk.monitors.create(endpoint="webpage")

    change = MonitorPageChange.model_validate(sk.monitors.runs(TID).results[0].results[0])  # type: ignore[index]
    assert change.change == "changed" and change.markdown == "# Pricing"

    sk.monitors.update(TID, metadata={"team": "seo"})
    assert sent(patch) == {"metadata": {"team": "seo"}}
    sk.monitors.update(TID, metadata=None)  # clears it: JSON null
    assert sent(patch) == {"metadata": None}
    sk.monitors.update(TID, name="n")  # metadata left out: unchanged
    assert sent(patch) == {"name": "n"}
    sk.monitors.update(TID, endpoint="webpage", url="https://example.com/blog")
    assert sent(patch) == {"endpoint": "webpage", "url": "https://example.com/blog"}
    assert repr(UNSET) == "UNSET" and not UNSET


def test_parse_webpage_monitor_webhook() -> None:
    ev = parse_webhook(
        {
            "event": "monitor.results",
            "monitor_id": TID,
            "run_id": TID,
            "name": "pricing",
            "endpoint": "webpage",
            "q": "",
            "url": "https://example.com/pricing",
            "metadata": {"team": "growth"},
            "first_run": False,
            "run_at": "2026-10-03T12:00:00Z",
            "new_results": [
                {
                    "url": "https://example.com/pricing",
                    "change": "changed",
                    "content_hash": "abc",
                    "markdown": "# Pricing",
                }
            ],
            "credits_used": 1,
        }
    )
    assert isinstance(ev, MonitorResultsEvent)
    assert ev.url == "https://example.com/pricing" and ev.metadata == {"team": "growth"}


@respx.mock
async def test_async_monitors() -> None:
    respx.post(f"{BASE}/v1/monitors").mock(return_value=httpx.Response(201, json=monitor()))
    respx.delete(f"{BASE}/v1/monitors/{TID}").mock(return_value=httpx.Response(204))
    runs = respx.get(f"{BASE}/v1/monitors/{TID}/runs").mock(
        return_value=httpx.Response(
            200, json={"results": [run_json(status="paused", results=None)], "next_before": None}
        )
    )
    async with AsyncSerpKite() as sk:
        m = await sk.monitors.create("ai agents", webhook_url="https://example.com/hook")
        assert m.endpoint == "news"
        page = await sk.monitors.runs(TID, limit=5)
        assert page.next_before is None and page.results[0].status == "paused"
        assert dict(runs.calls[-1].request.url.params) == {"limit": "5"}
        await sk.monitors.delete(TID)


# ── webhooks ───────────────────────────────────────────────────────────────


def test_parse_and_verify_task_and_monitor_webhooks() -> None:
    import hashlib
    import hmac

    body = json.dumps(
        {
            "event": "crawl.completed",
            "id": TID,
            "kind": "crawl",
            "status": "completed",
            "created_at": "2026-10-03T12:00:00Z",
            "completed_at": "2026-10-03T12:03:00Z",
            "credits_used": 50,
            "result": {"url": "https://d.com/", "pages": [], "failed": [], "stats": {"pages": 0}},
        }
    )
    ts = "1700000000"
    sig = "v1=" + hmac.new(b"whsec", f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-SerpKite-Signature": sig,
        "X-SerpKite-Timestamp": ts,
        "X-SerpKite-Event": "crawl.completed",
    }
    assert verify_webhook("whsec", body, headers, now=1700000000)
    ev = parse_webhook(body, headers)
    assert isinstance(ev, TaskCompletedEvent) and ev.credits_used == 50

    mon = parse_webhook(
        {
            "event": "monitor.results",
            "monitor_id": TID,
            "run_id": TID,
            "name": "n",
            "endpoint": "news",
            "q": "ai",
            "first_run": True,
            "run_at": "2026-10-03T12:00:00Z",
            "new_results": [{"title": "x", "link": "https://e.com"}],
            "credits_used": 1,
        }
    )
    assert isinstance(mon, MonitorResultsEvent) and mon.first_run and str(mon.run_id) == TID


def test_parse_task_webhook_with_omitted_result() -> None:
    ev = parse_webhook(
        {
            "event": "crawl.completed",
            "id": TID,
            "kind": "crawl",
            "status": "completed",
            "created_at": "2026-10-03T12:00:00Z",
            "completed_at": "2026-10-03T12:30:00Z",
            "credits_used": 1000,
            "poll_url": f"{BASE}/v1/crawl/{TID}",
            "result": None,
            "result_omitted": True,
        }
    )
    assert isinstance(ev, TaskCompletedEvent)
    assert ev.result is None and ev.result_omitted is True and ev.poll_url == f"{BASE}/v1/crawl/{TID}"


def test_parse_batch_completed_webhook() -> None:
    # The batch.completed body carries no poll_url / webhook_url (see deliver in batch.go).
    ev = parse_webhook(
        {
            "event": "batch.completed",
            "id": TID,
            "status": "done",
            "endpoint": "/v1/search",
            "created_at": "2026-10-03T12:00:00Z",
            "completed_at": "2026-10-03T12:00:05Z",
            "credits_used": 0.5,
            "result": {"results": []},
        }
    )
    assert isinstance(ev, Batch) and ev.status == "done" and ev.poll_url is None
    assert ev.event == "batch.completed"


def test_verify_webhook_rejects_non_ascii_headers() -> None:
    bad_sig = {"X-SerpKite-Signature": "v1=\u00e9", "X-SerpKite-Timestamp": "1"}
    assert not verify_webhook("whsec", "{}", bad_sig, now=1)
    bad_ts = {"X-SerpKite-Signature": "v1=ab", "X-SerpKite-Timestamp": "\u00b2"}
    assert not verify_webhook("whsec", "{}", bad_ts, now=1)


R1, R2, R3 = (f"01926a3e-7b2c-7c4e-9f1a-2b3c4d5e6f7{i}" for i in (1, 2, 3))


@respx.mock
def test_iter_runs_follows_next_before() -> None:
    runs = respx.get(f"{BASE}/v1/monitors/{TID}/runs").mock(
        side_effect=[
            httpx.Response(200, json={"results": [run_json(id=R3), run_json(id=R2)], "next_before": R2}),
            httpx.Response(200, json={"results": [run_json(id=R1)], "next_before": None}),
        ]
    )
    ids = [str(r.id) for r in SerpKite().monitors.iter_runs(TID, limit=2)]
    assert ids == [R3, R2, R1]
    assert [dict(c.request.url.params) for c in runs.calls] == [
        {"limit": "2"},
        {"limit": "2", "before": R2},
    ]


@respx.mock
def test_iter_runs_stops_when_the_caller_breaks() -> None:
    runs = respx.get(f"{BASE}/v1/monitors/{TID}/runs").mock(
        return_value=httpx.Response(
            200, json={"results": [run_json(id=R2), run_json(id=R1)], "next_before": R1}
        )
    )
    for r in SerpKite().monitors.iter_runs(TID):
        assert str(r.id) == R2
        break
    assert runs.call_count == 1


@respx.mock
async def test_async_iter_runs() -> None:
    respx.get(f"{BASE}/v1/monitors/{TID}/runs").mock(
        side_effect=[
            httpx.Response(200, json={"results": [run_json(id=R2)], "next_before": R2}),
            httpx.Response(200, json={"results": [], "next_before": R2}),
        ]
    )
    async with AsyncSerpKite() as sk:
        ids = [str(r.id) async for r in sk.monitors.iter_runs(TID)]
    assert ids == [R2]


@respx.mock
def test_ids_are_escaped_in_paths() -> None:
    route = respx.get(f"{BASE}/v1/crawl/a%2Fb").mock(
        return_value=httpx.Response(200, json=task("crawl", "queued"))
    )
    SerpKite().get_crawl("a/b")
    assert route.called
    with pytest.raises(ValueError):
        SerpKite().get_crawl("")


def test_parse_webhook_verifies_with_secret() -> None:
    import hashlib
    import hmac

    from serpkite.webhooks import WebhookSignatureError

    body = json.dumps(
        {
            "event": "crawl.completed",
            "id": TID,
            "kind": "crawl",
            "status": "canceled",
            "created_at": "2026-10-03T12:00:00Z",
            "credits_used": 0,
        }
    )
    ts = str(int(time.time()))
    sig = "v1=" + hmac.new(b"whsec", f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    headers = {"X-SerpKite-Signature": sig, "X-SerpKite-Timestamp": ts, "X-SerpKite-Event": "crawl.completed"}
    ev = parse_webhook(body, headers, secret="whsec")
    assert isinstance(ev, TaskCompletedEvent) and ev.status == "canceled"
    with pytest.raises(WebhookSignatureError):
        parse_webhook(body, {**headers, "X-SerpKite-Signature": "v1=00"}, secret="whsec")
    with pytest.raises(WebhookSignatureError):
        parse_webhook(body, None, secret="whsec")
