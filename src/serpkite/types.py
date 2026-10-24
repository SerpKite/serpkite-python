"""Public types: response models (generated from the OpenAPI contract) and request parameter dicts.

Response models are pydantic v2 models that keep unknown fields (``extra="allow"``), so a new
API field never breaks an older client. The generated module gives nested inline objects generic
names (``Key``, ``Match``…); this module re-exports them under descriptive ones.
"""

from __future__ import annotations

import datetime
from typing import Literal, Optional, Sequence, Union

from typing_extensions import TypedDict

from ._models import (
    Account,
    AnswerBox,
    AnswerMeta,
    AnswerRequest,
    AnswerResponse,
    AnswerSource,
    AnswerStreamDelta,
    AnswerStreamDone,
    AnswerStreamResults,
    AutocompleteResponse,
    Batch,
    BatchCreateRequest,
    BatchCreateResponse,
    Citation,
    CrawlPage,
    CrawlRequest,
    CrawlResult,
    CrawlTask,
    CSEResponse,
    ExtractFailure,
    ExtractRequest,
    ExtractResponse,
    ExtractResult,
    Highlight,
    HoursInterval,
    ImageResult,
    ImagesResponse,
    KnowledgeGraph,
    LLMUsage,
    MapRequest,
    MapResponse,
    MapURL,
    Meta,
    Monitor,
    MonitorCreateRequest,
    MonitorList,
    MonitorPageChange,
    MonitorResultsEvent,
    MonitorRun,
    MonitorRunList,
    MonitorSearch,
    MonitorUpdateRequest,
    NewsResponse,
    NewsResult,
    OrganicResult,
    PageLink,
    PageMetadata,
    PatentResult,
    PatentsResponse,
    PeopleAlsoAsk,
    PlacePhoto,
    PlaceRequest,
    PlaceResult,
    PlacesResponse,
    RankRequest,
    RankResponse,
    RelatedSearch,
    RequestEcho,
    ResearchRequest,
    ResearchResult,
    ResearchSource,
    ResearchTask,
    ReviewResult,
    ReviewsRequest,
    ReviewsResponse,
    RouteStep,
    ScholarResponse,
    ScholarResult,
    SearchAnswer,
    SearchRequest,
    SearchResponse,
    ShoppingResponse,
    ShoppingResult,
    Sitelink,
    Status,
    Suggestion,
    TaskCancelResponse,
    TaskCompletedEvent,
    TaskCreated,
    TaskError,
    VideoResult,
    VideosResponse,
    WebpageRequest,
    WebpageResponse,
    WeeklyHours,
)
from ._models import Credits as AnswerCredits
from ._models import Endpoint as StatusEndpoint
from ._models import Error as ErrorResponse
from ._models import Error1 as ErrorDetail
from ._models import Error2 as BatchError
from ._models import Item as CSEItem
from ._models import Key as AccountKey
from ._models import Match as RankMatch
from ._models import Month as AccountMonth
from ._models import Progress as ResearchProgress
from ._models import Progress1 as CrawlProgress
from ._models import Response as ReviewOwnerResponse
from ._models import SearchInformation as CSESearchInformation
from ._models import Stats as ResearchStats
from ._models import Stats1 as CrawlStats
from ._models import User as ReviewUser

__all__ = [
    "Account",
    "AccountKey",
    "AccountMonth",
    "AnswerBox",
    "AnswerCredits",
    "AnswerDepth",
    "AnswerMeta",
    "AnswerParams",
    "AnswerRequest",
    "AnswerResponse",
    "AnswerSource",
    "AnswerStreamDelta",
    "AnswerStreamDone",
    "AnswerStreamEvent",
    "AnswerStreamResults",
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
    "Citation",
    "CrawlPage",
    "CrawlProgress",
    "CrawlRequest",
    "CrawlResult",
    "CrawlStats",
    "CrawlStopReason",
    "CrawlTask",
    "DateLike",
    "Device",
    "DomainList",
    "Engine",
    "ErrorDetail",
    "ErrorResponse",
    "ExtractFailure",
    "ExtractRequest",
    "ExtractResponse",
    "ExtractResult",
    "Highlight",
    "HoursInterval",
    "ImageResult",
    "ImagesResponse",
    "KnowledgeGraph",
    "LLMUsage",
    "MapRequest",
    "MapResponse",
    "MapURL",
    "Meta",
    "Monitor",
    "MonitorCreateRequest",
    "MonitorEndpoint",
    "MonitorInterval",
    "MonitorList",
    "MonitorPageChange",
    "MonitorResultsEvent",
    "MonitorRun",
    "MonitorRunList",
    "MonitorRunStatus",
    "MonitorSearch",
    "MonitorSearchParams",
    "MonitorUpdateRequest",
    "NewsResponse",
    "NewsResult",
    "OrganicResult",
    "PageLink",
    "PageMetadata",
    "PatentResult",
    "PatentsResponse",
    "PeopleAlsoAsk",
    "PlaceParams",
    "PlacePhoto",
    "PlaceRequest",
    "PlaceResult",
    "PlacesResponse",
    "Provider",
    "RankMatch",
    "RankParams",
    "RankRequest",
    "RankResponse",
    "RelatedSearch",
    "RequestEcho",
    "ResearchEffort",
    "ResearchProgress",
    "ResearchRequest",
    "ResearchResult",
    "ResearchSource",
    "ResearchStats",
    "ResearchTask",
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
    "SearchAnswer",
    "SearchParams",
    "SearchRequest",
    "SearchResponse",
    "ShoppingResponse",
    "ShoppingResult",
    "Sitelink",
    "SitemapMode",
    "Status",
    "StatusEndpoint",
    "Suggestion",
    "TaskCancelResponse",
    "TaskCompletedEvent",
    "TaskCreated",
    "TaskError",
    "TaskStatus",
    "TimeRange",
    "VideoResult",
    "VideosResponse",
    "WebhookEvent",
    "WebpageRequest",
    "WebpageResponse",
    "WeeklyHours",
]

TimeRange = Literal["hour", "day", "week", "month", "year"]
Device = Literal["desktop", "mobile"]
ReviewsSort = Literal["most_relevant", "newest", "highest_rating", "lowest_rating"]
Provider = Literal["google", "brave", "bing", "yahoo", "duckduckgo", "mojeek", "wikipedia", "yandex"]
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
SitemapMode = Literal["include", "only", "skip"]
"""How ``map`` (and ``crawl``) use the site's sitemaps: with the start page's links, only, or not."""
DomainList = Union[str, Sequence[str]]
"""Domains as a list or a comma-separated string: a host, a host with a path prefix or a TLD."""
DateLike = Union[str, datetime.date]
"""A ``YYYY-MM-DD`` string or a ``datetime.date``."""
MonitorEndpoint = Literal["search", "news", "webpage"]
"""``search`` / ``news``: new results for ``q``; ``webpage``: content changes of ``url``."""
MonitorInterval = Literal["hourly", "daily", "weekly"]
MonitorRunStatus = Literal["ok", "error", "webhook_failed", "paused"]
"""``MonitorRun.status`` values (models keep it a plain ``str``)."""
CrawlStopReason = Literal["done", "limit", "time_limit", "size_limit", "too_many_failures", "canceled"]
"""``CrawlStats.stopped`` values: why a crawl ended (models keep it a plain ``str``)."""
TaskStatus = Literal["queued", "running", "completed", "failed", "canceled"]
"""Task ``status`` values (models keep it a plain ``str``)."""
WebhookEvent = Literal["batch.completed", "crawl.completed", "research.completed", "monitor.results"]
"""``X-SerpKite-Event`` values."""
AnswerDepth = Literal["fast", "standard"]
"""``fast`` answers from the result page; ``standard`` (default) also reads the top pages."""
ResearchEffort = Literal["lite", "standard", "deep"]
"""How much research searches and reads: ``lite``, ``standard`` (default) or ``deep``, each at a
flat price (https://serpkite.com/pricing)."""
BatchEndpoint = Literal[
    "search",
    "images",
    "videos",
    "news",
    "maps",
    "places",
    "place",
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
    include_domains: Optional[DomainList]
    """Only results from these domains (search, news, images, videos): ``example.com``,
    ``github.com/org`` (path prefix) or ``.gov`` (TLD). At most 20."""
    exclude_domains: Optional[DomainList]
    """Drop results from these domains (search, news, images, videos)."""
    boost_domains: Optional[DomainList]
    """Move results from these domains to the top (search, news)."""
    start_date: Optional[DateLike]
    """Only results published on or after this date (``YYYY-MM-DD`` or a ``datetime.date``;
    search, news, images, videos). Can't be combined with ``time`` or ``tbs``."""
    end_date: Optional[DateLike]
    """Only results published on or before this date."""
    highlights: Optional[bool]
    """Return query-ranked passages of the pages read by ``include_content`` instead of the
    whole page (search only, no extra credits)."""
    include_answer: Optional[bool]
    """Add a cited AI ``answer`` (``text`` with ``[n]`` markers, ``citations``) over the results
    (search and news, at an extra price). When the model fails there is no ``answer``, the extra
    credits are refunded and ``meta.warnings`` says why."""


class AnswerParams(TypedDict, total=False):
    """Search options of :meth:`SerpKite.answer` (``q``, ``depth``, ``num_sources`` and
    ``output_schema`` are keyword arguments of their own)."""

    country: Optional[str]
    language: Optional[str]
    location: Optional[str]
    uule: Optional[str]
    time: Optional[TimeRange]
    tbs: Optional[str]
    device: Optional[Device]
    safe: Optional[Literal["active", "off"]]
    autocorrect: Optional[bool]
    max_age: Optional[int]
    """Accept a cached search up to this many seconds old."""
    engine: Optional[Engine]
    include_domains: Optional[DomainList]
    exclude_domains: Optional[DomainList]
    boost_domains: Optional[DomainList]
    start_date: Optional[DateLike]
    end_date: Optional[DateLike]


class MonitorSearchParams(TypedDict, total=False):
    """Search options a monitor saves and reruns."""

    country: Optional[str]
    language: Optional[str]
    location: Optional[str]
    time: Optional[TimeRange]
    num: Optional[int]
    device: Optional[Device]
    safe: Optional[Literal["active", "off"]]
    include_domains: Optional[DomainList]
    exclude_domains: Optional[DomainList]
    engine: Optional[Engine]


class ReviewsParams(TypedDict, total=False):
    """Reviews parameters. Pass exactly one of ``place_id``, ``cid`` or ``fid``."""

    place_id: Optional[str]
    cid: Optional[str]
    fid: Optional[str]
    country: Optional[str]
    language: Optional[str]
    sort: Optional[ReviewsSort]
    """Only ``most_relevant`` is served; other orders return 400 (Google shows them only to
    signed-in visitors)."""
    page_token: Optional[str]
    """``next_page_token`` from the previous page."""
    num: Optional[int]
    """Reviews per page (1 credit per 10); at most 10, the first 10 reviews of a place being
    all Google shows."""
    max_age: Optional[int]


class PlaceParams(TypedDict, total=False):
    """Place details parameters. Pass one of ``place_id``, ``cid`` or ``fid`` (Google; ``place_id``
    wins, then ``fid``, then ``cid``), or ``oid`` (Yandex Maps)."""

    place_id: Optional[str]
    cid: Optional[str]
    fid: Optional[str]
    oid: Optional[str]
    """Yandex Maps organisation id (``oid`` of an ``engine="yandex"`` maps result); implies
    ``engine="yandex"`` and can't be combined with Google's ids."""
    country: Optional[str]
    """Country code (ISO 3166-1 alpha-2), default ``us``: Google's regional view of the listing."""
    language: Optional[str]
    """Language of the address, categories, hours and description, default ``en``."""
    max_age: Optional[int]
    """Accept a cached result up to this many seconds old (50% of credits on a hit)."""
    engine: Optional[Engine]


class RankParams(TypedDict, total=False):
    num: Optional[Literal[10, 20, 30, 50, 100]]
    """How deep to look, default 100."""
    country: Optional[str]
    language: Optional[str]
    location: Optional[str]
    device: Optional[Device]
    max_age: Optional[int]


BatchEntry = Union[Batch, ErrorResponse]

AnswerStreamEvent = Union[AnswerStreamResults, AnswerStreamDelta, AnswerStreamDone]
"""One event of ``answer_stream``: :class:`AnswerStreamResults` first, then
:class:`AnswerStreamDelta` pieces of text, then one :class:`AnswerStreamDone`."""
