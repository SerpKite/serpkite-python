from __future__ import annotations

from typing import Any, Optional

import httpx

__all__ = [
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "BatchTimeoutError",
    "InsufficientCreditsError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "SerpKiteError",
]


class SerpKiteError(Exception):
    """Base class for every error raised by the SDK.

    HTTP errors carry the API's error body ``{"error": {"code", "message", "request_id"}}``:
    ``status`` is the HTTP status (``None`` for errors raised before a response arrived),
    ``code`` the machine-readable code (e.g. ``insufficient_credits``), ``message`` the
    human-readable text and ``request_id`` the id to quote to support.
    """

    status: Optional[int]
    code: str
    message: str
    request_id: Optional[str]
    response: Optional[httpx.Response]
    body: Any

    def __init__(
        self,
        status: Optional[int],
        code: str,
        message: str,
        request_id: Optional[str] = None,
        *,
        response: Optional[httpx.Response] = None,
        body: Any = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.response = response
        self.body = body
        super().__init__(self._format())

    def _format(self) -> str:
        head = f"{self.status} {self.code}" if self.status is not None else self.code
        tail = f" (request_id={self.request_id})" if self.request_id else ""
        return f"{head}: {self.message}{tail}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status={self.status!r}, code={self.code!r}, "
            f"message={self.message!r}, request_id={self.request_id!r})"
        )


class BadRequestError(SerpKiteError):
    """400: invalid parameters (e.g. an unknown field or an out-of-range value)."""


class AuthenticationError(SerpKiteError):
    """401: missing, malformed or revoked API key."""


class InsufficientCreditsError(SerpKiteError):
    """402: the account balance can't cover the request. Buy a pack at app.serpkite.com/billing."""


class PermissionDeniedError(SerpKiteError):
    """403: a spend cap or per-key monthly limit was reached, or the key may not call this endpoint."""


class NotFoundError(SerpKiteError):
    """404: e.g. an unknown or expired batch id."""


class RateLimitError(SerpKiteError):
    """429: too many requests (``rate_limited``) or the free daily cap (``daily_limit_reached``)."""

    @property
    def retry_after(self) -> Optional[float]:
        """Seconds to wait before retrying, from the ``Retry-After`` header."""
        if self.response is None:
            return None
        value = self.response.headers.get("retry-after")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None


class APIError(SerpKiteError):
    """5xx: the API or the upstream search engine failed. Failed requests are not billed.

    Upstream failures (``upstream_error``, ``upstream_blocked``, ``upstream_timeout``)
    are 503 with ``Retry-After``; the API never sends 502 or 504.
    """


class APIConnectionError(SerpKiteError):
    """The request never got a response (DNS, TLS, connection reset…)."""

    def __init__(self, message: str, *, code: str = "connection_error") -> None:
        super().__init__(None, code, message)


class APITimeoutError(APIConnectionError):
    """The request timed out."""

    def __init__(self, message: str = "request timed out") -> None:
        super().__init__(message, code="timeout")


class BatchTimeoutError(SerpKiteError, TimeoutError):
    """``batches.wait`` gave up before the batch job finished."""

    def __init__(self, batch_id: str, timeout: float) -> None:
        super().__init__(None, "batch_timeout", f"batch {batch_id} did not finish within {timeout:g}s")
        self.batch_id = batch_id


class TaskTimeoutError(SerpKiteError, TimeoutError):
    """``wait_for_crawl`` gave up before the task finished.

    The task keeps running server-side; poll it again later (results are kept 24 h).
    """

    def __init__(self, task_id: str, timeout: float) -> None:
        super().__init__(None, "task_timeout", f"task {task_id} did not finish within {timeout:g}s")
        self.task_id = task_id


def error_class(status: int) -> type[SerpKiteError]:
    if status == 400:
        return BadRequestError
    if status == 401:
        return AuthenticationError
    if status == 402:
        return InsufficientCreditsError
    if status == 403:
        return PermissionDeniedError
    if status == 404:
        return NotFoundError
    if status == 429:
        return RateLimitError
    if status >= 500:
        return APIError
    return SerpKiteError


def from_response(response: httpx.Response) -> SerpKiteError:
    """Builds the matching exception from an error response."""
    body: Any = None
    code = "http_error"
    message = response.reason_phrase or f"HTTP {response.status_code}"
    request_id = response.headers.get("x-request-id")
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()
        if text:
            message = text[:500]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        code = str(err.get("code") or code)
        message = str(err.get("message") or message)
        request_id = err.get("request_id") or request_id
    cls = error_class(response.status_code)
    return cls(response.status_code, code, message, request_id, response=response, body=body)
