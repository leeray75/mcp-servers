# Implementation report: network failure diagnostics (gmail-mcp-server)

- **Spec:** `docs/specs/network-failure-diagnostics/SPEC.md` (Approved, 2026-11-14, approved by: human)
- **Depends on:** Spec A `docs/specs/container-dns-resilience/` (`netcheck.resolve_hosts`, `PROBE_HOSTS`), reused, not duplicated
- **Scope:** implements R1 to R7 exactly. Not committed, pushed, tagged or built with docker.

## Requirement -> implementation -> test

| Req | Implementation | Test(s) |
|---|---|---|
| R1 | `src/gmail_mcp/netcheck.py`: `is_dns_failure(exc)` walks `__cause__`, `__context__` and exception `args` entries, at most 8 exceptions, cycle-safe; true for `socket.gaierror` or `httplib2.ServerNotFoundError`. `src/gmail_mcp/gmail_client.py` `_execute`: inside the existing `(GoogleAuthError, HttpLib2Error, OSError, TimeoutError)` branch, before the generic transport log, DNS failures log `"gmail dns failure"` with `outcome="dns_failure"`, `cause=<class name of the caught exception>` and raise `NetworkUnreachableError()` `from None`. RefreshError and HttpError branches are unchanged; non-DNS failures keep `"gmail transport failure"` and `GmailApiError`. | `tests/test_gmail_client.py`: `test_dns_failures_raise_network_unreachable` (ServerNotFoundError; TransportError built like google_auth_httplib2 via `__context__`; TransportError with gaierror `__cause__`; bare gaierror), log record `outcome`/`cause`, `__suppress_context__`; `test_transport_failures_are_concise` (TimeoutError, OSError(ECONNRESET), HttpLib2Error, TransportError stay `GmailApiError`); `test_dns_failure_logs_no_host_or_query`; `test_dns_failure_leaves_the_client_usable_and_no_rejection_recorded`. `tests/test_netcheck.py`: `is_dns_failure` true/false cases, cause/context/args, 8-link bound (DNS at link 8 is found, at link 9 is not), cyclic chain terminates, cycle still finds DNS. |
| R2 | `src/gmail_mcp/errors.py`: `NetworkUnreachableError(GmailMcpError)`, message `NETWORK_UNREACHABLE: DNS resolution failed in the server container, so Google could not be reached. This may be temporary. Do not retry in a loop. Call auth_status: if it reports network: dns_failure, ask the user to run scripts/restart.* (logs: scripts/logs.*).` Maps through the existing `_instrumented` wrapper as a `GmailMcpError` (`user_message()`). | `tests/test_errors.py`: prefix, length <= 500, DNS sentence, "Do not retry in a loop", `network: dns_failure`, `scripts/restart.*`, `scripts/logs.*`; no `://`, `googleapis`, `http`, `/auth`, `token`, `query`, `ya29`, `127.0.0.1`; no "permanent". `tests/test_server.py`: `test_network_unreachable_is_a_clear_tool_error` (tool error text carries the `NETWORK_UNREACHABLE:` message, no `AUTH_REQUIRED`). |
| R3 | `src/gmail_mcp/auth_state.py`: `AuthStatus.network: Literal["ok","dns_failure"]`; `compute_auth_status(settings, rejection, sign_in_pending=False, network="ok")` stays pure and synchronous; `ready_to_retry = signed_in and network == "ok"`; on `dns_failure`, `next_step` is `DNS_NEXT_STEP` (replaces the text when signed in, is prefixed to the token text otherwise) and `warnings` gains `DNS_FAILURE_WARNING` ("Google hostnames could not be resolved from the server container."). Model stays `frozen`. | `tests/test_auth_state.py`: default `network == "ok"` keeps the signed-in payload; `test_dns_failure_is_never_ready_to_retry` (signed-in and all four problem states); restart instruction and warning; token text kept with dns; state unchanged under dns; no host or secret in the dumped payload; closed literal rejects other values; exact-field set now includes `network`; frozen and no-network tests still pass. |
| R4 | `src/gmail_mcp/server.py`: `probe_network()` calls `resolve_hosts(PROBE_HOSTS, timeout=DNS_PROBE_TIMEOUT_S)` (2.0 s), returns `"ok"` or `"dns_failure"` (a timeout is a failed host, so `dns_failure`), and `"dns_failure"` if the call raises. `_auth_status_now(settings, gmail, store)` computes the payload from it; the `auth_status` tool runs it via `asyncio.to_thread`, the resource calls it directly. Not cached. | `tests/test_auth_status_and_resources.py`: `test_tool_reports_dns_failure_without_raising`, `test_tool_and_resource_agree_on_the_network_result`, `test_healthy_probe_reports_ok_and_keeps_the_payload`, `test_probe_uses_the_two_google_hosts_and_a_two_second_cap`, `test_probe_is_dns_failure_when_the_probe_raises`, `test_hanging_resolver_is_dns_failure_within_the_cap` (real `resolve_hosts`, hung `getaddrinfo`, returns in < 1.5 s), `test_resolvable_hosts_probe_as_ok`. |
| R5 | `src/gmail_mcp/resources.py` guide: retry only when `ready_to_retry` is true (`state` signed_in and `network` ok); `network` value explained; `dns_failure` says do not retry in a loop, ask the user to run `scripts/restart.sh`/`.ps1` with logs in `scripts/logs.*`. `server.py` `auth_status` tool description: retry only when `ready_to_retry` is true; `network` is `dns_failure` means ask the user to restart, not loop; still one sentence; still read-only and "never". Resource description mentions `network`. `errors.auth_required_message` unchanged. | `tests/test_auth_status_and_resources.py`: `test_auth_status_description_states_the_retry_and_restart_rules`, `test_auth_guide_explains_dns_failure`; existing guide, resource description (one sentence) and tool annotation tests pass unchanged. |
| R6 | `README.md`: tool table row, `auth_status` bullet (`network`, `ready_to_retry` rule), Network triage steps (`network: dns_failure`, `NETWORK_UNREACHABLE`, restart guidance). `docs/SPEC.md`: `auth_status` row (DNS-only probe, no request to Google, `network`, `ready_to_retry`), `NETWORK_UNREACHABLE` paragraph. `CLAUDE.md`: `netcheck.py` (stdlib plus httplib2, `is_dns_failure`), `auth_state.py` (pure function, server measures DNS and injects `network`, full field list, `ready_to_retry` rule), invariant bullets (auth_status only DNS I/O, `NETWORK_UNREACHABLE` text rules), spec list. `CHANGELOG.md` `[Unreleased]`: Added (NETWORK_UNREACHABLE, `network` field) and Changed (DNS probe amends "pure, no network", `ready_to_retry` rule). | `tests/test_docs_and_config.py`: `test_readme_documents_network_dns_failure`, `test_changelog_records_network_dns_failure`, `test_claude_md_no_longer_claims_auth_status_is_network_free`. |
| R7 | Tool set still exactly seven in order; resources still four; bearer and Host/Origin middleware untouched; no new route; no new dependency; `requirements*.txt` untouched; logs carry no bodies, host names, addresses or query text (the DNS log line has a fixed message and only `outcome`/`cause`). | Existing `test_tool_order_is_deterministic_and_has_seven_tools`, `test_resources_are_listed_with_their_metadata`, bearer and guard tests in `tests/test_server.py`, `tests/test_auth_status_and_resources.py` and `tests/test_mcp_gate.py` all pass unedited. |

## Files changed in this feature (on top of Spec A)

- `src/gmail_mcp/netcheck.py`: `import httplib2`; `_CHAIN_LIMIT`; `is_dns_failure`; module docstring.
- `src/gmail_mcp/errors.py`: `NETWORK_UNREACHABLE_MESSAGE`, `NetworkUnreachableError`.
- `src/gmail_mcp/gmail_client.py`: DNS branch in `_execute`; imports.
- `src/gmail_mcp/auth_state.py`: `DNS_FAILURE_WARNING`, `DNS_NEXT_STEP`, `NetworkState`, `AuthStatus.network`, `compute_auth_status(..., network="ok")`, `ready_to_retry` rule, next step and warning.
- `src/gmail_mcp/server.py`: `DNS_PROBE_TIMEOUT_S`, `probe_network`, `_auth_status_now`; tool and resource use it; `_DESC_AUTH_STATUS`, `_DESC_RES_AUTH_STATUS` updated.
- `src/gmail_mcp/resources.py`: `auth_guide` text.
- Tests: `tests/conftest.py` (autouse stub of `gmail_mcp.server.resolve_hosts`, so no test resolves a real host), `tests/test_gmail_client.py`, `tests/test_netcheck.py`, `tests/test_auth_state.py`, `tests/test_auth_status_and_resources.py`, `tests/test_errors.py`, `tests/test_server.py`, `tests/test_docs_and_config.py`.
- Docs: `README.md`, `docs/SPEC.md`, `CLAUDE.md`, `CHANGELOG.md`.

## Deviations and notes

No deviations from the spec's requirements. Points the reviewer should know:

1. **Existing test changed (required by R1).** `test_transport_failures_are_concise` previously listed `httplib2.ServerNotFoundError("dns")` as a case expecting `GmailApiError`. R1 makes that case a DNS failure, so the case was removed from that list and the class is now covered by `test_dns_failures_raise_network_unreachable`. The list gained `OSError(errno.ECONNRESET)` and a plain `HttpLib2Error`, so the non-DNS transport cases stay covered.
2. **`cause` log field.** `cause` is the class name of the exception `_execute` caught, so for `TransportError` wrapping an httplib2 error it logs `TransportError`, not `ServerNotFoundError`. The spec says `cause=<class name>` without naming the link; I read it as the caught exception. Tell me if the root cause class is wanted instead.
3. **`netcheck.py` now imports `httplib2`.** `is_dns_failure` must recognise `httplib2.ServerNotFoundError`, so the module is no longer stdlib-only. The docstring and `CLAUDE.md` say so. `healthcheck.py` imports `netcheck` and runs in the same image, which already has httplib2.
4. **Autouse test stub.** `tests/conftest.py` stubs `gmail_mcp.server.resolve_hosts` for every test, so the tool and resource never resolve real names. Tests that need the real probe restore `netcheck.resolve_hosts` explicitly.
5. **Probe in a thread.** The resource handler is synchronous and runs the probe directly, as the spec allows (the resource handler is sync). The tool runs it through `asyncio.to_thread`. Nothing is cached.
6. **Log noise, not a failure.** The gate log contains a `ValueError: I/O operation on closed file` traceback from the mcp SDK's `logger.info` call, printed during `test_network_unreachable_is_a_clear_tool_error`. The test itself passes and the gate is green. I did not confirm the cause, and I did not check whether the existing tool-error tests print the same traceback.

## Commands run and results

Run from WSL (`MSYS_NO_PATHCONV=1 wsl.exe -e bash -lc "cd '/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/gmail-mcp-server' && make check VENV=~/.venvs/gmail-mcp"`):

| Step | Result |
|---|---|
| `ruff check .` | All checks passed (after fixing one E501 in `resources.py`, one docstring E501 and an I001 import order in `server.py`, and two SIM117 nested `with` in `tests/test_gmail_client.py`) |
| `ruff format --check .` | 86 files already formatted (after `ruff format src/gmail_mcp/server.py` reflowed one call) |
| `mypy --strict` | Success: no issues found in 41 source files |
| `pytest` with coverage gate | 468 passed |
| Coverage | Total 99.23% (gate 85%); `netcheck.py` 97% (lines 74-75 are pre-existing Spec A lines), `server.py` 98% (lines 312, 322, 339 are pre-existing) |

Final gate result: **`make check` exit 0**.

Not run: the live check in spec section 7 (blocking DNS inside the container and timing `auth_status`), because it needs docker and the instructions forbid running it. Still open for the reviewer or the human.

## Not done (out of scope per spec)

No commit, push, tag, docker build or restart. No `.env` values were read or printed. `requirements*.txt` untouched.
