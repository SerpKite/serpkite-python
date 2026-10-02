from __future__ import annotations

import json

import httpx
import pytest
import respx

pytest.importorskip("crewai", reason="install serpkite[crewai] to run the CrewAI tests")

from serpkite.crewai import SerpKiteSearchTool, SerpKiteSearchToolInput

from .helpers import BASE


@respx.mock
def test_tool_returns_markdown_and_uses_defaults() -> None:
    route = respx.post(f"{BASE}/v1/news").mock(
        return_value=httpx.Response(200, text="## News\n\n1. Item", headers={"content-type": "text/markdown"})
    )
    tool = SerpKiteSearchTool(endpoint="news", country="de", num=10)
    assert tool.name == "SerpKite Google news"
    assert "News" in tool.description
    assert tool.args_schema is SerpKiteSearchToolInput

    out = tool.run(query="ki regulierung", time="week")
    assert out == "## News\n\n1. Item"
    assert json.loads(route.calls.last.request.content) == {
        "q": "ki regulierung",
        "num": 10,
        "country": "de",
        "time": "week",
        "format": "markdown",
    }


@respx.mock
def test_tool_sends_configured_engine() -> None:
    route = respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(200, text="# r", headers={"content-type": "text/markdown"})
    )
    SerpKiteSearchTool(engine="auto").run(query="espresso")
    assert json.loads(route.calls.last.request.content) == {
        "q": "espresso",
        "engine": "auto",
        "format": "markdown",
    }


def test_tool_defaults() -> None:
    tool = SerpKiteSearchTool()
    assert tool.endpoint == "search"
    assert tool.name == "SerpKite Google search"
