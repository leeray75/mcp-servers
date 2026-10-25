# Implementation report: agent-auth (SPEC.md, R1-R7)

- **Status:** DONE. The pending deviations D1, D2, D4 and D6 are resolved by **Amendment A1** (see section 4).
  Fix round 1 (M1, m1) and fix round 2 (amendment A1) are recorded in `fix-round-1.md` and `fix-round-2.md`.
- **Date:** 2026-10-25
- **Spec:** `docs/specs/agent-auth/SPEC.md` (Approved 2026-10-25; Amendment A1 approved by the human 2026-10-25)
- **Scope kept:** only R1-R7 and A1. Nothing committed, pushed or tagged. No dependency change
  (`requirements*.txt` untouched, so `make audit` was not required). `.env` was never read.

## 1. R5.6 implementation gate (pinned `mcp==2.2.0`)

Verified in a WSL venv at `~/.venvs/gmail-mcp` (Python 3.12.14, outside the Windows drive) with a
stateless, JSON-mode `MCPServer` (`streamable_http_app(stateless_http=True, json_response=True)`).
The same facts are pinned by `tests/test_mcp_gate.py`.

| Gate item | Result | Evidence |
|---|---|---|
| (a) per-request client capabilities readable in a tool handler, stateless | **PASS** for the 2026-07-28 envelope. `ctx.client_capabilities` returns the `_meta["io.modelcontextprotocol/clientCapabilities"]` of each request. A 2026 request without that key is rejected by the SDK (-32602) before any tool runs. | `test_gate_a_per_request_capabilities_are_readable_when_stateless` |
| (a') capabilities on the 2025-11-25 handshake path, stateless | **NOT retained.** `ctx.client_capabilities` is `None` on a stateless `initialize` + `tools/call` chain. | `test_stateless_legacy_handshake_capabilities_are_not_retained` (pins the finding) |
| (b) (A1) 2026-07-28 `InputRequiredResult` with `elicitation/create`, `mode: "url"`, produced from a tool handler | **PASS** (`resultType: "input_required"`, `inputRequests`). | `test_gate_b_input_required_result_is_produced_from_a_tool_handler` |
| 2025-11-25 client with capabilities unknown (consequence of a'; D7 / A1) | **Fallback pinned.** The client gets a tool error with no JSON-RPC error and no elicitation (R5.4). | `test_2025_client_gets_the_r4_fallback_and_never_an_elicitation` |

Gate decision: **(a) and (b) hold as written** (per-request capabilities and the 2026-07-28 response shape
are both producible in stateless JSON mode). Stateful mode was not used and the SDK was not forked.
Because of (a'), elicitation is limited to the 2026-07-28 revision (D7, A1). The 2025-11-25 `-32042`
mechanism was removed from the code in fix round 2 and is not implemented.

Notes that shaped the implementation:
- `_sign_in_response` branches on `ctx.protocol_version`: only `2026-07-28` returns an `InputRequiredResult`.
  Every other revision returns `None`, which the wrapper turns into the R4 tool error.
- A tool that returns `InputRequiredResult` must not be used on a non-2026 path (the SDK answers
  "Handler returned an invalid result"), so the gate on the revision is required, not optional.

## 2. Requirement -> implementation -> test traceability

| Req | Implementation (file:symbol) | Test(s) |
|---|---|---|
| R1.1 model fields | `auth_state.py:AuthStatus`, `compute_auth_status` | `test_auth_state.py::test_status_is_frozen`, `test_missing_token_is_not_signed_in`, `test_signed_in_with_all_scopes` |
| R1.2 metadata only, no network, no secrets | `auth_state.py:_base_state` (uses `auth.read_token`), `_token_age_days` (stat) | `test_output_never_contains_credentials`, `test_status_makes_no_network_calls` (patches `socket.connect`) |
| R1.3 rejection record, thread-safe, cleared by reset and by success, precedence | `gmail_client.py:GmailClient.rejected_at`, `_record_rejection`, `_clear_rejection`, `reset`; `auth_state.py:_effective_rejection` (fix round 1 / M1); precedence in `compute_auth_status` | `test_gmail_rejection.py` (all), `test_rejection_recovery.py` (M1 and m1), `test_rejected_takes_precedence`, `test_unreadable_takes_precedence...` |
| R1.4 age warning at >= 6 days; rejection causes | `auth_state.py:EXPIRY_WARNING`, `REJECTED_CAUSES_WARNING` | `test_expiry_warning_below_six_days_is_absent` (5.9), `test_expiry_warning_at_six_days` (6.0), `test_rejection_is_reported_with_its_time` |
| R1.5 next_step | `auth_state.py:compute_auth_status` (`"No action needed."` / one imperative sentence) | `test_next_step_for_each_problem_state_is_one_sentence_with_the_link` |
| R1.6 open_commands from sign_in_url | `auth_state.py:compute_auth_status` | `test_open_commands_are_built_from_the_sign_in_url`, `test_sign_in_url_follows_host_port` |
| R2.1 tool `auth_status`, title, read-only description | `server.py:create_server` (`auth_status`), `_DESC_AUTH_STATUS` | `test_auth_status_annotations_title_and_output_schema` |
| R2.2 annotations | `server.py:AUTH_STATUS` | `test_auth_status_annotations_title_and_output_schema` |
| R2.3 (A1, D8) structured content + outputSchema + text block holding the full JSON payload | `server.py:auth_status` (returns `CallToolResult` with `content=[TextContent(status.model_dump_json())]` and `structured_content=status.model_dump(mode="json")`; declared output `AuthStatus`) | `test_auth_status_signed_out_is_a_normal_result` (text parses to the structured payload), `test_auth_status_annotations_title_and_output_schema` (schema) |
| R2.4 never raises AuthRequiredError; instrumented | `server.py:_instrumented` applied to `auth_status`; the function never calls Gmail | `test_auth_status_signed_out_is_a_normal_result`, `test_auth_status_reports_rejection_recorded_by_the_client` |
| R2.5 deterministic order, seven tools | `server.py:create_server` registration order | `test_tool_order_is_deterministic_and_has_seven_tools`, `test_server.py::test_registered_tool_set_is_exactly_the_spec` |
| R3.1 capability + exactly four static URIs, names, titles, mime, audience, priority | `server.py:_register_resources` | `test_resources_are_listed_with_their_metadata` |
| R3.2 (A1, D5) status content equals the auth_status payload; no `lastModified` annotation | `server.py:_register_resources.auth_status_resource` (same `compute_auth_status`) | `test_status_resource_equals_the_auth_status_payload` |
| R3.3 auth guide: ordered procedure, OS commands, no self-consent, 7-day explanation | `resources.py:auth_guide` | `test_auth_guide_contains_the_ordered_procedure` |
| R3.4 search operators | `resources.py:search_guide` | `test_search_guide_documents_the_operators` |
| R3.5 limits, read+draft only, untrusted, caps, 50 msgs, human sends | `resources.py:limits_guide`, `tools.THREAD_MESSAGE_LIMIT` | `test_limits_guide_states_the_configured_limits` |
| R3.6 no credentials in resources; unknown URI -> invalid params | `resources.py` (static text); SDK invalid-params path | `test_resource_content_holds_no_credentials`, `test_unknown_resource_uri_is_an_invalid_params_error` |
| R3.7 behind bearer + Host/Origin, no new unauthenticated route | `server.py:create_app` unchanged routing; resources served on `/mcp` | `test_resources_require_the_bearer_token`, `test_resources_are_served_through_the_guarded_mcp_route` |
| R4.1 `AUTH_REQUIRED:` shape, <= 700 chars | `errors.py:auth_required_message`, `AuthRequiredError.user_message` | `test_errors.py::test_auth_required_text_follows_the_contract` (table over causes x ports) |
| R4.2 (A1, D6) no `scripts/auth.*`; one bare URL token followed by a space; quoted copies allowed in the OS commands; SDK prefix accepted | `errors.py:auth_required_message` | `test_errors.py`, `test_server.py::test_auth_error_is_actionable`, `test_signin.py::test_auth_error_message_contains_the_clickable_link` |
| R4.3 reason sentence per cause, 7-day note on rejection | `errors.py:REJECTED_REASON`; `auth.py` reasons; `gmail_client.py` | `test_errors.py::test_rejected_reason_names_the_seven_day_testing_expiry`, `test_auth_required_text_follows_the_contract` (causes) |
| R4.4 URL built from HOST_PORT | `errors.py:auth_url` | `test_errors.py::test_url_is_built_from_host_port_not_a_constant`, `test_signin.py` (ports 8765/9001) |
| R5.1 `AUTH_URL_ELICITATION`, default true | `config.py:Settings.auth_url_elicitation`; `.env.example` | `test_docs_and_config.py::test_url_elicitation_is_on_by_default`, `::test_url_elicitation_can_be_switched_off` |
| R5.2 (A1, D7) elicitation only for a url-capable 2026-07-28 request, as `InputRequiredResult` | `server.py:_sign_in_response` (returns `InputRequiredResult` only when `protocol_version == "2026-07-28"`), wired in `_instrumented` | `test_elicitation.py::test_url_capable_2026_client_is_asked_to_open_the_plain_sign_in_url`, `::test_modern_revision_with_url_capability_returns_input_required`, `::test_2025_revision_with_url_capability_gets_no_elicitation`, `::test_instrumented_wrapper_maps_the_decision_to_the_right_response` |
| R5.3 exact `/auth` URL, no query/state | `server.py:_sign_in_response` (`auth_url(host_port)`) | `test_url_capable_2026_client_...` (asserts exact URL, no `?`) |
| R5.4 fallback when undeclared/unknown/off/non-2026; never undeclared modes | `server.py:_sign_in_response` returns `None` -> R4 `ToolError` | `test_form_only_client_gets_the_plain_tool_error_not_an_elicitation`, `test_kill_switch_disables_elicitation`, `test_legacy_2025_handshake_client_gets_the_fallback_when_capabilities_are_unknown`, `test_undeclared_or_form_only_capabilities_mean_no_elicitation`, `test_other_or_unknown_revisions_fall_back_to_the_tool_error`, `test_mcp_gate.py::test_2025_client_gets_the_r4_fallback_and_never_an_elicitation` |
| R5.5 no blocking, no persisted state | Stateless; no elicitation id is generated (the 2026 request carries none) and nothing is stored | by construction; `test_mcp_gate.py` |
| R5.6 (A1) gate: (a) per-request capabilities; (b) the 2026-07-28 shape from a tool handler | Section 1 of this report | `test_mcp_gate.py` (4 tests) |
| R6.1 success copy, client-neutral, static pages | `signin.py:_PAGES["succeeded"]` | `test_docs_and_config.py::test_success_page_copy_is_client_neutral`, `::test_all_sign_in_pages_stay_free_of_client_brands` |
| R7.1 `.env.example` and README config row | `.env.example`; `README.md` configuration table | `test_docs_and_config.py::test_env_example_documents_the_switch`, `::test_readme_documents_the_new_features` |
| R7.2 README: 7-day expiry, publish to In production, `auth_status`, resources, elicitation + fallback | `README.md` (Google Cloud note, Re-authenticating, tool table, troubleshooting) | `test_docs_and_config.py::test_readme_documents_the_new_features` (phrases) |
| R7.3 CHANGELOG [Unreleased], CLAUDE.md invariants, docs/SPEC.md | `CHANGELOG.md`, `CLAUDE.md`, `docs/SPEC.md` (sections 3, 4.2, 5) | `test_docs_and_config.py::test_changelog_records_the_additions` |
| Invariant: tool set (SPEC D3) | `tests/test_server.py:EXPECTED_TOOLS` (seven); `test_gmail_client_exposes_no_mutating_call_besides_create_draft` (adds `rejected_at`, read-only) | `test_server.py`; `test_no_send_delete_or_modify_code_path_exists_in_source` (unchanged, still passes) |

## 3. Commands run and results

Environment: Windows host; all Python/quality commands ran in WSL Ubuntu with a venv outside the
Windows drive (`make install VENV=~/.venvs/gmail-mcp PYTHON=$(uv python find 3.12)`, Python 3.12.14, `mcp==2.2.0`).

| Command | Result |
|---|---|
| `make install` (WSL venv) | Succeeded (hash-pinned dev dependencies, editable install) |
| `make check` (baseline, before changes) | lint OK, mypy OK, **286 passed**, coverage 99.19% |
| `make check` (implementation round) | `ruff check` OK, `ruff format --check` OK (58 files), `mypy --strict` OK (36 files), **366 passed**, coverage **99.29%** (gate 85%, branch on) |
| `make check` (fix round 1: M1, m1) | **372 passed**, coverage **99.30%** |
| `make check` (fix round 2: amendment A1) | ruff OK, ruff format OK (65 files), mypy --strict OK (37 files), **372 passed**, coverage **99.29%** (gate 85%, branch on) |
| Live smoke (implementation round): `python -m gmail_mcp` on 127.0.0.1:8799 with fake credentials and `TOKEN_PATH` in `$HOME` (not the repo `data/`) | `tools/list` OK; `auth_status` returned structured + text (`not_signed_in`); `list_labels` with URL caps returned `input_required` with `url: http://127.0.0.1:8799/auth`; form-only caps returned the `AUTH_REQUIRED` tool error; `resources/list` (4 resources), `resources/read` of `auth/status` and `guide/auth` OK; unknown URI gave -32602; no bearer gave 401. Scratch scripts were deleted afterwards. Not re-run after A1 (the text block and 2025 removal are covered by the in-process tests). |
| `make audit` | **Not run**: no dependency changed (`requirements*.txt` untouched). |
| Container rebuild / `auth` against Google | **Not run**: needs the user's Google account and Docker. Recommended as the spec's live smoke step. |

## 4. Deviations and open decisions

**Resolved by Amendment A1** (`SPEC.md`, approved by the human 2026-10-25):

- **D1, resolved by A1 D5. R3.2 `lastModified` is not implemented.** The SDK fixes resource annotations at
  registration, so a per-read token-mtime value cannot be expressed without patching it. The status content
  still carries `token_age_days` and `rejected_at`. Nothing in the code, tests or docs presents `lastModified`
  as a feature. R3.2 in the spec now says the resource carries no `lastModified` annotation.
- **D2, resolved by A1 D6. R4.2 "exactly once".** The text has one bare URL token followed by a space; the
  quoted copies inside the three OS commands are permitted, as the R4.1 template requires. The tests assert the
  bare count is 1.
- **D4, resolved by A1 D7. The 2025-11-25 elicitation branch.** Removed in fix round 2. The `-32042` /
  `UrlElicitationRequiredError` path is gone from `server.py`, its unit test and its gate test. Only the
  2026-07-28 `InputRequiredResult` remains. Clients on 2025-11-25 and requests with unknown capabilities take the
  R4 tool error (R5.4). `test_mcp_gate.py` still pins that 2025 handshake capabilities are not retained and that
  a 2025 client gets the fallback.
- **D6, resolved by A1 D8. The `auth_status` text rendering.** The text block is now the full JSON of the same
  payload as `structuredContent`, instead of the earlier two-line summary. The output schema and the instrumented
  wrapper are unchanged.

**Still open, not part of A1:**

- **D3. The SDK prefixes tool errors** with `Error executing tool <name>: `. The R4 text follows that prefix,
  and the total stays well under 700 characters. Tests assert `AUTH_REQUIRED: ` is contained. A1 R4.2 accepts this.
- **D5. README publish steps were written from general knowledge of the Google Auth Platform console**
  ("Audience" page, "Publish app"). The exact labels were not checked against the live console. The human
  should confirm them on first use.

Implementation details (not deviations): every tool gained a keyword-only `ctx: Context[Any, Any]`
parameter so the wrapper can read the per-request capabilities (the SDK injects it by name and keeps it
out of the input schema; verified by the existing schema tests). `GmailClient.rejected_at` is a new public
read-only property, so the source-scan test's expected set gained `rejected_at`. Pre-existing uncovered
tool bodies in `server.py` (`get_thread`, `get_message`, `list_drafts` return lines) were uncovered at
baseline as well and still are; they are not new.

## 5. Files created or updated

| File | Change and reason |
|---|---|
| `src/gmail_mcp/errors.py` | R4: `auth_required_message`, `REJECTED_REASON`, `AuthRequiredError(reason)`; removed the old `AUTH_HINT` / `auth_hint` (the text that pointed at `scripts/auth.ps1`) |
| `src/gmail_mcp/auth_state.py` (new) | R1: pure `AuthStatus` model and `compute_auth_status`; fix round 1 (M1) adds `_token_mtime` and `_effective_rejection` so a rejection older than the token is ignored |
| `src/gmail_mcp/resources.py` (new) | R3: static guide text (`auth_guide`, `search_guide`, `limits_guide`) |
| `src/gmail_mcp/server.py` | R2 `auth_status` tool (A1 D8: text is the full JSON payload), R3 resource registration, R5 `_sign_in_response` (A1 D7: 2026-07-28 only; the 2025-11-25 `-32042` path removed) and the `_instrumented` hook, `ctx` injection on tools |
| `src/gmail_mcp/gmail_client.py` | R1.3 rejection record (`rejected_at`, recorded on RefreshError / 401). Fix round 1: cleared on a successful call (M1) and atomically in `reset` (m1) |
| `src/gmail_mcp/config.py` | R5.1 `auth_url_elicitation` setting |
| `src/gmail_mcp/signin.py` | R6.1 success copy |
| `src/gmail_mcp/auth.py` | No code change: its reason sentences already fit the R4.3 causes |
| `tests/conftest.py` | `FakeGmail.rejected_at`; `AUTH_URL_ELICITATION` added to the env cleanup |
| `tests/test_server.py` | Seven-tool set; R4 assertions; `rejected_at` in the client surface |
| `tests/test_auth.py`, `tests/test_gmail_client.py`, `tests/test_signin.py` | Assertions moved from the removed `AUTH_HINT` to the R4 contract |
| `tests/test_auth_state.py` (new) | R1 tests; fix round 1 moved rejection timestamps to call time |
| `tests/test_errors.py` (new) | R4 contract table |
| `tests/test_elicitation.py` (new) | R5 tests (live HTTP and decision/wrapper unit tests); A1 D7 removed the 2025 `-32042` assertions and now pins the 2025 fallback |
| `tests/test_mcp_gate.py` (new) | R5.6 gate findings as tests; A1 D7 replaced the 2025 `-32042` test with the 2025 fallback test |
| `tests/test_auth_status_and_resources.py` (new) | R2 and R3 tests; A1 D8 checks the text block equals the structured payload |
| `tests/test_gmail_rejection.py` (new) | R1.3 tests |
| `tests/test_rejection_recovery.py` (new) | Fix round 1 (M1, m1): out-of-process re-auth, success clears, reset race |
| `tests/test_docs_and_config.py` (new) | R5.1, R6.1 and R7 presence tests |
| `.env.example` | R7.1 `AUTH_URL_ELICITATION` (commented, default true) |
| `README.md` | R7.2: seven tools, resources, expiry and publish steps, elicitation (2026-07-28 only) and fallback, config row, troubleshooting |
| `CHANGELOG.md` | R7.3 [Unreleased] additions and changes; A1: the elicitation entry no longer mentions 2025-11-25 `-32042` |
| `CLAUDE.md` | R7.3 invariants: seven tools, four resources, elicitation rules (A1: 2026-07-28 only; the `-32042` mechanism is not implemented); no new unauthenticated route |
| `docs/SPEC.md` | R7.3: config row, section 4.2 error contract, tool table with `auth_status` |
| `docs/specs/agent-auth/implementation-report.md` | This report (A1: D1, D2, D4, D6 marked resolved; gate, traceability and file list updated) |
| `docs/specs/agent-auth/fix-round-2.md` (new) | Fix round 2 (amendment A1) report |

`docker-compose.yml` needed no change: `env_file: .env` already passes `AUTH_URL_ELICITATION` through.
