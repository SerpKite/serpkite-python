"""SerpKite clients: :class:`SerpKite` (sync, httpx.Client) and :class:`AsyncSerpKite` (asyncio)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Literal, Optional, Union, overload

import httpx
from typing_extensions import Self, Unpack

from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    Config,
    M,
    Timeout,
    clean,
    parse_body,
    retry_delay,
    retryable_exception,
    should_retry,
    vertical_body,
)
from ._exceptions import APIConnectionError, APITimeoutError, BatchTimeoutError
from .types import (
    Account,
    AutocompleteResponse,
    Batch,
    BatchCreateResponse,
    BatchEndpoint,
    ImagesResponse,
    LensParams,
    LensResponse,
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
    Status,
    VideosResponse,
    WebpageResponse,
)

__all__ = ["AsyncBatches", "AsyncSerpKite", "Batches", "SerpKite"]

Format = Literal["json", "markdown", "compact"]
FINAL_STATUSES = frozenset({"done", "failed"})

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
    ) -> httpx.Response:
        attempt = 0
        while True:
            request = self._config.build(self._http, method, path, json, headers)
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
    ) -> Any:
        return parse_body(self._send(method, path, json, idempotent, headers), None, None, None)

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
    def lens(self, url: str, *, fields: str, **params: Unpack[LensParams]) -> dict[str, Any]: ...
    @overload
    def lens(self, url: str, *, fields: None = None, **params: Unpack[LensParams]) -> LensResponse: ...
    def lens(
        self, url: str, *, fields: Optional[str] = None, **params: Unpack[LensParams]
    ) -> Union[LensResponse, dict[str, Any]]:
        """Google Lens visual matches for a public image URL (2 credits)."""
        return self._vertical("/v1/lens", LensResponse, {"url": url, **params}, None, fields)

    @overload
    def webpage(
        self,
        url: str,
        *,
        format: Literal["markdown"],
        include_html: bool = False,
        max_age: Optional[int] = None,
    ) -> str: ...
    @overload
    def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json"]] = None,
        include_html: bool = False,
        max_age: Optional[int] = None,
    ) -> WebpageResponse: ...
    def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json", "markdown"]] = None,
        include_html: bool = False,
        max_age: Optional[int] = None,
    ) -> Union[WebpageResponse, str]:
        """Fetch any public URL and return clean Markdown plus metadata (1 credit).

        ``format="markdown"`` returns just the page Markdown as a ``str``.
        """
        body: dict[str, Any] = {"url": url, "max_age": max_age}
        if include_html:
            body["include_html"] = True
        return self._vertical("/v1/webpage", WebpageResponse, body, format, None)

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
    ) -> httpx.Response:
        attempt = 0
        while True:
            request = self._config.build(self._http, method, path, json, headers)
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
    ) -> Any:
        return parse_body(await self._send(method, path, json, idempotent, headers), None, None, None)

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
    async def lens(self, url: str, *, fields: str, **params: Unpack[LensParams]) -> dict[str, Any]: ...
    @overload
    async def lens(self, url: str, *, fields: None = None, **params: Unpack[LensParams]) -> LensResponse: ...
    async def lens(
        self, url: str, *, fields: Optional[str] = None, **params: Unpack[LensParams]
    ) -> Union[LensResponse, dict[str, Any]]:
        """Google Lens visual matches for a public image URL (2 credits)."""
        return await self._vertical("/v1/lens", LensResponse, {"url": url, **params}, None, fields)

    @overload
    async def webpage(
        self,
        url: str,
        *,
        format: Literal["markdown"],
        include_html: bool = False,
        max_age: Optional[int] = None,
    ) -> str: ...
    @overload
    async def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json"]] = None,
        include_html: bool = False,
        max_age: Optional[int] = None,
    ) -> WebpageResponse: ...
    async def webpage(
        self,
        url: str,
        *,
        format: Optional[Literal["json", "markdown"]] = None,
        include_html: bool = False,
        max_age: Optional[int] = None,
    ) -> Union[WebpageResponse, str]:
        """Fetch any public URL and return clean Markdown plus metadata (1 credit).

        ``format="markdown"`` returns just the page Markdown as a ``str``.
        """
        body: dict[str, Any] = {"url": url, "max_age": max_age}
        if include_html:
            body["include_html"] = True
        return await self._vertical("/v1/webpage", WebpageResponse, body, format, None)

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
