# Review round 3 (final): search request filter (Amendment 2)

Reviewer: independent. Date: 2026-10-08. Verdict: **APPROVED**.

## Round-2 issues
1. CLAUDE.md pointer: RESOLVED. CLAUDE.md:7 names `docs/reports/search-filter/` and `docs/SPEC-search-filter.md`.
2. headersTimeout: RESOLVED. src/api-filter.mjs:133-134 has a comment and `server.headersTimeout = server.keepAliveTimeout + 1000` (66000).
3. Timeouts test: RESOLVED. tests/api-filter.test.mjs:282 asserts `headersTimeout > keepAliveTimeout`. Without the line, Node's default headersTimeout (60000) is below keepAliveTimeout (65000), so the assertion would fail.
4. Optional behavioural keep-alive test: intentionally skipped (documented in fix-round-2); not required.

## Verification
- `npm test`: 78 tests, 78 pass; src/ line coverage 95.80% (api-filter 98.53, config 100, gateway 96.49, main 90.86, redact 100); >= 85%.
- `npm run check`: exit=0 (confirmed explicitly).
- Live, read-only (no rebuild): `scripts/smoke.sh` in WSL passed all steps (auth skipped, no token; live scrape and live search ok; abort then health ok). `docker ps`: firecrawl-mcp Up (healthy), only `127.0.0.1:8768->8000/tcp`. `docker logs --since 15m firecrawl-api-1 | grep -c unrecognized_keys` = 0. .env not printed.

## Stale-doc sweep
README.md, CLAUDE.md, docs/SPEC.md, docs/SPEC-search-filter.md, .env.example, package.json scripts, src/ and tests/ comments: no inconsistencies found. No doc or comment cites the old 30000 filter headersTimeout (src/gateway.mjs:169 headersTimeout=30000 is the gateway, unrelated and correct). Test counts appear only in dated historical reports. package.json scripts match the commands documented in CLAUDE.md.

## Issues
Must fix: none.
Optional follow-ups: (a) behavioural keep-alive reuse test across a >5 s idle gap (adds test time; low value).

## Verdict
APPROVED.
