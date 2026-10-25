# Summary report: agent-auth

- **Date:** 2026-10-25
- **Spec:** `SPEC.md` (Approved, with human-approved Amendment A1)
- **Result:** Review round 3 **APPROVED** (0 blockers, 0 majors, 2 informational minors). Review rounds used: **3 of 5**.
- **Not committed, pushed or tagged.** The changes are in the working tree on `release/v0.2.0`.

## 1. Process

| Step | Actor | Output | Result |
|---|---|---|---|
| Plan, research (MCP spec: elicitation, tools, resources) | Orchestrator + human | decisions D1-D4 | 4 choice questions answered |
| Spec | Orchestrator | `SPEC.md` | Approved 2026-10-25 |
| Implementation | Implementer (Haiku) | code, tests, docs, `implementation-report.md` | `make check` green; R5.6 SDK gate passed; 6 deviations reported |
| Review round 1 | Reviewer (Sonnet) | `review-round-1.md` | CHANGES REQUESTED: 0 blockers, 1 major (M1), 3 minors |
| Fix round 1 | Implementer (Haiku) | `fix-round-1.md` | M1 and m1 fixed; m2, m3 accepted with justification |
| Review round 2 | Reviewer (Sonnet) | `review-round-2.md` | APPROVED, 2 informational minors |
| Human decisions on deviations | Human | Amendment A1 in `SPEC.md` | D5-D8 below |
| Fix round 2 (Amendment A1) | Implementer (Haiku) | `fix-round-2.md` | A1 applied; 372 tests, 99.29% coverage |
| Review round 3 | Reviewer (Sonnet) | `review-round-3.md` | **APPROVED**, 2 informational minors |
| Docs restructure to the sports layout | Orchestrator | `docs/specs/<feature>/` | Done; links updated |
| Summary | Orchestrator | this file | |

## 2. What was built (by requirement)

| Req | Delivered |
|---|---|
| R1 | `auth_state.py`: pure `AuthStatus` model and `compute_auth_status` (states `signed_in`, `not_signed_in`, `token_unreadable`, `scopes_insufficient`, `rejected`; token age; expiry and rejection warnings; next step; OS open commands). No network, no secrets. `GmailClient` records a rejection and clears it on a successful call, on `reset()`, or when the token file is newer than the rejection (M1). |
| R2 | New read-only `auth_status` tool (seventh tool, after the original six). Structured result with an output schema; the text block is the full JSON of the same payload (D8). |
| R3 | Four static resources: `gmail://auth/status`, `gmail://guide/auth`, `gmail://guide/search`, `gmail://guide/limits`, behind the existing bearer and Host guards. |
| R4 | `AUTH_REQUIRED:` error contract: reason, one bare sign-in URL, ready-to-run open commands for Windows, macOS and Linux, no `scripts/auth.*` mention, 700 characters or fewer. |
| R5 | `AUTH_URL_ELICITATION` kill switch (default on). Requests whose client capabilities declare `elicitation.url` (protocol 2026-07-28) get an `InputRequiredResult` asking the client to open `http://127.0.0.1:<HOST_PORT>/auth`. Every other client gets the R4 tool error. The server stays stateless. |
| R6 | Client-neutral sign-in success page ("return to your assistant"). |
| R7 | README (expiry, publishing the consent screen, tool, resources, elicitation), CHANGELOG `[Unreleased]`, `.env.example`, `CLAUDE.md`, `docs/SPEC.md`. |

## 3. Decisions taken after implementation (Amendment A1)

| Item | Decision |
|---|---|
| D5 (was D1) | `lastModified` on `gmail://auth/status` dropped (the SDK fixes resource annotations at registration). |
| D6 (was D2) | One bare URL token in the error; quoted copies inside the OS commands allowed. |
| D7 (was D4) | The 2025-11-25 `-32042` elicitation path removed. Stateless mode keeps no handshake capabilities, so only 2026-07-28 clients get the prompt; others get the R4 error. |
| D8 (was D6) | `auth_status` text content is the full JSON payload. |

Process lesson: D1, D2 and D4 stemmed from SDK limits and a self-contradicting spec line that should have been settled before
implementation. Future specs should include a capabilities check against the pinned SDK before approval.

## 4. Verification

- `make check` in WSL (ruff, ruff format, `mypy --strict`, pytest, coverage gate): **372 passed, 99.29% coverage** (gate 85%,
  branch coverage on). Run independently by the reviewer in rounds 1-3 with the same result.
- Live smoke of `python -m gmail_mcp` with fake credentials (implementation round): tool list, `auth_status`, elicitation over
  HTTP, form-only fallback, resources, unknown URI (-32602) and missing bearer (401) behaved as specified.
- No dependency changes, so `make audit` was not needed. `.env` was never read.

## 5. Open items for the human

1. **Not run:** container rebuild and a real Google sign-in. Do this once: `scripts/rebuild`, call `auth_status` and one tool while
   signed out, complete `/auth`, confirm the page says "return to your assistant".
2. **README Google console labels** ("Audience", "Publish app") were written from general knowledge and not checked against the
   live console. Confirm them on first use.
3. **The root cause is not fixed by code:** publish the OAuth consent screen to Production, or the refresh token will expire again
   after 7 days. `auth_status` warns from day 6.
4. **Goose support for URL elicitation is unknown.** If Goose does not declare `elicitation.url`, the clear `AUTH_REQUIRED` error
   and `auth_status` are the working path.
5. Accepted minors: `auth_status` builds its own result with a `cast` (recheck if the SDK pin changes); token age uses the token
   file's modified time; the SDK prefixes tool errors with `Error executing tool <name>:` (D3).
6. The working tree is uncommitted; the repo-level `AGENTS.md` and root `CLAUDE.md` were also edited (spec-location convention).

## 6. Files created or updated

### Source (`gmail-mcp-server/src/gmail_mcp/`)

| File | Change | Why |
|---|---|---|
| `auth_state.py` | created | R1: auth state model and `compute_auth_status` |
| `resources.py` | created | R3: guide text for the three markdown resources |
| `server.py` | updated | `auth_status` tool, resource registration, URL-elicitation response, `ctx` on tools (R2, R3, R5) |
| `errors.py` | updated | R4: `AUTH_REQUIRED` message builder; old hint removed |
| `gmail_client.py` | updated | Rejection record, cleared on success and atomically on reset (R1.3, M1, m1) |
| `config.py` | updated | R5.1: `auth_url_elicitation` setting |
| `signin.py` | updated | R6: client-neutral success page copy |

### Tests (`gmail-mcp-server/tests/`)

| File | Change | Why |
|---|---|---|
| `test_auth_state.py` | created | R1 states, precedence, warnings, no secrets, no network |
| `test_gmail_rejection.py` | created | R1.3 rejection recording and reset |
| `test_rejection_recovery.py` | created | M1/m1: out-of-process sign-in, success clears, reset race |
| `test_errors.py` | created | R4 contract table |
| `test_elicitation.py` | created | R5 elicitation and fallbacks |
| `test_mcp_gate.py` | created | R5.6 SDK gate findings pinned as tests |
| `test_auth_status_and_resources.py` | created | R2 and R3 |
| `test_docs_and_config.py` | created | R5.1, R6, R7 presence checks |
| `conftest.py`, `test_server.py`, `test_auth.py`, `test_gmail_client.py`, `test_signin.py` | updated | Seven-tool set, new error contract, new success copy, `rejected_at` surface |

### Configuration and documentation

| File | Change | Why |
|---|---|---|
| `gmail-mcp-server/.env.example` | updated | Documents `AUTH_URL_ELICITATION` |
| `gmail-mcp-server/README.md` | updated | Expiry cause, publish steps, `auth_status`, resources, elicitation and fallback |
| `gmail-mcp-server/CHANGELOG.md` | updated | `[Unreleased]` entries |
| `gmail-mcp-server/CLAUDE.md` | updated | Seven-tool invariant, resources, elicitation rules, new docs layout |
| `gmail-mcp-server/docs/SPEC.md` | updated | Base spec reflects the new tool, config row and error contract |
| `CLAUDE.md` (repo root) | updated | Docs workflow now describes `docs/specs/<feature>/` |
| `AGENTS.md` (repo root) | updated | Spec-location row now `docs/specs/<feature>/SPEC.md` |

### SDD artifacts (`gmail-mcp-server/docs/specs/agent-auth/`, all created)

`SPEC.md` (with Amendment A1), `tasks.md`, `workflow.md`, `implementation-report.md`, `review-round-1.md`, `fix-round-1.md`,
`review-round-2.md`, `fix-round-2.md`, `review-round-3.md`, `summary-report.md`.

### Moved to match the sports layout (content unchanged except link updates)

- `docs/reauth/{PLAN,SPEC}.md` -> `docs/specs/reauth/`
- `docs/reports/reauth/*` (implementation, review rounds 1-4, fix rounds 1-3, summary) -> `docs/specs/reauth/`
- `docs/specs/reauth/PLAN.md` and `summary-report.md`: path references updated
