# firecrawl-mcp-server — Search request filter (Amendment 2)

Status: authoritative for this change. Extends `docs/SPEC.md`; where they conflict, this document wins for the API-filter
behaviour only. All invariants in `docs/SPEC.md` and `CLAUDE.md` still apply.
Date: 2026-10-08.

## 1. Problem

`firecrawl_search` fails through the MCP server with `Request failed with status code 400`. Calling the self-hosted API
directly (`POST http://127.0.0.1:3002/v2/search` with `{"query":"...","limit":3}`) works and returns DuckDuckGo results.

Root cause, confirmed in `node_modules/firecrawl-mcp/dist/index.js` (3.27.3):

- The `firecrawl_search` tool always sets `searchOpts.domainTools ??= defaultDomainTools(...)` and
  `searchOpts.toolDetail ??= "compact"` (around lines 5493-5494; a second code path around 6652-6653 does the same).
- These are Firecrawl-cloud (Alexandria) fields. The self-hosted API validates the body strictly and rejects them:
  `api/v2:searchController: Invalid request body ... "code":"unrecognized_keys","keys":["domainTools","toolDetail"]`.
- `postSearchWithFallback` retries only on specific cloud 403 errors, never on this 400. No tool argument avoids it.

## 2. Decision

Option 1: an in-process **API request filter** in the wrapper, between the child and the Firecrawl API. The upstream
package stays pinned (`firecrawl-mcp@3.27.3`) and unmodified. Rejected: patching `dist/index.js` at build time (breaks
silently on upgrade, violates "pinned, unmodified upstream") and forking the Firecrawl API.

## 3. Architecture change

```
Goose --> 127.0.0.1:8768 gateway :8000 --> 127.0.0.1:3000 firecrawl-mcp (child)
                                                  |
                                                  v  FIRECRAWL_API_URL=http://127.0.0.1:3001   (NEW: child points here)
                                       127.0.0.1:3001 API filter (NEW, in the supervisor process)
                                                  |
                                                  v  http://api:3002 (configured FIRECRAWL_API_URL, unchanged)
                                       self-hosted Firecrawl API
```

## 4. Requirements

### 4.1 New module `src/api-filter.mjs` (Node built-ins only, zero runtime dependencies)

Export a factory, e.g. `createApiFilter({ upstreamUrl, maxRequestBytes, logger })`, returning an `http.Server` (not yet
listening), mirroring the style of `createGateway` in `src/gateway.mjs`.

- **Filtered route:** `POST` requests whose path (ignoring the query string) is `/v2/search` or `/v1/search`.
  - Buffer the body up to `maxRequestBytes`; larger → 413 JSON body, upstream not called.
  - If the body parses as a JSON **object**, delete the top-level keys `domainTools` and `toolDetail` only (nested
    objects such as `scrapeOptions` are untouched), re-serialise, and set `content-length` to the new byte length
    (drop any `transfer-encoding`).
  - If the body is not valid JSON or not an object, forward it byte-for-byte unchanged (let the API answer).
  - Log at `debug` the names of removed keys only. Never log bodies, queries, headers or values.
- **Every other request** (any method, any path, including `GET /v2/crawl/<id>`, `/v2/scrape`, `/v2/map`, `/v2/parse`
  multipart uploads): stream request and response through unchanged, without buffering, no size cap beyond what the
  API enforces.
- **Forwarding:** upstream URL = configured `FIRECRAWL_API_URL` origin + its pathname prefix (if any, without a doubled
  slash) + incoming path + query. Forward method, status code, and headers except hop-by-hop headers; set `Host` to the
  upstream host. The `Authorization` header **is forwarded** here (it carries `FIRECRAWL_API_KEY` when one is set; this is
  the child talking to the API, not a client). It must never be logged.
- Stream the upstream response back (status, headers minus hop-by-hop, body), so long scrapes/crawls and any chunked
  responses work. No proxy-imposed timeout on upstream responses.
- Upstream unreachable / connection error before headers → 502 JSON body `{"error":"bad_gateway"}` (no internal detail).
- Client abort propagates to the upstream request (destroy it), as in the gateway.
- Support `http:` and `https:` upstreams.
- Reuse the hop-by-hop handling from `gateway.mjs` if practical (export a shared helper rather than duplicating it).

### 4.2 Configuration (`src/config.mjs` stays the only reader of `process.env`)

| Variable | Default | Rules |
|---|---|---|
| `API_FILTER_ENABLED` | `true` | `true`/`false` (case-insensitive; also accept `1`/`0`). Anything else → ConfigError naming the field |
| `API_FILTER_PORT` | `3001` | integer 1-65535; must differ from `CHILD_PORT` (ConfigError otherwise) |

`FIRECRAWL_API_URL` keeps its meaning: the real Firecrawl API.

### 4.3 Supervisor (`src/main.mjs`)

- When enabled: create the filter, listen on `127.0.0.1:<API_FILTER_PORT>` (loopback only, never `0.0.0.0`, never
  published) **before** spawning the child; `childEnv` sets `FIRECRAWL_API_URL=http://127.0.0.1:<API_FILTER_PORT>`.
  A listen failure aborts startup with a non-zero exit (same path as a gateway listen failure) and no child left running.
- When disabled: no filter server; `childEnv` passes the configured `FIRECRAWL_API_URL` directly (current behaviour).
- `shutdown()` closes the filter server (and its open connections) along with the gateway.
- The filter server must be injectable/overridable in tests the same way spawn/probe are.
- `/healthz` semantics are unchanged.

### 4.4 Container / compose

No changes to ports, hardening, mounts or networks. Port 3001 is internal to the container only. Do not publish it.

### 4.5 Smoke test (`scripts/smoke.sh`, and keep `smoke.ps1` forwarding working)

Add a live step (skipped by `--no-live`): `firecrawl_search` with `{"query":"firecrawl","limit":3}` returns
`isError` not true and at least one web result URL. Existing steps unchanged.

## 5. Tests (`node:test`, no Docker or network; keep `src/` ≥ 85% lines)

New `tests/api-filter.test.mjs` with a fake upstream HTTP server that echoes method, path, query, headers and body:

1. `POST /v2/search` with `domainTools`, `toolDetail`, and other fields → upstream receives the other fields only;
   `content-length` matches the new body; response status/body relayed.
2. Same for `/v1/search`; query string preserved.
3. Nested `scrapeOptions.toolDetail` (if present) is NOT removed; only top-level keys are.
4. Invalid JSON and a JSON array body on `/v2/search` → forwarded unchanged.
5. Non-search routes (`POST /v2/scrape`, `GET /v2/crawl/abc?x=1`) → byte-identical body, method, path, query.
6. `Authorization` forwarded to upstream; `Host` rewritten to upstream host; hop-by-hop headers dropped.
7. Oversize search body → 413, upstream not called.
8. Upstream down → 502 with the JSON body.
9. Upstream URL with a path prefix (e.g. `http://host:port/base`) → upstream sees `/base/v2/search`.
10. Streaming: upstream sends a chunk then waits; client receives the first chunk before the response ends.
11. Client abort mid-response destroys the upstream request.

Plus updates to existing tests:

- `config.test.mjs`: defaults, `API_FILTER_ENABLED` parsing and rejection, `API_FILTER_PORT` range and clash with `CHILD_PORT`.
- `main.test.mjs`: `childEnv` points to `http://127.0.0.1:<API_FILTER_PORT>` when enabled and to the real URL when
  disabled; filter listen failure aborts startup without spawning (or kills) the child; shutdown closes the filter.

## 6. Documentation (update in place; no stale statements may remain)

- `docs/SPEC.md`: add "Amendment 2 (2026-10-08)" pointing to this file; update the §2 diagram/child env bullet and the
  §3 config table (`API_FILTER_ENABLED`, `API_FILTER_PORT`).
- `README.md`: architecture diagram, `.env` reference, and replace the "Search backend unconfirmed" caveat with the
  current facts (search works through the filter; results come from the self-hosted API's DuckDuckGo fallback because
  `SEARXNG_ENDPOINT` is not set in the Firecrawl stack; Alexandria/`domainTools` results are cloud-only and not
  available; how to disable the filter).
- `CLAUDE.md`: architecture list gains `api-filter.mjs`; invariants mention the filter binds 127.0.0.1 only and that
  upstream stays unpatched.
- `.env.example`: the two new optional variables, commented, with defaults.
- `docs/reports/search-filter/`: all reports for this change go here (see §8). Do not edit or delete the existing
  reports in `docs/reports/` (historical record of the original build).

## 7. Acceptance criteria

1. `npm test` passes without network/Docker; `src/` line coverage ≥ 85%; `make check` passes.
2. Image rebuilt and container recreated via the project scripts (`scripts/rebuild.sh` / `restart.sh --recreate`
   as appropriate, run in WSL); container reports healthy; published port still `127.0.0.1:8768->8000` only.
3. Through the MCP endpoint (`http://127.0.0.1:8768/mcp`): `firecrawl_search` `{"query":"firecrawl self-hosted","limit":3}`
   returns results (no `isError`).
4. Regression through MCP: `firecrawl_scrape` (example.com), `firecrawl_map` (limit 5), `firecrawl_crawl` (limit 2) and
   `firecrawl_check_crawl_status` still succeed.
5. `docker logs firecrawl-api-1` shows no new `unrecognized_keys` warnings for search after the change.
6. `scripts/smoke.sh` passes including the new search step.
7. Other containers (gmail-mcp, vision-mcp, sports-mcp, the Firecrawl stack) untouched; Goose config untouched; the
   upstream `firecrawl-mcp` package is unmodified and still pinned at 3.27.3 in `package.json` and the lockfile.

## 8. Process and reports

- Implementer writes `docs/reports/search-filter/implementation-report.md` (what was built, deviations from this spec
  with reasons, test + coverage output summary, live verification results).
- Reviewer writes `docs/reports/search-filter/review-round-N.md` with a verdict (APPROVED / CHANGES REQUESTED) and
  issues tagged blocker / major / minor with file:line.
- Implementer writes `docs/reports/search-filter/fix-round-N.md` for each fix round.
- Maximum 3 review rounds.
