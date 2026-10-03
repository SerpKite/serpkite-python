"""Transport-independent pieces shared by the sync and async clients."""

from __future__ import annotations

import datetime
import email.utils
import os
import platform
import random
import time
from collections.abc import Mapping
from typing import Any, Optional, TypeVar, Union

import httpx
from pydantic import BaseModel

from ._exceptions import SerpKiteError, from_response
from ._version import __version__

DEFAULT_BASE_URL = "https://api.serpkite.com"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
API_KEY_ENV = "SERPKITE_API_KEY"
BASE_URL_ENV = "SERPKITE_BASE_URL"

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
MAX_RETRY_AFTER = 60.0

# The API sends upstream failures as 503; 502/504 stay for proxies in between.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
# 429s that won't clear by waiting a few seconds.
NON_RETRYABLE_CODES = frozenset({"daily_limit_reached"})

LIST_PARAMS = ("include_domains", "exclude_domains", "boost_domains", "include_paths", "exclude_paths")
DATE_PARAMS = ("start_date", "end_date")

M = TypeVar("M", bound=BaseModel)

Timeout = Union[float, httpx.Timeout, None]


def resolve_api_key(api_key: Optional[str]) -> str:
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
    if not key or not key.strip():
        raise SerpKiteError(
            None,
            "missing_api_key",
            f"No SerpKite API key. Pass api_key=... or set the {API_KEY_ENV} environment variable "
            "(create a key at https://app.serpkite.com/keys).",
        )
    return key.strip()


def resolve_base_url(base_url: Optional[str]) -> str:
    url = base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    return url.rstrip("/")


def user_agent() -> str:
    return f"serpkite-python/{__version__} python/{platform.python_version()}"


def clean(body: Mapping[str, Any]) -> dict[str, Any]:
    """Drops ``None`` values so server-side defaults apply.

    A non-string ``engine`` or domain-list sequence (tuple, list…) is sent as a JSON array;
    ``datetime.date`` values of ``start_date`` / ``end_date`` as ``YYYY-MM-DD``.
    """
    out = {k: v for k, v in body.items() if v is not None}
    for name in ("engine", *LIST_PARAMS):
        value = out.get(name)
        if value is not None and not isinstance(value, str):
            out[name] = list(value)
    for name in DATE_PARAMS:
        value = out.get(name)
        if isinstance(value, datetime.date):
            out[name] = value.isoformat()[:10]
    return out


def retry_delay(attempt: int, response: Optional[httpx.Response]) -> float:
    """Seconds to wait before retry number ``attempt`` (0-based).

    Honors ``Retry-After`` (seconds or an HTTP date), otherwise exponential backoff with jitter.
    """
    if response is not None:
        after = parse_retry_after(response.headers.get("retry-after"))
        if after is not None:
            return min(after, MAX_RETRY_AFTER)
    base = min(INITIAL_RETRY_DELAY * 2.0**attempt, MAX_RETRY_DELAY)
    return base * (0.75 + random.random() * 0.5)


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(when.timestamp() - time.time(), 0.0)


def error_code(response: httpx.Response) -> Optional[str]:
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        return str(code) if code is not None else None
    return None


def should_retry(response: httpx.Response, idempotent: bool) -> bool:
    status = response.status_code
    if status not in RETRY_STATUSES:
        return False
    if status == 429:
        return error_code(response) not in NON_RETRYABLE_CODES
    # A 5xx on a non-idempotent call (batch creation) may already have been applied.
    return idempotent


def retryable_exception(exc: httpx.TransportError, idempotent: bool) -> bool:
    if idempotent:
        return True
    # The request provably never reached the server.
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


def parse_body(
    response: httpx.Response,
    model: Optional[type[M]],
    fmt: Optional[str],
    fields: Optional[str],
) -> Any:
    """Turns a 2xx response into a str (Markdown), a dict (compact / projected) or a model."""
    if response.status_code >= 400:
        raise from_response(response)
    ctype = response.headers.get("content-type", "")
    if fmt == "markdown" or ctype.startswith("text/"):
        return response.text
    try:
        data = response.json()
    except ValueError as exc:
        raise SerpKiteError(
            response.status_code,
            "invalid_response",
            "expected a JSON response",
            response.headers.get("x-request-id"),
            response=response,
        ) from exc
    if model is None or fmt == "compact" or fields:
        return data
    return model.model_validate(data)


class Config:
    """Resolved client options."""

    __slots__ = ("api_key", "base_url", "headers", "max_retries", "timeout")

    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: Timeout,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.api_key = resolve_api_key(api_key)
        self.base_url = resolve_base_url(base_url)
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/markdown",
            "User-Agent": user_agent(),
            **(default_headers or {}),
        }

    def build(
        self,
        client: Union[httpx.Client, httpx.AsyncClient],
        method: str,
        path: str,
        json: Optional[Mapping[str, Any]],
        headers: Optional[Mapping[str, str]] = None,
        min_timeout: Optional[float] = None,
    ) -> httpx.Request:
        return client.build_request(
            method,
            self.base_url + path,
            json=dict(json) if json is not None else None,
            headers={**self.headers, **headers} if headers else self.headers,
            timeout=raise_timeout(self.timeout, min_timeout),
        )


def raise_timeout(timeout: Timeout, at_least: Optional[float]) -> Timeout:
    """``timeout`` raised to at least ``at_least`` seconds (``None`` stays disabled)."""
    if at_least is None or timeout is None:
        return timeout
    if isinstance(timeout, httpx.Timeout):
        read = timeout.read
        return timeout if read is None or read >= at_least else httpx.Timeout(timeout, read=at_least)
    return max(float(timeout), at_least)


def extract_wait(timeout: Optional[int]) -> float:
    """HTTP timeout for /v1/extract: the server's deadline (default 50 s) plus 15 s."""
    return float((timeout or 50) + 15)


def vertical_body(base: Mapping[str, Any], fmt: Optional[str], fields: Optional[str]) -> dict[str, Any]:
    body = dict(base)
    if fmt is not None and fmt != "json":
        body["format"] = fmt
    if fields:
        body["fields"] = fields
    return clean(body)
