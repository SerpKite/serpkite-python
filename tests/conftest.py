from __future__ import annotations

from collections.abc import Iterator

import pytest

import serpkite._client as client_mod

from .helpers import KEY


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPKITE_API_KEY", KEY)
    monkeypatch.delenv("SERPKITE_BASE_URL", raising=False)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Records retry/poll sleeps instead of sleeping."""
    calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    async def fake_async_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(client_mod, "_sleep", fake_sleep)
    monkeypatch.setattr(client_mod, "_async_sleep", fake_async_sleep)
    yield calls
