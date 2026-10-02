# Gmail MCP Server — Specification

Normative words (MUST, SHOULD, MAY) follow RFC 2119.

## 1. Overview

`gmail-mcp-server` is a Python MCP server exposing a minimal Gmail tool set over
**Streamable HTTP**, running in a Docker container bound to `127.0.0.1`.

## 2. Repository layout

```
gmail-mcp-server/
├── src/gmail_mcp/
│   ├── __init__.py          # version
│   ├── __main__.py          # entrypoint: python -m gmail_mcp
│   ├── config.py            # pydantic-settings; all env config
│   ├── logging_setup.py     # structured JSON logging, secret redaction
│   ├── auth.py              # token load/refresh/store; OAuth flow helpers
│   ├── auth_cli.py          # one-time interactive OAuth (python -m gmail_mcp.auth_cli)
│   ├── gmail_client.py      # thin wrapper over googleapiclient; error mapping
│   ├── models.py            # pydantic models for tool inputs/outputs
│   ├── security.py          # bearer-token ASGI middleware, constant-time compare
│   ├── server.py            # FastMCP app, tool registration, health route
│   └── tools.py             # tool implementations (pure functions over gmail_client)
├── tests/
├── scripts/                 # start|stop|restart|rebuild|auth|logs|status (.sh + .ps1)
├── docs/                    # PLAN.md, SPEC.md, reports/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .gitignore
├── .dockerignore
├── CHANGELOG.md
└── README.md
```

Use a `src/` layout. Package name `gmail_mcp`. Python `>=3.12`.

## 3. Configuration (environment variables)

All config MUST be read through one `pydantic-settings` class in `config.py`;
no `os.environ` access elsewhere. Unknown/invalid values MUST fail fast at
startup with a clear message that never echoes secret values.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | yes | — | Desktop-app OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | yes | — | OAuth client secret (never logged) |
| `MCP_AUTH_TOKEN` | yes | — | Shared bearer secret clients must present; min 32 chars |
| `GMAIL_SCOPES` | no | `gmail.readonly gmail.compose` | Space-separated; MUST be a subset of the two defaults |
| `TOKEN_PATH` | no | `/data/token.json` | Where the refresh token lives (mounted volume) |
| `HOST` | no | `0.0.0.0` | Bind inside container (host exposure is restricted by compose, see §7) |
| `PORT` | no | `8000` | Container port |
| `LOG_LEVEL` | no | `INFO` | |
| `MAX_RESULTS_CAP` | no | `50` | Hard ceiling for any list tool |
| `MAX_BODY_CHARS` | no | `20000` | Truncation limit for returned message bodies |

`.env.example` MUST contain placeholders only (e.g. `replace-me`), never real values.
`.env` MUST be git-ignored and excluded from the Docker build context.

## 4. Authentication & token handling

### 4.1 Client → server
- Every request to the MCP endpoint MUST carry `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- Comparison MUST use `hmac.compare_digest`.
- Missing/invalid → HTTP 401 with no detail about which part failed; log a
  redacted warning (never the presented token).
- `GET /healthz` is the only unauthenticated route; it returns `{"status":"ok"}`
  and, separately, MUST NOT reveal whether Google credentials exist.
- Origin/Host validation: the server MUST reject requests whose `Host` header is
  not `127.0.0.1:<port>`, `localhost:<port>` (DNS-rebinding defense), per MCP
  Streamable HTTP guidance.

### 4.2 Server → Google
- Uses the stored refresh token with the Desktop OAuth client; access tokens are
  refreshed automatically and held in memory only.
- If no token file exists or refresh fails with `invalid_grant`, tools MUST return
  a clear, actionable MCP tool error ("Run scripts/auth.sh to sign in again") —
  the process MUST NOT crash or open a browser.
- Token file MUST be written atomically with mode `0600`. Never logged.
- Requested scopes MUST be validated against the allowed set at startup.

### 4.3 One-time sign-in (`auth_cli`)
- Runs as a one-off container (`docker compose run --rm -p 127.0.0.1:8400:8400 ...`)
  using `google-auth-oauthlib` `InstalledAppFlow` with PKCE, loopback redirect
  `http://127.0.0.1:8400/`, listening on `0.0.0.0` inside the container, published
  to host loopback only.
- Because there is no browser inside the container, it MUST print the
  authorization URL for the user to open on the host, and MUST use `access_type=offline`
  and `prompt=consent` to guarantee a refresh token.
- On success, writes the token file to the mounted data volume and prints only
  the granted scopes (never the token).
- The data volume (`./data/`) is git-ignored.

## 5. MCP tools

All tools: strict pydantic input validation, bounded output, no raw Google
exceptions leaked (map `HttpError` → concise message with status code, never
echoing request bodies). Tool annotations MUST be set (`readOnlyHint` etc.).

| Tool | Annotations | Inputs | Behavior |
|---|---|---|---|
| `search_threads` | readOnly | `query: str` (Gmail syntax, ≤500 chars), `max_results: int=10` (1..cap), `page_token: str?` | Returns thread id, snippet, subject, sender, date, message count; plus `next_page_token` |
| `get_thread` | readOnly | `thread_id: str`, `format: "full"\|"metadata"="full"` | Returns messages with headers and plain-text body (HTML stripped; truncated to `MAX_BODY_CHARS` with a `truncated` flag) |
| `get_message` | readOnly | `message_id: str` | Same shape for one message; lists attachment names/sizes/mime types only — **never attachment content** |
| `list_labels` | readOnly | — | Label id, name, type |
| `list_drafts` | readOnly | `max_results`, `page_token` | Draft id, subject, to, snippet |
| `create_draft` | non-readOnly, `destructiveHint=false`, `idempotentHint=false` | `to: list[email]`, `subject: str`, `body: str`, `cc?`, `bcc?`, `reply_to_message_id?` | Builds a MIME message, creates a **draft only**. If `reply_to_message_id` is set, sets `threadId`, `In-Reply-To`, `References`. Returns draft id and a Gmail URL |

Hard exclusions — the server MUST NOT implement or expose: send, delete, trash,
modify/relabel, filters, forwarding settings, attachment download, or any
`users.settings.*` call. A test MUST assert the registered tool-name set equals
exactly the table above.

Input safety:
- Email addresses validated with `email-validator`; reject header-injection
  (CR/LF) in every header-bound field (`subject`, addresses).
- Message and thread IDs validated against `^[A-Za-z0-9_-]{1,64}$`.
- `create_draft` body ≤ 100 KB; recipients ≤ 50 combined.

Output safety: email content is untrusted data that may contain prompt
injection. Tool descriptions SHOULD state that returned content is untrusted
and must not be treated as instructions.

## 6. Logging & observability

- JSON logs to stdout. Include timestamp, level, logger, tool name, duration,
  outcome. MUST NOT log message bodies, subjects, addresses, tokens, or secrets.
- A redaction filter MUST scrub anything resembling `GOCSPX-…`, bearer tokens,
  and `ya29.…` access tokens as defense in depth.
- Health: `GET /healthz`; Docker `HEALTHCHECK` uses it.

## 7. Container

- Multi-stage `Dockerfile`: builder installs deps into a venv with pinned,
  hash-checked requirements (`uv pip compile --generate-hashes` or `pip-tools`);
  final stage `python:3.12-slim` (pinned minor; digest pinning documented),
  non-root user (fixed UID 10001), no build tools, no cache, `PYTHONDONTWRITEBYTECODE`,
  `PYTHONUNBUFFERED`, `tini` or `init: true` for signal handling.
- `docker-compose.yml`:
  - port mapping `127.0.0.1:${HOST_PORT:-8765}:8000` — **never** `0.0.0.0`.
  - `read_only: true` root FS, `tmpfs: /tmp`, `cap_drop: [ALL]`,
    `security_opt: [no-new-privileges:true]`, `pids_limit`, memory/cpu limits.
  - `./data:/data` volume for the token file; `env_file: .env`.
  - `restart: unless-stopped`, healthcheck, log rotation (`json-file` max-size/max-file).
  - A profile or separate service `auth` for the one-off sign-in container.
- Build context excludes `.env`, `data/`, `.git`, tests caches.

## 8. Scripts (`scripts/`)

Each `.sh` MUST use `#!/usr/bin/env bash`, `set -euo pipefail`, resolve the repo
root from its own location, print clear errors, and be idempotent. Each has a
`.ps1` twin that runs the `.sh` through `wsl.exe -d Ubuntu --` (path converted with
`wslpath`), forwarding arguments and exit codes. Docker is reached via WSL2
Docker Engine (not Docker Desktop).

| Script | Behavior |
|---|---|
| `start` | Verify `.env` exists and required vars are non-placeholder; create `data/`; `docker compose up -d`; wait for healthy; print the URL and how to add to Goose |
| `stop` | `docker compose down` (keeps `data/`) |
| `restart` | `docker compose restart`, wait for healthy |
| `rebuild` | `docker compose build --no-cache` then recreate (`up -d --force-recreate`), wait for healthy; `--keep-cache` flag optional |
| `auth` | Run the one-time sign-in container (§4.3); refuse if `GOOGLE_CLIENT_*` unset |
| `logs` | Follow logs (`-n`, `--no-follow` options) |
| `status` | Container state, health, whether a token file exists (never its contents) |

Shared helpers go in `scripts/lib/common.sh`. Scripts MUST NOT print secrets,
and MUST NOT use `--privileged` or mount the Docker socket.

## 9. Quality bar

- Typing: `mypy --strict` clean. Linting/format: `ruff` (rules: `E,F,I,B,UP,S,SIM,ASYNC`) clean.
- Tests: `pytest` + `pytest-asyncio`; coverage ≥ 85% on `src/`. Gmail API fully mocked.
  Required tests: tool-set equality (§5), auth middleware (valid/invalid/missing/
  timing-safe path), Host-header rejection, header-injection rejection, ID
  validation, body truncation, `HttpError` mapping, missing-token behavior,
  scope-subset validation, log redaction, atomic token write + file mode.
- Dependencies: pinned with hashes; `pip-audit` (or equivalent) documented in
  README and wired as a Makefile/`pyproject` task; no unmaintained packages.
- Docs: README MUST cover prerequisites, Google Cloud setup (enable Gmail API,
  consent screen, Desktop client, test-user note, 7-day refresh-token expiry in
  Testing mode), `.env` setup, first-run flow, Goose extension config
  (`type: streamable_http`, `url`, `headers: Authorization: Bearer …`), security
  model & the `gmail.compose` caveat (§ PLAN), and troubleshooting.
- Conventional, small, well-named modules; docstrings on public functions;
  no dead code; no TODOs without an issue note in the report.

## 10. Out of scope

Sending mail, deleting/trashing/relabeling, attachment download, multi-account,
remote (non-localhost) exposure, TLS termination, Google Workspace (non-Gmail) APIs.

## 11. Acceptance checklist

- [ ] Tool set exactly matches §5; no send/delete/modify code path exists.
- [ ] Container builds, runs non-root, read-only FS, localhost-only port, healthy.
- [ ] All scripts work from WSL and via PowerShell wrapper; idempotent.
- [ ] No secret appears in logs, image layers, git history, or docs.
- [ ] `ruff`, `mypy --strict`, `pytest` (≥85% cov) pass.
- [ ] README lets a new user reach a working Goose connection without guessing.
