"""Teams directory, team resolution, schedules and news."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sports_mcp.errors import InvalidInputError
from sports_mcp.leagues import fold
from sports_mcp.models import NewsItem, ScheduleGame, SeasonInfo, TeamMatch
from sports_mcp.normalize.common import (
    as_dict,
    as_int,
    as_list,
    get,
    normalize_status,
    ordered_teams,
    score_value,
    text,
    times,
)

MAX_MATCHES: Final = 5
_QUERY: Final = re.compile(r"^[\w .'&-]{1,60}$")
DESCRIPTION_MAX: Final = 240


@dataclass(frozen=True, slots=True)
class TeamRec:
    id: str
    abbr: str | None
    name: str  # display name, e.g. "Buffalo Bills"
    location: str | None
    nickname: str | None
    short_name: str | None
    slug: str | None

    def match(self) -> TeamMatch:
        return TeamMatch(id=self.id, abbr=self.abbr, name=self.name, location=self.location)


def directory(raw: dict[str, Any]) -> list[TeamRec]:
    """Team list from ESPN's ``/teams`` response."""
    out: list[TeamRec] = []
    for sport in as_list(raw.get("sports")):
        for lg in as_list(as_dict(sport).get("leagues")):
            for item in as_list(as_dict(lg).get("teams")):
                team = as_dict(as_dict(item).get("team"))
                team_id = text(team.get("id"))
                name = text(team.get("displayName")) or text(team.get("name"))
                if team_id and name:
                    out.append(
                        TeamRec(
                            id=team_id,
                            abbr=text(team.get("abbreviation")),
                            name=name,
                            location=text(team.get("location")),
                            nickname=text(team.get("nickname")) or text(team.get("name")),
                            short_name=text(team.get("shortDisplayName")),
                            slug=text(team.get("slug")),
                        )
                    )
    return out


def validate_query(query: str) -> str:
    cleaned = query.strip() if isinstance(query, str) else ""
    if not _QUERY.fullmatch(cleaned):
        raise InvalidInputError(
            "Invalid team query: use 1-60 letters, digits, spaces or . ' & - characters."
        )
    return cleaned


def _rank(team: TeamRec, needle: str) -> int | None:
    """0 = id, 1 = abbreviation, 2 = exact name, 3 = prefix, 4 = substring; ``None`` = no match."""
    if needle == team.id:
        return 0
    if team.abbr and fold(team.abbr) == needle:
        return 1
    names = [fold(n) for n in (team.name, team.location, team.nickname, team.short_name) if n]
    if team.slug:
        names.append(fold(team.slug.replace("-", " ")))
    if needle in names:
        return 2
    if any(n.startswith(needle) or any(w.startswith(needle) for w in n.split()) for n in names):
        return 3
    if any(needle in n for n in names):
        return 4
    return None


def rank_matches(teams: list[TeamRec], query: str) -> list[tuple[int, TeamRec]]:
    """Deterministic ranking: exact beats prefix beats substring; ties by name."""
    needle = fold(validate_query(query))
    scored = [(r, t) for t in teams if (r := _rank(t, needle)) is not None]
    scored.sort(key=lambda pair: (pair[0], fold(pair[1].name), pair[1].id))
    return scored


def resolve_one(teams: list[TeamRec], query: str) -> TeamRec | None:
    """The single best team, ``None`` if nothing matches; raises if the best tier is ambiguous."""
    scored = rank_matches(teams, query)
    if not scored:
        return None
    best = scored[0][0]
    tier = [t for r, t in scored if r == best]
    if len(tier) > 1:
        options = ", ".join(f"{t.name} ({t.abbr or t.id})" for t in tier[:8])
        raise InvalidInputError(f"Ambiguous team '{query.strip()}'. Did you mean: {options}?")
    return tier[0]


def team_extras(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """(record, standing summary) from ESPN's single-team response."""
    team = as_dict(raw.get("team"))
    record = None
    for item in as_list(get(team, "record", "items", default=[])):
        item = as_dict(item)
        if item.get("type") == "total":
            record = text(item.get("summary"))
    return record, text(team.get("standingSummary"))


# ---------------------------------------------------------------- schedule


def _result(own: int | None, other: int | None, winner: Any, completed: bool) -> str | None:
    if not completed or own is None or other is None:
        return None
    if winner is True or own > other:
        return f"W {own}-{other}"
    if winner is False and own < other:
        return f"L {own}-{other}"
    if own == other:
        return f"T {own}-{other}"
    return f"L {own}-{other}"


def schedule_game(event: dict[str, Any], team_id: str, tz: ZoneInfo) -> ScheduleGame | None:
    comp = as_dict(get(event, "competitions", 0))
    if not comp:
        return None
    competitors = ordered_teams(comp.get("competitors"))
    mine = next((c for c in competitors if text(as_dict(c.get("team")).get("id")) == team_id), None)
    other = next((c for c in competitors if c is not mine), None)
    status = normalize_status(comp.get("status") or event.get("status"))
    start_utc, start_local = times(event.get("date") or comp.get("date"), tz)
    tbd = event.get("timeValid") is False or comp.get("timeValid") is False
    if tbd:
        start_local = None
    own = score_value(mine.get("score")) if mine else None
    opp = score_value(other.get("score")) if other else None
    score = (
        f"{own}-{opp}" if own is not None and opp is not None and status.state != "pre" else None
    )
    return ScheduleGame(
        game_id=str(event.get("id") or comp.get("id") or ""),
        start_utc=start_utc,
        start_local=start_local,
        opponent_abbr=text(as_dict(as_dict(other).get("team")).get("abbreviation")),
        home_away=text(mine.get("homeAway")) if mine else None,
        status=status,
        result=_result(own, opp, mine.get("winner") if mine else None, status.completed),
        score=score,
        label=text(get(event, "week", "text")),
        time_tbd=True if tbd else None,
    )


def schedule_games(raw: dict[str, Any], team_id: str, tz: ZoneInfo) -> list[ScheduleGame]:
    games = [
        g
        for e in as_list(raw.get("events"))
        if isinstance(e, dict)
        for g in [schedule_game(e, team_id, tz)]
        if g is not None
    ]
    games.sort(key=lambda g: g.start_utc or "")
    return games


def schedule_season(raw: dict[str, Any], requested: bool = False) -> SeasonInfo | None:
    """Season of the events: ``requestedSeason`` when a season was asked for, else ``season``."""
    season = as_dict(raw.get("requestedSeason") if requested else raw.get("season"))
    year = as_int(season.get("year"))
    name = text(season.get("name"))
    if year is None and name is None:
        return None
    return SeasonInfo(year=year, type=name, name=text(season.get("displayName")))


def schedule_has_live(raw: dict[str, Any]) -> bool:
    return any(
        get(e, "competitions", 0, "status", "type", "state") == "in"
        or get(e, "status", "type", "state") == "in"
        for e in as_list(raw.get("events"))
    )


# ---------------------------------------------------------------- news


def news_items(raw: dict[str, Any], limit: int) -> list[NewsItem]:
    items: list[NewsItem] = []
    for article in as_list(raw.get("articles")):
        article = as_dict(article)
        headline = text(article.get("headline"))
        if not headline:
            continue
        description = text(article.get("description"))
        if description and len(description) > DESCRIPTION_MAX:
            description = description[: DESCRIPTION_MAX - 1].rstrip() + "…"
        published, _ = times(article.get("published"), ZoneInfo("UTC"))
        url = text(get(article, "links", "web", "href"))
        items.append(
            NewsItem(headline=headline, published_utc=published, description=description, url=url)
        )
        if len(items) >= limit:
            break
    return items
