# Fix round 2

Addresses `review-round-2.md` (APPROVED, minors). Date: 2026-10-08.

1. `CLAUDE.md`: the "what was built" pointer now also names `docs/reports/search-filter/` (reports) and `docs/SPEC-search-filter.md` (spec).
2. `src/api-filter.mjs`: `headersTimeout` is now `keepAliveTimeout + 1000` (66000 ms), with a one-line comment, because Node 22 (the container runtime) wants `headersTimeout > keepAliveTimeout`. The existing timeouts test also asserts `headersTimeout > keepAliveTimeout`.
3. Optional behavioural keep-alive test skipped on purpose: it would add more than 5 s to the test run for a timing-dependent scenario.

## Verification
- `npm test`: 78 tests pass; `npm run check` passes.
- Rebuilt/recreated with `scripts/rebuild.sh --keep-cache` in WSL; container healthy on `127.0.0.1:8768->8000/tcp`; `scripts/smoke.sh` passes.
