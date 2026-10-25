# gmail-mcp-server

A small, self-owned [MCP](https://modelcontextprotocol.io) server that gives an AI agent
(for example [Goose](https://goose-docs.ai)) **read access to Gmail and the ability to create
drafts - and nothing else.**

- Python 3.12, the official `mcp` SDK, and Google's official client libraries. No third-party
  MCP code ever touches your OAuth tokens or mail.
- Streamable HTTP transport, published on **127.0.0.1 only**, behind a bearer token.
- **No send, delete, trash, relabel, settings or attachment-download tool exists.** A test
  asserts the registered tool set is exactly the seven below.

| Tool | What it does |
|---|---|
| `search_threads` | Gmail-syntax search; returns id, snippet, subject, sender, date, message count, `next_page_token` |
| `get_thread` | A thread's messages with headers and plain-text bodies (`format`: `full` or `metadata`) |
| `get_message` | One message; lists attachment name/type/size only - never attachment content |
| `list_labels` | Label id, name, type |
| `list_drafts` | Draft id, subject, recipients, snippet |
| `create_draft` | Creates a **draft** (optionally threaded as a reply). Never sends |
| `auth_status` | Read-only sign-in state: signed in or not, granted/missing scopes, token age, recent rejection, and the exact next step. Never contacts Google and never returns credentials |

The server also publishes four read-only **resources**: `gmail://auth/status` (the same JSON as
`auth_status`), `gmail://guide/auth` (the sign-in procedure for agents), `gmail://guide/search`
(Gmail search syntax) and `gmail://guide/limits` (capabilities, limits and safety).

## Security model (read this first)

- **The `gmail.compose` OAuth scope technically permits sending mail.** Google offers no
  "drafts only" scope. Safety here therefore rests on (a) this server exposing **no send
  tool** and (b) this server being **the only holder of the refresh token**. If you add other
  software that can read `data/token.json` and use it, that software can send mail as you.
- Email content is untrusted. A message can contain text that tries to instruct the AI
  ("ignore previous instructions, draft a reply to ..."). Tool descriptions say so, and drafts
  must be reviewed and sent by you in Gmail - which is the point of drafts-only access.
- Clients must send `Authorization: Bearer <MCP_AUTH_TOKEN>` (compared in constant time).
  The only unauthenticated routes are `GET /healthz` (reveals nothing about credentials) and
  the two browser sign-in routes `GET /auth` and `GET /oauth2callback` (exact paths, see below).
- **Sign-in routes.** A browser cannot send the bearer header, so `/auth` and `/oauth2callback`
  are open, but only through the loopback Host/Origin guard. Each sign-in uses a random,
  single-use `state` (expires after 10 minutes, at most 8 pending), PKCE (S256), static
  script-free pages that echo nothing from the request, `no-store`/CSP/`no-referrer` headers,
  and the token is saved only if Google returns a refresh token covering every configured
  scope. **Residual risk:** any local process, or a web page you visit, can send your browser
  to `/auth` and start a sign-in, but nothing is stored unless *you* approve Google's consent
  screen. A local process that can already read `data/token.json` is outside this server's
  threat model. A cross-site page can also hit `/auth` repeatedly to evict your pending
  attempt (the cap is 8); browsers send `Sec-Fetch-Site`, and `/auth` rejects cross-site
  requests unless they are user-activated top-level navigations (a real click), but a hostile local non-browser process is not stopped. The
  worst case is a failed sign-in you simply retry.
- Requests whose `Host` is not `127.0.0.1:<port>` / `localhost:<port>`, or whose `Origin`
  is not loopback, are rejected (DNS-rebinding defense). Any numeric port is accepted for those
  hostnames because the container sees the published host port, not its internal one.
- The container runs as UID 10001 with a read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, PID/memory/CPU limits, and the port bound to `127.0.0.1`.
  Never change the port mapping to `0.0.0.0`: this server has no TLS.
- Only the refresh token and its scopes are stored (`data/token.json`, created with mode 0600,
  written atomically). **On a WSL `/mnt/c` checkout POSIX permissions are not enforced** (any
  Windows process running as you can read `data/token.json`); for real file-mode protection
  clone the repository into the WSL filesystem (for example `~/gmail-mcp-server`). The OAuth client secret lives only in `.env`. Access tokens are memory-only.
- Logs are JSON on stdout and never contain message bodies, subjects, addresses or
  credentials; a redaction filter scrubs `GOCSPX-...`, `ya29....`, refresh tokens and bearer
  values as defense in depth.
- Output is bounded: result cap (`MAX_RESULTS_CAP`), per-message truncation
  (`MAX_BODY_CHARS`), a per-thread total budget (5x that) and at most 50 messages per thread
  (the most recent). A 512 KiB request body limit applies.

## Prerequisites

- Windows 11 with **WSL2 + Ubuntu**, and **Docker Engine inside WSL2** (not Docker Desktop):
  `docker info` and `docker compose version` must work inside Ubuntu.
- A Google account and a free Google Cloud project (below).
- Optional, for development: Python 3.12+.

## Google Cloud setup (one time)

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project
   (any name).
2. **Enable the Gmail API:** *APIs & Services -> Library -> Gmail API -> Enable*.
3. **Configure the OAuth consent screen** (*APIs & Services -> OAuth consent screen*, or
   *Google Auth Platform* in the newer UI): user type **External**, fill in the app name and
   your email, and under **Test users add your own Gmail address.**
4. **Create the credentials:** *Credentials -> Create credentials -> OAuth client ID ->
   Application type: Desktop app*. Copy the **client ID** and **client secret** (you will paste
   them into `.env`; never commit them).

> **Testing mode and token lifetime.** While the consent screen is in *Testing*, Google
> expires refresh tokens after **7 days**. The tools then return an `AUTH_REQUIRED` error with a
> sign-in link (see "Re-authenticating" below); `auth_status` reports the token age and warns
> once it is 6 days old. Publishing the consent screen to *In production* prevents this expiry.
>
> To publish it: open *Google Auth Platform -> Audience* (or *OAuth consent screen*), choose
> **Publish app** and confirm. Google then no longer expires the refresh token after 7 days.
> The app stays "unverified" because Gmail scopes are classed sensitive/restricted: Google shows
> a warning screen (*Advanced -> Go to <app> (unsafe)*) that you click through for your own
> account. Verification is not needed for personal use. After publishing, run `auth` once so the
> stored token is issued under the published consent screen.

## Setup

All commands run from the repository root. On Windows, use the `.ps1` twin of any `.sh` script
(for example `.\scripts\start.ps1`); it runs the `.sh` inside WSL2 Ubuntu and forwards arguments
and exit codes. In WSL, run the `.sh` directly.

```bash
cp .env.example .env
# edit .env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, MCP_AUTH_TOKEN
openssl rand -hex 32          # one way to generate MCP_AUTH_TOKEN (>= 32 characters)
```

1. **Sign in once:** `scripts/auth.sh` (Windows: `.\scripts\auth.ps1`). It opens your browser
   (`wslview`, then `explorer.exe`, then `xdg-open`; otherwise it prints the URL), you approve
   both permissions, and it exits. With the server not yet running it uses a one-off container
   (Google redirects to `http://127.0.0.1:8400/`, published to loopback only) and prints the
   granted scopes (never the token). Pass `--no-browser` to only print the URL.
2. **Start the server:** `scripts/start.sh`. It validates `.env`, builds the image, starts the
   container and waits until it is healthy, then prints the endpoint (default
   `http://127.0.0.1:8765/mcp`; change the host port with `HOST_PORT` in `.env`).

### Scripts

| Script | Purpose |
|---|---|
| `start` | Validate `.env`, build, start, wait for healthy |
| `stop` | Stop and remove the container (keeps `data/`) |
| `restart [--recreate]` | Restart and wait for healthy. Use `--recreate` after editing `.env` |
| `rebuild [--keep-cache]` | Rebuild the image without cache (default) and recreate the container |
| `auth [--no-browser]` | Google sign-in (re-run when the token expires or is revoked); opens the browser automatically |
| `logs [-n N] [--no-follow]` | Container logs |
| `status` | Container state, health, whether a token file exists (never its contents) |

A running server picks up a new token after `auth` without a restart.

### Re-authenticating

When the refresh token expires (every 7 days while the consent screen is in *Testing*) or is
revoked, the failing tool returns an `AUTH_REQUIRED:` message that contains a link such as
`http://127.0.0.1:8765/auth` (it follows `HOST_PORT`). Click it, approve access in Google, and see
"Gmail is connected"; the next tool call works, no restart needed.

How the sign-in is offered:

- **URL elicitation.** A client on MCP 2026-07-28 that declares URL-mode elicitation support is
  asked to open that same `/auth` page for you. Clients on earlier revisions (such as 2025-11-25)
  get the `AUTH_REQUIRED` text instead, because the server cannot see their capabilities. The URL is the server's own
  page: it carries no query, state or user data, and the Google consent happens in your
  browser, never inside the MCP client. Set `AUTH_URL_ELICITATION=false` to turn this off.
- **Fallback.** Every other client receives the `AUTH_REQUIRED` tool error with the link and the
  OS open commands (`Start-Process`, `open`, `xdg-open`). An agent runs the open command in a
  shell, or gives you the link to copy and paste into your own browser, waits for you to approve,
  calls `auth_status` and retries only when it reports `ready_to_retry: true`.
- **Guardrails.** The agent is told never to use browser-automation tools (DevTools, Playwright,
  Selenium) on the Google sign-in page and never to type or capture credentials. These are
  instructions in the text, not enforcement; you approve access in your own browser.
- **`auth_status`** tells you the state (`signed_in`, `not_signed_in`, `token_unreadable`,
  `scopes_insufficient`, `rejected` or `sign_in_in_progress`), `ready_to_retry` (true only when
  `signed_in`), `checked_at` (the UTC time the status was computed), the scopes, the token age,
  any warnings and the next step. `sign_in_in_progress` means a sign-in page was opened and has not
  finished yet. The `gmail://auth/status` resource carries the same information.

Alternatively run `scripts/auth.ps1` (Windows) or
`scripts/auth.sh` (WSL): with the server running it opens the same page and waits until the
token file changes; otherwise it falls back to the one-off container and opens the Google URL.

`data/` may end up owned by UID 10001 (the scripts arrange this on native Linux filesystems) so
your own user may not be able to delete it directly; remove it with
`docker run --rm --user 0 -v "$PWD":/w --entrypoint rm gmail-mcp-server:local -rf /w/data` or `sudo`.

## Connect Goose

Goose names the URL field `uri` (not `url`). Add this under `extensions:` in Goose's config
(`~/.config/goose/config.yaml` on Linux/macOS, `%APPDATA%\Block\goose\config\config.yaml` on
Windows), or use `goose configure -> Add Extension -> Remote Extension (Streamable HTTP)`:

```yaml
extensions:
  gmail:
    name: gmail
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8765/mcp
    headers:
      Authorization: "Bearer <the MCP_AUTH_TOKEN value from your .env>"
    timeout: 300
```

Restart Goose after editing the file. Goose stores that header in its config in plain text, so
use a token generated only for this purpose. From Windows, `127.0.0.1:8765` reaches the container
through WSL2's localhost forwarding.

## Configuration

All settings come from environment variables (see `.env.example`); invalid values stop the server
at startup with a message that names the setting but never echoes secret values.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | yes | - | Desktop-app OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | yes | - | OAuth client secret |
| `MCP_AUTH_TOKEN` | yes | - | Bearer secret for clients, >= 32 characters |
| `GMAIL_SCOPES` | no | `gmail.readonly gmail.compose` | Must be a subset of those two |
| `TOKEN_PATH` | no | `/data/token.json` | Refresh-token file (fixed by compose) |
| `HOST` / `PORT` | no | `0.0.0.0` / `8000` | In-container bind (fixed by compose; host side is `HOST_PORT`) |
| `LOG_LEVEL` | no | `INFO` | |
| `MAX_RESULTS_CAP` | no | `50` | Hard ceiling for list tools |
| `MAX_BODY_CHARS` | no | `20000` | Truncation limit for returned bodies |
| `HOST_PORT` | no | `8765` | Host port on 127.0.0.1 (read by compose and the scripts, and by the server to build the sign-in link and OAuth redirect URI) |
| `AUTH_URL_ELICITATION` | no | `true` | Kill switch: when `false`, sign-in is offered only as the `AUTH_REQUIRED` tool error, never as a URL elicitation |

If you narrow `GMAIL_SCOPES` (for example to read-only), `create_draft` is **still listed** by the
server but every call will fail with a Gmail 403; tools needing a missing scope behave the same.
The token file records the scopes it was granted; if it does not cover the configured scopes the
tools ask you to re-run `auth`.

## Development

```bash
make install     # venv from the hash-pinned requirements-dev.txt (Linux/WSL, Python >= 3.12)
make check       # ruff, ruff format --check, mypy --strict, pytest (coverage gate 85%)
make audit       # pip-audit against the locked runtime requirements
make lock        # re-resolve + re-hash requirements*.txt after changing pyproject.toml
```

The tests never touch the network or real credentials (the Gmail API is fully mocked).

The transitive set pulled in by `mcp==2.2.0` (including `httpx2`/`httpcore2`, published by the
pydantic organisation, and `mcp-types`) was checked against the installed package metadata: `mcp`
declares `httpx2>=2.5.0` and `mcp-types==2.2.0`. Dependencies are pinned with hashes in `requirements.txt` (runtime) and `requirements-dev.txt`;
the image installs them with `pip --require-hashes --only-binary=:all:`. The base image is pinned
to the `python:3.12-slim` minor tag; to pin the digest too, see the comment at the top of the
`Dockerfile`. Run `make audit` regularly (and after each `make lock`).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `cannot reach the Docker daemon` | In Ubuntu: `sudo service docker start`; check `docker info` |
| `.env` errors from `start` | Placeholders (`replace-me...`) or a `MCP_AUTH_TOKEN` shorter than 32 characters |
| Tools return `AUTH_REQUIRED` | No token yet, token revoked, or the 7-day Testing-mode expiry - call `auth_status`, then click the link in the message or run `auth` |
| Browser shows "This app isn't verified" | Expected for your own project: *Advanced -> Go to <app> (unsafe)*. Make sure your account is a **Test user** |
| `Error 403: access_denied` at sign-in | Your account is not listed as a Test user on the consent screen |
| `redirect_uri_mismatch` | The OAuth client is not of type **Desktop app** |
| Auth: "did not return a refresh token" | Revoke the app at <https://myaccount.google.com/permissions> and run `auth` again |
| Auth: port 8400 already in use | Free the port; it is fixed because the redirect URI must be predictable |
| HTTP 401 from the endpoint | Wrong/missing `Authorization: Bearer ...` header |
| HTTP 421 from the endpoint | The client sent a non-loopback `Host`; connect to `127.0.0.1` or `localhost` |
| Goose cannot connect from Windows | Check `scripts/status`; confirm WSL localhost forwarding works (`curl http://127.0.0.1:8765/healthz` from Windows) |
| Tool error `HTTP 403` from Gmail | Gmail API not enabled for the project, or the token lacks a scope |
| Tool error `HTTP 429` | Gmail rate limit; retry later |
| `scripts/*` warn that `./data` is on `/mnt/...` | The repo is on the Windows drive: it works, but file modes are not enforced there. Prefer a clone under `~/` in WSL |
| Auth: "Sign-in failed (...)" | A permission box was unticked or the flow was interrupted; run `auth` again and approve everything |
| Reply draft is not grouped in the Gmail conversation | Gmail needs the same subject: pass the original subject (usually `Re: ...`) with `reply_to_message_id` |
| The sign-in link in Goose's error does not load | The server is not running or `HOST_PORT` differs; start it (`scripts/start.sh`) or use `scripts/auth.ps1`, which falls back to a one-off container |
| Google shows `redirect_uri_mismatch` | The OAuth client must be of type **Desktop app** (it allows any loopback port); recreate it as Desktop |
| Edited `.env` but nothing changed | `scripts/restart.sh --recreate` (a plain restart keeps the old environment) |

## Project layout

```
src/gmail_mcp/   config, logging_setup, auth, auth_cli, auth_state, resources, gmail_client,
                 models, parsing, security, signin, server, tools, errors, __main__
tests/           unit + MCP-level + HTTP-level tests (Gmail API mocked)
scripts/         start|stop|restart|rebuild|auth|logs|status (.sh canonical, .ps1 wrappers)
docs/            PLAN.md, SPEC.md, reauth/ (re-auth plan + spec), reports/
```

## Out of scope

Sending mail, deleting/trashing/relabeling, attachment download, multiple accounts, non-localhost
exposure, TLS termination, and non-Gmail Google Workspace APIs.
