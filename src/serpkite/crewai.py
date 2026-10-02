"""CrewAI integration: ``pip install "serpkite[crewai]"``.

>>> from serpkite.crewai import SerpKiteSearchTool
>>> tool = SerpKiteSearchTool()  # optional: endpoint="news", country="de", num=10
>>> agent = Agent(role="Researcher", goal="...", backstory="...", tools=[tool])

The tool returns Markdown, which is far more token-efficient for an LLM than raw JSON.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

try:
    from crewai.tools import BaseTool
except ImportError as exc:  # pragma: no cover - exercised only without crewai
    raise ImportError(
        'serpkite.crewai needs CrewAI. Install it with: pip install "serpkite[crewai]"'
    ) from exc

from ._client import SerpKite

__all__ = ["SerpKiteSearchTool", "SerpKiteSearchToolInput"]

ToolEndpoint = Literal[
    "search",
    "news",
    "images",
    "videos",
    "maps",
    "places",
    "shopping",
    "scholar",
    "patents",
    "autocomplete",
    "ai-mode",
]

_DESCRIPTIONS: dict[str, str] = {
    "search": "Search Google and get the top results (titles, links, snippets, AI Overview) as Markdown. "
    "Use it for current facts, recent events and finding sources.",
    "news": "Search Google News for recent articles (title, source, date, link) as Markdown.",
    "images": "Search Google Images and get image results with links as Markdown.",
    "videos": "Search Google Videos and get video results with links as Markdown.",
    "maps": "Search Google Maps for places (address, rating, phone, website) as Markdown.",
    "places": "Search Google local results for places (address, rating, phone) as Markdown.",
    "shopping": "Search Google Shopping for products with prices and sellers as Markdown.",
    "scholar": "Search Google Scholar for academic papers (title, authors, citations, PDF) as Markdown.",
    "patents": "Search Google Patents for patents (number, assignee, dates) as Markdown.",
    "autocomplete": "Get Google autocomplete suggestions for a partial query.",
    "ai-mode": "Ask Google AI Mode a question and get a synthesized answer with cited sources as Markdown.",
}


class SerpKiteSearchToolInput(BaseModel):
    """Arguments the agent can fill in."""

    query: str = Field(..., description="The search query.")
    num: Optional[int] = Field(
        None, description="Number of results: 10 (default) or up to 100 for a deep search."
    )
    country: Optional[str] = Field(None, description="Two-letter country code, e.g. us, de, gb.")
    language: Optional[str] = Field(None, description="Language code, e.g. en, de, fr.")
    time: Optional[Literal["hour", "day", "week", "month", "year"]] = Field(
        None, description="Only results from the last hour, day, week, month or year."
    )


class SerpKiteSearchTool(BaseTool):  # type: ignore[misc]
    """Google search for CrewAI agents, powered by SerpKite. Returns Markdown."""

    name: str = "SerpKite Google search"
    description: str = _DESCRIPTIONS["search"]
    args_schema: type[BaseModel] = SerpKiteSearchToolInput

    endpoint: ToolEndpoint = "search"
    """Which vertical to query (search, news, scholar, ...)."""
    country: Optional[str] = None
    """Default country when the agent doesn't pass one."""
    language: Optional[str] = None
    location: Optional[str] = None
    num: Optional[int] = None
    engine: Optional[Union[str, list[str]]] = None
    """Search providers allowed to answer: ``"google"`` (default), ``"auto"`` (fall back to other
    providers when Google is unavailable), one provider or a list. Set by you, not the agent."""
    api_key: Optional[str] = None
    """Defaults to the SERPKITE_API_KEY environment variable."""
    base_url: Optional[str] = None
    client: Optional[SerpKite] = None
    """A preconfigured client (overrides api_key/base_url)."""

    _client: Optional[SerpKite] = PrivateAttr(default=None)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, **data: Any) -> None:
        endpoint = str(data.get("endpoint", "search")).replace("_", "-")
        data["endpoint"] = endpoint
        if "description" not in data and endpoint in _DESCRIPTIONS:
            data["description"] = _DESCRIPTIONS[endpoint]
        if "name" not in data and endpoint != "search":
            data["name"] = f"SerpKite Google {endpoint.replace('-', ' ')}"
        super().__init__(**data)

    def _get_client(self) -> SerpKite:
        if self.client is not None:
            return self.client
        if self._client is None:
            self._client = SerpKite(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def _run(
        self,
        query: str,
        num: Optional[int] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        time: Optional[str] = None,
        **_: Any,
    ) -> str:
        body: dict[str, Any] = {
            "q": query,
            "num": num or self.num,
            "country": country or self.country,
            "language": language or self.language,
            "location": self.location,
            "time": time,
            "engine": self.engine,
        }
        body["format"] = "markdown"
        return str(self._get_client().request(self.endpoint, body))
