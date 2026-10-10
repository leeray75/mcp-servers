"""Unit tests for the normalisers: real fixtures plus hand-built edge cases."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sports_mcp.leagues import resolve_league
from sports_mcp.normalize import common, standings, summary, teams
from sports_mcp.normalize.odds import first_odds, normalize_odds, parse_moneyline
from sports_mcp.normalize.scoreboard import (
    empty_day_hint,
    has_live_game,
    normalize_event,
    normalize_scoreboard,
    season_info,
)
from sports_mcp.normalize.sports import situation_for, team_stats
from tests.conftest import load, make_buf_game_live

NY = ZoneInfo("America/New_York")
NFL = resolve_league("nfl")
MLB = resolve_league("mlb")
NHL = resolve_league("nhl")
NBA = resolve_league("nba")
EPL = resolve_league("epl")


def event(
    state: str = "post",
    name: str = "STATUS_FINAL",
    detail: str = "Final",
    home: tuple[str, int | str | None] = ("BUF", 24),
    away: tuple[str, int | str | None] = ("NE", 17),
    **extra: Any,
) -> dict[str, Any]:
    def comp(side: str, spec: tuple[str, Any]) -> dict[str, Any]:
        return {
            "homeAway": side,
            "team": {"id": spec[0], "abbreviation": spec[0], "displayName": spec[0] + " Team"},
            "score": str(spec[1]) if spec[1] is not None else None,
        }

    return {
        "id": "1",
        "date": "2026-10-04T17:00Z",
        "name": "A at B",
        "shortName": "NE @ BUF",
        "status": {
            "period": 4,
            "displayClock": "0:00",
            "type": {"state": state, "name": name, "detail": detail, "completed": state == "post"},
        },
        "competitions": [
            {"competitors": [comp("home", home), comp("away", away)], **extra},
        ],
    }


# ------------------------------------------------------------------ scoreboard


def test_scoreboard_real_nfl_week() -> None:
    raw = load("nfl_scoreboard_week")
    games = normalize_scoreboard(raw, NFL, NY)
    assert len(games) == 16
    states = [g.status.state for g in games]
    assert states.count("in") == 8 and states.count("post") == 2 and states.count("pre") == 6
    assert has_live_game(raw) is True
    final = next(g for g in games if g.id == "401872965")
    assert [t.abbr for t in final.teams] == ["IND", "WSH"]  # away first
    assert [t.score for t in final.teams] == [30, 13]
    assert final.teams[0].winner is True and final.teams[1].winner is False
    assert final.status.completed and final.status.detail == "Final"
    assert final.start_utc == "2026-10-04T13:30:00Z"
    assert final.start_local == "2026-10-04T09:30:00-0400"
    assert final.venue is not None and final.venue.city == "London"
    assert final.broadcasts == ["NFL Net"]


def test_pre_game_has_no_scores_but_has_odds() -> None:
    games = {g.id: g for g in normalize_scoreboard(load("nfl_scoreboard_week"), NFL, NY)}
    pre = games["401872974"]
    assert pre.status.state == "pre" and pre.status.period is None and pre.status.clock is None
    assert all(t.score is None and t.winner is None for t in pre.teams)
    assert pre.odds is not None
    assert (pre.odds.favorite_abbr, pre.odds.spread, pre.odds.over_under) == ("MIN", -10.0, 38.5)
    assert (pre.odds.away_moneyline, pre.odds.home_moneyline) == (400, -535)
    assert pre.odds.open is None and pre.odds.close is None  # compact on the scoreboard
    assert pre.situation is None


def test_live_games_have_no_odds_and_a_situation() -> None:
    games = normalize_scoreboard(load("nfl_scoreboard_week"), NFL, NY)
    live = [g for g in games if g.status.state == "in"]
    assert all(g.odds is None for g in live)
    first = live[0]
    assert first.status.period == 4 and first.status.clock
    assert first.situation is not None
    assert set(first.situation) >= {"down_distance", "possession"}
    assert first.teams[0].linescores and len(first.teams[0].linescores) >= 4


def test_empty_day_and_hint() -> None:
    raw = load("nfl_scoreboard_empty")
    assert normalize_scoreboard(raw, NFL, NY) == []
    assert has_live_game(raw) is False
    hint = empty_day_hint(raw, NFL, "2026-10-06")
    assert hint.startswith("No nfl games on 2026-10-06.")
    assert "2026" in hint and "season runs" in hint
    assert empty_day_hint({}, NFL, "2026-10-06") == "No nfl games on 2026-10-06."


def test_season_info() -> None:
    info = season_info(load("nfl_scoreboard_week"))
    assert info is not None and (info.year, info.type) == (2026, "Regular Season")
    assert season_info({}) is None
    assert season_info({"season": {"year": 2025}}).year == 2025  # type: ignore[union-attr]


def test_mlb_scoreboard_extra_innings_and_hits_errors() -> None:
    games = normalize_scoreboard(load("mlb_scoreboard_final"), MLB, NY)
    extra = next(g for g in games if g.id == "401907972")
    assert extra.status.detail == "Final/10"
    home = next(t for t in extra.teams if t.home_away == "home")
    assert len(home.linescores or []) == 10 and home.hits == 9 and home.errors == 0
    assert extra.teams[0].score == 4 and home.score == 3


def test_soccer_scoreboard_uses_regular_fields() -> None:
    games = normalize_scoreboard(load("epl_scoreboard_final"), EPL, NY)
    assert games and all(g.status.state == "post" for g in games)
    assert all(len(g.teams) == 2 for g in games)


def test_basketball_and_hockey_scoreboards_parse() -> None:
    nba = normalize_scoreboard(load("nba_scoreboard_pre"), NBA, NY)
    assert nba and all(g.status.state == "pre" for g in nba)
    nhl = normalize_scoreboard(load("nhl_scoreboard_final"), NHL, NY)
    assert len(nhl) == 13 and all(g.status.state == "post" for g in nhl)
    assert any(len(t.linescores or []) > 3 for g in nhl for t in g.teams)  # OT/shootout games


def test_event_without_competition_is_skipped() -> None:
    assert normalize_event({"id": "9"}, NFL, NY) is None
    raw = {"events": [{"id": "9"}, "junk", event()]}
    assert len(normalize_scoreboard(raw, NFL, NY)) == 1


@pytest.mark.parametrize(
    ("name", "state", "detail", "completed"),
    [
        ("STATUS_POSTPONED", "post", "Postponed", False),
        ("STATUS_CANCELED", "post", "Canceled", False),
        ("STATUS_DELAYED", "in", "Delayed", False),
        ("STATUS_SUSPENDED", "post", "Suspended", False),
        ("STATUS_FINAL_OT", "post", "Final/OT", True),
        ("STATUS_FINAL_SO", "post", "Final/SO", True),
        ("STATUS_HALFTIME", "in", "Halftime", False),
    ],
)
def test_unusual_statuses_pass_through(name: str, state: str, detail: str, completed: bool) -> None:
    raw = event(state=state, name=name, detail=detail)
    raw["status"]["type"]["completed"] = completed
    status = common.normalize_status(raw["status"])
    assert (status.state, status.detail, status.completed, status.name) == (
        state,
        detail,
        completed,
        name,
    )
    if state == "in":
        assert status.clock == "0:00"


def test_missing_and_odd_status_fields() -> None:
    assert common.normalize_status(None).state == "pre"
    assert common.normalize_status({"type": {"state": "weird"}}).state == "pre"
    assert common.normalize_status({"type": {"shortDetail": "x"}}).detail == "x"
    assert common.normalize_status({"type": {"description": "d"}}).detail == "d"


def test_tie_has_no_winner() -> None:
    raw = event(home=("BUF", 20), away=("NE", 20))
    raw["competitions"][0]["competitors"][0]["winner"] = False
    raw["competitions"][0]["competitors"][1]["winner"] = False
    game = normalize_event(raw, NFL, NY)
    assert game is not None and [t.winner for t in game.teams] == [False, False]
    assert [t.score for t in game.teams] == [20, 20]


def test_missing_fields_do_not_crash() -> None:
    raw = {"id": 7, "competitions": [{"competitors": [{"team": {}}, {}]}]}
    game = normalize_event(raw, NFL, NY)
    assert game is not None and game.id == "7" and game.start_utc is None
    assert game.venue is None and game.broadcasts is None and game.odds is None


def test_helpers() -> None:
    assert common.get({"a": [1, {"b": 2}]}, "a", 1, "b") == 2
    assert common.get({"a": 1}, "a", "b", default="d") == "d"
    assert common.get({"a": [1]}, "a", 5) is None
    assert common.get({"a": None}, "a", default=3) == 3
    assert common.as_int(True) is None and common.as_int("12") == 12 and common.as_int("1x") is None
    assert common.as_int(3.9) == 3
    assert common.as_float("o38.5") == 38.5 and common.as_float("+10") == 10.0
    assert common.as_float("u7") == 7.0 and common.as_float("x") is None
    assert (
        common.as_float(True) is None and common.as_float(3) == 3.0 and common.as_float([]) is None
    )
    assert common.text("  x ") == "x" and common.text("  ") is None and common.text(5) == "5"
    assert common.text(None) is None and common.text(True) is None
    assert common.score_value({"displayValue": "17"}) == 17
    assert common.score_value({"value": 17.0}) == 17
    assert common.score_value("x") is None
    assert common.record_summary({"records": [{"name": "overall", "summary": "3-0"}]}) == "3-0"
    assert common.record_summary({"record": [{"displayValue": "1-1"}]}) == "1-1"
    assert common.record_summary({}) is None
    assert common.broadcast_names([{"names": ["A", "B"]}, {"media": {"shortName": "A"}}, 5]) == [
        "A",
        "B",
    ]
    assert common.broadcast_names(None) is None
    assert common.normalize_venue("x") is None and common.normalize_venue({}) is None
    venue = common.normalize_venue({"name": "V", "address": {"city": "C"}, "indoor": True})
    assert venue is not None and (venue.name, venue.city, venue.indoor) == ("V", "C", True)
    assert common.times("2026-10-04T17:00Z", NY) == (
        "2026-10-04T17:00:00Z",
        "2026-10-04T13:00:00-0400",
    )
    assert common.times(None, NY) == (None, None)


# ------------------------------------------------------------------ situations


def test_football_situation() -> None:
    competitors = [
        {"team": {"id": "1", "abbreviation": "NE"}},
        {"team": {"id": "2", "abbreviation": "BUF"}},
    ]
    sit = {
        "downDistanceText": "2nd & 2 at NE 45",
        "possession": "1",
        "isRedZone": True,
        "homeTimeouts": 3,
        "awayTimeouts": 2,
    }
    assert situation_for("football", sit, competitors) == {
        "down_distance": "2nd & 2 at NE 45",
        "possession": "NE",
        "red_zone": True,
        "home_timeouts": 3,
        "away_timeouts": 2,
    }
    assert situation_for("football", {}, competitors) is None


def test_baseball_situation_is_synthetic_but_shaped_like_espn() -> None:
    sit = {
        "outs": 1,
        "balls": 2,
        "strikes": 1,
        "onFirst": True,
        "onSecond": False,
        "onThird": {"athlete": {"displayName": "Runner"}},
        "batter": {"athlete": {"displayName": "Batter Name"}},
        "pitcher": {"athlete": {"shortName": "P. Itcher"}},
    }
    assert situation_for("baseball", sit, []) == {
        "outs": 1,
        "balls": 2,
        "strikes": 1,
        "bases": ["1B", "3B"],
        "batter": "Batter Name",
        "pitcher": "P. Itcher",
    }


def test_hockey_situation_only_reports_special_strength() -> None:
    assert (
        situation_for("hockey", {"lastPlay": {"strength": {"text": "Even Strength"}}}, []) is None
    )
    assert situation_for("hockey", {"strength": "Power Play"}, []) == {"strength": "Power Play"}
    assert situation_for("basketball", {"x": 1}, []) is None
    assert situation_for("soccer", {"x": 1}, []) is None


def test_live_game_in_a_synthetic_mlb_event_gets_its_situation() -> None:
    raw = event(state="in", name="STATUS_IN_PROGRESS", detail="Top 5th")
    raw["competitions"][0]["situation"] = {"outs": 2, "onFirst": True}
    game = normalize_event(raw, MLB, NY)
    assert game is not None and game.situation == {"outs": 2, "bases": ["1B"]}


# ------------------------------------------------------------------ odds


def odds_raw(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider": {"name": "DraftKings"},
        "details": "BUF -7",
        "overUnder": 49.5,
        "spread": -7.0,
        "homeTeamOdds": {"favorite": True, "moneyLine": -345},
        "awayTeamOdds": {"favorite": False, "moneyLine": 275},
    }
    base.update(kw)
    return base


def test_odds_home_favorite() -> None:
    line = normalize_odds(odds_raw(), "BUF", "NE")
    assert line is not None
    assert (line.favorite_abbr, line.spread, line.over_under) == ("BUF", -7.0, 49.5)
    assert (line.home_moneyline, line.away_moneyline) == (-345, 275)
    assert line.details == "BUF -7" and line.provider == "DraftKings"


def test_odds_away_favorite_uses_the_home_spread_sign() -> None:
    line = normalize_odds(
        odds_raw(
            details="IND -4.5",
            spread=4.5,
            homeTeamOdds={"moneyLine": 185},
            awayTeamOdds={"moneyLine": -225},
        ),
        "WSH",
        "IND",
    )
    assert line is not None and (line.favorite_abbr, line.spread) == ("IND", -4.5)


def test_odds_pick_em_uses_moneyline_then_flags() -> None:
    line = normalize_odds(
        odds_raw(spread=0, homeTeamOdds={"moneyLine": -110}, awayTeamOdds={"moneyLine": 100}),
        "BUF",
        "NE",
    )
    assert line is not None and line.spread == 0.0 and line.favorite_abbr == "BUF"
    flagged = normalize_odds(
        {"spread": 0, "homeTeamOdds": {"favorite": False}, "awayTeamOdds": {"favorite": True}},
        "BUF",
        "NE",
    )
    assert flagged is not None and flagged.favorite_abbr == "NE"
    home_flag = normalize_odds({"spread": 0, "homeTeamOdds": {"favorite": True}}, "BUF", "NE")
    assert home_flag is not None and home_flag.favorite_abbr == "BUF"
    nothing = normalize_odds({"spread": 0}, "BUF", "NE")
    assert nothing is not None and nothing.favorite_abbr is None


def test_odds_nested_form_and_open_close_and_draw() -> None:
    raw = {
        "spread": -1.5,
        "overUnder": 2.5,
        "drawOdds": {"moneyLine": 390.0},
        "moneyline": {
            "home": {"close": {"odds": "-260"}, "open": {"odds": "-340"}},
            "away": {"close": {"odds": "+650"}, "open": {"odds": "+800"}},
        },
        "pointSpread": {"home": {"close": {"line": "-1.5"}, "open": {"line": "-1"}}},
        "total": {"over": {"close": {"line": "o2.5"}, "open": {"line": "o3"}}},
    }
    line = normalize_odds(raw, "ARS", "LEE")
    assert line is not None
    assert (line.home_moneyline, line.away_moneyline, line.draw_moneyline) == (-260, 650, 390)
    assert (
        line.open is not None and line.open.home_spread == -1.0 and line.open.home_moneyline == -340
    )
    assert (
        line.close is not None and line.close.over_under == 2.5 and line.close.away_moneyline == 650
    )
    assert line.favorite_abbr == "ARS" and line.spread == -1.5


def test_odds_absent_or_empty() -> None:
    assert normalize_odds(None, "A", "B") is None
    assert normalize_odds({}, "A", "B") is None
    assert normalize_odds({"provider": {"name": "X"}, "details": "n/a"}, "A", "B") is None
    assert first_odds([], "A", "B") is None
    assert first_odds(None, "A", "B") is None
    assert first_odds([{}, odds_raw()], "BUF", "NE") is not None  # skips unusable entries


@pytest.mark.parametrize(
    ("value", "expected"),
    [("+400", 400), ("-535", -535), (110, 110), (110.0, 110), ("EVEN", 100), ("PK", 100),
     ("x", None), (None, None), (True, None)],
)  # fmt: skip
def test_parse_moneyline(value: Any, expected: int | None) -> None:
    assert parse_moneyline(value) == expected


# ------------------------------------------------------------------ summary


def find(recs: list[teams.TeamRec], query: str) -> list[teams.TeamRec]:
    return [t for _, t in teams.rank_matches(recs, query)[: teams.MAX_MATCHES]]


def game_of(name: str, league: Any) -> tuple[dict[str, Any], Any]:
    raw = load(name)
    game = summary.summary_game(raw, league, NY)
    assert game is not None
    return raw, game


def test_summary_header_game_nfl_final() -> None:
    raw, game = game_of("nfl_summary_final", NFL)
    assert game.id == "401872965" and game.short_name == "IND @ WSH"
    assert game.name == "Indianapolis Colts at Washington Commanders"
    assert [t.score for t in game.teams] == [30, 13]
    assert game.venue is not None and game.venue.name == "Tottenham Hotspur Stadium"
    assert game.status.completed and game.broadcasts == ["NFL Net"]
    assert summary.attendance(raw) == 60907
    plays = summary.scoring_plays(raw, NFL, game)
    assert plays is not None and len(plays) == 9 and plays[0].clock == "12:57"
    assert plays[0].team_abbr == "WSH" and (plays[0].away_score, plays[0].home_score) == (0, 3)
    assert (plays[-1].away_score, plays[-1].home_score) == (30, 13)
    assert summary.win_probability(raw) is not None


def test_summary_leaders_injuries_weather() -> None:
    raw, _ = game_of("nfl_summary_pre", NFL)
    weather = summary.weather(raw)
    assert weather is not None and "temperature_f" in weather
    injuries = summary.injuries(raw)
    assert injuries is not None
    assert all(len(v) <= 8 for v in injuries.values())
    leaders = summary.leaders(raw)
    assert leaders is not None and len(leaders) <= 12
    assert summary.leaders({}) is None and summary.injuries({}) is None
    assert summary.weather({}) is None and summary.win_probability({}) is None
    assert summary.win_probability({"winprobability": [{"x": 1}]}) is None
    assert summary.attendance({"gameInfo": {"attendance": 0}}) is None


def test_win_probability_math() -> None:
    wp = summary.win_probability(
        {"winprobability": [{"homeWinPercentage": 0.4343, "tiePercentage": 0.0}]}
    )
    assert wp is not None and (wp.home_pct, wp.away_pct) == (43.4, 56.6)


def test_scoring_plays_per_sport() -> None:
    raw, game = game_of("mlb_summary_final", MLB)
    plays = summary.scoring_plays(raw, MLB, game)
    assert plays is not None and len(plays) == 7
    assert plays[0].clock == "Top" and plays[0].team_abbr == "PHI" and plays[0].period == 1
    raw, game = game_of("nhl_summary_final", NHL)
    plays = summary.scoring_plays(raw, NHL, game)
    assert plays is not None and len(plays) == 7 and plays[0].clock == "7:59"
    raw, game = game_of("nba_summary_final", NBA)
    assert summary.scoring_plays(raw, NBA, game) is None  # every basket: skipped on purpose
    raw, game = game_of("epl_summary_final", EPL)
    goals = summary.scoring_plays(raw, EPL, game)
    assert goals is not None and len(goals) == 1
    assert goals[0].text == "Goal: Alexander Isak" and goals[0].team_abbr == "LIV"
    assert (goals[0].away_score, goals[0].home_score) == (1, 0)


def test_soccer_goal_variants() -> None:
    raw, game = game_of("epl_summary_final", EPL)
    comp = raw["header"]["competitions"][0]
    comp["details"] = [
        {"scoringPlay": True, "team": {"id": "349"}, "penaltyKick": True,
         "participants": [{"athlete": {"displayName": "P. Taker"}}], "clock": {"displayValue": "10'"}},
        {"scoringPlay": True, "team": {"id": "364"}, "ownGoal": True, "participants": []},
        {"scoringPlay": False, "team": {"id": "364"}},
    ]  # fmt: skip
    goals = summary.scoring_plays(raw, EPL, game)
    assert goals is not None
    assert [g.text for g in goals] == ["Goal: P. Taker (pen)", "Goal: Goal (own goal)"]
    assert (goals[1].away_score, goals[1].home_score) == (1, 1)


def test_scoring_plays_are_capped_and_skip_blank_text() -> None:
    raw, game = game_of("nfl_summary_final", NFL)
    play = raw["scoringPlays"][0]
    raw["scoringPlays"] = [dict(play) for _ in range(60)] + [{"text": ""}, {"type": {}}]
    plays = summary.scoring_plays(raw, NFL, game)
    assert plays is not None and len(plays) == 40


def test_summary_odds_pre_and_post() -> None:
    raw, game = game_of("nfl_summary_pre", NFL)
    line = summary.summary_odds(raw, game)
    assert line is not None and line.favorite_abbr == "MIN" and line.open is not None
    assert (
        line.open.home_spread == -7.5 and line.close is not None and line.close.home_spread == -10.0
    )
    raw, game = game_of("nba_summary_pre", NBA)
    assert summary.summary_odds(raw, game) is None  # ESPN published no line for this game


def test_summary_without_header_is_none() -> None:
    assert summary.summary_game({}, NFL, NY) is None


# ------------------------------------------------------------------ team stats


def test_team_stats_allow_list_per_sport() -> None:
    for name, league, first, cap in (
        ("nfl_summary_final", NFL, "1st downs", 15),
        ("nba_summary_final", NBA, "FG", 15),
        ("nhl_summary_final", NHL, "Shots", 15),
        ("mlb_summary_final", MLB, "Runs", 15),
        ("epl_summary_final", EPL, "Possession %", 15),
    ):
        stats = team_stats(league.family, load(name)["boxscore"]["teams"], cap)
        assert stats is not None and stats[0].label == first and len(stats) <= cap
        assert all(s.away is not None or s.home is not None for s in stats)
    assert team_stats("football", [], 15) is None
    assert team_stats("football", None, 15) is None


def test_team_stats_values_are_oriented_away_then_home() -> None:
    stats = team_stats("football", load("nfl_summary_final")["boxscore"]["teams"])
    assert stats is not None
    first = stats[0]
    assert (first.label, first.away, first.home) == ("1st downs", "17", "12")  # IND away


def test_team_stats_cap_and_missing_home_away() -> None:
    teams_block = deepcopy(load("nfl_summary_final")["boxscore"]["teams"])
    assert len(team_stats("football", teams_block, 3) or []) == 3
    for t in teams_block:
        t.pop("homeAway")
    stats = team_stats("football", teams_block)
    assert stats is not None and stats[0].away == "17"  # falls back to document order


# ------------------------------------------------------------------ standings


def test_standings_nfl_divisions() -> None:
    groups = standings.normalize_standings(load("nfl_standings"), "football")
    assert len(groups) == 8
    east = next(g for g in groups if g.name == "AFC East")
    assert [e.rank for e in east.entries] == [1, 2, 3, 4]
    assert east.entries[0].team_abbr == "BUF" and east.entries[0].stats["wins"] == "3"
    assert list(east.entries[0].stats)[:3] == ["wins", "losses", "ties"]
    assert "playoffSeed" not in east.entries[0].stats  # unknown stat names are dropped
    assert standings.standings_season(load("nfl_standings")) == 2026


def test_standings_soccer_uses_stable_soccer_keys() -> None:
    (table,) = standings.normalize_standings(load("epl_standings"), "soccer")
    assert len(table.entries) == 20
    keys = list(table.entries[0].stats)
    assert keys == [
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
        "goal_diff",
        "points",
    ]
    assert [e.rank for e in table.entries] == list(range(1, 21))
    points = [int(e.stats["points"]) for e in table.entries]
    assert points == sorted(points, reverse=True)


def test_standings_hockey_basketball_baseball_college() -> None:
    nhl = standings.normalize_standings(load("nhl_standings"), "hockey")
    assert {g.name for g in nhl} == {"Eastern Conference", "Western Conference"}
    assert "otl" in nhl[0].entries[0].stats and "points" in nhl[0].entries[0].stats
    nba = standings.normalize_standings(load("nba_standings"), "basketball")
    assert len(nba) == 2 and len(nba[0].entries) == 15
    mlb = standings.normalize_standings(load("mlb_standings"), "baseball")
    assert len(mlb) == 6 and "games_behind" in mlb[0].entries[0].stats
    ncaaf = standings.normalize_standings(load("ncaaf_standings"), "football")
    assert ncaaf[0].abbr and "record" in ncaaf[0].entries[0].stats  # no losses stat: record shown


def test_standings_edge_cases() -> None:
    assert standings.normalize_standings({}, "football") == []
    assert standings.standings_season({"season": 2025}) == 2025
    assert standings.standings_season({}) is None
    node = {
        "name": "Solo",
        "standings": {
            "entries": [{"team": {}}, {"team": {"displayName": "T"}, "stats": [{"x": 1}]}]
        },
    }
    (group,) = standings.normalize_standings(node, "football")
    assert [e.team for e in group.entries] == ["T"]


# ------------------------------------------------------------------ teams / schedule / news


def test_team_directory_and_ranking() -> None:
    recs = teams.directory(load("nfl_teams"))
    assert len(recs) == 32
    bills = find(recs, "bills")
    assert [t.abbr for t in bills] == ["BUF"]
    assert find(recs, "BUF")[0].abbr == "BUF"
    assert find(recs, "2")[0].abbr == "BUF"  # by id
    assert find(recs, "buffalo bills")[0].abbr == "BUF"
    assert find(recs, "buff")[0].abbr == "BUF"  # prefix
    assert {t.abbr for t in find(recs, "ew york")} == {"NYG", "NYJ"}  # substring
    assert find(recs, "zzzz") == []
    assert len(find(recs, "a")) <= 5
    # exact beats prefix beats substring, deterministic
    ranked = teams.rank_matches(recs, "new")
    assert ranked == teams.rank_matches(recs, "NEW")
    assert [r for r, _ in ranked] == sorted(r for r, _ in ranked)
    assert find(recs, "Pat")[0].abbr == "NE"  # slug/nickname prefix


def test_diacritics_and_case_insensitive_matching() -> None:
    recs = [
        teams.TeamRec(
            "1", "ATM", "Atlético Madrid", "Madrid", "Atlético", "Atlético", "atletico-madrid"
        ),
        teams.TeamRec("2", "RMA", "Real Madrid", "Madrid", "Real", "Real Madrid", "real-madrid"),
    ]
    assert find(recs, "ATLETICO")[0].abbr == "ATM"
    assert [t.abbr for t in find(recs, "madrid")] == ["ATM", "RMA"]  # tie -> by name


def test_resolve_one_ambiguity_and_none() -> None:
    from sports_mcp.errors import InvalidInputError

    recs = teams.directory(load("nfl_teams"))
    assert teams.resolve_one(recs, "bills") is not None
    assert teams.resolve_one(recs, "zzzz") is None
    with pytest.raises(InvalidInputError, match="Ambiguous.*Giants.*Jets"):
        teams.resolve_one(recs, "New York")


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61, "a\nb", "a;b", "a<b", "../x", "a?b=1"])
def test_team_query_validation(bad: str) -> None:
    from sports_mcp.errors import InvalidInputError

    with pytest.raises(InvalidInputError):
        teams.validate_query(bad)


def test_team_extras() -> None:
    assert teams.team_extras(load("nfl_team_buf")) == ("3-0", "1st in AFC East")
    assert teams.team_extras({}) == (None, None)


def test_schedule_normalisation() -> None:
    raw = make_buf_game_live("nfl_schedule_buf", load("nfl_schedule_buf"))
    games = teams.schedule_games(raw, "2", NY)
    assert len(games) == 17
    assert games == sorted(games, key=lambda g: g.start_utc or "")
    first = games[0]
    assert (first.opponent_abbr, first.home_away, first.result, first.score) == (
        "HOU",
        "away",
        "W 36-31",
        "36-31",
    )
    assert first.label == "Week 1"
    assert first.status.completed
    live = next(g for g in games if g.status.state == "in")
    assert live.result is None and live.opponent_abbr == "NE"
    upcoming = next(g for g in games if g.status.state == "pre")
    assert upcoming.score is None and upcoming.result is None
    assert teams.schedule_has_live(raw) is True
    season = teams.schedule_season(raw)
    assert season is not None and season.year == 2026 and season.type == "Regular Season"
    assert teams.schedule_season({}) is None
    req = teams.schedule_season({"requestedSeason": {"year": 2025, "name": "Regular Season"}}, True)
    assert req is not None and req.year == 2025


def test_schedule_results_loss_tie_and_missing() -> None:
    assert teams._result(3, 10, None, True) == "L 3-10"
    assert teams._result(10, 3, None, True) == "W 10-3"
    assert teams._result(1, 1, None, True) == "T 1-1"
    assert teams._result(1, 2, True, True) == "W 1-2"  # ESPN's winner flag is authoritative
    assert teams._result(None, 2, None, True) is None
    assert teams._result(1, 2, None, False) is None
    assert teams.schedule_game({"id": "1"}, "2", NY) is None


def test_soccer_and_baseball_schedules() -> None:
    games = teams.schedule_games(load("epl_schedule_ars"), "359", NY)
    assert games and all(g.opponent_abbr for g in games)
    mlb = teams.schedule_games(load("mlb_schedule_atl"), "15", NY)
    assert any(g.result and g.result.startswith("W") for g in mlb)
    assert teams.schedule_has_live({"events": []}) is False


def test_news_items() -> None:
    items = teams.news_items(load("nfl_news"), 3)
    assert len(items) == 3
    assert all(i.headline and i.url and i.url.startswith("https://") for i in items)
    assert all(i.published_utc and i.published_utc.endswith("Z") for i in items)
    assert all(len(i.description or "") <= 240 for i in items)
    raw = {"articles": [{"headline": "H", "description": "d" * 400}, {"headline": ""}, "x"]}
    (item,) = teams.news_items(raw, 5)
    assert item.description is not None and len(item.description) == 240 and item.url is None
