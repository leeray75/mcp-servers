from __future__ import annotations

import pytest

from sports_mcp.errors import InvalidInputError
from sports_mcp.leagues import LEAGUES, fold, league_ids, resolve_league

REQUIRED = {
    "nfl", "college-football", "nba", "wnba", "mens-college-basketball",
    "womens-college-basketball", "mlb", "nhl", "epl", "la-liga", "bundesliga", "serie-a",
    "ligue-1", "mls", "champions-league",
}  # fmt: skip


def test_registry_has_every_required_league_and_no_individual_sports() -> None:
    assert set(league_ids()) == REQUIRED
    assert all(lg.kind == "team" for lg in LEAGUES)


def test_ids_and_aliases_are_unique() -> None:
    keys = [k.lower() for lg in LEAGUES for k in (lg.id, *lg.aliases)]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nfl", "nfl"),
        ("NFL", "nfl"),
        ("  Ncaaf ", "college-football"),
        ("premier-league", "epl"),
        ("Premier League", "epl"),
        ("EPL", "epl"),
        ("eng.1", "epl"),
        ("ncaam", "mens-college-basketball"),
        ("ncaaw", "womens-college-basketball"),
        ("UCL", "champions-league"),
        ("la_liga", "la-liga"),
    ],
)
def test_resolve_ids_and_aliases(text: str, expected: str) -> None:
    assert resolve_league(text).id == expected


@pytest.mark.parametrize(
    "bad", ["", "   ", "cricket", "../nfl", "nfl?x=1", "nfl\nx", "a" * 80, "football/nfl", "nfl;ls"]
)
def test_unknown_league_lists_valid_ids(bad: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        resolve_league(bad)
    message = str(info.value)
    for league_id in league_ids():
        assert league_id in message


def test_non_string_league_is_rejected() -> None:
    with pytest.raises(InvalidInputError):
        resolve_league(None)  # type: ignore[arg-type]


def test_paths_and_flags() -> None:
    nfl = resolve_league("nfl")
    assert nfl.path == "football/nfl"
    assert nfl.has_weeks and nfl.standings_level == 3
    assert resolve_league("epl").path == "soccer/eng.1"
    assert not resolve_league("nba").has_weeks


def test_fold_strips_diacritics_and_case() -> None:
    assert fold("Atlético MADRID") == "atletico madrid"
