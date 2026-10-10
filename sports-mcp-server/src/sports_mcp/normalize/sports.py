"""Per-sport adapters: live ``situation`` and the team-stat allow-lists."""

from __future__ import annotations

from typing import Any, Final

from sports_mcp.leagues import Family
from sports_mcp.models import TeamStat
from sports_mcp.normalize.common import as_dict, as_int, as_list, get, text


def _abbr_for(team_id: Any, competitors: list[dict[str, Any]]) -> str | None:
    wanted = text(team_id)
    for comp in competitors:
        team = as_dict(comp.get("team"))
        if wanted is not None and text(team.get("id")) == wanted:
            return text(team.get("abbreviation"))
    return None


def _athlete_name(entry: Any) -> str | None:
    athlete = as_dict(as_dict(entry).get("athlete"))
    return text(athlete.get("displayName")) or text(athlete.get("shortName"))


def situation_for(
    family: Family, sit: dict[str, Any], competitors: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Short live-only situation dict for a game in progress, or ``None``."""
    result: dict[str, Any] = {}
    if family == "football":
        result = {
            "down_distance": text(sit.get("downDistanceText"))
            or text(sit.get("shortDownDistanceText")),
            "possession": _abbr_for(sit.get("possession"), competitors),
            "red_zone": True if sit.get("isRedZone") is True else None,
            "home_timeouts": as_int(sit.get("homeTimeouts")),
            "away_timeouts": as_int(sit.get("awayTimeouts")),
        }
    elif family == "baseball":
        bases = [
            label
            for key, label in (("onFirst", "1B"), ("onSecond", "2B"), ("onThird", "3B"))
            if sit.get(key)
        ]
        result = {
            "outs": as_int(sit.get("outs")),
            "balls": as_int(sit.get("balls")),
            "strikes": as_int(sit.get("strikes")),
            "bases": bases or None,
            "batter": _athlete_name(sit.get("batter")),
            "pitcher": _athlete_name(sit.get("pitcher")),
        }
    elif family == "hockey":
        strength = text(sit.get("strength")) or text(get(sit, "lastPlay", "strength", "text"))
        if strength and strength.lower() not in {"even strength", "even-strength"}:
            result = {"strength": strength}
    return {k: v for k, v in result.items() if v is not None} or None


# (ESPN stat name, label); first occurrence of a name wins. Order = relevance.
_TEAM_STATS: Final[dict[str, list[tuple[str, str]]]] = {
    "football": [
        ("firstDowns", "1st downs"),
        ("totalYards", "Total yds"),
        ("netPassingYards", "Pass yds"),
        ("rushingYards", "Rush yds"),
        ("yardsPerPlay", "Yds/play"),
        ("completionAttempts", "Comp/Att"),
        ("rushingAttempts", "Rush att"),
        ("thirdDownEff", "3rd down"),
        ("fourthDownEff", "4th down"),
        ("redZoneAttempts", "Red zone"),
        ("sacksYardsLost", "Sacks-yds lost"),
        ("turnovers", "Turnovers"),
        ("totalPenaltiesYards", "Penalties-yds"),
        ("possessionTime", "Possession"),
        ("totalDrives", "Drives"),
    ],
    "basketball": [
        ("fieldGoalsMade-fieldGoalsAttempted", "FG"),
        ("fieldGoalPct", "FG%"),
        ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "3PT"),
        ("threePointFieldGoalPct", "3P%"),
        ("freeThrowsMade-freeThrowsAttempted", "FT"),
        ("freeThrowPct", "FT%"),
        ("totalRebounds", "Rebounds"),
        ("offensiveRebounds", "Off reb"),
        ("assists", "Assists"),
        ("steals", "Steals"),
        ("blocks", "Blocks"),
        ("totalTurnovers", "Turnovers"),
        ("pointsInPaint", "Paint pts"),
        ("fastBreakPoints", "Fast-break pts"),
        ("fouls", "Fouls"),
    ],
    "hockey": [
        ("shotsTotal", "Shots"),
        ("powerPlayGoals", "PP goals"),
        ("powerPlayOpportunities", "PP opps"),
        ("hits", "Hits"),
        ("blockedShots", "Blocked shots"),
        ("takeaways", "Takeaways"),
        ("giveaways", "Giveaways"),
        ("faceoffPercent", "Faceoff %"),
        ("penaltyMinutes", "PIM"),
        ("shortHandedGoals", "SH goals"),
    ],
    "soccer": [
        ("possessionPct", "Possession %"),
        ("totalShots", "Shots"),
        ("shotsOnTarget", "On target"),
        ("wonCorners", "Corners"),
        ("saves", "Saves"),
        ("foulsCommitted", "Fouls"),
        ("yellowCards", "Yellow cards"),
        ("redCards", "Red cards"),
        ("offsides", "Offsides"),
        ("passAccuracy", "Pass accuracy"),
        ("totalTackles", "Tackles"),
        ("totalClearance", "Clearances"),
    ],
    # Baseball nests team stats in groups: key is "<group>.<stat>".
    "baseball": [
        ("batting.runs", "Runs"),
        ("batting.hits", "Hits"),
        ("fielding.errors", "Errors"),
        ("batting.homeRuns", "Home runs"),
        ("batting.RBIs", "RBI"),
        ("batting.walks", "Walks"),
        ("batting.strikeouts", "Strikeouts (bat)"),
        ("batting.runnersLeftOnBase", "LOB"),
        ("batting.atBats", "At bats"),
        ("batting.avg", "Team AVG"),
        ("pitching.strikeouts", "Strikeouts (pitch)"),
        ("pitching.walks", "Walks (pitch)"),
        ("pitching.earnedRuns", "Earned runs"),
        ("pitching.pitches", "Pitches"),
    ],
}


def _stat_map(team_block: dict[str, Any], family: Family) -> dict[str, str]:
    values: dict[str, str] = {}
    for stat in as_list(team_block.get("statistics")):
        stat = as_dict(stat)
        if family == "baseball":
            group = text(stat.get("type")) or text(stat.get("name"))
            for inner in as_list(stat.get("stats")):
                inner = as_dict(inner)
                name, shown = text(inner.get("name")), text(inner.get("displayValue"))
                if group and name and shown is not None:
                    values.setdefault(f"{group}.{name}", shown)
        else:
            name, shown = text(stat.get("name")), text(stat.get("displayValue"))
            if name and shown is not None:
                values.setdefault(name, shown)
    if family == "soccer":  # ESPN's passPct is a rounded fraction (0.8); derive a real percentage
        done, total = values.get("accuratePasses"), values.get("totalPasses")
        if done and total and done.isdigit() and total.isdigit() and int(total) > 0:
            values["passAccuracy"] = f"{round(100 * int(done) / int(total))}%"
    return values


def team_stats(family: Family, boxscore_teams: Any, cap: int = 15) -> list[TeamStat] | None:
    """Allow-listed team stats as ``label / away / home`` rows (most relevant first)."""
    teams = [t for t in as_list(boxscore_teams) if isinstance(t, dict)]
    by_side: dict[str, dict[str, str]] = {}
    for team in teams:
        side = text(team.get("homeAway"))
        if side in {"home", "away"}:
            by_side[side] = _stat_map(team, family)
    if len(by_side) < len(teams) or not teams:
        # homeAway missing: fall back to document order (ESPN lists away first).
        by_side = {}
        for side, team in zip(("away", "home"), teams, strict=False):
            by_side[side] = _stat_map(team, family)
    rows: list[TeamStat] = []
    for key, label in _TEAM_STATS[family]:
        away, home = by_side.get("away", {}).get(key), by_side.get("home", {}).get(key)
        if away is None and home is None:
            continue
        rows.append(TeamStat(label=label, away=away, home=home))
        if len(rows) >= cap:
            break
    return rows or None
