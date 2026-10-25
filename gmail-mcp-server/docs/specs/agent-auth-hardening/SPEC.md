# Agent Auth Hardening: guardrails, `auth_status` freshness and in-progress state — Specification

- **Status:** Approved — 2026-10-25, approved by: human
- **Date:** 2026-10-25
- **Amends:** `docs/specs/agent-auth/SPEC.md` (R2, R3, R4) and `docs/SPEC.md`. Where they conflict, this document wins and
  `docs/SPEC.md` MUST be updated to match. Normative words (MUST, SHOULD, MAY) follow RFC 2119.

## 1. Problem / motivation

A Goose + Qwen incident report (2026-10-25) on the `agent-auth` release showed the recovery works (the agent ran the
server's open command and `auth_status` confirmed the new token) but exposed four weaknesses:

1. **The agent opened the sign-in page with a browser-automation tool** (an isolated Chrome with no user session) and
   snapshotted a Google credential page. The error text says what to do, never what not to do.
2. **`auth_status` results carry no "checked at" time**, so a frozen `rejected_at` could not be told apart from a stale
   cached result.
3. **"Call `auth_status` and retry" is ambiguous.** It can be read as "retry now". Agents need one field that says when a
   retry is safe, and a way to tell "sign-in page opened but not finished" from "not signed in".
4. **The agent reported `list_resources` returned nothing.** A live check on 2026-10-25 against the running container
   (`resources/list` with the bearer token) returned all four resources, so the server is correct. The only defect found:
   every resource has an empty `description`.

## 2. Goals and non-goals

**Goals**
- Tell agents explicitly how to hand the sign-in to the user safely: the harness opens the tab, or the user is given the
  URL to copy and paste; never browser automation, never typing credentials.
- Make `auth_status` self-describing for freshness and retry safety, and report an in-progress sign-in.
- Give each resource a useful description.

**Non-goals** — see §8.

## 3. Decisions (from Phase 1)

| # | Question | Decision |
|---|---|---|
| D1 | Should the server open the tab itself? | **No.** The agent harness opens the tab (shell command from `open_commands`) or gives the user the URL to copy and paste into their browser. No host-side helper. |
| D2 | Guardrail wording | **Yes**, in the `AUTH_REQUIRED` error, `next_step`, and `gmail://guide/auth`. |
| D3 | `auth_status` additions | **`checked_at`, `ready_to_retry`, and a `sign_in_in_progress` state.** |
| D4 | Empty `list_resources` | **Investigated live first.** The server returns four resources; no server bug. Add descriptions only. |

## 4. Requirements

**R1 — Error guardrails.** The `AUTH_REQUIRED:` message (`errors.auth_required_message`) MUST, within the existing 700
character limit and the one-bare-URL rule (agent-auth D6), tell the agent: (a) run the open command for the user's OS in
a shell, **or** give the user the URL to copy and paste into their own browser; (b) do not use browser-automation tools
(DevTools, Playwright, Selenium or similar) on the sign-in page; (c) never type or capture credentials; (d) after the
user finishes, call `auth_status` and retry only when it reports `ready_to_retry: true`. It MUST no longer say only
"call auth_status and retry".

**R2 — Auth guide.** `gmail://guide/auth` MUST contain the same guardrails and a "who opens the page" rule (shell open
command, else copy-and-paste URL), and MUST describe `ready_to_retry`, `checked_at` and `sign_in_in_progress`.

**R3 — `auth_status` fields.** The `AuthStatus` model, the `auth_status` tool and the `gmail://auth/status` resource MUST
add:
- R3.1 `checked_at`: UTC ISO-8601 time the status was computed (every call and every resource read recomputes it).
- R3.2 `ready_to_retry`: `true` iff `state == "signed_in"`, otherwise `false`. `signed_in` is kept.
- R3.3 `state` gains `"sign_in_in_progress"`, returned when an unexpired pending sign-in attempt exists (the `/auth`
  page was loaded and its callback has not completed) and the otherwise-computed state is not `signed_in`. The
  effective-rejection and token-file rules from agent-auth are unchanged; a pending attempt only replaces
  `rejected`, `token_unreadable`, `scopes_insufficient` and `not_signed_in`, and never changes `signed_in`.
- R3.4 For `sign_in_in_progress`, `next_step` says a sign-in is open, the user must finish approving in the browser, then
  call `auth_status`; `ready_to_retry` is `false`. All other `next_step` texts carry the R1 guardrail wording.
- R3.5 The status exposes only the resulting state for pending attempts: no state value, verifier, count or timing.
- R3.6 The tool's output schema, the structured result and the JSON text (agent-auth D8) all include the new fields.

**R4 — Resource descriptions.** All four resources MUST have a non-empty, one-sentence `description` that appears in
`resources/list`.

**R5 — Pending-attempt visibility.** `PendingStore` (`signin.py`) MUST expose a read-only check for any unexpired
attempt (purging expired ones), and the server MUST pass that boolean into `compute_auth_status`. The store stays in
memory and is never persisted.

**R6 — Docs.** README (auth section, `auth_status` fields, guardrails), CHANGELOG `[Unreleased]`, `docs/SPEC.md` and the
gmail `CLAUDE.md` MUST reflect R1–R5. The tool set stays exactly seven tools.

## 5. Design

- `auth_state.py`: add `checked_at`, `ready_to_retry`, the `sign_in_in_progress` literal and a `sign_in_pending: bool`
  parameter to `compute_auth_status` (default `False`, so existing callers and tests stay valid). Still pure: no
  network, no secrets.
- `signin.py`: `PendingStore.has_pending()` (expiry purge under the existing lock).
- `server.py`: pass `sign_in_pending` from the store for the tool and the resource; register resource descriptions.
- `errors.py`: new `AUTH_REQUIRED` wording, same builder signature.
- `resources.py`: guardrail text.

## 6. Security and invariants

- Seven-tool invariant, read-only Gmail scopes, bearer and Host/Origin guard, loopback binding and secret handling are
  unchanged. `/auth` and `/oauth2callback` remain the only unauthenticated routes.
- `has_pending()` leaks only a state change over the already-authenticated MCP channel. Any visitor to `/auth` (including
  a browser prefetch) makes the status read `sign_in_in_progress` until the attempt expires; this is accepted and
  documented, and it never reveals tokens or state values.
- The guardrails reduce the chance an agent drives a credential page; they are advisory text, not enforcement.

## 7. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1 | Error contract test: length ≤ 700, one bare URL, mentions shell and copy-and-paste, bans browser automation and credential entry, requires `ready_to_retry`. |
| R2 | Resource content test for the guardrails and the field descriptions. |
| R3 | `test_auth_state.py`: `checked_at` present and recent; `ready_to_retry` per state; `sign_in_in_progress` precedence table (pending with each base state); no secrets in the model dump. Tool and resource tests for the new fields and output schema. |
| R4 | `resources/list` test asserts every description is non-empty. |
| R5 | `PendingStore.has_pending` tests (empty, fresh, expired, consumed) and a server-level test: `/auth` then `auth_status`. |
| R6 | `test_docs_and_config.py` presence checks. |

`make check` MUST pass (ruff, ruff format, mypy --strict, pytest, branch coverage ≥ 85%). The container is rebuilt and
`auth_status` checked live before the summary.

## 8. Out of scope

Host-side opener or watcher; server-initiated browser launch; form-mode elicitation; Goose-specific behaviour; token
renewal (impossible in Testing mode; publishing the consent screen is the fix); a startup banner for an unpublished
consent screen (not detectable); the `serverInfo.version` value (`0.1.0` in `pyproject.toml` vs the v0.2.0 release); the
2026-07-28 URL-elicitation path.

## 9. Open questions

None.
