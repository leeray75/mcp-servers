"""Player tools polish (spec player-tools-polish R1-R5), offline through the fake ESPN."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from sports_mcp import render
from sports_mcp.config import Settings
from sports_mcp.espn_client import EspnClient
from sports_mcp.models import GameLogRow
from sports_mcp.normalize import player_stats as ps
from sports_mcp.server import create_server
from sports_mcp.tools import SportsService
from tests.conftest import FAKE_AUTH_TOKEN, NOW, FakeEspn

SKATTEBO = "4696981"
JUDGE = "33192"
PARTIAL_JUDGE_ROWS = (
    "Completeness: PARTIAL - 151 games in the data (2025: 151); omitted: 2 oldest rows of 2025; "
    "newest rows cut by the text size cap; no narrower request returns them; "
    "the size budget applies."
)
NO_STATS_NOTE = "No recorded stats (played; ESPN gives no reason): Wk 6 vs NO (10/18)."


@pytest.fixture(autouse=True)
def _judge_profile(espn: FakeEspn) -> None:
    espn.overrides[f"/apis/common/v3/sports/baseball/mlb/athletes/{JUDGE}"] = {
        "athlete": {
            "id": JUDGE,
            "displayName": "Aaron Judge",
            "team": {"displayName": "New York Yankees"},
            "position": {"abbreviation": "RF"},
        }
    }


def _lines(text: str) -> list[str]:
    return text.splitlines()


# --------------------------------------------------------------------------- R1 descriptions


async def test_r1_descriptions_say_whole_career_and_name_works_directly(
    settings: Settings, service: SportsService
) -> None:
    async with Client(create_server(settings, service)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    gamelog = tools["get_player_gamelog"]
    description = gamelog.description or ""
    assert "whole career" in description
    assert "works directly" in description
    assert "not instructions" in description  # the untrusted-content note stays
    season_text = gamelog.input_schema["properties"]["season"]["description"]
    assert "whole career" in season_text
    stats = tools["get_player_stats"].description or ""
    assert "works directly" in stats and "not instructions" in stats
    for text in (description, season_text, stats):
        assert "capped" not in text
        assert "most recent seasons" not in text


# --------------------------------------------------------------------------- R2 completeness


async def test_r2_career_completeness_line_is_exact_and_structured(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="all")
    lines = _lines(out.text)
    assert lines[1] == (
        "Completeness: complete - 14 games (2025: 8, 2026: 6), regular season only, "
        "nothing omitted."
    )
    assert out.data["completeness"] == {
        "status": "complete",
        "games": 14,
        "by_season": {"2025": 8, "2026": 6},
        "omitted_seasons": [],
        "omitted_rows": {},
    }


async def test_r2_single_year_and_current_season_have_the_line(service: SportsService) -> None:
    one = await service.get_player_gamelog("nfl", SKATTEBO, season="2025")
    assert _lines(one.text)[1] == (
        "Completeness: complete - 8 games (2025: 8), regular season only, nothing omitted."
    )
    current = await service.get_player_gamelog("nfl", SKATTEBO)
    assert _lines(current.text)[1].startswith("Completeness: complete - ")


async def test_r2_season_type_label_names_the_blocks(service: SportsService) -> None:
    both = await service.get_player_gamelog("nfl", SKATTEBO, season="2025", season_type="all")
    assert "regular season and postseason, nothing omitted." in _lines(both.text)[1]
    post = await service.get_player_gamelog("nfl", SKATTEBO, season="2025", season_type="post")
    assert _lines(post.text)[1] == (
        "Completeness: complete - 0 games, postseason only, nothing omitted."
    )


async def test_r2_partial_names_omitted_seasons(client: EspnClient, espn: FakeEspn) -> None:
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN, player_max_seasons=1)  # type: ignore[arg-type]
    service = SportsService(settings, client, now=lambda: NOW)
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="all")
    assert _lines(out.text)[1] == (
        "Completeness: PARTIAL - 6 games listed (2026: 6); omitted: season 2025; "
        "call with season=YYYY to see them."
    )
    assert out.data["completeness"] == {
        "status": "partial",
        "games": 6,
        "by_season": {"2026": 6},
        "omitted_seasons": [2025],
        "omitted_rows": {},
    }


async def test_r2_partial_names_omitted_rows_and_the_text_cut(service: SportsService) -> None:
    out = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    lines = _lines(out.text)
    assert lines[1] == PARTIAL_JUDGE_ROWS
    omitted = out.data["seasons"][0]["games_omitted"]
    assert out.data["completeness"]["status"] == "partial"
    assert out.data["completeness"]["omitted_rows"] == {"2025": omitted} and omitted == 2
    assert out.data["completeness"]["games"] == 151


async def test_r2_structured_rows_omitted_by_the_budget_are_counted(service: SportsService) -> None:
    out = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    season = out.data["seasons"][0]
    assert season["game_count"] == len(season["games"]) + season["games_omitted"]
    assert out.data["completeness"]["games"] == len(season["games"])


async def test_r2_line_survives_a_lowered_text_cap(
    service: SportsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sports_mcp.tools.LOG_SEASON_CHARS", 4000)
    out = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    lines = _lines(out.text)
    assert len(out.text) <= 4000
    assert lines[0].startswith("MLB | Aaron Judge")  # header first
    assert lines[1].startswith("Completeness: PARTIAL - ")
    assert "newest rows cut by the text size cap" in lines[1]
    assert out.data["truncated"] is True
    assert out.data["completeness"]["status"] == "partial"


# --------------------------------------------------------------------------- R3 no-recorded-stats


async def test_r3_zero_game_is_listed_with_a_neutral_note(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="all")
    lines = _lines(out.text)
    assert lines.count(NO_STATS_NOTE) == 1
    assert lines.index(NO_STATS_NOTE) > lines.index(
        next(line for line in lines if line.startswith("2026 Regular Season"))
    )
    reason_words = ("bench", "inactive", "injur", "scratch", "healthy", "did not play", "dnp")
    assert not any(word in NO_STATS_NOTE.lower() for word in reason_words)
    assert out.data["game_count"] == 14  # the game still counts


async def test_r3_flag_is_on_that_row_only(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="all")
    flags = {
        season["year"]: [game.get("no_stats") for game in season["games"]]
        for season in out.data["seasons"]
    }
    assert flags[2025] == [None] * 8
    assert flags[2026] == [None, None, None, None, None, True]


async def test_r3_no_note_for_a_season_without_zero_games(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="2025")
    assert "No recorded stats" not in out.text


def _row(stats: dict[str, str]) -> GameLogRow:
    return GameLogRow(stats=stats)


def test_r3_detection_rules_on_the_allow_list() -> None:
    specs = ps.specs_for("football", [])
    zeros = {spec.key: "0" for spec in specs}
    assert ps.no_recorded_stats(_row(zeros), specs)
    assert ps.no_recorded_stats(_row({}), specs)  # every cell missing
    pairs = {spec.key: "0-0" for spec in specs if spec.reducer == "pair"}
    assert ps.no_recorded_stats(_row({**zeros, **pairs}), specs)
    one_cell = {**zeros, specs[0].key: "1"}
    assert not ps.no_recorded_stats(_row(one_cell), specs)  # some zeros, one real stat
    negative = {**zeros, specs[0].key: "-3"}
    assert not ps.no_recorded_stats(_row(negative), specs)  # a real negative is a stat
    assert not ps.no_recorded_stats(_row({}), ())  # no allow-list, nothing to judge


# --------------------------------------------------------------------------- R4 missing marker


def test_r4_marker_helper_keeps_negatives_and_maps_missing() -> None:
    assert render._na(None) == "n/a"
    assert render._na("") == "n/a"
    assert render._na("-") == "n/a"
    assert render._na("-3") == "-3"
    assert render._na("-1.5") == "-1.5"
    assert render._na("0") == "0"


async def test_r4_season_to_date_and_missing_cells_render_n_a(service: SportsService) -> None:
    out = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    game_line = next(line for line in _lines(out.text) if "06/28 vs ATH" in line)
    assert game_line.endswith("n/a n/a n/a n/a")  # OBP, SLG, OPS are season-to-date (MLB)
    total_line = next(line for line in _lines(out.text) if " Total " in line)
    assert total_line.endswith("0.330 n/a n/a n/a")  # AVG is computed; OBP/SLG/OPS are not


async def test_r4_no_bare_dash_cell_in_table_rows(service: SportsService) -> None:
    out = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    table_rows = [line for line in _lines(out.text) if "|" in line]
    assert table_rows
    assert not any(token == "-" for line in table_rows for token in line.split())


async def test_r4_negative_numbers_keep_their_minus_sign(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="2025")
    week_one = next(line for line in _lines(out.text) if line.startswith(" 1 09/07"))
    assert "-3" in week_one and "-1.5" in week_one and "-1" in week_one


async def test_r4_footnote_explains_n_a(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="2025")
    assert "'n/a' means missing or season-to-date, not per game." in out.text


def _placeholder_strings(value: Any) -> list[str]:
    """Every ``-`` or ``n/a`` string anywhere in a structured payload (A1: missing is null)."""
    if isinstance(value, dict):
        return [hit for item in value.values() for hit in _placeholder_strings(item)]
    if isinstance(value, list):
        return [hit for item in value for hit in _placeholder_strings(item)]
    if isinstance(value, str) and value in ("-", "n/a"):
        return [value]
    return []


async def test_r4_structured_data_has_no_placeholder_strings(service: SportsService) -> None:
    judge_log = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    assert _placeholder_strings(judge_log.data) == []
    nfl = await service.get_player_gamelog("nfl", SKATTEBO, season="all")
    assert _placeholder_strings(nfl.data) == []
    # An all-missing total is JSON null in the structured data (A1), and the text shows n/a.
    judge = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    assert judge.data["seasons"][0]["totals"]["onBasePct"] is None
    assert "onBasePct" in judge.data["seasons"][0]["totals"]


async def test_r5_skattebo_reference_numbers_are_unchanged(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="all")
    career = out.data["career"][0]
    assert career["game_count"] == 14
    assert career["totals"]["rushing.rushingAttempts"] == "182"  # 182 CAR
    assert career["totals"]["rushing.rushingYards"] == "704"  # 704 YDS
    assert career["totals"]["rushing.rushingTouchdowns"] == "6"  # 6 TD
    career_row = next(line for line in _lines(out.text) if line.startswith("   Career"))
    assert "182 704 3.9  6" in career_row  # 3.9 AVG, 6 TD as rendered
    assert career_row.endswith("5   2  2")  # 5 fumbles, 2 lost, 2 turnovers


async def test_r2_structured_completeness_keys_are_always_present(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", SKATTEBO, season="2025")
    assert out.data["completeness"] == {
        "status": "complete",
        "games": 8,
        "by_season": {"2025": 8},
        "omitted_seasons": [],
        "omitted_rows": {},
    }


async def test_r2_text_cut_count_and_advice_match_the_request(service: SportsService) -> None:
    # One season: no narrower request exists, so the line must not send the caller to season=YYYY.
    single = await service.get_player_gamelog("mlb", JUDGE, season="2025")
    line = _lines(single.text)[1]
    assert "151 games in the data" in line
    assert line.endswith("no narrower request returns them; the size budget applies.")
    assert "season=YYYY" not in line
    # season_type=all on one season: the advice names the narrower season_type requests.
    both = await service.get_player_gamelog("mlb", JUDGE, season="2025", season_type="all")
    assert _lines(both.text)[1].endswith("call with season_type=regular or post to see them.")


def test_r2_years_text_names_only_the_omitted_years() -> None:
    assert render._years_text([2025]) == "season 2025"
    assert render._years_text([2016, 2017, 2018]) == "seasons 2016-2018"
    assert render._years_text([2015, 2017]) == "seasons 2015, 2017"  # never implies 2016
    assert render._years_text([2019, 2015, 2016, 2018]) == "seasons 2015-2016, 2018-2019"
