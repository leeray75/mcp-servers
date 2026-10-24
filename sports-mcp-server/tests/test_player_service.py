"""Player service logic through the fake ESPN (spec R1-R7, R13-R16, offline)."""

from __future__ import annotations

import copy
import json
import re
from zoneinfo import ZoneInfo

import pytest

from sports_mcp.config import Settings
from sports_mcp.errors import InvalidInputError, NotFoundError, UpstreamError
from sports_mcp.espn_client import EspnClient
from sports_mcp.leagues import resolve_league
from sports_mcp.normalize import player_stats as ps
from sports_mcp.tools import LOG_ALL_CHARS, LOG_SEASON_CHARS, STATS_CHARS, SportsService
from tests.conftest import FAKE_AUTH_TOKEN, NOW, FakeEspn, load

PLAYERS = "/apis/common/v3/sports"
TZ = ZoneInfo("America/New_York")
MLB = resolve_league("mlb")
SEARCH = "/apis/common/v3/search"
SKAT = f"{PLAYERS}/football/nfl/athletes/4696981"
DARNOLD = f"{PLAYERS}/football/nfl/athletes/3912547"
JUDGE = f"{PLAYERS}/baseball/mlb/athletes/33192"


@pytest.fixture(autouse=True)
def _profiles_without_captures(espn: FakeEspn) -> None:
    """Synthetic minimal profiles for ids that have no captured profile fixture."""
    espn.overrides[DARNOLD] = {
        "athlete": {
            "id": "3912547",
            "displayName": "Sam Darnold",
            "team": {"displayName": "Seattle Seahawks"},
            "position": {"abbreviation": "QB"},
        }
    }
    espn.overrides[JUDGE] = {
        "athlete": {
            "id": "33192",
            "displayName": "Aaron Judge",
            "team": {"displayName": "New York Yankees"},
            "position": {"abbreviation": "RF"},
        }
    }


def search_key(query: str) -> str:
    return f"{SEARCH}?limit=50&query={query}&type=player"


def make_empty_search(espn: FakeEspn, *queries: str) -> None:
    for query in queries:
        espn.overrides[search_key(query)] = {"count": 0, "items": []}


# --------------------------------------------------------------------------- find_player


async def test_find_player_exact_single_match(service: SportsService) -> None:
    out = await service.find_player("nfl", "skattebo")
    player = out.data["players"][0]
    assert player["id"] == "4696981"
    assert player["name"] == "Cam Skattebo"
    assert player["team"] == "New York Giants"
    assert "match" not in player  # plain listing, not a resolution
    assert out.data["meta"]["endpoint_kind"] == "player_search"
    assert out.cache == "miss"
    assert "Cam Skattebo" in out.text


async def test_find_player_fuzzy_typo_is_marked(service: SportsService) -> None:
    out = await service.find_player("nfl", "skattebu")
    assert out.data["players"][0]["match"] == "fuzzy"
    assert "closest" in out.data["note"]


async def test_find_player_empty_result_is_not_an_error(
    service: SportsService, espn: FakeEspn
) -> None:
    make_empty_search(espn, "nobody", "nobod", "nobo")
    out = await service.find_player("nfl", "nobody")
    assert out.data["players"] == []
    assert out.data["count"] == 0
    assert "Try another spelling" in out.data["note"]


async def test_find_player_limit_is_applied(service: SportsService) -> None:
    out = await service.find_player("nfl", "smith", limit=1)
    assert len(out.data["players"]) == 1


@pytest.mark.parametrize("bad", ["", "../etc", "a_b", "x" * 61, "a?b=1", "a\nb"])
async def test_player_name_validation_sends_no_request(
    service: SportsService, espn: FakeEspn, bad: str
) -> None:
    with pytest.raises(InvalidInputError):
        await service.find_player("nfl", bad)
    assert espn.requests == []


async def test_unknown_league_is_rejected(service: SportsService) -> None:
    with pytest.raises(InvalidInputError):
        await service.find_player("not-a-league", "skattebo")


# --------------------------------------------------------------------------- resolution


async def test_id_lookup_uses_profile_for_name_team_and_position(
    service: SportsService, espn: FakeEspn
) -> None:
    out = await service.get_player_stats("nfl", "4696981")
    player = out.data["player"]
    assert (player["name"], player["team"], player["position"]) == (
        "Cam Skattebo",
        "New York Giants",
        "RB",
    )
    assert player["match"] == "id"
    assert SKAT in espn.keys  # the profile endpoint was called
    assert out.data["resolved"] is True


async def test_name_lookup_does_not_call_the_profile(
    service: SportsService, espn: FakeEspn
) -> None:
    out = await service.get_player_stats("nfl", "skattebo")
    assert out.data["player"]["match"] == "single"
    assert SKAT not in espn.keys  # R4: name lookups take name/team from the search item


async def test_typo_resolves_to_skattebo_as_fuzzy(service: SportsService, espn: FakeEspn) -> None:
    out = await service.get_player_gamelog("nfl", "skattebu", season="2025")
    assert out.data["resolved"] is True
    assert out.data["player"]["id"] == "4696981"
    assert out.data["player"]["match"] == "fuzzy"
    assert search_key("skatteb") in espn.keys  # the first retry


async def test_ambiguous_name_is_unresolved_with_candidates(service: SportsService) -> None:
    out = await service.get_player_stats("nfl", "smith")
    assert out.data["resolved"] is False
    assert len(out.data["candidates"]) == 3
    assert "player=<id>" in out.data["note"]
    assert "blocks" not in out.data


async def test_unknown_athlete_id_is_not_found(service: SportsService) -> None:
    with pytest.raises(NotFoundError) as info:
        await service.get_player_stats("nfl", "99999999")
    assert str(info.value) == "Athlete not found for league nfl"


async def test_unresolved_gamelog_lists_candidates(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "smith")
    assert out.data["resolved"] is False
    assert out.data.get("seasons") is None


# --------------------------------------------------------------------------- stats


async def test_stats_regular_rows_and_espn_career_totals(service: SportsService) -> None:
    out = await service.get_player_stats("nfl", "4696981")
    block = out.data["blocks"][0]
    assert block["season_type"] == "regular"
    rushing = next(c for c in block["categories"] if c["name"] == "rushing")
    assert [r["season"] for r in rushing["rows"]] == [2025, 2026]
    assert rushing["career"]["rushingAttempts"] == "182"
    assert "[Rushing]" in out.text


async def test_stats_postseason_and_all(service: SportsService, espn: FakeEspn) -> None:
    post = await service.get_player_stats("nfl", "3912547", season_type="post")
    assert [b["season_type"] for b in post.data["blocks"]] == ["post"]
    assert f"{DARNOLD}/stats?seasontype=3" in espn.keys
    both = await service.get_player_stats("nfl", "3912547", season_type="all")
    assert [b["season_type"] for b in both.data["blocks"]] == ["regular", "post"]
    assert both.data["blocks"][1]["label"] == "Postseason"


async def test_stats_post_without_playoffs_has_a_note(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides[f"{SKAT}/stats?seasontype=3"] = {"categories": []}
    out = await service.get_player_stats("nfl", "4696981", season_type="post")
    assert "No postseason stats" in out.data["note"]


async def test_stats_season_type_validation(service: SportsService) -> None:
    with pytest.raises(InvalidInputError):
        await service.get_player_stats("nfl", "4696981", season_type="bogus")


async def test_stats_without_any_categories_says_so(service: SportsService, espn: FakeEspn) -> None:
    espn.overrides[f"{SKAT}/stats"] = {"categories": []}
    out = await service.get_player_stats("nfl", "4696981")
    assert "ESPN lists no stats" in out.data["note"]


# --------------------------------------------------------------------------- game logs


async def test_gamelog_one_season(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981", season="2025")
    assert out.data["game_count"] == 8  # AC2
    assert len(out.data["seasons"]) == 1
    assert "career" not in out.data  # one season: no career block
    assert out.data["meta"]["endpoint_kind"] == "player_gamelog"
    assert len(out.text) <= LOG_SEASON_CHARS
    assert "2025 Regular Season (8 games; totals computed from listed games)" in out.text
    assert "computed from listed games" in out.text


async def test_gamelog_current_season_default(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981")
    assert out.data["game_count"] == 6  # 2026 so far (includes the zero-stat game)


async def test_gamelog_empty_season_is_a_note_not_an_error(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981", season="2015")
    assert out.data["seasons"] == []
    assert out.data["game_count"] == 0
    assert "No games listed in 2015" in out.data["note"]


async def test_gamelog_all_seasons_with_career_totals(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981", season="all")
    assert [s["year"] for s in out.data["seasons"]] == [2025, 2026]
    career = out.data["career"][0]
    assert career["season_type"] == "regular"
    assert career["game_count"] == 14  # AC2
    assert career["totals"]["rushing.rushingAttempts"] == "182"  # AC2: 182 CAR
    assert career["totals"]["rushing.rushingYards"] == "704"  # AC2: 704 YDS
    assert career["totals"]["rushing.rushingTouchdowns"] == "6"  # AC2: 6 TD
    assert out.data["meta"]["note"] == (
        "Built from 4 upstream calls: 1 lookup, 1 stats, 2 game logs."
    )
    assert "computed from listed games" in out.text


async def test_gamelog_all_respects_player_max_seasons(client: EspnClient, espn: FakeEspn) -> None:
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN, player_max_seasons=1)  # type: ignore[arg-type]
    service = SportsService(settings, client, now=lambda: NOW)
    out = await service.get_player_gamelog("nfl", "4696981", season="all")
    assert [s["year"] for s in out.data["seasons"]] == [2026]
    assert out.data["truncated"] is True
    assert "1 earlier season omitted (2025); call with season=YYYY." in out.data["note"]
    assert out.data.get("career") is None


async def test_gamelog_post_and_all_for_darnold(service: SportsService) -> None:
    post = await service.get_player_gamelog("nfl", "3912547", season="2025", season_type="post")
    assert [s["season_type"] for s in post.data["seasons"]] == ["post"]
    assert post.data["seasons"][0]["game_count"] == 3
    both = await service.get_player_gamelog("nfl", "3912547", season="2025", season_type="all")
    assert [s["season_type"] for s in both.data["seasons"]] == ["regular", "post"]


async def test_gamelog_season_and_type_validation(service: SportsService, espn: FakeEspn) -> None:
    for season in ("1989", "2101", "abcd", "2025x"):
        with pytest.raises(InvalidInputError):
            await service.get_player_gamelog("nfl", "4696981", season=season)
    with pytest.raises(InvalidInputError):
        await service.get_player_gamelog("nfl", "4696981", season_type="pre")
    assert espn.requests == []


async def test_gamelog_all_truncates_oldest_seasons_to_fit_the_text_budget(
    client: EspnClient, espn: FakeEspn
) -> None:
    """Ten MLB seasons of 160 games each cannot fit 20 KB: oldest seasons go first."""
    years = list(range(2016, 2026))
    base = load("player_gl_mlb_judge_2025")
    espn.overrides[f"{JUDGE}/stats"] = {
        "categories": [
            {
                "name": "career-batting",
                "displayName": "Career Batting",
                "labels": ["GP"],
                "names": ["gamesPlayed"],
                "statistics": [
                    {
                        "season": {"year": y, "displayName": str(y)},
                        "teamSlug": "new-york-yankees",
                        "teamId": "10",
                        "stats": ["160"],
                    }
                    for y in years
                ],
                "totals": ["1600"],
            }
        ]
    }
    for year in years:
        payload = copy.deepcopy(base)
        for block in payload["seasonTypes"]:
            block["displayName"] = block["displayName"].replace("2025", str(year))
        espn.overrides[f"{JUDGE}/gamelog?season={year}"] = payload
    settings = Settings(mcp_auth_token=FAKE_AUTH_TOKEN)  # type: ignore[arg-type]
    service = SportsService(settings, client, now=lambda: NOW)
    out = await service.get_player_gamelog("mlb", "33192", season="all")
    assert out.data["truncated"] is True
    assert len(out.text) <= LOG_ALL_CHARS
    assert len(json.dumps(out.data)) <= 60000
    kept = sorted(s["year"] for s in out.data["seasons"])
    assert kept == list(range(kept[0], 2026))  # oldest seasons dropped first, newest kept


async def test_gamelog_year_bounds_are_accepted_by_the_server(
    service: SportsService, espn: FakeEspn
) -> None:
    for year in ("1990", "2100"):
        espn.overrides[f"{SKAT}/gamelog?season={year}"] = {"filters": []}
    out = await service.get_player_gamelog("nfl", "4696981", season="1990")
    assert out.data["game_count"] == 0


# --------------------------------------------------------------------------- caching and errors


async def test_cache_hit_refresh_and_stale_if_error(service: SportsService, espn: FakeEspn) -> None:
    first = await service.get_player_gamelog("nfl", "4696981", season="2025")
    second = await service.get_player_gamelog("nfl", "4696981", season="2025")
    assert (first.cache, second.cache) == ("miss", "hit")
    refreshed = await service.get_player_gamelog("nfl", "4696981", season="2025", refresh=True)
    assert refreshed.cache == "miss"
    espn.overrides[f"{SKAT}/gamelog?season=2025"] = 500
    stale = await service.get_player_gamelog("nfl", "4696981", season="2025", refresh=True)
    assert stale.cache == "stale"
    assert stale.data["meta"]["stale"] is True
    assert stale.data["game_count"] == 8


async def test_failed_upstream_is_not_cached(service: SportsService, espn: FakeEspn) -> None:
    espn.overrides[search_key("skattebo")] = 500
    with pytest.raises(UpstreamError):
        await service.find_player("nfl", "skattebo")
    del espn.overrides[search_key("skattebo")]
    out = await service.find_player("nfl", "skattebo")
    assert out.data["players"][0]["id"] == "4696981"
    assert out.cache == "miss"


async def test_upstream_errors_do_not_echo_query_or_body(
    service: SportsService, espn: FakeEspn
) -> None:
    espn.overrides[f"{SKAT}/gamelog?season=2025"] = 500
    with pytest.raises(UpstreamError) as info:
        await service.get_player_gamelog("nfl", "4696981", season="2025")
    assert "SECRET" not in str(info.value)
    assert "4696981" not in str(info.value)


async def test_meta_fields_present_on_every_player_result(service: SportsService) -> None:
    for out in (
        await service.find_player("nfl", "skattebo"),
        await service.get_player_stats("nfl", "4696981"),
        await service.get_player_gamelog("nfl", "4696981", season="2025"),
    ):
        meta = out.data["meta"]
        assert {"as_of", "cache_age_seconds", "cached", "endpoint_kind"} <= set(meta)
        assert isinstance(meta["as_of"], str) and meta["as_of"]


async def test_stats_text_respects_the_stats_budget(service: SportsService) -> None:
    out = await service.get_player_stats("nfl", "3912547", season_type="all")
    assert len(out.text) <= STATS_CHARS
    assert out.data["blocks"]


async def test_single_large_season_stays_within_the_structured_budget(
    service: SportsService,
) -> None:
    """R16: a 150-plus-game MLB season keeps structured data within 60 KB."""
    out = await service.get_player_gamelog("mlb", "33192", season="2025")
    assert len(json.dumps(out.data)) <= 60000
    season = out.data["seasons"][0]
    omitted = season["games_omitted"]
    assert omitted > 0
    assert season["game_count"] == len(season["games"]) + omitted


async def test_row_capping_never_changes_season_totals(service: SportsService) -> None:
    """M1: totals come from every game of the season; dropping rows only shortens the listing."""
    out = await service.get_player_gamelog("mlb", "33192", season="2025")
    season = out.data["seasons"][0]
    full = ps.parse_gamelog(load("player_gl_mlb_judge_2025"), MLB, TZ)
    regular = next(s for s in full.seasons if s.season_type == "regular")
    assert season["game_count"] == regular.game_count == 153
    assert season["totals"]["hits"] == regular.totals["hits"]
    assert season["totals"]["atBats"] == regular.totals["atBats"]
    assert season["totals"]["avg"] == regular.totals["avg"]
    assert out.data["truncated"] is True
    # the explanation is the first line under the header, so the size cap cannot remove it
    assert re.match(r"\d+ oldest game rows are not listed", out.text.splitlines()[1])
    assert out.data["game_count"] == 153


async def test_season_all_on_one_mlb_season_keeps_postseason_and_stays_in_budget(
    service: SportsService,
) -> None:
    """M2: the largest block is trimmed (regular), the 7-game postseason block is left whole."""
    out = await service.get_player_gamelog("mlb", "33192", season="2025", season_type="all")
    assert len(json.dumps(out.data)) <= 60000
    blocks = {s["season_type"]: s for s in out.data["seasons"]}
    assert blocks["post"]["game_count"] == 7
    assert len(blocks["post"]["games"]) == 7 and "games_omitted" not in blocks["post"]
    assert blocks["regular"]["games_omitted"] > 0
    assert "of 7 games" not in out.text
    assert "computed from all 153 games" in out.text


async def test_cam_skattebu_resolves_through_the_literal_retry(service: SportsService) -> None:
    """AC2 with the literal query: 'Cam Skattebu' finds nothing, 'Cam Skatteb' finds Skattebo."""
    out = await service.get_player_gamelog("nfl", "Cam Skattebu", season="all")
    assert out.data["resolved"] is True
    assert out.data["player"]["id"] == "4696981"
    assert out.data["player"]["match"] == "fuzzy"
    assert out.data["game_count"] == 14
    career = out.data["career"][0]
    assert career["totals"]["rushing.rushingAttempts"] == "182"
    assert career["totals"]["rushing.rushingYards"] == "704"
    assert career["totals"]["rushing.rushingTouchdowns"] == "6"


async def test_text_cut_without_row_drop_sets_truncated(
    service: SportsService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """m6: a text-only cut (structured data fits) still sets structured truncated = true.

    The text cap is lowered so the cut is guaranteed: LeBron's 2025 regular season is about
    7.2 KB of text and 35 KB of structured data, so only the text cap is exceeded, never the
    structured budget.
    """
    monkeypatch.setattr("sports_mcp.tools.LOG_SEASON_CHARS", 4000)
    out = await service.get_player_gamelog("nba", "1966", season="2025")
    assert "text cut to fit the size cap" in out.text  # the cut really happened
    assert out.data["truncated"] is True
    assert all("games_omitted" not in s for s in out.data["seasons"])  # no rows were dropped


async def test_passing_and_defense_columns_only_for_players_with_those_categories(
    service: SportsService,
) -> None:
    """m1: a running back's text has no Passing or Defense columns; a QB keeps Passing."""
    rb = await service.get_player_gamelog("nfl", "4696981", season="2025")
    assert "Rushing: CAR" in rb.text and "Passing:" not in rb.text and "Defense:" not in rb.text
    qb = await service.get_player_gamelog("nfl", "3912547", season="2025")
    assert "Passing: CMP" in qb.text
    assert "Defense:" not in qb.text


async def test_call_count_note_is_exact_for_season_all(service: SportsService) -> None:
    out = await service.get_player_gamelog("nfl", "4696981", season="all")
    assert out.data["meta"]["note"] == (
        "Built from 4 upstream calls: 1 lookup, 1 stats, 2 game logs."
    )
