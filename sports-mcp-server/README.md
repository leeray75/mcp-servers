# sports-mcp-server

An MCP server that gives agents **live, compact, correctly-labelled sports data** (scores, schedules,
standings, box scores with player stats, betting lines, headlines) for 15 leagues, through eleven typed,
read-only tools. It talks to ESPN's unofficial public JSON API, normalises the ~200 KB payloads into a
few hundred bytes of text, and stamps every answer with when ESPN was actually asked, so a stale cache is
visible instead of silently wrong.

- Python 3.12, official `mcp` SDK (`MCPServer`), `httpx`, `pydantic`, `starlette`/`uvicorn`.
- Streamable HTTP on `127.0.0.1:8767`, bearer-token protected, stateless; `python -m sports_mcp --stdio` also works.
- Runs in Docker inside WSL2 (read-only rootfs, non-root, all capabilities dropped). Same house style as the
  sibling `../vision-mcp-server` and `../gmail-mcp-server`.
- No API key. The only outbound traffic is HTTPS to `site.api.espn.com`.

## Why it exists

1. A web scraper once returned a **stale cached** scoreboard for a "live" question. Every result here carries
   `meta.as_of` (when ESPN was queried), `meta.cache_age_seconds`, `meta.cached` and `meta.source`.
2. ESPN's raw NFL scoreboard is ~220 KB (16 games) and a game summary 80-1000 KB. The tools return compact
   text (about 1.5 KB for a full NFL slate) instead of making the agent write parsing scripts.
3. ESPN removes betting lines once a game starts. `get_odds` says so, per game, instead of mixing numbers.
4. Sports differ (quarters, periods, innings; passing vs. batting). One normalised schema, per-sport adapters.

Non-goals: fantasy/projections, betting advice, write operations, accounts, a web UI, scraping anything other
than ESPN's public JSON endpoints.

## Quick start

All commands run in WSL2 Ubuntu (Docker Engine lives there). Windows users can run the `.ps1` twins
(`.\scripts\start.ps1`), which forward into WSL.

```bash
scripts/setup.sh     # interactive: writes .env (port, generated bearer token, time zone) - or write .env by hand
scripts/start.sh     # builds the image, starts the container, waits until healthy, prints the Goose snippet
scripts/smoke.sh     # live end-to-end check over HTTP with response sizes
```

Other scripts: `stop.sh`, `restart.sh [--recreate]` (needed after editing `.env`), `rebuild.sh [--keep-cache]`,
`logs.sh [-n N] [--no-follow]`, `status.sh`. `.env` holds the bearer token and is git-ignored and excluded from
the Docker context; scripts never print its values.

Minimal `.env`:

```
MCP_AUTH_TOKEN=<openssl rand -hex 32>
HOST_PORT=8767
```

### Client configuration

The client must send `Authorization: Bearer <MCP_AUTH_TOKEN>`. This repository does not edit anyone's client
config; add one of these yourself.

Goose (`extensions:` in `config.yaml`):

```yaml
  sports:
    name: sports
    description: Live scores, schedules, standings, box scores and betting lines (ESPN)
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8767/mcp
    headers:
      Authorization: "Bearer <MCP_AUTH_TOKEN from your .env>"
    timeout: 60
```

Claude Code:

```bash
claude mcp add --transport http sports http://127.0.0.1:8767/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN from your .env>"
```

stdio (no token needed, logs go to stderr): `python -m sports_mcp --stdio`.

## Leagues

`list_leagues` returns the registry. Ids (aliases in parentheses, all case-insensitive):

| id | league | ESPN path |
|---|---|---|
| `nfl` | NFL | football/nfl |
| `college-football` (`ncaaf`, `cfb`) | NCAA FBS | football/college-football |
| `nba` | NBA | basketball/nba |
| `wnba` | WNBA | basketball/wnba |
| `mens-college-basketball` (`ncaam`, `ncaab`, `cbb`) | NCAA men's | basketball/mens-college-basketball |
| `womens-college-basketball` (`ncaaw`) | NCAA women's | basketball/womens-college-basketball |
| `mlb` | MLB | baseball/mlb |
| `nhl` | NHL | hockey/nhl |
| `epl` (`premier-league`, `eng.1`, `pl`) | English Premier League | soccer/eng.1 |
| `la-liga` | La Liga | soccer/esp.1 |
| `bundesliga` | Bundesliga | soccer/ger.1 |
| `serie-a` | Serie A | soccer/ita.1 |
| `ligue-1` | Ligue 1 | soccer/fra.1 |
| `mls` | MLS | soccer/usa.1 |
| `champions-league` (`ucl`) | UEFA Champions League | soccer/uefa.champions |

All 15 were verified against the live API on 2026-10-04 (scoreboard, teams, news and standings). Individual
sports (F1, golf, UFC, tennis) are not implemented and are therefore not in the registry.

## Tools

Exactly eleven tools, all read-only (`readOnlyHint`, idempotent, non-destructive, open-world). Each result has a
compact **text** rendering in `content` (what the model reads) and the typed data in `structuredContent`
(nulls and empty strings are dropped). Descriptions tell the agent when to use the tool, mention freshness and
that returned text is third-party data, not instructions.

Every result carries:

```json
"meta": {"source": "espn", "as_of": "2026-10-04T19:55:30Z", "cache_age_seconds": 0,
         "cached": false, "endpoint_kind": "scoreboard"}
```

`as_of` is when ESPN was queried, not when the answer was built. If ESPN fails and a copy younger than
`STALE_IF_ERROR_SECONDS` exists it is served with `meta.stale: true` and a note; otherwise the call errors.

### `list_leagues()`

Registry with `id, name, sport, kind, aliases`. No network call.

### `get_scoreboard(league, date="today", status="all", week=None, season_type=None, limit=None, refresh=False)`

Games for one league and day. `date`: `today|yesterday|tomorrow|YYYY-MM-DD|YYYYMMDD` resolved in
`DEFAULT_TIMEZONE`. `status`: `all|live|final|upcoming` (`final` means completed; postponed or canceled games only
show under `all`). `week` (1-25) and `season_type` (1 pre, 2 regular, 3 post, 4 off) work for football leagues only
(ignored with a note elsewhere); a week request ignores `date`. `season_type` without `week` is ignored with a note.
`limit` 1-500 (without it, at most 100 games are returned, with a note saying how many matched). Games whose kickoff
time ESPN has not confirmed show `TBD` (`time_tbd: true`, no `start_local`); postponed or canceled games are counted
separately from finals and carry no placeholder score. Playoff notes (e.g. "Semifinals - Game 1") appear in the text.
Scoring plays use `OT`/`OT2` labels past regulation (hockey: `OT2/SO` from the second extra period, since ESPN does
not say shootout). An empty day is not an error: `games: []` plus a note with the season window.

Day boundary: ESPN groups events by **US Eastern date** (a 10 pm ET start belongs to that ET date even though it
is the next day in UTC). The server passes the calendar date it resolved from your time zone unchanged, so for the
default `America/New_York` the two agree; for other zones, late-evening games can land on the neighbouring day.
Without a date, ESPN returns the *current week* for football and the *next matchday* for soccer, so the server
always sends an explicit date.

Abridged real output (`get_scoreboard("nfl")`, 2026-10-04, mid-afternoon):

```
NFL, 2026-10-04 (as of 15:55:30 EDT, fetched just now) - 14 games (8 live, 1 final, 5 upcoming)
Q4 11:07  NE 21 @ BUF 20  CBS  2nd & 2 at NE 34, NE ball
Q4 8:17   LAR 10 @ PHI 20  FOX  3rd & Goal at LAR 2, PHI ball, red zone
FINAL     IND 30 @ WSH 13  NFL Net
4:05 PM EDT MIA @ MIN  FOX  MIN -10 O/U 38.5 ML +400/-535 (DraftKings)  Minneapolis
ids: NE @ BUF=401872971, ..., MIA @ MIN=401872974
```

Structured `games[]` entries hold `id, name, short_name, start_utc, start_local, status{state pre|in|post, detail,
period, clock, completed, name}, venue, broadcasts, teams[{home_away, id, abbr, name, score, record, winner,
linescores, hits/errors for baseball}]`, `odds{provider, details, favorite_abbr, spread, over_under,
home_moneyline, away_moneyline}` (pre-game only) and a short live-only `situation` (NFL down/distance/possession;
MLB outs/bases/batter/pitcher; NHL special strength when ESPN reports it).

### `get_game(league, game_id, detail="summary", top_n=5, max_chars=None, refresh=False)`

`game_id` is digits only (from `get_scoreboard`). `detail`:

- `summary`: header, scoring plays (oldest first, max 40; skipped for basketball), allow-listed team stats (max 15),
  leaders, injuries (max 8 per team), the pre-game line with open/close, win probability, weather, attendance.
- `performers`: summary plus the top `top_n` (1-15) players per team chosen by sport rules with one-line stat lines.
- `boxscore`: summary plus full per-team player tables, trimmed to `max_chars` (default 20000) with
  `truncated: true` and a note.

Before the game starts player stats are omitted unless they are clearly season totals (MLB), in which case the
block carries `basis: "season"` and the text says "SEASON stats, not this game". Season leaders are prefixed
"Season". Team stats are omitted before the game. Unknown game: `Game not found for league <id>`.

Abridged real output (`get_game("nfl", "401872965", "performers")`):

```
NFL game 401872965 (as of 15:40:33 EDT, fetched just now)
Indianapolis Colts 30 @ Washington Commanders 13  [FINAL]
Tottenham Hotspur Stadium, London; TV NFL Net; att. 60,907
Closing lines (pre-game, retained by ESPN): IND -4.5 O/U 46.5 ML -225/+185 (DraftKings)
Scoring:
 Q1 12:57 WSH Drew Stevens 31 Yd Field Goal (0-3)
 ...
Top performers:
  IND:
    Daniel Jones: 19/34, 143 yds, 1 INT
    Jonathan Taylor: 20 car, 95 yds, 2 TD
```

### `get_odds(league, date="today", game_id=None, refresh=False)`

Per game: `id, short_name, status{state}, lines{provider, details, favorite_abbr, spread, over_under,
home_moneyline, away_moneyline, draw_moneyline (soccer), open{}, close{}}`, `lines_available`, `lines_basis`.
Top-level note: *ESPN removes betting lines once a game starts; live in-game lines are not available from this
source.* Only ESPN data is used.

- **Spread sign:** `spread` is the favorite's handicap (negative = favorite gives points) and `favorite_abbr` names
  the team. ESPN's raw `spread` is the home team's number and its `details` string means a spread in football and
  basketball but a moneyline in baseball, hockey and soccer, so the server derives the favorite from the spread
  side (then the shorter moneyline, then ESPN's flags). Moneylines are listed away/home.
- `lines_basis`: `current` (pre-game line from the scoreboard/summary), `closing` (a started or finished game whose
  summary still carries the pre-game closing line; only when `game_id` is given), `unavailable`.
  `closing` is an addition to the specified `current|unavailable` so that retained pre-game lines are never
  presented as live ones. ESPN's summary also carries a "live" moneyline snapshot for in-progress games; the
  server deliberately ignores it because its freshness is unknowable.

### `get_standings(league, season=None, group=None, refresh=False)`

Groups of ranked rows with allow-listed stats under stable keys: pro leagues `wins, losses, ties, otl, pct, points,
points_for, points_against, diff, streak, games_behind, played`; soccer `played, wins, draws, losses, goals_for,
goals_against, goal_diff, points`. NFL and MLB are returned by division (`group="AFC East"`, `"National League West"`),
others by conference or table. `group` is a case-insensitive substring of the group name or its exact abbreviation
(`ACC`). Large leagues (college) show the first groups up to 100 rows with a hint and the list of group names; college
tables also show the conference record (`conf`), which is what the order follows. `season` is the year as ESPN labels
it: for two-year seasons the END year (`2026` = 2025-26); results name the season (`NBA standings 2026-27`). The label is
taken from the table itself (ESPN's top-level season can already be next year, e.g. MLB right after the playoffs).

### `find_team(league, query)`

Resolves a team id, abbreviation, name, location or nickname (case- and diacritic-insensitive; exact beats prefix
beats substring; ties by name). Up to 5 matches; record and standing summary are added for an unambiguous best
match. No match is an empty list and a note, not an error. The team list is cached 24 h.

### `get_team_schedule(league, team, season=None, status="all", limit=None, refresh=False, season_type=None)`

`team` as for `find_team`; an ambiguous name is an error listing candidates. Returns compact `games[]`
(`game_id, start_utc, start_local, opponent_abbr, home_away, status, result "W 24-17", score, label`), `next_game`
and `last_game`. ESPN serves one season type at a time (MLB shows just the postseason while it is on); the
header names it (`2026 Postseason`) and `season_type` (1 pre, 2 regular, 3 post) asks for another. For soccer ESPN
lists only played matches by default, so the server also requests `?fixture=true` and merges the upcoming matches
(if that second call fails, a note says only played matches are shown). Unconfirmed kickoff times show `TBD`. `limit` keeps the latest N for `status="final"` and the
first N otherwise.

### `get_news(league, limit=5)`

`limit` 1-20. Returns `headline, published_utc, description (<= 240 chars), url`. URLs are data only; the server
never fetches them.

### `find_player(league, query, limit=5, refresh=False)`

Players in one league by name (`query`: 1-60 letters, digits, spaces, `. ' -`). Up to `limit` (1-10) results with
`id`, `name` and `team`. Misspellings are retried with shorter prefixes and ranked by edit distance; a fuzzy result is
marked `match: "fuzzy"`. An empty result is not an error.

### `get_player_stats(league, player, season_type="regular", refresh=False)`

Season-by-season rows per stat category, plus ESPN's own career totals row (unmodified). `player` is an ESPN athlete
id (digits) or a name. A name resolves when one candidate clearly matches; otherwise the result has `resolved: false`
and candidates (call again with an `id`). `season_type`: `regular`, `post` or `all`.

### `get_player_gamelog(league, player, season="current", season_type="regular", refresh=False)`

One row per game (date, week or playoff round, home/away, opponent, result and score from the player's team's point
of view, stats) with totals computed from the listed games. `season`: `current`, a year (NBA `2025` means 2024-25) or
`all` (the whole career, up to `PLAYER_MAX_SEASONS` seasons, oldest dropped first). Career totals appear when more than
one season is listed. The first line under the header is always a completeness line: `Completeness: complete - ...`,
or `PARTIAL - ...` naming the omitted seasons or rows. A game with no recorded stats stays in the table and gets a note
under its season (no reason is given). Missing and season-to-date cells show `n/a`; `-` is only a minus sign. A player
given by name works directly; `find_player` is only needed to browse candidates. Football shows fumbles, fumbles lost, interceptions thrown and TO (interceptions thrown + fumbles
lost; defensive interceptions are never counted). MLB season-to-date rate cells are not shown per game, and the totals
row recomputes AVG from hits and at-bats. Settings: `TTL_PLAYER_SEARCH_SECONDS`, `TTL_PLAYER_SECONDS` and
`PLAYER_MAX_SEASONS` (see `.env.example`). Design: `docs/specs/player-stats/SPEC.md`.

## Resources (secondary)

Resources are application-controlled and many clients (including Goose) may not show them to the model, so nothing
is reachable *only* through a resource.

- `sports://guide` (markdown): tool selection, freshness and caching, odds caveat, dates, examples.
- `sports://leagues` (JSON): the registry.
- `sports://leagues/{league}/teams` (template, JSON): `id, abbr, name` for each team (cached 24 h).

No MCP prompts are registered. Resource reads are logged without content.

## Configuration

All environment-driven (`.env` via compose). Errors name the field, never the value.

| Variable | Default | Meaning |
|---|---|---|
| `MCP_AUTH_TOKEN` | required for HTTP | Bearer token, >= 32 chars; `replace-me*` placeholders rejected |
| `HOST_PORT` | `8767` | Host port published on 127.0.0.1 only (container listens on 8000) |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | In-container bind (compose fixes these) |
| `LOG_LEVEL` | `INFO` | JSON logs to stdout (stderr in stdio mode) |
| `ESPN_BASE_URL` | `https://site.api.espn.com` | The only host ever contacted |
| `ESPN_TIMEOUT_SECONDS` | `10` | Total timeout (connect timeout 5) |
| `ESPN_MAX_CONCURRENCY` | `4` | Concurrent upstream requests |
| `ESPN_MAX_RESPONSE_BYTES` | `12582912` | Upstream body cap (college standings are ~6 MB) |
| `DEFAULT_TIMEZONE` | `America/New_York` | IANA zone for `today`/`tomorrow`, `start_local` and rendering |
| `MAX_RESPONSE_CHARS` | `20000` | Cap on tool text |
| `TTL_LIVE_SECONDS` | `15` | Scoreboard with a live game; game summary while live |
| `TTL_SCOREBOARD_IDLE_SECONDS` | `120` | Scoreboard with only pre/post games |
| `TTL_SUMMARY_PRE_SECONDS` / `TTL_SUMMARY_POST_SECONDS` | `120` / `3600` | Game summary |
| `TTL_STANDINGS_SECONDS` | `600` | Standings |
| `TTL_TEAMS_SECONDS` | `86400` | Team lists |
| `TTL_SCHEDULE_SECONDS` / `TTL_SCHEDULE_LIVE_SECONDS` | `300` / `60` | Team schedule (shorter while a game is live) |
| `TTL_NEWS_SECONDS` | `300` | News |
| `STALE_IF_ERROR_SECONDS` | `300` | Max age of a copy served when ESPN fails |
| `CACHE_MAX_ENTRIES` | `256` | LRU bound on entry count |
| `CACHE_MAX_MB` | `96` | LRU bound on approximate resident size of cached payloads (parsed JSON is ~4x its serialised size); container limit is 512 MiB |

## Caching and freshness

In-memory TTL cache keyed by the normalised upstream URL; async-safe with in-flight de-duplication (concurrent
identical requests make one upstream call); LRU-bounded by both entry count and approximate bytes (a payload larger than the byte budget is served but not kept); failures are never cached. The TTL is chosen from the
fetched data (a scoreboard containing a live game expires after 15 s). `refresh=true` bypasses the cache and
updates it. `cache_age_seconds` is always accurate and `as_of` is the upstream fetch time.

## Odds caveats

- ESPN drops betting lines when a game starts. Started games have `lines_available: false` on `get_odds(date)`; the
  summary-based `get_odds(game_id)` can still return the **pre-game closing** line, labelled `closing`.
- Lines come from the provider ESPN lists first (DraftKings at the time of writing). Not every game has one
  (the NBA preseason games checked had none).
- No betting advice is given or implied.

## Security model

- Bearer auth on every route except `GET /healthz` (constant-time compare, identical 401 for every failure),
  Host/Origin loopback guard (421/403), 64 KiB request body limit, stateless JSON responses.
- Container: UID 10001, read-only rootfs, `cap_drop: [ALL]`, `no-new-privileges`, tmpfs `/tmp`, pids/memory/CPU
  limits, `init: true`, interpreter-based healthcheck, rotated json-file logs. Published on `127.0.0.1` only; there
  is **no TLS** (loopback only; do not expose the port).
- Inputs are validated by pydantic before any upstream call; league comes from the registry, ids are digits-only,
  dates are parsed, team/group text matches a strict character class. No agent-supplied string becomes a URL
  or path. Every request host is checked against `ESPN_BASE_URL`'s host; redirects are not followed.
- Upstream errors are mapped to safe messages (`ESPN is unavailable`, `ESPN rate-limited this server; retry
  shortly`, `HTTP <code>`); upstream bodies are never echoed or logged. Logs hold tool, league, duration,
  outcome and cache hit/miss only, with a redaction filter as a safety net.
- Third-party text (team names, headlines) is marked as data, not instructions, in descriptions and output.
- The only secret is `MCP_AUTH_TOKEN`. On the `/mnt/c` checkout POSIX file modes are not enforced for `.env`.

## Troubleshooting

- **`ESPN is unavailable`**: ESPN's undocumented API is down or changed. `scripts/logs.sh -n 50` shows `upstream
  retry` entries. A copy under 5 minutes old is served meanwhile (`meta.stale`). If a field you need disappeared,
  re-capture fixtures (below) and compare.
- **`ESPN rate-limited this server`**: back off; results are cached, so repeated identical calls are cheap. Raise
  `TTL_*` or lower `ESPN_MAX_CONCURRENCY` if you hit this regularly.
- **DNS or connect errors from WSL**: check `wsl.exe -e getent hosts site.api.espn.com`; restart WSL
  (`wsl --shutdown`) if its resolver is stuck, and make sure Docker's daemon has network access.
- **Container unhealthy / not starting**: `scripts/status.sh`, `scripts/logs.sh --no-follow`. A bad `.env` makes the
  process exit with a field-named message.
- **401 from the client**: the `Authorization` header must be exactly `Bearer <token from .env>`; after editing
  `.env` run `scripts/restart.sh --recreate`.
- **Wrong day**: see the day-boundary note above; pass an explicit `YYYY-MM-DD`.

## Development

```bash
make install VENV=~/.venvs/sports-mcp PYTHON=python3   # hash-pinned dev install (Python >= 3.12)
make check VENV=~/.venvs/sports-mcp     # ruff + mypy --strict + pytest (coverage gate 90%, branch)
make lock VENV=~/.venvs/sports-mcp      # re-resolve and re-hash requirements*.txt with uv
make audit VENV=~/.venvs/sports-mcp     # pip-audit on the locked runtime dependencies
pytest -m live --no-cov                 # opt-in smoke test against real ESPN (deselected by default)
python tools/try_live.py scoreboard nfl # print the real rendering and sizes for one call
```

Keep the virtualenv off the Windows drive (`~/.venvs/...` in WSL). Runtime dependencies: `mcp`, `httpx`,
`pydantic`, `pydantic-settings`, `starlette`, `uvicorn`, and `tzdata` (slim images ship no IANA zone data, so
`zoneinfo` needs it); nothing else.

### Fixtures

`tests/fixtures/*.json` are **real ESPN responses** captured on 2026-10-04 with `tools/capture_fixtures.py`
(stdlib only: `python tools/capture_fixtures.py [name-filter]`). The script drops tracking, link, image and
play-by-play fields, keeps only scoring plays from `plays`, trims standings split records and shortens team lists,
but leaves ESPN's structure intact. Each file is under 300 KB. Tests never touch the network: an autouse fixture
fails any request that reaches httpx's real transport, and everything else goes through `httpx.MockTransport`.
Covered: NFL (live, final, pre, a full 16-game slate, an empty day), NBA, MLB (including a 10-inning game), NHL,
EPL, WNBA (live). No NHL/MLB/NBA game was live at capture time; the MLB situation and NHL strength adapters are
tested with hand-built dicts shaped like ESPN's documented fields.

## Known limitations

- ESPN's API is unofficial and undocumented; it can change or break without notice (tests cover the shapes
  captured on 2026-10-04 only).
- No live in-game odds. No fantasy data, projections, play-by-play or video.
- Individual sports (F1, golf, UFC, tennis) are not covered.
- `get_team_schedule` returns whichever season type ESPN serves (not always the whole year).
- Player lines for football are prefixed with their group (`pass:`, `rush:`, `rec:`, `def:`, `K:`) because one player can appear in two groups.
- Argument-validation failures are returned as one short line (`Invalid arguments for get_game: game_id: ...`).
- Soccer box scores come from rosters (goals, assists, shots, cards, saves); ESPN publishes no richer table.
- Basketball scoring plays are omitted (every basket is a scoring play).
- Each response carries the data twice (compact text and structured content); clients that forward both pay for
  both. Text sizes are what the model reads by default.
- Standings and schedules for past seasons depend on what ESPN still serves; the `season` argument is passed through unchanged.
- Player search results carry no position; position comes from the athlete profile, which id lookups use.
- Player game logs for hockey and soccer use a generic column list (no fixtures). NBA shooting percentages are kept in
  structured data only. NBA preseason games are ignored.
