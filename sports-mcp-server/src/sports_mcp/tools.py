"""Transport-independent tool logic.

:class:`SportsService` validates arguments, fetches through the cache, normalises ESPN's
payloads and renders the compact text. It knows nothing about MCP; ``server.py`` wraps it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sports_mcp import render
from sports_mcp.cache import Fetched, TTLCache
from sports_mcp.config import Settings
from sports_mcp.dates import resolve_date, utc_iso
from sports_mcp.errors import InvalidInputError, NotFoundError, SportsMcpError, UpstreamError
from sports_mcp.espn_client import EspnClient
from sports_mcp.leagues import LEAGUES, League, resolve_league
from sports_mcp.models import (
    FindTeamResult,
    Game,
    GameDetail,
    LeagueInfo,
    LeaguesResult,
    Meta,
    NewsResult,
    OddsGame,
    OddsResult,
    OddsStatus,
    ScheduleResult,
    ScoreboardResult,
    StandingsResult,
    TeamMatch,
    dump,
)
from sports_mcp.normalize import players as players_mod
from sports_mcp.normalize import standings as standings_mod
from sports_mcp.normalize import summary as summary_mod
from sports_mcp.normalize import teams as teams_mod
from sports_mcp.normalize.common import as_dict, as_list, get
from sports_mcp.normalize.scoreboard import (
    empty_day_hint,
    has_live_game,
    normalize_event,
    normalize_scoreboard,
    season_info,
)
from sports_mcp.normalize.sports import team_stats

_GAME_ID: Final = re.compile(r"^[0-9]{1,20}$")
ODDS_NOTE: Final = (
    "ESPN removes betting lines once a game starts; live in-game lines are not available from "
    "this source."
)
STATUS_FILTERS: Final = ("all", "live", "final", "upcoming")
DETAIL_LEVELS: Final = ("summary", "performers", "boxscore")


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """Result of one tool call: model-facing text plus the structured form."""

    text: str
    data: dict[str, Any]
    league: str | None = None
    cache: str = "n/a"  # hit | miss | stale | n/a


def _cache_label(*fetches: Fetched) -> str:
    if any(f.stale for f in fetches):
        return "stale"
    if all(f.cached for f in fetches):
        return "hit"
    return "miss"


class SportsService:
    """All eight tools as plain async methods."""

    def __init__(
        self,
        settings: Settings,
        client: EspnClient,
        cache: TTLCache | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.client = client
        self.cache = (
            cache
            if cache is not None
            else TTLCache(
                settings.cache_max_entries,
                settings.stale_if_error_seconds,
                settings.cache_max_mb * 1024 * 1024,
            )
        )
        self._now = now
        self.tz = settings.tz

    # ------------------------------------------------------------------ plumbing

    async def _fetch(
        self,
        url: str,
        ttl: Callable[[Any], float],
        *,
        refresh: bool,
        id_endpoint: bool = False,
    ) -> Fetched:
        return await self.cache.get(
            url,
            lambda: self.client.get_json(url, id_endpoint=id_endpoint),
            ttl,
            refresh=refresh,
        )

    def _meta(self, fetched: Fetched, kind: str, note: str | None = None) -> Meta:
        stale_note = None
        if fetched.stale:
            stale_note = (
                "The live fetch from ESPN failed; this is the last good copy "
                f"({fetched.age_seconds}s old)."
            )
        return Meta(
            as_of=utc_iso(fetched.fetched_at),
            cache_age_seconds=fetched.age_seconds,
            cached=fetched.cached,
            endpoint_kind=kind,
            stale=True if fetched.stale else None,
            note=" ".join(x for x in (stale_note, note) if x) or None,
        )

    def _limit(self, text: str) -> str:
        return render.fit(text, self.settings.max_response_chars)

    # TTL policies ---------------------------------------------------------------

    def _ttl_scoreboard(self, raw: Any) -> float:
        s = self.settings
        return s.ttl_live_seconds if has_live_game(as_dict(raw)) else s.ttl_scoreboard_idle_seconds

    def _ttl_summary(self, raw: Any) -> float:
        s = self.settings
        state = get(raw, "header", "competitions", 0, "status", "type", "state")
        if state == "in":
            return s.ttl_live_seconds
        if state == "post":
            return s.ttl_summary_post_seconds
        return s.ttl_summary_pre_seconds

    def _ttl_schedule(self, raw: Any) -> float:
        s = self.settings
        return (
            s.ttl_schedule_live_seconds
            if teams_mod.schedule_has_live(as_dict(raw))
            else s.ttl_schedule_seconds
        )

    # ------------------------------------------------------------------ list_leagues

    async def list_leagues(self) -> ToolOutput:
        result = LeaguesResult(
            leagues=[
                LeagueInfo(
                    id=lg.id, name=lg.name, sport=lg.sport, kind=lg.kind, aliases=list(lg.aliases)
                )
                for lg in LEAGUES
            ],
            count=len(LEAGUES),
        )
        return ToolOutput(render.render_leagues(result), dump(result))

    # ------------------------------------------------------------------ get_scoreboard

    async def get_scoreboard(
        self,
        league: str,
        date: str = "today",
        status: str = "all",
        week: int | None = None,
        season_type: int | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> ToolOutput:
        lg = resolve_league(league)
        if status not in STATUS_FILTERS:
            raise InvalidInputError(f"status must be one of: {', '.join(STATUS_FILTERS)}.")
        day = resolve_date(date, self.tz, self._now())
        notes: list[str] = []
        use_week = week is not None
        if (week is not None or season_type is not None) and not lg.has_weeks:
            notes.append("week/season_type apply only to football leagues and were ignored.")
            use_week = False
        if season_type is not None and week is None and lg.has_weeks:
            notes.append("season_type only applies together with week and was ignored.")
        if use_week:
            url = self.client.scoreboard_url(lg, week=week, season_type=season_type)
            label = f"week {week}" + (f" (season type {season_type})" if season_type else "")
            notes.append("A week request ignores the date argument.")
        else:
            url = self.client.scoreboard_url(lg, date=day.strftime("%Y%m%d"))
            label = day.isoformat()
        fetched = await self._fetch(url, self._ttl_scoreboard, refresh=refresh)
        raw = as_dict(fetched.value)
        games = normalize_scoreboard(raw, lg, self.tz)
        if status != "all":
            wanted = {"live": "in", "upcoming": "pre"}.get(status)
            games = [
                g
                for g in games
                if (
                    g.status.state == wanted
                    if wanted
                    else g.status.state == "post" and g.status.completed
                )
            ]
        total = len(games)
        cap = limit if limit is not None else MAX_SCOREBOARD_GAMES
        if total > cap:
            games = games[:cap]
            if limit is None:
                notes.append(
                    f"{total} games match; showing the first {cap}. "
                    f"Pass limit=<n> (max {MAX_LIMIT}) or filter with status to see others."
                )
        if not games:
            notes.append(empty_day_hint(raw, lg, label))
        result = ScoreboardResult(
            league=lg.id,
            date=label,
            season=season_info(raw),
            games=games,
            count=len(games),
            note=" ".join(notes) or None,
            meta=self._meta(fetched, "scoreboard"),
        )
        return ToolOutput(
            self._limit(render.render_scoreboard(result, lg, self.tz)),
            dump(result),
            lg.id,
            _cache_label(fetched),
        )

    # ------------------------------------------------------------------ get_game

    async def get_game(
        self,
        league: str,
        game_id: str,
        detail: str = "summary",
        top_n: int = 5,
        max_chars: int | None = None,
        refresh: bool = False,
    ) -> ToolOutput:
        lg = resolve_league(league)
        if not isinstance(game_id, str) or not _GAME_ID.fullmatch(game_id):
            raise InvalidInputError("game_id must be digits only (up to 20 digits).")
        if detail not in DETAIL_LEVELS:
            raise InvalidInputError(f"detail must be one of: {', '.join(DETAIL_LEVELS)}.")
        cap = max_chars or self.settings.max_response_chars
        url = self.client.summary_url(lg, game_id)
        try:
            fetched = await self._fetch(url, self._ttl_summary, refresh=refresh, id_endpoint=True)
        except NotFoundError:
            raise NotFoundError(f"Game not found for league {lg.id}") from None
        raw = as_dict(fetched.value)
        game = summary_mod.summary_game(raw, lg, self.tz)
        if game is None:
            raise NotFoundError(f"Game not found for league {lg.id}")
        if len(game.teams) < 2:
            raise UpstreamError("ESPN returned an unexpected response")
        state = game.status.state
        box = as_dict(raw.get("boxscore"))
        pre = state == "pre"

        leaders = summary_mod.leaders(raw)
        if pre and leaders:
            leaders = [
                ld.model_copy(update={"category": f"Season {ld.category}"}) for ld in leaders
            ]
        odds = summary_mod.summary_odds(raw, game)
        notes: list[str] = []
        base: dict[str, Any] = {
            "league": lg.id,
            "detail": detail,
            "game": game,
            "scoring_plays": summary_mod.scoring_plays(raw, lg, game),
            "team_stats": None if pre else team_stats(lg.family, box.get("teams")),
            "leaders": leaders,
            "injuries": summary_mod.injuries(raw),
            "odds": odds,
            "odds_basis": ("current" if pre else "closing") if odds else None,
            "win_probability": None if pre else summary_mod.win_probability(raw),
            "weather": summary_mod.weather(raw),
            "attendance": summary_mod.attendance(raw),
            "meta": self._meta(fetched, "summary"),
        }

        players, rosters = box.get("players"), raw.get("rosters")
        basis = None
        if detail != "summary":
            usable = (
                lg.family != "soccer" and players_mod.has_numeric_stats(players)
                if pre
                else bool(players or rosters)
            )
            if usable:
                basis = "season" if pre else "game"
            else:
                notes.append(
                    "Player stats are not available before the game starts."
                    if pre
                    else "ESPN published no player stats for this game."
                )
        base["basis"] = basis

        if basis and detail == "performers":
            base["performers"] = players_mod.top_performers(lg.family, players, rosters, top_n)
        text = ""
        if basis and detail == "boxscore":
            tables = players_mod.box_tables(lg.family, players, rosters)
            result, text = self._fit_boxscore(base, tables, notes, cap, lg)
        else:
            base["note"] = " ".join(notes) or None
            result = GameDetail(**base)
            text = render.render_game(result, lg, self.tz)
        return ToolOutput(render.fit(text, cap), dump(result), lg.id, _cache_label(fetched))

    def _fit_boxscore(
        self,
        base: dict[str, Any],
        tables: dict[str, list[Any]] | None,
        notes: list[str],
        cap: int,
        lg: League,
    ) -> tuple[GameDetail, str]:
        """Render the full box score, trimming rows per group until it fits ``cap`` characters."""

        def build(row_cap: int | None, truncated: bool) -> tuple[GameDetail, str]:
            shown = tables
            if tables is not None and row_cap is not None:
                shown = {
                    abbr: [g.model_copy(update={"rows": g.rows[:row_cap]}) for g in groups]
                    for abbr, groups in tables.items()
                }
            extra = list(notes)
            if truncated:
                extra.append(
                    f"Box score truncated to {row_cap} rows per group to fit max_chars={cap}; "
                    "use detail='performers', or raise max_chars."
                )
            detail = GameDetail(
                **{
                    **base,
                    "boxscore": shown,
                    "truncated": True if truncated else None,
                    "note": " ".join(extra) or None,
                }
            )
            return detail, render.render_game(detail, lg, self.tz)

        result, text = build(None, False)
        if len(text) <= cap or not tables:
            return result, text
        longest = max(len(g.rows) for groups in tables.values() for g in groups)
        low, high, best = 0, longest, None
        while low <= high:
            mid = (low + high) // 2
            candidate = build(mid, True)
            if len(candidate[1]) <= cap:
                best, low = candidate, mid + 1
            else:
                high = mid - 1
        return best if best is not None else build(0, True)

    # ------------------------------------------------------------------ get_odds

    async def get_odds(
        self,
        league: str,
        date: str = "today",
        game_id: str | None = None,
        refresh: bool = False,
    ) -> ToolOutput:
        lg = resolve_league(league)
        games: list[OddsGame] = []
        if game_id is not None:
            if not isinstance(game_id, str) or not _GAME_ID.fullmatch(game_id):
                raise InvalidInputError("game_id must be digits only (up to 20 digits).")
            url = self.client.summary_url(lg, game_id)
            try:
                fetched = await self._fetch(
                    url, self._ttl_summary, refresh=refresh, id_endpoint=True
                )
            except NotFoundError:
                raise NotFoundError(f"Game not found for league {lg.id}") from None
            raw = as_dict(fetched.value)
            game = summary_mod.summary_game(raw, lg, self.tz)
            if game is None:
                raise NotFoundError(f"Game not found for league {lg.id}")
            lines = summary_mod.summary_odds(raw, game)
            games.append(_odds_game(game, lines))
            label: str | None = None
            kind = "summary"
        else:
            day = resolve_date(date, self.tz, self._now())
            label = day.isoformat()
            url = self.client.scoreboard_url(lg, date=day.strftime("%Y%m%d"))
            fetched = await self._fetch(url, self._ttl_scoreboard, refresh=refresh)
            raw = as_dict(fetched.value)
            for event in as_list(raw.get("events")):
                game = normalize_event(as_dict(event), lg, self.tz, full_odds=True)
                if game is not None:
                    games.append(_odds_game(game, game.odds))
            kind = "scoreboard"
        note = ODDS_NOTE
        if (
            any(g.status.state != "pre" and not g.lines_available for g in games)
            and game_id is None
        ):
            note += (
                " For a game that already started, pass its game_id: ESPN's game summary may "
                "still carry the pre-game closing line."
            )
        result = OddsResult(
            league=lg.id,
            date=label,
            games=games,
            count=len(games),
            note=note,
            meta=self._meta(fetched, kind),
        )
        return ToolOutput(
            self._limit(render.render_odds(result, lg, self.tz)),
            dump(result),
            lg.id,
            _cache_label(fetched),
        )

    # ------------------------------------------------------------------ get_standings

    async def get_standings(
        self,
        league: str,
        season: int | None = None,
        group: str | None = None,
        refresh: bool = False,
    ) -> ToolOutput:
        lg = resolve_league(league)
        needle = None
        if group is not None:
            needle = group.strip().casefold()
            if not needle or len(needle) > 60 or not re.fullmatch(r"[\w .'&-]+", needle):
                raise InvalidInputError("group must be 1-60 letters, digits or spaces.")
        url = self.client.standings_url(lg, season)
        fetched = await self._fetch(
            url, lambda _r: self.settings.ttl_standings_seconds, refresh=refresh
        )
        raw = as_dict(fetched.value)
        groups = standings_mod.normalize_standings(raw, lg.family, "college" in lg.id)
        names = [g.name for g in groups]
        notes: list[str] = []
        if needle:
            groups = [
                g
                for g in groups
                if needle in g.name.casefold() or needle == (g.abbr or "").casefold()
            ]
            if not groups:
                notes.append(f"No group matches '{group}'. Available groups are listed below.")
        truncated = None
        available = None
        total = sum(len(g.entries) for g in groups)
        if total > MAX_STANDINGS_ENTRIES:
            kept: list[Any] = []
            used = 0
            for g in groups:
                if used + len(g.entries) > MAX_STANDINGS_ENTRIES and kept:
                    break
                kept.append(g)
                used += len(g.entries)
            groups, truncated = kept, True
            notes.append(
                f"Large league: showing {len(groups)} of {len(names)} groups. "
                "Pass group=<name> to see a specific conference/division."
            )
        if truncated or not groups:
            available = names
        result = StandingsResult(
            league=lg.id,
            season=standings_mod.standings_season(raw) or season,
            season_name=standings_mod.standings_season_name(raw),
            groups=groups,
            available_groups=available,
            truncated=truncated,
            note=" ".join(notes) or None,
            meta=self._meta(fetched, "standings"),
        )
        return ToolOutput(
            self._limit(render.render_standings(result, lg, self.tz)),
            dump(result),
            lg.id,
            _cache_label(fetched),
        )

    # ------------------------------------------------------------------ teams

    async def _teams(self, lg: League, refresh: bool) -> tuple[list[teams_mod.TeamRec], Fetched]:
        url = self.client.teams_url(lg)
        fetched = await self._fetch(
            url, lambda _r: self.settings.ttl_teams_seconds, refresh=refresh
        )
        return teams_mod.directory(as_dict(fetched.value)), fetched

    async def team_directory(self, league: str) -> list[teams_mod.TeamRec]:
        """Team list for the ``sports://leagues/{league}/teams`` resource."""
        lg = resolve_league(league)
        teams, _ = await self._teams(lg, False)
        return teams

    async def find_team(self, league: str, query: str, refresh: bool = False) -> ToolOutput:
        lg = resolve_league(league)
        teams_mod.validate_query(query)
        teams, fetched = await self._teams(lg, refresh)
        ranked = teams_mod.rank_matches(teams, query)[: teams_mod.MAX_MATCHES]
        matches: list[TeamMatch] = [t.match() for _, t in ranked]
        if matches and (len(matches) == 1 or ranked[0][0] <= 2):
            try:
                detail = await self._fetch(
                    self.client.team_url(lg, matches[0].id),
                    lambda _r: self.settings.ttl_schedule_seconds,
                    refresh=refresh,
                    id_endpoint=True,
                )
                record, standing = teams_mod.team_extras(as_dict(detail.value))
                matches[0] = matches[0].model_copy(
                    update={"record": record, "standing_summary": standing}
                )
            except SportsMcpError:
                pass  # enrichment is optional
        result = FindTeamResult(
            league=lg.id,
            query=query.strip(),
            matches=matches,
            note=None if matches else f"No {lg.id} team matches '{query.strip()}'.",
            meta=self._meta(fetched, "teams"),
        )
        return ToolOutput(
            self._limit(render.render_find_team(result, lg, self.tz)),
            dump(result),
            lg.id,
            _cache_label(fetched),
        )

    # ------------------------------------------------------------------ get_team_schedule

    async def get_team_schedule(
        self,
        league: str,
        team: str,
        season: int | None = None,
        status: str = "all",
        limit: int | None = None,
        refresh: bool = False,
        season_type: int | None = None,
    ) -> ToolOutput:
        lg = resolve_league(league)
        if status not in STATUS_FILTERS:
            raise InvalidInputError(f"status must be one of: {', '.join(STATUS_FILTERS)}.")
        teams_mod.validate_query(team)
        teams, _ = await self._teams(lg, refresh)
        rec = teams_mod.resolve_one(teams, team)
        if rec is None:
            raise InvalidInputError(
                f"No {lg.id} team matches '{team.strip()}'. Use find_team to look it up."
            )
        url = self.client.schedule_url(lg, rec.id, season, season_type)
        try:
            fetched = await self._fetch(url, self._ttl_schedule, refresh=refresh, id_endpoint=True)
        except NotFoundError:
            raise NotFoundError(f"Schedule not found for {rec.name}") from None
        raw = as_dict(fetched.value)
        notes: list[str] = []
        if lg.family == "soccer":
            # ESPN's soccer schedule lists only played matches unless fixture=true is sent.
            try:
                upcoming = await self._fetch(
                    self.client.schedule_url(lg, rec.id, season, season_type, fixture=True),
                    self._ttl_schedule,
                    refresh=refresh,
                    id_endpoint=True,
                )
                seen = {str(as_dict(e).get("id")) for e in as_list(raw.get("events"))}
                extra = [
                    e
                    for e in as_list(as_dict(upcoming.value).get("events"))
                    if str(as_dict(e).get("id")) not in seen
                ]
                raw = {**raw, "events": as_list(raw.get("events")) + extra}
            except SportsMcpError:
                notes.append("Upcoming fixtures could not be fetched; only played matches shown.")
        games = teams_mod.schedule_games(raw, rec.id, self.tz)
        next_game = next(
            (g for g in games if g.status.state in {"pre", "in"} and not g.status.completed), None
        )
        last_game = next((g for g in reversed(games) if g.status.completed), None)
        if status != "all":
            wanted = {"live": "in", "upcoming": "pre"}.get(status)
            games = [
                g
                for g in games
                if (
                    g.status.state == wanted
                    if wanted
                    else g.status.state == "post" and g.status.completed
                )
            ]
        if limit is not None:
            games = games[-limit:] if status == "final" else games[:limit]
        if not games:
            notes.append(f"No {status} games found for {rec.name} in this season.")
        note = " ".join(notes) or None
        result = ScheduleResult(
            league=lg.id,
            team=rec.match(),
            season=teams_mod.schedule_season(
                raw, requested=season is not None or season_type is not None
            ),
            games=games,
            count=len(games),
            next_game=next_game,
            last_game=last_game,
            note=note,
            meta=self._meta(fetched, "schedule"),
        )
        return ToolOutput(
            self._limit(render.render_schedule(result, lg, self.tz)),
            dump(result),
            lg.id,
            _cache_label(fetched),
        )

    # ------------------------------------------------------------------ get_news

    async def get_news(self, league: str, limit: int = 5, refresh: bool = False) -> ToolOutput:
        lg = resolve_league(league)
        url = self.client.news_url(lg, limit)
        fetched = await self._fetch(url, lambda _r: self.settings.ttl_news_seconds, refresh=refresh)
        articles = teams_mod.news_items(as_dict(fetched.value), limit)
        result = NewsResult(
            league=lg.id,
            articles=articles,
            count=len(articles),
            note=None if articles else f"ESPN lists no {lg.id} headlines right now.",
            meta=self._meta(fetched, "news"),
        )
        return ToolOutput(
            self._limit(render.render_news(result, lg, self.tz)),
            dump(result),
            lg.id,
            _cache_label(fetched),
        )


MAX_STANDINGS_ENTRIES: Final = 100
MAX_SCOREBOARD_GAMES: Final = 100
MAX_LIMIT: Final = 500


def _odds_game(game: Game, lines: Any) -> OddsGame:
    state = game.status.state
    if lines is None:
        basis = "unavailable"
    elif state == "pre":
        basis = "current"
    else:
        basis = "closing"
    return OddsGame(
        id=game.id,
        short_name=game.short_name,
        status=OddsStatus(state=state, detail=game.status.detail),
        start_utc=game.start_utc,
        lines=lines,
        lines_available=lines is not None,
        lines_basis=basis,  # type: ignore[arg-type]
    )
