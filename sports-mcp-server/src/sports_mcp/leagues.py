"""League registry: the single source of truth mapping a stable id to ESPN sport/league slugs.

Every ESPN URL path segment for a sport or league comes from here, never from agent input.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from sports_mcp.errors import InvalidInputError

Kind = Literal["team", "individual"]
Family = Literal["football", "basketball", "baseball", "hockey", "soccer"]


@dataclass(frozen=True, slots=True)
class League:
    """One supported competition."""

    id: str
    name: str
    sport: str  # ESPN sport path segment, e.g. "football"
    slug: str  # ESPN league path segment, e.g. "nfl" or "eng.1"
    family: Family  # which sport adapter normalises it
    aliases: tuple[str, ...] = ()
    kind: Kind = "team"
    # Football leagues accept ?week= and ?seasontype= on the scoreboard.
    has_weeks: bool = False
    # ESPN standings "level": 3 returns divisions for leagues that have them.
    standings_level: int | None = None
    # ESPN's default college-basketball board shows only featured games; groups=50 is all of D1.
    scoreboard_groups: int | None = None

    @property
    def path(self) -> str:
        return f"{self.sport}/{self.slug}"


# Order is the order shown by list_leagues.
LEAGUES: Final[tuple[League, ...]] = (
    League(
        "nfl",
        "NFL",
        "football",
        "nfl",
        "football",
        ("national-football-league",),
        has_weeks=True,
        standings_level=3,
    ),
    League(
        "college-football",
        "NCAA Football (FBS)",
        "football",
        "college-football",
        "football",
        ("ncaaf", "cfb"),
        has_weeks=True,
    ),
    League("nba", "NBA", "basketball", "nba", "basketball", ("national-basketball-association",)),
    League("wnba", "WNBA", "basketball", "wnba", "basketball"),
    League(
        "mens-college-basketball",
        "NCAA Men's Basketball",
        "basketball",
        "mens-college-basketball",
        "basketball",
        ("ncaam", "ncaab", "cbb"),
        scoreboard_groups=50,
    ),
    League(
        "womens-college-basketball",
        "NCAA Women's Basketball",
        "basketball",
        "womens-college-basketball",
        "basketball",
        ("ncaaw",),
        scoreboard_groups=50,
    ),
    League(
        "mlb", "MLB", "baseball", "mlb", "baseball", ("major-league-baseball",), standings_level=3
    ),
    League("nhl", "NHL", "hockey", "nhl", "hockey", ("national-hockey-league",)),
    League(
        "epl",
        "English Premier League",
        "soccer",
        "eng.1",
        "soccer",
        ("premier-league", "eng.1", "english-premier-league", "pl"),
    ),
    League("la-liga", "Spanish La Liga", "soccer", "esp.1", "soccer", ("laliga", "esp.1")),
    League("bundesliga", "German Bundesliga", "soccer", "ger.1", "soccer", ("ger.1",)),
    League("serie-a", "Italian Serie A", "soccer", "ita.1", "soccer", ("seriea", "ita.1")),
    League("ligue-1", "French Ligue 1", "soccer", "fra.1", "soccer", ("ligue1", "fra.1")),
    League(
        "mls", "Major League Soccer", "soccer", "usa.1", "soccer", ("major-league-soccer", "usa.1")
    ),
    League(
        "champions-league",
        "UEFA Champions League",
        "soccer",
        "uefa.champions",
        "soccer",
        ("ucl", "uefa.champions", "champions"),
    ),
)

_BY_KEY: Final[dict[str, League]] = {}
for _league in LEAGUES:
    for _key in (_league.id, *_league.aliases):
        _BY_KEY[_key.lower()] = _league

_LEAGUE_INPUT: Final = re.compile(r"^[a-z0-9][a-z0-9 ._'-]{0,48}$")


def league_ids() -> list[str]:
    return [league.id for league in LEAGUES]


def resolve_league(value: str) -> League:
    """Resolve a league id or alias (case-insensitive) or raise with the list of valid ids."""
    key = (
        value.strip().lower().replace(" ", "-").replace("_", "-") if isinstance(value, str) else ""
    )
    league = _BY_KEY.get(key) if _LEAGUE_INPUT.fullmatch(key) else None
    if league is None:
        raise InvalidInputError(
            "Unknown league. Valid league ids: " + ", ".join(league_ids()) + " (call list_leagues "
            "for aliases)."
        )
    return league


def fold(text: str) -> str:
    """Lower-case and strip diacritics, for fuzzy team matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
