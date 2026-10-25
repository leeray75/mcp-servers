"""Transport-independent tool logic.

:class:`SportsService` validates arguments, fetches through the cache, normalises ESPN's
payloads and renders the compact text. It knows nothing about MCP; ``server.py`` wraps it.
"""

from __future__ import annotations

import asyncio
import json
import math
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
    CareerLog,
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
    PlayerGameLogResult,
    PlayerRef,
    PlayerSearchResult,
    PlayerStatBlock,
    PlayerStatsResult,
    ScheduleResult,
    ScoreboardResult,
    SeasonKind,
    SeasonLog,
    StandingsResult,
    TeamMatch,
    dump,
)
from sports_mcp.normalize import players as players_mod
from sports_mcp.normalize import standings as standings_mod
from sports_mcp.normalize import summary as summary_mod
from sports_mcp.normalize import teams as teams_mod
from sports_mcp.normalize.common import as_dict, as_list, get
from sports_mcp.normalize.player_stats import (
    MAX_CANDIDATES,
    SEARCH_LIMIT,
    Candidate,
    career_log,
    column_infos,
    completeness_of,
    parse_gamelog,
    parse_profile,
    parse_search,
    parse_stats,
    rank_for_listing,
    resolve,
    retry_queries,
    seasons_played,
    specs_for,
    visible_specs,
)
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
SORT_ORDERS: Final = ("time", "rank")
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
    """All tools as plain async methods."""

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
        ranked_only: bool = False,
        sort: str = "time",
    ) -> ToolOutput:
        lg = resolve_league(league)
        if status not in STATUS_FILTERS:
            raise InvalidInputError(f"status must be one of: {', '.join(STATUS_FILTERS)}.")
        if sort not in SORT_ORDERS:
            raise InvalidInputError(f"sort must be one of: {', '.join(SORT_ORDERS)}.")
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
        if ranked_only:
            games = [g for g in games if any(t.rank is not None for t in g.teams)]
        if sort == "rank":
            games = sorted(games, key=_rank_order)
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
            if ranked_only:
                if not lg.has_poll_ranks:
                    notes.append("This league's scoreboard carries no ranks.")
                elif status != "all":
                    notes.append(f"No ranked teams among the {status} games on this date.")
                else:
                    notes.append("No ranked teams play on this date.")
            if not ranked_only or not as_list(raw.get("events")):
                notes.append(empty_day_hint(raw, lg, label))

        def build(all_notes: list[str], show_records: bool) -> tuple[ScoreboardResult, str]:
            result = ScoreboardResult(
                league=lg.id,
                date=label,
                season=season_info(raw),
                games=games,
                count=len(games),
                note=" ".join(all_notes) or None,
                meta=self._meta(fetched, "scoreboard"),
            )
            return result, render.render_scoreboard(result, lg, self.tz, show_records)

        result, text = build(notes, True)
        # Records go first when the text is over the 6 KB scoreboard budget; ranks always stay.
        if len(text.encode()) > render.SCOREBOARD_TEXT_BUDGET and any(
            t.record for g in games for t in g.teams
        ):
            result, text = build([*notes, render.RECORDS_OMITTED_NOTE], False)
        return ToolOutput(self._limit(text), dump(result), lg.id, _cache_label(fetched))

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
        if basis == "game" and lg.family == "football":
            base["turnovers"] = players_mod.turnovers(players)
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

    # ------------------------------------------------------------------ player tools

    def _meta_for(self, fetches: list[Fetched], kind: str, note: str | None = None) -> Meta:
        """Freshness of a multi-call result: ``as_of`` is the oldest contributing response (R13)."""
        oldest = min(fetches, key=lambda f: f.fetched_at)
        stale = any(f.stale for f in fetches)
        stale_note = (
            "A live fetch from ESPN failed for part of this result; the last good copy is shown."
            if stale
            else None
        )
        return Meta(
            as_of=utc_iso(oldest.fetched_at),
            cache_age_seconds=max(f.age_seconds for f in fetches),
            cached=all(f.cached for f in fetches),
            endpoint_kind=kind,
            stale=True if stale else None,
            note=" ".join(x for x in (stale_note, note) if x) or None,
        )

    async def _search(
        self, lg: League, query: str, refresh: bool, fetches: list[Fetched]
    ) -> tuple[list[Candidate], bool]:
        """Search for ``query`` in ``lg``; on zero matches try the prefix retries (R5)."""

        async def search(text_query: str) -> list[Candidate]:
            fetched = await self._fetch(
                self.client.player_search_url(text_query, SEARCH_LIMIT),
                lambda _r: self.settings.ttl_player_search_seconds,
                refresh=refresh,
            )
            fetches.append(fetched)
            return parse_search(as_dict(fetched.value), lg)

        candidates = await search(query)
        if candidates:
            return candidates, False
        for retry in retry_queries(query):
            candidates = await search(retry)
            if candidates:
                return candidates, True
        return [], False

    async def _resolve_player(
        self, lg: League, player: str, refresh: bool, fetches: list[Fetched]
    ) -> tuple[PlayerRef | None, list[PlayerRef], str | None]:
        """R4: an id uses the profile; a name is searched and resolved, or left unresolved."""
        if _PLAYER_ID.fullmatch(player):
            try:
                fetched = await self._fetch(
                    self.client.player_profile_url(lg, player),
                    lambda _r: self.settings.ttl_player_search_seconds,
                    refresh=refresh,
                    id_endpoint=True,
                )
            except NotFoundError:
                raise NotFoundError(f"Athlete not found for league {lg.id}") from None
            fetches.append(fetched)
            profile = parse_profile(as_dict(fetched.value), player)
            ref = PlayerRef(
                id=profile.id,
                name=profile.name,
                team=profile.team,
                position=profile.position,
                league=lg.id,
                match="id",
            )
            return ref, [], None
        query = _player_name(player)
        candidates, fuzzy = await self._search(lg, query, refresh, fetches)
        resolution = resolve(query, candidates, fuzzy)
        if resolution.chosen is not None:
            chosen = resolution.chosen
            ref = PlayerRef(
                id=chosen.id,
                name=chosen.name,
                team=chosen.team,
                league=lg.id,
                match=resolution.match,  # type: ignore[arg-type]
            )
            return ref, [], None
        ranked = rank_for_listing(query, candidates, fuzzy)[:MAX_CANDIDATES]
        refs = [
            PlayerRef(id=c.id, name=c.name, team=c.team, league=lg.id, match=m)  # type: ignore[arg-type]
            for c, m in ranked
        ]
        if refs:
            note = "No single clear match. Call again with player=<id> using one of the ids above."
        else:
            note = f"No {lg.id} player matches '{query}'. Try another spelling."
        return None, refs, note

    async def find_player(
        self, league: str, query: str, limit: int = 5, refresh: bool = False
    ) -> ToolOutput:
        lg = resolve_league(league)
        name = _player_name(query)
        fetches: list[Fetched] = []
        candidates, fuzzy = await self._search(lg, name, refresh, fetches)
        ranked = rank_for_listing(name, candidates, fuzzy)[:limit]
        players = [
            PlayerRef(id=c.id, name=c.name, team=c.team, league=lg.id, match=m)  # type: ignore[arg-type]
            for c, m in ranked
        ]
        notes: list[str] = []
        if fuzzy and players:
            notes.append("No exact spelling found; closest matches shown.")
        if not players:
            notes.append(f"No {lg.name} player matches '{name}'. Try another spelling.")
        result = PlayerSearchResult(
            league=lg.id,
            query=name,
            players=players,
            count=len(players),
            note=" ".join(notes) or None,
            meta=self._meta_for(fetches, "player_search"),
        )
        text_out = render.fit(render.render_player_search(result, lg, self.tz), PLAYER_SEARCH_CHARS)
        return ToolOutput(text_out, dump(result), lg.id, _cache_label(*fetches))

    async def get_player_stats(
        self,
        league: str,
        player: str,
        season_type: str = "regular",
        refresh: bool = False,
    ) -> ToolOutput:
        lg = resolve_league(league)
        _season_type_kinds(season_type)
        fetches: list[Fetched] = []
        ref, candidates, note = await self._resolve_player(lg, player, refresh, fetches)
        if ref is None:
            unresolved = PlayerStatsResult(
                league=lg.id,
                resolved=False,
                candidates=candidates,
                season_type=season_type,
                note=note,
                meta=self._meta_for(fetches, "player_stats"),
            )
            text_out = render.render_player_stats(unresolved, lg, self.tz)
            return ToolOutput(text_out, dump(unresolved), lg.id, _cache_label(*fetches))
        notes: list[str] = []
        blocks: list[PlayerStatBlock] = []
        for kind in _season_type_kinds(season_type):
            fetched = await self._fetch(
                self.client.player_stats_url(lg, ref.id, kind),
                lambda _r: self.settings.ttl_player_seconds,
                refresh=refresh,
                id_endpoint=True,
            )
            fetches.append(fetched)
            categories = parse_stats(as_dict(fetched.value), kind)
            if kind == "post" and not categories:
                notes.append("No postseason stats are listed for this player.")
            blocks.append(
                PlayerStatBlock(
                    season_type=kind,
                    label="Postseason" if kind == "post" else "Regular season",
                    categories=categories,
                )
            )
        if all(not block.categories for block in blocks):
            notes.append("ESPN lists no stats for this player.")
        result = PlayerStatsResult(
            league=lg.id,
            resolved=True,
            player=ref,
            season_type=season_type,
            blocks=blocks,
            note=" ".join(notes) or None,
            meta=self._meta_for(fetches, "player_stats"),
        )
        text_out = render.fit(render.render_player_stats(result, lg, self.tz), STATS_CHARS)
        return ToolOutput(text_out, dump(result), lg.id, _cache_label(*fetches))

    async def get_player_gamelog(
        self,
        league: str,
        player: str,
        season: str = "current",
        season_type: str = "regular",
        refresh: bool = False,
    ) -> ToolOutput:
        lg = resolve_league(league)
        kinds = _season_type_kinds(season_type)
        season_arg = _season_arg(season)
        fetches: list[Fetched] = []
        ref, candidates, note = await self._resolve_player(lg, player, refresh, fetches)
        if ref is None:
            unresolved = PlayerGameLogResult(
                league=lg.id,
                resolved=False,
                candidates=candidates,
                season=season,
                season_type=season_type,
                note=note,
                meta=self._meta_for(fetches, "player_gamelog"),
            )
            text_out = render.render_player_gamelog(unresolved, lg, self.tz)
            return ToolOutput(text_out, dump(unresolved), lg.id, _cache_label(*fetches))

        years_omitted: list[int] = []
        if season_arg == "all":
            stats_fetch = await self._fetch(
                self.client.player_stats_url(lg, ref.id, "regular"),
                lambda _r: self.settings.ttl_player_seconds,
                refresh=refresh,
                id_endpoint=True,
            )
            fetches.append(stats_fetch)
            years_listed = seasons_played(as_dict(stats_fetch.value))
            limit = self.settings.player_max_seasons
            years_fetched = years_listed[:limit]
            years_omitted = years_listed[limit:]
            targets: list[int | None] = list(years_fetched)
        elif season_arg == "current":
            targets = [None]
        else:
            targets = [int(season_arg)]
        logs = await asyncio.gather(
            *(
                self._fetch(
                    self.client.player_gamelog_url(lg, ref.id, year),
                    lambda _r: self.settings.ttl_player_seconds,
                    refresh=refresh,
                    id_endpoint=True,
                )
                for year in targets
            )
        )
        lookup_calls = len(fetches) - (1 if season_arg == "all" else 0)
        fetches.extend(logs)
        parsed = [parse_gamelog(as_dict(f.value), lg, self.tz) for f in logs]
        base_specs = parsed[0].specs if parsed else specs_for(lg.family, [])
        present: set[str] = set()
        for item in parsed:
            present |= item.present
        columns = column_infos(visible_specs(base_specs, present))
        seasons = [s for p in parsed for s in p.seasons if s.season_type in kinds]
        seasons.sort(key=lambda s: (s.year, s.season_type == "post"))  # chronological
        notes: list[str] = []
        if not seasons:
            notes.append(_no_games_note(season, season_type))
        calls_note = None
        if season_arg == "all":
            calls_note = (
                f"Built from {len(fetches)} upstream calls: {lookup_calls} lookup, "
                f"1 stats, {len(logs)} game logs."
            )
        all_years = sorted({s.year for s in seasons}, reverse=True)

        def build(years: list[int], omitted: list[int]) -> PlayerGameLogResult:
            # Totals and careers are computed here, from every game of each kept season.
            chosen = [s for s in seasons if s.year in years]
            careers: list[CareerLog] = []
            if len({s.year for s in chosen}) > 1:
                for kind in kinds:
                    career = career_log(chosen, kind, base_specs)
                    if career is not None:
                        careers.append(career)
            trail = list(notes)
            if omitted:
                trail.append(_omitted_note(omitted))
            return PlayerGameLogResult(
                league=lg.id,
                resolved=True,
                player=ref,
                season=season,
                season_type=season_type,
                columns=columns,
                seasons=chosen,
                career=careers or None,
                game_count=sum(s.game_count for s in chosen),
                completeness=completeness_of(chosen, omitted),
                truncated=True if omitted else None,
                note=" ".join(trail) or None,
                meta=self._meta_for(fetches, "player_gamelog", calls_note),
            )

        years_kept = all_years if season_arg != "all" else list(years_fetched)
        omitted_all = list(years_omitted)
        result = build(years_kept, omitted_all)
        if season_arg == "all":
            # R6/R16: whole oldest seasons go first while the text or structured data is too big.
            while len(years_kept) > 1 and (
                len(render.render_player_gamelog(result, lg, self.tz)) > LOG_ALL_CHARS
                or not _structured_fits(result)
            ):
                omitted_all.insert(0, years_kept.pop())
                result = build(years_kept, omitted_all)
        result = _cap_games(result)
        text_out = render.render_player_gamelog(result, lg, self.tz)
        limit = LOG_ALL_CHARS if season_arg == "all" else LOG_SEASON_CHARS
        if len(text_out) > limit:
            # The text cap cuts the newest rows, so the completeness line must say partial too.
            cut = None
            if result.completeness is not None:
                cut = result.completeness.model_copy(update={"status": "partial"})
            result = result.model_copy(update={"truncated": True, "completeness": cut})
            text_out = render.fit(
                render.render_player_gamelog(result, lg, self.tz, text_cut=True),
                limit,
                render.GAMELOG_NOTICE,
            )
        return ToolOutput(text_out, dump(result), lg.id, _cache_label(*fetches))

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


def _rank_order(game: Game) -> tuple[int, int, int, bool, str]:
    """Ranked games first: better rank, then the other team's rank, then start time (R5).

    A game without a start time sorts last within its tier.
    """
    ranks = sorted(t.rank for t in game.teams if t.rank is not None)
    start = game.start_utc
    missing = start is None
    if not ranks:
        return (1, 0, 0, missing, start or "")
    other = ranks[1] if len(ranks) > 1 else 99  # an unranked team sorts after any ranked one
    return (0, ranks[0], other, missing, start or "")


MAX_LIMIT: Final = 500


PLAYER_SEARCH_CHARS: Final = 2000  # R16
STATS_CHARS: Final = 8000  # R16: get_player_stats text
LOG_SEASON_CHARS: Final = 8000  # R16: one season of a game log
LOG_ALL_CHARS: Final = 20000  # R16: season=all text
STRUCTURED_CHARS: Final = 60000  # R16: structured data for season=all
_PLAYER_ID: Final = re.compile(r"[0-9]{1,12}")  # R4: an ESPN athlete id
_SEASON_YEAR: Final = re.compile(r"[0-9]{4}")


def _structured_fits(result: PlayerGameLogResult) -> bool:
    """R16: structured data for a game log stays within 60 KB."""
    return len(json.dumps(dump(result), default=str)) <= STRUCTURED_CHARS


def _cap_games(result: PlayerGameLogResult) -> PlayerGameLogResult:
    """R16 structured budget: drop the oldest game rows, largest block first (M1, M2).

    Only rows go. Season totals and career totals were computed from every game before this runs,
    so they never change. Every candidate already carries the note that says which rows are not
    listed, so the size check measures the final output. Each pass drops at least one row.
    """
    if _structured_fits(result) or not result.seasons:
        return result

    omitted_years = result.completeness.omitted_seasons if result.completeness else []

    def candidate(seasons: list[SeasonLog]) -> PlayerGameLogResult:
        omitted = sum(s.games_omitted or 0 for s in seasons)
        completeness = completeness_of(seasons, omitted_years)
        if not omitted:
            return result.model_copy(update={"seasons": seasons, "completeness": completeness})
        note = (
            f"{omitted} oldest game rows are not listed to fit the 60 KB structured budget; "
            "season and career totals cover all games."
        )
        joined = " ".join(x for x in (note, result.note) if x)
        return result.model_copy(
            update={
                "seasons": seasons,
                "completeness": completeness,
                "truncated": True,
                "note": joined,
            }
        )

    seasons = list(result.seasons)
    while True:
        probe = candidate(seasons)
        size = len(json.dumps(dump(probe), default=str))
        if size <= STRUCTURED_CHARS:
            return probe
        index = max(range(len(seasons)), key=lambda i: len(seasons[i].games))
        held = seasons[index].games
        if len(held) <= 1:
            return probe  # nothing left to drop; the caller's text cap still applies
        rows = sum(len(s.games) for s in seasons)
        per_row = size / rows
        excess = size - STRUCTURED_CHARS
        drop = min(len(held) - 1, max(1, math.ceil(excess / per_row)))
        already = seasons[index].games_omitted or 0
        seasons[index] = seasons[index].model_copy(
            update={"games": held[drop:], "games_omitted": already + drop}
        )


def _player_name(value: str) -> str:
    """A player name: 1-60 letters, digits, spaces, . ' or - (no other characters reach ESPN)."""
    query = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(query) <= 60 or not re.fullmatch(r"[\w .'-]+", query) or "_" in query:
        raise InvalidInputError(
            "player must be an ESPN athlete id (digits) or a name of 1-60 letters, digits, "
            "spaces, . ' or -."
        )
    return query


def _season_type_kinds(season_type: str) -> list[SeasonKind]:
    """``regular`` | ``post`` | ``all`` -> the ESPN block kinds it selects (R7)."""
    if season_type == "all":
        return ["regular", "post"]
    if season_type == "regular":
        return ["regular"]
    if season_type == "post":
        return ["post"]
    raise InvalidInputError("season_type must be one of: regular, post, all.")


def _season_arg(season: str) -> str | int:
    """``current`` | ``all`` | a year from 1990 to 2100 (R6)."""
    if season in ("current", "all"):
        return season
    if not isinstance(season, str) or not _SEASON_YEAR.fullmatch(season):
        raise InvalidInputError("season must be 'current', 'all' or a year from 1990 to 2100.")
    year = int(season)
    if not 1990 <= year <= 2100:
        raise InvalidInputError("season must be 'current', 'all' or a year from 1990 to 2100.")
    return year


def _no_games_note(season: str, season_type: str) -> str:
    noun = "postseason games" if season_type == "post" else "games"
    when = {
        "current": "in the current season",
        "all": "in any season",
    }.get(season, f"in {season}")
    return f"No {noun} listed {when} for this player."


def _omitted_note(years: list[int]) -> str:
    """R6 wording: ``3 earlier seasons omitted (2016-2018); call with season=YYYY``."""
    low, high = min(years), max(years)
    span = str(low) if low == high else f"{low}-{high}"
    noun = "season" if len(years) == 1 else "seasons"
    return f"{len(years)} earlier {noun} omitted ({span}); call with season=YYYY."


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
