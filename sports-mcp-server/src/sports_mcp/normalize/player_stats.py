"""Player normalization: search ranking, name folding, profile, stats and game logs.

Pure functions, no I/O. ESPN's common/v3 payloads are parsed defensively: a missing optional
field is ``None``; a present-but-malformed structure raises :class:`UpstreamError` with the spec
message, never a fabricated value.

Rules implemented here (spec sections 4 R1-R18): name folding and Levenshtein ranking (R4, R5),
season-type block selection by display name (R7, D11), per-sport column allow-lists with reducers
(R8, R9), football turnovers (R12), season-to-date rate handling (R9, D12) and the player's-team-
first score derived from home/away points (R18).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from sports_mcp.dates import parse_espn_time
from sports_mcp.errors import UpstreamError
from sports_mcp.leagues import League
from sports_mcp.models import (
    CareerLog,
    ColumnInfo,
    Completeness,
    GameLogRow,
    PlayerStatCategory,
    PlayerStatColumn,
    PlayerStatRow,
    SeasonLog,
)
from sports_mcp.normalize.common import as_dict, as_int, as_list, text

MSG_UNEXPECTED: Final = "ESPN returned an unexpected response"
SEARCH_LIMIT: Final = 50  # ESPN returns mixed leagues; we filter, so ask for plenty
MAX_RETRIES: Final = 3
MIN_RETRY_CHARS: Final = 4
MAX_CANDIDATES: Final = 5
_ID: Final = re.compile(r"[0-9]{1,20}")
_NUMBER: Final = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)")
_PAIR: Final = re.compile(r"(\d+)-(\d+)")
_RATE_NAME: Final = re.compile(r"pct|percent|avg|average|rate|ratio|rtg|qbr|eff|%|/|-", re.I)
_LONG_NAME: Final = re.compile(r"long|lng", re.I)

Reducer = Literal["sum", "max", "ratio", "pair", "none"]
DERIVED: Final = "derived"


# --------------------------------------------------------------------------- names and search


def fold_name(value: str) -> str:
    """Case, accents and punctuation ignored: "Jaxon Smith-Njigba" -> "jaxon smith njigba"."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", stripped.casefold()).split())


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insert, delete, substitute), iterative two-row version."""
    if a == b:
        return 0
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            cost = 0 if char_a == char_b else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def name_distance(folded_query: str, name: str) -> int:
    """Distance from the folded query to a candidate: the full folded name or any single name
    word, whichever is closer (so ``skattebu`` matches ``Cam Skattebo``, as R5 requires)."""
    folded = fold_name(name)
    return min(levenshtein(folded_query, option) for option in [folded, *folded.split()])


def retry_queries(query: str) -> list[str]:
    """Prefix retries (R5): trim the longest alphabetic token by 1, 2, 3 characters.

    Each retry keeps at least :data:`MIN_RETRY_CHARS` characters of that token, at most
    :data:`MAX_RETRIES` queries are returned, and a query with no alphabetic token has none.
    """
    parts = query.split()
    alpha = [(i, p) for i, p in enumerate(parts) if p.isalpha()]
    if not alpha:
        return []
    index, token = max(alpha, key=lambda item: len(item[1]))
    retries: list[str] = []
    for trim in range(1, MAX_RETRIES + 1):
        kept = len(token) - trim
        if kept < MIN_RETRY_CHARS:
            break
        changed = list(parts)
        changed[index] = token[:kept]
        retries.append(" ".join(changed))
    return retries


@dataclass(frozen=True)
class Candidate:
    id: str
    name: str
    team: str | None
    order: int  # ESPN order among the league's players


def parse_search(raw: dict[str, Any], lg: League) -> list[Candidate]:
    """Search items for ``lg`` only (ESPN mixes sports and leagues)."""
    items = raw.get("items")
    if not isinstance(items, list):
        raise UpstreamError(MSG_UNEXPECTED)
    found: list[Candidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("sport") != lg.sport or item.get("league") != lg.slug:
            continue
        ident = text(item.get("id"))
        name = text(item.get("displayName"))
        if ident is None or name is None or not _ID.fullmatch(ident):
            continue
        team = None
        for rel in as_list(item.get("teamRelationships")):
            if isinstance(rel, dict) and rel.get("type") == "team":
                team = text(rel.get("displayName"))
                break
        found.append(Candidate(ident, name, team, len(found)))
    return found


@dataclass(frozen=True)
class Resolution:
    chosen: Candidate | None
    match: str | None  # exact | single | fuzzy


def resolve(query: str, candidates: list[Candidate], fuzzy: bool) -> Resolution:
    """R4(a)(b) for exact searches; R5 auto-resolve rule for fuzzy (retried) searches."""
    folded = fold_name(query)
    if fuzzy:
        scored = sorted((name_distance(folded, c.name), c.order, c) for c in candidates)
        if not scored:
            return Resolution(None, None)
        best_distance, _, best = scored[0]
        threshold = max(2.0, 0.2 * len(folded))
        clear_gap = len(scored) == 1 or scored[1][0] - best_distance >= 2
        if best_distance <= threshold and clear_gap:
            return Resolution(best, "fuzzy")
        return Resolution(None, None)
    if len(candidates) == 1:
        return Resolution(candidates[0], "single")
    exact = [c for c in candidates if fold_name(c.name) == folded]
    if len(exact) == 1:
        return Resolution(exact[0], "exact")
    return Resolution(None, None)


def rank_for_listing(
    query: str, candidates: list[Candidate], fuzzy: bool
) -> list[tuple[Candidate, str | None]]:
    """Ranked candidates for find_player and unresolved lookups (R1, R5)."""
    folded = fold_name(query)
    if fuzzy:
        scored = sorted((name_distance(folded, c.name), c.order, c) for c in candidates)
        return [(c, "fuzzy") for _, _, c in scored]
    exact_ids = {c.id for c in candidates if fold_name(c.name) == folded}
    exact = [c for c in candidates if c.id in exact_ids]
    rest = [c for c in candidates if c.id not in exact_ids]
    return [(c, "exact") for c in exact] + [(c, None) for c in rest]


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    team: str | None
    position: str | None


def parse_profile(raw: dict[str, Any], athlete_id: str) -> Profile:
    """Athlete profile (D10): name, team and position for an id lookup."""
    athlete = raw.get("athlete")
    if not isinstance(athlete, dict):
        raise UpstreamError(MSG_UNEXPECTED)
    name = text(athlete.get("displayName")) or text(athlete.get("fullName"))
    if name is None:
        raise UpstreamError(MSG_UNEXPECTED)
    return Profile(
        id=athlete_id,
        name=name,
        team=text(as_dict(athlete.get("team")).get("displayName")),
        position=text(as_dict(athlete.get("position")).get("abbreviation")),
    )


def seasons_played(stats_raw: dict[str, Any]) -> list[int]:
    """Distinct season years in a stats payload, most recent first (R6, section 5.2)."""
    years: set[int] = set()
    for category in as_list(stats_raw.get("categories")):
        for row in as_list(as_dict(category).get("statistics")):
            year = as_int(as_dict(as_dict(row).get("season")).get("year"))
            if year is not None:
                years.add(year)
    return sorted(years, reverse=True)


# --------------------------------------------------------------------------- values


def clean_value(value: Any) -> str | None:
    """ESPN cell -> text, with ``-`` and empty cells treated as missing (R9)."""
    cell = text(value)
    if cell is None or cell == "-":
        return None
    return cell


def to_number(value: str | None) -> float | None:
    """A plain number (thousands commas removed), else ``None``. Composite cells are not numbers."""
    if value is None:
        return None
    cleaned = value.replace(",", "")
    if not _NUMBER.fullmatch(cleaned):
        return None
    return float(cleaned)


def pair_parts(value: str | None) -> tuple[float, float] | None:
    """``"9-21"`` (made-attempted) -> (9, 21)."""
    if value is None:
        return None
    match = _PAIR.fullmatch(value.replace(",", ""))
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def format_number(value: float, digits: int) -> str:
    """Fixed decimals for a recomputed rate (``3.9``, ``.265`` is shown as ``0.265``)."""
    return f"{value:.{digits}f}"


# --------------------------------------------------------------------------- columns


def key_for(category: str | None, name: str) -> str:
    return f"{category}.{name}" if category else name


@dataclass(frozen=True)
class Column:
    key: str
    category: str | None
    name: str
    label: str


def columns_of(raw: dict[str, Any]) -> list[Column]:
    """Game-log columns from ``names``/``labels`` and the category slices (``count``).

    ``labels`` repeats across categories (``YDS`` twice), so the category slice, not the label,
    identifies a column. A missing category map is allowed (NBA, MLB); a map whose counts do not
    add up to the number of columns is malformed.
    """
    names = raw.get("names")
    labels = raw.get("labels")
    if not isinstance(names, list) or not names:
        return []
    if not isinstance(labels, list) or len(labels) != len(names):
        raise UpstreamError(MSG_UNEXPECTED)
    clean_names = [text(n) for n in names]
    if any(n is None for n in clean_names):
        raise UpstreamError(MSG_UNEXPECTED)
    owners: list[str | None] = [None] * len(names)
    categories = as_list(raw.get("categories"))
    if categories:
        position = 0
        for category in categories:
            cat = as_dict(category)
            count = as_int(cat.get("count"))
            label = text(cat.get("name"))
            if count is None or count < 0 or label is None:
                raise UpstreamError(MSG_UNEXPECTED)
            for index in range(position, min(position + count, len(names))):
                owners[index] = label
            position += count
        if position != len(names):
            raise UpstreamError(MSG_UNEXPECTED)
    return [
        Column(key_for(owner, str(name)), owner, str(name), str(label))
        for owner, name, label in zip(owners, clean_names, labels, strict=True)
    ]


@dataclass(frozen=True)
class Spec:
    """One text column of a sport's allow-list (R8) and how its totals are computed (R9)."""

    label: str
    group: str  # heading shown in text ("" = no heading)
    category: str | None  # ESPN category, None when the payload has no categories
    name: str  # ESPN stat name within the category (or the derived name)
    reducer: Reducer
    ratio: tuple[str, str] | None = None  # full keys (numerator, denominator) for ratio
    digits: int = 1
    season_to_date: bool = False  # per-game cell is season-to-date (MLB), hidden in text
    derived: bool = False

    @property
    def key(self) -> str:
        return key_for(self.category, self.name)


def _spec(
    label: str,
    group: str,
    category: str | None,
    name: str,
    reducer: Reducer,
    **extra: Any,
) -> Spec:
    return Spec(label, group, category, name, reducer, **extra)


_FOOTBALL: Final[tuple[Spec, ...]] = (
    _spec("CAR", "Rushing", "rushing", "rushingAttempts", "sum"),
    _spec("YDS", "Rushing", "rushing", "rushingYards", "sum"),
    _spec(
        "AVG",
        "Rushing",
        "rushing",
        "yardsPerRushAttempt",
        "ratio",
        ratio=("rushing.rushingYards", "rushing.rushingAttempts"),
    ),
    _spec("TD", "Rushing", "rushing", "rushingTouchdowns", "sum"),
    _spec("LNG", "Rushing", "rushing", "longRushing", "max"),
    _spec("REC", "Receiving", "receiving", "receptions", "sum"),
    _spec("TGT", "Receiving", "receiving", "receivingTargets", "sum"),
    _spec("YDS", "Receiving", "receiving", "receivingYards", "sum"),
    _spec(
        "AVG",
        "Receiving",
        "receiving",
        "yardsPerReception",
        "ratio",
        ratio=("receiving.receivingYards", "receiving.receptions"),
    ),
    _spec("TD", "Receiving", "receiving", "receivingTouchdowns", "sum"),
    _spec("LNG", "Receiving", "receiving", "longReception", "max"),
    _spec("CMP", "Passing", "passing", "completions", "sum"),
    _spec("ATT", "Passing", "passing", "passingAttempts", "sum"),
    _spec("YDS", "Passing", "passing", "passingYards", "sum"),
    _spec("TD", "Passing", "passing", "passingTouchdowns", "sum"),
    _spec("INT", "Turnovers", "passing", "interceptions", "sum"),  # thrown, not defensive
    _spec("FUM", "Turnovers", "fumbles", "fumbles", "sum"),
    _spec("LST", "Turnovers", "fumbles", "fumblesLost", "sum"),
    _spec("TO", "Turnovers", DERIVED, "TO", "sum", derived=True),
    _spec("TCK", "Defense", "tackles", "totalTackles", "sum"),
    _spec("SCK", "Defense", "tackles", "sacks", "sum"),
    _spec("DINT", "Defense", "interceptions", "interceptions", "sum"),  # defensive INT
    _spec("PD", "Defense", "interceptions", "passesDefended", "sum"),
)

_BASKETBALL: Final[tuple[Spec, ...]] = (
    _spec("MIN", "", None, "minutes", "sum"),
    _spec("FG", "", None, "fieldGoalsMade-fieldGoalsAttempted", "pair"),
    _spec("3PT", "", None, "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "pair"),
    _spec("FT", "", None, "freeThrowsMade-freeThrowsAttempted", "pair"),
    _spec("REB", "", None, "totalRebounds", "sum"),
    _spec("AST", "", None, "assists", "sum"),
    _spec("BLK", "", None, "blocks", "sum"),
    _spec("STL", "", None, "steals", "sum"),
    _spec("TO", "", None, "turnovers", "sum"),
    _spec("PF", "", None, "fouls", "sum"),
    _spec("PTS", "", None, "points", "sum"),
)

_BASEBALL: Final[tuple[Spec, ...]] = (
    _spec("AB", "Batting", None, "atBats", "sum"),
    _spec("R", "Batting", None, "runs", "sum"),
    _spec("H", "Batting", None, "hits", "sum"),
    _spec("2B", "Batting", None, "doubles", "sum"),
    _spec("3B", "Batting", None, "triples", "sum"),
    _spec("HR", "Batting", None, "homeRuns", "sum"),
    _spec("RBI", "Batting", None, "RBIs", "sum"),
    _spec("BB", "Batting", None, "walks", "sum"),
    _spec("SO", "Batting", None, "strikeouts", "sum"),
    _spec("SB", "Batting", None, "stolenBases", "sum"),
    _spec(
        "AVG",
        "Batting",
        None,
        "avg",
        "ratio",
        ratio=("hits", "atBats"),
        digits=3,
        season_to_date=True,
    ),
    _spec("OBP", "Batting", None, "onBasePct", "none", season_to_date=True),
    _spec("SLG", "Batting", None, "slugAvg", "none", season_to_date=True),
    _spec("OPS", "Batting", None, "OPS", "none", season_to_date=True),
)

_ALLOW: Final[dict[str, tuple[Spec, ...]]] = {
    "football": _FOOTBALL,
    "basketball": _BASKETBALL,
    "baseball": _BASEBALL,
}


def specs_for(family: str, columns: list[Column]) -> tuple[Spec, ...]:
    """The allow-list for a sport family; hockey and soccer (no fixtures) use a generic list.

    The generic list shows every upstream column. A column whose name or label marks a rate
    (pct, avg, ratio, ...) gets reducer ``none``; a long-shot column gets ``max``; the rest sum.
    """
    if family in _ALLOW:
        return _ALLOW[family]
    generic: list[Spec] = []
    for column in columns:
        if _RATE_NAME.search(column.name) or _RATE_NAME.search(column.label):
            reducer: Reducer = "none"
        elif _LONG_NAME.search(column.name):
            reducer = "max"
        else:
            reducer = "sum"
        generic.append(
            Spec(column.label, column.category or "", column.category, column.name, reducer)
        )
    return tuple(generic)


def column_infos(specs: tuple[Spec, ...]) -> list[ColumnInfo]:
    return [
        ColumnInfo(
            key=spec.key,
            label=spec.label,
            group=spec.group or None,
            season_to_date=True if spec.season_to_date else None,
        )
        for spec in specs
    ]


# --------------------------------------------------------------------------- totals


def _sum_totals(values: list[str | None]) -> str | None:
    numbers = [n for n in (to_number(v) for v in values) if n is not None]
    if not numbers:
        return None
    total = sum(numbers)
    return str(int(total)) if float(total).is_integer() else f"{total:.1f}"


def compute_totals(rows: list[GameLogRow], specs: tuple[Spec, ...]) -> dict[str, str | None]:
    """Computed totals from listed games (R9): summed, maxed, recomputed rates, or ``None``.

    ``None`` (JSON null) means every input was missing (A1, R4); the text renders it as ``n/a``.
    """
    totals: dict[str, str | None] = {}
    for spec in specs:
        values = [row.stats.get(spec.key) for row in rows]
        if spec.reducer == "sum":
            totals[spec.key] = _sum_totals(values)
        elif spec.reducer == "max":
            numbers = [n for n in (to_number(v) for v in values) if n is not None]
            totals[spec.key] = str(int(max(numbers))) if numbers else None
        elif spec.reducer == "ratio" and spec.ratio is not None:
            num = [to_number(row.stats.get(spec.ratio[0])) for row in rows]
            den = [to_number(row.stats.get(spec.ratio[1])) for row in rows]
            num_values = [n for n in num if n is not None]
            den_values = [d for d in den if d is not None]
            denominator = sum(den_values)
            if not num_values or not den_values or denominator == 0:
                totals[spec.key] = None
            else:
                totals[spec.key] = format_number(sum(num_values) / denominator, spec.digits)
        elif spec.reducer == "pair":
            parts = [p for p in (pair_parts(v) for v in values) if p is not None]
            if not parts:
                totals[spec.key] = None
            else:
                totals[spec.key] = (
                    f"{int(sum(p[0] for p in parts))}-{int(sum(p[1] for p in parts))}"
                )
        else:
            totals[spec.key] = None
    return totals


# --------------------------------------------------------------------------- game logs


def _season_kind(label: str) -> Literal["regular", "post"] | None:
    lowered = label.casefold()
    if lowered.endswith("postseason"):
        return "post"
    if lowered.endswith("regular season"):
        return "regular"
    return None  # preseason and anything else: ignored (D11)


def _season_year(label: str) -> int:
    """``"2025 Regular Season"`` -> 2025; NBA ``"2024-25 Regular Season"`` -> 2025 (end year)."""
    match = re.match(r"(\d{4})(?:-(\d{2}))?", label)
    if match is None:
        raise UpstreamError(MSG_UNEXPECTED)
    return int(match.group(1)) + (1 if match.group(2) else 0)


def _values(keys: list[str], stats: Any) -> dict[str, str | None]:
    if not isinstance(stats, list) or len(stats) != len(keys):
        raise UpstreamError(MSG_UNEXPECTED)
    return {key: clean_value(cell) for key, cell in zip(keys, stats, strict=True)}


def _derived_turnovers(values: dict[str, str | None], present: set[str]) -> str | None:
    """R12: TO = INT thrown + fumbles lost, from whichever components the payload has."""
    components = [k for k in ("passing.interceptions", "fumbles.fumblesLost") if k in present]
    if not components:
        return None
    numbers = [to_number(values.get(k)) for k in components]
    real = [n for n in numbers if n is not None]
    if not real:
        return None
    return str(int(sum(real)))


def _score_and_result(
    event: dict[str, Any], upstream_result: str | None
) -> tuple[str | None, str | None, Literal["home", "away"] | None]:
    """R18: score and result from the player's team's point of view."""
    team_id = text(as_dict(event.get("team")).get("id"))
    home_id = text(event.get("homeTeamId"))
    away_id = text(event.get("awayTeamId"))
    is_home: bool | None = None
    if team_id is not None and home_id is not None:
        is_home = team_id == home_id
    elif team_id is not None and away_id is not None:
        is_home = team_id != away_id
    elif text(event.get("atVs")) is not None:
        is_home = text(event.get("atVs")) == "vs"
    home_away: Literal["home", "away"] | None = None
    if is_home is not None:
        home_away = "home" if is_home else "away"
    home_points = as_int(event.get("homeTeamScore"))
    away_points = as_int(event.get("awayTeamScore"))
    if is_home is None or home_points is None or away_points is None:
        result = upstream_result if upstream_result in {"W", "L", "T"} else None
        return None, result, home_away
    mine, theirs = (home_points, away_points) if is_home else (away_points, home_points)
    result = "W" if mine > theirs else "L" if mine < theirs else "T"
    return f"{mine}-{theirs}", result, home_away  # derived result wins over the upstream letter


def _game_row(
    event: dict[str, Any],
    keys: list[str],
    stats: Any,
    present: set[str],
    tz: ZoneInfo,
    derive_turnovers: bool,
) -> tuple[datetime | None, GameLogRow]:
    values = _values(keys, stats)
    to_value = _derived_turnovers(values, present) if derive_turnovers else None
    upstream = text(event.get("gameResult"))
    score, result, home_away = _score_and_result(event, upstream.upper() if upstream else None)
    moment = parse_espn_time(event.get("gameDate"))
    shown = {key: value for key, value in values.items() if value is not None}
    if to_value is not None:
        shown["derived.TO"] = to_value
    row = GameLogRow(
        date=moment.astimezone(tz).date().isoformat() if moment else None,
        week=as_int(event.get("week")),
        round=text(event.get("eventNote")),
        home_away=home_away,
        opponent=text(as_dict(event.get("opponent")).get("abbreviation"))
        or text(as_dict(event.get("opponent")).get("displayName")),
        result=result,
        score=score,
        stats=shown,
    )
    return moment, row


@dataclass(frozen=True)
class ParsedGameLog:
    specs: tuple[Spec, ...]
    seasons: list[SeasonLog]
    present: frozenset[str] = frozenset()  # column keys this response actually carries


def visible_specs(specs: tuple[Spec, ...], present: set[str]) -> tuple[Spec, ...]:
    """Text columns to show: those whose stat category the payload has (m1), plus the derived TO.

    A non-QB has no passing category, so INT is not shown for him; a defender has no passing
    category either. Columns outside every category (NBA, MLB) always stay.
    """
    return tuple(s for s in specs if s.derived or s.category is None or s.key in present)


def parse_gamelog(raw: dict[str, Any], lg: League, tz: ZoneInfo) -> ParsedGameLog:
    """Season blocks (regular and postseason) with rows and computed totals (R3, R7, R9-R12).

    A payload with no ``names`` and no ``seasonTypes`` is a season with no games (empty, R6).
    """
    if "names" not in raw and "seasonTypes" not in raw:
        return ParsedGameLog(specs_for(lg.family, []), [], frozenset())
    columns = columns_of(raw)
    specs = specs_for(lg.family, columns)
    keys = [c.key for c in columns]
    present = set(keys)
    events = raw.get("events")
    season_types = raw.get("seasonTypes")
    if not isinstance(season_types, list):
        raise UpstreamError(MSG_UNEXPECTED)
    if events is not None and not isinstance(events, dict):
        raise UpstreamError(MSG_UNEXPECTED)
    seasons: list[SeasonLog] = []
    for block in season_types:
        block_dict = as_dict(block)
        label = text(block_dict.get("displayName"))
        if label is None:
            raise UpstreamError(MSG_UNEXPECTED)
        kind = _season_kind(label)
        if kind is None:
            continue
        games: list[tuple[datetime | None, GameLogRow]] = []
        for category in as_list(block_dict.get("categories")):
            cat = as_dict(category)
            if cat.get("type") != "event":
                continue  # "total" blocks hold ESPN's own summary; we compute our own
            for entry in as_list(cat.get("events")):
                entry_dict = as_dict(entry)
                event_id = text(entry_dict.get("eventId"))
                event = as_dict(as_dict(events).get(event_id)) if event_id else {}
                if not event:
                    raise UpstreamError(MSG_UNEXPECTED)
                games.append(
                    _game_row(
                        event,
                        keys,
                        entry_dict.get("stats"),
                        present,
                        tz,
                        derive_turnovers=lg.family == "football",
                    )
                )
        if not games:
            continue
        games.sort(key=lambda item: item[0].timestamp() if item[0] else 0.0)
        rows = [row for _, row in games]
        rows = [
            row.model_copy(update={"no_stats": True}) if no_recorded_stats(row, specs) else row
            for row in rows
        ]
        seasons.append(
            SeasonLog(
                year=_season_year(label),
                season_type=kind,
                label=label,
                game_count=len(rows),
                games=rows,
                totals=compute_totals(rows, specs),
            )
        )
    seasons.sort(key=lambda s: (s.year, s.season_type == "post"))
    return ParsedGameLog(specs, seasons, frozenset(keys))


def _is_zeroish(value: str | None) -> bool:
    """A missing cell, a numeric zero, or a made-attempted pair of zeros (``0-0``)."""
    if value is None:
        return True
    number = to_number(value)
    if number is not None:
        return number == 0
    pair = pair_parts(value)
    return pair is not None and pair == (0.0, 0.0)


def no_recorded_stats(row: GameLogRow, specs: tuple[Spec, ...]) -> bool:
    """R3: every allow-listed stat of the game is zero or missing. Says nothing about why."""
    keys = [spec.key for spec in specs]
    return bool(keys) and all(_is_zeroish(row.stats.get(key)) for key in keys)


def completeness_of(seasons: list[SeasonLog], omitted_years: list[int]) -> Completeness:
    """R2: what the log lists (games by year) and what it omits (whole seasons, oldest rows)."""
    by_season: dict[int, int] = {}
    omitted_rows: dict[int, int] = {}
    for season in seasons:
        by_season[season.year] = by_season.get(season.year, 0) + len(season.games)
        if season.games_omitted:
            omitted_rows[season.year] = omitted_rows.get(season.year, 0) + season.games_omitted
    omitted = sorted(set(omitted_years))
    partial = bool(omitted or omitted_rows)
    return Completeness(
        status="partial" if partial else "complete",
        games=sum(by_season.values()),
        by_season=dict(sorted(by_season.items())),
        omitted_seasons=omitted,
        omitted_rows=dict(sorted(omitted_rows.items())),
    )


def career_log(
    seasons: list[SeasonLog], season_type: Literal["regular", "post"], specs: tuple[Spec, ...]
) -> CareerLog | None:
    """Computed totals over the listed seasons of one type (R3, R9); the caller applies the rule
    that career totals appear only when more than one season is listed."""
    chosen = [s for s in seasons if s.season_type == season_type]
    if not chosen:
        return None
    rows = [row for season in chosen for row in season.games]
    return CareerLog(
        season_type=season_type,
        seasons=[s.year for s in chosen],
        game_count=len(rows),
        totals=compute_totals(rows, specs),
    )


# --------------------------------------------------------------------------- stats


def _team_label(slug: str | None) -> str | None:
    if slug is None:
        return None
    if slug.casefold().endswith("totals"):
        return "all teams (combined)"
    return slug.replace("-", " ").title()


def parse_stats(
    raw: dict[str, Any], season_type: Literal["regular", "post"]
) -> list[PlayerStatCategory]:
    """Season rows and ESPN's career totals by category (R2, R10).

    For ``regular`` categories named "Postseason..." are dropped (MLB returns one by default).
    """
    categories = raw.get("categories")
    if categories is None:
        return []
    if not isinstance(categories, list):
        raise UpstreamError(MSG_UNEXPECTED)
    out: list[PlayerStatCategory] = []
    for category in categories:
        cat = as_dict(category)
        name = text(cat.get("name"))
        label = text(cat.get("displayName")) or name
        if name is None or label is None:
            raise UpstreamError(MSG_UNEXPECTED)
        if season_type == "regular" and "postseason" in label.casefold():
            continue
        names = [text(n) for n in as_list(cat.get("names"))]
        labels = [text(lbl) for lbl in as_list(cat.get("labels"))]
        if not names or any(n is None for n in names) or len(labels) != len(names):
            raise UpstreamError(MSG_UNEXPECTED)
        stat_names = [str(n) for n in names]
        columns = [
            PlayerStatColumn(name=stat_name, label=str(lbl or stat_name))
            for stat_name, lbl in zip(stat_names, labels, strict=True)
        ]
        rows: list[PlayerStatRow] = []
        for item in as_list(cat.get("statistics")):
            row = as_dict(item)
            season = as_dict(row.get("season"))
            year = as_int(season.get("year"))
            if year is None:
                raise UpstreamError(MSG_UNEXPECTED)
            slug = text(row.get("teamSlug"))
            stats = {
                key: value
                for key, value in _values(stat_names, row.get("stats")).items()
                if value is not None
            }
            rows.append(
                PlayerStatRow(
                    season=year,
                    season_label=text(season.get("displayName")) or str(year),
                    team=_team_label(slug),
                    position=text(row.get("position")),
                    stats=stats,
                )
            )
        career = None
        totals = as_list(cat.get("totals"))
        if totals:
            career = {
                key: value
                for key, value in _values(stat_names, totals).items()
                if value is not None
            }
        out.append(
            PlayerStatCategory(name=name, label=label, columns=columns, rows=rows, career=career)
        )
    return out
