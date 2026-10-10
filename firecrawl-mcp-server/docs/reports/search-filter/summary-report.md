# Search request filter (Amendment 2) — Summary Report

Date: 2026-10-08. Spec: `docs/SPEC-search-filter.md`.

## Problem
`firecrawl_search` failed through the MCP server with HTTP 400. The pinned `firecrawl-mcp@3.27.3` always adds the
cloud-only fields `domainTools` and `toolDetail` to `/v2/search`, and the self-hosted Firecrawl API rejects unknown keys
(`unrecognized_keys`). The API's own search worked when called directly.

## Process
| Step | Actor | Output | Result |
|---|---|---|---|
| 1. Spec | Orchestrator | `docs/SPEC-search-filter.md` | Option 1: in-process API filter; upstream package unpatched |
| 2. Implement | Implementer subagent (Sonnet) | code, tests, docs, `implementation-report.md` | 77 tests pass, container rebuilt, smoke passes |
| 3. Review round 1 | Reviewer subagent (Sonnet) | `review-round-1.md` | APPROVED, 2 minor doc issues + 1 uncertain keep-alive race |
| 4. Fix round 1 | Implementer | `fix-round-1.md` | Docs fixed; filter `keepAliveTimeout` raised to 65 s with a test |
| 5. Review round 2 | Reviewer subagent (Sonnet) | `review-round-2.md` | APPROVED, 1 minor (stale CLAUDE.md pointer) + `headersTimeout` ordering note |
| 6. Fix round 2 | Implementer | `fix-round-2.md` | CLAUDE.md pointer fixed; `headersTimeout = keepAliveTimeout + 1000` |
| 7. Review round 3 | Reviewer subagent (Sonnet) | `review-round-3.md` | APPROVED, no must-fix issues |

All 3 allowed review rounds were used; the last two found only minor issues.

## What changed
- `src/api-filter.mjs` (new): loopback-only HTTP proxy on `127.0.0.1:3001` inside the container. For `POST /v1/search` and
  `/v2/search` it removes the top-level `domainTools` and `toolDetail` keys and recomputes `content-length`; every other
  request streams through unchanged. 502 if the API is unreachable, 413 over the body cap, client aborts propagate.
- `src/main.mjs`: starts the filter before the child and points the child's `FIRECRAWL_API_URL` at it; closes it on
  shutdown, child exit and startup failure.
- `src/config.mjs`: `API_FILTER_ENABLED` (default `true`) and `API_FILTER_PORT` (default `3001`, must differ from `CHILD_PORT`).
- `src/gateway.mjs`: exports the hop-by-hop header list and `sendJson` for reuse.
- Tests: new `tests/api-filter.test.mjs`; additions to `config.test.mjs` and `main.test.mjs`.
- `scripts/smoke.sh`: new live search step.
- Docs: `docs/SPEC.md` (Amendment 2), `README.md`, `CLAUDE.md`, `.env.example`.
- Unchanged: `firecrawl-mcp` still pinned at 3.27.3 with `node_modules` unmodified; no new dependencies; compose ports,
  hardening and networks unchanged.

## Verification (orchestrator re-checked after round 3)
- `npm test`: 78/78 pass, `src/` line coverage 95.80%; `npm run check` exit 0.
- Container `firecrawl-mcp` healthy, published on `127.0.0.1:8768->8000/tcp` only.
- `firecrawl_search` through `http://127.0.0.1:8768/mcp` returns web results (no `isError`); `firecrawl-api-1` logs show
  no new `unrecognized_keys`.
- `scripts/smoke.sh` passes all 7 steps (reviewer and implementer runs).
- Scrape, map, crawl and crawl status still work through MCP (implementer run; reviewers re-checked scrape and map).

## Stale-document check
- `README.md`, `CLAUDE.md`, `docs/SPEC.md`, `docs/SPEC-search-filter.md`, `.env.example`, package scripts and code
  comments are consistent with the current code (round-3 reviewer sweep plus orchestrator grep).
- The top-level `docs/reports/*.md` describe the original 2026-10-05 build and were left unchanged as a historical record.
  Open item 2 in `docs/reports/summary-report.md` ("`firecrawl_search` against the self-hosted API is unverified") is
  **superseded by this report**: search is verified and working through the filter.

## Notes and follow-ups
- Search results come from the self-hosted API's DuckDuckGo fallback because `SEARXNG_ENDPOINT` is not set in the
  Firecrawl stack. Adding a SearXNG container would improve result quality.
- Alexandria / `domainTools` tool matches are a Firecrawl-cloud feature and are not available self-hosted.
- Optional, not done: a behavioural keep-alive reuse test (needs a >5 s idle gap; the config is asserted instead).
- Setting `API_FILTER_ENABLED=false` restores the old behaviour (search returns 400 again).
