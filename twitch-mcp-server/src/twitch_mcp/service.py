"""TwitchService: one async method per tool of SPEC §6.4 (23 reads, 2 account, 16 writes).

Transport-independent and fully injected: this module imports no network library and never reads
the process env (R6, R11). Every collaborator is handed in by ``server.py`` — settings, the Helix
client, the rate limiter, the guard, the metadata cache, the identity verifier,
the confirmation store and the app/user credential sources — so the same object is testable against
:class:`tests.twitch_fakes.FakeTwitch` and against the real API without changing a line.

**Ordering inside a method is the security model, not a style choice** (SPEC §6.3):

1. argument checks the schema cannot express (mutually exclusive cursors, documented bounds);
2. group switch — ``kind=disabled`` when a write group is off (R28);
3. :meth:`Guard.require_broadcaster` **before any request** — a non-allowlisted id costs zero
   requests (R25);
4. writes only: :meth:`IdentityVerifier.require_writable` (R29) then the confirmation gate
   (R31–R34);
5. ``client.<verb>(...)`` — at most one request per tool call; a write exactly once, never retried
   (R16, delegated to the client, which owns that rule);
6. projection into the declared model, with ``meta`` and, for list tools, ``pagination`` (R38,
   R42).

An empty list always means genuinely empty. A refusal is always an error kind —
``forbidden_channel`` for an id this server will not touch (R25/R27), ``permission`` when Twitch
names a missing scope (R45, mapped by the client), ``not_authorized`` when the operator is not
verified for a write target (R29) — never ``[]``.

Credential ownership stays outside this module: the service asks an injected ``AppTokenSource`` /
``UserTokenSource`` for a bearer at the moment of the request and holds no token itself (R52).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from twitch_mcp.cache import Fetched, TTLCache
from twitch_mcp.capabilities import ToolPlan, compute_tool_plan
from twitch_mcp.config import Settings
from twitch_mcp.confirmation import ConfirmationStore, MintedConfirmation
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.guard import Guard
from twitch_mcp.identity import IdentityVerifier
from twitch_mcp.models import (
    AccountStatusResult,
    AutomodSettingsResult,
    BannedUsersResult,
    ChannelsResult,
    ChatBadgesResult,
    ChatSettingsResult,
    ChattersResult,
    ClipsResult,
    ConnectResult,
    DisconnectResult,
    EditorsResult,
    EmotesResult,
    FollowsResult,
    GamesResult,
    MarkersResult,
    Meta,
    ModeratorsResult,
    PollsResult,
    PredictionsResult,
    ScheduleResult,
    SearchChannelsResult,
    StreamsResult,
    SubscribersResult,
    UsersResult,
    VideosResult,
    WriteConfirmation,
    WriteResult,
)
from twitch_mcp.oauth import CONNECT_HINT, OAuthFlow
from twitch_mcp.project import (
    now_iso,
    project_automod_settings,
    project_banned_users,
    project_channels,
    project_chat_badges,
    project_chat_settings,
    project_chatters,
    project_clips,
    project_editors,
    project_emotes,
    project_follows,
    project_games,
    project_markers,
    project_moderators,
    project_polls,
    project_predictions,
    project_schedule,
    project_search_channels,
    project_streams,
    project_subscribers,
    project_token_validation,
    project_users,
    project_videos,
    project_write_data,
)
from twitch_mcp.ratelimit import APP_CLASS, USER_CLASS, RateLimiter, TokenType
from twitch_mcp.twitch_client import (
    InvalidArgument,
    Response,
    Route,
    TwitchClient,
    cursor,
    login,
    opaque,
    repeat,
)
from twitch_mcp.twitch_client import user_id as validate_user_id

logger = logging.getLogger(__name__)

#: ``awaitable() -> str`` returning a usable bearer for one credential class. A refresh failure
#: raises ``kind=unauthenticated``; there is never an app-token fallback for a user call (R49).
AppTokenSource = Callable[[], Awaitable[str]]
UserTokenSource = Callable[[], Awaitable[str]]

#: The single ``kind=disabled`` message for a write whose kill switches are not both thrown (R28).
DISABLED_WRITE_MESSAGE: Final = (
    "This write tool is not enabled: TWITCH_ENABLE_WRITES must be true and the tool's group must "
    "be listed in TWITCH_WRITE_GROUPS"
)

#: The connect instructions. A procedure, never a code, token or ``state`` (R52).
CONNECT_INSTRUCTIONS: Final = (
    "Open the sign-in URL in a browser, approve the requested scopes, and complete the callback at "
    "/oauth/callback. Then call twitch_account_status to confirm the connection."
)
CONNECT_ALREADY_MESSAGE: Final = (
    "A Twitch account is already connected to this server; call again with reconnect to re-consent"
)

#: The disconnect message: what was touched, and what was not (R51).
DISCONNECT_MESSAGE: Final = (
    "This server's stored Twitch credential was revoked and deleted. Nothing else on the account "
    "was changed."
)
DISCONNECT_NOT_CONNECTED_MESSAGE: Final = (
    "No Twitch account is connected to this server, so there was nothing to revoke"
)

#: Maximum ``first`` per list tool where Helix documents one (R40). Above it the call is refused as
#: an invalid argument before any request, so a tool never asks for a page it cannot answer.
MAX_FIRST: Final[dict[str, int]] = {
    "get_users": 100,
    "get_users_follows": 200,
    "get_channels": 100,
    "search_channels": 100,
    "get_streams": 100,
    "get_top_games": 100,
    "get_games": 100,
    "get_videos": 100,
    "get_clips": 100,
    "get_polls": 20,
    "get_predictions": 20,
    "get_emotes": 1000,
    "get_stream_markers": 100,
    "get_broadcaster_subscribers": 20,
    "get_channel_editors": 100,
    "get_moderators": 100,
    "get_banned_users": 100,
    "get_chat_chatters": 1000,
    "get_schedule": 100,
}

#: ``POST /chat/messages`` documents 10–500 characters.
MIN_CHAT_MESSAGE_LENGTH: Final = 10
MAX_CHAT_MESSAGE_LENGTH: Final = 500
#: A timeout's duration, in seconds (Twitch's ceiling is 14 days; a longer value is a ban).
MIN_TIMEOUT_SECONDS: Final = 1
MAX_TIMEOUT_SECONDS: Final = 1_209_600
#: ``POST /polls`` documents 2–5 choices and 15–604800 seconds; ``POST /predictions`` exactly 2
#: outcomes and a 30–120 second window.
MIN_POLL_CHOICES: Final = 2
MAX_POLL_CHOICES: Final = 5
MIN_POLL_DURATION_SECONDS: Final = 15
MAX_POLL_DURATION_SECONDS: Final = 604_800
PREDICTION_OUTCOMES: Final = 2
MIN_PREDICTION_WINDOW_SECONDS: Final = 30
MAX_PREDICTION_WINDOW_SECONDS: Final = 120
#: The maximum length of each free-text write argument, per Helix's own field documentation.
MAX_TITLE_LENGTH: Final = 100
MAX_POLL_TITLE_LENGTH: Final = 48
MAX_CHOICE_LENGTH: Final = 25
MAX_REASON_LENGTH: Final = 500
MAX_TAG_LENGTH: Final = 25

#: The automod categories of ``PUT /moderation/automod/terms`` (Helix's own names).
AUTOMOD_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"profanity", "sexuality_sex", "sexuality_stance", "violence", "racism", "harassment"}
)
#: The two terminal statuses Helix accepts for a poll and a prediction.
POLL_END_STATUSES: Final[frozenset[str]] = frozenset({"TERMINATED", "ARCHIVED"})
PREDICTION_END_STATUSES: Final[frozenset[str]] = frozenset({"RESOLVED", "CANCELLED"})


def _helix_path(template: str) -> str:
    """The route's concrete path: the template minus its HTTP verb (R17).

    ``Route.template`` is the log/limit identity (``GET /channels``); ``Route.path`` must be the
    single segment set the client will pin to the two allowed hosts.
    """
    return template.split(" ", 1)[1] if " " in template else template


def _invalid(message: str, hint: str | None = None) -> TwitchMcpError:
    """An argument the schema cannot express. Refused before any request (R39's shape)."""
    return TwitchMcpError("invalid_arguments", message, hint=hint)


def _pairs(**named: Any) -> list[tuple[str, str]]:
    """Query pairs: ``None``s dropped, lists repeated per value (R18), booleans as Helix says."""
    out: list[tuple[str, str]] = []
    for name, value in named.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out.append((name, "true" if value else "false"))
        elif isinstance(value, (list, tuple)):
            out.extend(repeat(name, [str(item) for item in value]))
        else:
            out.append((name, str(value)))
    return out


def _cursor_params(**named: str | None) -> list[tuple[str, str]]:
    """``after``/``before`` pairs, each validated as a cursor before it can reach a URL (R17).

    A rejected cursor is an ``invalid_arguments`` refusal — the argument is malformed; the channel
    was never implicated (same shape as :func:`_validated_user_id` gives a malformed id).
    """
    checked: dict[str, str | None] = {}
    for name, value in named.items():
        if value is None:
            checked[name] = None
            continue
        try:
            checked[name] = cursor(value, what=name)
        except InvalidArgument as exc:
            raise _invalid(f"{name} is not a valid cursor") from exc
    return _pairs(**checked)


def _cursors_exclusive(tool: str, **named: str | None) -> None:
    """R40: mutually exclusive cursors in one call are an invalid argument, never a request."""
    given = [name for name, value in named.items() if value is not None]
    if len(given) > 1:
        raise _invalid(
            f"{tool} accepts only one of {' or '.join(sorted(named))} per call",
            hint="Helix pages in one direction at a time; issue a second call for the other.",
        )


def _first(tool: str, first: int) -> str:
    """Validate ``first`` against the endpoint's documented maximum (R40)."""
    ceiling = MAX_FIRST.get(tool)
    bad = isinstance(first, bool) or not isinstance(first, int) or first < 1
    if bad or (ceiling is not None and first > ceiling):
        raise _invalid(
            f"first must be between 1 and {ceiling} for {tool}",
            hint="Lower first so the page fits TWITCH_MAX_RESPONSE_BYTES (R14).",
        )
    return str(first)


def _required(text: str, name: str, *, maxlen: int | None = None) -> str:
    """A non-blank, bounded string argument. Never logged (R52)."""
    if not isinstance(text, str):
        raise _invalid(f"{name} must be a string")
    value = text.strip()
    if not value:
        raise _invalid(f"{name} must not be empty")
    if maxlen is not None and len(value) > maxlen:
        raise _invalid(f"{name} must be at most {maxlen} characters")
    return value


def _free_text(text: str, name: str, *, maxlen: int) -> str:
    """A free-text write argument: bounded and non-blank, kept verbatim, never logged (R52/R53)."""
    if not isinstance(text, str):
        raise _invalid(f"{name} must be a string")
    if not text.strip():
        raise _invalid(f"{name} must not be empty")
    if len(text) > maxlen:
        raise _invalid(f"{name} must be at most {maxlen} characters")
    return text


def _validated_user_id(value: str, *, what: str = "user id") -> str:
    """A user id argument that is shape-safe for a URL segment (R17) — 1–20 digits."""
    try:
        return validate_user_id(value, what=what)
    except InvalidArgument as exc:
        raise _invalid(f"{what} must be 1 to 20 digits") from exc


def _opaque(value: str, what: str) -> str:
    """An opaque Helix id, shape-checked (R17)."""
    try:
        return opaque(value, what=what)
    except InvalidArgument as exc:
        raise _invalid(f"{what} is not a valid identifier") from exc


def _summary(**values: Any) -> dict[str, Any]:
    """The validated argument *summary* shown in the confirmation plan (R31, R52).

    Scalars keep their value, because the human must read what they are about to approve. Free text
    (a chat message, a ban reason, an automod term) becomes its length and a collection becomes its
    size — the plan describes the action without hoarding its content.
    """
    summary: dict[str, Any] = {}
    for name, value in values.items():
        if value is None:
            continue
        if isinstance(value, str):
            summary[name] = {"characters": len(value)}
        elif isinstance(value, (list, tuple, Mapping)):
            summary[name] = {"count": len(value)}
        else:
            summary[name] = value
    return summary


class TwitchService:
    """One method per tool: guard → identity → confirmation → client → projection."""

    def __init__(
        self,
        settings: Settings,
        client: TwitchClient,
        limiter: RateLimiter,
        guard: Guard,
        cache: TTLCache,
        identity: IdentityVerifier,
        confirmations: ConfirmationStore,
        *,
        app_token: AppTokenSource,
        user_token: UserTokenSource | None = None,
        oauth: OAuthFlow | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Everything is injected: no module-level state and no ``os`` access (R6, R11).

        ``limiter`` is the same :class:`~twitch_mcp.ratelimit.RateLimiter` the client holds, so a
        learned wait beyond ``TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS`` refuses before the socket (R21)
        and the process-wide 401 circuit (R24) is shared rather than duplicated.
        """
        self._settings = settings
        self._client = client
        self._limiter = limiter
        # R30's third trigger has no other route to the caches. The client trips the circuit on the
        # first Helix 401 and knows nothing about identity; only this object holds the verifier and
        # the confirmation store that the tripped credential authorised, so the listener is attached
        # here rather than by whoever assembles the server. Registering in the constructor means no
        # composition path — production or test — can silently leave R30 unwired.
        limiter.set_auth_failure_listener(self.on_identity_change)
        self._guard = guard
        self._cache = cache
        self._identity = identity
        self._confirmations = confirmations
        self._app_token = app_token
        self._user_token = user_token
        self._oauth = oauth
        self._now = now
        self._ttl = float(settings.ttl_metadata_seconds)

    # ------------------------------------------------------------ lifecycle / wiring

    @property
    def settings(self) -> Settings:
        """The boot settings this service was built from (read-only)."""
        return self._settings

    @property
    def limiter(self) -> RateLimiter:
        """The shared rate limiter (R21, R24)."""
        return self._limiter

    @property
    def identity_verifier(self) -> IdentityVerifier:
        """The verifier whose cached verdicts must die on refresh, disconnect or the 401 (R30)."""
        return self._identity

    @property
    def confirmations(self) -> ConfirmationStore:
        """The token store that must be cleared on those same moments (R33)."""
        return self._confirmations

    def tool_plan(self) -> ToolPlan:
        """The effective registration plan (R35) — the same pure decision ``/healthz`` reports."""
        return compute_tool_plan(self._settings, self._user_token is not None)

    def invalidate_identity(self) -> None:
        """Drop every verified moderator/editor verdict (R30)."""
        self._identity.invalidate()

    def clear_confirmations(self) -> int:
        """Drop every outstanding confirm token (R33). Returns how many were dropped."""
        return self._confirmations.clear()

    def on_identity_change(self) -> None:
        """The one listener behind every R30 trigger: refresh, disconnect, and the 401 circuit.

        Both live caches of R30/R33 go together: a credential that changed must leave neither a
        stale roster verdict nor an outstanding confirm token behind. ``server.py`` registers it for
        refresh and disconnect; the constructor registers it with the limiter for the 401 circuit.
        """
        self._identity.invalidate()
        self._confirmations.clear()

    # ------------------------------------------------------------ credentials

    async def _app(self) -> str:
        return await self._app_token()

    async def _user(self) -> str:
        if self._user_token is None:
            raise TwitchMcpError(
                "unauthenticated",
                "No Twitch account is connected, so this server holds no user credential",
                hint=CONNECT_HINT,
            )
        return await self._user_token()

    async def _user_if_connected(self) -> str | None:
        """The user bearer when one exists, else ``None`` — never a crash for the status tool."""
        if self._user_token is None:
            return None
        try:
            return await self._user_token()
        except TwitchMcpError as exc:
            if exc.kind == "unauthenticated":
                return None
            raise

    # ------------------------------------------------------------ request helpers

    def _meta(self, endpoint: str, fetched: Fetched | None = None) -> Meta:
        """``meta`` carries the route template and freshness only — never a URL (R52/R53)."""
        if fetched is None:
            return Meta(as_of=now_iso(self._now), endpoint=endpoint)
        return Meta(
            as_of=now_iso(self._now),
            cached=fetched.cached,
            cache_age_seconds=fetched.age_seconds,
            endpoint=endpoint,
        )

    async def _get(
        self,
        template: str,
        *,
        params: Sequence[tuple[str, str]] = (),
        major: str = "",
        as_user: bool = False,
    ) -> Response:
        """One Helix GET on the app or user credential. Never reached for a refused id (R25)."""
        bearer = await (self._user() if as_user else self._app())
        kind: TokenType = USER_CLASS if as_user else APP_CLASS
        route = Route.build(_helix_path(template), major, token_type=kind)
        return await self._client.get(
            route, client_id=self._client.client_id, bearer=bearer, params=params
        )

    async def _cached_body(
        self,
        key: str,
        template: str,
        *,
        params: Sequence[tuple[str, str]] = (),
        major: str = "",
    ) -> Fetched:
        """A slow-changing metadata GET through the TTL cache (never used for list results)."""
        client_id = self._client.client_id

        async def fetch() -> Any:
            bearer = await self._app()
            route = Route.build(_helix_path(template), major)
            response = await self._client.get(
                route, client_id=client_id, bearer=bearer, params=params
            )
            return response.body

        return await self._cache.get(key, fetch, self._ttl)

    def _guard_broadcaster(self, broadcaster_id: str, *, field: str = "broadcaster_id") -> str:
        """R25: shape-check and allowlist-check happen before anything can reach the client."""
        self._guard.require_broadcaster(broadcaster_id, field=field)
        return broadcaster_id

    async def _owner_of(self, object_id: str, *, field: str) -> str:
        """R26/R27: one cached lookup learns the owning broadcaster; every failure denies uniformly.

        The guard owns the lookup (it holds the uniform-denial rules); the ``field`` name only
        selects which cache key the guard's resolver fills.
        """
        resolution = await self._guard.resolve_owner(object_id)
        return resolution.broadcaster_id

    # ------------------------------------------------------------ tools: reads (23)

    async def twitch_account_status(self) -> AccountStatusResult:
        """Health of both identities plus the effective plan. Scope names, never a token (R52)."""
        plan = self.tool_plan()
        fields: dict[str, Any] = {
            "meta": self._meta("GET /oauth2/validate"),
            "user_oauth_enabled": self._settings.twitch_user_oauth,
            "writes_enabled": self._settings.writes_enabled,
            "enabled_write_groups": list(plan.enabled_write_groups),
            "tool_count": plan.tool_count,
        }
        try:
            app_bearer = await self._app()
        except TwitchMcpError as exc:
            if exc.kind != "unauthenticated":
                raise
            fields["degraded"] = True
            fields["degraded_reason"] = "app_token_unavailable"
            return AccountStatusResult(**fields)
        fields["app_token"] = project_token_validation(
            (await self._client.fetch_validate(app_bearer, client_id=self._client.client_id)).body
        )
        user_bearer = await self._user_if_connected()
        if user_bearer is None:
            fields["user_connected"] = False
            if self._settings.twitch_user_oauth:
                fields["sign_in_url"] = self._settings.sign_in_url
            return AccountStatusResult(**fields)
        fields["user_connected"] = True
        fields["user"] = project_token_validation(
            (await self._client.fetch_validate(user_bearer, client_id=self._client.client_id)).body
        )
        return AccountStatusResult(**fields)

    async def get_users(
        self, *, ids: Sequence[str] | None = None, logins: Sequence[str] | None = None
    ) -> UsersResult:
        """``GET /users`` by id or login. Every id must be allowlisted (R25)."""
        if not ids and not logins:
            raise _invalid(
                "get_users needs at least one id or login",
                hint="Pass ids for allowlisted broadcasters, or logins for public profiles.",
            )
        checked_ids = [_validated_user_id(value, what="id") for value in ids or ()]
        for candidate in checked_ids:
            self._guard.require_broadcaster(candidate, field="id")
        checked_logins = [login(value) for value in logins or ()]
        response = await self._get(
            "GET /users",
            params=_pairs(id=checked_ids or None, login=checked_logins or None),
            major=checked_ids[0] if checked_ids else "",
        )
        return project_users(response.body, endpoint="GET /users", as_of=now_iso(self._now))

    async def get_users_follows(
        self,
        *,
        from_id: str | None = None,
        to_id: str | None = None,
        first: int = 20,
        after: str | None = None,
        before: str | None = None,
    ) -> FollowsResult:
        """``GET /users/follows``. Both sides of the relation are guarded (R25)."""
        if from_id is None and to_id is None:
            raise _invalid("get_users_follows needs from_id or to_id")
        if from_id is not None:
            self._guard_broadcaster(from_id, field="from_id")
        if to_id is not None:
            self._guard_broadcaster(to_id, field="to_id")
        _cursors_exclusive("get_users_follows", after=after, before=before)
        response = await self._get(
            "GET /users/follows",
            params=_pairs(from_id=from_id, to_id=to_id, first=_first("get_users_follows", first))
            + _cursor_params(after=after, before=before),
            major=to_id or from_id or "",
        )
        return project_follows(
            response.body, endpoint="GET /users/follows", as_of=now_iso(self._now)
        )

    async def get_channels(self, *, broadcaster_ids: Sequence[str]) -> ChannelsResult:
        """``GET /channels`` — every requested id is allowlisted before the request (R25)."""
        if not broadcaster_ids:
            raise _invalid("get_channels needs at least one broadcaster_id")
        checked = [_validated_user_id(value) for value in broadcaster_ids]
        for candidate in checked:
            self._guard.require_broadcaster(candidate)
        response = await self._get(
            "GET /channels", params=_pairs(broadcaster_id=checked), major=checked[0]
        )
        return project_channels(response.body, endpoint="GET /channels", as_of=now_iso(self._now))

    async def search_channels(
        self, *, query: str, first: int = 20, after: str | None = None, before: str | None = None
    ) -> SearchChannelsResult:
        """``GET /search/channels`` — not broadcaster-scoped, so no allowlist applies (R25)."""
        text = _required(query, "query", maxlen=100)
        _cursors_exclusive("search_channels", after=after, before=before)
        response = await self._get(
            "GET /search/channels",
            params=_pairs(query=text, first=_first("search_channels", first))
            + _cursor_params(after=after, before=before),
        )
        return project_search_channels(
            response.body, endpoint="GET /search/channels", as_of=now_iso(self._now)
        )

    async def get_streams(
        self,
        *,
        user_ids: Sequence[str] | None = None,
        game_id: str | None = None,
        first: int = 20,
        after: str | None = None,
        before: str | None = None,
    ) -> StreamsResult:
        """``GET /streams`` — with ``user_ids`` every id is guarded; ``game_id`` needs no guard."""
        if not user_ids and not game_id:
            raise _invalid("get_streams needs user_ids or game_id")
        checked = [_validated_user_id(value, what="user_ids") for value in user_ids or ()]
        for candidate in checked:
            self._guard.require_broadcaster(candidate, field="user_ids")
        category = _opaque(game_id, "game_id") if game_id else None
        _cursors_exclusive("get_streams", after=after, before=before)
        response = await self._get(
            "GET /streams",
            params=_pairs(
                user_id=checked or None,
                game_id=category,
                first=_first("get_streams", first),
            )
            + _cursor_params(after=after, before=before),
            major=checked[0] if checked else (category or ""),
        )
        return project_streams(response.body, endpoint="GET /streams", as_of=now_iso(self._now))

    async def get_top_games(
        self, *, first: int = 20, after: str | None = None, before: str | None = None
    ) -> GamesResult:
        """``GET /games/top`` — a cached metadata read (R26's cache; lists are never cached)."""
        page = _first("get_top_games", first)
        _cursors_exclusive("get_top_games", after=after, before=before)
        fetched = await self._cached_body(
            f"games:top:{page}:{after or ''}:{before or ''}",
            "GET /games/top",
            params=_pairs(first=page) + _cursor_params(after=after, before=before),
        )
        return project_games(fetched.value, endpoint="GET /games/top", as_of=now_iso(self._now))

    async def get_games(
        self,
        *,
        ids: Sequence[str] | None = None,
        names: Sequence[str] | None = None,
        first: int = 20,
        after: str | None = None,
        before: str | None = None,
    ) -> GamesResult:
        """``GET /games`` by category id or name."""
        if not ids and not names:
            raise _invalid("get_games needs at least one id or name")
        checked_ids = [_opaque(value, "game id") for value in ids or ()]
        checked_names = [_required(value, "name", maxlen=100) for value in names or ()]
        _cursors_exclusive("get_games", after=after, before=before)
        response = await self._get(
            "GET /games",
            params=_pairs(
                id=checked_ids or None,
                name=checked_names or None,
                first=_first("get_games", first),
            )
            + _cursor_params(after=after, before=before),
        )
        return project_games(response.body, endpoint="GET /games", as_of=now_iso(self._now))

    async def get_videos(
        self,
        *,
        user_id: str | None = None,
        game_id: str | None = None,
        video_ids: Sequence[str] | None = None,
        first: int = 20,
        after: str | None = None,
        before: str | None = None,
    ) -> VideosResult:
        """``GET /videos`` by uploader, category, or explicit ids.

        An explicit ``video_id`` is resolved to its broadcaster through exactly one cached lookup
        and then allowlist-checked (R26/R27); a ``user_id`` is checked directly (R25).
        """
        if user_id is None and game_id is None and not video_ids:
            raise _invalid("get_videos needs user_id, game_id or video_ids")
        if user_id is not None:
            self._guard_broadcaster(user_id, field="user_id")
        _cursors_exclusive("get_videos", after=after, before=before)
        ids: list[str] = []
        for value in video_ids or ():
            validated = _opaque(value, "video_id")
            await self._owner_of(validated, field="video_id")
            ids.append(validated)
        category = _opaque(game_id, "game_id") if game_id else None
        response = await self._get(
            "GET /videos",
            params=_pairs(
                user_id=user_id,
                game_id=category,
                id=ids or None,
                first=_first("get_videos", first),
            )
            + _cursor_params(after=after, before=before),
            major=user_id or (ids[0] if ids else (category or "")),
        )
        return project_videos(response.body, endpoint="GET /videos", as_of=now_iso(self._now))

    async def get_clips(
        self,
        *,
        broadcaster_id: str | None = None,
        game_id: str | None = None,
        clip_ids: Sequence[str] | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        first: int = 20,
        after: str | None = None,
        before: str | None = None,
    ) -> ClipsResult:
        """``GET /clips`` — clip ids resolve to their broadcaster before any request (R26/R27)."""
        if broadcaster_id is None and game_id is None and not clip_ids:
            raise _invalid("get_clips needs broadcaster_id, game_id or clip_ids")
        if broadcaster_id is not None:
            self._guard_broadcaster(broadcaster_id)
        _cursors_exclusive("get_clips", after=after, before=before)
        ids: list[str] = []
        for value in clip_ids or ():
            validated = _opaque(value, "clip_id")
            await self._owner_of(validated, field="clip_id")
            ids.append(validated)
        category = _opaque(game_id, "game_id") if game_id else None
        response = await self._get(
            "GET /clips",
            params=_pairs(
                broadcaster_id=broadcaster_id,
                game_id=category,
                id=ids or None,
                started_at=started_at,
                ended_at=ended_at,
                first=_first("get_clips", first),
            )
            + _cursor_params(after=after, before=before),
            major=broadcaster_id or (ids[0] if ids else (category or "")),
        )
        return project_clips(response.body, endpoint="GET /clips", as_of=now_iso(self._now))

    async def get_polls(
        self, *, broadcaster_id: str, first: int = 20, after: str | None = None
    ) -> PollsResult:
        """``GET /polls`` — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /polls",
            params=_pairs(broadcaster_id=broadcaster_id, first=_first("get_polls", first))
            + _cursor_params(after=after),
            major=broadcaster_id,
        )
        return project_polls(response.body, endpoint="GET /polls", as_of=now_iso(self._now))

    async def get_predictions(
        self, *, broadcaster_id: str, first: int = 20, after: str | None = None
    ) -> PredictionsResult:
        """``GET /predictions`` — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /predictions",
            params=_pairs(broadcaster_id=broadcaster_id, first=_first("get_predictions", first))
            + _cursor_params(after=after),
            major=broadcaster_id,
        )
        return project_predictions(
            response.body, endpoint="GET /predictions", as_of=now_iso(self._now)
        )

    async def get_emotes(
        self,
        *,
        broadcaster_id: str | None = None,
        emote_set_ids: Sequence[str] | None = None,
        emote_ids: Sequence[str] | None = None,
        module_metadata: bool = False,
    ) -> EmotesResult:
        """``GET /chat/emotes`` (+ ``/set``, ``/global``) — the endpoint follows the arguments."""
        if broadcaster_id is None and not emote_set_ids and not emote_ids:
            raise _invalid("get_emotes needs broadcaster_id, emote_set_ids or emote_ids")
        if broadcaster_id is not None:
            self._guard_broadcaster(broadcaster_id)
        sets = [_opaque(value, "emote_set_id") for value in emote_set_ids or ()]
        emotes = [_opaque(value, "emote_id") for value in emote_ids or ()]
        if emotes:
            template = "GET /chat/emotes"
        elif sets:
            template = "GET /chat/emotes/set"
        elif module_metadata:
            template = "GET /chat/emotes/global"
        else:
            template = "GET /chat/emotes"
        response = await self._get(
            template,
            params=_pairs(
                broadcaster_id=broadcaster_id,
                emote_set_id=sets or None,
                emote_id=emotes or None,
                module_metadata=True if module_metadata else None,
            ),
            major=broadcaster_id or "",
        )
        return project_emotes(response.body, endpoint=template, as_of=now_iso(self._now))

    async def get_channel_chat_settings(
        self, *, broadcaster_ids: Sequence[str]
    ) -> ChatSettingsResult:
        """``GET /chat/settings`` — every requested room's broadcaster must be allowlisted (R25)."""
        if not broadcaster_ids:
            raise _invalid("get_channel_chat_settings needs at least one broadcaster_id")
        checked = [_validated_user_id(value) for value in broadcaster_ids]
        for candidate in checked:
            self._guard.require_broadcaster(candidate)
        response = await self._get(
            "GET /chat/settings", params=_pairs(broadcaster_id=checked), major=checked[0]
        )
        return project_chat_settings(
            response.body, endpoint="GET /chat/settings", as_of=now_iso(self._now)
        )

    async def get_channel_chat_badges(self, *, broadcaster_id: str) -> ChatBadgesResult:
        """``GET /chat/badges`` — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /chat/badges",
            params=_pairs(broadcaster_id=broadcaster_id),
            major=broadcaster_id,
        )
        return project_chat_badges(
            response.body, endpoint="GET /chat/badges", as_of=now_iso(self._now)
        )

    async def get_stream_markers(
        self,
        *,
        broadcaster_id: str,
        video_id: str | None = None,
        first: int = 20,
        after: str | None = None,
    ) -> MarkersResult:
        """``GET /streams/markers`` (user token). An explicit ``video_id`` is checked too (R26)."""
        self._guard_broadcaster(broadcaster_id)
        if video_id is not None:
            await self._owner_of(_opaque(video_id, "video_id"), field="video_id")
        response = await self._get(
            "GET /streams/markers",
            params=_pairs(
                user_id=broadcaster_id,
                video_id=video_id,
                first=_first("get_stream_markers", first),
            )
            + _cursor_params(after=after),
            major=broadcaster_id,
            as_user=True,
        )
        return project_markers(
            response.body, endpoint="GET /streams/markers", as_of=now_iso(self._now)
        )

    async def get_broadcaster_subscribers(
        self, *, broadcaster_id: str, first: int = 20, after: str | None = None
    ) -> SubscribersResult:
        """``GET /subscriptions`` (user token) — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /subscriptions",
            params=_pairs(
                broadcaster_id=broadcaster_id,
                first=_first("get_broadcaster_subscribers", first),
            )
            + _cursor_params(after=after),
            major=broadcaster_id,
            as_user=True,
        )
        return project_subscribers(
            response.body, endpoint="GET /subscriptions", as_of=now_iso(self._now)
        )

    async def get_channel_editors(self, *, broadcaster_id: str) -> EditorsResult:
        """``GET /channels/editors`` (user token) — the roster R29 uses for the channel group."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /channels/editors",
            params=_pairs(broadcaster_id=broadcaster_id),
            major=broadcaster_id,
            as_user=True,
        )
        return project_editors(
            response.body, endpoint="GET /channels/editors", as_of=now_iso(self._now)
        )

    async def get_moderators(
        self, *, broadcaster_id: str, first: int = 20, after: str | None = None
    ) -> ModeratorsResult:
        """``GET /moderation/moderators`` (user token) — R29's roster for moderation groups."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /moderation/moderators",
            params=_pairs(broadcaster_id=broadcaster_id, first=_first("get_moderators", first))
            + _cursor_params(after=after),
            major=broadcaster_id,
            as_user=True,
        )
        return project_moderators(
            response.body, endpoint="GET /moderation/moderators", as_of=now_iso(self._now)
        )

    async def get_banned_users(
        self,
        *,
        broadcaster_id: str,
        user_ids: Sequence[str] | None = None,
        first: int = 20,
        after: str | None = None,
    ) -> BannedUsersResult:
        """``GET /moderation/banned`` (user token) — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        checked = [_validated_user_id(value) for value in user_ids or ()]
        response = await self._get(
            "GET /moderation/banned",
            params=_pairs(
                broadcaster_id=broadcaster_id,
                user_id=checked or None,
                first=_first("get_banned_users", first),
            )
            + _cursor_params(after=after),
            major=broadcaster_id,
            as_user=True,
        )
        return project_banned_users(
            response.body, endpoint="GET /moderation/banned", as_of=now_iso(self._now)
        )

    async def get_automod_settings(self, *, broadcaster_id: str) -> AutomodSettingsResult:
        """``GET /moderation/automod/settings`` (user token) — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /moderation/automod/settings",
            params=_pairs(broadcaster_id=broadcaster_id),
            major=broadcaster_id,
            as_user=True,
        )
        return project_automod_settings(
            response.body, endpoint="GET /moderation/automod/settings", as_of=now_iso(self._now)
        )

    async def get_chat_chatters(
        self, *, broadcaster_id: str, first: int = 100, after: str | None = None
    ) -> ChattersResult:
        """``GET /chat/chatters`` (user token) — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        response = await self._get(
            "GET /chat/chatters",
            params=_pairs(broadcaster_id=broadcaster_id, first=_first("get_chat_chatters", first))
            + _cursor_params(after=after),
            major=broadcaster_id,
            as_user=True,
        )
        return project_chatters(
            response.body, endpoint="GET /chat/chatters", as_of=now_iso(self._now)
        )

    async def get_schedule(
        self,
        *,
        broadcaster_id: str,
        first: int = 25,
        after: str | None = None,
        before: str | None = None,
        segment_id: str | None = None,
        start_time: str | None = None,
    ) -> ScheduleResult:
        """``GET /schedule`` — guarded before the request (R25)."""
        self._guard_broadcaster(broadcaster_id)
        _cursors_exclusive("get_schedule", after=after, before=before)
        response = await self._get(
            "GET /schedule",
            params=_pairs(
                broadcaster_id=broadcaster_id,
                first=_first("get_schedule", first),
                segment_id=_opaque(segment_id, "segment_id") if segment_id else None,
                start_time=start_time,
            )
            + _cursor_params(after=after, before=before),
            major=broadcaster_id,
        )
        return project_schedule(response.body, endpoint="GET /schedule", as_of=now_iso(self._now))

    # ------------------------------------------------------------ tools: account (2)

    async def connect_twitch_account(self, *, reconnect: bool = False) -> ConnectResult:
        """Hand back the sign-in URL. Creates a pending flow; stores no secret (R47)."""
        if self._oauth is None or not self._settings.twitch_user_oauth:
            raise TwitchMcpError(
                "disabled",
                "User OAuth is off, so this server cannot connect a Twitch account",
                hint="Set TWITCH_USER_OAUTH=true plus TWITCH_OAUTH_SCOPES in .env and restart.",
            )
        if self._oauth.connected and not reconnect:
            return ConnectResult(
                already_connected=True,
                sign_in_url=self._oauth.sign_in_url,
                redirect_uri=self._oauth.redirect_uri,
                scopes=list(self._oauth.scopes),
                instructions=CONNECT_ALREADY_MESSAGE,
            )
        self._oauth.start()
        return ConnectResult(
            already_connected=False,
            sign_in_url=self._oauth.start_url(),
            redirect_uri=self._oauth.redirect_uri,
            scopes=list(self._oauth.scopes),
            expires_in_seconds=self._oauth.auth.pending.seconds_left,
            instructions=CONNECT_INSTRUCTIONS,
        )

    async def disconnect_twitch_account(self) -> DisconnectResult:
        """Revoke and delete this server's own credential — nothing else, ever (R51)."""
        if self._oauth is None or not self._settings.twitch_user_oauth:
            raise TwitchMcpError(
                "disabled",
                "User OAuth is off, so this server holds no account credential to revoke",
                hint="Nothing was changed.",
            )
        disconnected = await self._oauth.disconnect()
        self.on_identity_change()  # R30/R33: no stale roster verdict or confirm token survives this
        logger.info(
            "twitch account disconnected",
            extra={"outcome": "disconnected", "tool": "disconnect_twitch_account"},
        )
        if disconnected is None:
            return DisconnectResult(revoked=False, message=DISCONNECT_NOT_CONNECTED_MESSAGE)
        return DisconnectResult(revoked=True, user_id=disconnected, message=DISCONNECT_MESSAGE)

    # ------------------------------------------------------------ tools: writes (16)

    async def update_channel(
        self,
        *,
        broadcaster_id: str,
        title: str | None = None,
        game_id: str | None = None,
        broadcaster_language: str | None = None,
        tags: Sequence[str] | None = None,
        is_branded_content: bool | None = None,
        confirm: str | None = None,
    ) -> WriteResult:
        """``PATCH /channels`` — the only write of the ``channel`` group (group roster: editors)."""
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = _free_text(title, "title", maxlen=MAX_TITLE_LENGTH)
        if game_id is not None:
            body["game_id"] = _opaque(game_id, "game_id")
        if broadcaster_language is not None:
            body["broadcaster_language"] = _required(
                broadcaster_language, "broadcaster_language", maxlen=16
            ).lower()
        if tags is not None:
            body["tags"] = [_required(tag, "tag", maxlen=MAX_TAG_LENGTH) for tag in tags]
        if is_branded_content is not None:
            body["is_branded_content"] = bool(is_branded_content)
        if not body:
            raise _invalid("update_channel needs at least one field to change")
        return await self._write(
            tool="update_channel",
            group="channel",
            broadcaster_id=broadcaster_id,
            method="PATCH",
            template="PATCH /channels",
            params=_pairs(broadcaster_id=broadcaster_id),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def create_poll(
        self,
        *,
        broadcaster_id: str,
        title: str,
        choices: Sequence[str],
        duration: int = 60,
        channel_points_betting: bool = True,
        bits_betting: bool = True,
        channel_points_voting: bool = False,
        confirm: str | None = None,
    ) -> WriteResult:
        """``POST /polls`` (group ``polls``)."""
        if not MIN_POLL_CHOICES <= len(choices) <= MAX_POLL_CHOICES:
            raise _invalid(
                f"choices must hold between {MIN_POLL_CHOICES} and {MAX_POLL_CHOICES} entries",
                hint="Helix documents 2-5 choices per poll.",
            )
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or not (MIN_POLL_DURATION_SECONDS <= duration <= MAX_POLL_DURATION_SECONDS)
        ):
            raise _invalid(
                f"duration must be between {MIN_POLL_DURATION_SECONDS} and "
                f"{MAX_POLL_DURATION_SECONDS} seconds"
            )
        body: dict[str, Any] = {
            "title": _free_text(title, "title", maxlen=MAX_POLL_TITLE_LENGTH),
            "duration": int(duration),
            "channel_points_betting": bool(channel_points_betting),
            "bits_betting": bool(bits_betting),
            "channel_points_voting": bool(channel_points_voting),
            "choices": [
                {"title": _free_text(choice, "choice", maxlen=MAX_CHOICE_LENGTH)}
                for choice in choices
            ],
        }
        return await self._write(
            tool="create_poll",
            group="polls",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /polls",
            params=_pairs(broadcaster_id=broadcaster_id),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def end_poll(
        self,
        *,
        broadcaster_id: str,
        poll_id: str,
        status: str = "TERMINATED",
        confirm: str | None = None,
    ) -> WriteResult:
        """``PATCH /polls`` (group ``polls``) — ending a poll cannot be undone."""
        if status not in POLL_END_STATUSES:
            raise _invalid("status must be TERMINATED or ARCHIVED")
        arguments = {"poll_id": _opaque(poll_id, "poll_id"), "status": status}
        return await self._write(
            tool="end_poll",
            group="polls",
            broadcaster_id=broadcaster_id,
            method="PATCH",
            template="PATCH /polls",
            params=_pairs(broadcaster_id=broadcaster_id),
            json_body={
                "broadcaster_id": broadcaster_id,
                "id": arguments["poll_id"],
                "status": arguments["status"],
            },
            arguments=arguments,
            confirm=confirm,
        )

    async def create_prediction(
        self,
        *,
        broadcaster_id: str,
        title: str,
        outcomes: Sequence[str],
        prediction_window: int = 60,
        confirm: str | None = None,
    ) -> WriteResult:
        """``POST /predictions`` (group ``predictions``)."""
        if len(outcomes) != PREDICTION_OUTCOMES:
            raise _invalid(
                "outcomes must hold exactly 2 entries",
                hint="Helix accepts exactly two outcomes per prediction.",
            )
        if (
            not isinstance(prediction_window, int)
            or isinstance(prediction_window, bool)
            or not (
                MIN_PREDICTION_WINDOW_SECONDS <= prediction_window <= MAX_PREDICTION_WINDOW_SECONDS
            )
        ):
            raise _invalid(
                f"prediction_window must be between {MIN_PREDICTION_WINDOW_SECONDS} and "
                f"{MAX_PREDICTION_WINDOW_SECONDS} seconds"
            )
        body: dict[str, Any] = {
            "title": _free_text(title, "title", maxlen=MAX_POLL_TITLE_LENGTH),
            "prediction_window": int(prediction_window),
            "outcomes": [
                {"title": _free_text(outcome, "outcome", maxlen=MAX_CHOICE_LENGTH)}
                for outcome in outcomes
            ],
        }
        return await self._write(
            tool="create_prediction",
            group="predictions",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /predictions",
            params=_pairs(broadcaster_id=broadcaster_id),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def end_prediction(
        self,
        *,
        broadcaster_id: str,
        prediction_id: str,
        status: str,
        winning_outcome_id: str | None = None,
        confirm: str | None = None,
    ) -> WriteResult:
        """``PATCH /predictions`` (group ``predictions``)."""
        if status not in PREDICTION_END_STATUSES:
            raise _invalid("status must be RESOLVED or CANCELLED")
        if status == "RESOLVED" and not winning_outcome_id:
            raise _invalid("winning_outcome_id is required when status is RESOLVED")
        arguments: dict[str, Any] = {
            "prediction_id": _opaque(prediction_id, "prediction_id"),
            "status": status,
        }
        body: dict[str, Any] = {
            "broadcaster_id": broadcaster_id,
            "id": arguments["prediction_id"],
            "status": status,
        }
        if winning_outcome_id:
            arguments["winning_outcome_id"] = _opaque(winning_outcome_id, "winning_outcome_id")
            body["winning_outcome_id"] = arguments["winning_outcome_id"]
        return await self._write(
            tool="end_prediction",
            group="predictions",
            broadcaster_id=broadcaster_id,
            method="PATCH",
            template="PATCH /predictions",
            params=_pairs(broadcaster_id=broadcaster_id),
            json_body=body,
            arguments=arguments,
            confirm=confirm,
        )

    async def send_chat_message(
        self,
        *,
        broadcaster_id: str,
        message: str,
        to_chat: bool = True,
        reply_parent_message_id: str | None = None,
        confirm: str | None = None,
    ) -> WriteResult:
        """``POST /chat/messages`` (group ``chat``) — public, irreversible, never retried (R16).

        The message body never reaches a log, an error or the confirmation plan: the plan carries
        its length only (R52/R53). ``user_id`` is the operator's own id from the token, never an
        argument (R29).
        """
        text = _free_text(message, "message", maxlen=MAX_CHAT_MESSAGE_LENGTH)
        if len(text) < MIN_CHAT_MESSAGE_LENGTH:
            raise _invalid(f"message must be at least {MIN_CHAT_MESSAGE_LENGTH} characters")
        body: dict[str, Any] = {"message": text, "to_chat": bool(to_chat)}
        if reply_parent_message_id is not None:
            body["reply_parent_message_id"] = _opaque(
                reply_parent_message_id, "reply_parent_message_id"
            )
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="send_chat_message",
            group="chat",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /chat/messages",
            params=_pairs(broadcaster_id=broadcaster_id, user_id=operator),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def timeout_user(
        self,
        *,
        broadcaster_id: str,
        user_id: str,
        duration: int = 600,
        reason: str | None = None,
        confirm: str | None = None,
    ) -> WriteResult:
        """``POST /moderation/timeouts`` (group ``moderation``) — acts on a third party."""
        target = _validated_user_id(user_id, what="user_id")
        if (
            not isinstance(duration, int)
            or isinstance(duration, bool)
            or not (MIN_TIMEOUT_SECONDS <= duration <= MAX_TIMEOUT_SECONDS)
        ):
            raise _invalid(
                f"duration must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS} seconds"
            )
        body: dict[str, Any] = {"user_id": target, "duration": int(duration)}
        if reason is not None:
            body["reason"] = _free_text(reason, "reason", maxlen=MAX_REASON_LENGTH)
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="timeout_user",
            group="moderation",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /moderation/timeouts",
            params=_pairs(broadcaster_id=broadcaster_id, moderator_id=operator),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def remove_timeout(
        self, *, broadcaster_id: str, user_id: str, confirm: str | None = None
    ) -> WriteResult:
        """``DELETE /moderation/timeouts`` (group ``moderation``)."""
        target = _validated_user_id(user_id, what="user_id")
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="remove_timeout",
            group="moderation",
            broadcaster_id=broadcaster_id,
            method="DELETE",
            template="DELETE /moderation/timeouts",
            params=_pairs(
                broadcaster_id=broadcaster_id, moderator_id=operator, target_user_id=target
            ),
            json_body=None,
            arguments={"target_user_id": target},
            confirm=confirm,
        )

    async def ban_user(
        self,
        *,
        broadcaster_id: str,
        user_id: str,
        reason: str | None = None,
        confirm: str | None = None,
    ) -> WriteResult:
        """``POST /moderation/banned`` (group ``moderation``) — a permanent ban."""
        target = _validated_user_id(user_id, what="user_id")
        body: dict[str, Any] = {"user_id": target}
        if reason is not None:
            body["reason"] = _free_text(reason, "reason", maxlen=MAX_REASON_LENGTH)
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="ban_user",
            group="moderation",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /moderation/banned",
            params=_pairs(broadcaster_id=broadcaster_id, moderator_id=operator),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def unban_user(
        self, *, broadcaster_id: str, user_id: str, confirm: str | None = None
    ) -> WriteResult:
        """``DELETE /moderation/banned`` (group ``moderation``)."""
        target = _validated_user_id(user_id, what="user_id")
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="unban_user",
            group="moderation",
            broadcaster_id=broadcaster_id,
            method="DELETE",
            template="DELETE /moderation/banned",
            params=_pairs(
                broadcaster_id=broadcaster_id, moderator_id=operator, target_user_id=target
            ),
            json_body=None,
            arguments={"target_user_id": target},
            confirm=confirm,
        )

    async def set_automod_term(
        self, *, broadcaster_id: str, text: str, category: str, confirm: str | None = None
    ) -> WriteResult:
        """``PUT /moderation/automod/terms`` (group ``moderation``)."""
        if category not in AUTOMOD_CATEGORIES:
            raise _invalid(
                "category must be one of " + ", ".join(sorted(AUTOMOD_CATEGORIES)),
                hint="These are Helix's own AutoMod category names.",
            )
        body = {"text": _free_text(text, "text", maxlen=MAX_REASON_LENGTH), "category": category}
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="set_automod_term",
            group="moderation",
            broadcaster_id=broadcaster_id,
            method="PUT",
            template="PUT /moderation/automod/terms",
            params=_pairs(broadcaster_id=broadcaster_id, moderator_id=operator),
            json_body=body,
            arguments=body,
            confirm=confirm,
        )

    async def remove_automod_term(
        self, *, broadcaster_id: str, text: str, confirm: str | None = None
    ) -> WriteResult:
        """``DELETE /moderation/automod/terms`` (group ``moderation``).

        Helix identifies the term by its text in the query string, so the text is part of the bound
        argument set: it is hashed into the token and never logged (R52).
        """
        term = _free_text(text, "text", maxlen=MAX_REASON_LENGTH)
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="remove_automod_term",
            group="moderation",
            broadcaster_id=broadcaster_id,
            method="DELETE",
            template="DELETE /moderation/automod/terms",
            params=_pairs(broadcaster_id=broadcaster_id, moderator_id=operator, text=term),
            json_body=None,
            arguments={"text": term},
            confirm=confirm,
        )

    async def add_vip(
        self, *, broadcaster_id: str, user_id: str, confirm: str | None = None
    ) -> WriteResult:
        """``POST /channels/vips`` (group ``roles``)."""
        target = _validated_user_id(user_id, what="user_id")
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="add_vip",
            group="roles",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /channels/vips",
            params=_pairs(broadcaster_id=broadcaster_id, user_id=target, moderator_id=operator),
            json_body=None,
            arguments={"target_user_id": target},
            confirm=confirm,
        )

    async def remove_vip(
        self, *, broadcaster_id: str, user_id: str, confirm: str | None = None
    ) -> WriteResult:
        """``DELETE /channels/vips`` (group ``roles``)."""
        target = _validated_user_id(user_id, what="user_id")
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="remove_vip",
            group="roles",
            broadcaster_id=broadcaster_id,
            method="DELETE",
            template="DELETE /channels/vips",
            params=_pairs(broadcaster_id=broadcaster_id, user_id=target, moderator_id=operator),
            json_body=None,
            arguments={"target_user_id": target},
            confirm=confirm,
        )

    async def add_moderator(
        self, *, broadcaster_id: str, user_id: str, confirm: str | None = None
    ) -> WriteResult:
        """``POST /moderation/moderators`` (group ``roles``)."""
        target = _validated_user_id(user_id, what="user_id")
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="add_moderator",
            group="roles",
            broadcaster_id=broadcaster_id,
            method="POST",
            template="POST /moderation/moderators",
            params=_pairs(broadcaster_id=broadcaster_id, user_id=target, moderator_id=operator),
            json_body=None,
            arguments={"target_user_id": target},
            confirm=confirm,
        )

    async def remove_moderator(
        self, *, broadcaster_id: str, user_id: str, confirm: str | None = None
    ) -> WriteResult:
        """``DELETE /moderation/moderators`` (group ``roles``)."""
        target = _validated_user_id(user_id, what="user_id")
        operator = self._identity.operator.require_present()
        return await self._write(
            tool="remove_moderator",
            group="roles",
            broadcaster_id=broadcaster_id,
            method="DELETE",
            template="DELETE /moderation/moderators",
            params=_pairs(broadcaster_id=broadcaster_id, user_id=target, moderator_id=operator),
            json_body=None,
            arguments={"target_user_id": target},
            confirm=confirm,
        )

    # ------------------------------------------------------------ the write machinery

    def _require_group(self, tool: str, group: str) -> None:
        """R28: a write exists only when both kill switches have been thrown."""
        if not self._settings.writes_enabled or group not in self._settings.write_groups:
            raise TwitchMcpError(
                "disabled",
                DISABLED_WRITE_MESSAGE,
                hint=f"Enable the '{group}' group to use {tool}.",
            )

    async def _write(
        self,
        *,
        tool: str,
        group: str,
        broadcaster_id: str,
        method: str,
        template: str,
        params: Sequence[tuple[str, str]],
        json_body: Any,
        arguments: Mapping[str, Any],
        confirm: str | None,
    ) -> WriteResult:
        """The single write path of SPEC §6.3, in the order the spec fixes.

        ``disabled`` → guard (R25) → identity (R29) → confirmation (R31–R34) → exactly one dispatch.
        A token never bypasses the guard: after it is consumed, the guard runs again (R34).
        """
        self._require_group(tool, group)
        self._guard_broadcaster(broadcaster_id)
        await self._identity.require_writable(broadcaster_id, group)
        bound = dict(arguments)
        if confirm is None:
            minted = self._confirmations.mint(tool, broadcaster_id, bound)
            return self._confirmation_result(tool, minted, bound)
        self._confirmations.consume(tool, broadcaster_id, bound, confirm)
        self._guard_broadcaster(broadcaster_id)  # R34: never bypassed by a token
        bearer = await self._user()
        route = Route.build(_helix_path(template), broadcaster_id, token_type=USER_CLASS)
        response = await self._dispatch(
            method, route, bearer=bearer, params=params, json_body=json_body
        )
        records = [dict(record) for record in project_write_data({"data": response.data})]
        return WriteResult(
            confirmation_required=False,
            dispatched=True,
            data=records,
            message=f"{tool} completed on channel {broadcaster_id}",
        )

    async def _dispatch(
        self,
        method: str,
        route: Route,
        *,
        bearer: str,
        params: Sequence[tuple[str, str]],
        json_body: Any,
    ) -> Any:
        """Call exactly one write verb; the client owns the never-retry rule (R16)."""
        client_id = self._client.client_id
        if method == "POST":
            return await self._client.post(
                route, client_id=client_id, bearer=bearer, json_body=json_body, params=params
            )
        if method == "PATCH":
            return await self._client.patch(
                route, client_id=client_id, bearer=bearer, json_body=json_body, params=params
            )
        if method == "PUT":
            return await self._client.put(
                route, client_id=client_id, bearer=bearer, json_body=json_body, params=params
            )
        if method == "DELETE":
            return await self._client.delete(
                route, client_id=client_id, bearer=bearer, params=params
            )
        raise _invalid(f"{method} is not a method this server issues")

    def _confirmation_result(
        self, tool: str, minted: MintedConfirmation, arguments: Mapping[str, Any]
    ) -> WriteResult:
        """R31: the resolved plan plus the single-use token — and nothing was sent.

        A *successful* (non-``isError``) result: this is the normal first turn of every write (SPEC
        §6.3). The token appears here and nowhere else (R33), and ``arguments`` is a summary rather
        than the free text itself (R52).
        """
        expires_at = self._confirmations.expires_at_wall(minted.expires_at)
        return WriteResult(
            confirmation_required=True,
            dispatched=False,
            confirmation=WriteConfirmation(
                token=minted.token,
                expires_at=datetime.fromtimestamp(expires_at, tz=UTC).isoformat(),
                tool=tool,
                broadcaster_id=minted.plan.broadcaster_id,
                arguments=_summary(**dict(arguments)),
            ),
            message=(
                f"Review this plan, then call {tool} again with the confirm token to perform it. "
                "The token is single-use and bound to these arguments."
            ),
        )


__all__ = [
    "AUTOMOD_CATEGORIES",
    "DISABLED_WRITE_MESSAGE",
    "MAX_FIRST",
    "AppTokenSource",
    "TwitchService",
    "UserTokenSource",
]
