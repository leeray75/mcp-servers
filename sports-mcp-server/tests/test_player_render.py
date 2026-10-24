"""Text shape and size budgets of the player renderers (spec R16, R17), on real fixtures."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from sports_mcp import render
from sports_mcp.leagues import resolve_league
from sports_mcp.tools import (
    LOG_ALL_CHARS,
    LOG_SEASON_CHARS,
    PLAYER_SEARCH_CHARS,
    STATS_CHARS,
    SportsService,
)
from tests.conftest import FakeEspn

TZ = ZoneInfo("America/New_York")
NFL = resolve_league("nfl")
JUDGE_PROFILE = "/apis/common/v3/sports/baseball/mlb/athletes/33192"


@pytest.fixture(autouse=True)
def _judge_profile(espn: FakeEspn) -> None:
    espn.overrides[JUDGE_PROFILE] = {
        "athlete": {
            "id": "33192",
            "displayName": "Aaron Judge",
            "team": {"displayName": "New York Yankees"},
            "position": {"abbreviation": "RF"},
        }
    }


async def test_find_player_text_fits_the_two_kilobyte_budget(service: SportsService) -> None:
    out = await service.find_player("nfl", "smith", limit=10)
    assert len(out.text) <= PLAYER_SEARCH_CHARS
    assert out.text.startswith("NFL | player search 'smith' | 3 matches")


async def test_find_player_marks_fuzzy_and_empty_results(
    service: SportsService, espn: FakeEspn
) -> None:
    fuzzy = await service.find_player("nfl", "skattebu")
    assert "[fuzzy]" in fuzzy.text
    espn.overrides["/apis/common/v3/search?limit=50&query=zzzz&type=player"] = {"items": []}
    espn.overrides["/apis/common/v3/search?limit=50&query=zzz&type=player"] = {"items": []}
    espn.overrides["/apis/common/v3/search?limit=50&query=zz&type=player"] = {"items": []}
    empty = await service.find_player("nfl", "zzzz")
    assert "0 matches" in empty.text and "Try another spelling" in empty.text


def test_search_renderer_singular_and_plural(service: SportsService) -> None:
    del service
    from sports_mcp.models import Meta, PlayerRef, PlayerSearchResult

    meta = Meta(as_of="2026-10-24T12:00:00Z", cache_age_seconds=0, cached=False, endpoint_kind="x")
    one = PlayerSearchResult(
        league="nfl",
        query="a",
        players=[PlayerRef(id="1", name="A B", league="nfl")],
        count=1,
        meta=meta,
    )
    assert "1 match |" in render.render_player_search(one, NFL, TZ)
    many = one.model_copy(update={"count": 2})
    assert "2 matches |" in render.render_player_search(many, NFL, TZ)


async def test_stats_text_shape_and_budget(service: SportsService) -> None:
    out = await service.get_player_stats("nfl", "4696981")
    assert out.text.startswith("NFL | Cam Skattebo, RB, New York Giants (id 4696981) [id]")
    assert "Regular season stats" in out.text
    assert "[Rushing]" in out.text and "ESPN career" in out.text
    assert len(out.text) <= STATS_CHARS


async def test_stats_with_no_postseason_categories_says_so(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides["/apis/common/v3/sports/football/nfl/athletes/4696981/stats?seasontype=3"] = {
        "categories": []
    }
    out = await service.get_player_stats("nfl", "4696981", season_type="post")
    assert "no stats recorded" in out.text


async def test_unresolved_stats_text_lists_candidates(service: SportsService) -> None:
    out = await service.get_player_stats("nfl", "smith")
    assert out.text.startswith("NFL | no single player matched; candidates:")
    assert out.text.count("  - ") == 3


async def test_gamelog_text_groups_and_career_line(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981", season="all")
    assert "Rushing: CAR" in out.text  # group heading shown once above its columns
    assert "| Receiving: REC" in out.text
    assert "Career, regular season(s) 2025-2026 (14 games, computed from listed games):" in out.text
    assert "computed from listed games" in out.text
    assert "Total" in out.text
    assert len(out.text) <= LOG_ALL_CHARS


async def test_gamelog_one_season_budget_and_home_away_markers(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981", season="2025")
    assert len(out.text) <= LOG_SEASON_CHARS
    assert "@" in out.text  # away games are marked with @
    assert "Res" in out.text
    assert "@PHI" in out.text and "L 20-38" in out.text  # a concrete 2025 row


async def test_mlb_gamelog_hides_season_to_date_cells_and_recomputes_avg(
    service: SportsService,
) -> None:
    out = await service.get_player_gamelog("mlb", "33192", season="2025")
    # Wk is right-aligned; one game with no week shows n/a, which widens the column (R4)
    header = next(line for line in out.text.splitlines() if line.lstrip().startswith("Wk"))
    assert "AVG" in header and "OBP" in header
    assert "Total" in out.text
    total_line = next(line for line in out.text.splitlines() if " Total " in line)
    assert "n/a" in total_line  # OBP/SLG/OPS totals are not rebuilt from game rows


async def test_one_mlb_season_is_truncated_to_its_budget(service: SportsService) -> None:
    out = await service.get_player_gamelog("mlb", "33192", season="2025")
    assert len(out.text) <= LOG_SEASON_CHARS
    assert "text cut to fit the size cap" in out.text
    assert "season=YYYY" in out.text


async def test_nba_playoff_round_label_replaces_the_week(service: SportsService) -> None:
    out = await service.get_player_gamelog("nba", "1966", season="2025", season_type="post")
    assert "West 1st Ro." in out.text  # text shortens the round; structured keeps it
    assert "West 1st Round - Game 5" in [g["round"] for g in out.data["seasons"][0]["games"]]
    assert "2024-25 Postseason (5 games; totals computed from listed games)" in out.text


def test_table_aligns_columns_and_repeats_group_headings_once() -> None:
    lines = render._table(
        [("", "Wk"), ("Rushing", "CAR"), ("Rushing", "YDS"), ("Receiving", "REC")],
        [["1", "3", "12", "2"], ["10", "20", "300", "4"]],
    )
    assert lines[0].count("Rushing:") == 1
    assert lines[0].count("|") == 2
    assert all(len(line) == len(lines[0]) or line.rstrip() == line for line in lines)


def test_fit_adds_the_truncation_notice() -> None:
    long_text = "\n".join("line " * 10 for _ in range(200))
    fitted = render.fit(long_text, 300)
    assert len(fitted) <= 300 and "truncated" in fitted
