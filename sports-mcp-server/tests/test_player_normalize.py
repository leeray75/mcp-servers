"""Pure player normalization against the captured fixtures (spec R1-R18, offline)."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sports_mcp.errors import UpstreamError
from sports_mcp.leagues import resolve_league
from sports_mcp.models import GameLogRow, SeasonLog
from sports_mcp.normalize import player_stats as ps
from tests.conftest import load

TZ = ZoneInfo("America/New_York")
NFL = resolve_league("nfl")
NBA = resolve_league("nba")
MLB = resolve_league("mlb")


# --------------------------------------------------------------------------- names and search


def test_fold_name_ignores_case_accents_and_punctuation() -> None:
    assert ps.fold_name("Jaxon Smith-Njigba") == "jaxon smith njigba"
    assert ps.fold_name("José  Núñez") == "jose nunez"
    assert ps.fold_name("Jabari Smith Jr.") == "jabari smith jr"


def test_levenshtein_edit_distance() -> None:
    assert ps.levenshtein("skattebu", "skattebo") == 1
    assert ps.levenshtein("kitten", "sitting") == 3
    assert ps.levenshtein("", "abc") == 3
    assert ps.levenshtein("same", "same") == 0


def test_name_distance_uses_full_name_or_any_word() -> None:
    assert ps.name_distance("skattebu", "Cam Skattebo") == 1
    assert ps.name_distance("camm", "Cam Skattebo") == 1


def test_retry_queries_trim_longest_alpha_token_keeping_four_chars() -> None:
    assert ps.retry_queries("skattebu") == ["skatteb", "skatte", "skatt"]
    assert ps.retry_queries("Bob Lee") == []  # trimming "Bob" would leave fewer than 4 chars
    assert ps.retry_queries("12345") == []
    assert len(ps.retry_queries("abcdefghijkl")) == ps.MAX_RETRIES


def test_parse_search_keeps_only_the_requested_league() -> None:
    found = ps.parse_search(load("player_search_smith"), NFL)
    assert [c.id for c in found] == ["4241478", "4430878", "15864"]
    assert found[0].team == "Philadelphia Eagles"
    assert [c.order for c in found] == [0, 1, 2]


def test_parse_search_empty_and_malformed() -> None:
    assert ps.parse_search(load("player_search_skattebu"), NFL) == []
    with pytest.raises(UpstreamError):
        ps.parse_search({"items": "nope"}, NFL)
    skipped = {"items": [5, {"sport": "football", "league": "nfl", "id": "x"}]}
    assert ps.parse_search(skipped, NFL) == []


def test_resolve_single_exact_and_unresolved() -> None:
    skat = ps.parse_search(load("player_search_skattebo"), NFL)
    assert ps.resolve("skattebo", skat, fuzzy=False).match == "single"
    smiths = ps.parse_search(load("player_search_smith"), NFL)
    geno = ps.resolve("Geno Smith", smiths, fuzzy=False)
    assert geno.match == "exact" and geno.chosen is not None and geno.chosen.id == "15864"
    assert ps.resolve("smith", smiths, fuzzy=False).chosen is None


def test_resolve_fuzzy_auto_resolves_only_when_clear() -> None:
    cands = ps.parse_search(load("player_search_skatteb"), NFL)
    fuzzy = ps.resolve("skattebu", cands, fuzzy=True)
    assert fuzzy.match == "fuzzy" and fuzzy.chosen is not None
    assert ps.resolve("zzzzzzzz", cands, fuzzy=True).chosen is None
    assert ps.resolve("skattebu", [], fuzzy=True).chosen is None


def test_resolve_fuzzy_needs_a_clear_gap_to_the_next_candidate() -> None:
    a = ps.Candidate("1", "Jon Smith", None, 0)
    b = ps.Candidate("2", "Jon Smith", None, 1)
    assert ps.resolve("jon smith", [a, b], fuzzy=True).chosen is None  # equal: no clear winner


def test_rank_for_listing_exact_first_then_order_and_fuzzy_by_distance() -> None:
    cands = ps.parse_search(load("player_search_smith"), NFL)
    ranked = ps.rank_for_listing("Geno Smith", cands, fuzzy=False)
    assert [c.id for c, _ in ranked][0] == "15864"
    assert ranked[0][1] == "exact"
    fuzzy_ranked = ps.rank_for_listing("geno smyth", cands, fuzzy=True)
    assert fuzzy_ranked[0][0].id == "15864"
    assert all(m == "fuzzy" for _, m in fuzzy_ranked)


def test_parse_profile_for_skattebo_and_lebron() -> None:
    skat = ps.parse_profile(load("player_profile_nfl_skattebo"), "4696981")
    assert (skat.name, skat.team, skat.position) == ("Cam Skattebo", "New York Giants", "RB")
    lebron = ps.parse_profile(load("player_profile_nba_lebron"), "1966")
    assert lebron.name == "LeBron James" and lebron.position == "F"
    with pytest.raises(UpstreamError):
        ps.parse_profile({"athlete": "x"}, "1")
    with pytest.raises(UpstreamError):
        ps.parse_profile({"athlete": {"team": {}}}, "1")


def test_seasons_played_most_recent_first() -> None:
    assert ps.seasons_played(load("player_stats_nfl_skattebo")) == [2026, 2025]
    assert ps.seasons_played({"categories": None}) == []


# --------------------------------------------------------------------------- values and reducers


def test_to_number_and_pairs() -> None:
    assert ps.to_number("4,048") == 4048.0
    assert ps.to_number(".265") == 0.265
    assert ps.to_number("-3") == -3.0
    assert ps.to_number("-") is None
    assert ps.to_number("9-21") is None
    assert ps.pair_parts("9-21") == (9.0, 21.0)
    assert ps.pair_parts("9.5") is None
    assert ps.pair_parts(None) is None


def _row(**stats: str) -> GameLogRow:
    return GameLogRow(stats=stats)


def test_compute_totals_each_reducer() -> None:
    specs = (
        ps.Spec("A", "", None, "a", "sum"),
        ps.Spec("M", "", None, "m", "max"),
        ps.Spec("R", "", None, "r", "ratio", ratio=("n", "d")),
        ps.Spec("P", "", None, "p", "pair"),
        ps.Spec("N", "", None, "n2", "none"),
    )
    rows = [
        _row(a="2", m="5", n="3", d="4", p="9-21", n2="x"),
        _row(a="-", m="9", n="1", d="4", p="8-15"),  # "-" is missing, not zero
        _row(a="1.5", d="0"),
    ]
    totals = ps.compute_totals(rows, specs)
    assert totals == {"a": "3.5", "m": "9", "r": "0.5", "p": "17-36", "n2": "-"}
    assert ps.compute_totals([_row()], specs)["a"] == "-"  # every input missing
    assert ps.compute_totals([_row(n="2", d="0")], specs)["r"] == "-"  # zero denominator


def test_compute_totals_ratio_for_rates_and_missing_inputs() -> None:
    spec = ps.Spec("AVG", "", None, "avg", "ratio", ratio=("hits", "atBats"), digits=3)
    rows = [_row(hits="2", atBats="5"), _row(hits="1", atBats="3")]
    assert ps.compute_totals(rows, (spec,))["avg"] == "0.375"
    assert ps.compute_totals([_row(hits="1")], (spec,))["avg"] == "-"


def test_specs_for_generic_fallback_and_allow_lists() -> None:
    assert ps.specs_for("football", []) == ps._FOOTBALL
    columns = [
        ps.Column("x", None, "fieldGoalPct", "FG%"),
        ps.Column("x", None, "longShot", "LNG"),
        ps.Column("x", None, "goals", "G"),
    ]
    generic = ps.specs_for("hockey", columns)
    assert [s.reducer for s in generic] == ["none", "max", "sum"]


# --------------------------------------------------------------------------- columns


def test_columns_of_uses_category_slices_not_labels() -> None:
    cols = ps.columns_of(load("player_gl_nfl_byard_2025"))
    keys = [c.key for c in cols]
    assert "interceptions.interceptions" in keys
    assert "passing.interceptions" not in keys  # a defender has no passing category
    assert len(keys) == 17


def test_columns_of_without_categories_and_when_empty() -> None:
    assert ps.columns_of({"names": []}) == []
    cols = ps.columns_of(load("player_gl_nba_lebron_2025"))
    assert cols[0].key == "minutes" and cols[0].category is None


@pytest.mark.parametrize(
    "bad",
    [
        {"names": ["a", "b"], "labels": ["A"]},  # label count mismatch
        {"names": ["a"], "labels": ["A"], "categories": [{"name": "c", "count": 2}]},
        {"names": ["a"], "labels": ["A"], "categories": [{"name": "c", "count": "x"}]},
        {"names": [None], "labels": ["A"]},
    ],
)
def test_columns_of_rejects_malformed_maps(bad: dict[str, object]) -> None:
    with pytest.raises(UpstreamError):
        ps.columns_of(bad)


# --------------------------------------------------------------------------- game logs


def _season(parsed: ps.ParsedGameLog, year: int, kind: str) -> SeasonLog:
    matches = [s for s in parsed.seasons if s.year == year and s.season_type == kind]
    assert len(matches) == 1, (year, kind)
    return matches[0]


def test_gamelog_skattebo_2025_matches_espn_and_spec() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nfl_skattebo_2025"), NFL, TZ)
    season = _season(parsed, 2025, "regular")
    assert season.game_count == 8  # AC2: 8 games in 2025
    assert season.totals["rushing.rushingAttempts"] == "101"
    assert season.totals["rushing.rushingYards"] == "410"
    assert season.totals["rushing.rushingTouchdowns"] == "5"
    assert season.totals["rushing.yardsPerRushAttempt"] == "4.1"
    assert season.totals["rushing.longRushing"] == "24"
    assert season.totals["passing.interceptions"] == "-"  # no passing category: INT is missing
    assert season.totals["derived.TO"] == season.totals["fumbles.fumblesLost"]


def test_gamelog_skattebo_2026_includes_the_all_zero_game() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nfl_skattebo_2026"), NFL, TZ)
    season = _season(parsed, 2026, "regular")
    assert season.game_count == 6  # AC2: 6 games in 2026, zero-stat game counted
    zero = [g for g in season.games if g.opponent == "NO"]
    assert len(zero) == 1 and zero[0].stats["rushing.rushingAttempts"] == "0"
    assert season.totals["rushing.rushingAttempts"] == "81"
    assert season.totals["rushing.rushingYards"] == "294"


def test_career_totals_equal_espn_career_and_spec_line() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nfl_skattebo_2025"), NFL, TZ)
    later = ps.parse_gamelog(load("player_gl_nfl_skattebo_2026"), NFL, TZ)
    career = ps.career_log(parsed.seasons + later.seasons, "regular", parsed.specs)
    assert career is not None and career.game_count == 14
    assert career.totals["rushing.rushingAttempts"] == "182"  # ESPN career CAR 182
    assert career.totals["rushing.rushingYards"] == "704"  # ESPN career YDS 704
    assert career.totals["rushing.rushingTouchdowns"] == "6"  # ESPN career TD 6
    assert career.totals["rushing.yardsPerRushAttempt"] == "3.9"  # ESPN career AVG 3.9
    assert ps.career_log(parsed.seasons, "post", parsed.specs) is None


def test_gamelog_totals_equal_arithmetic_sums_of_rows() -> None:
    """Spec R9 property: every summable column's total equals the sum of the listed rows."""
    for fixture in (
        "player_gl_nfl_skattebo_2025",
        "player_gl_nfl_darnold_2025",
        "player_gl_nfl_byard_2025",
    ):
        parsed = ps.parse_gamelog(load(fixture), NFL, TZ)
        for season in parsed.seasons:
            for spec in parsed.specs:
                if spec.reducer != "sum":
                    continue
                numbers = [
                    n
                    for n in (ps.to_number(g.stats.get(spec.key)) for g in season.games)
                    if n is not None
                ]
                expected = "-" if not numbers else str(int(sum(numbers)))
                assert season.totals[spec.key] == expected, (fixture, spec.key)


def test_gamelog_postseason_blocks_and_round_labels_and_r18_score() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nfl_darnold_2025"), NFL, TZ)
    post = _season(parsed, 2025, "post")
    assert post.game_count == 3
    final = max(post.games, key=lambda g: g.date or "")
    assert final.round == "Super Bowl LX" and final.week == 5
    assert final.result == "W" and final.score == "29-13"  # player's team (SEA) first
    regular = _season(parsed, 2025, "regular")
    assert regular.game_count == 17


def test_r18_shows_the_player_team_first_for_a_loss_with_winner_first_upstream_score() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nfl_skattebo_2026"), NFL, TZ)
    season = _season(parsed, 2026, "regular")
    zero = next(g for g in season.games if g.opponent == "NO")
    assert zero.result == "L" and zero.score == "27-34"


def test_derived_result_wins_over_upstream_letter_without_error() -> None:
    event = {
        "team": {"id": "19"},
        "homeTeamId": "19",
        "awayTeamId": "2",
        "homeTeamScore": "10",
        "awayTeamScore": "20",
        "gameResult": "W",  # wrong upstream letter: derived L wins
    }
    score, result, home_away = ps._score_and_result(event, "W")
    assert (score, result, home_away) == ("10-20", "L", "home")


def test_score_and_result_fallbacks() -> None:
    assert ps._score_and_result({}, "T") == (None, "T", None)
    assert ps._score_and_result({"atVs": "@", "gameResult": "Z"}, "Z") == (None, None, "away")
    atvs_only = {"atVs": "vs", "homeTeamScore": 3, "awayTeamScore": 3}
    assert ps._score_and_result(atvs_only, None) == ("3-3", "T", "home")
    by_team_away = {"team": {"id": "2"}, "awayTeamId": "2", "homeTeamScore": 1, "awayTeamScore": 4}
    assert ps._score_and_result(by_team_away, None) == ("4-1", "W", "away")


def test_turnovers_derived_for_qb_rb_and_defender() -> None:
    # Darnold (QB, no fumble columns): TO is the interceptions thrown only.
    qb = ps.parse_gamelog(load("player_gl_nfl_darnold_2025"), NFL, TZ)
    season = _season(qb, 2025, "regular")
    assert season.totals["derived.TO"] == season.totals["passing.interceptions"]
    assert season.totals["fumbles.fumblesLost"] == "-"
    # Skattebo (RB, no passing category): INT thrown is missing, TO is fumbles lost.
    rb = ps.parse_gamelog(load("player_gl_nfl_adams_2025"), NFL, TZ)
    adams = _season(rb, 2025, "regular")
    assert adams.totals["passing.interceptions"] == "-"
    assert adams.totals["derived.TO"] == adams.totals["fumbles.fumblesLost"]
    # Byard (defender): his interceptions are defensive and never counted into TO.
    defender = ps.parse_gamelog(load("player_gl_nfl_byard_2025"), NFL, TZ)
    byard = _season(defender, 2025, "regular")
    assert byard.totals["interceptions.interceptions"] == "7"
    assert byard.totals["derived.TO"] == byard.totals["fumbles.fumblesLost"]


def test_turnovers_absent_when_no_component_category_present() -> None:
    raw = {
        "names": ["rushingAttempts"],
        "labels": ["CAR"],
        "categories": [{"name": "rushing", "count": 1}],
        "events": {"1": {"id": "1", "gameDate": "2025-09-07T17:00Z", "week": 1}},
        "seasonTypes": [
            {
                "displayName": "2025 Regular Season",
                "categories": [{"type": "event", "events": [{"eventId": "1", "stats": ["3"]}]}],
            }
        ],
    }
    parsed = ps.parse_gamelog(raw, NFL, TZ)
    assert parsed.seasons[0].games[0].stats.get("derived.TO") is None
    assert parsed.seasons[0].totals["derived.TO"] == "-"


def test_nba_gamelog_flattens_month_blocks_and_ignores_preseason() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nba_lebron_2025"), NBA, TZ)
    regular = _season(parsed, 2025, "regular")
    post = _season(parsed, 2025, "post")
    assert regular.game_count == 70
    assert post.game_count == 5
    assert len(parsed.seasons) == 2  # the 3 preseason games are not listed
    assert "derived.TO" not in regular.games[0].stats  # TO is football-only (S3)
    assert regular.totals["fieldGoalsMade-fieldGoalsAttempted"].count("-") == 1


def test_mlb_rates_recomputed_and_season_to_date_hidden() -> None:
    parsed = ps.parse_gamelog(load("player_gl_mlb_judge_2025"), MLB, TZ)
    regular = _season(parsed, 2025, "regular")
    avg_spec = next(s for s in parsed.specs if s.label == "AVG")
    obp_spec = next(s for s in parsed.specs if s.label == "OBP")
    assert avg_spec.season_to_date and obp_spec.season_to_date
    assert obp_spec.reducer == "none"
    hits = sum(float(g.stats["hits"]) for g in regular.games)
    at_bats = sum(float(g.stats["atBats"]) for g in regular.games)
    assert regular.totals["avg"] == f"{hits / at_bats:.3f}"
    assert regular.totals["onBasePct"] == "-"
    assert regular.totals["OPS"] == "-"


def test_gamelog_empty_season_is_not_an_error() -> None:
    parsed = ps.parse_gamelog(load("player_gl_nfl_skattebo_2015"), NFL, TZ)
    assert parsed.seasons == []
    assert parsed.specs == ps._FOOTBALL


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(seasonTypes="nope"),
        lambda d: d.update(events="nope"),
        lambda d: d["events"].pop(next(iter(d["events"]))),
        lambda d: d["seasonTypes"][0]["categories"][0]["events"][0].update(stats=["1"]),
        lambda d: d["seasonTypes"][0].pop("displayName"),
        lambda d: d["seasonTypes"][0]["categories"][0]["events"].append({"stats": []}),
    ],
)
def test_gamelog_malformed_payloads_raise_upstream_error(mutate: Any) -> None:
    raw = load("player_gl_nfl_skattebo_2025")
    mutate(raw)
    with pytest.raises(UpstreamError):
        ps.parse_gamelog(raw, NFL, TZ)


def test_gamelog_season_year_from_nba_block_name() -> None:
    assert ps._season_year("2024-25 Regular Season") == 2025
    assert ps._season_year("2025 Postseason") == 2025
    assert ps._season_kind("2025 Preseason") is None
    with pytest.raises(UpstreamError):
        ps._season_year("regular")


def test_gamelog_rejects_non_list_block_names() -> None:
    raw = load("player_gl_nfl_skattebo_2025")
    raw["seasonTypes"][0]["displayName"] = None
    with pytest.raises(UpstreamError):
        ps.parse_gamelog(raw, NFL, TZ)


# --------------------------------------------------------------------------- stats


def test_stats_skattebo_rows_and_espn_career_totals_unmodified() -> None:
    cats = ps.parse_stats(load("player_stats_nfl_skattebo"), "regular")
    rushing = next(c for c in cats if c.name == "rushing")
    assert [r.season for r in rushing.rows] == [2025, 2026]
    assert rushing.rows[0].team == "New York Giants"
    assert rushing.career is not None
    assert rushing.career["rushingAttempts"] == "182"  # ESPN's own total, not recomputed
    assert rushing.career["rushingYards"] == "704"


def test_stats_regular_drops_postseason_categories_and_post_keeps_them() -> None:
    mlb_regular = ps.parse_stats(load("player_stats_mlb_ohtani"), "regular")
    assert all("postseason" not in c.label.casefold() for c in mlb_regular)
    assert any(c.name == "career-batting" for c in mlb_regular)
    mlb_post = ps.parse_stats(
        {
            "categories": [
                {
                    "name": "postseason-batting",
                    "displayName": "Postseason Batting",
                    "names": ["hits"],
                    "labels": ["H"],
                    "statistics": [],
                    "totals": [],
                }
            ]
        },
        "post",
    )
    assert [c.name for c in mlb_post] == ["postseason-batting"]


def test_stats_postseason_rows_and_comma_totals_kept_as_espn_wrote_them() -> None:
    post = ps.parse_stats(load("player_stats_nfl_darnold_post"), "post")
    passing = next(c for c in post if c.name == "passing")
    assert [r.season for r in passing.rows] == [2024, 2025]
    regular = ps.parse_stats(load("player_stats_nfl_darnold"), "regular")
    passing_regular = next(c for c in regular if c.name == "passing")
    assert passing_regular.career is not None
    assert passing_regular.career["passingYards"] == "21,464"


def test_stats_traded_season_has_a_combined_row() -> None:
    cats = ps.parse_stats(load("player_stats_nfl_adams"), "regular")
    receiving = next(c for c in cats if c.name == "receiving")
    teams_2024 = [r.team for r in receiving.rows if r.season == 2024]
    assert "all teams (combined)" in teams_2024
    assert len(teams_2024) >= 2


def test_stats_nba_and_empty_and_malformed() -> None:
    cats = ps.parse_stats(load("player_stats_nba_lebron"), "regular")
    assert {c.name for c in cats} == {"averages", "totals", "miscellaneous"}
    assert ps.parse_stats({}, "regular") == []
    assert ps.parse_stats({"categories": []}, "regular") == []
    with pytest.raises(UpstreamError):
        ps.parse_stats({"categories": "x"}, "regular")
    broken = {"categories": [{"name": "a", "displayName": "A", "names": ["x"], "labels": []}]}
    with pytest.raises(UpstreamError):
        ps.parse_stats(broken, "regular")
    no_year = {
        "categories": [
            {
                "name": "a",
                "displayName": "A",
                "names": ["x"],
                "labels": ["X"],
                "statistics": [{"season": {}, "stats": ["1"]}],
            }
        ]
    }
    with pytest.raises(UpstreamError):
        ps.parse_stats(no_year, "regular")
