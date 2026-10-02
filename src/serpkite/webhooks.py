"""Verify SerpKite batch webhook deliveries."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from typing import Optional, Union

__all__ = ["verify_webhook"]


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
    """Check a batch webhook delivery.

    ``X-SerpKite-Signature`` must be ``v1=<hex HMAC-SHA256(secret, "<X-SerpKite-Timestamp>.<raw body>")>``
    and the timestamp within ``tolerance_seconds`` of now. Pass the raw request body exactly
    as received, not re-serialised JSON.

    >>> verify_webhook(os.environ["SERPKITE_WEBHOOK_SECRET"], request.body, request.headers)
    """
    signature = _header(headers, "X-SerpKite-Signature") or ""
    ts = _header(headers, "X-SerpKite-Timestamp") or ""
    if not secret or not signature.startswith("v1=") or not ts.isdigit():
        return False
    current = time.time() if now is None else now
    if abs(current - int(ts)) > tolerance_seconds:
        return False
    body = payload.encode() if isinstance(payload, str) else payload
    want = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature[3:].lower())
