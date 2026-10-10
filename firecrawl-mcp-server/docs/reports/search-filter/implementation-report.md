# Implementation report: search request filter (Amendment 2)

Date: 2026-10-08. Spec: `docs/SPEC-search-filter.md`.

## Built
- `src/api-filter.mjs` (new): `createApiFilter({ upstreamUrl, maxRequestBytes, logger })` returns an unstarted `http.Server`; also exports `filterSearchBody`.
  `POST /v1|v2/search` bodies are buffered (cap `maxRequestBytes`, 413 otherwise), top-level `domainTools`/`toolDetail` removed, `content-length` recomputed;
  non-JSON / non-object bodies forwarded unchanged. All other requests stream unbuffered. Upstream prefix, `Host` rewrite, `Authorization` forwarded,
  hop-by-hop dropped both ways, 502 `{"error":"bad_gateway"}`, client abort destroys upstream, http and https upstreams. Debug log names removed keys only.
- `src/gateway.mjs`: now exports `HOP_BY_HOP` and `sendJson` (shared, not duplicated).
- `src/config.mjs`: `API_FILTER_ENABLED` (true/false/1/0, case-insensitive), `API_FILTER_PORT` (1-65535, must differ from `CHILD_PORT`).
- `src/main.mjs`: filter listens on 127.0.0.1 before the child is spawned; `childEnv` points `FIRECRAWL_API_URL` at it (or at the real URL when disabled);
  listen failure rejects with no child spawned; filter closed on shutdown, child exit, gateway listen failure. `createFilter` is injectable.
- `scripts/smoke.sh`: new step 6 (live search, skipped by `--no-live`); old step 6 is now 7. `smoke.ps1` unchanged (forwards args).
- Docs: `docs/SPEC.md` (Amendment 2, diagram, child env, config table), `README.md` (diagram, env table, caveat replaced), `CLAUDE.md`, `.env.example`; `package.json` `check` script includes the new file.

## Tests
`npm test`: 77 tests, 0 failures. Coverage of `src/`: 95.77% lines (api-filter 98.48, config 100, gateway 96.49, main 90.86, redact 100).
New `tests/api-filter.test.mjs` covers spec §5 items 1-11 plus https and non-object bodies; config and main tests extended. Main tests default to
`API_FILTER_ENABLED=false` so they do not contend for port 3001. `make` is not installed here; `npm run check` and `node --check tests/*.mjs` pass.

## Live verification (image rebuilt with `scripts/rebuild.sh --keep-cache` in WSL)
2. Container `Up (healthy)`, ports `127.0.0.1:8768->8000/tcp` only. Startup log shows `api filter listening 127.0.0.1:3001`.
3. MCP `firecrawl_search {"query":"firecrawl self-hosted","limit":3}`: isError false, web results returned.
4. `firecrawl_scrape` (example.com), `firecrawl_map` (limit 5), `firecrawl_crawl` (limit 2), `firecrawl_check_crawl_status`: all isError false (map returned an empty link list for example.com, which is expected for a single-page site).
5. `docker logs firecrawl-api-1 | grep -c unrecognized_keys` is 2 before and after the new searches (only the two pre-change occurrences).
6. `scripts/smoke.sh`: all steps pass including new search step.
7. Other containers (gmail-mcp, vision-mcp, sports-mcp, Firecrawl stack) not touched; Goose config not touched; `firecrawl-mcp` still `3.27.3`; `node_modules` unmodified.

## Deviations
- None functional. Rebuild used `--keep-cache` (skips base-layer refresh) since only `src/` changed.
- Search results come from the self-hosted API's DuckDuckGo fallback (no `SEARXNG_ENDPOINT`); documented in README.
