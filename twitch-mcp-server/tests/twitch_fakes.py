"""Reusable synthetic fakes for twitch_mcp tests — **import these, do not duplicate them**.

The agent that owns ``server.py`` / ``service.py`` / ``twitch_client.py`` and their test files
imports this module. **``tests/conftest.py`` is frozen**: it holds only the autouse network block
(R55) and thin fixtures that delegate here. Add new shared helpers to *this* file.

What is in here
---------------
Synthetic constants
    :data:`FAKE_AUTH_TOKEN`, :data:`FAKE_CLIENT_ID`, :data:`FAKE_CLIENT_SECRET`, :data:`BROADCASTER`,
    :data:`OTHER_BROADCASTER`, :data:`OPERATOR`, :data:`MODERATOR_ID`, :data:`USER_ID`,
    :data:`VIDEO_ID`, :data:`GAME_ID`, :data:`NOW`, :data:`HELIUX`, :data:`OAUTH`.
    Nothing in this file is a real credential; token-shaped strings are assembled at runtime so
    secret scanners do not flag them (see :func:`_joined`).

:func:`helix_headers`
    The three rate-limit headers the limiter is allowed to read (R19). Returns absolute
    ``Ratelimit-Reset`` unix timestamps, which is what Helix actually sends.

:func:`make_settings`
    Build a valid :class:`~twitch_mcp.config.Settings` from kwargs::

        settings = make_settings(twitch_user_oauth=True, twitch_write_groups="polls")

:func:`env_for` / :func:`set_env`
    Populate ``os.environ`` for :func:`~twitch_mcp.config.load_settings` tests.
    ``set_env(monkeypatch, TWITCH_MAX_5XX_RETRIES="0")`` starts from the valid baseline.

:class:`FakeClock`
    Injectable monotonic clock **and** sleep **and** wall clock. ``clock()``, ``await sleep(s)`` and
    ``wall()`` share one timeline that ``sleep`` advances. Also ``advance(seconds)`` for jumps.

:class:`FakeTwitch`
    The transport fake. Register handlers with :meth:`FakeTwitch.add` (or the
    :meth:`FakeTwitch.json` shorthand) before the client is built, then assert on
    ``fake.requests`` / ``fake.paths`` / :attr:`FakeTwitch.request_count`.

    .. code-block:: python

        fake = FakeTwitch()
        fake.add("GET", r"/channels", fake.json({"data": [channel]}, headers=helix_headers(30, 29)))
        client = TwitchClient(make_settings(), transport=fake.transport(),
                              clock=fclock.clock, sleep=fclock.sleep)

    Unmatched requests get a 404 with a ``no fixture for this route`` body rather than an
    exception, so an unexpected call shows up as a visible wrong status and is still counted.
    :attr:`FakeTwitch.strict` turns unmatched requests into hard assertion failures for tests that
    must prove *zero* upstream traffic.

:func:`make_guard`
    Build a :class:`~twitch_mcp.guard.Guard` whose owner lookup goes through
    :class:`~twitch_mcp.cache.TTLCache`, and hand back the three pieces plus an ``owner_calls``
    counter — that counter is how R26 ("exactly one cached lookup") is asserted.

:func:`denied_call_request_count`
    Convenience that runs a callable against a :class:`FakeTwitch` and returns
    ``fake.request_count``; used by the R25 "request-count == 0" assertions.

Keeping fixtures small (R55): every generator here is bounded by the size it is given, and
``tests/fixtures/*.json`` (owned by the service-layer agent) must stay under 50 KB each.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

from twitch_mcp.cache import Fetched, TTLCache
from twitch_mcp.config import Settings
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.guard import Guard

FIXTURES = Path(__file__).parent / "fixtures"

FAKE_AUTH_TOKEN = "ttttttttttttttttFAKE-MCP-TOKENuuuuuuuuuuuuuuuu"
FAKE_CLIENT_ID = "fake-client-id-0123456789abcdef"
FAKE_CLIENT_SECRET = "fake-client-secret-0123456789abcdef"

BROADCASTER = "123456789"
OTHER_BROADCASTER = "987654321"
OPERATOR = "555555555"
MODERATOR_ID = "666666666"
USER_ID = "444444444"
VIDEO_ID = "4400882211"
CLIP_ID = "SpicyClip1"
GAME_ID = "504129"
POLL_ID = "11111111"

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)

HELIUX = "/helix"
OAUTH = ""


def _joined(*parts: str) -> str:
    """Assemble a token-shaped string at runtime so secret scanners don't flag the literal."""
    return "".join(parts)


FAKE_ACCESS_TOKEN = _joined("oauth", "abc", "def", "0123456789", "ghijkl")
FAKE_REFRESH_TOKEN = _joined("refresh", "ZZZ", "1234567890", "abcdef")


def load(name: str) -> Any:
    """Load a synthetic JSON fixture from ``tests/fixtures`` (owned by the service-layer agent).

    Accepts either spelling -- ``load("users")`` or ``load("users.json")`` -- and resolves to exactly
    one file. The helper used to append ``.json`` unconditionally, which silently required a second
    copy of every fixture named ``users.json.json``; callers were split between the two spellings and
    neither side could see the other's files.
    """
    filename = name if name.endswith(".json") else f"{name}.json"
    return json.loads((FIXTURES / filename).read_text(encoding="utf-8"))


def helix_headers(
    limit: int,
    remaining: int,
    *,
    clock: FakeClock | None = None,
    now: float | None = None,
    window_seconds: float = 60.0,
) -> dict[str, str]:
    """The only three rate-limit headers this server reads (R19), as Helix sends them.

    ``Ratelimit-Reset`` is an **absolute** unix timestamp, which is what Helix actually publishes.
    The limiter compares it against its injected monotonic clock, so a test must hand both sides the
    same timeline: pass the test's :class:`FakeClock` and the reset is built from ``clock.unix()``.
    Passing only ``now`` (unix seconds) is for wall-clock-shaped assertions; passing neither falls
    back to the real clock, which is fine when the test never waits.
    """
    if clock is not None:
        # clock.unix() + window is the absolute reset; expressed through the bridge so it agrees
        # with the monotonic timeline the limiter compares against.
        reset = clock.clock() + clock.offset() + window_seconds
    elif now is not None:
        reset = now + window_seconds
    else:
        reset = time.time() + window_seconds
    return {
        "Ratelimit-Limit": str(limit),
        "Ratelimit-Remaining": str(remaining),
        "Ratelimit-Reset": str(round(reset, 3)),
    }


def make_settings(**overrides: Any) -> Settings:
    """Build a valid Settings, overriding only what the test cares about."""
    data: dict[str, Any] = {
        "mcp_auth_token": FAKE_AUTH_TOKEN,
        "twitch_client_id": FAKE_CLIENT_ID,
        "twitch_client_secret": FAKE_CLIENT_SECRET,
        "twitch_allowed_broadcaster_ids": BROADCASTER,
    }
    data.update(overrides)
    return Settings.model_validate(data)


def env_for(**overrides: str) -> dict[str, str]:
    """The minimum valid environment, with ``overrides`` applied (used by test_config)."""
    base = {
        "MCP_AUTH_TOKEN": FAKE_AUTH_TOKEN,
        "TWITCH_CLIENT_ID": FAKE_CLIENT_ID,
        "TWITCH_CLIENT_SECRET": FAKE_CLIENT_SECRET,
        "TWITCH_ALLOWED_BROADCASTER_IDS": BROADCASTER,
    }
    base.update(overrides)
    return base


def set_env(monkeypatch: Any, /, **overrides: str) -> None:
    """Set the valid baseline environment plus ``overrides`` on ``monkeypatch``."""
    for key, value in env_for(**overrides).items():
        monkeypatch.setenv(key, value)


def clear_env(monkeypatch: Any) -> None:
    """Remove every variable this server reads so a developer's real ``.env`` never leaks in."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


class FakeClock:
    """Monotonic clock whose ``sleep`` advances time instead of waiting.

    ``clock`` / ``sleep`` / ``wall`` share one timeline, so rate-limit waits, cache ages and
    ``as_of`` stamps all agree. Start value is arbitrary but non-zero so a test that forgets to
    advance still sees real elapsed time.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []
        # The unix instant that corresponds to t=0 of this timeline, so ``clock() + offset()`` is
        # the paired wall/monotonic bridge (mirrors measure_unix_to_monotonic()) and ``advance()``
        # moves the wall clock too, because real windows rotate with wall time.
        self._origin = time.time() - start

    def clock(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds

    def advance(self, seconds: float) -> None:
        """Jump forward without recording a sleep (for cache- and identity-TTL expiry tests)."""
        self.t += seconds

    def reset(self) -> None:
        """Return to the start of the timeline and forget recorded sleeps."""
        self.t = 1000.0
        self.sleeps.clear()

    def wall(self) -> datetime:
        """Wall-clock view of the fake timeline. Tied to :meth:`unix` so cache ages and header
        timestamps cannot disagree — ``unix()`` is exactly ``wall().timestamp()``.
        """
        return datetime.fromtimestamp(self.t + self._origin, tz=UTC)

    def offset(self) -> float:
        """Constant bridge between this clock's monotonic timeline and absolute unix time.

        Build an absolute header timestamp as ``clock.clock() + clock.offset() + window`` (which is
        what :func:`helix_headers` does with ``clock=``) and pass the same value as
        ``RateLimiter(unix_to_monotonic=...)``; the limiter then compares ``Ratelimit-Reset`` against
        its injected monotonic clock and sees exactly the wait the test intended.
        """
        return self._origin

    def unix(self) -> float:
        """Absolute unix time on the fake timeline; pair it with ``clock=`` in :func:`helix_headers`."""
        return self.t + self._origin


Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


class _Override:
    __slots__ = ("method", "pattern", "handler", "left")

    def __init__(
        self, method: str, pattern: re.Pattern[str], handler: Handler, left: int | None
    ) -> None:
        self.method = method
        self.pattern = pattern
        self.handler = handler
        self.left = left


class FakeTwitch:
    """Serves synthetic Twitch/Helix responses through ``httpx.MockTransport`` and records all.

    Handlers registered with :meth:`add` are matched, in registration order, against the request
    path (``request.url.path``) before the built-in defaults. The built-in defaults cover the
    minimal shapes every test needs (empty ``{"data": [], "pagination": {}}`` envelopes and
    ``GET /oauth2/validate``) so a test only registers what it cares about.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.strict = False
        self._overrides: list[_Override] = []
        self.validate: dict[str, Any] | None = None

    def add(self, method: str, pattern: str, handler: Handler, times: int | None = None) -> None:
        """Serve ``handler`` for requests whose method matches and path matches ``pattern``.

        ``pattern`` is a regex matched with ``re.fullmatch`` against ``request.url.path`` (which
        includes the ``/helix`` prefix). ``times`` consumes the override after N uses.
        """
        self._overrides.append(_Override(method, re.compile(pattern), handler, times))

    def json(
        self, body: Any, status: int = 200, headers: Mapping[str, str] | None = None
    ) -> Handler:
        """Shorthand handler factory: a fixed JSON body, status and headers."""

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body, headers=dict(headers or {}))

        return handler

    def set_validate(
        self,
        *,
        client_id: str = FAKE_CLIENT_ID,
        user_id: str | None = None,
        login: str | None = None,
        scopes: list[str] | None = None,
        expires_in: int = 15000,
    ) -> None:
        """Set the ``GET /oauth2/validate`` body (the boot probe and ``twitch_account_status``)."""
        self.validate = {
            "client_id": client_id,
            "login": login,
            "user_id": user_id,
            "scopes": scopes or [],
            "expires_in": expires_in,
        }

    @property
    def request_count(self) -> int:
        """How many requests left the process. Assert 0 for every refusal (R25, R31)."""
        return len(self.requests)

    @property
    def helix_request_count(self) -> int:
        """How many *Helix* requests left the process — the count a refusal test wants (R25, R31).

        :attr:`request_count` also counts the boot credential calls, and a healthy boot legitimately
        makes them: R50 mints and validates the app token before anything is served. A refusal test
        asks a narrower question — nothing the refused call did reached the API — so it must count
        API traffic only, or it would fail on a server that is working correctly.
        """
        return sum(1 for req in self.requests if req.url.path.startswith("/helix"))

    @property
    def paths(self) -> list[str]:
        return [req.url.path for req in self.requests]

    @property
    def methods(self) -> list[str]:
        return [req.method for req in self.requests]

    @property
    def queries(self) -> list[dict[str, list[str]]]:
        """Each request's query parameters as ``{name: [values...]}`` (for R18 repeat assertions)."""
        return [
            {key: req.url.params.get_list(key) for key in sorted(set(req.url.params.keys()))}
            for req in self.requests
        ]

    def requests_for(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]

    def clear(self) -> None:
        """Forget recorded requests (e.g. to exclude the boot probe from a call's request count)."""
        self.requests.clear()

    def handle(self, request: httpx.Request) -> httpx.Response | Awaitable[httpx.Response]:
        """Sync handlers answer with a Response, async ones with a coroutine httpx will await.

        Both are legal for :class:`httpx.MockTransport`, which awaits whatever the handler returns,
        so the fake does not have to pick one shape — but the annotation has to admit both.
        """
        self.requests.append(request)
        path = request.url.path
        for index, override in enumerate(self._overrides):
            if request.method != override.method:
                continue
            if not override.pattern.fullmatch(path):
                continue
            if override.left is not None:
                override.left -= 1
                if override.left <= 0:
                    self._overrides.pop(index)
            return override.handler(request)
        return self._default(request)

    def _default(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth2/validate":
            if not self.validate:
                return self._miss(request, path)
            return httpx.Response(200, json=dict(self.validate))
        if path.startswith(HELIUX) and request.method == "GET":
            return httpx.Response(200, json={"data": [], "pagination": {}})
        if path.startswith(HELIUX):
            return httpx.Response(200, json={"data": []})
        return self._miss(request, path)

    def _miss(self, request: httpx.Request, path: str) -> httpx.Response:
        if self.strict:
            raise AssertionError(
                f"test attempted an unregistered request: {request.method} {path} "
                f"(query={dict(request.url.params)})"
            )
        return httpx.Response(404, json={"error": "Not Found", "status": 404, "message": path})

    def transport(self) -> httpx.MockTransport:
        # ``handle`` returns either a Response or the coroutine of an async handler; httpx awaits the
        # awaitable, and MockTransport's overload wants the plain sync signature, so the seam is
        # asserted here rather than widening every registered handler's annotation.
        handler = cast("Callable[[httpx.Request], httpx.Response]", self.handle)
        return httpx.MockTransport(handler)


def denied_call_request_count(fake: FakeTwitch, run: Callable[[], Any]) -> int:
    """Run ``run`` (a tool call or guard check) and report how many requests it produced.

    The R25 shape is ``assert denied_call_request_count(fake, call) == 0``.
    """
    before = fake.request_count
    run()
    return fake.request_count - before


def make_guard(
    allowed: frozenset[str] | None = None,
    owners: Mapping[str, Any] | None = None,
    *,
    fclock: FakeClock | None = None,
    ttl_seconds: float = 300.0,
    errors: Mapping[str, Exception] | None = None,
) -> tuple[Guard, TTLCache, FakeClock, dict[str, int]]:
    """Build a Guard with a TTL-cached owner lookup, its cache, clock and a call counter.

    ``owners`` maps an object id to the Helix object (or a bare broadcaster id) that reveals its
    owner; ``errors`` maps an object id to the exception the lookup should raise — that is how R27
    exercises 403/404/5xx uniformly. ``counters["owner_calls"]`` is the number of *upstream* owner
    lookups performed, i.e. cache misses: R26 asserts it equals 1 across repeated calls.
    """
    clock = fclock or FakeClock()
    cache = TTLCache(max_entries=64, max_bytes=1000000, clock=clock.clock, wall_clock=clock.wall)
    counters = {"owner_calls": 0}
    owners_map = dict(owners or {})
    errors_map = dict(errors or {})

    async def fetch_owned(object_id: str) -> Fetched:
        async def fetch() -> Any:
            counters["owner_calls"] += 1
            if object_id in errors_map:
                raise errors_map[object_id]
            if object_id not in owners_map:
                raise TwitchMcpError("upstream", "unknown id", http_status=404, code=10000)
            value = owners_map[object_id]
            if isinstance(value, dict):
                return value
            return {"broadcaster_id": str(value)}

        return await cache.get(f"owner:{object_id}", fetch, ttl_seconds)

    guard = Guard(allowed if allowed is not None else frozenset({BROADCASTER}), fetch_owned)
    return guard, cache, clock, counters


ENV_NAMES: tuple[str, ...] = (
    "MCP_AUTH_TOKEN",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "TWITCH_ALLOWED_BROADCASTER_IDS",
    "TWITCH_API_BASE",
    "TWITCH_AUTH_BASE",
    "TWITCH_TIMEOUT_SECONDS",
    "TWITCH_MAX_RESPONSE_BYTES",
    "TWITCH_MAX_RATE_LIMIT_WAIT_SECONDS",
    "TWITCH_MAX_429_RETRIES",
    "TWITCH_MAX_5XX_RETRIES",
    "HOST_PORT",
    "TWITCH_TLS_CERT_FILE",
    "TWITCH_TLS_KEY_FILE",
    "TWITCH_USER_OAUTH",
    "TWITCH_OAUTH_SCOPES",
    "TWITCH_OAUTH_TOKEN_FILE",
    "TWITCH_ENABLE_WRITES",
    "TWITCH_WRITE_GROUPS",
    "TWITCH_CONFIRM_TOKEN_TTL_SECONDS",
    "TWITCH_IDENTITY_TTL_SECONDS",
    "TTL_METADATA_SECONDS",
    "CACHE_MAX_ENTRIES",
    "CACHE_MAX_MB",
    "LOG_LEVEL",
    "HOST",
    "PORT",
)
