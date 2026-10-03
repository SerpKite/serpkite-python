"""SerpKite: the Google search API built for AI agents.

>>> from serpkite import SerpKite
>>> sk = SerpKite()  # reads SERPKITE_API_KEY
>>> res = sk.search("best espresso machine", country="us")
>>> print(res.results[0].title, res.meta.credits_used)
"""

from . import types
from ._base import DEFAULT_BASE_URL
from ._client import AsyncBatches, AsyncSerpKite, Batches, SerpKite
from ._exceptions import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    BatchTimeoutError,
    InsufficientCreditsError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    SerpKiteError,
    TaskTimeoutError,
)
from ._tasks import UNSET, AsyncMonitors, Monitors, Unset
from ._version import __version__
from .webhooks import WebhookSignatureError, parse_webhook, verify_webhook

__all__ = [
    "DEFAULT_BASE_URL",
    "UNSET",
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AsyncBatches",
    "AsyncMonitors",
    "AsyncSerpKite",
    "AuthenticationError",
    "BadRequestError",
    "BatchTimeoutError",
    "Batches",
    "InsufficientCreditsError",
    "Monitors",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "SerpKite",
    "SerpKiteError",
    "TaskTimeoutError",
    "Unset",
    "WebhookSignatureError",
    "__version__",
    "parse_webhook",
    "types",
    "verify_webhook",
]
