# Sports MCP server: specification

Status: draft v1 (2026-10-04). Authoritative for implementation and review. Keywords MUST / SHOULD / MAY
are used in the RFC 2119 sense. "Stretch" items are optional and only attempted after every MUST passes review.

> **Amendment pointer (2026-10-24):** the player tools `find_player`, `get_player_stats` and
> `get_player_gamelog` are specified in `docs/specs/player-stats/SPEC.md`, which governs them where it conflicts
> with this document. The tool set grows from 8 to 11 (its section 7).

## 1. Purpose

Give agents **live, compact, correctly-labelled sports data** (scores, schedules, standings, box scores with
player stats, betting lines, headlines) for many leagues, through a few typed MCP tools, so an agent never has to
scrape web pages or hand-parse 200 KB ESPN JSON.

Problems this must fix (observed with a real agent session, 2026-10-04):

1. A web scraper returned a **stale cached** scoreboard for a "live" question. Every result here MUST carry
   freshness metadata (`as_of`, `cache_age_seconds`, `source`) so staleness is visible.
2. ESPN's raw scoreboard is ~200 KB (16 NFL games) and the game summary 80-430 KB. The agent burned many calls
   writing PowerShell to parse it. Tools MUST return **compact, normalized** data, not raw payloads.
3. Betting lines are removed by ESPN once a game starts. Results MUST say so explicitly instead of silently
   mixing pre-game and live numbers or sources.
4. Different sports have different shapes (quarters, periods, innings; passing vs. batting stats). The server
   normalizes team sports into one schema with sport-specific adapters.

Non-goals: no fantasy/projections, no betting advice, no write operations, no user accounts, no web UI, no
scraping of anything other than the ESPN public JSON endpoints below, no paid APIs.

## 2. Data source

ESPN's **unofficial, undocumented** public JSON API (no key needed). It can change or break without notice; the
design must make that failure obvious and contained (see 8, 12). Base URL is configurable
(`ESPN_BASE_URL`, default `https://site.api.espn.com`). The implementer MUST confirm every endpoint by live
calls and record real responses as test fixtures (see 11). Starting points (verify; adjust if they differ):

| Need | Endpoint pattern (relative to base) |
|---|---|
| Scoreboard | `/apis/site/v2/sports/{sport}/{league}/scoreboard?dates=YYYYMMDD[&week=N&seasontype=N&limit=N]` |
| Game detail (box score, plays, odds) | `/apis/site/v2/sports/{sport}/{league}/summary?event={id}` |
| Teams | `/apis/site/v2/sports/{sport}/{league}/teams` |
| Team schedule | `/apis/site/v2/sports/{sport}/{league}/teams/{teamId}/schedule[?season=YYYY]` |
| Standings | `/apis/v2/sports/{sport}/{league}/standings[?season=YYYY]` (note `/apis/v2`, not `/apis/site/v2`) |
| News | `/apis/site/v2/sports/{sport}/{league}/news?limit=N` |

Facts already observed (2026-10-04, verify again): scoreboard works with the same URL shape for football/nfl,
basketball/nba, baseball/mlb, hockey/nhl, soccer/eng.1, soccer/usa.1, football/college-football,
basketball/mens-college-basketball, basketball/wnba; `racing/f1`, `golf/pga`, `mma/ufc` return events whose
competitors are individuals (no home/away); `tennis/atp` has a different layout. Scoreboard events carry
`competitions[0].competitors[]` (with `homeAway`, `score`, `records`, `linescores`, `leaders`, `statistics`),
`status.type.{state,detail,completed}`, `odds` only for some games (null after kickoff), `broadcasts`, `venue`,
`weather`, `situation`. Summary carries `boxscore.{teams,players}`, `scoringPlays`, `drives` or `plays`,
`winprobability`, `injuries`, `leaders`, `odds`/`pickcenter`/`againstTheSpread`, `standings`, `news`. In pre-game
summaries `boxscore.players` may hold **season** totals (seen for MLB), and may be absent (NBA, soccer): label this.

## 3. Technology and structure

Mirror `../vision-mcp-server` (read it first; its README, CLAUDE.md, Dockerfile, compose file, scripts and tests are
the house style, and `../gmail-mcp-server` is the older sibling):

- Python 3.12, official `mcp` SDK (`from mcp.server.mcpserver import MCPServer`, version resolved by `uv`, currently 2.3.0),
  `httpx`, `pydantic` + `pydantic-settings`, `starlette` + `uvicorn`, `tzdata` (slim images lack zoneinfo data).
  No other runtime dependencies without a written justification in the README.
- Layout `src/sports_mcp/`: `__main__.py`, `config.py`, `server.py`, `espn_client.py` (the ONLY module that imports
  httpx / touches the network), `cache.py`, `leagues.py` (registry), `normalize/` (shared + per-sport adapters),
  `tools.py` (transport-independent logic), `render.py` (compact text), `models.py`, `errors.py`, `security.py` and
  `logging_setup.py` (copy from vision-mcp-server, adapt names), `resources.py`.
- Hash-pinned `requirements.txt` / `requirements-dev.txt` generated with `uv pip compile --generate-hashes --universal
  --python-version 3.12`; Makefile with `install lint format typecheck test audit lock check`.
- Project root: `C:\Users\hoyen\Documents\Roo Code Workspace\mcp-servers\sports-mcp-server\`. Package `sports_mcp`,
  image `sports-mcp-server:local`, container/service `sports-mcp`, default host port **8767** (8765 = gmail, 8766 = vision).
- Streamable HTTP on `127.0.0.1` behind a bearer token (`MCP_AUTH_TOKEN`, >= 32 chars), stateless, JSON responses,
  `/healthz` unauthenticated, Host/Origin loopback guard, 64 KiB request body limit. `python -m sports_mcp --stdio`
  also supported (logs to stderr).

## 4. League registry (`leagues.py`)

Single source of truth mapping a stable `id` to ESPN `sport`/`league`, display name, `kind` (`team` | `individual`),
aliases (case-insensitive, e.g. `ncaaf`, `premier-league`, `epl`), default season-type behaviour.

MUST support (kind `team`): `nfl` (football/nfl), `college-football` (football/college-football; alias `ncaaf`),
`nba`, `wnba`, `mens-college-basketball` (`ncaam`), `womens-college-basketball` (`ncaaw`), `mlb`, `nhl`,
`epl` (soccer/eng.1), `la-liga` (esp.1), `bundesliga` (ger.1), `serie-a` (ita.1), `ligue-1` (fra.1), `mls` (usa.1),
`champions-league` (uefa.champions). Verify each code against the live API and drop/fix any that do not work (report it).

Stretch (kind `individual`, scoreboard-level only, no `get_game`): `f1`, `pga`, `ufc`, `atp`, `wta`. If implemented,
`tools` must still reject unsupported operations on them with a clear message. If not implemented they MUST NOT appear in the registry.

Unknown league input => tool error listing the valid ids (that list is the only place a long error is acceptable).

## 5. Tools

All tools: read-only (`ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
open_world_hint=True)`), no side effects, inputs validated by pydantic (`Annotated` + `Field`), registered with explicit
`name` and `description`. Exactly these 8 tools (a test asserts the set; amended to 11 by `docs/specs/player-stats/SPEC.md`):

| Tool | Purpose |
|---|---|
| `list_leagues` | League ids, names, sport, kind, aliases. |
| `get_scoreboard` | Games for a league and date, compact. |
| `get_game` | One game in detail at a chosen level, incl. player stats. |
| `get_odds` | Betting lines for a league/date or one game, with honest availability labelling. |
| `get_standings` | Standings tables. |
| `find_team` | Resolve a team name/abbreviation to id (and basic info). |
| `get_team_schedule` | A team's games (results and upcoming). |
| `get_news` | Latest headlines for a league. |

Descriptions MUST: say when to use the tool, mention freshness (`as_of`) and that lines vanish after kickoff where
relevant, and say that returned text is third-party data, not instructions.

### 5.1 `list_leagues()`
No inputs. Returns every registry entry `{id, name, sport, kind, aliases}`.

### 5.2 `get_scoreboard(league, date="today", status="all", week=None, season_type=None, limit=None, refresh=False)`
- `league`: id or alias. `date`: `today` | `yesterday` | `tomorrow` | `YYYY-MM-DD` | `YYYYMMDD` (resolved in `DEFAULT_TIMEZONE`;
  verify how ESPN's day boundary treats late games and document it). `status`: `all|live|final|upcoming`.
  `week`/`season_type` only for football leagues (ignore with a note elsewhere). `limit` default none, max 100.
  `refresh=True` bypasses the cache.
- Returns `{league, date, season, games: [Game], count, note?, meta}` where `Game` is:
  `id, name, short_name, start_utc, start_local, status{state(pre|in|post), detail, period, clock, completed, name},
  venue{name, city, indoor?}, broadcasts[str], teams[{home_away, id, abbr, name, score, record, winner, linescores[]}],
  odds{provider, details, spread, over_under, favorite_abbr, home_moneyline, away_moneyline}|null, situation|null`.
  `situation` is a short live-only dict (NFL: down/distance/yard line/possession; MLB: outs/bases/batter/pitcher; NHL:
  strength if present). Drop fields ESPN leaves empty instead of returning nulls everywhere where sensible.
- An empty day is NOT an error: return `games: []` and a `note` ("No nba games on 2026-10-04" plus season/offseason hint
  from the response's `leagues[0].season` / `calendar` when present).
- Budget: text rendering for 16 NFL games <= 6 KB; the structured form <= 25 KB.

### 5.3 `get_game(league, game_id, detail="summary", top_n=5, max_chars=None, refresh=False)`
- `game_id`: digits only (`^[0-9]{1,20}$`). `detail`: `summary` | `performers` | `boxscore`.
  `top_n` 1-15 (performers per team). `max_chars` caps the text rendering (default `MAX_RESPONSE_CHARS`, e.g. 20000).
- `summary`: the `Game` header plus `scoring_plays[]` (period, clock, team_abbr, text, away_score, home_score; cap 40,
  oldest first), `team_stats[]` (label, away, home; cap 15, most relevant first per sport), `leaders[]` (team_abbr, category,
  player, value), `injuries{abbr: [{player, position, status}]}` (cap 8 per team), `odds` + `pickcenter` open/close when
  present, `win_probability{home_pct, away_pct}` latest when present, `weather`, `attendance`.
- `performers`: summary plus `performers{abbr: [{player, position, line, stats{label:value}}]}`: the top `top_n` per team chosen
  by sport rules (NFL: top passer, rushers, receivers, defenders by tackles/sacks; NBA: by points then PRA; MLB: batters by
  H/HR/RBI, pitchers by IP/K; NHL: skaters by points then shots, goalie saves; soccer: scorers/assists/cards from `details` or
  `rosters` when available). Each entry's `line` is a one-line human summary, e.g. `13/22, 153 yds, 2 TD, 1 INT`.
- `boxscore`: summary plus full per-team player tables `{group, labels[], rows[{player, position, stats[]}]}`, truncated to
  `max_chars` with `truncated: true` and a note saying how to ask for less.
- Pre-game (`state == pre`): player stats are omitted unless clearly season stats, in which case the block carries
  `basis: "season"` (else `basis: "game"`). Never present season totals as game stats.
- Unknown game => tool error "Game not found for league X" (ESPN 404/empty), no upstream body echoed.
- Budget: `summary` text <= 8 KB; `performers` <= 12 KB (top_n=5).

### 5.4 `get_odds(league, date="today", game_id=None, refresh=False)`
- Returns per game: `id, short_name, status.state, lines{provider, details, spread, over_under, home_moneyline, away_moneyline,
  open{...}, close{...}}|null, lines_available: bool, lines_basis: "current" | "unavailable"` and a top-level `note`:
  "ESPN removes betting lines once a game starts; live in-game lines are not available from this source."
- MUST NOT mix sources: only ESPN data (scoreboard `odds`, summary `pickcenter`/`odds`/`againstTheSpread`). Spread sign convention
  MUST be explained in one sentence in the tool description (negative = favorite) and stated per game as `favorite_abbr`.
- `game_id` given => fetch the summary for just that game.
- Stretch: persist the last pre-game lines seen per game (`/data/odds_snapshots.json`, bounded size, atomic writes, volume in
  compose) and serve them for started games as `lines_basis: "snapshot"` with `captured_at`. Only if the base spec passes.

### 5.5 `get_standings(league, season=None, group=None, refresh=False)`
- `group`: optional case-insensitive filter on conference/division/group names. Returns
  `groups: [{name, entries: [{rank?, team_abbr, team, stats{...}}]}]` where `stats` uses a per-sport allowlist with stable keys
  (NFL/NBA/MLB/NHL: wins, losses, ties/otl, pct or points, points_for, points_against, diff, streak, games_behind where present;
  soccer: played, wins, draws, losses, goals_for, goals_against, goal_diff, points). Unknown stat names are dropped.
- Budget: <= 8 KB text for a full league with 30+ teams (ranked rows, short labels).

### 5.6 `find_team(league, query)`
Resolve by id, abbreviation, display name or fuzzy substring (case/diacritic-insensitive; deterministic ranking, exact beats
prefix beats substring). Returns up to 5 matches `{id, abbr, name, location, record?, standing_summary?}`. Teams come from the
`teams` endpoint, cached 24 h. No match => empty list plus a note, not an error.

### 5.7 `get_team_schedule(league, team, season=None, status="all", limit=None, refresh=False)`
`team`: id/abbr/name (resolved with the same logic; ambiguous => error listing candidates). Returns
`games: [{game_id, start_utc, start_local, opponent_abbr, home_away, status, result("W 24-17"|"L ..."|"T ..."|null), score}]`
(compact; no per-game boxscore), season info, `next_game`, `last_game`.

### 5.8 `get_news(league, limit=5)`
`limit` 1-20. Returns `[{headline, published_utc, description (<=240 chars), url}]`. URLs are data only: the server never fetches them.

### 5.9 Common result envelope
Every tool result includes `meta`: `{source: "espn", as_of (UTC ISO of the upstream fetch), cache_age_seconds, cached: bool,
endpoint_kind}` and, when relevant, `note`. `as_of` is the time ESPN was queried, not the time the response was built.
Responses are returned as:
- `content`: a **compact text rendering** (markdown-ish lines/tables, `render.py`) that is what the model actually reads;
- `structuredContent`: the typed data (pydantic `model_dump(mode="json", exclude_none=True)`).
Verify with the SDK how to return both (e.g. a `CallToolResult`). If the SDK cannot return both without duplicating text,
fall back to JSON text only and document it; measure and report sizes either way.

Example text for `get_scoreboard("nfl")` (shape, not exact):
```
NFL, Sun 2026-10-04 (as of 14:48 ET, live, cached 6s) - 16 games
FINAL  IND 30 @ WAS 13   (London, NFLN)
Q3 14:16  NE 14 @ BUF 17   CBS   BUF -6.5 O/U 44.5 (DraftKings)
Halftime  ARI 17 @ NYG 19   FOX
4:05 PM ET  MIA @ MIN   CBS   MIN -10 O/U 38.5
```

## 6. Resources (secondary, optional-for-clients)

Resources are application-controlled: many clients (including the target, Goose) may not expose them to the model.
Therefore **nothing may be reachable only through a resource**; they duplicate tool data for clients that support them.
MUST provide (read-only, static or cheap, `text/markdown` or `application/json`):

- `sports://guide`: usage notes: tool selection, freshness and caching rules, odds caveat, date semantics, example calls. The same
  essentials also go into the server `instructions` and tool descriptions.
- `sports://leagues`: the registry as JSON.
- `sports://leagues/{league}/teams` (resource template): team directory for a league (id, abbr, name), from the cached teams call.

MUST NOT add MCP prompts. Resource reads are logged without content.

## 7. Caching and freshness (`cache.py`)

In-memory TTL cache keyed by the normalized upstream URL; async-safe; in-flight request de-duplication (one upstream call for
concurrent identical requests); bounded size (e.g. 256 entries, LRU). TTLs (env-overridable):

| Data | TTL |
|---|---|
| Scoreboard containing any `in` game | 15 s (`TTL_LIVE_SECONDS`) |
| Scoreboard with only `pre`/`post` games | 120 s |
| Game summary while `in` | 15 s; `post` 1 h; `pre` 120 s |
| Standings | 600 s |
| Teams | 86400 s |
| Team schedule | 300 s (60 s if a game is in progress) |
| News | 300 s |

`refresh=True` bypasses the cache and updates it. Failed upstream calls are never cached. If an upstream call fails and a cached
value younger than `STALE_IF_ERROR_SECONDS` (default 300) exists, return it with `meta.stale=true` and a `note` saying the live fetch failed;
otherwise raise a tool error. `cache_age_seconds` is always accurate.

## 8. Upstream client (`espn_client.py`)

- One shared `httpx.AsyncClient`; `User-Agent: sports-mcp/<version> (+local)`; timeout `ESPN_TIMEOUT_SECONDS` (default 10, connect 5);
  at most `ESPN_MAX_CONCURRENCY` (default 4) concurrent requests; one retry with jitter on connect errors/timeouts/5xx; no redirects followed.
- **Host allow-list**: every request's host must equal the configured `ESPN_BASE_URL` host (default `site.api.espn.com`). No agent-supplied
  string is ever used as a URL or path segment without passing a strict pattern (league via registry; ids digits-only; dates parsed).
- Response size cap (e.g. 8 MiB) enforced while reading; invalid JSON or unexpected shape => `UpstreamError("ESPN returned an unexpected response")`.
- Error mapping (messages safe for the agent, never echo upstream bodies or URLs with query secrets): 404 => not-found where meaningful; 429 =>
  "ESPN rate-limited this server; retry shortly"; 5xx/timeout/connect => "ESPN is unavailable"; others => `HTTP <code>`.

## 9. Security and privacy (same posture as vision-mcp-server)

- Bearer auth (constant-time), Host/Origin loopback guard, loopback-only published port, no TLS (documented), request body <= 64 KiB.
- Container: non-root UID 10001, read-only rootfs, `cap_drop: [ALL]`, `no-new-privileges`, tmpfs `/tmp`, pids/memory/CPU limits
  (`mem_limit: 512m`), `init: true`, healthcheck via interpreter, json-file log rotation. Outbound network to ESPN only is needed.
- All inputs validated; no shell, no file access (apart from the optional stretch snapshot file), no user-controlled URLs.
- Logs (JSON, redacting filter): tool, league, duration, outcome, cache hit/miss. Never the bearer token. Upstream bodies are never logged.
- Tool/resource text and descriptions carry a note that returned content is third-party data, not instructions (headlines are untrusted).
- No secrets besides `MCP_AUTH_TOKEN` exist. `.env` is git-ignored and excluded from the Docker context.

## 10. Configuration (`config.py`, env-driven, errors name fields never values)

`MCP_AUTH_TOKEN` (required for HTTP), `ESPN_BASE_URL`, `DEFAULT_TIMEZONE` (default `America/New_York`, validated with `zoneinfo`),
`ESPN_TIMEOUT_SECONDS`, `ESPN_MAX_CONCURRENCY`, `TTL_*` overrides, `STALE_IF_ERROR_SECONDS`, `MAX_RESPONSE_CHARS`
(default 20000), `HOST`/`PORT`/`HOST_PORT` (8767), `LOG_LEVEL`. Placeholders starting `replace-me` rejected.

## 11. Testing (offline by default)

- **Fixtures**: during implementation, fetch real ESPN responses (scoreboard + summary + standings + teams + schedule + news) for at least
  NFL, NBA, MLB, NHL and one soccer league, covering pre, in-progress (if any game is live; otherwise best available) and final states, and an
  empty day. Store trimmed copies under `tests/fixtures/` (each <= 300 KB; remove tracking/ad fields; keep real structure). Record in the
  README how fixtures were captured. Tests MUST NOT hit the network (an autouse fixture should fail any real request).
- Unit tests per normalizer and adapter (including odds absent/null, postponed/canceled/delayed statuses, overtime/extra innings/shootout,
  ties, missing fields); date/timezone resolution including DST edges; registry/alias resolution; stat allowlists; render budgets
  (assert the size budgets above on fixtures); performers selection per sport; pre-game `basis` labelling.
- Tool-level tests through an in-memory `mcp.Client` with a fake ESPN transport (`httpx.MockTransport`); exact tool set assertion;
  annotations; input-validation rejections (bad league/game_id/date/limits, injection-looking strings such as `../`, `?x=`, newline).
- Cache tests: TTL by state, refresh bypass, in-flight dedupe, stale-if-error, no caching of failures, `cache_age_seconds`.
- Client tests: host allow-list, retries, timeout, 404/429/5xx mapping, oversize body, invalid JSON, no redirect following.
- HTTP-layer tests (adapt from vision-mcp-server): healthz, bearer matrix, Host/Origin, body-size limit, no token in logs.
- Resources: read each, template resolution, invalid league => error.
- Gate: `ruff check`, `ruff format --check`, `mypy --strict` clean; coverage >= 90% (branch); `pip-audit` clean; one opt-in
  `-m live` test module (deselected by default) that calls real ESPN for a smoke check.

## 12. Documentation and operations

- `README.md`: purpose, quick start, tools reference with example calls and abridged real outputs, resources, configuration table, caching
  and freshness, odds caveats, security model, Goose/Claude Code client snippets (do NOT edit anyone's config), troubleshooting
  (ESPN changes/429, DNS from WSL), development, how fixtures were captured, known limitations (unofficial API; no live in-game odds;
  sports not covered).
- `CLAUDE.md` with architecture and invariants (tool set exact; only `espn_client.py` imports a network library; host allow-list; budgets).
- `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh` + `.ps1` twins (copy and adapt from vision-mcp-server; `setup`
  only needs the port and token; `smoke` calls `list_leagues`, a scoreboard, a game, standings and news over HTTP and prints sizes).
  Docker Engine lives inside WSL2 Ubuntu; scripts run there.
- `.env.example`, `.gitignore`, `.dockerignore`, `.gitattributes`, `Dockerfile` (multi-stage, hash-pinned, `--only-binary`), `docker-compose.yml`.
- `git init` the project; do not commit and do not touch any other project or Goose's config.

## 13. Acceptance criteria

1. `make check` equivalent passes in WSL (`ruff`, `mypy --strict`, tests with >= 90% coverage), `pip-audit` clean.
2. `scripts/start.sh` builds, the container becomes healthy, runs read-only/non-root/cap-dropped, bound to `127.0.0.1:8767`.
3. `scripts/smoke.sh` against **live ESPN** passes: `list_leagues`; `get_scoreboard` for nfl, nba, mlb, nhl, epl (empty days handled); `get_game`
   at all three detail levels for one real finished or live game in at least two different sports; `get_standings` for three leagues;
   `find_team` + `get_team_schedule`; `get_news`; `get_odds`. Print the byte size of each response.
4. Size budgets in 5.x met on real data (report measured numbers).
5. Security tests prove: unauthenticated 401, bad Host 421, invalid inputs rejected before any upstream call, only the allow-listed host is
   ever contacted, nothing sensitive in logs.
6. Documentation complete and accurate (every claim in the README verified against the running server).
7. Every `MUST` in this document is either satisfied or listed in the implementation report as a deviation with the reason.

## 14. Phasing

Phase 1 (all MUSTs): team sports, 8 tools, resources, cache, container, docs. Phase 2 (stretch, only after Phase 1 is reviewed clean):
individual sports scoreboards (f1, pga, ufc, atp/wta) and the odds snapshot. Phase 2 items that are not finished MUST be absent, not half-working.
