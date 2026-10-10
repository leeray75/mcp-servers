# Fix round 1

Addresses `review-round-1.md` (APPROVED with minors). Date: 2026-10-08.

1. **SPEC.md section 7 stale caveat (minor):** the README-caveat requirement now states the original "search backend unconfirmed" item is superseded by Amendment 2 (search works via the filter, DuckDuckGo fallback, Alexandria cloud-only).
2. **README checklist (minor):** smoke bullet now lists "live search" before "abort then health"; the quick-start comment for `--no-live` mentions the search step. `CLAUDE.md` says `--no-live` skips the live scrape and search.
3. **Keep-alive ECONNRESET race (uncertain note):** evaluated and judged plausible. The child's axios uses Node's default agent, which drops idle sockets after about 5 s, equal to the filter server's default `keepAliveTimeout` of 5 s, so a request could land on a socket the filter just closed. Cheap, low-risk fix applied: `server.keepAliveTimeout = 65000` in `src/api-filter.mjs` (server now outlives any client idle timeout). Test added asserting `keepAliveTimeout > 5000` and `requestTimeout === 0`. A true race reproduction is timing-dependent and was not attempted.
4. **JSON number precision (review item 3):** acknowledged, no code change. Search payloads carry no integers above 2^53; re-serialisation only happens when a key is actually removed.

Final sweep of README.md, CLAUDE.md, docs/SPEC.md, .env.example: no other stale statements found (the historical SPEC.md smoke description in section 5 predates the amendment and is not contradicted; Amendment 2 covers the added step).

## Verification
- `npm test`: 78 tests pass, `src/` line coverage 95.79%; `npm run check` passes.
- Image rebuilt/recreated with `scripts/rebuild.sh --keep-cache` in WSL; container healthy on `127.0.0.1:8768->8000` only; `scripts/smoke.sh` passes all 7 steps (see run output in the coordinator summary).
