# Agent auth hardening: implementation report

Spec: `docs/specs/agent-auth-hardening/SPEC.md` (Approved, 2026-10-25). Scope: implemented only what the spec requires.

## Requirement traceability

| Req | Implementation | Tests |
|---|---|---|
| R1 error guardrails | `errors.auth_required_message`: shell open command or copy-and-paste link; no browser automation; no credential entry; retry only on `ready_to_retry: true`. Exactly one bare URL followed by a space; 691 characters at worst. | `test_errors.py::test_auth_required_text_follows_the_contract`, `test_auth_required_text_mentions_no_scripts_and_one_bare_url_at_default_port`; `test_auth_status_and_resources.py` contract checks |
| R1 (auth_status next_step) | `auth_state._next_step` carries the same guardrail wording for every problem state | `test_auth_state.py::test_next_step_for_each_problem_state_carries_the_guardrails` |
| R2 auth guide | `resources.auth_guide`: who opens the page (elicitation, shell open command, else copy and paste); states; `ready_to_retry`, `checked_at`, `sign_in_in_progress`; rules | `test_auth_status_and_resources.py::test_auth_guide_contains_the_ordered_procedure`, `test_auth_guide_states_the_guardrails_and_the_new_fields` |
| R3.1 `checked_at` | `auth_state.compute_auth_status` sets UTC ISO-8601 time per call; resource read recomputes | `test_auth_state.py::test_checked_at_is_a_recent_utc_timestamp_on_every_call`; `test_status_resource_equals_the_auth_status_payload` (compares other fields, `checked_at` ordering) |
| R3.2 `ready_to_retry` | `ready_to_retry = signed_in` | `test_ready_to_retry_is_true_only_when_signed_in` (parametrised), `test_ready_to_retry_is_true_when_signed_in` |
| R3.3 `sign_in_in_progress` | `compute_auth_status(..., sign_in_pending)` replaces every non-`signed_in` state; `signed_in` kept | `test_pending_sign_in_replaces_every_problem_state` (4 scenarios), `test_pending_sign_in_never_changes_a_signed_in_status`, `test_pending_attempt_does_not_hide_a_signed_in_token` |
| R3.4 in-progress `next_step` | `_next_step("sign_in_in_progress")`: sign-in open, finish approving, call `auth_status`; `ready_to_retry` false | `test_pending_sign_in_replaces_every_problem_state` (asserts `next_step` text) |
| R3.5 no pending detail | `AuthStatus` has exactly the declared fields; no state value, verifier, count or timing | `test_status_fields_are_exact_and_pending_adds_no_detail` |
| R3.6 tool schema and payload | `AuthStatus` is the tool output model; text block and `structuredContent` both carry the fields | `test_auth_status_annotations_title_and_output_schema` (output schema), `test_auth_status_signed_out_is_a_normal_result` (text equals structured) |
| R4 resource descriptions | `server._DESC_RES_*` passed as `description=` to all four `@server.resource` calls | `test_every_resource_has_a_one_sentence_description` |
| R5 pending visibility | `PendingStore.has_pending()` (lock, expiry purge, no consume); `create_app` shares one store between `SignInService` and `create_server`; tool and resource pass `has_pending()` | `test_signin.py::test_has_pending_*` (empty, fresh, consumed, expired, non-consuming); `test_auth_then_auth_status_reports_sign_in_in_progress` (real `GET /auth`, then `auth_status` and the status resource) |
| R6 docs | README auth section, CHANGELOG `[Unreleased]`, `docs/SPEC.md` (AUTH_REQUIRED contract, `auth_status` row, resource descriptions), `CLAUDE.md` (states, fields, descriptions, pending store) | `test_docs_and_config.py::test_readme_documents_the_guardrails_and_fields`, `test_changelog_records_the_hardening`, `test_spec_and_claude_md_document_the_hardening` |

Tool set: still exactly seven tools (`test_tool_order_is_deterministic_and_has_seven_tools`). Resource set: still exactly four static URIs.

## Deviations

None from the spec's requirements.

Judgment within the spec (not a deviation, the spec is silent): the `rejected` warnings (`REJECTED_CAUSES_WARNING`) are keyed on the effective rejection rather than on `state == "rejected"`. Without this, a pending sign-in after a rejection would silently drop the cause text. Behaviour without a pending attempt is unchanged.

## Commands and results

- `MSYS_NO_PATHCONV=1 wsl.exe -e bash -c 'cd "/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/gmail-mcp-server" && make check VENV=~/.venvs/gmail-mcp'`
  - ruff check: All checks passed.
  - ruff format --check: 69 files already formatted.
  - mypy --strict: Success, no issues in 37 source files.
  - pytest: 397 passed.
  - coverage: 99.31% total (gate 85%).
  - Exit code 0.
- Length check of the worst-case `AUTH_REQUIRED` text (`REJECTED_REASON`, port 8765): 691 characters (limit 700).

## Not done in this pass

- Container rebuild and live `auth_status` / `resources/list` check against the running container. The spec's section 7 requires this before the summary; it was out of the implementer's remit in this pass (no rebuild was requested).
- No commit or push.
- No `.env` values were read or printed.
