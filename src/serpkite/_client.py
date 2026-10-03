"""SerpKite clients: :class:`SerpKite` (sync, httpx.Client) and :class:`AsyncSerpKite` (asyncio)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Literal, Optional, TypeVar, Union, overload

import httpx
from typing_extensions import Self, Unpack

from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    Config,
    M,
    Timeout,
    clean,
    extract_wait,
    parse_body,
    retry_delay,
    retryable_exception,
    should_retry,
    vertical_body,
)
from ._exceptions import (
    APIConnectionError,
    APITimeoutError,
    BatchTimeoutError,
    TaskTimeoutError,
    from_response,
)
from ._tasks import TASK_FINAL_STATUSES, AsyncMonitors, Id, Monitors, next_poll, path_id, task_body
from .types import (
    Account,
    AutocompleteResponse,
    Batch,
    BatchCreateResponse,
    BatchEndpoint,
    CrawlTask,
    ExtractResponse,
    ImagesResponse,
    MapResponse,
    NewsResponse,
    PatentsResponse,
    PlacesResponse,
    RankParams,
    RankResponse,
    ReviewsParams,
    ReviewsResponse,
    ScholarResponse,
    SearchParams,
    SearchResponse,
    ShoppingResponse,
    SitemapMode,
    Status,
    TaskCancelResponse,
    TaskCreated,
    VideosResponse,
    WebpageResponse,
)

__all__ = ["AsyncBatches", "AsyncSerpKite", "Batches", "SerpKite"]

Format = Literal["json", "markdown", "compact"]
FINAL_STATUSES = frozenset({"done", "failed"})
T = TypeVar("T")

# Indirections so tests can skip real sleeping.
_sleep = time.sleep
_async_sleep = asyncio.sleep


def _batch_body(
    endpoint: str, requests: Sequence[Mapping[str, Any]], webhook_url: Optional[str]
) -> dict[str, Any]:
    if not requests:
        raise ValueError("requests must contain at least one request body")
    if len(requests) > 100:
        raise ValueError("a batch takes at most 100 requests; split it into several create() calls")
    return clean(
        {
            "endpoint": endpoint,
            "requests": [dict(r) for r in requests],
            "webhook_url": webhook_url,
        }
    )


def _idem_headers(key: Optional[str]) -> Optional[dict[str, str]]:
    if key is None:
        return None
    if not key or len(key) > 255 or any(not ("!" <= c <= "~") for c in key):
        raise ValueError("idempotency_key must be 1-255 printable ASCII characters without spaces")
    return {"Idempotency-Key": key}


class Batches:
    """Batch lane (half price): queue up to 100 requests, then poll or receive a webhook."""

    def __init__(self, client: SerpKite) -> None:
        self._client = client

    def create(
        self,
        endpoint: BatchEndpoint,
        requests: Sequence[Mapping[str, Any]],
        webhook_url: Optional[str] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> BatchCreateResponse:
        """Queue ``requests`` (request bodies for ``endpoint``). One entry per request, in order:
        a :class:`Batch` job or an :class:`ErrorResponse` for a rejected request.

        Without ``idempotency_key`` only 429 is retried, so a lost response never queues
        (and bills) the jobs twice. With one (sent as ``Idempotency-Key``, e.g. a UUID),
        5xx and network errors are retried too: for 24 h the server replays the first
        response for the same key and body."""
        body = _batch_body(endpoint, requests, webhook_url)
        data = self._client._json(
            "POST",
            "/v1/batches",
            body,
            idempotent=idempotency_key is not None,
            headers=_idem_headers(idempotency_key),
        )
        return BatchCreateResponse.model_validate(data)

    def get(self, id: str) -> Batch:
        """Current state of one batch job (``result`` is present once it is done; kept 24 h)."""
        return Batch.model_validate(self._client._json("GET", f"/v1/batches/{id}", None))

    def wait(self, id: str, *, timeout: float = 300.0, poll_interval: float = 2.0) -> Batch:
        """Poll until the job is ``done`` or ``failed``. Raises :class:`BatchTimeoutError`."""
        deadline = time.monotonic() + timeout
        while True:
            batch = self.get(id)
            if batch.status in FINAL_STATUSES:
                return batch
            if time.monotonic() + poll_interval > deadline:
                raise BatchTimeoutError(id, timeout)
            _sleep(poll_interval)


class AsyncBatches:
    """Async batch lane; see :class:`Batches`."""

    def __init__(self, client: AsyncSerpKite) -> None:
        self._client = client

    async def create(
        self,
        endpoint: BatchEndpoint,
        requests: Sequence[Mapping[str, Any]],
        webhook_url: Optional[str] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> BatchCreateResponse:
        """Queue ``requests`` (request bodies for ``endpoint``). One entry per request, in order.
        See :meth:`Batches.create` for ``idempotency_key``."""
        body = _batch_body(endpoint, requests, webhook_url)
        data = await self._client._json(
            "POST",
            "/v1/batches",
            body,
            idempotent=idempotency_key is not None,
            headers=_idem_headers(idempotency_key),
        )
        return BatchCreateResponse.model_validate(data)

    async def get(self, id: str) -> Batch:
        """Current state of one batch job."""
        return Batch.model_validate(await self._client._json("GET", f"/v1/batches/{id}", None))

    async def wait(self, id: str, *, timeout: float = 300.0, poll_interval: float = 2.0) -> Batch:
        """Poll until the job is ``done`` or ``failed``. Raises :class:`BatchTimeoutError`."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            batch = await self.get(id)
            if batch.status in FINAL_STATUSES:
                return batch
            if loop.time() + poll_interval > deadline:
                raise BatchTimeoutError(id, timeout)
            await _async_sleep(poll_interval)


class SerpKite:
    """Synchronous SerpKite client.

    >>> sk = SerpKite()  # reads SERPKITE_API_KEY
    >>> res = sk.search("best espresso machine", country="us")
    >>> res.results[0].title

    Args:
        api_key: API key (``skt_live_…``). Defaults to the ``SERPKITE_API_KEY`` env var.
        base_url: API origin. Defaults to ``SERPKITE_BASE_URL`` or ``https://api.serpkite.com``.
        timeout: Per-request timeout in seconds (or an ``httpx.Timeout``).
        max_retries: Retries on 429, 5xx and connection errors (exponential backoff with jitter,
            honoring ``Retry-After``).
        http_client: Your own ``httpx.Client`` (proxies, transports…). It is not closed by
            :meth:`close` unless the SDK created it.
        default_headers: Extra headers sent with every request.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.Client] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._config = Config(api_key, base_url, timeout, max_retries, default_headers)
        self._owns_client = http_client is None
        self._http = http_client if http_client is not None else httpx.Client()
        self.batches = Batches(self)
        self.monitors = Monitors(self)

    @property
    def base_url(self) -> str:
        return self._config.base_url

    @property
    def max_retries(self) -> int:
        return self._config.max_retries

    def close(self) -> None:
        """Close the underlying HTTP client (if the SDK created it)."""
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def _send(
        self,
        method: str,
        path: str,
        json: Optional[Mapping[str, Any]],
        idempotent: bool = True,
        headers: Optional[Mapping[str, str]] = None,
        min_timeout: Optional[float] = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            request = self._config.build(self._http, method, path, json, headers, min_timeout)
            try:
                response = self._http.send(request)
            except httpx.TransportError as exc:
                if attempt < self._config.max_retries and retryable_exception(exc, idempotent):
                    _sleep(retry_delay(attempt, None))
                    attempt += 1
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    raise APITimeoutError(f"request to {path} timed out") from exc
                raise APIConnectionError(f"could not reach {self._config.base_url}: {exc}") from exc
            if attempt < self._config.max_retries and should_retry(response, idempotent):
                response.close()
                _sleep(retry_delay(attempt, response))
                attempt += 1
                continue
            return response

    def _json(
        self,
        method: str,
        path: str,
        json: Optional[Mapping[str, Any]],
        idempotent: bool = True,
        headers: Optional[Mapping[str, str]] = None,
        min_timeout: Optional[float] = None,
    ) -> Any:
        return parse_body(self._send(method, path, json, idempotent, headers, min_timeout), None, None, None)

    def _vertical(
        self,
        path: str,
        model: type[M],
        base: Mapping[str, Any],
        fmt: Optional[str],
        fields: Optional[str],
    ) -> Any:
        response = self._send("POST", path, vertical_body(base, fmt, fields))
        return parse_body(response, model, fmt, fields)

    def request(self, endpoint: str, params: Mapping[str, Any]) -> Any:
        """Low-level call: POST ``params`` to ``/v1/<endpoint>`` (e.g. ``"search"``, ``"news"``).

        Returns a ``str`` when ``params["format"] == "markdown"``, otherwise the JSON body as a
        ``dict``. Errors and retries behave like the typed methods.
        """
        path = "/v1/" + endpoint.strip("/")
        fmt = params.get("format")
        response = self._send("POST", path, clean(params))
        return parse_body(response, None, fmt, None)

    @overload
    def search(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def search(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def search(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def search(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> SearchResponse: ...
    def search(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[SearchResponse, str, dict[str, Any]]:
        """Google web search: organic results, answer box, knowledge graph, people also ask,
        top stories and the local pack.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/search", SearchResponse, {"q": q, **params}, format, fields)

    @overload
    def images(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def images(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def images(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def images(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> ImagesResponse: ...
    def images(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[ImagesResponse, str, dict[str, Any]]:
        """Google Images.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/images", ImagesResponse, {"q": q, **params}, format, fields)

    @overload
    def videos(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def videos(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def videos(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def videos(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> VideosResponse: ...
    def videos(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[VideosResponse, str, dict[str, Any]]:
        """Google Videos.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/videos", VideosResponse, {"q": q, **params}, format, fields)

    @overload
    def news(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def news(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def news(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def news(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> NewsResponse: ...
    def news(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[NewsResponse, str, dict[str, Any]]:
        """Google News.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/news", NewsResponse, {"q": q, **params}, format, fields)

    @overload
    def maps(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def maps(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def maps(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def maps(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> PlacesResponse: ...
    def maps(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[PlacesResponse, str, dict[str, Any]]:
        """Google Maps search (places with coordinates). Accepts ``ll`` (``"@lat,lng,14z"``).

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/maps", PlacesResponse, {"q": q, **params}, format, fields)

    @overload
    def places(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def places(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def places(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def places(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> PlacesResponse: ...
    def places(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[PlacesResponse, str, dict[str, Any]]:
        """Google local results (places).

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/places", PlacesResponse, {"q": q, **params}, format, fields)

    @overload
    def reviews(
        self, *, format: Literal["markdown"], fields: Optional[str] = None, **params: Unpack[ReviewsParams]
    ) -> str: ...
    @overload
    def reviews(
        self, *, format: Literal["compact"], fields: Optional[str] = None, **params: Unpack[ReviewsParams]
    ) -> dict[str, Any]: ...
    @overload
    def reviews(
        self, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[ReviewsParams]
    ) -> dict[str, Any]: ...
    @overload
    def reviews(
        self,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[ReviewsParams],
    ) -> ReviewsResponse: ...
    def reviews(
        self,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[ReviewsParams],
    ) -> Union[ReviewsResponse, str, dict[str, Any]]:
        """Reviews of a place, by ``place_id``, ``cid`` or ``fid`` (1 credit per 10 reviews).

        Page with ``page_token=res.next_page_token``.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        if not (params.get("place_id") or params.get("cid") or params.get("fid")):
            raise ValueError("reviews() needs one of place_id=, cid= or fid=")
        return self._vertical("/v1/reviews", ReviewsResponse, dict(params), format, fields)

    @overload
    def shopping(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def shopping(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def shopping(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def shopping(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> ShoppingResponse: ...
    def shopping(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[ShoppingResponse, str, dict[str, Any]]:
        """Google Shopping.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/shopping", ShoppingResponse, {"q": q, **params}, format, fields)

    @overload
    def scholar(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def scholar(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def scholar(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def scholar(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> ScholarResponse: ...
    def scholar(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[ScholarResponse, str, dict[str, Any]]:
        """Google Scholar.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/scholar", ScholarResponse, {"q": q, **params}, format, fields)

    @overload
    def patents(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def patents(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def patents(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def patents(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> PatentsResponse: ...
    def patents(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[PatentsResponse, str, dict[str, Any]]:
        """Google Patents.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/patents", PatentsResponse, {"q": q, **params}, format, fields)

    @overload
    def autocomplete(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    def autocomplete(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    def autocomplete(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    def autocomplete(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> AutocompleteResponse: ...
    def autocomplete(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[AutocompleteResponse, str, dict[str, Any]]:
        """Google autocomplete suggestions (0.5 credit).

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return self._vertical("/v1/autocomplete", AutocompleteResponse, {"q": q, **params}, format, fields)

    @overload
    def webpage(
        self,
        url: str,
        *,
        format: Literal["markdown"],
        include_html: bool = False,
        include_links: bool = False,
        include_images: bool = False,
        country: Optional[str] = None,
        max_age: Optional[int] = None,
    ) -> str: ...
    @overload
    def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json"]] = None,
        include_html: bool = False,
        include_links: bool = False,
        include_images: bool = False,
        country: Optional[str] = None,
        max_age: Optional[int] = None,
    ) -> WebpageResponse: ...
    def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json", "markdown"]] = None,
        include_html: bool = False,
        include_links: bool = False,
        include_images: bool = False,
        country: Optional[str] = None,
        max_age: Optional[int] = None,
    ) -> Union[WebpageResponse, str]:
        """Fetch any public URL (HTML or PDF) and return clean Markdown plus metadata (1 credit).

        ``format="markdown"`` returns just the page Markdown as a ``str``.
        ``include_links`` / ``include_images`` add the page's outbound links and image URLs.
        ``country`` fetches through an exit in that country (geo-dependent pages).
        """
        body: dict[str, Any] = {"url": url, "country": country, "max_age": max_age}
        if include_html:
            body["include_html"] = True
        if include_links:
            body["include_links"] = True
        if include_images:
            body["include_images"] = True
        return self._vertical("/v1/webpage", WebpageResponse, body, format, None)

    def extract(
        self,
        urls: Sequence[str],
        *,
        format: Optional[Literal["markdown", "text", "html"]] = None,
        query: Optional[str] = None,
        highlights: Optional[int] = None,
        max_tokens: Optional[int] = None,
        include_links: bool = False,
        include_images: bool = False,
        max_age: Optional[int] = None,
        country: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> ExtractResponse:
        """Read up to 20 URLs (HTML or PDF) as Markdown, text or HTML in one call.

        ``query`` + ``highlights`` (1-10) add the passages of each page most relevant to the
        query (BM25). 1 credit per URL that comes back (0.5 from cache); URLs that fail are
        listed in ``failed`` and cost nothing. ``timeout`` (seconds, 1-90, default 50) reports pages
        still loading as failed (``upstream_timeout``)."""
        body = clean(
            {
                "urls": list(urls),
                "format": format,
                "query": query,
                "highlights": highlights,
                "max_tokens": max_tokens,
                "include_links": include_links or None,
                "include_images": include_images or None,
                "max_age": max_age,
                "country": country,
                "timeout": timeout,
            }
        )
        # Billed per page even when the response is lost: no retry on 5xx or a read timeout.
        raw = self._json("POST", "/v1/extract", body, idempotent=False, min_timeout=extract_wait(timeout))
        return ExtractResponse.model_validate(raw)

    def map(
        self,
        url: str,
        *,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        include_subdomains: bool = False,
        sitemap: Optional[SitemapMode] = None,
        include_paths: Optional[Sequence[str]] = None,
        exclude_paths: Optional[Sequence[str]] = None,
        ignore_query_parameters: bool = False,
    ) -> MapResponse:
        """The URLs of a site, from robots.txt sitemaps and the start page's links, optionally
        ranked and filtered by ``search``. ``include_paths`` / ``exclude_paths`` are regexes
        matched against the URL path; ``ignore_query_parameters`` folds URLs that differ only in
        their query string. URLs come back cleaned (fragments and tracking parameters dropped)
        and de-duplicated. 1 credit (free when nothing is found)."""
        body = clean(
            {
                "url": url,
                "search": search,
                "limit": limit,
                "include_subdomains": include_subdomains or None,
                "sitemap": sitemap,
                "include_paths": include_paths,
                "exclude_paths": exclude_paths,
                "ignore_query_parameters": ignore_query_parameters or None,
            }
        )
        return MapResponse.model_validate(self._json("POST", "/v1/map", body))

    def crawl(
        self,
        url: str,
        *,
        limit: Optional[int] = None,
        max_depth: Optional[int] = None,
        include_paths: Optional[Sequence[str]] = None,
        exclude_paths: Optional[Sequence[str]] = None,
        include_subdomains: bool = False,
        sitemap: Optional[SitemapMode] = None,
        query: Optional[str] = None,
        ignore_query_parameters: bool = False,
        format: Optional[Literal["markdown", "text"]] = None,
        include_links: bool = False,
        max_tokens: Optional[int] = None,
        max_age: Optional[int] = None,
        webhook_url: Optional[str] = None,
    ) -> TaskCreated:
        """Start an async crawl of one site (same host, each host's robots.txt honoured), up to
        ``limit`` pages and ``max_depth`` (0-10, default 2) link hops. ``include_paths`` /
        ``exclude_paths`` are regexes matched against the URL path. ``sitemap`` ``"include"``
        (default) also seeds the crawl with the site's sitemap URLs, ``"only"`` reads the start
        page and sitemap URLs without following links, ``"skip"`` follows links only. ``query``
        makes it best first (pages whose URL and link text match are read first);
        ``ignore_query_parameters`` treats URLs that differ only in their query string as one.

        1 credit per page read (0.5 from cache); ``limit`` (default 25, max 1000) credits are
        reserved up front and the rest refunded. Poll with :meth:`get_crawl` or
        :meth:`wait_for_crawl`, or receive the signed ``crawl.completed`` webhook. Only retried
        on 429, so a lost response never starts (and reserves) a second crawl."""
        body = task_body(
            {
                "url": url,
                "limit": limit,
                "max_depth": max_depth,
                "include_paths": include_paths,
                "exclude_paths": exclude_paths,
                "include_subdomains": include_subdomains,
                "sitemap": sitemap,
                "query": query,
                "ignore_query_parameters": ignore_query_parameters,
                "format": format,
                "include_links": include_links,
                "max_tokens": max_tokens,
                "max_age": max_age,
                "webhook_url": webhook_url,
            }
        )
        return TaskCreated.model_validate(self._json("POST", "/v1/crawl", body, idempotent=False))

    def get_crawl(self, id: Id) -> CrawlTask:
        """Current state of a crawl (``result`` once it ends; kept 24 h)."""
        return CrawlTask.model_validate(self._json("GET", f"/v1/crawl/{path_id(id)}", None))

    def cancel_crawl(self, id: Id) -> TaskCancelResponse:
        """Cancel a crawl: a queued one is refunded at once (``canceled``); a running one stops at
        its next checkpoint (``canceling``) and is charged for the pages read."""
        return TaskCancelResponse.model_validate(self._json("DELETE", f"/v1/crawl/{path_id(id)}", None))

    def wait_for_crawl(self, id: Id, *, timeout: float = 2100.0, poll_interval: float = 2.0) -> CrawlTask:
        """Poll (backing off up to 15 s) until the crawl is ``completed``, ``failed`` or
        ``canceled`` (a failed task is returned, not raised; check ``status`` and ``error``).
        Raises :class:`TaskTimeoutError` after ``timeout`` (default 35 minutes: a crawl runs for
        up to 30)."""
        return self._wait_task(self.get_crawl, id, timeout, poll_interval)

    def rank(self, q: str, domain: str, **params: Unpack[RankParams]) -> RankResponse:
        """Position of ``domain`` (subdomains match) for ``q`` in the top ``num`` results (default 100).

        ``res.position`` is ``None`` when the domain isn't in the checked results.
        """
        body = clean({"q": q, "domain": domain, **params})
        return RankResponse.model_validate(self._json("POST", "/v1/rank", body))

    def account(self) -> Account:
        """Balance, limits and this month's usage for the calling key's account."""
        return Account.model_validate(self._json("GET", "/v1/account", None))

    def status(self) -> Status:
        """Public live status: requests, success rate and latency per endpoint over the last hour."""
        return Status.model_validate(self._json("GET", "/v1/status", None))

    def _no_content(self, method: str, path: str) -> None:
        response = self._send(method, path, None)
        if response.status_code >= 400:
            raise from_response(response)

    def _wait_task(self, get: Callable[[Id], T], id: Id, timeout: float, poll_interval: float) -> T:
        deadline = time.monotonic() + timeout
        interval = poll_interval
        while True:
            task = get(id)
            if getattr(task, "status", None) in TASK_FINAL_STATUSES:
                return task
            left = deadline - time.monotonic()
            if left <= 0:
                raise TaskTimeoutError(str(id), timeout)
            _sleep(min(interval, left))
            interval = next_poll(interval)


class AsyncSerpKite:
    """Asynchronous SerpKite client (``httpx.AsyncClient``). Same surface as :class:`SerpKite`.

    >>> async with AsyncSerpKite() as sk:
    ...     res = await sk.search("best espresso machine")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.AsyncClient] = None,
        default_headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._config = Config(api_key, base_url, timeout, max_retries, default_headers)
        self._owns_client = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient()
        self.batches = AsyncBatches(self)
        self.monitors = AsyncMonitors(self)

    @property
    def base_url(self) -> str:
        return self._config.base_url

    @property
    def max_retries(self) -> int:
        return self._config.max_retries

    async def close(self) -> None:
        """Close the underlying HTTP client (if the SDK created it)."""
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.close()

    async def _send(
        self,
        method: str,
        path: str,
        json: Optional[Mapping[str, Any]],
        idempotent: bool = True,
        headers: Optional[Mapping[str, str]] = None,
        min_timeout: Optional[float] = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            request = self._config.build(self._http, method, path, json, headers, min_timeout)
            try:
                response = await self._http.send(request)
            except httpx.TransportError as exc:
                if attempt < self._config.max_retries and retryable_exception(exc, idempotent):
                    await _async_sleep(retry_delay(attempt, None))
                    attempt += 1
                    continue
                if isinstance(exc, httpx.TimeoutException):
                    raise APITimeoutError(f"request to {path} timed out") from exc
                raise APIConnectionError(f"could not reach {self._config.base_url}: {exc}") from exc
            if attempt < self._config.max_retries and should_retry(response, idempotent):
                await response.aclose()
                await _async_sleep(retry_delay(attempt, response))
                attempt += 1
                continue
            return response

    async def _json(
        self,
        method: str,
        path: str,
        json: Optional[Mapping[str, Any]],
        idempotent: bool = True,
        headers: Optional[Mapping[str, str]] = None,
        min_timeout: Optional[float] = None,
    ) -> Any:
        return parse_body(
            await self._send(method, path, json, idempotent, headers, min_timeout), None, None, None
        )

    async def _vertical(
        self,
        path: str,
        model: type[M],
        base: Mapping[str, Any],
        fmt: Optional[str],
        fields: Optional[str],
    ) -> Any:
        response = await self._send("POST", path, vertical_body(base, fmt, fields))
        return parse_body(response, model, fmt, fields)

    async def request(self, endpoint: str, params: Mapping[str, Any]) -> Any:
        """Low-level call: POST ``params`` to ``/v1/<endpoint>`` (e.g. ``"search"``, ``"news"``).

        Returns a ``str`` when ``params["format"] == "markdown"``, otherwise the JSON body as a
        ``dict``. Errors and retries behave like the typed methods.
        """
        path = "/v1/" + endpoint.strip("/")
        fmt = params.get("format")
        response = await self._send("POST", path, clean(params))
        return parse_body(response, None, fmt, None)

    @overload
    async def search(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def search(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def search(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def search(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> SearchResponse: ...
    async def search(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[SearchResponse, str, dict[str, Any]]:
        """Google web search: organic results, answer box, knowledge graph, people also ask,
        top stories and the local pack.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/search", SearchResponse, {"q": q, **params}, format, fields)

    @overload
    async def images(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def images(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def images(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def images(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> ImagesResponse: ...
    async def images(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[ImagesResponse, str, dict[str, Any]]:
        """Google Images.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/images", ImagesResponse, {"q": q, **params}, format, fields)

    @overload
    async def videos(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def videos(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def videos(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def videos(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> VideosResponse: ...
    async def videos(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[VideosResponse, str, dict[str, Any]]:
        """Google Videos.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/videos", VideosResponse, {"q": q, **params}, format, fields)

    @overload
    async def news(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def news(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def news(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def news(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> NewsResponse: ...
    async def news(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[NewsResponse, str, dict[str, Any]]:
        """Google News.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/news", NewsResponse, {"q": q, **params}, format, fields)

    @overload
    async def maps(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def maps(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def maps(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def maps(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> PlacesResponse: ...
    async def maps(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[PlacesResponse, str, dict[str, Any]]:
        """Google Maps search (places with coordinates). Accepts ``ll`` (``"@lat,lng,14z"``).

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/maps", PlacesResponse, {"q": q, **params}, format, fields)

    @overload
    async def places(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def places(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def places(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def places(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> PlacesResponse: ...
    async def places(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[PlacesResponse, str, dict[str, Any]]:
        """Google local results (places).

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/places", PlacesResponse, {"q": q, **params}, format, fields)

    @overload
    async def reviews(
        self, *, format: Literal["markdown"], fields: Optional[str] = None, **params: Unpack[ReviewsParams]
    ) -> str: ...
    @overload
    async def reviews(
        self, *, format: Literal["compact"], fields: Optional[str] = None, **params: Unpack[ReviewsParams]
    ) -> dict[str, Any]: ...
    @overload
    async def reviews(
        self, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[ReviewsParams]
    ) -> dict[str, Any]: ...
    @overload
    async def reviews(
        self,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[ReviewsParams],
    ) -> ReviewsResponse: ...
    async def reviews(
        self,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[ReviewsParams],
    ) -> Union[ReviewsResponse, str, dict[str, Any]]:
        """Reviews of a place, by ``place_id``, ``cid`` or ``fid`` (1 credit per 10 reviews).

        Page with ``page_token=res.next_page_token``.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        if not (params.get("place_id") or params.get("cid") or params.get("fid")):
            raise ValueError("reviews() needs one of place_id=, cid= or fid=")
        return await self._vertical("/v1/reviews", ReviewsResponse, dict(params), format, fields)

    @overload
    async def shopping(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def shopping(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def shopping(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def shopping(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> ShoppingResponse: ...
    async def shopping(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[ShoppingResponse, str, dict[str, Any]]:
        """Google Shopping.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/shopping", ShoppingResponse, {"q": q, **params}, format, fields)

    @overload
    async def scholar(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def scholar(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def scholar(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def scholar(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> ScholarResponse: ...
    async def scholar(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[ScholarResponse, str, dict[str, Any]]:
        """Google Scholar.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/scholar", ScholarResponse, {"q": q, **params}, format, fields)

    @overload
    async def patents(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def patents(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def patents(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def patents(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> PatentsResponse: ...
    async def patents(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[PatentsResponse, str, dict[str, Any]]:
        """Google Patents.

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical("/v1/patents", PatentsResponse, {"q": q, **params}, format, fields)

    @overload
    async def autocomplete(
        self,
        q: str,
        *,
        format: Literal["markdown"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> str: ...
    @overload
    async def autocomplete(
        self,
        q: str,
        *,
        format: Literal["compact"],
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> dict[str, Any]: ...
    @overload
    async def autocomplete(
        self, q: str, *, format: Optional[Literal["json"]] = None, fields: str, **params: Unpack[SearchParams]
    ) -> dict[str, Any]: ...
    @overload
    async def autocomplete(
        self,
        q: str,
        *,
        format: Optional[Literal["json"]] = None,
        fields: None = None,
        **params: Unpack[SearchParams],
    ) -> AutocompleteResponse: ...
    async def autocomplete(
        self,
        q: str,
        *,
        format: Optional[Format] = None,
        fields: Optional[str] = None,
        **params: Unpack[SearchParams],
    ) -> Union[AutocompleteResponse, str, dict[str, Any]]:
        """Google autocomplete suggestions (0.5 credit).

        ``format="markdown"`` returns a Markdown ``str``; ``format="compact"`` or ``fields=...``
        return a plain ``dict``; otherwise a typed model.
        """
        return await self._vertical(
            "/v1/autocomplete", AutocompleteResponse, {"q": q, **params}, format, fields
        )

    @overload
    async def webpage(
        self,
        url: str,
        *,
        format: Literal["markdown"],
        include_html: bool = False,
        include_links: bool = False,
        include_images: bool = False,
        country: Optional[str] = None,
        max_age: Optional[int] = None,
    ) -> str: ...
    @overload
    async def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json"]] = None,
        include_html: bool = False,
        include_links: bool = False,
        include_images: bool = False,
        country: Optional[str] = None,
        max_age: Optional[int] = None,
    ) -> WebpageResponse: ...
    async def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json", "markdown"]] = None,
        include_html: bool = False,
        include_links: bool = False,
        include_images: bool = False,
        country: Optional[str] = None,
        max_age: Optional[int] = None,
    ) -> Union[WebpageResponse, str]:
        """Fetch any public URL (HTML or PDF) and return clean Markdown plus metadata (1 credit).

        ``format="markdown"`` returns just the page Markdown as a ``str``.
        ``include_links`` / ``include_images`` add the page's outbound links and image URLs.
        ``country`` fetches through an exit in that country (geo-dependent pages).
        """
        body: dict[str, Any] = {"url": url, "country": country, "max_age": max_age}
        if include_html:
            body["include_html"] = True
        if include_links:
            body["include_links"] = True
        if include_images:
            body["include_images"] = True
        return await self._vertical("/v1/webpage", WebpageResponse, body, format, None)

    async def extract(
        self,
        urls: Sequence[str],
        *,
        format: Optional[Literal["markdown", "text", "html"]] = None,
        query: Optional[str] = None,
        highlights: Optional[int] = None,
        max_tokens: Optional[int] = None,
        include_links: bool = False,
        include_images: bool = False,
        max_age: Optional[int] = None,
        country: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> ExtractResponse:
        """Read up to 20 URLs (HTML or PDF) as Markdown, text or HTML in one call.

        ``query`` + ``highlights`` (1-10) add the passages of each page most relevant to the
        query (BM25). 1 credit per URL that comes back (0.5 from cache); URLs that fail are
        listed in ``failed`` and cost nothing. ``timeout`` (seconds, 1-90, default 50) reports pages
        still loading as failed (``upstream_timeout``)."""
        body = clean(
            {
                "urls": list(urls),
                "format": format,
                "query": query,
                "highlights": highlights,
                "max_tokens": max_tokens,
                "include_links": include_links or None,
                "include_images": include_images or None,
                "max_age": max_age,
                "country": country,
                "timeout": timeout,
            }
        )
        # Billed per page even when the response is lost: no retry on 5xx or a read timeout.
        raw = await self._json(
            "POST", "/v1/extract", body, idempotent=False, min_timeout=extract_wait(timeout)
        )
        return ExtractResponse.model_validate(raw)

    async def map(
        self,
        url: str,
        *,
        search: Optional[str] = None,
        limit: Optional[int] = None,
        include_subdomains: bool = False,
        sitemap: Optional[SitemapMode] = None,
        include_paths: Optional[Sequence[str]] = None,
        exclude_paths: Optional[Sequence[str]] = None,
        ignore_query_parameters: bool = False,
    ) -> MapResponse:
        """The URLs of a site, from robots.txt sitemaps and the start page's links, optionally
        ranked and filtered by ``search``. ``include_paths`` / ``exclude_paths`` are regexes
        matched against the URL path; ``ignore_query_parameters`` folds URLs that differ only in
        their query string. URLs come back cleaned (fragments and tracking parameters dropped)
        and de-duplicated. 1 credit (free when nothing is found)."""
        body = clean(
            {
                "url": url,
                "search": search,
                "limit": limit,
                "include_subdomains": include_subdomains or None,
                "sitemap": sitemap,
                "include_paths": include_paths,
                "exclude_paths": exclude_paths,
                "ignore_query_parameters": ignore_query_parameters or None,
            }
        )
        return MapResponse.model_validate(await self._json("POST", "/v1/map", body))

    async def crawl(
        self,
        url: str,
        *,
        limit: Optional[int] = None,
        max_depth: Optional[int] = None,
        include_paths: Optional[Sequence[str]] = None,
        exclude_paths: Optional[Sequence[str]] = None,
        include_subdomains: bool = False,
        sitemap: Optional[SitemapMode] = None,
        query: Optional[str] = None,
        ignore_query_parameters: bool = False,
        format: Optional[Literal["markdown", "text"]] = None,
        include_links: bool = False,
        max_tokens: Optional[int] = None,
        max_age: Optional[int] = None,
        webhook_url: Optional[str] = None,
    ) -> TaskCreated:
        """See :meth:`SerpKite.crawl`."""
        body = task_body(
            {
                "url": url,
                "limit": limit,
                "max_depth": max_depth,
                "include_paths": include_paths,
                "exclude_paths": exclude_paths,
                "include_subdomains": include_subdomains,
                "sitemap": sitemap,
                "query": query,
                "ignore_query_parameters": ignore_query_parameters,
                "format": format,
                "include_links": include_links,
                "max_tokens": max_tokens,
                "max_age": max_age,
                "webhook_url": webhook_url,
            }
        )
        return TaskCreated.model_validate(await self._json("POST", "/v1/crawl", body, idempotent=False))

    async def get_crawl(self, id: Id) -> CrawlTask:
        """Current state of a crawl (``result`` once it ends; kept 24 h)."""
        return CrawlTask.model_validate(await self._json("GET", f"/v1/crawl/{path_id(id)}", None))

    async def cancel_crawl(self, id: Id) -> TaskCancelResponse:
        """See :meth:`SerpKite.cancel_crawl`."""
        data = await self._json("DELETE", f"/v1/crawl/{path_id(id)}", None)
        return TaskCancelResponse.model_validate(data)

    async def wait_for_crawl(
        self, id: Id, *, timeout: float = 2100.0, poll_interval: float = 2.0
    ) -> CrawlTask:
        """See :meth:`SerpKite.wait_for_crawl`."""
        return await self._wait_task(self.get_crawl, id, timeout, poll_interval)

    async def rank(self, q: str, domain: str, **params: Unpack[RankParams]) -> RankResponse:
        """Position of ``domain`` (subdomains match) for ``q`` in the top ``num`` results (default 100).

        ``res.position`` is ``None`` when the domain isn't in the checked results.
        """
        body = clean({"q": q, "domain": domain, **params})
        return RankResponse.model_validate(await self._json("POST", "/v1/rank", body))

    async def account(self) -> Account:
        """Balance, limits and this month's usage for the calling key's account."""
        return Account.model_validate(await self._json("GET", "/v1/account", None))

    async def status(self) -> Status:
        """Public live status: requests, success rate and latency per endpoint over the last hour."""
        return Status.model_validate(await self._json("GET", "/v1/status", None))

    async def _no_content(self, method: str, path: str) -> None:
        response = await self._send(method, path, None)
        if response.status_code >= 400:
            raise from_response(response)

    async def _wait_task(
        self, get: Callable[[Id], Awaitable[T]], id: Id, timeout: float, poll_interval: float
    ) -> T:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        interval = poll_interval
        while True:
            task = await get(id)
            if getattr(task, "status", None) in TASK_FINAL_STATUSES:
                return task
            left = deadline - loop.time()
            if left <= 0:
                raise TaskTimeoutError(str(id), timeout)
            await _async_sleep(min(interval, left))
            interval = next_poll(interval)
