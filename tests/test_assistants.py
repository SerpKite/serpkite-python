"""AI assistant endpoints (/v1/chatgpt, /v1/gemini, /v1/copilot, /v1/ai_mode, /v1/brave_ai),
ai_overview on search, and AI assistant monitors and batches."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from serpkite import APIError, AsyncSerpKite, SerpKite, parse_webhook
from serpkite.types import AssistantResponse, MonitorAnswer, MonitorResultsEvent

from .helpers import BASE, error_body, meta, search_body

MID = "01926a3e-7b2c-7c4e-9f1a-2b3c4d5e6f71"


def sent(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)


def assistant_json(endpoint: str = "chatgpt", **extra: Any) -> dict[str, Any]:
    return {
        "request": {"endpoint": endpoint, "prompt": "best CRM", "country": "us", "web_search": True},
        "text": "HubSpot and Pipedrive [1].",
        "markdown": "HubSpot and Pipedrive [1](https://www.hubspot.com/).",
        "model": "gpt-5-mini",
        "sources": [
            {
                "position": 1,
                "title": "HubSpot",
                "link": "https://www.hubspot.com/",
                "domain": "hubspot.com",
                "cited": True,
            },
            {"position": 2, "link": "https://g2.com/", "domain": "g2.com", "cited": False},
        ],
        "search_queries": ["best crm for startups"],
        "products": [{"position": 1, "title": "Pipedrive", "price": "$14", "extracted_price": 14}],
        "mentions": [
            {"term": "hubspot.com", "mentioned": True, "cited": True, "sources": [1], "text_position": 0},
            {"term": "Salesforce", "mentioned": False, "cited": False, "sources": [], "text_position": -1},
        ],
        "meta": meta(credits_used=5, engine="chatgpt"),
        **extra,
    }


@respx.mock
def test_chatgpt_sends_prompt_and_types_the_answer() -> None:
    route = respx.post(f"{BASE}/v1/chatgpt").mock(return_value=httpx.Response(200, json=assistant_json()))
    res = SerpKite().chatgpt(
        "best CRM", web_search=True, country="us", track=("hubspot.com", "Salesforce"), include_html=False
    )
    assert isinstance(res, AssistantResponse)
    assert sent(route) == {
        "prompt": "best CRM",
        "web_search": True,
        "country": "us",
        "track": ["hubspot.com", "Salesforce"],
    }
    assert res.request.prompt == "best CRM"
    assert res.sources[0].cited and not res.sources[1].cited
    assert res.products[0].extracted_price == 14
    assert res.mentions is not None
    assert res.mentions[0].sources == [1] and res.mentions[1].text_position == -1


@pytest.mark.parametrize(
    ("method", "path"),
    [("gemini", "gemini"), ("copilot", "copilot"), ("ai_mode", "ai_mode"), ("brave_ai", "brave_ai")],
)
@respx.mock
def test_assistant_paths(method: str, path: str) -> None:
    route = respx.post(f"{BASE}/v1/{path}").mock(return_value=httpx.Response(200, json=assistant_json(path)))
    res = getattr(SerpKite(), method)("p", language="de", include_html=True, max_age=600)
    assert isinstance(res, AssistantResponse)
    assert sent(route) == {"prompt": "p", "language": "de", "include_html": True, "max_age": 600}


@respx.mock
def test_assistant_markdown_and_fields() -> None:
    route = respx.post(f"{BASE}/v1/gemini").mock(
        return_value=httpx.Response(200, text="HubSpot [1]", headers={"content-type": "text/markdown"})
    )
    assert SerpKite().gemini("p", format="markdown") == "HubSpot [1]"
    assert sent(route)["format"] == "markdown"

    respx.post(f"{BASE}/v1/copilot").mock(
        return_value=httpx.Response(200, json={"text": "t", "meta": meta()})
    )
    assert SerpKite().copilot("p", fields="text") == {"text": "t", "meta": meta()}


@respx.mock
def test_assistant_only_retried_on_429_and_waits_for_the_answer() -> None:
    # A lost answer is billed: no retry on 5xx (failed answers are free anyway) or a read timeout.
    route = respx.post(f"{BASE}/v1/chatgpt").mock(
        return_value=httpx.Response(503, json=error_body("upstream_blocked"), headers={"Retry-After": "0"})
    )
    with pytest.raises(APIError) as info:
        SerpKite(timeout=10).chatgpt("p")
    assert info.value.code == "upstream_blocked"
    assert route.call_count == 1
    assert route.calls[0].request.extensions["timeout"]["read"] == 120.0

    limited = respx.post(f"{BASE}/v1/brave_ai").mock(
        side_effect=[
            httpx.Response(429, json=error_body("rate_limited"), headers={"Retry-After": "0"}),
            httpx.Response(200, json=assistant_json("brave_ai")),
        ]
    )
    SerpKite().brave_ai("p")
    assert limited.call_count == 2


def test_assistant_needs_a_prompt() -> None:
    with pytest.raises(ValueError):
        SerpKite().chatgpt("")


@respx.mock
async def test_async_assistants() -> None:
    route = respx.post(f"{BASE}/v1/ai_mode").mock(
        return_value=httpx.Response(200, json=assistant_json("ai_mode"))
    )
    async with AsyncSerpKite(timeout=5) as sk:
        res = await sk.ai_mode("p", country="de", track=["acme.com"])
        assert isinstance(res, AssistantResponse)
        assert res.model == "gpt-5-mini"
    assert sent(route) == {"prompt": "p", "country": "de", "track": ["acme.com"]}
    assert route.calls[0].request.extensions["timeout"]["read"] == 120.0


@respx.mock
def test_search_ai_overview() -> None:
    overview = {
        "text": "Heat pumps move heat [1].",
        "markdown": "Heat pumps move heat [1](https://a.example/).",
        "sources": [{"position": 1, "link": "https://a.example/", "domain": "a.example", "cited": True}],
    }
    respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(200, json=search_body(ai_overview=overview))
    )
    res = SerpKite().search("heat pumps")
    assert res.ai_overview is not None
    assert res.ai_overview.sources[0].domain == "a.example"


@respx.mock
def test_ai_monitor_and_batch() -> None:
    monitor = {
        "id": MID,
        "name": "crm",
        "endpoint": "chatgpt",
        "request": {"prompt": "best CRM", "web_search": False, "track": ["hubspot.com"]},
        "interval_seconds": 86400,
        "webhook_url": None,
        "active": True,
        "next_run_at": "2026-10-28T00:00:00Z",
        "runs": 0,
        "consecutive_failures": 0,
        "credits_used": 0,
        "created_at": "2026-10-28T00:00:00Z",
    }
    create = respx.post(f"{BASE}/v1/monitors").mock(return_value=httpx.Response(201, json=monitor))
    update = respx.patch(f"{BASE}/v1/monitors/{MID}").mock(return_value=httpx.Response(200, json=monitor))
    batches = respx.post(f"{BASE}/v1/batches").mock(return_value=httpx.Response(202, json={"batches": []}))
    sk = SerpKite()
    m = sk.monitors.create(endpoint="chatgpt", prompt="best CRM", web_search=False, track=("hubspot.com",))
    assert m.endpoint == "chatgpt" and m.request.prompt == "best CRM"
    assert sent(create) == {
        "endpoint": "chatgpt",
        "prompt": "best CRM",
        "web_search": False,
        "track": ["hubspot.com"],
    }
    sk.monitors.update(MID, track=["pipedrive.com"])
    assert sent(update) == {"track": ["pipedrive.com"]}
    with pytest.raises(ValueError):
        sk.monitors.create(endpoint="gemini")

    sk.batches.create("brave_ai", [{"prompt": "a", "track": ["acme.com"]}])
    assert sent(batches) == {"endpoint": "brave_ai", "requests": [{"prompt": "a", "track": ["acme.com"]}]}


def test_ai_monitor_webhook() -> None:
    row = {
        "text": "t",
        "markdown": "m",
        "model": "gpt-5-mini",
        "sources": [{"position": 1, "link": "https://a.example/", "domain": "a.example", "cited": True}],
        "new_sources": ["https://a.example/"],
        "search_queries": [],
        "mentions": [
            {"term": "Acme", "mentioned": True, "cited": False, "sources": [], "text_position": 0.4}
        ],
    }
    body = {
        "event": "monitor.results",
        "monitor_id": MID,
        "run_id": MID,
        "name": "crm",
        "endpoint": "gemini",
        "prompt": "best CRM",
        "first_run": False,
        "run_at": "2026-10-28T00:00:00Z",
        "new_results": [row],
        "credits_used": 5,
    }
    ev = parse_webhook(body)
    assert isinstance(ev, MonitorResultsEvent)
    assert ev.prompt == "best CRM" and ev.q is None
    answer = MonitorAnswer.model_validate(ev.new_results[0])
    assert answer.new_sources == ["https://a.example/"]
    assert answer.mentions is not None and answer.mentions[0].text_position == 0.4
