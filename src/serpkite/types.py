"""Public types: response models (generated from the OpenAPI contract) and request parameter dicts.

Response models are pydantic v2 models that keep unknown fields (``extra="allow"``), so a new
API field never breaks an older client. The generated module gives nested inline objects generic
names (``Key``, ``Match``…); this module re-exports them under descriptive ones.
"""

from __future__ import annotations

from typing import Literal, Optional, Sequence, Union

from typing_extensions import TypedDict

from ._models import (
    Account,
    AnswerBox,
    AutocompleteResponse,
    Batch,
    BatchCreateRequest,
    BatchCreateResponse,
    CSEResponse,
    ImageResult,
    ImagesResponse,
    KnowledgeGraph,
    Meta,
    NewsResponse,
    NewsResult,
    OrganicResult,
    PageMetadata,
    PatentResult,
    PatentsResponse,
    PeopleAlsoAsk,
    PlaceResult,
    PlacesResponse,
    RankRequest,
    RankResponse,
    RelatedSearch,
    RequestEcho,
    ReviewResult,
    ReviewsRequest,
    ReviewsResponse,
    RouteStep,
    ScholarResponse,
    ScholarResult,
    SearchRequest,
    SearchResponse,
    ShoppingResponse,
    ShoppingResult,
    Sitelink,
    Status,
    Suggestion,
    VideoResult,
    VideosResponse,
    WebpageRequest,
    WebpageResponse,
)
from ._models import Endpoint as StatusEndpoint
from ._models import Error as ErrorResponse
from ._models import Error1 as ErrorDetail
from ._models import Error2 as BatchError
from ._models import Item as CSEItem
from ._models import Key as AccountKey
from ._models import Match as RankMatch
from ._models import Month as AccountMonth
from ._models import Response as ReviewOwnerResponse
from ._models import SearchInformation as CSESearchInformation
from ._models import User as ReviewUser

__all__ = [
    "Account",
    "AccountKey",
    "AccountMonth",
    "AnswerBox",
    "AutocompleteResponse",
    "Batch",
    "BatchCreateRequest",
    "BatchCreateResponse",
    "BatchEndpoint",
    "BatchEntry",
    "BatchError",
    "CSEItem",
    "CSEResponse",
    "CSESearchInformation",
    "Device",
    "Engine",
    "ErrorDetail",
    "ErrorResponse",
    "ImageResult",
    "ImagesResponse",
    "KnowledgeGraph",
    "Meta",
    "NewsResponse",
    "NewsResult",
    "OrganicResult",
    "PageMetadata",
    "PatentResult",
    "PatentsResponse",
    "PeopleAlsoAsk",
    "PlaceResult",
    "PlacesResponse",
    "Provider",
    "RankMatch",
    "RankParams",
    "RankRequest",
    "RankResponse",
    "RelatedSearch",
    "RequestEcho",
    "ReviewOwnerResponse",
    "ReviewResult",
    "ReviewUser",
    "ReviewsParams",
    "ReviewsRequest",
    "ReviewsResponse",
    "ReviewsSort",
    "RouteOutcome",
    "RouteStep",
    "ScholarResponse",
    "ScholarResult",
    "SearchParams",
    "SearchRequest",
    "SearchResponse",
    "ShoppingResponse",
    "ShoppingResult",
    "Sitelink",
    "Status",
    "StatusEndpoint",
    "Suggestion",
    "TimeRange",
    "VideoResult",
    "VideosResponse",
    "WebpageRequest",
    "WebpageResponse",
]

TimeRange = Literal["hour", "day", "week", "month", "year"]
Device = Literal["desktop", "mobile"]
ReviewsSort = Literal["most_relevant", "newest", "highest_rating", "lowest_rating"]
Provider = Literal["google", "brave", "bing", "yahoo", "duckduckgo", "mojeek", "wikipedia"]
"""A search provider that can answer a request; ``meta.engine`` names the one that did."""
RouteOutcome = Literal[
    "ok",
    "partial",
    "empty",
    "soft_empty",
    "blocked",
    "timeout",
    "error",
    "unsupported",
    "bad_param",
    "bad_target",
    "canceled",
]
"""``RouteStep.outcome`` values (response models keep it a plain ``str`` so new values never break)."""
Engine = Union[Literal["google", "auto", "consensus"], Provider, str, Sequence[str]]
"""The ``engine`` param: ``"google"`` (default, Google only), ``"auto"`` (fall back to other enabled
providers when Google is blocked or times out), ``"consensus"`` (``search`` only: several independent
indexes in parallel, merged by URL and ranked by agreement; each result lists its ``sources`` and it
costs the sum of one page per provider that returned results), one provider (``"brave"``) or a list
of providers (``["google", "brave"]``). ``"auto"`` and ``"consensus"`` can't be combined with other
names; unknown names, or a provider that doesn't serve the endpoint, are a 400."""
BatchEndpoint = Literal[
    "search",
    "images",
    "videos",
    "news",
    "maps",
    "places",
    "reviews",
    "shopping",
    "scholar",
    "patents",
    "autocomplete",
    "webpage",
]


class SearchParams(TypedDict, total=False):
    """Optional parameters shared by the query-based verticals (``q`` is positional).

    ``None`` values are dropped, so the server default applies.
    """

    country: Optional[str]
    """Country code (ISO 3166-1 alpha-2), default ``us``."""
    language: Optional[str]
    """Interface language (e.g. ``en``, ``de``, ``pt-br``), default ``en``."""
    location: Optional[str]
    """Free-text location, e.g. ``"Austin, Texas, United States"``."""
    uule: Optional[str]
    """Pre-encoded Google location (overrides ``location``)."""
    ll: Optional[str]
    """Maps viewport ``"@lat,lng,14z"`` (maps only)."""
    num: Optional[int]
    """10, or up to 100 for the depth bundle (7 credits for 100)."""
    page: Optional[int]
    """Result page, 1-10."""
    time: Optional[TimeRange]
    """Only results from the last hour/day/week/month/year."""
    tbs: Optional[str]
    """Advanced: raw Google ``tbs`` filter (overrides ``time``)."""
    device: Optional[Device]
    safe: Optional[Literal["active", "off"]]
    autocorrect: Optional[bool]
    include_content: Optional[int]
    """Also fetch the top N (0-5) organic pages as Markdown (+1 credit each, search only)."""
    ads: Optional[bool]
    """Include sponsored results."""
    max_age: Optional[int]
    """Accept a cached result up to this many seconds old (50% of credits on a hit)."""
    engine: Optional[Engine]
    """Search providers allowed to answer: ``"google"`` (default), ``"auto"``, one provider or a list.
    ``meta.engine`` names the provider that answered; credits follow its price."""


class ReviewsParams(TypedDict, total=False):
    """Reviews parameters. Pass exactly one of ``place_id``, ``cid`` or ``fid``."""

    place_id: Optional[str]
    cid: Optional[str]
    fid: Optional[str]
    country: Optional[str]
    language: Optional[str]
    sort: Optional[ReviewsSort]
    page_token: Optional[str]
    """``next_page_token`` from the previous page."""
    num: Optional[int]
    """Reviews per page, up to 50 (1 credit per 10)."""
    max_age: Optional[int]


class RankParams(TypedDict, total=False):
    num: Optional[Literal[10, 20, 30, 50, 100]]
    """How deep to look, default 100."""
    country: Optional[str]
    language: Optional[str]
    location: Optional[str]
    device: Optional[Device]
    max_age: Optional[int]


BatchEntry = Union[Batch, ErrorResponse]
