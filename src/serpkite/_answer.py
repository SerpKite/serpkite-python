"""AI surfaces shared by both clients: ``/v1/answer`` and ``/v1/research`` request bodies and
the Server-Sent Events parser behind ``answer_stream``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any, Optional, Union

from pydantic import BaseModel, ValidationError

from ._base import clean
from ._exceptions import APIError, SerpKiteError
from .types import (
    AnswerResponse,
    AnswerStreamDelta,
    AnswerStreamDone,
    AnswerStreamEvent,
    AnswerStreamResults,
    DomainList,
)

Schema = Union[Mapping[str, Any], type[BaseModel]]
"""A JSON Schema (a ``dict``) or a pydantic model class, turned into its JSON Schema."""

STREAM_HEADERS = {"Accept": "text/event-stream, application/json"}

ANSWER_WAIT = 100.0
"""Minimum HTTP timeout of ``/v1/answer`` (seconds): the search, pages read included, runs before
the first byte, then the model writes (the API answers within Cloudflare's 100 s)."""


def schema_dict(schema: Optional[Schema]) -> Optional[dict[str, Any]]:
    """A JSON Schema mapping, or a pydantic model class turned into one."""
    if schema is None:
        return None
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    return dict(schema)


def answer_body(
    q: str,
    depth: Optional[str],
    num_sources: Optional[int],
    output_schema: Optional[Schema],
    params: Mapping[str, Any],
    *,
    stream: bool,
) -> dict[str, Any]:
    body = clean(
        {
            "q": q,
            "depth": depth,
            "num_sources": num_sources,
            "output_schema": schema_dict(output_schema),
            **params,
        }
    )
    if stream:
        body["stream"] = True
    return body


def domain_list(value: Optional[DomainList]) -> Optional[list[str]]:
    """Research takes domain lists as arrays: a comma-separated string is split."""
    if value is None:
        return None
    if isinstance(value, str):
        return [d.strip() for d in value.split(",") if d.strip()]
    return list(value)


def research_body(
    input: str,
    effort: Optional[str],
    output_schema: Optional[Schema],
    country: Optional[str],
    language: Optional[str],
    include_domains: Optional[DomainList],
    exclude_domains: Optional[DomainList],
    webhook_url: Optional[str],
) -> dict[str, Any]:
    if not input:
        raise ValueError("research() needs an input")
    return clean(
        {
            "input": input,
            "effort": effort,
            "output_schema": schema_dict(output_schema),
            "country": country,
            "language": language,
            "include_domains": domain_list(include_domains),
            "exclude_domains": domain_list(exclude_domains),
            "webhook_url": webhook_url,
        }
    )


def extract_ai_wait(timeout: Optional[int], ai: bool) -> Optional[int]:
    """The server's extract deadline: ``timeout``, else 75 s with summary/output_schema."""
    if timeout is None and ai:
        return 75
    return timeout


# ── Server-Sent Events ─────────────────────────────────────────────────────


class SSEParser:
    """Incremental ``text/event-stream`` parser: feed lines, get ``(event, data)`` pairs."""

    def __init__(self) -> None:
        self.event = "message"
        self.data: list[str] = []

    def feed(self, line: str) -> Optional[tuple[str, str]]:
        line = line.rstrip("\r")
        if line == "":
            if not self.data:
                self.event = "message"
                return None
            out = (self.event, "\n".join(self.data))
            self.event, self.data = "message", []
            return out
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            self.event = value
        elif field == "data":
            self.data.append(value)
        return None

    def flush(self) -> Optional[tuple[str, str]]:
        return self.feed("")


def stream_event(event: str, data: str) -> Optional[AnswerStreamEvent]:
    """One SSE event as a typed model. An ``error`` event raises :class:`APIError`."""
    models: dict[str, type[BaseModel]] = {
        "results": AnswerStreamResults,
        "delta": AnswerStreamDelta,
        "done": AnswerStreamDone,
    }
    if event != "error" and event not in models:
        return None  # unknown events are skipped (unparsed), so new ones never break older clients
    try:
        payload = json.loads(data)
    except ValueError as exc:
        if event == "error":
            payload = {"error": {"message": data.strip()}}
        else:
            raise SerpKiteError(
                None, "invalid_response", f"malformed {event!r} event in the answer stream"
            ) from exc
    if event == "error":
        err = payload.get("error") if isinstance(payload, dict) else None
        err = err if isinstance(err, dict) else {}
        raise APIError(
            503,  # like the TypeScript and Go SDKs: the stream ended with a 503-class error
            str(err.get("code") or "ai_unavailable"),
            str(err.get("message") or "answer generation failed"),
            err.get("request_id"),
            body=payload,
        )
    try:
        return models[event].model_validate(payload)  # type: ignore[return-value]
    except ValidationError as exc:
        raise SerpKiteError(
            None, "invalid_response", f"unexpected {event!r} event in the answer stream: {exc}"
        ) from exc


def json_as_events(data: Any) -> list[AnswerStreamEvent]:
    """A plain JSON answer (e.g. from a proxy that buffered the stream) as results + done."""
    res = AnswerResponse.model_validate(data)
    done = AnswerStreamDone.model_validate(
        {
            "answer": res.answer or "",
            "citations": [c.model_dump() for c in res.citations],
            "meta": res.meta.model_dump(),
        }
    )
    return [AnswerStreamResults(request=res.request, results=res.results), done]


def truncated_stream() -> SerpKiteError:
    """The stream closed without a ``done`` or ``error`` event (connection cut mid-answer)."""
    return SerpKiteError(None, "invalid_response", "the answer stream ended before the done event")


def iter_events(lines: Iterator[str]) -> Iterator[AnswerStreamEvent]:
    parser = SSEParser()
    for line in lines:
        got = parser.feed(line)
        if got is not None:
            ev = stream_event(*got)
            if ev is not None:
                yield ev
                if isinstance(ev, AnswerStreamDone):
                    return
    got = parser.flush()
    if got is not None:
        ev = stream_event(*got)
        if ev is not None:
            yield ev
            if isinstance(ev, AnswerStreamDone):
                return
    raise truncated_stream()


async def aiter_events(lines: AsyncIterator[str]) -> AsyncIterator[AnswerStreamEvent]:
    parser = SSEParser()
    async for line in lines:
        got = parser.feed(line)
        if got is not None:
            ev = stream_event(*got)
            if ev is not None:
                yield ev
                if isinstance(ev, AnswerStreamDone):
                    return
    got = parser.flush()
    if got is not None:
        ev = stream_event(*got)
        if ev is not None:
            yield ev
            if isinstance(ev, AnswerStreamDone):
                return
    raise truncated_stream()


__all__ = [
    "ANSWER_WAIT",
    "STREAM_HEADERS",
    "Schema",
    "aiter_events",
    "answer_body",
    "extract_ai_wait",
    "iter_events",
    "json_as_events",
    "research_body",
    "schema_dict",
]
