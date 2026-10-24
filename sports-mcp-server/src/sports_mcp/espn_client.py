"""The ONLY module that imports httpx / touches the network.

Every request goes to the host of ``ESPN_BASE_URL``. Path segments come from the league
registry or from values validated by strict patterns upstream (digits-only ids, parsed
dates); this module re-checks the final URL against the host allow-list anyway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx

from sports_mcp import __version__
from sports_mcp.config import Settings
from sports_mcp.errors import InvalidInputError, NotFoundError, UpstreamError
from sports_mcp.leagues import League

logger = logging.getLogger(__name__)

SITE_V2: Final = "/apis/site/v2/sports"
V2: Final = "/apis/v2/sports"
PLAYERS: Final = "/apis/common/v3/sports"
PLAYER_SEARCH: Final = "/apis/common/v3/search"
_PLAYER_QUERY: Final = re.compile(r"[\w .'-]{1,60}")
_PLAYER_SEASON_TYPES: Final = frozenset({"regular", "post"})
_PLAYER_SEASON_MIN: Final = 1990
_PLAYER_SEASON_MAX: Final = 2100
_RETRY_STATUS: Final = range(500, 600)
_ID: Final = frozenset("0123456789")

MSG_UNEXPECTED: Final = "ESPN returned an unexpected response"
MSG_UNAVAILABLE: Final = "ESPN is unavailable"
MSG_RATE_LIMITED: Final = "ESPN rate-limited this server; retry shortly"


def _digits(value: str, what: str) -> str:
    if not value or len(value) > 20 or not set(value) <= _ID:
        # Tool-level validation already rejected bad agent input; this guards ESPN-derived ids.
        logger.warning("rejected id", extra={"outcome": what.replace(" ", "_")})
        raise UpstreamError(MSG_UNEXPECTED)
    return value


class _Retryable(Exception):
    """Internal: a failure worth one retry."""

    def __init__(self, kind: str, message: str, status: int | None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.message = message
        self.status = status


class EspnClient:
    """Thin async client for the handful of ESPN endpoints the tools need."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._base = settings.espn_base_url
        self._host = settings.espn_host
        self._max_bytes = settings.espn_max_response_bytes
        self._sem = asyncio.Semaphore(settings.espn_max_concurrency)
        self._sleep = sleep
        timeout = settings.espn_timeout_seconds
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            follow_redirects=False,
            headers={
                "User-Agent": f"sports-mcp/{__version__} (+local)",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ URLs

    def build_url(self, path: str, params: Mapping[str, str | int] | None = None) -> str:
        """Join the base URL, a path and query parameters, then enforce the host allow-list."""
        query = f"?{urlencode(sorted((params or {}).items()))}" if params else ""
        url = f"{self._base}{path}{query}"
        if urlsplit(url).netloc.lower() != self._host:
            raise UpstreamError(MSG_UNEXPECTED)
        return url

    def scoreboard_url(
        self,
        league: League,
        *,
        date: str | None = None,
        week: int | None = None,
        season_type: int | None = None,
        limit: int | None = None,
    ) -> str:
        params: dict[str, str | int] = {}
        if week is not None:
            params["week"] = week
            if season_type is not None:
                params["seasontype"] = season_type
        elif date is not None:
            params["dates"] = date
        if limit is not None:
            params["limit"] = limit
        if league.scoreboard_groups is not None:
            params["groups"] = league.scoreboard_groups
        return self.build_url(f"{SITE_V2}/{league.path}/scoreboard", params)

    def summary_url(self, league: League, event_id: str) -> str:
        return self.build_url(
            f"{SITE_V2}/{league.path}/summary", {"event": _digits(event_id, "game id")}
        )

    def teams_url(self, league: League) -> str:
        return self.build_url(f"{SITE_V2}/{league.path}/teams", {"limit": 1000})

    def team_url(self, league: League, team_id: str) -> str:
        return self.build_url(f"{SITE_V2}/{league.path}/teams/{_digits(team_id, 'team id')}")

    def schedule_url(
        self,
        league: League,
        team_id: str,
        season: int | None = None,
        season_type: int | None = None,
        fixture: bool = False,
    ) -> str:
        """Team schedule; ``fixture=True`` adds upcoming matches (soccer omits them otherwise)."""
        params: dict[str, str | int] = {}
        if season is not None:
            params["season"] = season
        if season_type is not None:
            params["seasontype"] = season_type
        if fixture:
            params["fixture"] = "true"
        return self.build_url(
            f"{SITE_V2}/{league.path}/teams/{_digits(team_id, 'team id')}/schedule", params
        )

    def standings_url(self, league: League, season: int | None = None) -> str:
        params: dict[str, str | int] = {}
        if league.standings_level is not None:
            params["level"] = league.standings_level
        if season is not None:
            params["season"] = season
        return self.build_url(f"{V2}/{league.path}/standings", params)

    def news_url(self, league: League, limit: int) -> str:
        return self.build_url(f"{SITE_V2}/{league.path}/news", {"limit": limit})

    # Player endpoints (common/v3). The search query is the only free text sent upstream: a
    # URL-encoded query parameter, length-limited and restricted to letters, digits and . ' -.

    def player_search_url(self, query: str, limit: int) -> str:
        if not _PLAYER_QUERY.fullmatch(query) or "_" in query:
            raise InvalidInputError("Player name must be 1-60 letters, digits, spaces, . ' or -.")
        return self.build_url(PLAYER_SEARCH, {"query": query, "limit": limit, "type": "player"})

    def player_profile_url(self, league: League, athlete_id: str) -> str:
        return self.build_url(
            f"{PLAYERS}/{league.path}/athletes/{_digits(athlete_id, 'athlete id')}"
        )

    def player_stats_url(self, league: League, athlete_id: str, season_type: str) -> str:
        """Season stats. ``post`` adds ``seasontype=3`` (ESPN honours it on this endpoint only)."""
        if season_type not in _PLAYER_SEASON_TYPES:
            raise InvalidInputError("season_type must be one of: regular, post.")
        params: dict[str, str | int] = {"seasontype": 3} if season_type == "post" else {}
        return self.build_url(
            f"{PLAYERS}/{league.path}/athletes/{_digits(athlete_id, 'athlete id')}/stats", params
        )

    def player_gamelog_url(self, league: League, athlete_id: str, season: int | None) -> str:
        """Game log for one season (``None`` = ESPN's current season). Carries both season types."""
        params: dict[str, str | int] = {}
        if season is not None:
            if not _PLAYER_SEASON_MIN <= season <= _PLAYER_SEASON_MAX:
                raise InvalidInputError("season must be a year from 1990 to 2100.")
            params["season"] = season
        return self.build_url(
            f"{PLAYERS}/{league.path}/athletes/{_digits(athlete_id, 'athlete id')}/gamelog", params
        )

    # ------------------------------------------------------------------ fetching

    async def get_json(self, url: str, *, id_endpoint: bool = False) -> dict[str, Any]:
        """GET ``url`` and return the decoded JSON object.

        Args:
            url: a URL produced by one of the ``*_url`` builders.
            id_endpoint: the endpoint addresses one object by id, so ESPN's 400/404 means
                "no such object".

        Raises:
            NotFoundError: ESPN has no such object (only when ``id_endpoint``).
            UpstreamError: any other failure; the message never contains upstream data.
        """
        if urlsplit(url).netloc.lower() != self._host:
            raise UpstreamError(MSG_UNEXPECTED)
        failure: _Retryable | None = None
        for attempt in range(2):
            try:
                return await self._once(url, id_endpoint=id_endpoint)
            except _Retryable as exc:
                failure = exc
                logger.warning("upstream retry", extra={"outcome": exc.kind, "status": exc.status})
                if attempt == 0:
                    await self._sleep(random.uniform(0.2, 0.6))  # noqa: S311 - jitter only
        raise UpstreamError(failure.message if failure else MSG_UNAVAILABLE)

    async def _once(self, url: str, *, id_endpoint: bool) -> dict[str, Any]:
        async with self._sem:
            try:
                async with self._http.stream("GET", url) as response:
                    status = response.status_code
                    if status in _RETRY_STATUS:
                        raise _Retryable("http_5xx", MSG_UNAVAILABLE, status)
                    if status == 429:
                        raise UpstreamError(MSG_RATE_LIMITED)
                    if id_endpoint and status in (400, 404):
                        raise NotFoundError("ESPN has no such object")
                    if status != 200:
                        raise UpstreamError(f"ESPN returned HTTP {status}")
                    body = await self._read_capped(response)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise _Retryable(type(exc).__name__, MSG_UNAVAILABLE, None) from None
            except httpx.HTTPError:
                raise UpstreamError(MSG_UNAVAILABLE) from None
        try:
            data = json.loads(body)
        except ValueError:
            raise UpstreamError(MSG_UNEXPECTED) from None
        if not isinstance(data, dict):
            raise UpstreamError(MSG_UNEXPECTED)
        return data

    async def _read_capped(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise UpstreamError(MSG_UNEXPECTED)
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise UpstreamError(MSG_UNEXPECTED)
            chunks.append(chunk)
        return b"".join(chunks)
