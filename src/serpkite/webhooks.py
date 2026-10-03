"""Verify and parse SerpKite webhook deliveries.

Every event is signed the same way: ``batch.completed``, ``crawl.completed`` and
``monitor.results`` (the ``X-SerpKite-Event`` header names it).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, Optional, Union

from . import _models
from ._models import Batch, MonitorResultsEvent, TaskCompletedEvent

__all__ = [
    "BatchCompletedEvent",
    "WebhookPayload",
    "WebhookSignatureError",
    "parse_webhook",
    "verify_webhook",
]


class BatchCompletedEvent(Batch):
    """Body of ``batch.completed``: the finished :class:`Batch` (without ``poll_url``) plus ``event``."""

    event: str = "batch.completed"
    poll_url: Optional[str] = None  # type: ignore[assignment]


BatchCompletedEvent.model_rebuild(_types_namespace=vars(_models))

WebhookPayload = Union[TaskCompletedEvent, MonitorResultsEvent, BatchCompletedEvent]
"""What :func:`parse_webhook` returns for each event type."""


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    getter = getattr(headers, "get", None)
    value = getter(name) if getter is not None else None
    if value is None:
        for k, v in headers.items():
            if k.lower() == name.lower():
                return v
    return value


def verify_webhook(
    secret: str,
    payload: Union[str, bytes],
    headers: Mapping[str, str],
    *,
    tolerance_seconds: int = 300,
    now: Optional[float] = None,
) -> bool:
    """Check a webhook delivery (any event: batches, crawls, monitors).

    ``X-SerpKite-Signature`` must be ``v1=<hex HMAC-SHA256(secret, "<X-SerpKite-Timestamp>.<raw body>")>``
    and the timestamp within ``tolerance_seconds`` of now. Pass the raw request body exactly
    as received, not re-serialised JSON.

    >>> verify_webhook(os.environ["SERPKITE_WEBHOOK_SECRET"], request.body, request.headers)
    """
    signature = _header(headers, "X-SerpKite-Signature") or ""
    ts = _header(headers, "X-SerpKite-Timestamp") or ""
    if not secret or not signature.startswith("v1=") or not (ts.isascii() and ts.isdigit()):
        return False
    current = time.time() if now is None else now
    if abs(current - int(ts)) > tolerance_seconds:
        return False
    body = payload.encode() if isinstance(payload, str) else payload
    want = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str input.
    return hmac.compare_digest(want.encode(), signature[3:].lower().encode())


def parse_webhook(
    payload: Union[str, bytes, Mapping[str, Any]],
    headers: Optional[Mapping[str, str]] = None,
    *,
    secret: Optional[str] = None,
    tolerance_seconds: int = 300,
) -> WebhookPayload:
    """Parse a delivery body. With ``secret`` it is verified first (:func:`verify_webhook`,
    ``payload`` must then be the raw body) and :class:`WebhookSignatureError` is raised when the
    signature or timestamp is invalid; without it, verify the delivery yourself first.

    ``crawl.completed`` → :class:`TaskCompletedEvent`, ``monitor.results`` →
    :class:`MonitorResultsEvent`, ``batch.completed`` → :class:`BatchCompletedEvent` (a :class:`Batch`).
    The event comes from the ``X-SerpKite-Event`` header, or the body's ``event`` field.
    A crawl event's ``result`` is ``None`` with ``result_omitted=True`` when it was larger than
    4 MB; fetch it from ``poll_url`` (or ``get_crawl``). A monitor event's ``run_id`` (also the
    ``X-SerpKite-Delivery`` header) identifies the run: use it to deduplicate retries.
    """
    if secret is not None:
        if isinstance(payload, Mapping):
            raise TypeError("pass the raw request body (str or bytes) to verify it")
        if headers is None or not verify_webhook(
            secret, payload, headers, tolerance_seconds=tolerance_seconds
        ):
            raise WebhookSignatureError("webhook signature or timestamp is invalid")
    data: Any = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
    event = _header(headers, "X-SerpKite-Event") if headers is not None else None
    if not event and isinstance(data, dict):
        event = data.get("event")
    if event == "crawl.completed":
        return TaskCompletedEvent.model_validate(data)
    if event == "monitor.results":
        return MonitorResultsEvent.model_validate(data)
    return BatchCompletedEvent.model_validate(data)


class WebhookSignatureError(ValueError):
    """:func:`parse_webhook` with ``secret``: the delivery's signature or timestamp is invalid."""
