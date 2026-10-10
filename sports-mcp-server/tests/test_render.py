"""Rendering: size budgets on real fixtures and the text helpers' edge cases."""

from __future__ import annotations

import json
from zoneinfo import ZoneInfo

import pytest

from sports_mcp import render
from sports_mcp.leagues import resolve_league
from sports_mcp.models import Game, LineSnapshot, Meta, OddsLine, Status, TeamLine
from sports_mcp.tools import SportsService

NY = ZoneInfo("America/New_York")
NFL = resolve_league("nfl")


def game(
    state: str = "pre",
    name: str = "STATUS_SCHEDULED",
    detail: str | None = None,
    period: int | None = None,
    clock: str | None = None,
    completed: bool = False,
    **kw: object,
) -> Game:
    return Game(
        id="1",
        start_utc="2026-10-04T20:05:00Z",
        status=Status(
            state=state,  # type: ignore[arg-type]
            name=name,
            detail=detail,
            period=period,
            clock=clock,
            completed=completed,
        ),
        teams=[
            TeamLine(home_away="away", abbr="MIA", score=3 if state != "pre" else None),
            TeamLine(home_away="home", abbr="MIN", score=7 if state != "pre" else None),
        ],
        **kw,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ budgets on real data


async def test_nfl_scoreboard_budget(service: SportsService) -> None:
    out = await service.get_scoreboard("nfl")
    assert out.data["count"] == 16
    assert len(out.text.encode()) <= 6_000
    assert len(json.dumps(out.data).encode()) <= 25_000


@pytest.mark.parametrize(
    ("league", "game_id"),
    [
        ("nfl", "401872965"),
        ("nfl", "401872971"),
        ("nfl", "401872974"),
        ("nba", "401859964"),
        ("wnba", "401918295"),
        ("mlb", "401907972"),
        ("mlb", "401908003"),
        ("nhl", "401891776"),
        ("epl", "401879276"),
        ("epl", "401879268"),
    ],
)
async def test_game_text_budgets(service: SportsService, league: str, game_id: str) -> None:
    summary = await service.get_game(league, game_id, detail="summary")
    assert len(summary.text.encode()) <= 8_000
    performers = await service.get_game(league, game_id, detail="performers", top_n=5)
    assert len(performers.text.encode()) <= 12_000
    boxscore = await service.get_game(league, game_id, detail="boxscore")
    assert len(boxscore.text) <= 20_000


@pytest.mark.parametrize("league", ["nfl", "nba", "mlb", "nhl", "epl"])
async def test_full_standings_budget(service: SportsService, league: str) -> None:
    out = await service.get_standings(league)
    assert len(out.text.encode()) <= 8_000


async def test_other_outputs_are_compact(service: SportsService) -> None:
    assert len((await service.get_team_schedule("nfl", "BUF")).text.encode()) <= 3_000
    assert len((await service.get_news("nfl", limit=5)).text.encode()) <= 4_000
    assert len((await service.get_odds("nfl")).text.encode()) <= 4_000


# ------------------------------------------------------------------ helpers


def test_clock_time_and_freshness() -> None:
    assert render.clock_time("2026-10-04T20:05:00Z", NY) == "4:05 PM EDT"
    assert render.clock_time(None, NY) == "TBD"
    meta = Meta(as_of="2026-10-04T19:00:00Z", cache_age_seconds=6, cached=True, endpoint_kind="x")
    assert render.freshness(meta, NY) == "as of 15:00:00 EDT, cached 6s"
    fresh = meta.model_copy(update={"cached": False, "stale": True})
    assert render.freshness(fresh, NY).startswith("STALE COPY (live refresh failed), as of 15:00")
    assert "fetched just now" in render.freshness(fresh, NY)
    bad = meta.model_copy(update={"as_of": "garbage"})
    assert "as of garbage" in render.freshness(bad, NY)


def test_status_text_variants() -> None:
    assert render.status_text(game(), NFL, NY) == "4:05 PM EDT"
    assert (
        render.status_text(game(name="STATUS_POSTPONED", detail="Postponed"), NFL, NY)
        == "Postponed"
    )
    live = game("in", "STATUS_IN_PROGRESS", "3:00 - 3rd Quarter", period=3, clock="3:00")
    assert render.status_text(live, NFL, NY) == "Q3 3:00"
    nhl = resolve_league("nhl")
    assert render.status_text(live, nhl, NY) == "P3 3:00"
    ot = game("in", "STATUS_IN_PROGRESS", "4:12 - OT", period=5, clock="4:12")
    assert render.status_text(ot, nhl, NY) == "4:12 - OT"
    half = game("in", "STATUS_HALFTIME", "Halftime", period=2, clock="0:00")
    assert render.status_text(half, resolve_league("nba"), NY) == "Halftime"
    soccer = game("in", "STATUS_IN_PROGRESS", "57'", period=2, clock="57'")
    assert render.status_text(soccer, resolve_league("epl"), NY) == "57'"
    assert render.status_text(game("in", "STATUS_X"), NFL, NY) == "In progress"
    final = game("post", "STATUS_FINAL", "Final/OT", completed=True)
    assert render.status_text(final, NFL, NY) == "FINAL/OT"
    assert render.status_text(game("post", "STATUS_CANCELED", "Canceled"), NFL, NY) == "Canceled"
    assert (
        render.status_text(game("post", "STATUS_FINAL", None, completed=True), NFL, NY) == "FINAL"
    )


def test_odds_text_variants() -> None:
    assert render.odds_text(None) == ""
    full = OddsLine(
        provider="DK",
        favorite_abbr="BUF",
        spread=-6.5,
        over_under=44.5,
        home_moneyline=-300,
        away_moneyline=250,
    )
    assert render.odds_text(full) == "BUF -6.5 O/U 44.5 ML +250/-300 (DK)"
    assert render.odds_text(OddsLine(spread=0.0, over_under=40.0)) == "PK O/U 40"
    assert render.odds_text(OddsLine(over_under=40.0)) == "O/U 40"
    assert render.odds_text(OddsLine(draw_moneyline=390)) == "draw +390"


def test_situation_text_variants() -> None:
    assert render.situation_text(None) == ""
    football = {"down_distance": "2nd & 2 at NE 45", "possession": "NE", "red_zone": True}
    assert render.situation_text(football) == "2nd & 2 at NE 45, NE ball, red zone"
    baseball = {"outs": 2, "bases": ["1B", "3B"], "batter": "A. Batter"}
    assert render.situation_text(baseball) == "2 out, on 1B 3B, AB A. Batter"
    assert render.situation_text({"strength": "Power Play"}) == "Power Play"
    assert render.situation_text({"bases": []}) == ""
    assert render.situation_text({"other": 1}) == ""


def test_game_line_variants() -> None:
    no_sides = Game(
        id="2", name="Mystery game", status=Status(state="pre"), teams=[TeamLine(abbr="AAA")]
    )
    assert "AAA" in render.game_line(no_sides, NFL, NY)
    nameless = Game(id="3", status=Status(state="pre"), teams=[])
    assert render.game_line(nameless, NFL, NY).endswith("3")
    live = game(
        "in", "STATUS_IN_PROGRESS", "x", period=1, clock="9:00", situation={"strength": "PP"}
    )
    line = render.game_line(live, resolve_league("nhl"), NY)
    assert line.startswith("P1 9:00") and "MIA 3 @ MIN 7" in line and line.endswith("PP")
    assert render.counts([live]) == "1 live"


def test_fit_truncates_on_a_line_boundary_with_a_notice() -> None:
    text = "\n".join(f"line {i:03d}" for i in range(200))
    assert render.fit("short", 100) == "short"
    cut = render.fit(text, 300)
    assert len(cut) <= 300 and "truncated" in cut
    assert cut.startswith("line 000\nline 001")
    assert render.fit("x" * 500, 200).endswith("less (smaller top_n / detail / limit)]")


def test_snapshot_rendering_in_odds_output() -> None:
    line = OddsLine(
        open=LineSnapshot(
            home_spread=-7.5, over_under=43.5, home_moneyline=-395, away_moneyline=310
        ),
        close=LineSnapshot(home_spread=-10.0),
    )
    text = render._snapshot(line)
    assert text == "open: home -7.5, O/U 43.5, ML +310/-395; close: home -10"
    assert render._snapshot(OddsLine()) == ""
