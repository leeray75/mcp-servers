# Review round 1: search request filter (Amendment 2)

Reviewer: independent. Date: 2026-10-08. Verdict: **APPROVED** (no blocker or major issues; 3 minors, optional).

## Verification performed
- Read all changed files (api-filter, config, main, gateway exports, tests, smoke.sh, package.json, .env.example, README, CLAUDE.md, SPEC.md) against SPEC-search-filter.md.
- `npm test`: 77 tests, 77 pass, 0 fail. Coverage of src/: 95.77% lines (api-filter 98.48, config 100, gateway 96.49, main 90.86, redact 100). Tests use port 0 or a freshly probed free port; main tests default the filter off, so no port 3001 contention.
- `grep process.env src`: only config.mjs reads it; main.mjs/gateway have defaults-parameter references only (pre-existing `childEnv(base = process.env)` and `main(env = process.env)`). No new dependencies; package.json still `firecrawl-mcp: 3.27.3`, lockfile and node_modules/firecrawl-mcp/package.json = 3.27.3.
- Live (read-only), through http://127.0.0.1:8768/mcp (initialize, notifications/initialized, tools/call, SSE data lines; token read from .env, not printed): `firecrawl_search` {"query":"firecrawl self-hosted","limit":3} returned success with web results (no isError); `firecrawl_scrape` (example.com) and `firecrawl_map` (limit 5, empty links, expected) no isError.
- `wsl docker logs --since 10m firecrawl-api-1 | grep -c unrecognized_keys` = 0.
- `wsl docker ps`: firecrawl-mcp `127.0.0.1:8768->8000/tcp`, Up (healthy); nothing else published.
- `scripts/smoke.sh` in WSL: all 7 steps pass, including new step 6 search.
- Not re-run live: crawl / check_crawl_status (relied on implementer's report; same pass-through code path as scrape/map, covered by the GET /v2/crawl/abc test).

## Spec conformance
| Spec item | Result |
|---|---|
| 4.1 filtered routes POST /v1,/v2/search, query ignored for match | Pass (api-filter.mjs:119-125) |
| 4.1 buffer cap -> 413, upstream not called (declared and chunked) | Pass (:91-107), tested |
| 4.1 strip only top-level keys, new content-length, drop transfer-encoding | Pass (:12-24, :40-47) |
| 4.1 invalid/non-object JSON forwarded unchanged | Pass |
| 4.1 debug log of key names only; no bodies/headers | Pass (:112, :73) |
| 4.1 other requests streamed, unbuffered, no cap | Pass (`req.pipe`, :81) |
| 4.1 prefix join, Host rewrite, Authorization forwarded, hop-by-hop dropped both ways | Pass (:36,56,42-46,62) |
| 4.1 response streamed; no response timeout (requestTimeout=0) | Pass |
| 4.1 502 `{"error":"bad_gateway"}` pre-headers; no double response post-headers (sendJson destroys when headersSent) | Pass |
| 4.1 client abort destroys upstream; http and https; shared HOP_BY_HOP/sendJson exported from gateway | Pass |
| 4.2 config vars, parsing, clash check, errors name field only | Pass (config.mjs:148-197) |
| 4.3 loopback listen before spawn, childEnv, listen failure no child, shutdown closes (+connections), injectable `createFilter`, /healthz unchanged | Pass (main.mjs:266-283, 318-325); gateway-listen-failure and child-exit paths also close the filter |
| 4.4 compose/Dockerfile unchanged, 3001 not published | Pass (grep: no 3001 in compose/Dockerfile) |
| 4.5 smoke step, skipped by --no-live, smoke.ps1 forwards | Pass |
| 5 tests 1-11 + config + main updates | Pass; each assertion would fail if behaviour broke (content-length equality, upstream.seen.length===0, first-chunk-before-end, upstream res close on abort) |
| 6 docs: SPEC.md amendment/diagram/env/table, README diagram/env/caveat, CLAUDE.md, .env.example | Pass, see minors below |
| 7 acceptance 1-7 | Pass (crawl/check_crawl_status per report, see above) |

## Issues
No blockers. No majors.

1. **minor** `docs/SPEC.md:106` : §7 still says README open caveats include "search backend unconfirmed". Now stale relative to Amendment 2 (README caveat was replaced). Fix: change to "search works through the API filter; results from the DuckDuckGo fallback; Alexandria is cloud-only" or append "(superseded by Amendment 2)".
2. **minor** `README.md:~48` (verification checklist, smoke.sh bullet): lists "health, 401 ..., initialize, tools/list, live scrape, abort then health" but omits the new live search step. Fix: insert "live search" before "abort then health".
3. **minor** `src/api-filter.mjs:16` / `:23` (uncertain, low impact): on search bodies where a key is removed, the body is re-serialised via JSON.parse/stringify, which can lose precision on integers above 2^53 and normalises escapes. Search payloads do not carry such numbers in practice; acceptable, mention only. No fix required (could splice keys out of the raw text, not worth it).

Also noted, no action: the Node default 5 s server keepAliveTimeout on the filter against the child's keep-alive agent is a theoretical ECONNRESET race on idle sockets; not observed in smoke or live calls (uncertain, unconfirmed).
