"""Shared helpers that turn ESPN fragments into the compact models.

ESPN's JSON is unofficial and inconsistent between sports, so every accessor here is
defensive: a missing or oddly-typed field yields ``None`` instead of an exception.
"""

from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo

from sports_mcp.dates import local_iso, parse_espn_time, utc_iso
from sports_mcp.models import State, Status, TeamLine, Venue

_STATES: tuple[State, ...] = ("pre", "in", "post")
_INT = re.compile(r"^-?\d+$")


def get(data: Any, *path: str | int, default: Any = None) -> Any:
    """Safely walk nested dicts/lists."""
    current = data
    for key in path:
        if isinstance(current, dict) and isinstance(key, str):
            current = current.get(key)
        elif (
            isinstance(current, list)
            and isinstance(key, int)
            and -len(current) <= key < len(current)
        ):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and _INT.match(value.strip()):
        return int(value.strip())
    return None


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().lstrip("+")
        for prefix in ("o", "u", "O", "U"):
            if cleaned.startswith(prefix) and len(cleaned) > 1:
                cleaned = cleaned[1:]
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def text(value: Any) -> str | None:
    """A stripped non-empty string, else ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def times(value: Any, tz: ZoneInfo) -> tuple[str | None, str | None]:
    """(UTC ISO, local ISO) for an ESPN timestamp."""
    moment = parse_espn_time(value)
    if moment is None:
        return None, None
    return utc_iso(moment), local_iso(moment, tz)


def normalize_status(status: Any) -> Status:
    """ESPN ``status`` object -> :class:`Status` (state is always pre/in/post)."""
    kind = as_dict(get(status, "type"))
    raw_state = text(kind.get("state"))
    state: State = raw_state if raw_state in _STATES else "pre"
    clock = text(get(status, "displayClock")) if state == "in" else None
    return Status(
        state=state,
        detail=text(kind.get("detail"))
        or text(kind.get("shortDetail"))
        or text(kind.get("description")),
        period=as_int(get(status, "period")) if state != "pre" else None,
        clock=clock,
        completed=bool(kind.get("completed", False)),
        name=text(kind.get("name")),
    )


def normalize_venue(venue: Any) -> Venue | None:
    if not isinstance(venue, dict):
        return None
    name = text(venue.get("fullName")) or text(venue.get("name"))
    city = text(get(venue, "address", "city"))
    indoor = venue.get("indoor") if isinstance(venue.get("indoor"), bool) else None
    if name is None and city is None and indoor is None:
        return None
    return Venue(name=name, city=city, indoor=indoor)


def broadcast_names(*sources: Any) -> list[str] | None:
    """Unique broadcaster names from scoreboard (``names``) or summary (``media``) shapes."""
    seen: list[str] = []
    for source in sources:
        for item in as_list(source):
            if not isinstance(item, dict):
                continue
            candidates = [text(n) for n in as_list(item.get("names"))]
            candidates.append(text(get(item, "media", "shortName")))
            for name in candidates:
                if name and name not in seen:
                    seen.append(name)
    return seen or None


def record_summary(competitor: dict[str, Any]) -> str | None:
    """Overall record such as ``3-0`` from scoreboard (``records``) or schedule (``record``)."""
    records = as_list(competitor.get("records")) or as_list(competitor.get("record"))
    dicts = [r for r in records if isinstance(r, dict)]
    for rec in dicts:
        if rec.get("type") == "total" or rec.get("name") == "overall":
            return text(rec.get("summary")) or text(rec.get("displayValue"))
    for rec in dicts:
        value = text(rec.get("summary")) or text(rec.get("displayValue"))
        if value and "-" in value:
            return value
    return None


def score_value(raw: Any) -> int | None:
    """Scores arrive as ``"17"`` (scoreboard) or ``{"value": 17.0, "displayValue": "17"}``."""
    if isinstance(raw, dict):
        return (
            as_int(raw.get("displayValue")) if raw.get("displayValue") else as_int(raw.get("value"))
        )
    return as_int(raw)


def poll_rank(value: Any) -> int | None:
    """ESPN poll rank: an integer 1..25, else ``None`` (ESPN sends 99 or nothing when unranked)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 25 else None


def team_line(competitor: dict[str, Any]) -> TeamLine:
    team = as_dict(competitor.get("team"))
    winner = competitor.get("winner")
    lines = [text(as_dict(ls).get("displayValue")) for ls in as_list(competitor.get("linescores"))]
    # Scoreboard competitors carry curatedRank.current; game-summary header competitors carry rank.
    rank = poll_rank(get(competitor, "curatedRank", "current")) or poll_rank(competitor.get("rank"))
    return TeamLine(
        home_away=text(competitor.get("homeAway")),
        id=text(team.get("id")) or text(competitor.get("id")),
        abbr=text(team.get("abbreviation")),
        name=text(team.get("displayName")) or text(team.get("name")),
        score=score_value(competitor.get("score")),
        rank=rank,
        record=record_summary(competitor),
        winner=winner if isinstance(winner, bool) else None,
        linescores=[x for x in lines if x is not None] or None,
        hits=as_int(competitor.get("hits")),
        errors=as_int(competitor.get("errors")),
    )


def ordered_teams(competitors: Any) -> list[dict[str, Any]]:
    """Competitors as dicts, away team first (the way scores are read: ``AWAY @ HOME``)."""
    items = [c for c in as_list(competitors) if isinstance(c, dict)]
    return sorted(items, key=lambda c: 0 if c.get("homeAway") == "away" else 1)
