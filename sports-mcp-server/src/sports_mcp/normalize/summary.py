"""Game-summary normalisation (ESPN ``/summary?event=``)."""

from __future__ import annotations

from typing import Any, Final
from zoneinfo import ZoneInfo

from sports_mcp.leagues import League
from sports_mcp.models import (
    Game,
    Injury,
    Leader,
    OddsLine,
    ScoringPlay,
    WinProbability,
)
from sports_mcp.normalize.common import (
    as_dict,
    as_float,
    as_int,
    as_list,
    broadcast_names,
    get,
    text,
)
from sports_mcp.normalize.odds import first_odds
from sports_mcp.normalize.scoreboard import normalize_event

MAX_SCORING_PLAYS: Final = 40
MAX_INJURIES_PER_TEAM: Final = 8
MAX_LEADERS: Final = 12


def summary_game(raw: dict[str, Any], league: League, tz: ZoneInfo) -> Game | None:
    """Build the :class:`Game` header from a summary's ``header`` + ``gameInfo``."""
    header = as_dict(raw.get("header"))
    comp = as_dict(get(header, "competitions", 0))
    if not comp:
        return None
    competitors = [c for c in as_list(comp.get("competitors")) if isinstance(c, dict)]
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)

    def label(c: dict[str, Any] | None, key: str) -> str:
        return text(as_dict(as_dict(c).get("team")).get(key)) or "?"

    merged = dict(comp)
    merged["venue"] = as_dict(get(raw, "gameInfo", "venue")) or comp.get("venue")
    merged["broadcasts"] = [
        {"names": broadcast_names(comp.get("broadcasts"), raw.get("broadcasts")) or []}
    ]
    event = {
        "id": header.get("id") or comp.get("id"),
        "date": comp.get("date"),
        "name": f"{label(away, 'displayName')} at {label(home, 'displayName')}",
        "shortName": f"{label(away, 'abbreviation')} @ {label(home, 'abbreviation')}",
        "status": comp.get("status"),
        "competitions": [merged],
    }
    return normalize_event(event, league, tz)


def _team_ids(game: Game) -> dict[str, str]:
    return {t.id: t.abbr for t in game.teams if t.id and t.abbr}


def scoring_plays(raw: dict[str, Any], league: League, game: Game) -> list[ScoringPlay] | None:
    """Scoring plays oldest first, capped. Basketball scores every basket, so it is skipped."""
    if league.family == "basketball":
        return None
    ids = _team_ids(game)
    plays = [p for p in as_list(raw.get("scoringPlays")) if isinstance(p, dict)]
    if not plays and league.family in {"baseball", "hockey"}:
        plays = [
            p for p in as_list(raw.get("plays")) if isinstance(p, dict) and p.get("scoringPlay")
        ]
    out: list[ScoringPlay] = []
    for play in plays:
        body = text(play.get("text")) or text(get(play, "type", "text"))
        if not body:
            continue
        team = as_dict(play.get("team"))
        abbr = text(team.get("abbreviation")) or ids.get(text(team.get("id")) or "")
        period = as_dict(play.get("period"))
        clock = text(get(play, "clock", "displayValue"))
        if league.family == "baseball":
            clock = text(period.get("type")) or clock
        out.append(
            ScoringPlay(
                period=as_int(period.get("number")),
                clock=clock,
                team_abbr=abbr,
                text=body,
                away_score=as_int(play.get("awayScore")),
                home_score=as_int(play.get("homeScore")),
            )
        )
    if not out and league.family == "soccer":
        out = _soccer_goals(raw, game)
    return out[:MAX_SCORING_PLAYS] or None


def _soccer_goals(raw: dict[str, Any], game: Game) -> list[ScoringPlay]:
    """Goals from ``header.competitions[0].details`` with a running score."""
    ids = _team_ids(game)
    home_id = next((t.id for t in game.teams if t.home_away == "home"), None)
    home_score = away_score = 0
    out: list[ScoringPlay] = []
    for item in as_list(get(raw, "header", "competitions", 0, "details")):
        item = as_dict(item)
        if item.get("scoringPlay") is not True:
            continue
        team_id = text(as_dict(item.get("team")).get("id"))
        if team_id is not None and team_id == home_id:
            home_score += 1
        else:
            away_score += 1
        who = text(get(item, "participants", 0, "athlete", "displayName")) or "Goal"
        tag = " (pen)" if item.get("penaltyKick") else " (own goal)" if item.get("ownGoal") else ""
        out.append(
            ScoringPlay(
                clock=text(get(item, "clock", "displayValue")),
                team_abbr=ids.get(team_id or ""),
                text=f"Goal: {who}{tag}",
                away_score=away_score,
                home_score=home_score,
            )
        )
    return out


def leaders(raw: dict[str, Any]) -> list[Leader] | None:
    out: list[Leader] = []
    for block in as_list(raw.get("leaders")):
        block = as_dict(block)
        abbr = text(get(block, "team", "abbreviation"))
        for category in as_list(block.get("leaders")):
            category = as_dict(category)
            top = as_dict(get(category, "leaders", 0))
            player = text(get(top, "athlete", "displayName")) or text(
                get(top, "athlete", "shortName")
            )
            name = text(category.get("displayName")) or text(category.get("name"))
            if player and name:
                out.append(
                    Leader(
                        team_abbr=abbr,
                        category=name,
                        player=player,
                        value=text(top.get("displayValue")),
                    )
                )
    return out[:MAX_LEADERS] or None


def injuries(raw: dict[str, Any]) -> dict[str, list[Injury]] | None:
    out: dict[str, list[Injury]] = {}
    for block in as_list(raw.get("injuries")):
        block = as_dict(block)
        abbr = text(get(block, "team", "abbreviation"))
        if not abbr:
            continue
        items: list[Injury] = []
        for entry in as_list(block.get("injuries")):
            entry = as_dict(entry)
            athlete = as_dict(entry.get("athlete"))
            name = text(athlete.get("displayName")) or text(athlete.get("shortName"))
            if name:
                items.append(
                    Injury(
                        player=name,
                        position=text(get(athlete, "position", "abbreviation")),
                        status=text(entry.get("status")) or text(get(entry, "type", "description")),
                    )
                )
        if items:
            out[abbr] = items[:MAX_INJURIES_PER_TEAM]
    return out or None


def win_probability(raw: dict[str, Any]) -> WinProbability | None:
    entries = as_list(raw.get("winprobability"))
    if not entries:
        return None
    last = as_dict(entries[-1])
    home = as_float(last.get("homeWinPercentage"))
    if home is None:
        return None
    tie = as_float(last.get("tiePercentage")) or 0.0
    return WinProbability(home_pct=round(home * 100, 1), away_pct=round((1 - home - tie) * 100, 1))


def weather(raw: dict[str, Any]) -> dict[str, Any] | None:
    block = as_dict(get(raw, "gameInfo", "weather"))
    result = {
        "condition": text(block.get("displayValue")),
        "temperature_f": as_int(block.get("temperature")),
        "wind_gust": as_int(block.get("gust")),
        "precip_pct": as_int(block.get("precipitation")),
    }
    return {k: v for k, v in result.items() if v is not None} or None


def attendance(raw: dict[str, Any]) -> int | None:
    value = as_int(get(raw, "gameInfo", "attendance"))
    return value if value else None


def summary_odds(raw: dict[str, Any], game: Game) -> OddsLine | None:
    """Lines from ``pickcenter`` (fallback ``odds``), including open/close when present."""
    home = next((t.abbr for t in game.teams if t.home_away == "home"), None)
    away = next((t.abbr for t in game.teams if t.home_away == "away"), None)
    return first_odds(raw.get("pickcenter"), home, away) or first_odds(raw.get("odds"), home, away)
