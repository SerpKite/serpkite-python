"""Async tasks (crawl) and monitors: request bodies, polling helpers and the monitors
namespaces shared by both clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final, Optional, Union
from urllib.parse import quote, urlencode
from uuid import UUID

from typing_extensions import Unpack

from ._base import clean
from .types import (
    Monitor,
    MonitorEndpoint,
    MonitorInterval,
    MonitorList,
    MonitorRun,
    MonitorRunList,
    MonitorSearchParams,
)

if TYPE_CHECKING:
    from ._client import AsyncSerpKite, SerpKite

__all__ = ["UNSET", "AsyncMonitors", "Id", "Monitors", "Unset"]

Id = Union[str, UUID]
"""A task, monitor or run id: the string, or the ``UUID`` the response models carry."""

TASK_FINAL_STATUSES = frozenset({"completed", "failed", "canceled"})
MAX_POLL_INTERVAL = 15.0
POLL_BACKOFF = 1.5


def next_poll(interval: float) -> float:
    return min(interval * POLL_BACKOFF, MAX_POLL_INTERVAL)


def path_id(id: Id) -> str:
    """A task or monitor id (``str`` or ``UUID``) as one URL path segment."""
    s = "" if id is None else str(id)
    if not s:
        raise ValueError("an id is required")
    return quote(s, safe="")


def str_list(value: Union[str, Sequence[str], None]) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def task_body(options: Mapping[str, Any]) -> dict[str, Any]:
    """A crawl body: ``False`` flags left out, path filters as lists, ``None`` dropped."""
    body = dict(options)
    for flag in ("include_subdomains", "include_links", "ignore_query_parameters"):
        if body.get(flag) is False:
            body.pop(flag)
    for name in ("include_paths", "exclude_paths"):
        if name in body:
            body[name] = str_list(body[name])
    return clean(body)


class Unset:
    """Type of :data:`UNSET`: an argument that was not passed (as opposed to ``None``)."""

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Final = Unset()
"""Default of arguments where ``None`` means something (``monitors.update(metadata=None)`` clears)."""


def monitor_body(q: Optional[str], url: Optional[str], fields: Mapping[str, Any]) -> dict[str, Any]:
    if not q and not url:
        raise ValueError('create() needs q (search and news monitors) or url (endpoint="webpage")')
    body = clean({"q": q, "url": url, **fields})
    if "metadata" in body:
        body["metadata"] = dict(body["metadata"])
    return body


def monitor_update_body(fields: Mapping[str, Any], metadata: Any) -> dict[str, Any]:
    body = clean(fields)
    if not isinstance(metadata, Unset):
        body["metadata"] = None if metadata is None else dict(metadata)
    if not body:
        raise ValueError(
            "update() needs at least one of name, interval, interval_seconds, webhook_url, active,"
            " metadata, endpoint, q, url or a search option"
        )
    return body


def monitor_runs_path(id: Id, limit: Optional[int], before: Optional[Id]) -> str:
    query = urlencode(clean({"limit": limit, "before": None if before is None else str(before)}))
    path = f"/v1/monitors/{path_id(id)}/runs"
    return f"{path}?{query}" if query else path


class Monitors:
    """Scheduled searches (new results) or watched pages (content changes) that report what is
    new: to a signed ``monitor.results`` webhook (when the monitor has a ``webhook_url``) and
    always in the run history (:meth:`runs`).

    Each run is billed like the request it makes (1 credit per 10 results; empty runs are free). A
    monitor pauses itself after 10 failed runs in a row; ``update(id, active=True)`` resumes it."""

    def __init__(self, client: SerpKite) -> None:
        self._client = client

    def create(
        self,
        q: Optional[str] = None,
        *,
        url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        endpoint: Optional[MonitorEndpoint] = None,
        name: Optional[str] = None,
        interval: Optional[MonitorInterval] = None,
        interval_seconds: Optional[int] = None,
        active: Optional[bool] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        **params: Unpack[MonitorSearchParams],
    ) -> Monitor:
        """Save ``q`` (``endpoint`` ``"search"`` or ``"news"``; new results among the top
        ``num``) or watch ``url`` (``endpoint="webpage"``, optional ``country`` only; 1 credit
        per check, reported when the page's content changes) to run every ``interval``
        (``hourly``/``daily``/``weekly``, default daily) or ``interval_seconds`` (≥ 3600).
        The first run is due right away (``active=False`` creates it paused). Without a
        ``webhook_url``, read new results from :meth:`runs`. ``metadata`` is your own JSON
        object (≤ 2 KB), echoed on the monitor and in its webhooks. Only retried on 429."""
        body = monitor_body(
            q,
            url,
            {
                "webhook_url": webhook_url,
                "endpoint": endpoint,
                "name": name,
                "interval": interval,
                "interval_seconds": interval_seconds,
                "active": active,
                "metadata": metadata,
                **params,
            },
        )
        return Monitor.model_validate(self._client._json("POST", "/v1/monitors", body, idempotent=False))

    def list(self) -> MonitorList:
        """Every monitor of the account."""
        return MonitorList.model_validate(self._client._json("GET", "/v1/monitors", None))

    def get(self, id: Id) -> Monitor:
        return Monitor.model_validate(self._client._json("GET", f"/v1/monitors/{path_id(id)}", None))

    def update(
        self,
        id: Id,
        *,
        name: Optional[str] = None,
        interval: Optional[MonitorInterval] = None,
        interval_seconds: Optional[int] = None,
        webhook_url: Optional[str] = None,
        active: Optional[bool] = None,
        metadata: Union[Mapping[str, Any], Unset, None] = UNSET,
        endpoint: Optional[MonitorEndpoint] = None,
        q: Optional[str] = None,
        url: Optional[str] = None,
        **params: Unpack[MonitorSearchParams],
    ) -> Monitor:
        """Change the name, schedule, webhook (``webhook_url=""`` removes it), pause/resume
        (``active``; resuming makes it due now and clears the failure streak), ``metadata``
        (a mapping replaces it, ``None`` clears it; left out, it is unchanged) or the saved
        request (``endpoint``, ``q``/``url`` and search options, merged into it; switching
        between a search and ``"webpage"`` drops the other kind's fields). A changed request
        starts a new baseline: the next run reports everything as new."""
        body = monitor_update_body(
            {
                "name": name,
                "interval": interval,
                "interval_seconds": interval_seconds,
                "webhook_url": webhook_url,
                "active": active,
                "endpoint": endpoint,
                "q": q,
                "url": url,
                **params,
            },
            metadata,
        )
        return Monitor.model_validate(self._client._json("PATCH", f"/v1/monitors/{path_id(id)}", body))

    def delete(self, id: Id) -> None:
        self._client._no_content("DELETE", f"/v1/monitors/{path_id(id)}")

    def run(self, id: Id) -> Monitor:
        """Run an active monitor at the next scheduler poll (about 30 s).

        Paused monitors return 409; update(id, active=True) resumes and schedules them.
        """
        data = self._client._json("POST", f"/v1/monitors/{path_id(id)}/run", None, idempotent=False)
        return Monitor.model_validate(data)

    def runs(self, id: Id, *, limit: Optional[int] = None, before: Optional[Id] = None) -> MonitorRunList:
        """Run history, newest first: each run's status, error, credits, webhook delivery and
        its new ``results`` (kept 24 h; runs are kept 30 days). ``limit`` 1-100 (default 20);
        pass a page's ``next_before`` as ``before`` for the next one."""
        data = self._client._json("GET", monitor_runs_path(id, limit, before), None)
        return MonitorRunList.model_validate(data)

    def iter_runs(
        self, id: Id, *, limit: Optional[int] = None, before: Optional[Id] = None
    ) -> Iterator[MonitorRun]:
        """Every run of the monitor, newest first, fetching pages of ``limit`` as needed."""
        while True:
            page = self.runs(id, limit=limit, before=before)
            yield from page.results
            if not page.next_before or not page.results:
                return
            before = str(page.next_before)


class AsyncMonitors:
    """Async monitors; see :class:`Monitors`."""

    def __init__(self, client: AsyncSerpKite) -> None:
        self._client = client

    async def create(
        self,
        q: Optional[str] = None,
        *,
        url: Optional[str] = None,
        webhook_url: Optional[str] = None,
        endpoint: Optional[MonitorEndpoint] = None,
        name: Optional[str] = None,
        interval: Optional[MonitorInterval] = None,
        interval_seconds: Optional[int] = None,
        active: Optional[bool] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        **params: Unpack[MonitorSearchParams],
    ) -> Monitor:
        """See :meth:`Monitors.create`."""
        body = monitor_body(
            q,
            url,
            {
                "webhook_url": webhook_url,
                "endpoint": endpoint,
                "name": name,
                "interval": interval,
                "interval_seconds": interval_seconds,
                "active": active,
                "metadata": metadata,
                **params,
            },
        )
        data = await self._client._json("POST", "/v1/monitors", body, idempotent=False)
        return Monitor.model_validate(data)

    async def list(self) -> MonitorList:
        return MonitorList.model_validate(await self._client._json("GET", "/v1/monitors", None))

    async def get(self, id: Id) -> Monitor:
        return Monitor.model_validate(await self._client._json("GET", f"/v1/monitors/{path_id(id)}", None))

    async def update(
        self,
        id: Id,
        *,
        name: Optional[str] = None,
        interval: Optional[MonitorInterval] = None,
        interval_seconds: Optional[int] = None,
        webhook_url: Optional[str] = None,
        active: Optional[bool] = None,
        metadata: Union[Mapping[str, Any], Unset, None] = UNSET,
        endpoint: Optional[MonitorEndpoint] = None,
        q: Optional[str] = None,
        url: Optional[str] = None,
        **params: Unpack[MonitorSearchParams],
    ) -> Monitor:
        """See :meth:`Monitors.update`."""
        body = monitor_update_body(
            {
                "name": name,
                "interval": interval,
                "interval_seconds": interval_seconds,
                "webhook_url": webhook_url,
                "active": active,
                "endpoint": endpoint,
                "q": q,
                "url": url,
                **params,
            },
            metadata,
        )
        data = await self._client._json("PATCH", f"/v1/monitors/{path_id(id)}", body)
        return Monitor.model_validate(data)

    async def delete(self, id: Id) -> None:
        await self._client._no_content("DELETE", f"/v1/monitors/{path_id(id)}")

    async def run(self, id: Id) -> Monitor:
        """Run an active monitor at the next scheduler poll (about 30 s).

        Paused monitors return 409; update(id, active=True) resumes and schedules them.
        """
        data = await self._client._json("POST", f"/v1/monitors/{path_id(id)}/run", None, idempotent=False)
        return Monitor.model_validate(data)

    async def runs(
        self, id: Id, *, limit: Optional[int] = None, before: Optional[Id] = None
    ) -> MonitorRunList:
        """See :meth:`Monitors.runs`."""
        data = await self._client._json("GET", monitor_runs_path(id, limit, before), None)
        return MonitorRunList.model_validate(data)

    async def iter_runs(
        self, id: Id, *, limit: Optional[int] = None, before: Optional[Id] = None
    ) -> AsyncIterator[MonitorRun]:
        """See :meth:`Monitors.iter_runs`."""
        while True:
            page = await self.runs(id, limit=limit, before=before)
            for run in page.results:
                yield run
            if not page.next_before or not page.results:
                return
            before = str(page.next_before)
