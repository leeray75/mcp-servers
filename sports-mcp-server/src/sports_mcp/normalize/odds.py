"""Betting-line normalisation (ESPN scoreboard ``odds`` and summary ``pickcenter`` entries).

ESPN's own ``spread`` number is from the *home* team's point of view and its ``details``
string is a favourite's spread in football/basketball but a favourite's moneyline in
baseball, hockey and soccer. The compact form therefore derives the favourite from the
explicit ``favorite`` flags and states the spread as the favourite's handicap (negative).
"""

from __future__ import annotations

from typing import Any

from sports_mcp.models import LineSnapshot, OddsLine
from sports_mcp.normalize.common import as_dict, as_float, as_int, get, text


def parse_moneyline(value: Any) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().upper()
        if cleaned in {"EVEN", "EV", "PK"}:
            return 100
        return as_int(cleaned.lstrip("+"))
    return None


def _snapshot(raw: dict[str, Any], phase: str) -> LineSnapshot | None:
    snap = LineSnapshot(
        home_spread=as_float(get(raw, "pointSpread", "home", phase, "line")),
        over_under=as_float(get(raw, "total", "over", phase, "line")),
        home_moneyline=parse_moneyline(get(raw, "moneyline", "home", phase, "odds")),
        away_moneyline=parse_moneyline(get(raw, "moneyline", "away", phase, "odds")),
        draw_moneyline=parse_moneyline(get(raw, "moneyline", "draw", phase, "odds")),
    )
    return snap if snap.model_dump(exclude_none=True) else None


def _moneyline(raw: dict[str, Any], side: str) -> int | None:
    """Closing moneyline for ``side`` (home/away/draw), from the flat or the nested form."""
    flat = get(raw, f"{side}TeamOdds" if side != "draw" else "drawOdds", "moneyLine")
    if flat is not None:
        return parse_moneyline(flat)
    return parse_moneyline(get(raw, "moneyline", side, "close", "odds"))


def _favorite(
    home_spread: float | None,
    home_ml: int | None,
    away_ml: int | None,
    raw: dict[str, Any],
    home_abbr: str | None,
    away_abbr: str | None,
) -> str | None:
    """Spread side first, then the shorter moneyline, then ESPN's own flags."""
    if home_spread:
        return home_abbr if home_spread < 0 else away_abbr
    if home_ml is not None and away_ml is not None and home_ml != away_ml:
        return home_abbr if home_ml < away_ml else away_abbr
    if get(raw, "homeTeamOdds", "favorite") is True:
        return home_abbr
    if get(raw, "awayTeamOdds", "favorite") is True:
        return away_abbr
    return None


def normalize_odds(raw: Any, home_abbr: str | None, away_abbr: str | None) -> OddsLine | None:
    """Turn one ESPN odds entry into :class:`OddsLine` (``None`` when it holds no numbers)."""
    if not isinstance(raw, dict):
        return None
    home_spread = as_float(get(raw, "pointSpread", "home", "close", "line"))
    if home_spread is None:
        home_spread = as_float(raw.get("spread"))
    home_ml, away_ml = _moneyline(raw, "home"), _moneyline(raw, "away")
    spread = None if home_spread is None else (0.0 if home_spread == 0 else -abs(home_spread))

    provider = as_dict(raw.get("provider"))
    line = OddsLine(
        provider=text(provider.get("name")) or text(provider.get("displayName")),
        details=text(raw.get("details")),
        favorite_abbr=_favorite(home_spread, home_ml, away_ml, raw, home_abbr, away_abbr),
        spread=spread,
        over_under=as_float(raw.get("overUnder"))
        or as_float(get(raw, "total", "over", "close", "line")),
        home_moneyline=home_ml,
        away_moneyline=away_ml,
        draw_moneyline=_moneyline(raw, "draw"),
        open=_snapshot(raw, "open"),
        close=_snapshot(raw, "close"),
    )
    numbers = line.model_dump(exclude_none=True, exclude={"provider", "details", "open", "close"})
    return line if numbers else None


def first_odds(entries: Any, home_abbr: str | None, away_abbr: str | None) -> OddsLine | None:
    """The first usable entry of an ESPN odds list (ESPN lists its priority provider first)."""
    if not isinstance(entries, list):
        return None
    for entry in entries:
        line = normalize_odds(entry, home_abbr, away_abbr)
        if line is not None:
            return line
    return None
