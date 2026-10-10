# Review round 2: search request filter (Amendment 2)

Reviewer: independent. Date: 2026-10-08. Verdict: **APPROVED** (no blocker/major; 2 optional minors).

## Round-1 issues
1. SPEC.md section 7 stale caveat: RESOLVED (docs/SPEC.md:106 now says superseded by Amendment 2, search via filter, DuckDuckGo fallback, Alexandria cloud-only).
2. README smoke bullet: RESOLVED (README.md:111 lists live search before abort; line 49 mentions search under --no-live; CLAUDE.md matches).
3. Keep-alive race: MITIGATED (src/api-filter.mjs:133 keepAliveTimeout=65000). See assessment below.
4. JSON precision: acknowledged, no change; acceptable (only re-serialised when a key is removed).

## Keep-alive assessment
- keepAliveTimeout 65000 > the child's default-agent idle drop (~5 s), so the filter never closes an idle socket first. Correct direction.
- headersTimeout 30000 < keepAliveTimeout 65000: unusual ordering (some guidance says headersTimeout > keepAliveTimeout, from an old Node bug where headersTimeout could reap idle keep-alive sockets). Empirically checked with the real filter on local Node v24.15: a keep-alive socket idle 40 s (> headersTimeout) was reused successfully, no ECONNRESET. Not run on the container's Node 22 (no Docker touch); Node 22 uses the same parser logic, so low risk (uncertain, unconfirmed on 22).
- requestTimeout 0: no deadline for a client to finish sending a request. The filter listens on 127.0.0.1 inside the container and is not published; the only client is the supervised child, so a stalled-client socket exhaustion is not a realistic threat. headersTimeout (30 s) still bounds slowloris-style header stalls. Search bodies are capped at MAX_REQUEST_BYTES. Acceptable and intentional (long scrapes/crawls).
- Test (tests/api-filter.test.mjs:278-282) asserts requestTimeout===0 and keepAliveTimeout>5000. It would fail if the setting were removed (Node default is 5000). It is a property assertion only, not a behavioural race test; acceptable given the race is timing-dependent.

## Verification
- `npm test`: 78 tests, 78 pass; src/ line coverage 95.79% (api-filter 98.52, config 100, gateway 96.49, main 90.86, redact 100). `npm run check` passes.
- Re-read src/api-filter.mjs and src/main.mjs in full: no new bugs. (main.mjs: filter closed on child exit, gateway listen failure, filter listen failure, shutdown; api-filter unchanged apart from the timeout line.)
- Live, read-only: `scripts/smoke.sh` in WSL passes all 7 steps (auth step skipped, no token); `docker ps`: firecrawl-mcp Up (healthy), only 127.0.0.1:8768->8000/tcp; `docker logs --since 15m firecrawl-api-1 | grep -c unrecognized_keys` = 0. .env not printed.

## Stale-doc sweep
README.md, CLAUDE.md, docs/SPEC.md, docs/SPEC-search-filter.md, .env.example: ports (3001 filter, 8768 host, 3000 child), defaults (API_FILTER_ENABLED true, MAX_REQUEST_BYTES 1048576), smoke step list consistent with code. Test counts/coverage appear only in dated reports (77 in implementation/round-1, 78 in fix-round-1), correctly as point-in-time records.

## Issues
1. **minor** CLAUDE.md:7 (pointer to `docs/reports/implementation-report.md` as "what was built"): that report predates Amendment 2 and does not cover the filter, so the pointer is incomplete rather than wrong. Fix: append "; the search filter is recorded in `docs/reports/search-filter/`" (SPEC.md:125 already points there).
2. **minor** (uncertain, optional) tests/api-filter.test.mjs:278: the keep-alive test checks configuration only. Optionally add a behavioural test reusing a keep-alive agent across a >5 s idle gap; not required.

## Verdict
APPROVED.
