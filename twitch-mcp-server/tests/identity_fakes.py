"""Extra helpers for the identity tests (R29–R30) — ``tests/twitch_fakes.py`` is frozen.

The frozen :class:`~tests.twitch_fakes.FakeTwitch` matches handlers on the *full* request path, and
Helix routes sit behind the ``/helix`` prefix that ``Settings.twitch_api_base`` already carries.
:func:`serve_roster` hides that prefix so a roster test reads as the endpoint it proves:
``/moderation/moderators`` for the moderation groups, ``/channels/editors`` for the channel group.

Rosters are data, not code: a :class:`RosterService` holds ``{broadcaster_id: [user_id, ...]}`` per
roster and its handler reads the table **at request time**, so a test can revoke a role mid-flight
and the identity cache (R30) decides whether the change is even seen.

:func:`make_wiring` assembles the identity stack the way ``server.py`` does — one :class:`TTLCache`
shared with the guard's owner lookup (exactly as in ``build_wiring``), a real :class:`TwitchClient`
on the fake transport, and an :class:`IdentityVerifier` whose ``fetch_roster`` is
:func:`~twitch_mcp.identity.members_of` applied to the Helix body. The identity-TTL knob of R30 is
taken from settings, so a test that says ``identity_ttl=45`` proves the env var is the TTL.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.core_fakes import make_client
from tests.twitch_fakes import OPERATOR, FakeClock, FakeTwitch, make_settings
from twitch_mcp.cache import TTLCache
from twitch_mcp.config import Settings
from twitch_mcp.identity import WRITE_GROUPS, IdentityVerifier, OperatorIdentity, members_of
from twitch_mcp.twitch_client import Route, TwitchClient

#: The roster endpoints of R29, as the client builds them (``twitch_api_base`` ends in ``/helix``).
MODERATORS_PATH = "/moderation/moderators"
EDITORS_PATH = "/channels/editors"

#: The groups the operator is proven in this module's rosters by the moderators list.
MODERATION_GROUPS: tuple[str, ...] = ("moderation", "chat", "roles", "polls", "predictions")

#: Registered page size of the roster lookup (``server.ROSTER_PAGE_SIZE``).
ROSTER_PAGE_SIZE = "100"


def roster_body(user_ids: Sequence[str], *, key: str = "user_id") -> dict[str, Any]:
    """A Helix roster envelope: ``{"data": [{"user_id": ...}, ...]}``."""
    return {"data": [{key: user_id} for user_id in user_ids], "pagination": {}}


class RosterService:
    """Which channels each roster names — mutable so a test can revoke a role mid-flight."""

    def __init__(self) -> None:
        self.moderators: dict[str, list[str]] = {}
        self.editors: dict[str, list[str]] = {}
        self._registered: set[str] = set()

    def serve(
        self, fake: FakeTwitch, path: str, broadcaster_id: str, members: Sequence[str]
    ) -> None:
        """Record that ``broadcaster_id``'s roster on ``path`` contains ``members``."""
        table = self.moderators if path == MODERATORS_PATH else self.editors
        table[broadcaster_id] = list(members)
        if path not in self._registered:
            self._registered.add(path)
            fake.add("GET", f"^/helix{re.escape(path)}$", self._handler(path))

    def _handler(self, path: str) -> Any:
        table = self.moderators if path == MODERATORS_PATH else self.editors

        def handler(request: httpx.Request) -> httpx.Response:
            asked = request.url.params.get("broadcaster_id")
            members = [] if asked is None else table.get(asked, [])
            return httpx.Response(200, json=roster_body(members))

        return handler


def serve_roster(
    fake: FakeTwitch,
    path: str,
    broadcaster_id: str,
    user_ids: Sequence[str],
    *,
    roster: RosterService | None = None,
) -> RosterService:
    """Serve one channel's roster on ``path``; returns the table so it can be reused or mutated."""
    service = roster or RosterService()
    service.serve(fake, path, broadcaster_id, user_ids)
    return service


def clear_identity_cache(cache: TTLCache) -> None:
    """Drop the identity entries only — the guard's metadata entries survive (R30)."""
    from twitch_mcp.identity import IDENTITY_CACHE_PREFIX

    cache.invalidate(IDENTITY_CACHE_PREFIX)


@dataclass
class IdentityWiring:
    """The verifier plus everything a test needs to inspect what it did."""

    verifier: IdentityVerifier
    operator: OperatorIdentity
    cache: TTLCache
    clock: FakeClock
    fake: FakeTwitch
    client: TwitchClient
    settings: Settings
    fetch_calls: list[tuple[str, str]] = field(default_factory=list)
    roster: RosterService = field(default_factory=RosterService)

    @property
    def roster_requests(self) -> list[Any]:
        """Roster lookups that left the process — the number R30 asserts stays flat."""
        return self.fake.requests_for("GET", f"/helix{MODERATORS_PATH}") + self.fake.requests_for(
            "GET", f"/helix{EDITORS_PATH}"
        )

    def reset_counts(self) -> None:
        """Forget recorded requests (e.g. to exclude an earlier verification from a count)."""
        self.fake.clear()
        self.fetch_calls.clear()


def make_wiring(
    fake: FakeTwitch,
    fclock: FakeClock,
    *,
    operator: str | None = OPERATOR,
    identity_ttl: int = 300,
    settings: Settings | None = None,
    moderators: dict[str, Sequence[str]] | None = None,
    editors: dict[str, Sequence[str]] | None = None,
) -> IdentityWiring:
    """Build the R29–R30 stack over the fake transport, mirroring ``server.build_wiring``."""
    cfg = settings or make_settings(
        twitch_identity_ttl_seconds=identity_ttl,
        # R28: both kill switches, so a service-level test reaches the identity check (R29).
        twitch_enable_writes=True,
        twitch_write_groups=",".join(sorted(WRITE_GROUPS)),
    )
    client = make_client(fake, fclock, cfg)
    cache = TTLCache(
        max_entries=64, max_bytes=1_000_000, clock=fclock.clock, wall_clock=fclock.wall
    )
    identity = OperatorIdentity(operator)
    fetch_calls: list[tuple[str, str]] = []
    roster = RosterService()
    for broadcaster_id, members in (moderators or {}).items():
        roster.serve(fake, MODERATORS_PATH, broadcaster_id, members)
    for broadcaster_id, members in (editors or {}).items():
        roster.serve(fake, EDITORS_PATH, broadcaster_id, members)

    async def fetch_roster(name: str, broadcaster_id: str) -> Sequence[str]:
        """``server.py``'s resolver: one Helix GET for the roster, parsed by ``members_of``."""
        fetch_calls.append((name, broadcaster_id))
        template = MODERATORS_PATH if name == "moderators" else EDITORS_PATH
        response = await client.get(
            Route.build(template, broadcaster_id, token_type="user"),
            client_id=client.client_id,
            bearer=USER_BEARER_PLACEHOLDER,
            params=[("broadcaster_id", broadcaster_id), ("first", ROSTER_PAGE_SIZE)],
        )
        return members_of(response.body)

    verifier = IdentityVerifier(
        identity, cache, fetch_roster, ttl_seconds=float(cfg.twitch_identity_ttl_seconds)
    )
    return IdentityWiring(
        verifier=verifier,
        operator=identity,
        cache=cache,
        clock=fclock,
        fake=fake,
        client=client,
        settings=cfg,
        fetch_calls=fetch_calls,
        roster=roster,
    )


#: A non-secret bearer for the roster lookup; the client only requires it to be present (R13).
USER_BEARER_PLACEHOLDER = "identity-test-roster-bearer"
