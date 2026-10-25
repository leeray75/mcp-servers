"""Service-level tests: validation, fetch/cache behaviour and result shapes (offline)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from sports_mcp import render
from sports_mcp.cache import TTLCache
from sports_mcp.config import Settings
from sports_mcp.errors import InvalidInputError, NotFoundError, UpstreamError
from sports_mcp.espn_client import EspnClient
from sports_mcp.tools import SportsService
from tests.conftest import (
    FAKE_AUTH_TOKEN,
    NOW,
    FakeEspn,
    _no_sleep,
    load,
    make_buf_game_live,
)

NFL_BOARD = "/apis/site/v2/sports/football/nfl/scoreboard?dates=20261004"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def mono(self) -> float:
        return self.t


def make_service(espn: FakeEspn, clock: Clock | None = None, **env: Any) -> SportsService:
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN, **env)  # type: ignore[arg-type]
    cache = None
    if clock is not None:
        cache = TTLCache(
            settings.cache_max_entries, settings.stale_if_error_seconds, clock=clock.mono
        )
    client = EspnClient(settings, transport=espn.transport(), sleep=_no_sleep)
    return SportsService(settings, client, cache, now=lambda: NOW)


# ------------------------------------------------------------------ list_leagues


async def test_list_leagues(service: SportsService, espn: FakeEspn) -> None:
    out = await service.list_leagues()
    assert out.data["count"] == 15 and len(out.data["leagues"]) == 15
    nfl = out.data["leagues"][0]
    assert nfl["id"] == "nfl" and nfl["sport"] == "football" and nfl["kind"] == "team"
    assert "epl" in out.text and "ncaaf" in out.text
    assert espn.requests == []  # static data


# ------------------------------------------------------------------ get_scoreboard


async def test_scoreboard_shape_and_meta(service: SportsService) -> None:
    out = await service.get_scoreboard("NFL")
    data = out.data
    assert data["league"] == "nfl" and data["date"] == "2026-10-04" and data["count"] == 16
    assert data["season"] == {"year": 2026, "type": "Regular Season", "name": "2026"}
    meta = data["meta"]
    assert meta["source"] == "espn" and meta["endpoint_kind"] == "scoreboard"
    assert meta["cached"] is False and meta["cache_age_seconds"] == 0
    assert meta["as_of"].endswith("Z") and "stale" not in meta
    assert out.cache == "miss" and out.league == "nfl"
    assert "16 games (8 live, 2 final, 6 upcoming)" in out.text
    assert "fetched just now" in out.text
    assert "FINAL" in out.text and "MIN -10 O/U 38.5" in out.text
    # nulls are dropped, not returned as placeholders
    assert "null" not in json.dumps(data)


async def test_scoreboard_second_call_is_cached_and_age_is_accurate(espn: FakeEspn) -> None:
    clock = Clock()
    svc = make_service(espn, clock)
    first = await svc.get_scoreboard("nfl")
    clock.t += 7
    second = await svc.get_scoreboard("nfl")
    assert len(espn.requests) == 1
    assert second.data["meta"]["cached"] is True and second.data["meta"]["cache_age_seconds"] == 7
    assert second.data["meta"]["as_of"] == first.data["meta"]["as_of"]
    assert second.cache == "hit" and "cached 7s" in second.text


async def test_live_scoreboard_ttl_is_short_idle_is_longer(espn: FakeEspn) -> None:
    clock = Clock()
    svc = make_service(espn, clock)
    await svc.get_scoreboard("nfl")  # has live games: 15 s
    clock.t += 16
    await svc.get_scoreboard("nfl")
    assert len(espn.requests) == 2
    await svc.get_scoreboard("nba")  # only pre games: 120 s
    clock.t += 100
    await svc.get_scoreboard("nba")
    assert len(espn.requests) == 3
    clock.t += 30
    await svc.get_scoreboard("nba")
    assert len(espn.requests) == 4


async def test_refresh_bypasses_cache(service: SportsService, espn: FakeEspn) -> None:
    await service.get_scoreboard("nfl")
    await service.get_scoreboard("nfl", refresh=True)
    assert espn.keys.count(NFL_BOARD) == 2


async def test_status_filters_and_limit(service: SportsService) -> None:
    live = await service.get_scoreboard("nfl", status="live")
    assert live.data["count"] == 8 and all(g["status"]["state"] == "in" for g in live.data["games"])
    final = await service.get_scoreboard("nfl", status="final")
    assert final.data["count"] == 2
    upcoming = await service.get_scoreboard("nfl", status="upcoming", limit=3)
    assert upcoming.data["count"] == 3
    with pytest.raises(InvalidInputError, match="status"):
        await service.get_scoreboard("nfl", status="bogus")


async def test_empty_day_is_not_an_error(service: SportsService) -> None:
    out = await service.get_scoreboard("nfl", date="2026-10-06")
    assert out.data["games"] == [] and out.data["count"] == 0
    assert out.data["note"].startswith("No nfl games on 2026-10-06.")
    assert "season runs" in out.data["note"]
    assert "0 games" in out.text


async def test_date_words_and_formats(service: SportsService, espn: FakeEspn) -> None:
    await service.get_scoreboard("hockey" if False else "nhl", date="yesterday")
    assert espn.keys[-1].endswith("scoreboard?dates=20261003")
    out = await service.get_scoreboard("mlb", date="20260930")
    assert out.data["date"] == "2026-09-30"


async def test_week_and_season_type_for_football(service: SportsService, espn: FakeEspn) -> None:
    out = await service.get_scoreboard("nfl", week=3, season_type=2)
    assert espn.keys[-1].endswith("scoreboard?seasontype=2&week=3")
    assert out.data["date"].startswith("week 3")
    assert "ignores the date" in out.data["note"]


async def test_week_is_ignored_with_a_note_outside_football(
    service: SportsService, espn: FakeEspn
) -> None:
    out = await service.get_scoreboard("nba", week=3)
    assert "scoreboard?dates=20261004" in espn.keys[-1]
    assert "only to football" in out.data["note"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"league": "cricket"},
        {"league": "nfl", "date": "someday"},
        {"league": "nfl", "date": "2026-02-30"},
        {"league": "../nfl"},
    ],
)
async def test_invalid_scoreboard_input_never_reaches_espn(
    service: SportsService, espn: FakeEspn, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_scoreboard(**kwargs)
    assert espn.requests == []


async def test_upstream_failure_without_cache_is_an_error(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.default_status = 503
    with pytest.raises(UpstreamError, match="unavailable"):
        await service.get_scoreboard("nfl")


async def test_stale_copy_is_served_and_labelled_when_espn_fails(espn: FakeEspn) -> None:
    clock = Clock()
    svc = make_service(espn, clock)
    await svc.get_scoreboard("nfl")
    clock.t += 120  # expired (15 s live TTL) but inside the 300 s stale window
    espn.default_status = 503
    out = await svc.get_scoreboard("nfl")
    assert out.data["meta"]["stale"] is True and out.cache == "stale"
    assert out.data["meta"]["cache_age_seconds"] == 120
    assert "live fetch from ESPN failed" in out.data["meta"]["note"]
    assert "STALE" in out.text
    clock.t += 400
    with pytest.raises(UpstreamError):
        await svc.get_scoreboard("nfl")


async def test_failures_are_not_cached(espn: FakeEspn) -> None:
    clock = Clock()
    svc = make_service(espn, clock, stale_if_error_seconds=0)
    espn.default_status = 503
    with pytest.raises(UpstreamError):
        await svc.get_scoreboard("nfl")
    espn.default_status = None
    out = await svc.get_scoreboard("nfl")
    assert out.data["count"] == 16


# ------------------------------------------------------------------ get_game


@pytest.mark.parametrize("bad", ["", "abc", "12 3", "../1", "1?x=2", "1\n2", "9" * 21])
async def test_bad_game_ids_are_rejected_before_any_request(
    service: SportsService, espn: FakeEspn, bad: str
) -> None:
    with pytest.raises(InvalidInputError, match="digits"):
        await service.get_game("nfl", bad)
    assert espn.requests == []


async def test_bad_detail_level(service: SportsService) -> None:
    with pytest.raises(InvalidInputError, match="detail"):
        await service.get_game("nfl", "401872965", detail="everything")


async def test_unknown_game_is_a_clean_not_found(service: SportsService, espn: FakeEspn) -> None:
    espn.default_status = 404
    with pytest.raises(NotFoundError) as info:
        await service.get_game("nfl", "1")
    assert str(info.value) == "Game not found for league nfl"
    assert "SECRET-BODY" not in str(info.value)
    espn.default_status = 400
    with pytest.raises(NotFoundError, match="nfl"):
        await service.get_game("nfl", "999999999999")


async def test_summary_without_header_is_not_found(service: SportsService, espn: FakeEspn) -> None:
    espn.overrides["/apis/site/v2/sports/football/nfl/summary?event=5"] = {"boxscore": {}}
    with pytest.raises(NotFoundError):
        await service.get_game("nfl", "5")


async def test_game_summary_final_nfl(service: SportsService) -> None:
    out = await service.get_game("nfl", "401872965")
    d = out.data
    assert d["detail"] == "summary" and d["game"]["status"]["completed"] is True
    assert len(d["scoring_plays"]) == 9 and len(d["team_stats"]) <= 15 and len(d["leaders"]) <= 12
    assert d["injuries"]["IND"] and d["attendance"] == 60907
    assert d["odds"]["favorite_abbr"] == "IND" and d["odds_basis"] == "closing"
    assert d["odds"]["open"]["home_spread"] == -1.5
    assert d["win_probability"]["home_pct"] >= 0  # latest ESPN value, present for post too
    assert "performers" not in d and "boxscore" not in d and "basis" not in d
    assert d["meta"]["endpoint_kind"] == "summary"
    assert "Closing lines" in out.text and "Scoring:" in out.text


async def test_game_summary_live_nfl(service: SportsService) -> None:
    out = await service.get_game("nfl", "401872971")
    d = out.data
    assert d["game"]["status"]["state"] == "in" and d["game"]["status"]["clock"]
    assert d["win_probability"]["home_pct"] + d["win_probability"]["away_pct"] == pytest.approx(
        100, abs=0.2
    )
    assert d["weather"]["temperature_f"]
    assert d["odds_basis"] == "closing"


async def test_game_summary_pre_never_presents_season_data_as_game_data(
    service: SportsService,
) -> None:
    out = await service.get_game("nfl", "401872974", detail="boxscore")
    d = out.data
    assert d["game"]["status"]["state"] == "pre"
    assert "team_stats" not in d and "scoring_plays" not in d
    assert all(ld["category"].startswith("Season ") for ld in d["leaders"])
    assert d["odds_basis"] == "current"
    assert "performers" not in d and "boxscore" not in d and "basis" not in d
    assert "not available before the game starts" in d["note"]


async def test_mlb_pre_game_player_stats_are_season_totals_and_labelled(
    service: SportsService,
) -> None:
    out = await service.get_game("mlb", "401908003", detail="performers")
    assert out.data["basis"] == "season"
    first = out.data["performers"]["SD"][0]
    assert first["player"] == "Fernando Tatis Jr." and first["line"].startswith("181-626")
    assert "SEASON stats, not this game" in out.text
    box = await service.get_game("mlb", "401908003", detail="boxscore")
    assert box.data["basis"] == "season" and "SEASON stats" in box.text


async def test_pre_game_placeholder_stats_are_omitted(
    service: SportsService, espn: FakeEspn
) -> None:
    def blank(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "mlb_summary_pre":
            for team in data["boxscore"]["players"]:
                for group in team["statistics"]:
                    for athlete in group["athletes"]:
                        athlete["stats"] = ["--" for _ in athlete["stats"]]
        return data

    espn.transform = blank
    out = await service.get_game("mlb", "401908003", detail="performers")
    assert "performers" not in out.data and "basis" not in out.data
    assert "before the game starts" in out.data["note"]


async def test_nfl_performers_top_n(service: SportsService) -> None:
    out = await service.get_game("nfl", "401872965", detail="performers", top_n=5)
    assert out.data["basis"] == "game"
    perf = out.data["performers"]
    assert set(perf) == {"IND", "WSH"}
    assert all(1 <= len(v) <= 5 for v in perf.values())
    ind = perf["IND"]
    assert ind[0]["player"] == "Daniel Jones" and ind[0]["line"] == "pass: 19/34, 143 yds, 1 INT"
    assert any(p["line"].startswith("rush: 20 car, 95 yds") for p in ind)
    small = await service.get_game("nfl", "401872965", detail="performers", top_n=2)
    assert all(len(v) <= 2 for v in small.data["performers"].values())
    assert len(out.text.encode()) <= 12_000


async def test_performers_for_every_sport(service: SportsService) -> None:
    nba = await service.get_game("nba", "401859964", detail="performers", top_n=3)
    top = nba.data["performers"]["SA"][0]
    assert top["player"] == "Victor Wembanyama" and top["line"].startswith("29 pts, 9 reb, 2 ast")
    wnba = await service.get_game("wnba", "401918295", detail="performers")
    assert wnba.data["game"]["status"]["state"] == "in" and wnba.data["performers"]
    mlb = await service.get_game("mlb", "401907972", detail="performers", top_n=4)
    lines = [p["line"] for people in mlb.data["performers"].values() for p in people]
    assert any(" IP, " in line for line in lines) and any("-" in line for line in lines)
    nhl = await service.get_game("nhl", "401891776", detail="performers")
    assert any("saves on" in p["line"] for ps in nhl.data["performers"].values() for p in ps)
    epl = await service.get_game("epl", "401879276", detail="performers")
    assert epl.data["performers"]["LIV"][0]["player"] == "Alexander Isak"
    assert epl.data["performers"]["LIV"][0]["line"] == "1 G, 1 SOT, 1 yellow"


async def test_boxscore_full_tables(service: SportsService) -> None:
    out = await service.get_game("nba", "401859964", detail="boxscore")
    box = out.data["boxscore"]
    assert set(box) == {"NY", "SA"}
    group = box["SA"][0]
    assert group["labels"][:3] == ["MIN", "PTS", "FG"]
    assert group["rows"][0]["player"] and len(group["rows"][0]["stats"]) == len(group["labels"])
    assert out.data["basis"] == "game" and "truncated" not in out.data
    nfl = await service.get_game("nfl", "401872965", detail="boxscore")
    assert {g["group"] for g in nfl.data["boxscore"]["IND"]} >= {"passing", "rushing", "receiving"}
    soccer = await service.get_game("epl", "401879276", detail="boxscore")
    assert soccer.data["boxscore"]["LIV"][0]["group"] == "players"


async def test_boxscore_is_truncated_to_max_chars_with_a_note(service: SportsService) -> None:
    out = await service.get_game("nfl", "401872965", detail="boxscore", max_chars=2500)
    assert len(out.text) <= 2500
    assert out.data["truncated"] is True
    assert "truncated" in out.data["note"] and "max_chars=2500" in out.data["note"]
    rows = sum(len(g["rows"]) for groups in out.data["boxscore"].values() for g in groups)
    full = await service.get_game("nfl", "401872965", detail="boxscore")
    full_rows = sum(len(g["rows"]) for groups in full.data["boxscore"].values() for g in groups)
    assert rows < full_rows


async def test_boxscore_tiny_cap_still_fits(service: SportsService) -> None:
    out = await service.get_game("nfl", "401872965", detail="boxscore", max_chars=1000)
    assert len(out.text) <= 1000


async def test_game_without_player_stats_says_so(service: SportsService, espn: FakeEspn) -> None:
    def strip(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nba_summary_final":
            data["boxscore"].pop("players", None)
        return data

    espn.transform = strip
    out = await service.get_game("nba", "401859964", detail="performers")
    assert "performers" not in out.data and "no player stats" in out.data["note"]


async def test_game_cache_ttl_by_state(espn: FakeEspn) -> None:
    clock = Clock()
    svc = make_service(espn, clock)
    await svc.get_game("nfl", "401872971")  # in progress: 15 s
    clock.t += 16
    await svc.get_game("nfl", "401872971")
    assert len(espn.requests) == 2
    await svc.get_game("nfl", "401872965")  # post: 1 hour
    clock.t += 3000
    await svc.get_game("nfl", "401872965")
    assert len(espn.requests) == 3
    await svc.get_game("nfl", "401872974")  # pre: 120 s
    clock.t += 121
    await svc.get_game("nfl", "401872974")
    assert len(espn.requests) == 5


# ------------------------------------------------------------------ get_odds


async def test_odds_for_a_day_labels_every_game(service: SportsService) -> None:
    out = await service.get_odds("nfl")
    d = out.data
    assert d["count"] == 16 and "ESPN removes betting lines once a game starts" in d["note"]
    by_state: dict[str, list[dict[str, Any]]] = {}
    for g in d["games"]:
        by_state.setdefault(g["status"]["state"], []).append(g)
    assert all(g["lines_available"] and g["lines_basis"] == "current" for g in by_state["pre"])
    assert all(
        not g["lines_available"] and g["lines_basis"] == "unavailable" for g in by_state["in"]
    )
    mia = next(g for g in d["games"] if g["id"] == "401872974")
    assert mia["lines"]["favorite_abbr"] == "MIN" and mia["lines"]["spread"] == -10.0
    assert (
        mia["lines"]["open"]["home_spread"] == -7.5
        and mia["lines"]["close"]["home_spread"] == -10.0
    )
    assert "pass its game_id" in d["note"]
    assert "negative = favorite" in out.text


async def test_odds_for_one_started_game_returns_the_labelled_closing_line(
    service: SportsService, espn: FakeEspn
) -> None:
    out = await service.get_odds("nfl", game_id="401872971")
    (game,) = out.data["games"]
    assert (
        game["status"]["state"] == "in"
        and game["lines_basis"] == "closing"
        and game["lines_available"]
    )
    assert game["lines"]["favorite_abbr"] == "BUF" and game["lines"]["spread"] == -7.0
    assert espn.keys[-1].endswith("summary?event=401872971")
    assert "date" not in out.data
    assert "pass its game_id" not in out.data["note"]


async def test_odds_unavailable_game(service: SportsService) -> None:
    out = await service.get_odds("nba", game_id="401914127")
    (game,) = out.data["games"]
    assert game["lines_basis"] == "unavailable" and game["lines_available"] is False
    assert "lines" not in game


async def test_odds_bad_game_id_and_unknown_game(service: SportsService, espn: FakeEspn) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_odds("nfl", game_id="x1")
    espn.default_status = 404
    with pytest.raises(NotFoundError):
        await service.get_odds("nfl", game_id="1")


async def test_odds_unknown_game_without_header(service: SportsService, espn: FakeEspn) -> None:
    espn.overrides["/apis/site/v2/sports/football/nfl/summary?event=6"] = {}
    with pytest.raises(NotFoundError):
        await service.get_odds("nfl", game_id="6")


async def test_odds_soccer_has_draw_price(service: SportsService) -> None:
    out = await service.get_odds("epl", game_id="401879268")
    (game,) = out.data["games"]
    assert game["lines"]["draw_moneyline"] == 390 and game["lines"]["favorite_abbr"] == "ARS"


# ------------------------------------------------------------------ standings


async def test_standings_group_filter(service: SportsService) -> None:
    out = await service.get_standings("nfl", group="afc east")
    assert [g["name"] for g in out.data["groups"]] == ["AFC East"]
    assert out.data["season"] == 2026
    both = await service.get_standings("nfl", group="AFC")
    assert len(both.data["groups"]) == 4
    none = await service.get_standings("nfl", group="Atlantis")
    assert none.data["groups"] == [] and none.data["available_groups"]
    assert "No group matches" in none.data["note"]


async def test_standings_full_nfl_text_budget(service: SportsService) -> None:
    out = await service.get_standings("nfl")
    assert len(out.data["groups"]) == 8
    assert len(out.text.encode()) <= 8_000


async def test_standings_for_other_sports(service: SportsService, espn: FakeEspn) -> None:
    assert (await service.get_standings("epl")).data["groups"][0]["entries"][0]["stats"]["points"]
    assert espn.keys[-1].endswith("/soccer/eng.1/standings")
    await service.get_standings("mlb")
    assert espn.keys[-1].endswith("/baseball/mlb/standings?level=3")
    await service.get_standings("nhl")
    espn.routes["/apis/v2/sports/basketball/nba/standings?season=2025"] = "nba_standings"
    await service.get_standings("nba", season=2025)
    assert espn.keys[-1].endswith("/basketball/nba/standings?season=2025")


async def test_large_standings_are_truncated_with_a_hint(
    service: SportsService, espn: FakeEspn
) -> None:
    def many(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "ncaaf_standings":
            first = data["children"][0]
            data["children"] = [
                {**first, "name": f"Conference {i}", "abbreviation": f"C{i}"} for i in range(12)
            ]
        return data

    espn.transform = many
    out = await service.get_standings("college-football")
    d = out.data
    assert d["truncated"] is True and len(d["available_groups"]) == 12
    assert 0 < len(d["groups"]) < 12
    assert sum(len(g["entries"]) for g in d["groups"]) <= 100
    assert "Pass group=" in d["note"]
    assert len(out.text) <= 20_000
    pick = await service.get_standings("college-football", group="c7")
    assert [g["name"] for g in pick.data["groups"]] == ["Conference 7"]  # by abbreviation


@pytest.mark.parametrize("group", ["  ", "x" * 61, "a\nb", "../x", "a;b"])
async def test_standings_bad_group(service: SportsService, espn: FakeEspn, group: str) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_standings("nfl", group=group)
    assert espn.requests == []


# ------------------------------------------------------------------ find_team


async def test_find_team_enriches_a_unique_match(service: SportsService, espn: FakeEspn) -> None:
    out = await service.find_team("nfl", "bills")
    (match,) = out.data["matches"]
    assert match["id"] == "2" and match["abbr"] == "BUF" and match["record"] == "3-0"
    assert match["standing_summary"] == "1st in AFC East"
    assert any(k.endswith("/teams/2") for k in espn.keys)
    assert out.data["meta"]["endpoint_kind"] == "teams"


async def test_find_team_ambiguous_and_no_match(service: SportsService) -> None:
    out = await service.find_team("nfl", "new york")
    assert {m["abbr"] for m in out.data["matches"]} == {"NYG", "NYJ"}
    assert "record" not in out.data["matches"][0]  # ambiguous: not enriched
    none = await service.find_team("nfl", "zzzz")
    assert none.data["matches"] == [] and "No nfl team matches" in none.data["note"]


async def test_find_team_enrichment_failure_is_ignored(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides["/apis/site/v2/sports/football/nfl/teams/2"] = 500
    out = await service.find_team("nfl", "bills")
    assert out.data["matches"][0]["abbr"] == "BUF" and "record" not in out.data["matches"][0]


@pytest.mark.parametrize("query", ["", "../x", "a\nb", "x" * 61])
async def test_find_team_rejects_odd_queries(
    service: SportsService, espn: FakeEspn, query: str
) -> None:
    with pytest.raises(InvalidInputError):
        await service.find_team("nfl", query)
    assert not any("teams" in k for k in espn.keys)


async def test_team_directory(service: SportsService) -> None:
    teams = await service.team_directory("epl")
    assert any(t.abbr == "ARS" for t in teams)


# ------------------------------------------------------------------ get_team_schedule


async def test_schedule_for_a_team(service: SportsService, espn: FakeEspn) -> None:
    espn.transform = make_buf_game_live
    out = await service.get_team_schedule("nfl", "Buffalo")
    d = out.data
    assert d["team"]["abbr"] == "BUF" and d["count"] == 17 and d["season"]["year"] == 2026
    assert d["next_game"]["opponent_abbr"] == "NE" and d["next_game"]["status"]["state"] == "in"
    assert d["last_game"]["result"] == "W 24-16"
    assert d["meta"]["endpoint_kind"] == "schedule"
    assert "Next:" in out.text and "Last:" in out.text
    assert espn.keys[-1].endswith("/teams/2/schedule")


async def test_schedule_filters_and_limits(service: SportsService, espn: FakeEspn) -> None:
    espn.transform = make_buf_game_live
    final = await service.get_team_schedule("nfl", "BUF", status="final", limit=2)
    assert [g["result"] for g in final.data["games"]] == ["W 41-31", "W 24-16"]  # the latest two
    upcoming = await service.get_team_schedule("nfl", "BUF", status="upcoming", limit=2)
    assert upcoming.data["count"] == 2 and upcoming.data["games"][0]["status"]["state"] == "pre"
    live = await service.get_team_schedule("nfl", "BUF", status="live")
    assert live.data["count"] == 1
    first = await service.get_team_schedule("nfl", "BUF", limit=1)
    assert first.data["count"] == 1 and first.data["games"][0]["label"] == "Week 1"


async def test_schedule_empty_filter_has_a_note(service: SportsService) -> None:
    out = await service.get_team_schedule("mlb", "Braves", status="live")
    assert out.data["games"] == [] and "No live games" in out.data["note"]


async def test_schedule_ambiguous_unknown_and_missing(
    service: SportsService, espn: FakeEspn
) -> None:
    with pytest.raises(InvalidInputError, match="Ambiguous"):
        await service.get_team_schedule("nfl", "New York")
    with pytest.raises(InvalidInputError, match="find_team"):
        await service.get_team_schedule("nfl", "zzzz")
    with pytest.raises(InvalidInputError, match="status"):
        await service.get_team_schedule("nfl", "BUF", status="x")
    espn.overrides["/apis/site/v2/sports/football/nfl/teams/2/schedule"] = 404
    with pytest.raises(NotFoundError, match="Buffalo Bills"):
        await service.get_team_schedule("nfl", "BUF")


async def test_schedule_season_param(service: SportsService, espn: FakeEspn) -> None:
    espn.overrides["/apis/site/v2/sports/football/nfl/teams/2/schedule?season=2025"] = {
        **load("nfl_schedule_buf"),
        "requestedSeason": {"year": 2025, "name": "Regular Season"},
    }
    out = await service.get_team_schedule("nfl", "BUF", season=2025)
    assert out.data["season"]["year"] == 2025


async def test_schedule_ttl_is_shorter_while_live(espn: FakeEspn) -> None:
    clock = Clock()
    espn.transform = make_buf_game_live
    svc = make_service(espn, clock)
    await svc.get_team_schedule("nfl", "BUF")  # a game is live: 60 s
    n = len(espn.requests)
    clock.t += 61
    await svc.get_team_schedule("nfl", "BUF")
    assert len(espn.requests) == n + 1  # schedule refetched, teams list still cached
    await svc.get_team_schedule("epl", "Arsenal")  # idle: 300 s
    n = len(espn.requests)
    clock.t += 200
    await svc.get_team_schedule("epl", "Arsenal")
    assert len(espn.requests) == n


# ------------------------------------------------------------------ get_news


async def test_news(service: SportsService, espn: FakeEspn) -> None:
    out = await service.get_news("nfl", limit=5)
    assert out.data["count"] == 5 and out.data["articles"][0]["headline"]
    assert espn.keys[-1].endswith("/news?limit=5")
    assert "never opens them" in out.text and "third-party data" in out.text
    assert out.data["meta"]["endpoint_kind"] == "news"


async def test_news_empty(service: SportsService, espn: FakeEspn) -> None:
    espn.overrides["/apis/site/v2/sports/football/nfl/news?limit=5"] = {"articles": []}
    out = await service.get_news("nfl")
    assert out.data["articles"] == [] and "no nfl headlines" in out.data["note"]


async def test_only_the_allow_listed_host_is_contacted(
    service: SportsService, espn: FakeEspn
) -> None:
    await service.get_scoreboard("nfl")
    await service.get_game("nfl", "401872965")
    await service.get_standings("epl")
    await service.find_team("nfl", "bills")
    await service.get_news("epl")
    assert {r.url.host for r in espn.requests} == {"site.api.espn.com"}
    assert all(r.url.scheme == "https" for r in espn.requests)


async def test_a_network_attempt_outside_the_fixtures_fails_the_test() -> None:
    async with httpx.AsyncClient() as c:
        with pytest.raises(AssertionError, match="real network request"):
            await c.get("https://site.api.espn.com/")


async def test_huge_scoreboards_are_capped_with_a_note(
    service: SportsService, espn: FakeEspn
) -> None:
    def many(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "nfl_scoreboard_week":
            data["events"] = data["events"] * 8  # 128 games
        return data

    espn.transform = many
    out = await service.get_scoreboard("nfl")
    assert out.data["count"] == 100
    assert "128 games match; showing the first 100" in out.data["note"]
    explicit = await service.get_scoreboard("nfl", limit=100)
    assert explicit.data["count"] == 100 and explicit.data["note"] == render.RECORDS_OMITTED_NOTE


async def test_college_basketball_requests_the_full_division_one_board(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.routes[
        "/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates=20261004&groups=50"
    ] = "nba_scoreboard_pre"
    out = await service.get_scoreboard("ncaam")
    assert out.data["count"] > 0 and espn.keys[-1].endswith("dates=20261004&groups=50")
