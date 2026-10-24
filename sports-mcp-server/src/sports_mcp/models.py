"""Typed result models. ``dump`` produces the compact ``structuredContent`` form.

Fields ESPN leaves empty are ``None`` (omitted from the output) rather than null/empty
placeholders; lists that carry meaning even when empty (``games``, ``groups``...) stay.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

State = Literal["pre", "in", "post"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Meta(_Model):
    """Freshness metadata carried by every tool result."""

    source: str = "espn"
    as_of: str  # UTC ISO time of the upstream fetch (not of response building)
    cache_age_seconds: int
    cached: bool
    endpoint_kind: str
    stale: bool | None = None  # true when a live refresh failed and an older copy is served
    note: str | None = None


class Status(_Model):
    state: State
    detail: str | None = None
    period: int | None = None
    clock: str | None = None
    completed: bool = False
    name: str | None = None


class Venue(_Model):
    name: str | None = None
    city: str | None = None
    indoor: bool | None = None


class TeamLine(_Model):
    home_away: str | None = None
    id: str | None = None
    abbr: str | None = None
    name: str | None = None
    score: int | None = None
    record: str | None = None
    winner: bool | None = None
    linescores: list[str] | None = None
    hits: int | None = None  # baseball
    errors: int | None = None  # baseball


class LineSnapshot(_Model):
    """An opening or closing set of numbers. Spreads are from the home team's point of view."""

    home_spread: float | None = None
    over_under: float | None = None
    home_moneyline: int | None = None
    away_moneyline: int | None = None
    draw_moneyline: int | None = None


class OddsLine(_Model):
    """Betting line. ``spread`` is the favorite's handicap (negative = favorite gives points)."""

    provider: str | None = None
    details: str | None = None  # the provider's own one-line text, unmodified
    favorite_abbr: str | None = None
    spread: float | None = None
    over_under: float | None = None
    home_moneyline: int | None = None
    away_moneyline: int | None = None
    draw_moneyline: int | None = None
    open: LineSnapshot | None = None
    close: LineSnapshot | None = None


class Game(_Model):
    id: str
    name: str | None = None
    short_name: str | None = None
    start_utc: str | None = None
    start_local: str | None = None
    status: Status
    venue: Venue | None = None
    broadcasts: list[str] | None = None
    teams: list[TeamLine]
    odds: OddsLine | None = None
    situation: dict[str, Any] | None = None
    notes: list[str] | None = None
    week: int | None = None
    time_tbd: bool | None = None  # ESPN has no confirmed kickoff time yet


class SeasonInfo(_Model):
    year: int | None = None
    type: str | None = None
    name: str | None = None


class ScoreboardResult(_Model):
    league: str
    date: str
    season: SeasonInfo | None = None
    games: list[Game]
    count: int
    note: str | None = None
    meta: Meta


class ScoringPlay(_Model):
    period: int | None = None
    clock: str | None = None
    team_abbr: str | None = None
    text: str
    away_score: int | None = None
    home_score: int | None = None


class TeamStat(_Model):
    label: str
    away: str | None = None
    home: str | None = None


class Leader(_Model):
    team_abbr: str | None = None
    category: str
    player: str
    value: str | None = None


class Injury(_Model):
    player: str
    position: str | None = None
    status: str | None = None


class WinProbability(_Model):
    home_pct: float
    away_pct: float


class Performer(_Model):
    player: str
    position: str | None = None
    line: str
    stats: dict[str, str] | None = None


class BoxRow(_Model):
    player: str
    position: str | None = None
    stats: list[str]


class BoxGroup(_Model):
    group: str
    labels: list[str]
    rows: list[BoxRow]


class GameDetail(_Model):
    league: str
    detail: str
    game: Game
    scoring_plays: list[ScoringPlay] | None = None
    team_stats: list[TeamStat] | None = None
    leaders: list[Leader] | None = None
    injuries: dict[str, list[Injury]] | None = None
    odds: OddsLine | None = None
    odds_basis: str | None = None
    win_probability: WinProbability | None = None
    weather: dict[str, Any] | None = None
    attendance: int | None = None
    basis: Literal["game", "season"] | None = None  # what the player stats are
    performers: dict[str, list[Performer]] | None = None
    boxscore: dict[str, list[BoxGroup]] | None = None
    truncated: bool | None = None
    note: str | None = None
    meta: Meta


class OddsStatus(_Model):
    state: State
    detail: str | None = None


class OddsGame(_Model):
    id: str
    short_name: str | None = None
    status: OddsStatus
    start_utc: str | None = None
    lines: OddsLine | None = None
    lines_available: bool
    lines_basis: Literal["current", "closing", "unavailable"]


class OddsResult(_Model):
    league: str
    date: str | None = None
    games: list[OddsGame]
    count: int
    note: str
    meta: Meta


class StandingsEntry(_Model):
    rank: int | None = None
    team_abbr: str | None = None
    team: str
    stats: dict[str, str]


class StandingsGroup(_Model):
    name: str
    abbr: str | None = None
    entries: list[StandingsEntry]


class StandingsResult(_Model):
    league: str
    season: int | None = None
    season_name: str | None = None  # e.g. "2026-27" when the season spans two years
    groups: list[StandingsGroup]
    available_groups: list[str] | None = None
    truncated: bool | None = None
    note: str | None = None
    meta: Meta


class TeamMatch(_Model):
    id: str
    abbr: str | None = None
    name: str
    location: str | None = None
    record: str | None = None
    standing_summary: str | None = None


class FindTeamResult(_Model):
    league: str
    query: str
    matches: list[TeamMatch]
    note: str | None = None
    meta: Meta


class ScheduleGame(_Model):
    game_id: str
    start_utc: str | None = None
    start_local: str | None = None
    opponent_abbr: str | None = None
    home_away: str | None = None
    status: Status
    result: str | None = None  # "W 24-17" / "L 3-10" / "T 1-1"
    score: str | None = None  # "<team>-<opponent>"
    label: str | None = None  # week / round label when ESPN gives one
    time_tbd: bool | None = None


class ScheduleResult(_Model):
    league: str
    team: TeamMatch
    season: SeasonInfo | None = None
    games: list[ScheduleGame]
    count: int
    next_game: ScheduleGame | None = None
    last_game: ScheduleGame | None = None
    note: str | None = None
    meta: Meta


class NewsItem(_Model):
    headline: str
    published_utc: str | None = None
    description: str | None = None
    url: str | None = None


class NewsResult(_Model):
    league: str
    articles: list[NewsItem]
    count: int
    note: str | None = None
    meta: Meta


class LeagueInfo(_Model):
    id: str
    name: str
    sport: str
    kind: str
    aliases: list[str]


class LeaguesResult(_Model):
    leagues: list[LeagueInfo]
    count: int


MatchKind = Literal["id", "exact", "single", "fuzzy"]
SeasonKind = Literal["regular", "post"]


class PlayerRef(_Model):
    """A player as ESPN names him. ``match`` says how a lookup matched (absent in plain lists)."""

    id: str
    name: str | None = None
    team: str | None = None
    position: str | None = None
    league: str
    match: MatchKind | None = None


class PlayerSearchResult(_Model):
    league: str
    query: str
    players: list[PlayerRef]
    count: int
    note: str | None = None
    meta: Meta


class ColumnInfo(_Model):
    """One game-log column: its structured key, text label and group heading."""

    key: str
    label: str
    group: str | None = None
    season_to_date: bool | None = None  # per-game cell is season-to-date (hidden in text)


class GameLogRow(_Model):
    date: str | None = None  # local calendar date (YYYY-MM-DD)
    week: int | None = None
    round: str | None = None  # ESPN event note for playoff games
    home_away: Literal["home", "away"] | None = None
    opponent: str | None = None
    result: str | None = None  # W | L | T, from the player's team's point of view
    score: str | None = None  # player's team first, e.g. "27-34"
    stats: dict[str, str]


class SeasonLog(_Model):
    year: int
    season_type: SeasonKind
    label: str  # ESPN block name, e.g. "2025 Regular Season"
    game_count: int  # every game in the block, including any omitted rows
    games: list[GameLogRow]  # the rows listed (oldest rows may be omitted for the size budget)
    totals: dict[str, str]  # computed from every game in the block
    computed: bool = True
    games_omitted: int | None = None  # oldest rows not listed (R16 structured budget)


class CareerLog(_Model):
    season_type: SeasonKind
    seasons: list[int]
    game_count: int
    totals: dict[str, str]
    computed: bool = True


class PlayerGameLogResult(_Model):
    league: str
    resolved: bool
    player: PlayerRef | None = None
    candidates: list[PlayerRef] | None = None
    season: str
    season_type: str
    columns: list[ColumnInfo] | None = None
    seasons: list[SeasonLog] | None = None
    career: list[CareerLog] | None = None
    game_count: int = 0
    truncated: bool | None = None
    note: str | None = None
    meta: Meta


class PlayerStatColumn(_Model):
    name: str
    label: str


class PlayerStatRow(_Model):
    season: int
    season_label: str
    team: str | None = None
    position: str | None = None
    stats: dict[str, str]


class PlayerStatCategory(_Model):
    name: str
    label: str
    columns: list[PlayerStatColumn]
    rows: list[PlayerStatRow]
    career: dict[str, str] | None = None  # ESPN's own career totals row, unmodified


class PlayerStatBlock(_Model):
    season_type: SeasonKind
    label: str
    categories: list[PlayerStatCategory]


class PlayerStatsResult(_Model):
    league: str
    resolved: bool
    player: PlayerRef | None = None
    candidates: list[PlayerRef] | None = None
    season_type: str
    blocks: list[PlayerStatBlock] | None = None
    note: str | None = None
    meta: Meta


def prune(value: Any) -> Any:
    """Drop empty strings and empty dicts recursively (empty lists are kept: they carry meaning)."""
    if isinstance(value, dict):
        cleaned = {k: prune(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if v != "" and v != {}}
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


def dump(model: BaseModel) -> dict[str, Any]:
    """The compact JSON-able form used for ``structuredContent``."""
    result: dict[str, Any] = prune(model.model_dump(mode="json", exclude_none=True))
    return result
