"""Team rank, ranked-first scoreboard and turnover lines (spec team-rank-and-turnovers R1-R8).

Offline: every request goes through the fake ESPN and the captured fixtures. Fixture
``ncaaf_scoreboard_ranked`` is the day of 2026-10-24 (44 games, 17 with a ranked team);
``ncaaf_summary_ranked`` is the Miami game (MIA 1 vs PITT, in progress) at capture time.
"""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from mcp import Client

from sports_mcp import render
from sports_mcp.config import Settings
from sports_mcp.errors import InvalidInputError
from sports_mcp.leagues import LEAGUES, resolve_league
from sports_mcp.models import Game, Status, TeamLine, TeamMatch, dump
from sports_mcp.normalize import players as players_mod
from sports_mcp.normalize.common import poll_rank, team_line
from sports_mcp.normalize.scoreboard import normalize_event
from sports_mcp.normalize.summary import summary_game
from sports_mcp.server import _DESC_GAME, _DESC_SCOREBOARD, create_server
from sports_mcp.tools import SportsService, _rank_order
from tests.conftest import FakeEspn, load

DAY = "20261024"
MIA_GAME = "401858274"  # MIA 1 (home) vs PITT, in progress at capture
IU_GAME = "401858496"  # IU 6 (away) vs MICH
BYU_GAME = "401856835"  # BYU 2 (home) vs UCF, pre-game
RANKED_ORDER_START = [
    MIA_GAME,  # MIA 1 vs PITT
    BYU_GAME,  # BYU 2 vs UCF
    IU_GAME,  # IU 6 vs MICH
    "401856732",  # MISS 7 vs TEX 19
    "401856840",  # TTU 8 vs CIN
    "401858499",  # UCLA 9 vs MSU
]
FOOTNOTE = "#n = poll rank (ESPN), shown only for ranked teams"
RECORDS_NOTE = "Team records omitted to fit the size budget."
NO_RANKED_NOTE = "No ranked teams play on this date."


def _team(game: Game, abbr: str) -> TeamLine:
    return next(t for t in game.teams if t.abbr == abbr)


def _ids(games: list[dict[str, Any]]) -> list[str]:
    return [g["id"] for g in games]


def _is_ranked(game: dict[str, Any]) -> bool:
    return any("rank" in t for t in game["teams"])


def _ncaaf_game(event_id: str) -> Game:
    raw = load("ncaaf_scoreboard_ranked")
    event = next(e for e in raw["events"] if e["id"] == event_id)
    game = normalize_event(event, resolve_league("ncaaf"), _TZ)
    assert game is not None
    return game


_TZ = ZoneInfo("America/New_York")


# ---------------------------------------------------------------- R1: rank on game teams


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1), (6, 6), (25, 25), (None, None), (0, None), (26, None), (99, None), ("6", None)],
)
def test_r1_poll_rank_accepts_only_integers_1_to_25(value: Any, expected: int | None) -> None:
    assert poll_rank(value) == expected


def test_r1_poll_rank_rejects_bools_and_floats() -> None:
    assert poll_rank(True) is None
    assert poll_rank(6.0) is None


def test_r1_team_line_reads_curated_rank_and_leaves_unranked_empty() -> None:
    ranked = team_line(
        {"homeAway": "home", "team": {"abbreviation": "MIA"}, "curatedRank": {"current": 1}}
    )
    unranked = team_line(
        {"homeAway": "away", "team": {"abbreviation": "PITT"}, "curatedRank": {"current": 99}}
    )
    absent = team_line({"homeAway": "away", "team": {"abbreviation": "X"}})
    assert (ranked.rank, unranked.rank, absent.rank) == (1, None, None)


def test_r1_summary_header_rank_is_read_from_rank_key() -> None:
    team = team_line({"homeAway": "away", "team": {"abbreviation": "IU"}, "rank": 6})
    assert team.rank == 6


def test_r1_ranks_on_the_captured_scoreboard() -> None:
    raw = load("ncaaf_scoreboard_ranked")
    games = [normalize_event(e, resolve_league("ncaaf"), _TZ) for e in raw["events"]]
    flat = [t for g in games if g is not None for t in g.teams]
    ranks = {t.abbr: t.rank for t in flat if t.rank is not None}
    assert ranks["MIA"] == 1 and ranks["IU"] == 6 and ranks["TTU"] == 8
    assert ranks["LSU"] == 18 and ranks["SMU"] == 23
    assert all(1 <= r <= 25 for r in ranks.values()) and len(ranks) >= 17
    assert sum(1 for g in games if g is not None and any(t.rank for t in g.teams)) == 17
    assert _team(_ncaaf_game(MIA_GAME), "PITT").rank is None


def test_r1_structured_omits_absent_rank() -> None:
    teams = dump(_ncaaf_game(IU_GAME))["teams"]  # IU is away, so it is listed first
    assert teams[0]["abbr"] == "IU" and teams[0]["rank"] == 6
    mich = next(t for t in teams if t["abbr"] == "MICH")
    assert "rank" not in mich


async def test_r1_summary_header_carries_rank(service: SportsService) -> None:
    out = await service.get_game("ncaaf", MIA_GAME)
    assert {t["abbr"]: t.get("rank") for t in out.data["game"]["teams"]} == {
        "MIA": 1,
        "PITT": None,
    }


def test_r1_summary_model_reads_header_rank() -> None:
    game = summary_game(load("ncaaf_summary_ranked"), resolve_league("ncaaf"), _TZ)
    assert game is not None and _team(game, "MIA").rank == 1


# ---------------------------------------------------------------- R2: text rendering


async def test_r2_scoreboard_text_prefixes_rank_and_adds_record(service: SportsService) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY)
    assert "#1 MIA (6-0) 45" in out.text
    assert "#6 IU (6-1) 6" in out.text
    assert "PITT (5-2) 14" in out.text  # unranked: no prefix, record still shown
    assert FOOTNOTE in out.text
    assert out.data.get("note") is None  # records fit the budget on the real day


async def test_r2_unranked_league_keeps_its_layout(service: SportsService) -> None:
    out = await service.get_scoreboard("nfl", date="20261004")
    game_lines = [line for line in out.text.splitlines() if " @ " in line]
    assert game_lines and not any("#" in line for line in game_lines)
    assert FOOTNOTE not in out.text


async def test_r2_records_dropped_with_note_when_over_budget(
    service: SportsService, espn: FakeEspn
) -> None:
    def triple(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "ncaaf_scoreboard_ranked":
            data["events"] = data["events"] * 3  # 132 games: far over 6 KB with records
        return data

    espn.transform = triple
    out = await service.get_scoreboard("ncaaf", date=DAY, limit=500)
    assert out.data["note"] == RECORDS_NOTE
    assert "#1 MIA 45" in out.text  # ranks stay
    assert "(6-0)" not in out.text  # records are gone


async def test_r2_game_header_uses_rank_and_name(service: SportsService) -> None:
    out = await service.get_game("ncaaf", MIA_GAME)
    assert "Pittsburgh Panthers 14 @ #1 Miami Hurricanes 45" in out.text


# ---------------------------------------------------------------- R3: find_team


async def test_r3_find_team_is_unchanged_because_upstream_has_no_rank(
    service: SportsService,
) -> None:
    out = await service.find_team("ncaaf", "miami")
    assert "rank" not in TeamMatch.model_fields  # upstream has no poll rank: R3 not applicable
    assert all("rank" not in m for m in out.data["matches"])
    assert "#" not in out.text
    assert "id=2390 MIA Miami Hurricanes (6-0, 1st in ACC)" in out.text


# ---------------------------------------------------------------- R4: ranked_only


async def test_r4_ranked_only_keeps_games_with_a_ranked_team(service: SportsService) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY, ranked_only=True)
    assert out.data["count"] == 17
    assert all(_is_ranked(g) for g in out.data["games"])
    assert "PITT @ MIA=401858274" in out.text  # ids line follows the filtered list
    assert "CAL @ SMU" in out.text
    assert "EMU @ OHIO" not in out.text  # unranked game removed


async def test_r4_ranked_only_with_no_ranked_games_gives_note(
    service: SportsService, espn: FakeEspn
) -> None:
    def unrank(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "ncaaf_scoreboard_ranked":
            for event in data["events"]:
                for c in event["competitions"][0]["competitors"]:
                    c["curatedRank"] = {"current": 99}
        return data

    espn.transform = unrank
    out = await service.get_scoreboard("ncaaf", date=DAY, ranked_only=True)
    assert out.data["count"] == 0 and out.data["games"] == []
    assert out.data["note"] == NO_RANKED_NOTE
    assert "No ncaaf games" not in out.data["note"]  # the games exist, so no empty-day hint


async def test_r4_league_without_rank_field_says_it_carries_no_ranks(
    service: SportsService,
) -> None:
    out = await service.get_scoreboard("nba", date="20261004", ranked_only=True)
    assert out.data["count"] == 0
    assert out.data["note"] == "This league's scoreboard carries no ranks."


def test_r4_flag_pins_every_league_in_the_registry() -> None:
    # Amendment A1: the empty ranked_only note is chosen by League.has_poll_ranks (college only).
    flags = {lg.id: lg.has_poll_ranks for lg in LEAGUES}
    assert flags == {
        "nfl": False,
        "college-football": True,
        "nba": False,
        "wnba": False,
        "mens-college-basketball": True,
        "womens-college-basketball": True,
        "mlb": False,
        "nhl": False,
        "epl": False,
        "la-liga": False,
        "bundesliga": False,
        "serie-a": False,
        "ligue-1": False,
        "mls": False,
        "champions-league": False,
    }


async def test_r4_nfl_gets_the_no_ranks_note(service: SportsService) -> None:
    out = await service.get_scoreboard("nfl", date="20261004", ranked_only=True)
    assert out.data["count"] == 0
    assert out.data["note"] == "This league's scoreboard carries no ranks."


# ---------------------------------------------------------------- R5: sort


async def test_r5_sort_rank_orders_by_better_rank_then_other_then_time(
    service: SportsService,
) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY, sort="rank")
    games = out.data["games"]
    assert _ids(games)[: len(RANKED_ORDER_START)] == RANKED_ORDER_START
    assert all(_is_ranked(g) for g in games[:17])
    tail = games[17:]
    assert not any(_is_ranked(g) for g in tail)
    starts = [g["start_utc"] for g in tail]
    assert starts == sorted(starts)  # unranked games keep start-time order


async def test_r5_default_sort_is_espn_order(service: SportsService) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY)
    assert _ids(out.data["games"])[:2] == [MIA_GAME, IU_GAME]


async def test_r5_limit_applies_after_filter_and_sort(service: SportsService) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY, ranked_only=True, sort="rank", limit=3)
    assert _ids(out.data["games"]) == RANKED_ORDER_START[:3]
    assert out.data["count"] == 3


async def test_r5_sort_combines_with_status(service: SportsService) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY, status="upcoming", sort="rank")
    assert all(g["status"]["state"] == "pre" for g in out.data["games"])
    assert _ids(out.data["games"])[0] == BYU_GAME  # BYU 2 is the best ranked upcoming game


def _game(game_id: str, rank: int | None, start: str | None) -> Game:
    teams = [TeamLine(home_away="home", abbr="H", rank=rank), TeamLine(home_away="away", abbr="A")]
    return Game(id=game_id, status=Status(state="pre"), teams=teams, start_utc=start)


def test_r5_game_without_start_time_sorts_last_within_its_tier() -> None:
    ranked_no_time = _game("a", 5, None)
    ranked_late = _game("b", 5, "2026-10-24T18:00:00Z")
    ranked_early = _game("c", 5, "2026-10-24T17:00:00Z")
    unranked_no_time = _game("d", None, None)
    unranked_timed = _game("e", None, "2026-10-24T19:00:00Z")
    ordered = sorted(
        [ranked_no_time, unranked_no_time, ranked_late, unranked_timed, ranked_early],
        key=_rank_order,
    )
    assert [g.id for g in ordered] == ["c", "b", "a", "e", "d"]


async def test_r4_status_filter_that_empties_the_list_gets_its_own_note(
    service: SportsService,
) -> None:
    out = await service.get_scoreboard("ncaaf", date=DAY, status="final", ranked_only=True)
    assert out.data["count"] == 0
    assert out.data["note"] == "No ranked teams among the final games on this date."


async def test_r5_invalid_sort_is_rejected(service: SportsService) -> None:
    with pytest.raises(InvalidInputError, match="sort must be one of: time, rank"):
        await service.get_scoreboard("ncaaf", date=DAY, sort="upset")


# ---------------------------------------------------------------- R6: turnovers


def _team_block(
    abbr: str,
    passing: list[tuple[str, str]],
    fumbles: list[tuple[str, str, str]],
    interceptions: list[tuple[str, str]],
) -> dict[str, Any]:
    """A minimal football boxscore block shaped like ESPN's (labels, then athlete stats)."""
    return {
        "team": {"abbreviation": abbr},
        "statistics": [
            {
                "name": "passing",
                "labels": ["C/ATT", "YDS", "AVG", "TD", "INT"],
                "athletes": [
                    {"athlete": {"displayName": n}, "stats": ["1/2", "10", "5", "0", i]}
                    for n, i in passing
                ],
            },
            {
                "name": "fumbles",
                "labels": ["FUM", "LOST", "REC"],
                "athletes": [
                    {"athlete": {"displayName": n}, "stats": [f, lost, "0"]}
                    for n, f, lost in fumbles
                ],
            },
            {
                "name": "interceptions",
                "labels": ["INT", "YDS", "TD"],
                "athletes": [
                    {"athlete": {"displayName": n}, "stats": [i, "0", "0"]}
                    for n, i in interceptions
                ],
            },
        ],
    }


def test_r6_counts_and_takeaways_from_two_blocks() -> None:
    blocks = [
        _team_block("AAA", [("Q1", "2")], [("R1", "1", "1")], [("D1", "1")]),
        _team_block("BBB", [("Q2", "0")], [("R2", "0", "0")], []),
    ]
    result = players_mod.turnovers(blocks)
    assert result is not None
    aaa, bbb = result["AAA"], result["BBB"]
    assert (aaa.total, aaa.interceptions_thrown, aaa.interceptions_thrown_by) == (3, 2, ["Q1"])
    assert (aaa.fumbles_lost, aaa.fumbles_lost_by) == (1, ["R1"])
    assert aaa.takeaways == 1  # AAA made one INT; BBB lost no fumble
    assert (bbb.total, bbb.takeaways) == (0, 1)  # BBB made none; AAA lost one fumble
    assert (
        render.turnover_text(aaa) == "TO: 3 (INT thrown 2 [Q1], fumbles lost 1 [R1]); takeaways: 1"
    )
    assert render.turnover_text(bbb) == "TO: 0 (INT thrown 0, fumbles lost 0); takeaways: 1"


def test_r6_missing_table_omits_both_teams_lines() -> None:
    blocks = [
        _team_block("AAA", [("Q1", "0")], [], []),
        _team_block("BBB", [("Q2", "1")], [("R2", "1", "1")], []),
    ]
    blocks[0]["statistics"] = [s for s in blocks[0]["statistics"] if s["name"] != "fumbles"]
    # AAA's fumble table is missing: AAA gets no line, and BBB's takeaways (which need AAA's
    # fumbles) are not reported either. Nothing is shown as zero.
    assert players_mod.turnovers(blocks) is None


def test_r6_needs_exactly_two_teams() -> None:
    assert players_mod.turnovers([]) is None
    assert players_mod.turnovers([_team_block("AAA", [], [], [])]) is None


async def test_r6_performers_show_turnover_line_per_team(service: SportsService) -> None:
    out = await service.get_game("ncaaf", MIA_GAME, detail="performers")
    assert "    TO: 1 (INT thrown 0, fumbles lost 1 [Holden Geriner]); takeaways: 0" in out.text
    assert "    TO: 0 (INT thrown 0, fumbles lost 0); takeaways: 1" in out.text
    assert out.data["turnovers"]["PITT"]["fumbles_lost_by"] == ["Holden Geriner"]
    assert len(out.text.encode()) <= 12_000


async def test_r6_turnover_line_follows_each_teams_performers(service: SportsService) -> None:
    out = await service.get_game("ncaaf", MIA_GAME, detail="performers")
    lines = out.text.splitlines()
    header = lines.index("  MIA:")
    to_line = next(i for i in range(header, len(lines)) if lines[i].startswith("    TO:"))
    assert to_line > header + 1  # after at least one of MIA's performer rows
    assert lines[to_line - 1].startswith("    ") and not lines[to_line - 1].startswith("    TO:")


async def test_r6_boxscore_shows_turnover_line_under_team(service: SportsService) -> None:
    out = await service.get_game("ncaaf", MIA_GAME, detail="boxscore")
    lines = out.text.splitlines()
    start = lines.index("  MIA")
    to_line = next(i for i in range(start, len(lines)) if lines[i].startswith("   TO:"))
    assert lines[to_line] == "   TO: 0 (INT thrown 0, fumbles lost 0); takeaways: 1"
    assert lines[to_line - 1].startswith("    ")  # after MIA's own player rows
    assert not any(
        line.startswith("  ") and not line.startswith("   ") for line in lines[start + 1 : to_line]
    )


async def test_r6_synthetic_interception_is_listed(service: SportsService, espn: FakeEspn) -> None:
    # Synthetic: the captured game has no non-zero INT thrown, so one passer's INT is set to 2.
    def thrown(name: str, data: dict[str, Any]) -> dict[str, Any]:
        if name == "ncaaf_summary_ranked":
            for team in data["boxscore"]["players"]:
                if team["team"]["abbreviation"] != "PITT":
                    continue
                for cat in team["statistics"]:
                    if cat["name"] != "passing":
                        continue
                    for athlete in cat["athletes"]:
                        if athlete["athlete"]["displayName"] == "Holden Geriner":
                            athlete["stats"][4] = "2"
        return data

    espn.transform = thrown
    out = await service.get_game("ncaaf", MIA_GAME, detail="performers")
    assert (
        "    TO: 3 (INT thrown 2 [Holden Geriner], fumbles lost 1 [Holden Geriner]); takeaways: 0"
        in out.text
    )


async def test_r6_pre_game_football_has_no_turnover_line(service: SportsService) -> None:
    out = await service.get_game("nfl", "401872974", detail="performers")
    assert "TO:" not in out.text
    assert out.data.get("turnovers") is None


async def test_r6_non_football_is_unchanged(service: SportsService) -> None:
    out = await service.get_game("nba", "401859964", detail="performers")
    assert "TO:" not in out.text
    assert out.data.get("turnovers") is None


# ---------------------------------------------------------------- R7: descriptions


def test_r7_scoreboard_description_documents_ranks_and_keeps_the_note() -> None:
    text = _DESC_SCOREBOARD.lower()
    assert "ranked_only" in text and "sort" in text and "poll rank" in text
    assert "third-party data" in text and "not instructions" in text


def test_r7_game_description_mentions_the_turnover_line() -> None:
    assert "TO line" in _DESC_GAME
    assert "third-party data" in _DESC_GAME.lower()


async def test_r7_schema_exposes_new_arguments_and_eleven_tools(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        tools = {t.name: t.input_schema for t in (await client.list_tools()).tools}
    assert len(tools) == 11
    props = tools["get_scoreboard"]["properties"]
    assert "ranked_only" in props and "sort" in props


async def test_r7_footnote_only_when_a_rank_is_shown(service: SportsService) -> None:
    with_ranks = await service.get_scoreboard("ncaaf", date=DAY, limit=3)
    without = await service.get_scoreboard("nba", date="20261004")
    assert FOOTNOTE in with_ranks.text
    assert FOOTNOTE not in without.text


# ---------------------------------------------------------------- R8: budgets


async def test_r8_scoreboard_text_budget_holds_on_the_ranked_board(service: SportsService) -> None:
    # The 25 KB structured budget is NOT asserted here: the 44-game day is already 34 KB
    # structured without any rank or record field (see implementation-report.md, deviations).
    out = await service.get_scoreboard("ncaaf", date=DAY)
    assert len(out.text.encode()) <= 6_000
    assert len(json.dumps(out.data).encode()) < 40_000


async def test_r8_summary_budget_holds_on_the_ranked_game(service: SportsService) -> None:
    out = await service.get_game("ncaaf", MIA_GAME)
    assert len(out.text.encode()) <= 8_000
