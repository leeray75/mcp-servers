# Implementation report: sports-mcp-server

Date: 2026-10-04. Scope: Phase 1 of `docs/SPEC.md` (all MUSTs). Phase 2 stretch items were not attempted and are
absent (no individual-sport leagues in the registry, no odds snapshot file).

## 1. Summary

| Item | Result |
|---|---|
| Tests | 387 offline tests pass, plus 2 opt-in live tests (`pytest -m live`, also pass) |
| Coverage | 98.3% (branch coverage, gate 90%) |
| Static checks | `ruff check`, `ruff format --check`, `mypy --strict` (39 files, src and tests) clean |
| `pip-audit` | no known vulnerabilities in the locked runtime requirements |
| Container | `sports-mcp` running and healthy on `127.0.0.1:8767`; non-root 10001, read-only rootfs, `cap_drop ALL`, `no-new-privileges`, `init`, 512 MB / 128 pids; image 181 MB |
| Smoke | `scripts/smoke.sh` passes against live ESPN (sizes in section 5) |

## 2. What was built, per spec section

| Spec | Built | Where |
|---|---|---|
| 1 Purpose | Freshness metadata on every result; compact normalised output; honest odds labelling; per-sport adapters | `tools.py`, `normalize/`, `render.py` |
| 2 Data source | All endpoints probed live and recorded as fixtures; base URL configurable | `espn_client.py`, `tests/fixtures/` |
| 3 Technology/structure | Layout exactly as specified (`__main__, config, server, espn_client, cache, leagues, normalize/, tools, render, models, errors, security, logging_setup, resources`) plus `dates.py`. Hash-pinned `requirements*.txt` (uv, `--universal`), Makefile `install lint format typecheck test audit lock check`. Image `sports-mcp-server:local`, service `sports-mcp`, port 8767, stateless streamable HTTP + JSON, `/healthz`, Host/Origin guard, 64 KiB body limit, `--stdio` | whole tree |
| 4 League registry | 15 team leagues with aliases; each ESPN code verified live (all 15 returned scoreboard, teams, news, standings); unknown league error lists valid ids | `leagues.py` |
| 5.1-5.8 Tools | Exactly 8 tools, read-only annotations, explicit names/descriptions, pydantic `Annotated`/`Field` validation, descriptions state when to use, freshness, odds caveat and third-party-data warning | `server.py`, `tools.py` |
| 5.9 Envelope | `meta{source, as_of, cache_age_seconds, cached, endpoint_kind[, stale, note]}` on every data result; `content` = compact text only, `structuredContent` = typed data (via `CallToolResult`, no duplication in `content`) | `server.py`, `models.py` |
| 6 Resources | `sports://guide` (markdown), `sports://leagues` (JSON), `sports://leagues/{league}/teams` (template). No prompts. Reads logged without content | `resources.py` |
| 7 Caching | TTL by data/state as specified, LRU 256, in-flight dedupe, `refresh`, stale-if-error with `meta.stale`, failures never cached, accurate `cache_age_seconds`; all TTLs env-overridable | `cache.py`, `tools.py`, `config.py` |
| 8 Upstream client | One shared `AsyncClient`, UA `sports-mcp/0.1.0 (+local)`, 10 s/5 s connect timeouts, concurrency 4, one jittered retry on connect/timeout/5xx, no redirects, host allow-list, streaming size cap, invalid JSON/shape handling, safe error mapping | `espn_client.py` |
| 9 Security | Bearer (constant time), Host/Origin guard, loopback publish, hardened container, validated inputs, redacting JSON logs (tool, league, duration, outcome, cache), untrusted-text notes, `.env` ignored and excluded from the Docker context | `security.py`, `logging_setup.py`, `docker-compose.yml` |
| 10 Configuration | All variables in the spec plus `ESPN_MAX_RESPONSE_BYTES`, `CACHE_MAX_ENTRIES`; secret-free errors; `replace-me*` rejected; `DEFAULT_TIMEZONE` validated | `config.py` |
| 11 Testing | Real captured fixtures (37 files, 2.3 MB, each < 300 KB), network guard, all test categories listed in the spec, opt-in live module | `tests/` |
| 12 Docs/ops | README, CLAUDE.md, scripts `setup start stop restart rebuild logs status smoke` as `.sh` plus `.ps1` twins (status twin exercised from Windows PowerShell), `.env.example`, `.gitignore`, `.dockerignore`, `.gitattributes`, Dockerfile (multi-stage, hash-pinned, `--only-binary`), compose; `git init` only (nothing committed) | repo root |

### Probe results worth knowing (2026-10-04)

- All 15 leagues work with the spec's URL shapes; standings need `/apis/v2` and, for NFL/MLB, `?level=3` to get divisions.
- `teams` returns only 50 teams unless `?limit=1000` is sent (college leagues have 360-760); the client always sends it.
- Without `dates`, ESPN's scoreboard returns the whole current *week* for football and the *next matchday* for soccer, so
  the server always sends an explicit date.
- College basketball's default scoreboard shows only featured games (2 vs 148 on a busy day); the registry sends `groups=50`.
- Day boundary: events at 02:00Z belong to the previous US Eastern date (checked with MLB 2026-09-30 and NHL 2026-10-03).
- Unknown game ids give 404 or 400 depending on length; both map to "Game not found for league X".
- ESPN's `spread` is the *home* team's number; `details` means a spread in football/basketball but a moneyline
  in baseball/hockey/soccer; `favorite` flags can be both true for a near pick'em. The server derives the favorite
  from the spread side (then moneyline, then flags).
- Summaries for started games still carry the pre-game `pickcenter` (open/close), plus a "live" moneyline snapshot that
  the server ignores. Scoreboards drop `odds` for started games (confirmed on 8 live NFL games).
- MLB pre-game summaries really do carry season totals in `boxscore.players` (labelled `basis: "season"`).
- NBA preseason games had no odds at all.

## 3. Deviations from the spec (with reasons)

1. **`lines_basis` has a third value `closing`** (spec: `current | unavailable`). ESPN's summary retains the pre-game
   closing line after kickoff; labelling it `current` would imply it is live, and `unavailable` would discard real
   data. It is only returned for started/finished games and is explained in the tool description.
2. **Upstream body cap is 12 MiB** (`ESPN_MAX_RESPONSE_BYTES`, spec "e.g. 8 MiB"). College standings are ~6.2 MB, the largest
   seen; MLB summaries are ~1 MB.
3. **Default scoreboard cap of 100 games** when `limit` is omitted (spec: "default none, max 100"). A busy college-basketball day has 148
   games; the cap keeps structured output bounded and a note says how many matched. `limit` still caps at 100.
4. **College-basketball scoreboards send `groups=50`** (not in the spec) so they cover all of Division I.
5. **`win_probability` is returned for in-progress and finished games**, omitted before the game (it is `None` in ESPN pre-game data anyway).
6. **Pre-game `team_stats` are always omitted.** The spec requires never presenting season totals as game stats; ESPN does not
   mark which team-stat values are season-level in pre-game payloads. Player blocks are kept only when they contain numeric
   values (then `basis: "season"`).
7. **Basketball has no `scoring_plays`.** In ESPN every basket is a scoring play (109 in one game), so the spec's cap-40 list would be
   meaningless; leaders and team stats cover the story. Soccer goals are built from `header.competitions[0].details` with a running score.
8. **Standings for very large leagues are truncated** to 100 rows with a hint and the list of group names (college leagues, > 360 rows).
   Group filtering matches a case-insensitive substring of the group name or its exact abbreviation.
9. **Tool errors carry the SDK's prefix** (`Error executing tool get_game: Game not found for league nfl`); the specified text is intact after the prefix. Unknown extra
   arguments are ignored by the SDK rather than rejected.
10. **`get_odds` per-game `status` is `{state, detail}`** (spec: `status.state`); `draw_moneyline` was added for soccer.
11. **Spread sign is normalised to the favorite's handicap** (spec: "negative = favorite" and per-game `favorite_abbr`). ESPN's own raw spread is the home number; this is documented in the tool description and README.
12. **`httpx` request logging is muted** (it logs URLs at INFO); spec only requires that upstream bodies are not logged, this is stricter.
13. **Team queries and standings groups may not contain `/` or `?`** (character class `[\w .'&-]`), so injection-looking strings are rejected with a clear message.
14. **Phase 2 stretch items are absent** (spec section 14 allows this): no F1/PGA/UFC/ATP/WTA, no odds snapshot volume.

No MUST in the spec was left unimplemented.

## 4. Test inventory

387 tests (one process, ~21 s): `test_normalize` 69, `test_server` 87 (tool surface, schemas, injection-style rejections before any upstream
call, error mapping, log hygiene, resources, HTTP auth/Host/Origin/body limit), `test_tools` 74, `test_espn_client` 31 (host allow-list, retry, 404/429/5xx
mapping, redirects, oversize and invalid JSON, concurrency, network-import invariant), `test_leagues` 25, `test_render` 25 (size budgets on real fixtures), `test_dates` 21
(including DST edges), `test_config` 18, `test_logging` 11, `test_security` 11, `test_cache` 10, `test_main` 5; 2 live (`tests/test_live.py`).
Coverage by module is >= 94% everywhere (lowest: `normalize/players.py` 94%, `render.py` 96%).

Fixtures (real ESPN captures, trimmed by `tools/capture_fixtures.py`): NFL scoreboard for a full week with 8 live, 2 final and 6 pre-game events; an empty NFL day; NFL
summaries live/final/pre; NBA pre/final; MLB pre/final (10 innings); NHL scoreboards (today; a 13-game final day with OT/shootout games) and a final summary; EPL pre/final; WNBA live scoreboard and summary; standings for NFL, NBA,
NHL, MLB, EPL and college football (2 conferences); teams, a team detail, schedules (NFL, MLB, EPL) and news.

## 5. Measured response sizes (budget vs measured)

Text is what the model reads; "structured" is `structuredContent` JSON. Fixture rows are from `tests/` data; the live rows are from
`scripts/smoke.sh` against ESPN on 2026-10-04 (text / whole JSON-RPC response).

| Output | Budget | Measured (text) | Measured (structured) |
|---|---|---|---|
| `get_scoreboard("nfl")`, 16 games (fixture) | text <= 6 KB, structured <= 25 KB | 1,470 B | 11,977 B |
| `get_scoreboard("nfl")`, 14 games (live) | same | 1,243-1,259 B | 11.7-11.9 KB (whole response) |
| `get_game` summary (NFL final / live / pre) | <= 8 KB | 2,151 / 2,067 / 1,188 B | 4.7 / 4.5 / 2.7 KB |
| `get_game` summary, other sports (NBA, MLB, NHL, EPL, WNBA) | <= 8 KB | 1,036-1,768 B | 2.5-3.8 KB |
| `get_game` performers, top_n=5 (NFL, NBA, MLB, NHL, EPL) | <= 12 KB | 1,317-2,572 B | 3.7-6.0 KB |
| `get_game` boxscore (largest: NHL, NFL) | <= `max_chars` (20,000) | 7,131 B (NHL), 6,887 B (NFL) | 10.5-11.4 KB |
| `get_standings` full league (NFL 32, NBA 30, MLB 30, NHL 32, EPL 20 teams) | <= 8 KB | 705-1,757 B | 3.8-6.4 KB |
| `get_odds` NFL day | - | 1,564 B | 5.4 KB |
| `get_team_schedule` NFL (17 games) | - | 1,286 B | 5.5 KB |
| `get_news` (5) | - | 1,618 B | 1.9 KB |
| `list_leagues` | - | 1,134 B | 1.7 KB |
| `tools/list` (all 8 descriptions and schemas) | - | - | 10.5 KB |

For comparison ESPN's raw payloads: NFL scoreboard ~219 KB, NFL final summary 660 KB, MLB final summary 1.0 MB, college standings 6.2 MB.
Roughly 99% of the bytes are removed from the text the model reads. Clients that forward `structuredContent` as well pay for both forms.

## 6. Commands

```bash
# WSL, from the project root; venv outside the Windows drive
uv venv ~/.venvs/sports-mcp --python 3.12
VIRTUAL_ENV=~/.venvs/sports-mcp ~/.local/bin/uv pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
VIRTUAL_ENV=~/.venvs/sports-mcp ~/.local/bin/uv pip install --no-deps -e .
make check VENV=~/.venvs/sports-mcp           # ruff + mypy --strict + pytest (>= 90% coverage)
make audit VENV=~/.venvs/sports-mcp           # pip-audit
make lock VENV=~/.venvs/sports-mcp            # regenerate hash-pinned locks
pytest -m live --no-cov                       # real ESPN (opt-in)
python tools/capture_fixtures.py              # re-capture fixtures from live ESPN
python tools/try_live.py game nfl 401872965 performers

scripts/setup.sh | start.sh | stop.sh | restart.sh [--recreate] | rebuild.sh [--keep-cache] | logs.sh | status.sh | smoke.sh
# Windows: .\scripts\start.ps1 etc. (forward into WSL "Ubuntu")
```

## 7. Known limitations and things not verified

- ESPN's API is unofficial; tests pin the shapes captured on 2026-10-04.
- **Not captured live:** no NBA, MLB, NHL or soccer game was in progress at capture time. The live paths for those sports (MLB situation, NHL special-teams strength, in-game soccer
  cards) are tested with hand-built dicts shaped like ESPN's fields, not with real in-game payloads. NFL and WNBA live payloads are real. The first live NHL/MLB game should be eyeballed.
- Soccer box scores come from `rosters` (goals, assists, shots, cards, saves); other leagues' pre-game player blocks other than MLB were absent, so only MLB exercises `basis: "season"` with real data.
- The day-boundary rule was verified for evening ET games (02:00Z) only, not for games starting after 11 pm ET.
- `get_team_schedule` returns the season type ESPN serves for the team (e.g. only the postseason for MLB right now), and the `season` argument was only tested for NFL and via mocks.
- The Goose and Claude Code client snippets are standard configurations but were not tested against those clients; the server was exercised over HTTP with `mcp.Client` and raw JSON-RPC.
- PowerShell twins: only `status.ps1` was executed; the others share the same helper and differ only in script name.
- No TLS (loopback only by design). `.env` mode 600 is not enforced on the `/mnt/c` checkout.
- Individual sports and the odds snapshot (Phase 2) are intentionally absent.
