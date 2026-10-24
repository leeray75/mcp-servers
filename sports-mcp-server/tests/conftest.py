"""Shared fixtures. Nothing here touches the network or real credentials."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from sports_mcp.config import Settings
from sports_mcp.espn_client import EspnClient
from sports_mcp.tools import SportsService

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_AUTH_TOKEN = "t" * 16 + "FAKE-MCP-TOKEN" + "u" * 16  # 46 chars, obviously fake
BASE = "https://site.api.espn.com"
SITE = "/apis/site/v2/sports"
V2 = "/apis/v2/sports"
# The fixtures were captured on Sunday 2026-10-04; tests pretend it is 3 pm Eastern that day.
NOW = datetime(2026, 10, 4, 19, 0, tzinfo=UTC)

_ENV_NAMES = (
    "ESPN_BASE_URL",
    "ESPN_TIMEOUT_SECONDS",
    "ESPN_MAX_CONCURRENCY",
    "ESPN_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEZONE",
    "MAX_RESPONSE_CHARS",
    "STALE_IF_ERROR_SECONDS",
    "TTL_LIVE_SECONDS",
    "TTL_SCOREBOARD_IDLE_SECONDS",
    "TTL_SUMMARY_PRE_SECONDS",
    "TTL_SUMMARY_POST_SECONDS",
    "TTL_STANDINGS_SECONDS",
    "TTL_TEAMS_SECONDS",
    "TTL_SCHEDULE_SECONDS",
    "TTL_SCHEDULE_LIVE_SECONDS",
    "TTL_NEWS_SECONDS",
    "TTL_PLAYER_SEARCH_SECONDS",
    "TTL_PLAYER_SECONDS",
    "PLAYER_MAX_SEASONS",
    "CACHE_MAX_ENTRIES",
    "MCP_AUTH_TOKEN",
    "HOST",
    "PORT",
    "LOG_LEVEL",
)


def load(name: str) -> dict[str, Any]:
    """Load a captured ESPN response from tests/fixtures."""
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure the developer's real environment never leaks into tests."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail any request that would leave the process through httpx's real transport."""
    if request.node.get_closest_marker("live"):
        return

    async def refuse(self: object, req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"test attempted a real network request: {req.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


def routes() -> dict[str, str]:
    """Map ``path?query`` (query sorted, as EspnClient builds it) to a fixture name."""
    r: dict[str, str] = {}

    def sb(league: str, date: str, name: str) -> None:
        r[f"{SITE}/{league}/scoreboard?dates={date}"] = name

    sb("football/nfl", "20261004", "nfl_scoreboard_week")
    sb("football/nfl", "20261006", "nfl_scoreboard_empty")
    sb("basketball/nba", "20261004", "nba_scoreboard_pre")
    sb("basketball/nba", "20260605", "nba_scoreboard_final")
    sb("baseball/mlb", "20261004", "mlb_scoreboard_pre")
    sb("baseball/mlb", "20260930", "mlb_scoreboard_final")
    sb("hockey/nhl", "20261004", "nhl_scoreboard_today")
    sb("hockey/nhl", "20261003", "nhl_scoreboard_final")
    sb("soccer/eng.1", "20261004", "epl_scoreboard_pre")
    sb("soccer/eng.1", "20260920", "epl_scoreboard_final")
    sb("basketball/wnba", "20261004", "wnba_scoreboard_live")
    r[f"{SITE}/football/nfl/scoreboard?seasontype=2&week=3"] = "nfl_scoreboard_week"

    for league, event, name in (
        ("football/nfl", "401872971", "nfl_summary_live"),
        ("football/nfl", "401872965", "nfl_summary_final"),
        ("football/nfl", "401872974", "nfl_summary_pre"),
        ("basketball/nba", "401859964", "nba_summary_final"),
        ("basketball/nba", "401914127", "nba_summary_pre"),
        ("baseball/mlb", "401907972", "mlb_summary_final"),
        ("baseball/mlb", "401908003", "mlb_summary_pre"),
        ("hockey/nhl", "401891776", "nhl_summary_final"),
        ("basketball/wnba", "401918295", "wnba_summary_live"),
        ("soccer/eng.1", "401879276", "epl_summary_final"),
        ("soccer/eng.1", "401879268", "epl_summary_pre"),
    ):
        r[f"{SITE}/{league}/summary?event={event}"] = name

    r[f"{V2}/football/nfl/standings?level=3"] = "nfl_standings"
    r[f"{V2}/basketball/nba/standings"] = "nba_standings"
    r[f"{V2}/hockey/nhl/standings"] = "nhl_standings"
    r[f"{V2}/baseball/mlb/standings?level=3"] = "mlb_standings"
    r[f"{V2}/soccer/eng.1/standings"] = "epl_standings"
    r[f"{V2}/football/college-football/standings"] = "ncaaf_standings"
    r[f"{SITE}/football/nfl/teams?limit=1000"] = "nfl_teams"
    r[f"{SITE}/soccer/eng.1/teams?limit=1000"] = "epl_teams"
    r[f"{SITE}/baseball/mlb/teams?limit=1000"] = "mlb_teams"
    r[f"{SITE}/football/nfl/teams/2"] = "nfl_team_buf"
    r[f"{SITE}/football/nfl/teams/2/schedule"] = "nfl_schedule_buf"
    r[f"{SITE}/baseball/mlb/teams/15/schedule"] = "mlb_schedule_atl"
    r[f"{SITE}/soccer/eng.1/teams/359/schedule?fixture=true"] = "epl_schedule_ars_fixtures"
    r[f"{SITE}/soccer/eng.1/teams/359/schedule"] = "epl_schedule_ars"
    r[f"{SITE}/football/nfl/news?limit=5"] = "nfl_news"
    r[f"{SITE}/soccer/eng.1/news?limit=5"] = "epl_news"
    _player_routes(r)
    return r


PLAYERS = "/apis/common/v3/sports"
SEARCH = "/apis/common/v3/search"


def _player_routes(r: dict[str, str]) -> None:
    """Player endpoints. Query strings are the sorted parameters the client actually sends."""
    for query in ("skattebo", "skattebu", "skatteb", "smith"):
        r[f"{SEARCH}?limit=50&query={query}&type=player"] = f"player_search_{query}"
    r[f"{SEARCH}?limit=50&query=Cam+Skattebu&type=player"] = "player_search_cam_skattebu"
    r[f"{SEARCH}?limit=50&query=Cam+Skatteb&type=player"] = "player_search_cam_skatteb"
    r[f"{PLAYERS}/football/nfl/athletes/4696981"] = "player_profile_nfl_skattebo"
    r[f"{PLAYERS}/basketball/nba/athletes/1966"] = "player_profile_nba_lebron"
    skat = f"{PLAYERS}/football/nfl/athletes/4696981"
    r[f"{skat}/gamelog?season=2025"] = "player_gl_nfl_skattebo_2025"
    r[f"{skat}/gamelog"] = "player_gl_nfl_skattebo_2026"
    r[f"{skat}/gamelog?season=2026"] = "player_gl_nfl_skattebo_2026"
    r[f"{skat}/gamelog?season=2015"] = "player_gl_nfl_skattebo_2015"
    r[f"{skat}/stats"] = "player_stats_nfl_skattebo"
    dar = f"{PLAYERS}/football/nfl/athletes/3912547"
    r[f"{dar}/gamelog?season=2025"] = "player_gl_nfl_darnold_2025"
    r[f"{dar}/stats"] = "player_stats_nfl_darnold"
    r[f"{dar}/stats?seasontype=3"] = "player_stats_nfl_darnold_post"
    r[f"{PLAYERS}/football/nfl/athletes/4431452/gamelog?season=2025"] = "player_gl_nfl_maye_2025"
    r[f"{PLAYERS}/football/nfl/athletes/2574056/gamelog?season=2025"] = "player_gl_nfl_byard_2025"
    r[f"{PLAYERS}/football/nfl/athletes/16800/gamelog?season=2025"] = "player_gl_nfl_adams_2025"
    r[f"{PLAYERS}/football/nfl/athletes/16800/stats"] = "player_stats_nfl_adams"
    r[f"{PLAYERS}/basketball/nba/athletes/1966/gamelog?season=2025"] = "player_gl_nba_lebron_2025"
    r[f"{PLAYERS}/basketball/nba/athletes/1966/stats"] = "player_stats_nba_lebron"
    r[f"{PLAYERS}/baseball/mlb/athletes/33192/gamelog?season=2025"] = "player_gl_mlb_judge_2025"
    r[f"{PLAYERS}/baseball/mlb/athletes/39832/stats"] = "player_stats_mlb_ohtani"


def make_buf_game_live(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """The Bills-Patriots game finished after the schedule fixture was captured mid-game; this puts
    it back in progress (state/status only) so live-schedule behaviour stays covered."""
    if name == "nfl_schedule_buf":
        for event in data["events"]:
            if event["id"] == "401872971":
                comp = event["competitions"][0]
                comp["status"] = {
                    "clock": 662.0,
                    "displayClock": "11:02",
                    "period": 4,
                    "type": {
                        "id": "2",
                        "name": "STATUS_IN_PROGRESS",
                        "state": "in",
                        "completed": False,
                        "description": "In Progress",
                        "detail": "11:02 - 4th Quarter",
                        "shortDetail": "11:02 - 4th",
                    },
                }
                for c in comp["competitors"]:
                    c.pop("winner", None)
    return data


class FakeEspn:
    """Stands in for ESPN: serves captured fixtures and records every request."""

    def __init__(self) -> None:
        self.routes = routes()
        self.requests: list[httpx.Request] = []
        self.overrides: dict[str, Any] = {}  # key -> dict body | int status | Exception
        self.default_status: int | None = None
        self.raw_body: bytes | None = None
        self.transform: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None

    @staticmethod
    def key(request: httpx.Request) -> str:
        query = request.url.query.decode()
        return request.url.path + (f"?{query}" if query else "")

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = self.key(request)
        if key in self.overrides:
            override = self.overrides[key]
            if isinstance(override, Exception):
                raise override
            if isinstance(override, int):
                return httpx.Response(override, json={"code": override, "message": "SECRET-BODY"})
            return httpx.Response(200, json=override)
        if self.default_status is not None:
            return httpx.Response(self.default_status, json={"message": "SECRET-BODY"})
        if self.raw_body is not None:
            return httpx.Response(200, content=self.raw_body)
        name = self.routes.get(key)
        if name is None:
            return httpx.Response(404, json={"code": 404, "message": f"no fixture for {key}"})
        data = load(name)
        if self.transform is not None:
            data = self.transform(name, data)
        return httpx.Response(200, json=data)

    @property
    def keys(self) -> list[str]:
        return [self.key(r) for r in self.requests]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def espn() -> FakeEspn:
    return FakeEspn()


@pytest.fixture
def settings() -> Settings:
    return Settings(mcp_auth_token=FAKE_AUTH_TOKEN)  # type: ignore[arg-type]


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def client(settings: Settings, espn: FakeEspn) -> Iterator[EspnClient]:
    yield EspnClient(settings, transport=espn.transport(), sleep=_no_sleep)


@pytest.fixture
def service(settings: Settings, client: EspnClient) -> SportsService:
    return SportsService(settings, client, now=lambda: NOW)
