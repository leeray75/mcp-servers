# firecrawl-mcp-server

Runs the upstream [`firecrawl-mcp`](https://www.npmjs.com/package/firecrawl-mcp) package (pinned to **3.27.3**) as a
**persistent Streamable HTTP MCP server** in Docker (WSL2), pointed at the **self-hosted Firecrawl API**, with
optional bearer-token auth added by a small gateway. Same house style as `../sports-mcp-server`, `../vision-mcp-server` and
`../gmail-mcp-server`.

This repository does **not** modify Goose's `config.yaml`; the snippet below is for you to apply.

## Why

Goose's stdio `firecrawl-local` extension could crash with `EPIPE`: the MCP SDK's `StdioServerTransport.send()` writes to
`process.stdout` with no `error` listener, so a client disconnect before the handshake completes kills the whole server
process. Over HTTP a client disconnect is just a closed connection, and the server outlives Goose sessions.
`firecrawl-mcp`'s local HTTP mode has no endpoint auth, so a gateway can add `Authorization: Bearer`; the token is optional (see Security tradeoff).

## Architecture

```
Goose --HTTP (+Bearer if a token is set)--> 127.0.0.1:8768 (host) -> :8000 gateway (container) --> 127.0.0.1:3000 firecrawl-mcp (HTTP, stateless)
                                                                                   |
                                                                                   v  http://127.0.0.1:3001 API filter (container-internal)
                                                                                   |
                                                                                   v  http://api:3002 (firecrawl_backend network)
                                                                            self-hosted Firecrawl API
```

One container, two Node processes (plus an in-process API filter) started by `src/main.mjs` (supervisor):

- `firecrawl-mcp` (lockfile-pinned) on `127.0.0.1:3000`, inside the container only, never published.
- `src/gateway.mjs` (zero dependencies): `/healthz` unauthenticated; `/mcp` requires the bearer token when `MCP_AUTH_TOKEN` is set (constant-time
  compare); Host-header allow-list (421 otherwise); streaming proxy with `Authorization` stripped; 1 MiB body cap (413);
  502 if the child is unreachable; client aborts propagate upstream; anything else is 404.
- `src/api-filter.mjs` (zero dependencies, runs inside the supervisor process on `127.0.0.1:3001`, never published): the child's `FIRECRAWL_API_URL`
  points here. It removes the cloud-only `domainTools` and `toolDetail` keys from `POST /v1/search` and `/v2/search` bodies (the self-hosted API
  rejects them with 400 `unrecognized_keys`) and streams every other request and response through unchanged.
- The supervisor exits non-zero if the child dies (Docker restarts the container), shuts down gracefully on SIGTERM and
  redacts the token and any `FIRECRAWL_API_KEY` from child output.

## Quick start

All commands run in WSL2 Ubuntu (Docker Engine lives there). Windows users can use the `.ps1` twins
(`.\scripts\start.ps1`), which forward into WSL. The self-hosted Firecrawl stack must be running (network
`firecrawl_backend` must exist).

```bash
scripts/setup.sh     # writes .env with HOST_PORT=8768 and NO token (auth off); --with-token generates one; --force rewrites
scripts/start.sh     # checks the network, builds, starts, waits until healthy, prints the Goose snippet
scripts/smoke.sh     # end-to-end over HTTP; add --no-live to skip the live scrape of example.com and the live search
```

Other scripts: `stop.sh`, `restart.sh [--recreate]` (needed after editing `.env`), `rebuild.sh [--keep-cache]`,
`logs.sh [-n N] [--no-follow]`, `status.sh`. `.env` is git-ignored and excluded from the Docker context; scripts never
print its values.

Development (no Docker or network needed): `npm ci --ignore-scripts && npm test` (Node 22+). `make check` also
syntax-checks all files; `make audit` runs `npm audit --omit=dev`.

## `.env` reference

| Variable | Default | Notes |
|---|---|---|
| `MCP_AUTH_TOKEN` | unset (auth off) | optional; if set: >= 32 chars, `replace-me*` rejected, bearer enforced. Unset/empty logs a startup warning. Errors name the field, never the value |
| `HOST_PORT` | `8768` | host side of the loopback publish (compose) |
| `FIRECRAWL_API_URL` | `http://api:3002` | http/https, no credentials in the URL |
| `FIRECRAWL_API_KEY` | unset | only if your Firecrawl API enforces auth; passed to the child only |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warn`, `error` |
| `MAX_REQUEST_BYTES` | `1048576` | request body cap |
| `CHILD_START_TIMEOUT_SECONDS` | `30` | wait for firecrawl-mcp to accept connections |
| `API_FILTER_ENABLED` | `true` | `true`/`false`/`1`/`0`. `false` sends the child straight to `FIRECRAWL_API_URL` (search then fails with 400 on self-hosted) |
| `API_FILTER_PORT` | `3001` | container-internal loopback port of the filter; must differ from `CHILD_PORT` |

Fixed inside the container: `HOST=0.0.0.0`, `PORT=8000`, `CHILD_PORT=3000` (the filter uses 3001), `PUBLIC_PORT` (= `HOST_PORT`, for the Host guard).

## Goose configuration

Add to `extensions:` in `C:\Users\hoyen\AppData\Roaming\Block\goose\config\config.yaml` (back it up first,
`config.yaml.bak-YYYYMMDD-HHMMSS`), and **disable or replace the stdio `firecrawl-local` entry**:

```yaml
  firecrawl:
    name: firecrawl
    description: Self-hosted Firecrawl (scrape, map, crawl) over Streamable HTTP
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8768/mcp
    timeout: 300
```

If you enabled a token, add under `uri:`:

```yaml
    headers:
      Authorization: "Bearer <MCP_AUTH_TOKEN from .env>"
```

## Security tradeoff: auth is OFF by default

With no `MCP_AUTH_TOKEN`, anything that can reach `127.0.0.1:8768` can call the tools: any local process, and any web
page open in your browser (the Host-header guard blocks DNS rebinding, but not a page that simply posts to
`http://127.0.0.1:8768/mcp`). The self-hosted Firecrawl can fetch internal/LAN URLs, so an unauthenticated caller could use
it to probe your network. The port is published on loopback only, which keeps other machines out. The `Authorization`
header is still stripped before forwarding, and the server logs a startup warning while auth is off.

Enable the token: `scripts/setup.sh --force --with-token` (backs up the old `.env`), then `scripts/restart.sh --recreate`
and add the `headers:` block above to Goose. With a token set, unauthenticated `/mcp` returns 401.

## Verification checklist

- [ ] `scripts/status.sh`: running, healthy, network present; `docker ps` shows `127.0.0.1:8768->8000/tcp`.
- [ ] `scripts/smoke.sh` passes (health, 401 without token when a token is set, `initialize` -> `serverInfo`, `tools/list` has `firecrawl_scrape`, live scrape, live search, abort then health).
- [ ] `firecrawl-api` logs show the scrape request.
- [ ] Goose config backed up, `firecrawl-local` disabled, new Goose session lists the `firecrawl_*` tools and a scrape works.
- [ ] No Goose-spawned `npx firecrawl-mcp` process remains.
- [ ] `scripts/logs.sh --no-follow` contains no token.

## Open caveats

- **Search works through the API filter, with limits.** `firecrawl_search` works against the self-hosted API because the filter strips the
  cloud-only `domainTools`/`toolDetail` fields. Results come from the self-hosted API's DuckDuckGo fallback because `SEARXNG_ENDPOINT` is not
  set in the Firecrawl stack; Alexandria / `domainTools` results are Firecrawl-cloud only and are not available. To disable the filter set
  `API_FILTER_ENABLED=false` in `.env` and run `scripts/restart.sh --recreate` (search will then return 400 again).
- The container health probe and aborted requests make `firecrawl-mcp` print FastMCP warnings/stack traces to the log
  (an empty `GET /mcp`); they are harmless noise.
- `npm audit --omit=dev` reports advisories in `axios` (a transitive dependency of `firecrawl-mcp` 3.27.3); they are
  outside this repo's control until upstream bumps it. Re-check on each upgrade.
- Plain HTTP on loopback: never publish on `0.0.0.0`. On the `/mnt/c` checkout POSIX modes are not enforced for `.env`.
