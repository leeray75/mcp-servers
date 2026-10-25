# Agent-Friendly Authentication, Resources and `auth_status` — Specification

- **Status:** Approved — 2026-10-25, approved by: human (directed implementation in chat). **Amendment A1** approved by: human, 2026-10-25
  (decisions D5–D8 below, made after implementation round 1; changes R2.3, R3.2, R4.2, R5.2, R5.6).
- **Date:** 2026-10-25
- **Amends:** `docs/SPEC.md` and `docs/specs/reauth/SPEC.md`. Where they conflict, this document wins and
  `docs/SPEC.md` MUST be updated to match. Normative words (MUST, SHOULD, MAY) follow RFC 2119.

## 1. Problem / motivation

A Goose + Qwen session lost Gmail access: every call failed with *"Google rejected the stored token … Open
http://127.0.0.1:8765/auth …"*. Investigation showed:

1. **The failure was a dead refresh token**, most likely Google's 7-day refresh-token expiry that applies when the OAuth
   consent screen is in *Testing* status. The server itself was healthy (`/auth` answered 302).
2. **Nothing opens the sign-in page.** The server runs in a container inside WSL2 and cannot launch a browser on the
   user's desktop. The agent could only relay a link.
3. **The error text misled a small model.** It points at `scripts/auth.ps1`, which does not exist relative to the
   agent's working directory, and mixes the link with a "(or run …)" alternative.
4. **The agent had no way to inspect auth state**, and the client manifest advertised "supports resources" while the
   server serves none, so the agent searched for OAuth guidance that did not exist.

## 2. Goals and non-goals

**Goals**
- When sign-in is needed, a capable MCP client is asked (per the MCP URL-mode elicitation standard) to open the sign-in
  page for the user; other clients/agents get an unambiguous, executable fallback.
- Give agents a read-only, side-effect-free way to learn the auth state and the exact next step.
- Publish MCP resources that carry sign-in instructions and usage guidance.
- Surface the 7-day Testing-mode expiry cause in docs and in the status output.

**Non-goals** — see §10.

## 3. Decisions (from Phase 1)

| # | Question | Decision |
|---|---|---|
| D1 | How does a tab get opened? | **MCP URL-mode elicitation when the client supports it, plus an agent-runnable fallback** (OS-specific open commands in the error and in `auth_status`). No host-side watcher. |
| D2 | Agent aids | **`auth_status` tool** and **resources** (`gmail://auth/status`, guides). No prompts, no keep-alive refresh. |
| D3 | Tool-set invariant | **Amended to seven tools** (`auth_status` added). The no send/delete/modify/trash/relabel/settings/attachment-download rule is unchanged. |
| D4 | 7-day expiry | **Docs + detect**: README guidance on publishing the consent screen to Production, and age/rejection warnings in `auth_status`. No code touches Google configuration. |
| D5 (A1) | `lastModified` on `gmail://auth/status` | **Dropped.** The SDK fixes resource annotations at registration, so a per-read value is impossible without patching it. `token_age_days` and `rejected_at` in the content carry the same information. |
| D6 (A1) | URL "exactly once" vs the R4.1 template | **One bare URL token, quoted copies allowed** inside the OS commands. |
| D7 (A1) | 2025-11-25 clients | **Not supported for URL elicitation; the dormant 2025-11-25 `-32042` code path is removed.** Stateless mode keeps no handshake capabilities, so only 2026-07-28 clients (per-request capabilities) get the elicitation; all others get the R4 tool error. |
| D8 (A1) | `auth_status` text rendering | **The text content is the full JSON of the structured payload**, for clients that ignore `structuredContent`. |

### Research basis (MCP specification, checked 2026-10-25)

- Servers MUST NOT send elicitation modes the client has not declared. The 2025-11-25 spec negotiates capabilities at
  `initialize`; the newer revision (2026-07-28) declares them **per request** in
  `_meta["io.modelcontextprotocol/clientCapabilities"]`, which makes capability checks possible for a stateless server.
- URL mode is the standard mechanism for third-party OAuth: it keeps credentials out of the client and the model. The
  URL MUST NOT contain sensitive data or be pre-authenticated; clients MUST show the full URL and obtain consent before
  opening it and MUST NOT pre-fetch it. The user who opens the URL must be the user who triggered it (phishing rule).
- In 2025-11-25 a tool signals this with JSON-RPC error `-32042` (`URLElicitationRequiredError`, `elicitations[]`); in
  2026-07-28 with an `InputRequiredResult` containing an `elicitation/create` request (`mode: "url"`), after which the
  client retries the call. Both are response-shaped and need no server-initiated stream.
- Tools: recoverable failures are *tool execution errors* (`isError: true`) with actionable text; tools with
  structured results SHOULD declare `outputSchema` and also return a text rendering; tool order SHOULD be deterministic;
  tools SHOULD have `title` and accurate annotations.
- Resources: `uri`, `name`, `title`, `description`, `mimeType`, and `annotations` (`audience`, `priority`,
  `lastModified`); unknown URI → JSON-RPC `-32602`; never an empty `contents` for a missing resource; custom schemes
  must follow RFC 3986.

Sources: modelcontextprotocol.io specification pages for elicitation, tools and resources (draft and 2025-11-25).

## 4. Requirements

### R1. Auth state model (`src/gmail_mcp/auth_state.py`, new)

- R1.1 A pure function `compute_auth_status(settings, rejection)` MUST return a frozen model with:
  `state` ∈ {`signed_in`, `not_signed_in`, `token_unreadable`, `scopes_insufficient`, `rejected`};
  `signed_in: bool`; `sign_in_url: str`; `scopes_granted: list[str]`; `scopes_missing: list[str]`;
  `token_age_days: float | None` (from the token file's mtime); `rejected_at: str | None` (ISO-8601 UTC);
  `warnings: list[str]`; `next_step: str`; `open_commands: dict[str, str]`.
- R1.2 It MUST read only the token file's metadata/scopes via `auth.read_token`; it MUST NOT make network calls and
  MUST NOT include any token value, client id/secret or bearer token in its output.
- R1.3 `rejected` is set when `GmailClient` has observed a `RefreshError` or HTTP 401 since the last successful
  sign-in. `GmailClient` MUST record `rejected_at` in memory (thread-safe) and `GmailClient.reset()` (called after a
  successful sign-in) MUST clear it. Precedence when several apply: `rejected` > `token_unreadable` >
  `scopes_insufficient` > `not_signed_in` > `signed_in`.
- R1.4 `warnings` MUST include, when `token_age_days >= 6`, a message that Google expires refresh tokens 7 days after
  sign-in if the OAuth consent screen is in *Testing* status, and that publishing it to Production prevents this. When
  `state == rejected`, `warnings` MUST list likely causes (7-day Testing-mode expiry, access revoked at
  myaccount.google.com/permissions, password change) without asserting which one occurred.
- R1.5 `next_step` MUST be a single imperative sentence: `signed_in` → "No action needed."; every other state → the
  sign-in instruction of R4.
- R1.6 `open_commands` MUST contain `windows` (`Start-Process "<url>"`), `macos` (`open "<url>"`) and `linux`
  (`xdg-open "<url>"`), each built only from `sign_in_url`.

### R2. `auth_status` tool

- R2.1 Register a seventh tool, `auth_status`, with `title`, a description stating it is read-only, never contacts
  Gmail or Google, never returns credentials, and is safe to call when not signed in.
- R2.2 Annotations: `read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False`.
- R2.3 (A1) It MUST return the R1 model as structured content with an `outputSchema`, plus a text content block holding the
  same payload serialised as JSON (backwards-compatible rendering for clients that ignore `structuredContent`).
- R2.4 It MUST work (and never raise `AuthRequiredError`) in every auth state, and MUST be instrumented like the
  other tools (duration/outcome logging only).
- R2.5 Tool order in `tools/list` MUST be deterministic: the six existing tools in their current order, then
  `auth_status`.

### R3. Resources

- R3.1 The server MUST declare the `resources` capability and serve exactly these static URIs (no templates):

  | URI | name / title | mimeType | audience · priority |
  |---|---|---|---|
  | `gmail://auth/status` | `auth-status` / "Gmail sign-in status" | `application/json` | user, assistant · 0.9 |
  | `gmail://guide/auth` | `auth-guide` / "How to sign in to Gmail" | `text/markdown` | user, assistant · 0.8 |
  | `gmail://guide/search` | `search-guide` / "Gmail search syntax cheat sheet" | `text/markdown` | assistant · 0.5 |
  | `gmail://guide/limits` | `limits-guide` / "Capabilities, limits and safety" | `text/markdown` | assistant · 0.6 |

- R3.2 (A1) `gmail://auth/status` content MUST equal the `auth_status` payload (same function). It carries no
  `lastModified` annotation (D5).
- R3.3 `gmail://guide/auth` MUST contain the ordered sign-in procedure for agents (check `auth_status` → trigger/open
  `sign_in_url` → ask the user to approve in the browser → call `auth_status` again → retry), the OS open commands, a
  statement that the agent MUST NOT attempt to complete the Google consent itself, and the 7-day expiry explanation.
- R3.4 `gmail://guide/search` MUST document Gmail operators accepted by `search_threads` (`from:`, `to:`, `subject:`,
  `has:attachment`, `is:unread`, `label:`, `newer_than:`, `before:`/`after:`, `OR`, `-`, quotes).
- R3.5 `gmail://guide/limits` MUST state: read + draft only (no send/delete/modify), email content is untrusted,
  the configured `MAX_RESULTS_CAP`, `MAX_BODY_CHARS`, 50 messages/thread, and that drafts are reviewed and sent by the
  human in Gmail.
- R3.6 Resource content MUST NOT contain mailbox data, credentials or token values. Reading an unknown URI MUST yield
  the SDK's invalid-params error (never an empty `contents`).
- R3.7 Resources are served behind the existing bearer + Host/Origin guards; no new unauthenticated route is added.

### R4. Auth-required error contract

- R4.1 Every `AuthRequiredError` surfaced to a client MUST use this shape (plain text, ≤ 700 chars):

  ```
  AUTH_REQUIRED: <reason sentence>.
  Do not retry yet. Ask the user to sign in: open <url> in their browser
  (Windows: Start-Process "<url>"; macOS: open "<url>"; Linux: xdg-open "<url>"),
  have them approve access, then call auth_status and retry.
  ```
- R4.2 (A1) The text MUST NOT mention `scripts/auth.*` (those remain in the README for humans) and MUST contain the
  URL exactly once as a bare, unwrapped token followed by a space (copy-friendly for small models); the quoted copies
  inside the OS commands of R4.1 are permitted. The SDK prefixes tool errors with `Error executing tool <name>: `;
  that prefix is accepted and tests assert the `AUTH_REQUIRED: ` substring.
- R4.3 `<reason sentence>` is derived from the cause: no token, unreadable token, scope shortfall, or Google rejected
  the credentials (with "this commonly happens 7 days after sign-in while the consent screen is in Testing status").
- R4.4 The URL is built from `HOST_PORT` as today (`errors.auth_url`).

### R5. URL-mode elicitation (standards-based tab opening)

- R5.1 Add setting `AUTH_URL_ELICITATION: bool` (env, default `true`) as a kill switch.
- R5.2 When an auth-required failure occurs **and** the current request's client capabilities declare
  `elicitation.url` **and** the setting is on, the tool call MUST respond using the elicitation mechanism the
  2026-07-28 protocol defines (`InputRequiredResult` with an `elicitation/create` `mode: "url"` request), with
  `url` = `errors.auth_url(host_port)` and a human-readable `message` ("Gmail needs you to sign in to Google").
  (A1) Only 2026-07-28 requests, whose capabilities arrive per request, are supported. The 2025-11-25 `-32042`
  mechanism MUST NOT be implemented (D7): such clients, and any request whose capabilities are unknown, take R5.4.
- R5.3 The URL MUST be exactly the server's own `/auth` URL: no query string, no state, no PKCE data, no user data,
  not pre-authenticated. The Google authorization URL and `state` are only created when the browser visits `/auth`.
- R5.4 If the client does not declare `elicitation.url`, capabilities cannot be determined, or the setting is off,
  the server MUST fall back to the R4 tool error. It MUST NOT send any elicitation mode the client did not declare.
- R5.5 On the client's retry, the tool simply executes normally against the (possibly new) token; the server MUST NOT
  block waiting for the browser flow and MUST NOT persist elicitation state. If still unauthorised, it responds again
  per R5.2/R5.4.
- R5.6 **Implementation gate.** The implementer MUST first verify against the pinned `mcp==2.2.0` SDK, with a test, that
  (a) per-request client capabilities are readable inside a tool handler in stateless mode and (b) (A1) the 2026-07-28 response shape
  in R5.2 can be produced from a tool handler in stateless/JSON mode. If either is not possible, the implementer MUST
  stop and report; the orchestrator returns to the human for a spec amendment. Switching the server to stateful mode or
  forking the SDK is NOT permitted without an amendment.

### R6. Sign-in page copy

- R6.1 The success page MUST read "Gmail is connected. You can close this tab and return to your assistant." (no
  client brand name). Other pages unchanged. Pages remain static, script-free, and reflect no request data.

### R7. Configuration and docs

- R7.1 `.env.example` and the README configuration table document `AUTH_URL_ELICITATION`.
- R7.2 README gains: the Testing-mode 7-day expiry explanation and how to move the consent screen to *In production*
  (including the "unverified app" warning that a personal-use client will show), the `auth_status` tool, the
  resources, and the elicitation behaviour with its fallback.
- R7.3 `CHANGELOG.md` `[Unreleased]` records the additions; `CLAUDE.md` (invariants: seven tools; resources; no new
  unauthenticated routes) and `docs/SPEC.md` are updated to match.

## 5. Design

Files touched: `errors.py` (R4 message builders), `auth_state.py` (new, R1), `resources.py` (new, R3: pure functions
returning text; registered in `server.py`), `server.py` (tool + resources + elicitation hook, R2/R3/R5),
`gmail_client.py` (rejection recording, R1.3), `config.py` (R5.1), `signin.py` (R6), docs and tests.

Data flow on a failing call: tool → `GmailClient` raises `AuthRequiredError` (recording a rejection when applicable) →
`_instrumented` wrapper → if elicitation conditions hold, raise/return the SDK-defined URL elicitation; otherwise
`ToolError(R4 text)`. The user opens `/auth` (unchanged flow, state + PKCE + single-use), `GmailClient.reset()` clears the
rejection, and the agent's next call succeeds.

## 6. Security and invariants

- **Tool set:** deliberately changed from six to seven by adding the read-only, mail-free `auth_status`. No tool can
  send, delete, trash, relabel, change settings or download attachments.
- **Loopback, bearer, Host/Origin guards:** unchanged. The only unauthenticated routes remain `GET/HEAD /healthz`,
  `/auth`, `/oauth2callback`. Resources and `auth_status` require the bearer token.
- **Secrets:** status/resources/errors never include refresh/access tokens, client secret, bearer token or token-file
  contents; only booleans, scope names, ages and timestamps. Logs unchanged (no bodies/subjects/addresses/credentials).
- **Elicitation safety (MCP spec):** URL contains no sensitive or per-user data and is not pre-authenticated;
  client-side consent and full-URL display are the client's duty; the Google credentials never transit the MCP client.
  Phishing/user-binding: the server is single-user and loopback-only; `/auth` mints a fresh single-use `state` bound to
  PKCE, so opening someone else's link cannot bind a token to a different identity.
- **Network confinement:** `auth_state.py` and `resources.py` perform no network I/O; Google network access stays in
  its current modules.
- **Untrusted content:** the guides and status contain only server-authored text; the untrusted-email warning in tool
  descriptions is unchanged.

## 7. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1 | Unit tests for each state and the precedence order using temp token files and a fake rejection record; age warning at 5.9 vs 6.0 days; assert no token/secret substring appears in the serialized output; `GmailClient` records on `RefreshError`/401 and `reset()` clears. |
| R2 | `tests/test_server.py` asserts the exact tool set (seven, deterministic order), annotations, `outputSchema`, and that `auth_status` succeeds signed-out, signed-in and rejected. |
| R3 | In-process MCP client lists/reads each URI; asserts mimeType/annotations, content equals `auth_status` for the status resource, required phrases in guides, unknown URI errors, and that resources require the bearer token (401 without). |
| R4 | Table test over causes: prefix `AUTH_REQUIRED:`, URL exactly once, no `scripts/auth`, length ≤ 700, built from `HOST_PORT`. |
| R5 | Tests with a client declaring `elicitation.url` → elicitation response with exact `/auth` URL and no query; without the capability / setting off → R4 text; no elicitation of undeclared modes; R5.6 gate tests recorded in the implementation report. |
| R6 | `signin` test asserts the new success copy and that pages stay static. |
| R7 | Docs checked by the reviewer; `.env.example` entry validated by `test_config`. |

Acceptance: `make check` passes with the coverage gate (≥ 85%, branch); `make audit` unaffected (no dependency
changes expected); reviewer APPROVED within 5 rounds. A live smoke check (rebuild container, call `auth_status` and one
tool while signed out) is recommended and reported, but requires the human's Google account for the final step.

## 8. Out of scope

Keep-alive/background token refresh; MCP prompts; a host-side watcher or Task Scheduler job; moving the server to
stateful mode; MCP-level OAuth for the client→server connection; changing Google Cloud configuration; any new mail
capability (send, delete, modify, attachments); unauthenticated status endpoints.

## 9. Open questions

None.
