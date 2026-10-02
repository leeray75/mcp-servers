# Implementation report

Scope: SPEC sections 2-9 (PLAN phases 1-8). Implemented by the implementer agent on 2026-10-01.
No real Google credential, client secret or token was read, printed or used. No `git push` was run.
Everything below was actually executed; items I could not verify are listed in the last section.

## Environment

- Host Python 3.10 (too old). Used the Windows `py -3.13` (3.13.15) for a local venv (`.venv/`, git-ignored)
  and, for a second independent run, `python:3.12-slim` (3.12.15) in a throwaway container with the
  hash-locked `requirements-dev.txt`.
- `mcp` SDK installed was **2.2.0** (latest). Its API differs from the 1.x "FastMCP" in the SPEC; verified against the
  installed package before coding (see Deviations).

## What was built, per SPEC section

| SPEC | Delivered |
|---|---|
| 2 Layout | `src/gmail_mcp/` (all listed modules + `errors.py`, `parsing.py`), `tests/`, `scripts/`, `docs/`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.env.example`, `.gitignore`, `.dockerignore`, `CHANGELOG.md`, `README.md`, plus `Makefile`, `.gitattributes`, `requirements.txt`, `requirements-dev.txt`. src layout, package `gmail_mcp`, Python >=3.12. |
| 3 Config | `config.py`: one `pydantic-settings` class; only module touching the environment. Fail-fast `ConfigError` lists field names + constraint text only (`hide_input_in_errors`; message rebuilt from error locations). Secrets are `SecretStr`. Scope aliases (`gmail.readonly`) normalised to full URLs and validated as a subset. `replace-me*` placeholders and `MCP_AUTH_TOKEN` < 32 chars rejected. `.env` git- and docker-ignored; `.env.example` placeholders only. |
| 4.1 Client auth | `security.py`: pure-ASGI `BearerAuthMiddleware` (`hmac.compare_digest`, always executed even for a missing header; uniform `401 {"error":"unauthorized"}` with no detail; redacted warning log) and `HostGuardMiddleware` (421 on bad Host, 403 on non-loopback Origin). `GET /healthz` returns `{"status":"ok"}` unauthenticated and independent of credential state (tested). The SDK's own DNS-rebinding protection is also enabled. |
| 4.2 Google auth | `auth.py`/`gmail_client.py`: refresh token loaded from `TOKEN_PATH`, access token memory-only, 30 s HTTP timeout. Missing/invalid/revoked token -> `AuthRequiredError` -> MCP tool error "Gmail is not authorised. Run scripts/auth.sh ..."; process never crashes or opens a browser; service is rebuilt lazily so a fresh `auth` is picked up without restart. Token written atomically (mkstemp 0600 in the same dir, fsync, `os.replace`). Stored token contains only refresh token + scopes (not the client secret). Scopes validated at startup and against the stored token. |
| 4.3 Sign-in | `auth_cli.py`: `InstalledAppFlow` (PKCE S256, confirmed in a test), `access_type=offline`, `prompt=consent`, redirect `http://127.0.0.1:8400/`, listens on `0.0.0.0` inside the container, `open_browser=False`, prints the URL and, on success, only the granted scopes. Fails clearly if no refresh token / missing scopes. |
| 5 Tools | Exactly six: `search_threads`, `get_thread`, `get_message`, `list_labels`, `list_drafts`, `create_draft`, with annotations (`readOnlyHint`; `create_draft`: not read-only, `destructiveHint=false`, `idempotentHint=false`). Strict validated types (`models.py`): ID regex, CR/LF/control-char rejection in subject/addresses/query, `email-validator` addresses (no display names), body <=100 KB (bytes), <=50 recipients, query <=500, result cap enforced in the JSON schema (`maximum` = `MAX_RESULTS_CAP`). Output bounded (per-message truncation + `truncated` flag, thread budget 5x, <=50 most-recent messages). HTML stripped with the stdlib parser. `get_message` lists attachment name/type/size only. `HttpError` mapped to `Gmail API error: HTTP <status> <phrase>` (body never echoed); unexpected exceptions become a generic tool error. Reply drafts set `threadId`, `In-Reply-To`, `References`. Descriptions state returned content is untrusted. No send/delete/trash/modify/settings/attachment code path exists; tests assert both the tool-name set and (by source scan) the absence of those API calls, and that `GmailClient` has no other public mutators. |
| 6 Logging | JSON to stdout (timestamp, level, logger, tool, duration_ms, outcome). Only a fixed set of extra fields is emitted; exceptions log only the class name. `RedactionFilter` + formatter-level scrub of `GOCSPX-`, `ya29.`, `1//` refresh tokens, bearer values and the literal configured secrets. uvicorn access log disabled. Healthcheck via `/healthz`. |
| 7 Container | Multi-stage `Dockerfile`: builder installs `requirements.txt` with `--require-hashes --only-binary=:all:`; runtime `python:3.12-slim`, UID/GID 10001, no build tools, no pip cache, `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED`, app run from `/app/src` (no unhashed build backend), HEALTHCHECK with `python`. Compose: `127.0.0.1:${HOST_PORT:-8765}:8000`, `read_only`, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, `init: true` (tini), pids/memory/cpu limits, `./data:/data`, `env_file: .env`, `restart: unless-stopped`, healthcheck, json-file rotation, `auth` profile service. `.dockerignore` excludes `.env`, `data/`, `.git`, caches, tests, docs. Digest pinning documented in the Dockerfile header. |
| 8 Scripts | `start stop restart rebuild auth logs status` as `.sh` (`#!/usr/bin/env bash`, `set -euo pipefail`, root resolved from own location, shared `scripts/lib/common.sh`) and `.ps1` twins (dot-source `lib/Invoke-WslScript.ps1`, `wslpath`, `wsl.exe -d Ubuntu --`, args + exit code forwarded). `.env` parsed as data (never sourced), only variable names printed, no `--privileged`, no docker socket mount. `data/` is made writable for UID 10001 via a short-lived container with only `CAP_CHOWN` (no sudo). |
| 9 Quality | See results. Docs: README covers every item listed in SPEC 9 including Goose config, security model, `gmail.compose` caveat, troubleshooting, `pip-audit`. `Makefile` targets: `check`, `audit`, `lock`. |

## Commands run and real outcomes

| Command | Outcome |
|---|---|
| `pytest` (Windows, Python 3.13.15) | **207 passed, 1 skipped** (POSIX mode test; Windows has no meaningful file modes). Coverage **99.02%** (gate 85%). |
| `pytest` (Linux container, Python 3.12.15, hash-locked dev deps; run before the last 10 security tests were added) | **197 passed, 0 skipped** (the 0600 file-mode test ran and passed), coverage 97.82%. I did not re-run the Linux container after adding `tests/test_security.py`; the Windows run covers it. |
| `ruff check .` / `ruff format --check .` | clean / 29 files already formatted (also clean in the Linux run) |
| `mypy` (`strict = true`, src + tests) | `Success: no issues found in 25 source files` (Windows and Linux) |
| `pip-audit --requirement requirements.txt --require-hashes --disable-pip` | `No known vulnerabilities found` |
| `uv pip compile ... --generate-hashes --universal` | produced `requirements.txt` (runtime) and `requirements-dev.txt` |
| `scripts/start.sh` (via `wsl.exe -d Ubuntu`) with a throwaway fake `.env` | image built (hash-checked install worked), container healthy in the first poll |
| Container checks | `uid=10001(gmailmcp)`; `touch /x` -> read-only file system; `/data` writable; inspect: `ReadonlyRootfs=true`, `CapDrop=[ALL]`, `no-new-privileges`, `init=true`, pids 128, mem 512 MB, port binding `127.0.0.1:18765`->8000 (`ss` shows `127.0.0.1:18765` only); `GET /healthz` -> 200 `{"status":"ok"}`; `POST /mcp` without token -> 401 `{"error":"unauthorized"}` + `WWW-Authenticate: Bearer`; wrong token -> 401; `Host: evil.com` -> 421; correct (fake) token -> `initialize` succeeded and `tools/call list_labels` returned the actionable "Run scripts/auth.sh" tool error (server stayed up). Logs were JSON, no secrets. Image history contains no secret strings; no `.env` in the image. Image size ~309 MB. |
| `status.sh`, `logs.sh` (valid and invalid options), `restart.sh`, `stop.sh` (twice), `status.sh`, `rebuild.sh --keep-cache`, `rebuild.sh` (no cache), bad-option cases | all behaved as designed; stop and restart are idempotent. Two bugs found and fixed during this testing: `container_state` printed an empty line when the container did not exist, and the failure-path log redirect order was wrong. |
| `status.ps1`, `logs.ps1 -n 1 --no-follow`, `logs.ps1 --bogus` via both `pwsh` 7 and Windows PowerShell 5.1 | worked; exit code `1` propagated for the bad option |
| `auth.sh` with **fake** credentials for ~25 s, then killed by `timeout` | printed the authorisation URL (PKCE `code_challenge_method=S256`, `access_type=offline`, `prompt=consent`, `redirect_uri=http://127.0.0.1:8400/`); `ss` showed `127.0.0.1:8400` only; the `--rm` container was gone afterwards. I did not complete a sign-in and no request to Google was made. |
| chown helper test on an ext4 WSL directory | `chown` with only `CAP_CHOWN` (read-only, no-new-privileges) worked; the directory then was writable by UID 10001 |
| Teardown | container stopped and removed, network removed, fake `.env` deleted. The `gmail-mcp-server:local` image and the `python:3.12-slim` base image remain in the WSL Docker store. |

## Deviations from the SPEC (and why)

1. **`mcp` SDK 2.2.0 `MCPServer`, not `FastMCP`.** The latest release renamed `FastMCP` to `MCPServer`
   (`from mcp.server.mcpserver import MCPServer`; `mcp.server.fastmcp` raises with a migration notice).
   I used the current API rather than pinning `<2`. `pyproject.toml` pins `mcp>=2.2,<3`. Middleware is attached to
   the Starlette app returned by `streamable_http_app()`; `/healthz` is added with `app.add_route` (the SDK's
   `custom_route` decorator is untyped under `mypy --strict`).
2. **Goose key is `uri`, not `url`.** SPEC 9 says `url`; Goose's documented/seen config field is `uri` (verified against
   Goose docs and published examples via a web search; I could not run Goose). The README uses `uri` and states why.
3. **Host check accepts any numeric port** for `127.0.0.1` / `localhost` (SPEC: `127.0.0.1:<port>`). The container sees the
   published host port (e.g. 8765), not its internal 8000, so a single fixed port cannot be hard-coded. The security property
   (an attacker-controlled hostname is rejected) is preserved; documented in the README and module docstring.
4. **Stateless HTTP + JSON responses** (`stateless_http=True`, `json_response=True`) so clients survive server restarts and
   no per-session state accumulates. Not in the SPEC; chosen for robustness and smaller attack surface.
5. **Extra modules** `errors.py` and `parsing.py` (not in the SPEC layout) to avoid circular imports and keep `tools.py`
   focused. Extra files `Makefile`, `.gitattributes`, `requirements*.txt`.
6. **App is run from source in the image** (`PYTHONPATH=/app/src`) instead of `pip install .`, so no build backend is downloaded
   without hashes. `pyproject.toml` still defines an installable package for development.
7. **Token file contains the refresh token and scopes only** (not Google's standard authorized-user JSON, which embeds the client
   secret). Consequence: tokens made by other tools are not interchangeable.
8. **Thread output limits** (<=50 most-recent messages, 5x total body budget) and `search_threads` makes one metadata request per
   result (N+1, up to the cap) - added to satisfy "bounded output"; not specified.
9. **`create_draft` is registered even if `GMAIL_SCOPES` is read-only**; Gmail returns 403 and the tool reports it.
10. **Windows `py -3.13` was used instead of 3.12** for the local venv (3.12 not installed); 3.12 was covered with the container run.

## Known gaps / things I could not verify

- **No end-to-end test against real Google**, by design: the OAuth round-trip, token refresh against Google, real Gmail JSON shapes,
  and draft creation in a real mailbox are verified only with mocks. The Gmail field handling follows the public API docs.
- **Goose was not run**; the `uri` config shape comes from public docs/examples. Reaching the endpoint from Windows was only checked
  with `curl` (Git Bash) against WSL's localhost forwarding, which worked.
- The PowerShell wrappers were tested with `status`/`logs` only; `start/auth/rebuild` `.ps1` twins share the same code path but were
  not each executed.
- The 0600 mode test is skipped on Windows; it passed in the Linux run. The Linux run predates `tests/test_security.py`.
- Hash locks use `--universal` (include Windows-only markers such as `pywin32`); only the Linux/amd64 resolution was exercised in
  the Docker build. Arm64 images should work (wheels are universal-resolved) but were not built.
- `data/` can become owned by UID 10001 on native Linux filesystems, so deleting it needs `sudo` or the documented container command.
- `RefreshError` handling treats every refresh failure as "sign in again"; a transient Google outage during refresh would show the
  same message. If Google rotates the refresh token, the new one is not persisted (Google normally does not rotate for Desktop clients).
- No TODOs were left in the code.
