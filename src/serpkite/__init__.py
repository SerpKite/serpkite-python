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
)
from ._version import __version__
from .webhooks import verify_webhook

__all__ = [
    "DEFAULT_BASE_URL",
    "APIConnectionError",
    "APIError",
    "APITimeoutError",
    "AsyncBatches",
    "AsyncSerpKite",
    "AuthenticationError",
    "BadRequestError",
    "BatchTimeoutError",
    "Batches",
    "InsufficientCreditsError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "SerpKite",
    "SerpKiteError",
    "__version__",
    "types",
    "verify_webhook",
]
