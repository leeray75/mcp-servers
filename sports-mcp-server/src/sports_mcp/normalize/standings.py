"""Standings normalisation with a per-sport stat allow-list and stable keys."""

from __future__ import annotations

import re
from typing import Any, Final

from sports_mcp.leagues import Family
from sports_mcp.models import StandingsEntry, StandingsGroup
from sports_mcp.normalize.common import as_dict, as_int, as_list, text

# ESPN stat name -> stable output key. Names not listed here are dropped.
_NAMES: Final[dict[str, str]] = {
    "gamesPlayed": "played",
    "wins": "wins",
    "losses": "losses",
    "ties": "ties",
    "otLosses": "otl",
    "winPercent": "pct",
    "points": "points",
    "pointsFor": "points_for",
    "pointsAgainst": "points_against",
    "pointDifferential": "diff",
    "streak": "streak",
    "gamesBehind": "games_behind",
}

_ORDER: Final[dict[str, tuple[str, ...]]] = {
    "football": ("wins", "losses", "ties", "pct", "points_for", "points_against", "diff", "streak"),
    "basketball": (
        "wins",
        "losses",
        "pct",
        "games_behind",
        "points_for",
        "points_against",
        "diff",
        "streak",
    ),
    "baseball": (
        "wins",
        "losses",
        "pct",
        "games_behind",
        "points_for",
        "points_against",
        "diff",
        "streak",
    ),
    "hockey": (
        "played",
        "wins",
        "losses",
        "otl",
        "points",
        "points_for",
        "points_against",
        "diff",
        "streak",
    ),
    "soccer": (
        "played",
        "wins",
        "draws",
        "losses",
        "goals_for",
        "goals_against",
        "goal_diff",
        "points",
    ),
}
_SOCCER_RENAME: Final[dict[str, str]] = {
    "ties": "draws",
    "points_for": "goals_for",
    "points_against": "goals_against",
    "diff": "goal_diff",
}


def _entry(
    raw: dict[str, Any], family: Family, conference: bool = False
) -> tuple[StandingsEntry, int] | None:
    """(entry, sort key). The sort key is the table rank, else ESPN's playoff seed."""
    team = as_dict(raw.get("team"))
    name = text(team.get("displayName")) or text(team.get("name")) or text(team.get("location"))
    if not name:
        return None
    seen: dict[str, str] = {}
    rank: int | None = None
    seed: int | None = None
    record: str | None = None
    conf: str | None = None
    for stat in as_list(raw.get("stats")):
        stat = as_dict(stat)
        stat_name = text(stat.get("name"))
        shown = text(stat.get("displayValue"))
        if stat_name is None:
            continue
        if stat_name == "rank" and rank is None:
            rank = as_int(stat.get("value"))
        elif stat_name == "playoffSeed" and seed is None:
            seed = as_int(stat.get("value"))
        elif stat_name == "overall" and record is None:
            record = text(stat.get("summary")) or shown
        elif stat_name == "vs. Conf." and conf is None:
            conf = text(stat.get("summary")) or shown
        key = _NAMES.get(stat_name)
        if key and shown is not None:
            if family == "soccer":
                key = _SOCCER_RENAME.get(key, key)
            seen.setdefault(key, shown)
    stats = {k: seen[k] for k in _ORDER[family] if k in seen}
    if conference and conf:
        stats = {"conf": conf, **stats}
    if "losses" not in seen and record:
        stats = {"record": record, **stats}
    entry = StandingsEntry(team_abbr=text(team.get("abbreviation")), team=name, stats=stats)
    return entry, rank or seed or 1_000_000


def _leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = [c for c in as_list(node.get("children")) if isinstance(c, dict)]
    if children:
        return [leaf for child in children for leaf in _leaves(child)]
    return [node] if as_dict(node.get("standings")).get("entries") else []


def normalize_standings(
    raw: dict[str, Any], family: Family, conference: bool = False
) -> list[StandingsGroup]:
    """Flatten ESPN's group tree into leaf groups (conference/division/table).

    Rows are ordered by ESPN's rank (soccer) or playoff seed and numbered 1..n within the
    group. The sort is stable, so ties keep ESPN's order.
    """
    groups: list[StandingsGroup] = []
    for leaf in _leaves(raw):
        scored = [
            pair
            for pair in (
                _entry(x, family, conference)
                for x in as_list(as_dict(leaf.get("standings")).get("entries"))
            )
            if pair is not None
        ]
        if not scored:
            continue
        scored.sort(key=lambda pair: pair[1])
        entries = [e.model_copy(update={"rank": i}) for i, (e, _) in enumerate(scored, start=1)]
        name = text(leaf.get("name")) or text(leaf.get("abbreviation")) or "Standings"
        groups.append(
            StandingsGroup(name=name, abbr=text(leaf.get("abbreviation")), entries=entries)
        )
    return groups


def _first_leaf_value(raw: dict[str, Any], key: str) -> Any:
    for leaf in _leaves(raw):
        value = as_dict(leaf.get("standings")).get(key)
        if value is not None:
            return value
    return None


def standings_season(raw: dict[str, Any]) -> int | None:
    """Season of the *table*: each leaf carries it; ESPN's top level can already be next year."""
    from_leaf = as_int(_first_leaf_value(raw, "season"))
    if from_leaf is not None:
        return from_leaf
    season = raw.get("season")
    if isinstance(season, dict):
        return as_int(season.get("year"))
    return as_int(season)


def standings_season_name(raw: dict[str, Any]) -> str | None:
    """Display name such as ``2026-27`` when ESPN gives a short one (else ``None``)."""
    name = text(_first_leaf_value(raw, "seasonDisplayName"))
    return name if name and re.fullmatch(r"\d{4}(-\d{2,4})?", name) else None
