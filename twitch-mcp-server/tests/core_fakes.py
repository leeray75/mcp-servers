"""Shared fakes for the network/identity/confirmation layer (owned by the client-layer tests).

The frozen fixtures in ``tests/twitch_fakes.py`` cover the generic surface; these helpers wire the
concrete collaborators used by ``test_twitch_client``, ``test_identity``, ``test_confirmation`` and
``test_write_safety`` (the R31/R32 dispatch mirror included). The service-layer agent may reuse
:class:`Roster` and :func:`make_client` rather than rebuilding them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from tests.twitch_fakes import FakeClock, FakeTwitch, make_settings
from twitch_mcp.config import Settings
from twitch_mcp.confirmation import ConfirmationStore
from twitch_mcp.identity import IdentityVerifier, OperatorIdentity
from twitch_mcp.ratelimit import RateLimiter
from twitch_mcp.twitch_client import Route, TwitchClient

#: Client-Id / bearer used by every client test. Values here are test constants, not secrets.
APP_ID = "app-client-id"
APP_TOKEN = "app-access-token"
USER_TOKEN = "user-access-token"


def helix(template: str, **segments: str) -> Route:
    """A validated Helix route, app-credential class. ``Route.build`` percent-encodes (R17)."""
    return Route.build(template, **segments)


def helix_user(template: str, **segments: str) -> Route:
    """A Helix route that carries the user token (the write / user-credential class)."""
    return Route.build(template, token_type="user", **segments)


def params(**named: Any) -> list[tuple[str, str]]:
    """Query pairs with list values repeated per value (R18)."""
    pairs: list[tuple[str, str]] = []
    for key, value in named.items():
        values = value if isinstance(value, (list, tuple)) else [value]
        pairs.extend((key, str(v)) for v in values)
    return pairs


class Roster:
    """A scripted moderator/editor roster for identity tests, counting upstream fetches."""

    def __init__(
        self, moderators: Mapping[str, Sequence[str]], editors: Mapping[str, Sequence[str]]
    ) -> None:
        self._moderators = {k: list(v) for k, v in moderators.items()}
        self._editors = {k: list(v) for k, v in editors.items()}
        self.calls = 0

    async def fetch(self, roster: str, broadcaster_id: str) -> Sequence[str]:
        self.calls += 1
        table = self._moderators if roster == "moderators" else self._editors
        return table.get(broadcaster_id, [])

    def verifier(
        self, operator: OperatorIdentity, cache: Any, ttl: float = 300.0
    ) -> IdentityVerifier:
        return IdentityVerifier(operator, cache, self.fetch, ttl_seconds=ttl)


def make_identity(
    operators_id: str | None,
    cache: Any,
    fclock: FakeClock,
    *,
    moderators: Mapping[str, Sequence[str]] | None = None,
    editors: Mapping[str, Sequence[str]] | None = None,
) -> tuple[IdentityVerifier, Roster]:
    """Verifier + scripted roster for identity tests."""
    roster = Roster(moderators or {}, editors or {})
    return roster.verifier(OperatorIdentity(operators_id), cache), roster


def make_client(
    fake: FakeTwitch,
    fclock: FakeClock,
    settings: Settings | None = None,
    *,
    limiter: RateLimiter | None = None,
) -> TwitchClient:
    """A real :class:`TwitchClient` on the fake transport and fake clock."""
    return TwitchClient(
        settings or make_settings(),
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        limiter=limiter
        or RateLimiter(max_wait_seconds=30.0, clock=fclock.clock, sleep=fclock.sleep),
    )


def make_confirmation_store(fclock: FakeClock, ttl_seconds: float = 300.0) -> ConfirmationStore:
    return ConfirmationStore(ttl_seconds=ttl_seconds, clock=fclock.clock, wall_clock=fclock.unix)


def serve_json(
    fake: FakeTwitch,
    method: str,
    pattern: str,
    body: Any,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    times: int | None = None,
) -> None:
    """Register a fixed-JSON handler on the frozen FakeTwitch (its ``add`` takes a handler)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=dict(headers or {}))

    fake.add(method, pattern, handler, times)


def serve_exc(
    fake: FakeTwitch,
    method: str,
    pattern: str,
    exc: BaseException | Callable[[], BaseException],
    *,
    times: int | None = None,
) -> None:
    """Register a handler that raises — for transport-error injection."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc() if callable(exc) else exc

    fake.add(method, pattern, handler, times)


def request_json(request: httpx.Request) -> Any:
    """Decode a recorded request's JSON body."""
    if not request.content:
        return None
    return json.loads(request.content.decode("utf-8"))


def request_form(request: httpx.Request) -> dict[str, str]:
    """Decode a recorded ``application/x-www-form-urlencoded`` body."""
    from urllib.parse import parse_qsl

    return dict(parse_qsl(request.content.decode("utf-8")))


def query_of(request: httpx.Request, name: str) -> list[str]:
    """All values recorded for one query parameter (R18 assertions)."""
    return request.url.params.get_list(name)


def path_matches(request: httpx.Request, pattern: str) -> bool:
    return re.fullmatch(pattern, request.url.path) is not None
