"""Shared fakes for the service layer (owned by the ``service.py`` agent).

``tests/conftest.py`` and ``tests/twitch_fakes.py`` are frozen; this module adds only what
``test_service.py`` needs on top of them, and later agents may import it too.

:func:`make_service` assembles a whole :class:`~twitch_mcp.service.TwitchService` against the
frozen :class:`~tests.twitch_fakes.FakeTwitch` transport: a real client, limiter, cache,
identity verifier and confirmation store, with the guard's owner lookup and both credential sources
wired to fakes. Everything is counted or inspectable:

``harness.counters["owner_calls"]``
    upstream owner lookups — R26 asserts this is 1 across repeated owned-object calls.
``counters["app_token_calls"]`` / ``counters["user_token_calls"]``
    how often a credential was asked for; a refused call must ask for neither.
``harness.requests`` (``fake.request_count``)
    requests that left the process — R25/R31 assert 0 for every refusal.

Rosters are data, not code: ``moderators={broadcaster_id: [user_id, ...]}``, likewise ``editors``.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from tests.core_fakes import Roster
from tests.twitch_fakes import (
    BROADCASTER,
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    OPERATOR,
    FakeClock,
    FakeTwitch,
    make_settings,
)
from twitch_mcp.cache import Fetched, TTLCache
from twitch_mcp.config import Settings
from twitch_mcp.confirmation import ConfirmationStore
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.guard import Guard
from twitch_mcp.identity import IdentityVerifier, OperatorIdentity
from twitch_mcp.logging_setup import register_secret
from twitch_mcp.oauth import OAuthFlow, UserToken
from twitch_mcp.ratelimit import RateLimiter
from twitch_mcp.service import TwitchService
from twitch_mcp.twitch_client import TwitchClient

#: The two bearers the injected credential sources hand out. Test values, registered for redaction.
APP_BEARER = "app-bearer-value"
USER_BEARER = "user-bearer-value"

#: All six groups, so ``writes_on`` throws both R28 kill switches at once.
ALL_WRITE_GROUPS = "channel,polls,predictions,chat,moderation,roles"

TOKEN_FILE = "/tmp/twitch-mcp-service-tests/twitch_user_token.json"


@dataclass
class ServiceHarness:
    """Everything a service test needs, in one object."""

    service: TwitchService
    client: TwitchClient
    fake: FakeTwitch
    clock: FakeClock
    settings: Settings
    guard: Guard
    cache: TTLCache
    identity: IdentityVerifier
    confirmations: ConfirmationStore
    limiter: RateLimiter
    roster: Roster
    oauth: OAuthFlow | None
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def requests(self) -> int:
        """Requests that left the process — the number every refusal test asserts is 0."""
        return self.fake.request_count

    @property
    def methods(self) -> list[str]:
        return self.fake.methods

    @property
    def paths(self) -> list[str]:
        return self.fake.paths

    def reset_counts(self) -> None:
        """Forget recorded requests and collaborator calls (e.g. after a boot probe)."""
        self.fake.clear()
        for key in list(self.counters):
            self.counters[key] = 0


def make_connected_flow(settings: Settings) -> OAuthFlow:
    """A real :class:`OAuthFlow` holding a connected account (built without any I/O)."""
    flow = OAuthFlow(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        auth_base=settings.twitch_auth_base,
        redirect_uri=settings.redirect_uri,
        sign_in_url=settings.sign_in_url,
        scopes=settings.oauth_scope_list,
        token_file=TOKEN_FILE,
        enabled=True,
        clock=time.time,
    )
    register_secret(USER_BEARER)
    flow.auth.token = UserToken(
        access_token=SecretStr(USER_BEARER),
        refresh_token=SecretStr("fake-refresh-token-value"),
        token_type="Bearer",
        scope=" ".join(settings.oauth_scope_list),
        expires_at=time.time() + 3600.0,
        user_id=OPERATOR,
        user_login="operator",
        connected_at=time.time(),
    )
    return flow


def make_service(
    fake: FakeTwitch,
    fclock: FakeClock,
    *,
    settings: Settings | None = None,
    allowed: frozenset[str] | None = None,
    operator: str | None = OPERATOR,
    moderators: dict[str, Sequence[str]] | None = None,
    editors: dict[str, Sequence[str]] | None = None,
    owners: dict[str, Any] | None = None,
    owner_errors: dict[str, Exception] | None = None,
    writes_on: bool = False,
    user_oauth: bool = False,
    user_connected: bool = False,
    metadata_ttl: int = 300,
    identity_ttl: int = 300,
    confirm_ttl: int = 300,
    max_wait: float = 30.0,
    oauth: OAuthFlow | None = None,
) -> ServiceHarness:
    """Build a fully-wired :class:`TwitchService` over the fake transport.

    ``writes_on`` throws both R28 kill switches. ``user_oauth`` + ``user_connected`` decide whether
    user-token tools exist and whether a user bearer is available (R46, R49).
    """
    baseline = make_settings(
        twitch_allowed_broadcaster_ids=",".join(sorted(allowed or {BROADCASTER})),
        twitch_enable_writes=writes_on,
        twitch_write_groups=ALL_WRITE_GROUPS if writes_on else "",
        twitch_user_oauth=user_oauth,
        twitch_oauth_scopes="user:read:email chat:read",
        ttl_metadata_seconds=metadata_ttl,
        twitch_identity_ttl_seconds=identity_ttl,
        twitch_confirm_token_ttl_seconds=confirm_ttl,
    )
    cfg = settings or baseline
    register_secret(APP_BEARER)
    register_secret(USER_BEARER)

    limiter = RateLimiter(max_wait_seconds=max_wait, clock=fclock.clock, sleep=fclock.sleep)
    client = TwitchClient(
        cfg, transport=fake.transport(), clock=fclock.clock, sleep=fclock.sleep, limiter=limiter
    )
    cache = TTLCache(
        max_entries=64, max_bytes=1_000_000, clock=fclock.clock, wall_clock=fclock.wall
    )
    counters: dict[str, int] = {"owner_calls": 0, "app_token_calls": 0, "user_token_calls": 0}
    owner_map = dict(owners or {})
    error_map = dict(owner_errors or {})
    owner_ttl = float(cfg.twitch_identity_ttl_seconds)

    async def fetch_owned(object_id: str) -> Fetched:
        """The guard's single cached owner lookup (R26), counting only real upstream fetches."""

        async def fetch() -> Any:
            counters["owner_calls"] += 1
            if object_id in error_map:
                raise error_map[object_id]
            if object_id not in owner_map:
                raise TwitchMcpError("upstream", "unknown id", http_status=404, code=10000)
            value = owner_map[object_id]
            return dict(value) if isinstance(value, dict) else {"broadcaster_id": str(value)}

        return await cache.get(f"owner:{object_id}", fetch, owner_ttl)

    guard = Guard(frozenset(allowed if allowed is not None else {BROADCASTER}), fetch_owned)
    roster = Roster(moderators or {}, editors or {})
    identity = IdentityVerifier(
        OperatorIdentity(operator),
        cache,
        roster.fetch,
        ttl_seconds=float(cfg.twitch_identity_ttl_seconds),
    )
    confirmations = ConfirmationStore(
        ttl_seconds=float(confirm_ttl), clock=fclock.clock, wall_clock=fclock.unix
    )

    async def app_token() -> str:
        counters["app_token_calls"] += 1
        return APP_BEARER

    user_source = None
    if user_connected:

        async def user_token() -> str:
            counters["user_token_calls"] += 1
            return USER_BEARER

        user_source = user_token

    flow = oauth
    if flow is None and user_oauth:
        flow = make_connected_flow(cfg) if user_connected else _make_flow(cfg)

    print(f"[MAKE] building TwitchService allowed={sorted(allowed or set())} guard_id={id(guard)}")
    service = TwitchService(
        cfg,
        client,
        limiter,
        guard,
        cache,
        identity,
        confirmations,
        app_token=app_token,
        user_token=user_source,
        oauth=flow,
        now=fclock.wall,
    )
    return ServiceHarness(
        service=service,
        client=client,
        fake=fake,
        clock=fclock,
        settings=cfg,
        guard=guard,
        cache=cache,
        identity=identity,
        confirmations=confirmations,
        limiter=limiter,
        roster=roster,
        oauth=flow,
        counters=counters,
    )


def _make_flow(settings: Settings) -> OAuthFlow:
    """An unconnected OAuthFlow — enough for ``connect_twitch_account``."""
    return OAuthFlow(
        client_id=FAKE_CLIENT_ID,
        client_secret=FAKE_CLIENT_SECRET,
        auth_base=settings.twitch_auth_base,
        redirect_uri=settings.redirect_uri,
        sign_in_url=settings.sign_in_url,
        scopes=settings.oauth_scope_list,
        token_file=TOKEN_FILE,
        enabled=True,
        clock=time.time,
    )


def envelope(
    records: list[Any], *, cursor: str | None = None, total: int | None = None
) -> dict[str, Any]:
    """A Helix response envelope: ``data``, ``pagination`` and optional ``total``."""
    body: dict[str, Any] = {"data": records, "pagination": {"cursor": cursor} if cursor else {}}
    if total is not None:
        body["total"] = total
    return body
