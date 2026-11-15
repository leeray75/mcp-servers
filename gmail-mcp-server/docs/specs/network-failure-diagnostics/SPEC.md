# Network failure diagnostics (gmail-mcp-server) — Specification

- **Status:** Approved — 2026-11-14, approved by: human
- **Date:** 2026-11-14
- **Amends:** `docs/specs/agent-auth/SPEC.md` and `docs/specs/agent-auth-hardening/SPEC.md` (`auth_status` payload,
  `ready_to_retry`), `docs/SPEC.md`, and the server `CLAUDE.md` invariants "`auth_state.py` … no network" and "the
  tool set / payload". **Depends on** `docs/specs/container-dns-resilience/SPEC.md` (Spec A): reuses its
  `netcheck.resolve_hosts`. Implement Spec A first.
- **Source:** `docs/reports/container-dns-outage/bug-report.md` (defects C3, C4), corrected by the review below.

## 1. Problem / motivation

During the outage every tool returned "Could not reach the Gmail API; try again shortly." and `auth_status` returned
`signed_in: true, ready_to_retry: true, next_step: "No action needed."`. An agent that follows the documented contract
retried forever against a failure that does not clear by itself.

Corrections to the bug report that shape this spec:

- DNS failure reaches the handler in `gmail_client._execute` in **two shapes**: `httplib2.ServerNotFoundError` (an
  `HttpLib2Error`, raised on a direct API call, httplib2 `__init__.py:1404`) and
  `google.auth.exceptions.TransportError` (a `GoogleAuthError` wrapping it, raised on token refresh). A bare
  `socket.gaierror` is not what arrives. Detection must inspect the exception **cause chain**, not the top-level type.
- `auth_status` is deliberately pure today (`auth_state.py` docstring and `CLAUDE.md`). Adding a probe is an explicit
  amendment of that invariant, decided by the human (D2).
- `getaddrinfo` cannot be cancelled; "bounded" means the caller stops waiting, not that the thread stops.

## 2. Goals and non-goals

**Goals:** (a) tool errors that name a DNS failure instead of "try again shortly"; (b) `auth_status` and
`gmail://auth/status` that report resolver health and stop telling agents to retry when it is broken.

**Non-goals:** new tools or resources (still seven and four); TCP/HTTPS egress probing; auto-restart; any change to
auth states, sign-in, elicitation or the AUTH_REQUIRED text; logging query content.

## 3. Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Split | Own spec (this one) because it amends an invariant. |
| D2 | `auth_status` network I/O | **Allowed**: a bounded DNS probe (decision of the human, 2026-11-14). Nothing else leaves the process. |
| D3 | Field | New `network: Literal["ok","dns_failure"]` on `AuthStatus`. A probe timeout counts as `dns_failure`. |
| D4 | `ready_to_retry` | True only when `state == "signed_in"` **and** `network == "ok"` (retrying into a dead resolver is pointless). `signed_in` keeps meaning token state only. |
| D5 | Probe | `netcheck.resolve_hosts(PROBE_HOSTS, timeout=2.0)` from Spec A; run off the event loop (the tool already uses `asyncio.to_thread`). Not cached. |
| D6 | Error type | New `NetworkUnreachableError(GmailMcpError)`; `GmailApiError` keeps real transient failures. |
| D7 | Wording | The DNS error does not claim the condition is permanent (a resolver error can be momentary). It says: do not retry in a loop, call `auth_status`, and if it reports `network: dns_failure` ask the user to run `scripts/restart.*`; logs via `scripts/logs.*`. |

## 4. Requirements

- **R1** `gmail_client._execute`: before the generic transport branch, classify with `is_dns_failure(exc)` (in
  `netcheck.py`): true if `exc` or anything on its `__cause__`/`__context__` chain (bounded to 8 links, cycle-safe) is
  `socket.gaierror` or `httplib2.ServerNotFoundError`. On true, log `"gmail dns failure"` with
  `extra={"outcome": "dns_failure", "cause": <class name>}` and raise `NetworkUnreachableError` with `from None`.
  Otherwise behaviour is unchanged (`"gmail transport failure"`, `GmailApiError`).
- **R2** `NetworkUnreachableError` message begins `NETWORK_UNREACHABLE:` and follows D7. It is at most 500 characters,
  contains no URL, host name beyond `Google`, query text, token or path, and maps through the existing tool error
  handling like other `GmailMcpError`s.
- **R3** `AuthStatus` gains `network` (D3). `compute_auth_status` takes the probe result as a parameter and stays pure
  and synchronous; the server passes the result in. `ready_to_retry` follows D4. When `network == "dns_failure"`,
  `next_step` is prefixed or replaced by the D7 instruction (token problems still add their own text), and a warning
  states that Google hostnames could not be resolved. The model stays `frozen`.
- **R4** `auth_status` (tool) and `gmail://auth/status` (resource) both run the probe (once per call or read, 2.0 s
  cap). A hung resolver delays the response by at most about 2 s and never raises. The `auth_status` tool never raises
  an auth or network error.
- **R5** `resources.py` (`gmail://guide/auth`) and the tool description are updated: retry only when
  `ready_to_retry` is true; `network: dns_failure` means ask the user to restart, do not loop. `errors.py`
  `auth_required_message` is unchanged.
- **R6** Docs: `README.md`, `docs/SPEC.md`, server `CLAUDE.md` (the "pure / no network" sentences, the payload field
  list, network confinement) and `CHANGELOG.md` `[Unreleased]` match the behaviour. The README triage section from Spec A
  gains `network: dns_failure`.
- **R7** Invariants preserved: seven tools in order, four resources, bearer and Host/Origin guards, loopback binding,
  logs free of bodies, subjects, addresses, credentials and query content, no new unauthenticated route.

## 5. Design

Files: `src/gmail_mcp/netcheck.py` (adds `is_dns_failure`), `errors.py` (`NetworkUnreachableError`),
`gmail_client.py`, `auth_state.py` (`network` field, `ready_to_retry`, `next_step`), `server.py` (pass probe result into
`compute_auth_status` for the tool and the resource), `resources.py`, docs, tests.

Flow: tool call fails on DNS → `_execute` classifies via the cause chain → `NetworkUnreachableError` → the agent calls
`auth_status` → probe → `network: dns_failure`, `ready_to_retry: false`, `next_step` says restart.

## 6. Security and invariants

The only new I/O is DNS resolution of the two hosts already contacted. `auth_status` still returns no secrets and no
addresses (the probe returns booleans only). The change amends one stated invariant (pure `auth_state`); purity of
`compute_auth_status` itself is kept by injecting the probe result. Error text is a fixed string. `from None` stays so
no Google-internal detail leaks.

## 7. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1 | `tests/test_gmail_client.py`: fake service raising `ServerNotFoundError` → `NetworkUnreachableError`; `TransportError` wrapping a `gaierror` (via `__cause__`) → same; bare `gaierror` → same; plain `HttpLib2Error`, `TimeoutError`, `OSError(ECONNRESET)` → unchanged `GmailApiError`; cyclic chain terminates; log `outcome == "dns_failure"` |
| R2 | length, prefix, forbidden-content assertions; `user_message()` through the server tool wrapper |
| R3, R4 | `tests/test_auth_status_and_resources.py` and `tests/test_auth_state.py`: ok → unchanged payload plus `network: "ok"`; failure → `network: "dns_failure"`, `ready_to_retry` false, `signed_in` still true, restart instruction; timeout path; resource and tool agree; model frozen |
| R5 | tests on guide resource text and tool description (retry rule, dns_failure sentence); existing description-length tests still pass |
| R6 | reviewer reads docs against code |
| R7 | existing tool-set, resource-set, guard and logging tests unchanged and passing |
| Existing | `test_gmail_client.py`, `test_gmail_rejection.py`, `test_rejection_recovery.py`, `test_mcp_gate.py` pass without edits except where `network` or `ready_to_retry` are asserted |
| Gate | `make check VENV=~/.venvs/gmail-mcp` |
| Live (manual) | With the resolver deliberately blocked in the container, a tool call returns `NETWORK_UNREACHABLE`, and `auth_status` returns `network: "dns_failure"` within about 3 s |

## 8. Out of scope

TCP or HTTPS reachability probing; caching probe results; a `doctor` script; Spec A items; commit, push, tag.

## 9. Open questions

None.
