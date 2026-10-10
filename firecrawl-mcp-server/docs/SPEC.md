# firecrawl-mcp-server — Specification

Status: authoritative. Source context: `C:\Users\hoyen\Documents\firecrawl-mcp-streamable-http-handoff.md` (Option B).
Sibling servers to mirror in layout, scripts and security posture: `../sports-mcp-server` (primary reference), `../vision-mcp-server`, `../gmail-mcp-server`.

## 1. Purpose

Run the upstream `firecrawl-mcp` npm package (pinned) as a **persistent Streamable HTTP MCP server** in Docker (WSL2),
pointed at the **self-hosted Firecrawl API** (`firecrawl-api-1`, WSL Docker network `firecrawl_backend`, port 3002).
This replaces Goose's stdio `firecrawl-local` extension, eliminating the stdio EPIPE crash and making the server
outlive client sessions. Bearer-token auth is added (house pattern) via a small gateway, because upstream's local HTTP
mode has no endpoint auth.

Non-goals: re-implementing Firecrawl tools; forking firecrawl-mcp; editing anyone's Goose config (README documents the
snippet only); cloud Firecrawl usage; exposing anything beyond 127.0.0.1.

## 2. Architecture

One container, two Node processes, started by a Node supervisor entrypoint:

```
Goose --HTTP+Bearer--> 127.0.0.1:8768 (host) -> :8000 gateway (container) --> 127.0.0.1:3000 firecrawl-mcp (HTTP, stateless)
                                                                                     |
                                                                                     v  http://127.0.0.1:3001  API filter (Amendment 2)
                                                                                     |
                                                                                     v  http://api:3002  (firecrawl_backend network)
                                                                              self-hosted Firecrawl API
```

- **firecrawl-mcp**: installed at image build with `npm ci` from a committed `package.json` + `package-lock.json` pinning
  `firecrawl-mcp@3.27.3` (exact version, integrity hashes in lockfile). Run as `node node_modules/firecrawl-mcp/dist/index.js`
  with env `HTTP_STREAMABLE_SERVER=true`, `HOST=127.0.0.1`, `PORT=3000` (internal only, never published),
  `FIRECRAWL_API_URL` (Amendment 2: `http://127.0.0.1:<API_FILTER_PORT>`, the loopback API filter, when `API_FILTER_ENABLED`; otherwise the configured URL, default `http://api:3002`), `FIRECRAWL_NO_SEARCH_FEEDBACK=1`, `FIRECRAWL_NO_ENDPOINT_FEEDBACK=1`.
  The child's MCP endpoint is `/mcp`.
- **gateway** (`src/gateway.mjs`, zero runtime dependencies, Node built-ins only; Node 22 LTS):
  - Listens on `HOST:PORT` (container: `0.0.0.0:8000`).
  - `GET /healthz`: unauthenticated, returns 200 `{"status":"ok"}` only if the child process is alive AND a local probe to the child
    succeeds (any HTTP response, including 4xx/405, from `http://127.0.0.1:3000/mcp` counts as up; connection refused = unhealthy → 503). No secrets in body.
  - `/mcp` (any method): requires `Authorization: Bearer <MCP_AUTH_TOKEN>`; compared with `crypto.timingSafeEqual` on equal-length
    digests (hash both with SHA-256 first). Missing/wrong → 401 with `WWW-Authenticate: Bearer`, JSON body, no detail about why.
  - Everything else → 404.
  - Host-header guard: allow only `127.0.0.1:<hostport>`, `localhost:<hostport>`, `[::1]:<hostport>` and `127.0.0.1:8000`/`localhost:8000` (the in-container health probe); allowed list is derived from env `PUBLIC_PORT` (default 8768). Others → 421. (DNS-rebinding defence, mirrors sports-mcp.)
  - Streams requests and responses without buffering (SSE/chunked must work); forwards method, path, query, and headers
    (except hop-by-hop headers and `Authorization`, which is **stripped** before forwarding); preserves status codes; propagates client abort to upstream.
  - Request body size cap (`MAX_REQUEST_BYTES`, default 1 MiB) → 413.
  - Child unreachable mid-request → 502 JSON body.
- **supervisor** (`src/main.mjs`): validates config, spawns the child, spawns/starts the gateway in-process, forwards SIGTERM/SIGINT to the child,
  exits non-zero (so Docker restarts the container) if the child exits unexpectedly, and shuts down gracefully (stop accepting, close child, exit 0) on signal.
  Waits for child readiness (poll up to `CHILD_START_TIMEOUT_SECONDS`, default 30) before the gateway reports healthy.
  Child stdout/stderr are passed through to the container log prefixed `[firecrawl-mcp]`, with the bearer token and any `FIRECRAWL_API_KEY` value redacted if they appear.

## 3. Configuration (`src/config.mjs` is the only module reading `process.env`)

| Variable | Default | Rules |
|---|---|---|
| `MCP_AUTH_TOKEN` | unset (auth disabled) | OPTIONAL (amendment 1). If unset or empty: no auth on `/mcp`, a loud startup WARNING is logged. If set: ≥ 32 chars, reject values starting `replace-me`, bearer enforced. Errors name the field, never the value |
| `FIRECRAWL_API_URL` | `http://api:3002` | must be `http`/`https` URL; no credentials in URL |
| `FIRECRAWL_API_KEY` | unset | optional (self-hosted w/o auth needs none); passed to child only if set |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | gateway bind inside container |
| `PUBLIC_PORT` | `8768` | used only for Host-header allow-list (compose sets it from `HOST_PORT`) |
| `CHILD_PORT` | `3000` | child bound to 127.0.0.1 only |
| `API_FILTER_ENABLED` | `true` | Amendment 2: `true`/`false`/`1`/`0`; route the child's API calls through the request filter |
| `API_FILTER_PORT` | `3001` | Amendment 2: 1-65535, must differ from `CHILD_PORT`; filter binds 127.0.0.1 only |
| `MAX_REQUEST_BYTES` | `1048576` | also caps buffered search bodies in the API filter |
| `CHILD_START_TIMEOUT_SECONDS` | `30` | |
| `LOG_LEVEL` | `info` | `debug|info|warn|error` |

Config errors print a clear message to stderr and exit 2 before anything starts.

## 4. Docker and operations (mirror `../sports-mcp-server`)

Files: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `.gitignore`, `.gitattributes`, `Makefile` (optional targets: `test`, `lint`, `audit`), `scripts/{setup,start,stop,restart,rebuild,logs,status,smoke}.sh` plus `.ps1` twins that forward into WSL, `scripts/lib/` shared helpers — copy and adapt from sports-mcp (rename, port 8768, health path, container name `firecrawl-mcp`).

- Dockerfile: multi-stage; builder `node:22-alpine` runs `npm ci --omit=dev --ignore-scripts` from lockfile; runtime `node:22-alpine`, non-root UID/GID 10001, `COPY src`, `ENV NODE_ENV=production`, `EXPOSE 8000`, HEALTHCHECK via `node -e` fetch of `/healthz` (no curl), `CMD ["node","src/main.mjs"]`. `PYTHON`-style digest-pin comment as in sports-mcp.
- Compose: same hardening anchor as sports-mcp (read-only rootfs, tmpfs `/tmp`, `cap_drop: [ALL]`, `no-new-privileges`, `init: true`, pids/mem/cpu limits, json-file log rotation, `restart: unless-stopped`); `container_name: firecrawl-mcp`; image `firecrawl-mcp-server:local`; `env_file: .env`; ports `127.0.0.1:${HOST_PORT:-8768}:8000` (**loopback only**, comment forbidding 0.0.0.0); joins the **external** network `firecrawl_backend` (so `http://api:3002` resolves) — declared `external: true` with a comment; `PUBLIC_PORT: "${HOST_PORT:-8768}"`.
- `start.sh` must fail with a clear message if the `firecrawl_backend` network does not exist (Firecrawl stack not running), build, start, wait healthy, and print the Goose snippet (token never echoed to logs beyond the snippet the user asked for — follow sports-mcp's behaviour exactly).
- `setup.sh`: generates `.env` with `MCP_AUTH_TOKEN` (`openssl rand -hex 32`) and `HOST_PORT=8768`.
- `smoke.sh`: end-to-end over HTTP with the bearer token: (1) `/healthz` 200; (2) `/mcp` without token → 401; (3) `initialize` → `serverInfo`; (4) `tools/list` contains `firecrawl_scrape`; (5) `firecrawl_scrape` of `https://example.com` returns text containing "Example Domain" (mark this step skippable via `--no-live`). Handles the `mcp-session-id`/SSE response format correctly (stateless server: parse `data:` lines).

## 5. Security requirements

**Amendment 1 (2026-10-05): the bearer token is OPTIONAL.** Default deployment = loopback-only with auth OFF. When `MCP_AUTH_TOKEN` is unset, the gateway performs no check on `/mcp` (the `Authorization` header is still stripped before forwarding), logs a startup warning, and 401 behaviour/tests apply only when a token is set. `setup.sh` generates `.env` without a token by default; `--with-token` generates one. `start.sh` prints the Goose snippet with the `Authorization` header only when a token is set. `smoke.sh` skips the 401 steps with a note when auth is disabled. Tests: config (unset/empty = disabled), and a no-token gateway test (proxies without Authorization, warning emitted, header stripped upstream). README must explain the tradeoff (any local process/page can call it; the self-hosted Firecrawl can fetch internal URLs) and how to enable the token. Acceptance criterion 4 becomes: with a token set, unauthenticated `/mcp` returns 401; with none set, `/mcp` is reachable and the log shows the warning. This amendment overrides any conflicting text above (§2, §3, §4, §6, §8).

- Loopback-only publish; token required for `/mcp`; constant-time compare; token and API key never logged, never in URLs, never in error bodies.
- `Authorization` header never forwarded to the child.
- Non-root, read-only rootfs, caps dropped, no host mounts; `.env` git-ignored and excluded from Docker context.
- Lockfile-pinned dependencies; `npm ci --ignore-scripts`; `npm audit` target in Makefile.
- Child bound to 127.0.0.1 inside the container; not reachable from the network.

## 6. Tests (Node built-in `node:test`, no extra deps; `npm test`)

Must exist and pass without Docker or network, using a fake child MCP server (`tests/fake-child.mjs`) the gateway proxies to:

1. config: defaults, each validation failure (short token, `replace-me` token, bad URL, URL with credentials, bad ports), errors never contain the secret value.
2. gateway auth: no header / wrong scheme / wrong token / right token; timing-safe path exercised; 401 shape includes `WWW-Authenticate`.
3. gateway `/healthz`: 200 when child up, 503 when down; unauthenticated; no secrets in body.
4. Host guard: allowed and disallowed Host values → pass / 421.
5. Proxying: JSON POST round-trip; SSE streaming response arrives incrementally (first chunk before stream ends); `Authorization` stripped upstream (fake child echoes headers); query string and status codes preserved; 404 for other paths; 413 on oversize body; 502 when child dies; client abort propagates.
6. Redaction: log redaction function removes token and API key.
7. Supervisor: exits non-zero when child exits unexpectedly; forwards SIGTERM (unit-test the logic through injectable spawn).

Coverage via `node --test --experimental-test-coverage`; aim ≥ 85% lines for `src/`. Lint: `eslint` is NOT required (no extra deps); `node --check` all sources.

## 7. Documentation

- `README.md`: purpose, why (EPIPE), architecture diagram, quick start (WSL + `.ps1`), `.env` reference, Goose snippet (`type: streamable_http`, `uri: http://127.0.0.1:8768/mcp`, `Authorization: Bearer <token>`, `timeout: 300`, with a note to disable/replace `firecrawl-local`), verification checklist (from the handoff §4), open caveats (the original "search backend unconfirmed" item is superseded by Amendment 2: search works through the API filter, results come from the DuckDuckGo fallback, Alexandria/`domainTools` results are cloud-only), and a note that Goose config is **not** modified by this repo.
- `CLAUDE.md`: same structure as sports-mcp's (what it is, commands, architecture, invariants).
- `docs/reports/implementation-report.md`: written by the implementer (what was built, deviations, test/coverage results, live-verification results).

## 8. Acceptance criteria

1. `npm test` passes with no network/Docker.
2. `docker compose build` succeeds in WSL; `scripts/start.sh` brings the container to healthy while the Firecrawl stack is up.
3. `scripts/smoke.sh` passes all steps including the live scrape of example.com against the self-hosted API.
4. Unauthenticated `/mcp` returns 401; published port is bound to `127.0.0.1` only (`docker ps` shows `127.0.0.1:8768->8000`).
5. Container survives client disconnects mid-request (a smoke step aborts a streaming request, then re-checks `/healthz`).
6. No secrets in logs (`scripts/logs.sh --no-follow` grep for the token returns nothing).
7. Existing containers (gmail 8765, vision 8766, sports 8767, firecrawl stack) are untouched; Goose `config.yaml` is untouched.

## Amendment 2 (2026-10-08): search request filter

`firecrawl_search` failed with 400 because firecrawl-mcp 3.27.3 sends cloud-only `domainTools`/`toolDetail` fields that the
self-hosted API rejects. An in-process loopback filter (`src/api-filter.mjs`) now sits between the child and the API and
removes those two top-level keys from `/v1|v2/search` bodies. See `docs/SPEC-search-filter.md` (authoritative for this
behaviour); reports are in `docs/reports/search-filter/`.
