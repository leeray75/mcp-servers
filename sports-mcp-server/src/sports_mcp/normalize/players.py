"""Player-level adapters: top performers per team and full box-score tables, per sport.

Inputs are the summary's ``boxscore.players`` blocks (and ``rosters`` for soccer, where
ESPN publishes no box-score table). Everything is defensive about missing labels.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sports_mcp.leagues import Family
from sports_mcp.models import BoxGroup, BoxRow, Performer, Turnovers
from sports_mcp.normalize.common import as_dict, as_list, text

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_MAX_LABELS_IN_FALLBACK = 6


def fnum(value: str | None) -> float:
    """First number in a display value (``"5-10"`` -> 5.0, ``"--"`` -> 0.0)."""
    if not value:
        return 0.0
    match = _NUM.search(value)
    return float(match.group()) if match else 0.0


class _Row:
    """One athlete's line in a group: name, position and ``label -> value``."""

    def __init__(self, name: str, position: str | None, labels: list[str], values: list[str]):
        self.name = name
        self.position = position
        self.labels = labels
        self.values = values
        self.s: dict[str, str] = {}
        for label, value in zip(labels, values, strict=False):
            self.s.setdefault(label, value)

    def n(self, label: str) -> float:
        return fnum(self.s.get(label))

    def nz(self, label: str) -> bool:
        return self.n(label) != 0


class _Group:
    def __init__(self, name: str, labels: list[str], rows: list[_Row]):
        self.name = name
        self.labels = labels
        self.rows = rows


def _groups(block: dict[str, Any]) -> list[_Group]:
    groups: list[_Group] = []
    for raw in as_list(block.get("statistics")):
        raw = as_dict(raw)
        labels = [text(x) or "" for x in as_list(raw.get("labels"))]
        rows: list[_Row] = []
        for entry in as_list(raw.get("athletes")):
            entry = as_dict(entry)
            athlete = as_dict(entry.get("athlete"))
            name = text(athlete.get("displayName")) or text(athlete.get("shortName"))
            stats = [text(x) or "" for x in as_list(entry.get("stats"))]
            if not name or not stats or entry.get("didNotPlay") is True:
                continue
            pos = text(as_dict(athlete.get("position")).get("abbreviation")) or text(
                as_dict(entry.get("position")).get("abbreviation")
            )
            rows.append(_Row(name, pos, labels, stats))
        name = text(raw.get("name")) or text(raw.get("type")) or "players"
        groups.append(_Group(name, labels, rows))
    return groups


def _team_abbr(block: dict[str, Any]) -> str:
    return text(as_dict(block.get("team")).get("abbreviation")) or "?"


def _fallback_line(row: _Row) -> str:
    pairs = [
        f"{v} {k}"
        for k, v in zip(row.labels, row.values, strict=False)
        if v and v not in {"--", "-"}
    ]
    return ", ".join(pairs[:_MAX_LABELS_IN_FALLBACK])


def has_numeric_stats(blocks: Any) -> bool:
    """True when any athlete stat contains a digit (pre-game blocks hold ``--`` placeholders)."""
    for block in as_list(blocks):
        for group in _groups(as_dict(block)):
            for row in group.rows:
                if any(any(ch.isdigit() for ch in v) for v in row.values):
                    return True
    return False


def _performer(row: _Row, line: str, wanted: tuple[str, ...] = ()) -> Performer:
    keys = wanted or tuple(row.labels)
    stats = {k: row.s[k] for k in keys if k in row.s and row.s[k] not in {"", "--"}}
    return Performer(
        player=row.name,
        position=row.position,
        line=line or _fallback_line(row),
        stats=stats or None,
    )


def _interleave(buckets: list[list[Performer]], top_n: int) -> list[Performer]:
    """Round-robin across category buckets so one category cannot crowd out the rest."""
    picked: list[Performer] = []
    depth = 0
    while len(picked) < top_n and any(depth < len(b) for b in buckets):
        for bucket in buckets:
            if depth < len(bucket) and len(picked) < top_n:
                picked.append(bucket[depth])
        depth += 1
    return picked


def _count(value: str | None, singular: str) -> str:
    """``1 sack`` / ``2 sacks`` / ``0.5 sacks``."""
    shown = value or "0"
    return f"{shown} {singular}" if shown == "1" else f"{shown} {singular}s"


def _tail(parts: list[tuple[str, str]]) -> str:
    """``[("2","TD"),("0","INT")]`` -> ``", 2 TD"`` (zero values omitted)."""
    return "".join(f", {v} {k}" for v, k in parts if fnum(v) != 0)


# ---------------------------------------------------------------- football


def _football(block: dict[str, Any], top_n: int) -> list[Performer]:
    groups = {g.name: g for g in _groups(block)}

    def ranked(name: str, key: str, then: str = "") -> list[_Row]:
        group = groups.get(name)
        if group is None:
            return []
        return sorted(group.rows, key=lambda r: (-r.n(key), -r.n(then) if then else 0.0))

    buckets: list[list[Performer]] = []
    buckets.append(
        [
            _performer(
                r,
                f"pass: {r.s.get('C/ATT', '?')}, {r.s.get('YDS', '0')} yds"
                + _tail([(r.s.get("TD", "0"), "TD"), (r.s.get("INT", "0"), "INT")]),
                ("C/ATT", "YDS", "TD", "INT", "RTG"),
            )
            for r in ranked("passing", "YDS")[:2]
        ]
    )
    buckets.append(
        [
            _performer(
                r,
                f"rush: {r.s.get('CAR', '0')} car, {r.s.get('YDS', '0')} yds"
                + _tail([(r.s.get("TD", "0"), "TD")]),
                ("CAR", "YDS", "AVG", "TD", "LONG"),
            )
            for r in ranked("rushing", "YDS")[:3]
        ]
    )
    buckets.append(
        [
            _performer(
                r,
                f"rec: {r.s.get('REC', '0')} rec, {r.s.get('YDS', '0')} yds"
                + _tail([(r.s.get("TD", "0"), "TD")]),
                ("REC", "YDS", "TD", "LONG", "TGTS"),
            )
            for r in ranked("receiving", "YDS")[:4]
        ]
    )
    buckets.append(
        [
            _performer(
                r,
                f"def: {r.s.get('TOT', '0')} tot, {r.s.get('SOLO', '0')} solo"
                + (f", {_count(r.s.get('SACKS'), 'sack')}" if r.nz("SACKS") else "")
                + _tail([(r.s.get("TFL", "0"), "TFL")]),
                ("TOT", "SOLO", "SACKS", "TFL", "PD"),
            )
            for r in sorted(
                groups["defensive"].rows if "defensive" in groups else [],
                key=lambda r: (-r.n("TOT"), -r.n("SACKS")),
            )[:5]
        ]
    )
    buckets.append(
        [
            _performer(
                r,
                f"K: FG {r.s.get('FG', '0')}, XP {r.s.get('XP', '0')}",
                ("FG", "XP", "LONG", "PTS"),
            )
            for r in ranked("kicking", "PTS")[:1]
        ]
    )
    return _interleave(buckets, top_n)


# ---------------------------------------------------------------- basketball


def _basketball(block: dict[str, Any], top_n: int) -> list[Performer]:
    rows = [r for g in _groups(block) for r in g.rows]
    rows = [r for r in rows if r.nz("PTS") or r.nz("REB") or r.nz("AST") or r.nz("MIN")]
    rows.sort(key=lambda r: (-r.n("PTS"), -(r.n("PTS") + r.n("REB") + r.n("AST"))))
    out: list[Performer] = []
    for r in rows[:top_n]:
        line = f"{r.s.get('PTS', '0')} pts, {r.s.get('REB', '0')} reb, {r.s.get('AST', '0')} ast"
        extras = [f"{r.s[k]} {k}" for k in ("FG", "3PT", "MIN") if r.s.get(k)]
        if extras:
            line += f" ({', '.join(extras)})"
        out.append(
            _performer(r, line, ("MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "FG", "3PT", "FT"))
        )
    return out


# ---------------------------------------------------------------- baseball


def _baseball(block: dict[str, Any], top_n: int) -> list[Performer]:
    groups = {g.name: g for g in _groups(block)}
    batters = [
        r
        for r in (groups["batting"].rows if "batting" in groups else [])
        if r.nz("AB") or r.nz("BB")
    ]
    batters.sort(key=lambda r: (-r.n("H"), -r.n("HR"), -r.n("RBI")))
    pitchers = [
        r
        for r in (groups["pitching"].rows if "pitching" in groups else [])
        if r.s.get("IP") not in (None, "", "--", "--.--")
    ]
    pitchers.sort(key=lambda r: (-r.n("IP"), -r.n("K")))
    bat = [
        _performer(
            r,
            f"{r.s.get('H-AB', '?')}"
            + _tail(
                [
                    (r.s.get("HR", "0"), "HR"),
                    (r.s.get("RBI", "0"), "RBI"),
                    (r.s.get("R", "0"), "R"),
                    (r.s.get("BB", "0"), "BB"),
                    (r.s.get("K", "0"), "K"),
                ]
            ),
            ("H-AB", "R", "H", "RBI", "HR", "BB", "K", "AVG"),
        )
        for r in batters[:6]
    ]
    pit = [
        _performer(
            r,
            f"{r.s.get('IP', '0')} IP, {r.s.get('H', '0')} H, {r.s.get('ER', '0')} ER, "
            f"{r.s.get('BB', '0')} BB, {r.s.get('K', '0')} K",
            ("IP", "H", "R", "ER", "BB", "K", "HR", "PC-ST", "ERA"),
        )
        for r in pitchers[:3]
    ]
    return _interleave([bat, pit], top_n)


# ---------------------------------------------------------------- hockey


def _hockey(block: dict[str, Any], top_n: int) -> list[Performer]:
    groups = _groups(block)
    skaters = [
        r
        for g in groups
        if g.name != "goalies"
        for r in g.rows
        if r.s.get("TOI") not in (None, "", "--", "0:00")
    ]
    skaters.sort(key=lambda r: (-(r.n("G") + r.n("A")), -r.n("G"), -r.n("S")))
    goalies = [
        r
        for g in groups
        if g.name == "goalies"
        for r in g.rows
        if r.s.get("TOI") not in (None, "", "--")
    ]
    goalies.sort(key=lambda r: -r.n("SA"))
    sk = [
        _performer(
            r,
            f"{r.s.get('G', '0')} G, {r.s.get('A', '0')} A, {_count(r.s.get('S'), 'shot')}, "
            f"{r.s.get('+/-', '0')} +/-",
            ("G", "A", "S", "+/-", "HT", "BS", "TOI", "PIM"),
        )
        for r in skaters[:6]
    ]
    gl = [
        _performer(
            r,
            f"{_count(r.s.get('SV'), 'save')} on {_count(r.s.get('SA'), 'shot')}, "
            f"{r.s.get('SV%', '-')} SV%, "
            f"{r.s.get('GA', '0')} GA",
            ("SA", "SV", "GA", "SV%", "TOI"),
        )
        for r in goalies[:1]
    ]
    return _interleave([sk, gl], top_n)


# ---------------------------------------------------------------- soccer

_SOCCER_COLS: tuple[tuple[str, str], ...] = (
    ("totalGoals", "G"),
    ("goalAssists", "A"),
    ("totalShots", "SH"),
    ("shotsOnTarget", "SOT"),
    ("yellowCards", "YC"),
    ("redCards", "RC"),
    ("saves", "SV"),
    ("foulsCommitted", "FC"),
)


def _soccer_rows(roster_block: dict[str, Any]) -> list[_Row]:
    rows: list[_Row] = []
    for entry in as_list(roster_block.get("roster")):
        entry = as_dict(entry)
        if not (entry.get("starter") or entry.get("subbedIn")):
            continue
        athlete = as_dict(entry.get("athlete"))
        name = text(athlete.get("displayName")) or text(athlete.get("shortName"))
        if not name:
            continue
        stats = {
            text(as_dict(s).get("name")) or "": text(as_dict(s).get("displayValue")) or "0"
            for s in as_list(entry.get("stats"))
        }
        labels = [label for _, label in _SOCCER_COLS]
        values = [stats.get(key, "0") for key, _ in _SOCCER_COLS]
        pos = text(as_dict(entry.get("position")).get("abbreviation"))
        rows.append(_Row(name, pos, labels, values))
    return rows


def _soccer(roster_block: dict[str, Any], top_n: int) -> list[Performer]:
    def score(r: _Row) -> float:
        return (
            5 * r.n("G")
            + 3 * r.n("A")
            + 2 * r.n("RC")
            + r.n("YC")
            + 0.5 * r.n("SOT")
            + 0.25 * r.n("SV")
        )

    rows = [r for r in _soccer_rows(roster_block) if score(r) > 0]
    rows.sort(key=lambda r: -score(r))
    out: list[Performer] = []
    for r in rows[:top_n]:
        parts = [
            (_count(r.s[k], "save") if k == "SV" else f"{r.s[k]} {word}")
            for k, word in (
                ("G", "G"),
                ("A", "A"),
                ("SOT", "SOT"),
                ("SV", "saves"),
                ("YC", "yellow"),
                ("RC", "red"),
            )
            if r.nz(k)
        ]
        out.append(_performer(r, ", ".join(parts), ("G", "A", "SH", "SOT", "YC", "RC", "SV")))
    return out


# ---------------------------------------------------------------- dispatch

_PERFORMERS: dict[str, Callable[[dict[str, Any], int], list[Performer]]] = {
    "football": _football,
    "basketball": _basketball,
    "baseball": _baseball,
    "hockey": _hockey,
}


def top_performers(
    family: Family, boxscore_players: Any, rosters: Any, top_n: int
) -> dict[str, list[Performer]] | None:
    """Top ``top_n`` performers per team keyed by team abbreviation."""
    result: dict[str, list[Performer]] = {}
    if family == "soccer":
        for block in as_list(rosters):
            block = as_dict(block)
            performers = _soccer(block, top_n)
            if performers:
                result[_team_abbr(block)] = performers
    else:
        for block in as_list(boxscore_players):
            block = as_dict(block)
            performers = _PERFORMERS[family](block, top_n)
            if performers:
                result[_team_abbr(block)] = performers
    return result or None


def box_tables(
    family: Family, boxscore_players: Any, rosters: Any
) -> dict[str, list[BoxGroup]] | None:
    """Full per-team player tables keyed by team abbreviation."""
    result: dict[str, list[BoxGroup]] = {}
    if family == "soccer":
        for block in as_list(rosters):
            block = as_dict(block)
            rows = _soccer_rows(block)
            if rows:
                result[_team_abbr(block)] = [
                    BoxGroup(
                        group="players",
                        labels=[label for _, label in _SOCCER_COLS],
                        rows=[
                            BoxRow(player=r.name, position=r.position, stats=r.values) for r in rows
                        ],
                    )
                ]
        return result or None
    for block in as_list(boxscore_players):
        block = as_dict(block)
        groups = [
            BoxGroup(
                group=g.name,
                labels=g.labels,
                rows=[BoxRow(player=r.name, position=r.position, stats=r.values) for r in g.rows],
            )
            for g in _groups(block)
            if g.rows
        ]
        if groups:
            result[_team_abbr(block)] = groups
    return result or None


# ---------------------------------------------------------------- football turnovers (R6)


@dataclass(frozen=True, slots=True)
class _TeamTurnovers:
    """One team's raw turnover counts, before the opponent's fumbles are known."""

    thrown: int
    thrown_by: list[str]
    lost: int
    lost_by: list[str]
    made: int  # interceptions made by this team's defence


def _team_turnovers(block: dict[str, Any]) -> _TeamTurnovers | None:
    """``None`` when a needed table or label is missing: "not reported", not "none"."""
    groups = {g.name: g for g in _groups(block)}
    passing, fumbles, made = (
        groups.get("passing"),
        groups.get("fumbles"),
        groups.get("interceptions"),
    )
    if passing is None or fumbles is None or made is None:
        return None
    if "INT" not in passing.labels or "LOST" not in fumbles.labels or "INT" not in made.labels:
        return None
    return _TeamTurnovers(
        thrown=sum(int(r.n("INT")) for r in passing.rows),
        thrown_by=[r.name for r in passing.rows if r.nz("INT")],
        lost=sum(int(r.n("LOST")) for r in fumbles.rows),
        lost_by=[r.name for r in fumbles.rows if r.nz("LOST")],
        made=sum(int(r.n("INT")) for r in made.rows),
    )


def turnovers(boxscore_players: Any) -> dict[str, Turnovers] | None:
    """Per-team turnover totals from a football box score (exactly two teams, else ``None``).

    A team whose own tables, or whose opponent's fumble table, are missing gets no entry.
    """
    blocks = [as_dict(b) for b in as_list(boxscore_players)]
    if len(blocks) != 2:
        return None
    parsed = [_team_turnovers(b) for b in blocks]
    result: dict[str, Turnovers] = {}
    for own_index, opponent_index in ((0, 1), (1, 0)):
        own, opponent = parsed[own_index], parsed[opponent_index]
        if own is None or opponent is None:
            continue
        result[_team_abbr(blocks[own_index])] = Turnovers(
            total=own.thrown + own.lost,
            interceptions_thrown=own.thrown,
            interceptions_thrown_by=own.thrown_by,
            fumbles_lost=own.lost,
            fumbles_lost_by=own.lost_by,
            takeaways=own.made + opponent.lost,
        )
    return result or None
