"""Regression tests for review round 1 (docs/reports/review-round-1.md), one per finding."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

from sports_mcp import render
from sports_mcp.cache import TTLCache, estimate_bytes
from sports_mcp.config import ConfigError, Settings, load_settings
from sports_mcp.dates import resolve_date
from sports_mcp.errors import InvalidInputError, UpstreamError
from sports_mcp.espn_client import EspnClient
from sports_mcp.leagues import resolve_league
from sports_mcp.models import Game, Status, TeamLine
from sports_mcp.normalize import standings as standings_mod
from sports_mcp.normalize import teams as teams_mod
from sports_mcp.normalize.scoreboard import normalize_event, season_info
from sports_mcp.server import create_server
from sports_mcp.tools import SportsService
from tests.conftest import (
    FAKE_AUTH_TOKEN,
    NOW,
    FakeEspn,
    _no_sleep,
    load,
    make_buf_game_live,
)

NY = ZoneInfo("America/New_York")
NFL = resolve_league("nfl")


# ---- R1-01 / R1-10: standings season label, conference record


async def test_r1_01_mlb_standings_use_the_table_season_not_next_years(
    service: SportsService,
) -> None:
    raw = load("mlb_standings")
    assert raw["season"]["year"] == 2027  # ESPN's top level is already next year
    out = await service.get_standings("mlb")
    assert out.data["season"] == 2026
    assert out.text.splitlines()[0].startswith("MLB standings 2026 (")


async def test_r1_10_two_year_seasons_are_named(service: SportsService) -> None:
    nba = await service.get_standings("nba")
    assert nba.data["season"] == 2027 and nba.data["season_name"] == "2026-27"
    assert nba.text.splitlines()[0].startswith("NBA standings 2026-27 (")
    epl = await service.get_standings("epl")  # ESPN's long display name is not used
    assert epl.data["season"] == 2026 and "season_name" not in epl.data
    assert standings_mod.standings_season_name({}) is None


async def test_r1_10_college_tables_show_the_conference_record(service: SportsService) -> None:
    out = await service.get_standings("college-football")
    first = out.data["groups"][0]["entries"][0]
    assert "conf" in first["stats"] and "record" in first["stats"]
    assert "conf" in out.text.splitlines()[2]
    pro = await service.get_standings("nfl")
    assert "conf" not in pro.data["groups"][0]["entries"][0]["stats"]


def test_r1_10_season_argument_is_documented_as_end_year() -> None:
    from sports_mcp.server import SEASON_HELP

    assert "END year" in SEASON_HELP and "2025-26" in SEASON_HELP


# ---- R1-02: soccer upcoming fixtures


async def test_r1_02_soccer_schedule_includes_upcoming_fixtures(
    service: SportsService, espn: FakeEspn
) -> None:
    out = await service.get_team_schedule("epl", "Arsenal")
    d = out.data
    assert d["count"] == 5 + 33  # played matches plus upcoming fixtures
    assert d["next_game"]["status"]["state"] == "pre" and d["next_game"]["game_id"] == "401879268"
    assert d["last_game"]["status"]["completed"] is True
    assert "Next:" in out.text
    assert any(k.endswith("teams/359/schedule?fixture=true") for k in espn.keys)
    upcoming = await service.get_team_schedule("epl", "Arsenal", status="upcoming", limit=3)
    assert upcoming.data["count"] == 3


async def test_r1_02_missing_fixtures_are_reported_not_hidden(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides["/apis/site/v2/sports/soccer/eng.1/teams/359/schedule?fixture=true"] = 503
    out = await service.get_team_schedule("epl", "Arsenal")
    assert out.data["count"] == 5 and "next_game" not in out.data
    assert "Upcoming fixtures could not be fetched" in out.data["note"]


async def test_r1_02_other_sports_do_not_request_fixtures(
    service: SportsService, espn: FakeEspn
) -> None:
    await service.get_team_schedule("mlb", "Braves")
    assert not any("fixture" in k for k in espn.keys)


def test_r1_02_schedule_url_builder(client: EspnClient) -> None:
    epl = resolve_league("epl")
    assert client.schedule_url(epl, "359", fixture=True).endswith("/schedule?fixture=true")
    url = client.schedule_url(epl, "359", 2025, 2, fixture=True)
    assert url.endswith("/schedule?fixture=true&season=2025&seasontype=2")


# ---- R1-03: cache bounded by bytes


async def _fill(cache: TTLCache, key: str, payload: Any) -> None:
    async def fetch() -> Any:
        return payload

    await cache.get(key, fetch, lambda _v: 60)


async def test_r1_03_cache_is_bounded_by_bytes_with_lru_eviction() -> None:
    payload = {"x": "a" * 10_000}
    size = estimate_bytes(payload)
    cache = TTLCache(max_entries=100, max_bytes=int(size * 2.5))
    await _fill(cache, "a", payload)
    await _fill(cache, "b", payload)
    await _fill(cache, "a", payload)  # still fresh: a becomes most recently used
    await _fill(cache, "c", payload)  # over budget: b (least recently used) is evicted
    assert len(cache) == 2 and cache.total_bytes == 2 * size
    assert cache.total_bytes <= int(size * 2.5)
    calls = {"n": 0}

    async def fetch() -> Any:
        calls["n"] += 1
        return payload

    await cache.get("a", fetch, lambda _v: 60)
    assert calls["n"] == 0  # a survived
    await cache.get("b", fetch, lambda _v: 60)
    assert calls["n"] == 1  # b was evicted


async def test_r1_03_a_payload_larger_than_the_budget_is_served_but_not_kept() -> None:
    cache = TTLCache(max_bytes=1000)
    result = await cache.get("big", _constant({"x": "a" * 5000}), lambda _v: 60)
    assert result.value["x"].startswith("aaa")
    assert len(cache) == 0 and cache.total_bytes == 0


def _constant(value: Any) -> Any:
    async def fetch() -> Any:
        return value

    return fetch


async def test_r1_03_replacing_and_clearing_keep_the_byte_count_exact() -> None:
    cache = TTLCache()
    await _fill(cache, "k", {"v": "x" * 100})
    first = cache.total_bytes
    await cache.get("k", _constant({"v": "x" * 400}), lambda _v: 60, refresh=True)
    assert cache.total_bytes > first and len(cache) == 1
    cache.clear()
    assert cache.total_bytes == 0


def test_r1_03_estimate_tracks_payload_size() -> None:
    assert estimate_bytes({"a": "x" * 1000}) > estimate_bytes({"a": "x" * 10}) * 10


async def test_r1_03_service_uses_the_configured_budget(espn: FakeEspn) -> None:
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN, cache_max_mb=8)  # type: ignore[arg-type]
    svc = SportsService(settings, EspnClient(settings, transport=espn.transport()), now=lambda: NOW)
    for game in ("401872965", "401872971", "401872974"):
        await svc.get_game("nfl", game)
    assert 0 < svc.cache.total_bytes <= 8 * 1024 * 1024


def test_r1_03_default_is_well_under_the_container_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", FAKE_AUTH_TOKEN)
    assert load_settings().cache_max_mb == 96
    monkeypatch.setenv("CACHE_MAX_MB", "500")
    with pytest.raises(ConfigError, match="CACHE_MAX_MB"):
        load_settings()


# ---- R1-04: TBD kickoff times


async def test_r1_04_tbd_time_is_not_shown_as_midnight(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.transform = make_buf_game_live
    out = await service.get_team_schedule("nfl", "BUF")
    week18 = out.data["games"][-1]
    assert week18["time_tbd"] is True and "start_local" not in week18
    assert "12:00 AM" not in out.text
    assert "TBD [Week 18]" in out.text


def test_r1_04_scoreboard_event_with_unconfirmed_time() -> None:
    raw = deepcopy(load("nfl_scoreboard_week"))
    event = next(e for e in raw["events"] if e["status"]["type"]["state"] == "pre")
    event["competitions"][0]["timeValid"] = False
    game = normalize_event(event, NFL, NY)
    assert game is not None and game.time_tbd is True and game.start_local is None
    assert render.status_text(game, NFL, NY) == "TBD"


# ---- R1-05 / R1-06: season_type handling


async def test_r1_05_season_type_without_week_is_explained(service: SportsService) -> None:
    out = await service.get_scoreboard("nfl", season_type=3)
    assert "season_type only applies together with week" in out.data["note"]


def test_r1_06_season_type_comes_from_the_season_actually_returned() -> None:
    raw = deepcopy(load("nfl_scoreboard_week"))
    assert season_info(raw).type == "Regular Season"  # type: ignore[union-attr]
    raw["season"] = {"type": 1, "year": 2026}
    info = season_info(raw)
    assert info is not None and info.type == "Preseason"


# ---- R1-07: schedule header names the season type


async def test_r1_07_schedule_header_names_the_season_type(service: SportsService) -> None:
    out = await service.get_team_schedule("mlb", "Braves")
    assert out.text.splitlines()[0].startswith("Atlanta Braves schedule, 2026 Postseason (")
    nfl = await service.get_team_schedule("nfl", "BUF")
    assert "schedule, 2026 Regular Season (" in nfl.text.splitlines()[0]
    epl = await service.get_team_schedule("epl", "Arsenal")
    assert "schedule, 2026-27 English Premier League (" in epl.text.splitlines()[0]


async def test_r1_07_season_type_argument_selects_another_season(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides["/apis/site/v2/sports/baseball/mlb/teams/15/schedule?seasontype=2"] = {
        **load("mlb_schedule_atl"),
        "requestedSeason": {
            "year": 2026,
            "type": 2,
            "name": "Regular Season",
            "displayName": "2026",
        },
    }
    out = await service.get_team_schedule("mlb", "Braves", season_type=2)
    assert "schedule, 2026 Regular Season (" in out.text.splitlines()[0]


# ---- R1-08: postponed games


def test_r1_08_postponed_games_are_not_final_and_have_no_placeholder_score() -> None:
    raw = deepcopy(load("nfl_scoreboard_week"))
    event = next(e for e in raw["events"] if e["status"]["type"]["state"] == "pre")
    event["status"]["type"].update(
        {"state": "post", "name": "STATUS_POSTPONED", "completed": False, "detail": "Postponed"}
    )
    for c in event["competitions"][0]["competitors"]:
        c["score"] = "0"
    game = normalize_event(event, NFL, NY)
    assert game is not None and all(t.score is None for t in game.teams)
    line = render.game_line(game, NFL, NY)
    assert line.startswith("Postponed") and " 0 " not in line
    done = normalize_event(next(e for e in raw["events"] if e["id"] == "401872965"), NFL, NY)
    assert done is not None
    assert render.counts([game, done]) == "1 final, 1 postponed/canceled"


# ---- R1-09: football performers


async def test_r1_09_defenders_ranked_by_tackles_and_lines_are_labelled(
    service: SportsService,
) -> None:
    out = await service.get_game("nfl", "401872965", detail="performers", top_n=15)
    wsh = out.data["performers"]["WSH"]
    defenders = [p for p in wsh if p["line"].startswith("def:")]
    assert defenders[0]["player"] == "Amik Robertson" and defenders[0]["line"].startswith(
        "def: 9 tot"
    )
    assert all(p["line"].split(":")[0] in {"pass", "rush", "rec", "def", "K"} for p in wsh)
    mariota = [p["line"] for p in wsh if p["player"] == "Marcus Mariota"]
    assert len(mariota) == 2 and {line.split(":")[0] for line in mariota} == {"pass", "rush"}
    texts = [p["line"] for people in out.data["performers"].values() for p in people]
    assert not any(" 1 sacks" in t or ", 1 sacks" in t for t in texts)
    assert any("1 sack" in t and "sacks" not in t for t in texts)


def test_r1_09_plural_helper() -> None:
    from sports_mcp.normalize.players import _count

    assert [_count(v, "sack") for v in ("1", "2", "0.5", None)] == [
        "1 sack",
        "2 sacks",
        "0.5 sacks",
        "0 sacks",
    ]


async def test_r1_09_hockey_goalie_line_grammar(service: SportsService) -> None:
    out = await service.get_game("nhl", "401891776", detail="performers", top_n=15)
    lines = [p["line"] for ps in out.data["performers"].values() for p in ps]
    assert any(" saves on " in t and " shots, " in t for t in lines)
    assert not any(" 1 shots" in t for t in lines)


# ---- R1-11: overtime labels


@pytest.mark.parametrize(
    ("family", "period", "expected"),
    [
        ("football", 4, "Q4"),
        ("football", 5, "OT"),
        ("football", 6, "OT2"),
        ("basketball", 5, "OT"),
        ("hockey", 3, "P3"),
        ("hockey", 4, "OT"),
        ("hockey", 5, "OT2/SO"),
        ("baseball", 10, "10"),
        ("soccer", 2, ""),
        ("football", None, ""),
    ],
)
def test_r1_11_period_labels(family: str, period: int | None, expected: str) -> None:
    assert render.period_label(family, period) == expected


async def test_r1_11_overtime_goal_is_labelled(service: SportsService, espn: FakeEspn) -> None:
    def to_ot(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nhl_summary_final":
            for play in data["plays"]:
                if play.get("scoringPlay"):
                    play["period"]["number"] = 4
        return data

    espn.transform = to_ot
    out = await service.get_game("nhl", "401891776")
    assert " OT " in out.text and "\n P4 " not in out.text


# ---- R1-12: notes rendered


async def test_r1_12_playoff_notes_reach_the_text(service: SportsService) -> None:
    board = await service.get_scoreboard("wnba")
    assert "[Semifinals - Game 1]" in board.text


async def test_r1_12_notes_in_game_header(service: SportsService, espn: FakeEspn) -> None:
    def add_note(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "wnba_summary_live":
            data["header"]["competitions"][0]["notes"] = [{"headline": "Semifinals - Game 1"}]
        return data

    espn.transform = add_note
    out = await service.get_game("wnba", "401918295")
    assert out.text.splitlines()[2] == "Semifinals - Game 1"


# ---- R1-13: limit beyond 100


async def test_r1_13_limit_can_raise_the_default_cap(
    service: SportsService, espn: FakeEspn
) -> None:
    def many(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nfl_scoreboard_week":
            data["events"] = data["events"] * 10  # 160 games
        return data

    espn.transform = many
    default = await service.get_scoreboard("nfl")
    assert default.data["count"] == 100 and "limit=<n> (max 500)" in default.data["note"]
    more = await service.get_scoreboard("nfl", limit=150)
    assert more.data["count"] == 150 and "note" not in more.data
    all_of_them = await service.get_scoreboard("nfl", limit=500)
    assert all_of_them.data["count"] == 160


# ---- R1-14: soccer pass accuracy


async def test_r1_14_pass_accuracy_is_a_real_percentage(service: SportsService) -> None:
    out = await service.get_game("epl", "401879276")
    row = next(s for s in out.data["team_stats"] if s["label"] == "Pass accuracy")
    assert (row["away"], row["home"]) == ("81%", "78%")  # 384/474 and 297/382


# ---- R1-15: malformed shapes


async def test_r1_15_summary_without_two_competitors_is_an_unexpected_response(
    service: SportsService, espn: FakeEspn
) -> None:
    def broken(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nba_summary_final":
            data["header"]["competitions"][0]["competitors"] = []
        return data

    espn.transform = broken
    with pytest.raises(UpstreamError, match="unexpected response"):
        await service.get_game("nba", "401859964")


async def test_r1_15_scoreboard_rows_without_an_id_are_skipped(
    service: SportsService, espn: FakeEspn
) -> None:
    def junk(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nfl_scoreboard_week":
            data["events"].append(
                {"competitions": [{"competitors": [{"team": {"abbreviation": "A"}}]}]}
            )
        return data

    espn.transform = junk
    out = await service.get_scoreboard("nfl")
    assert out.data["count"] == 16 and "None=" not in out.text


# ---- R1-16 / R1-17: schema warning, short validation messages


async def test_r1_16_no_output_schema_warning_at_startup(
    settings: Settings, service: SportsService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        server = create_server(settings, service)
    assert not [r for r in caplog.records if "Cannot create schema" in r.getMessage()]
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert all(t.output_schema is None for t in tools)


async def test_r1_17_validation_errors_are_one_short_line(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        bad_id = await client.call_tool("get_game", {"league": "nfl", "game_id": "abc"})
        two = await client.call_tool("get_news", {"league": "nfl", "limit": 99, "refresh": "x"})
    text = str(bad_id.content[0].text)  # type: ignore[union-attr]
    assert bad_id.is_error and text.startswith("Invalid arguments for get_game: game_id:")
    assert "errors.pydantic.dev" not in text and "input_value" not in text and len(text) < 200
    two_text = str(two.content[0].text)  # type: ignore[union-attr]
    assert "limit:" in two_text and "refresh:" in two_text and "\n" not in two_text


# ---- R1-18: ASCII digits, messages, id errors


def test_r1_18_non_ascii_digits_are_not_dates() -> None:
    with pytest.raises(InvalidInputError):
        resolve_date("٢٠٢٦-١٠-٠٤", NY, NOW)
    with pytest.raises(InvalidInputError):
        resolve_date("٢٠٢٦١٠٠٤", NY, NOW)


def test_r1_18_team_query_message_matches_the_pattern() -> None:
    with pytest.raises(InvalidInputError) as info:
        teams_mod.validate_query("a/b")
    assert "/" not in str(info.value)


# ---- R1-19: tidy-ups


def test_r1_19_cryptic_labels_and_dead_code_are_gone() -> None:
    assert not hasattr(render, "now_label") and not hasattr(teams_mod, "find")
    labels = {
        "Sacks-yds lost",
        "Penalties-yds",
    }
    from sports_mcp.normalize.sports import _TEAM_STATS

    assert labels <= {label for _, label in _TEAM_STATS["football"]}


def test_r1_19_pre_game_start_wording() -> None:
    game = Game(
        id="1",
        start_utc="2026-10-04T20:05:00Z",
        start_local="2026-10-04T16:05:00-0400",
        status=Status(state="pre"),
        teams=[TeamLine(home_away="away", abbr="A"), TeamLine(home_away="home", abbr="B")],
    )
    from sports_mcp.models import GameDetail, Meta

    detail = GameDetail(
        league="nfl",
        detail="summary",
        game=game,
        meta=Meta(
            as_of="2026-10-04T19:00:00Z", cache_age_seconds=0, cached=False, endpoint_kind="s"
        ),
    )
    text = render.render_game(detail, NFL, NY)
    assert "starts 4:05 PM EDT" in text and "kickoff" not in text


async def test_unexpected_upstream_id_is_an_upstream_error_not_an_internal_error(
    service: SportsService, espn: FakeEspn
) -> None:
    def bad_ids(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nfl_teams":
            data["sports"][0]["leagues"][0]["teams"][0]["team"]["id"] = "12x"
            data["sports"][0]["leagues"][0]["teams"][0]["team"]["abbreviation"] = "ZZZ"
        return data

    espn.transform = bad_ids
    with pytest.raises(UpstreamError, match="unexpected response"):
        await service.get_team_schedule("nfl", "ZZZ")


async def test_no_sleep_helper_is_importable() -> None:
    await _no_sleep(0)
