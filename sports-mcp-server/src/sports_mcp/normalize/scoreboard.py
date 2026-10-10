"""Scoreboard (and schedule-event) normalisation, shared by every team sport."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from sports_mcp.leagues import League
from sports_mcp.models import Game, SeasonInfo
from sports_mcp.normalize.common import (
    as_dict,
    as_int,
    as_list,
    broadcast_names,
    get,
    normalize_status,
    normalize_venue,
    ordered_teams,
    team_line,
    text,
    times,
)
from sports_mcp.normalize.odds import first_odds
from sports_mcp.normalize.sports import situation_for

SEASON_TYPES = {1: "Preseason", 2: "Regular Season", 3: "Postseason", 4: "Off Season"}


def normalize_event(
    event: dict[str, Any], league: League, tz: ZoneInfo, *, full_odds: bool = False
) -> Game | None:
    """One ESPN scoreboard event -> :class:`Game` (``None`` if it has no competition).

    ``full_odds`` keeps the opening/closing snapshots (dropped by default to keep the board small).
    """
    competition = as_dict(get(event, "competitions", 0))
    if not competition:
        return None
    competitors = ordered_teams(competition.get("competitors"))
    teams = [team_line(c) for c in competitors]
    home = next((t for t in teams if t.home_away == "home"), None)
    away = next((t for t in teams if t.home_away == "away"), None)
    status = normalize_status(event.get("status") or competition.get("status"))
    start_utc, start_local = times(event.get("date") or competition.get("date"), tz)
    tbd = event.get("timeValid") is False or competition.get("timeValid") is False
    if tbd:
        start_local = None
    if status.state == "post" and not status.completed:
        # Postponed/canceled games carry placeholder 0-0 scores.
        teams = [t.model_copy(update={"score": None, "winner": None}) for t in teams]

    if status.state == "pre":
        teams = [t.model_copy(update={"score": None, "winner": None}) for t in teams]

    odds = first_odds(
        competition.get("odds"), home.abbr if home else None, away.abbr if away else None
    )
    if odds is not None and not full_odds:
        odds = odds.model_copy(update={"open": None, "close": None})

    situation = None
    if status.state == "in":
        situation = situation_for(league.family, as_dict(competition.get("situation")), competitors)

    notes = [
        n
        for n in (text(as_dict(x).get("headline")) for x in as_list(competition.get("notes")))
        if n
    ]
    game_id = str(event.get("id") or competition.get("id") or "")
    if not game_id:
        return None
    return Game(
        id=game_id,
        time_tbd=True if tbd else None,
        name=text(event.get("name")),
        short_name=text(event.get("shortName")),
        start_utc=start_utc,
        start_local=start_local,
        status=status,
        venue=normalize_venue(competition.get("venue")),
        broadcasts=broadcast_names(competition.get("broadcasts")),
        teams=teams,
        odds=odds,
        situation=situation,
        notes=notes or None,
        week=as_int(get(event, "week", "number")),
    )


def season_info(raw: dict[str, Any]) -> SeasonInfo | None:
    """Season descriptor from a scoreboard response (``leagues[0].season`` or ``season``)."""
    league_season = as_dict(get(raw, "leagues", 0, "season"))
    top = as_dict(raw.get("season"))
    year = as_int(league_season.get("year")) or as_int(top.get("year"))
    type_name = text(get(league_season, "type", "name"))
    top_type = SEASON_TYPES.get(as_int(top.get("type")) or 0)
    if top_type:  # the type of the season actually returned (a week/season_type request differs)
        type_name = top_type
    name = text(league_season.get("displayName")) or type_name
    if year is None and name is None:
        return None
    return SeasonInfo(year=year, type=type_name, name=name)


def empty_day_hint(raw: dict[str, Any], league: League, date_label: str) -> str:
    """Note for a day with no games, with an offseason hint from the league calendar."""
    note = f"No {league.id} games on {date_label}."
    season = as_dict(get(raw, "leagues", 0, "season"))
    start = text(season.get("startDate"))
    end = text(season.get("endDate"))
    name = text(season.get("displayName"))
    if start and end:
        label = f"{name} season" if name else "season"
        note += f" The {label} runs {start[:10]} to {end[:10]} (check dates near its edges)."
    return note


def normalize_scoreboard(raw: dict[str, Any], league: League, tz: ZoneInfo) -> list[Game]:
    games: list[Game] = []
    for event in as_list(raw.get("events")):
        if isinstance(event, dict):
            game = normalize_event(event, league, tz)
            if game is not None:
                games.append(game)
    return games


def has_live_game(raw: dict[str, Any]) -> bool:
    """True when any event in a scoreboard-like response is in progress."""
    for event in as_list(raw.get("events")):
        if get(event, "status", "type", "state") == "in":
            return True
    return False
