# Fix round 2: agent-auth (Amendment A1)

- **Date:** 2026-10-25
- **Driver:** Amendment A1 to `docs/specs/agent-auth/SPEC.md`, approved by the human. This is not a review finding.
  Review round 2 was APPROVED before this round.
- **Scope:** only the four A1 decisions (D5 to D8). No other requirement or code changed.
- **Not committed, not pushed.**

## What changed, per item

### 1. D5 / R3.2: no `lastModified` on `gmail://auth/status`. Kept as built

- No code change was needed. `server.py:_register_resources.auth_status_resource` registers the resource
  without an annotation for `lastModified`. Its annotations are the static audience and priority values.
- Checked that no code, test, README, CHANGELOG, CLAUDE.md or SPEC mentions `lastModified` as a feature of
  `gmail://auth/status`. The only remaining mentions are in the historical review and fix reports, which
  describe the decision.

### 2. D6 / R4.2: one bare URL token, quoted copies allowed, SDK prefix accepted. Kept as built

- No code change. `errors.py:auth_required_message` keeps the bare token and the three quoted OS commands.
- No test file changed for this item. The assertions and comments in `tests/test_errors.py` and
  `tests/test_signin.py` already describe one bare token (`count(url + " ") == 1`) and the accepted SDK prefix,
  so they match A1 as written. The implementation report's D2 entry was updated.

### 3. D7 / R5.2: 2025-11-25 `-32042` path removed. Only the 2026-07-28 elicitation remains

Code (`src/gmail_mcp/server.py`):
- `_sign_in_response`: now returns `InputRequiredResult | None`. It returns the 2026-07-28 `InputRequiredResult`
  only when `ctx.protocol_version == MODERN_REVISION`. Every other revision, and unknown capabilities, returns
  `None`, which the wrapper turns into the R4 `ToolError` (R5.4).
- `_instrumented`: the branch that raised `UrlElicitationRequiredError` is removed. An elicitation response is
  returned, and anything else becomes the R4 `ToolError`.
- Removed: `URL_ELICITATION_REVISION` constant, `import uuid` (its only use), and the
  `UrlElicitationRequiredError` import.

Tests:
- `tests/test_elicitation.py`:
  - removed the `UrlElicitationRequiredError` and `URL_ELICITATION_REVISION` imports;
  - `test_2025_revision_with_url_capability_returns_url_elicitation_error` replaced by
    `test_2025_revision_with_url_capability_gets_no_elicitation` (asserts `None`);
  - `test_instrumented_wrapper_maps_the_decision_to_the_right_response`: the 2025 case now expects the R4
    `ToolError`, not `-32042`;
  - the live handshake test `test_legacy_2025_handshake_client_gets_the_fallback_when_capabilities_are_unknown`
    kept, using a test-local `LEGACY_REVISION = "2025-11-25"` constant.
- `tests/test_mcp_gate.py`:
  - the `url_error` tool and the test that expected `-32042` are removed;
  - `test_gate_b_url_elicitation_error_is_produced_for_the_2025_revision` replaced by
    `test_2025_client_gets_the_r4_fallback_and_never_an_elicitation` (no JSON-RPC error, no `inputRequests`,
    `isError: true`). The gate app's bare `auth_fails` tool does not use the production wrapper, so the R4 text
    itself is asserted in `test_elicitation.py` against the production server;
  - `test_stateless_legacy_handshake_capabilities_are_not_retained` kept: it still pins that 2025 handshake
    capabilities are not retained;
  - module docstring updated: gate (b) is the 2026-07-28 shape only.

Docs:
- `CLAUDE.md` (Invariants): the sentence that said the server "decides per protocol revision (2026-07-28
  `InputRequiredResult`; 2025-11-25 `-32042`)" now says only 2026-07-28 gets the elicitation, that the `-32042`
  mechanism is not implemented, and that 2025-11-25 clients get the R4 `AUTH_REQUIRED` tool error.
- `README.md`: the URL-elicitation bullet now says only clients on MCP 2026-07-28 are asked to open the page;
  earlier revisions get the `AUTH_REQUIRED` text.
- `CHANGELOG.md`: the elicitation entry now says 2026-07-28 only (`InputRequiredResult`, `mode: "url"`) and that
  earlier revisions get the `AUTH_REQUIRED` text. The `-32042` mention is gone.
- `docs/specs/agent-auth/implementation-report.md`: section 1 (gate) and section 4 (D4) updated.

### 4. D8 / R2.3: `auth_status` text block is the full JSON of the structured payload

- `src/gmail_mcp/server.py:auth_status`: the `TextContent` block is now `status.model_dump_json()`, the same data
  as `structured_content=status.model_dump(mode="json")`. The two-line summary (`_status_text`) is removed.
  The output schema (`-> AuthStatus`) and the `_instrumented` wrapper are unchanged.
- Secrets: the payload is the `AuthStatus` model, which holds only the state, booleans, scope names, the token
  age, the rejection timestamp, warnings, the next step and the sign-in URL. No secrets are added.
- Tests: `tests/test_auth_status_and_resources.py::test_auth_status_signed_out_is_a_normal_result` now asserts
  `json.loads(text) == result.structured_content`, that the state is `not_signed_in`, and that the old
  `Gmail sign-in:` summary text is gone.
- Docs: no README, SPEC or CHANGELOG text described the two-line form, so none changed. The implementation
  report's R2.3 row and D6 entry were updated.

## Tests added and removed

| Change | Count effect |
|---|---|
| Removed `test_2025_revision_with_url_capability_returns_url_elicitation_error` | -1 |
| Added `test_2025_revision_with_url_capability_gets_no_elicitation` | +1 |
| Removed `test_gate_b_url_elicitation_error_is_produced_for_the_2025_revision` | -1 |
| Added `test_2025_client_gets_the_r4_fallback_and_never_an_elicitation` | +1 |
| Changed `test_instrumented_wrapper_maps_the_decision_to_the_right_response` (2025 case) and `test_auth_status_signed_out_is_a_normal_result` (text assertion) | 0 |

Net: 372 tests before and after.

## make check

Run in WSL venv `~/.venvs/gmail-mcp` (`make check VENV=$HOME/.venvs/gmail-mcp`):

- ruff check: all checks passed. ruff format: 65 files already formatted.
- mypy --strict: success, no issues in 37 source files.
- pytest: **372 passed**, coverage **99.29%** (gate 85%, branch on). `make check` exit code 0.
- Uncovered lines: `server.py` pre-existing tool-return lines (`get_thread`, `get_message`, `list_drafts`). They were
  uncovered at baseline too.

No dependency file changed, so `make audit` was not run.

## Files touched this round

| File | Why |
|---|---|
| `src/gmail_mcp/server.py` | A1 D7: removed the 2025 `-32042` path (`URL_ELICITATION_REVISION`, `uuid`, `UrlElicitationRequiredError` imports, the 2025 branch in `_sign_in_response` and `_instrumented`). A1 D8: `auth_status` text is the full JSON; `_status_text` removed |
| `tests/test_elicitation.py` | A1 D7: removed `-32042` imports and assertions; 2025 now gets no elicitation and the R4 fallback |
| `tests/test_mcp_gate.py` | A1 D7: replaced the 2025 `-32042` gate test with the 2025 fallback test; removed the `url_error` tool; updated docstring |
| `tests/test_auth_status_and_resources.py` | A1 D8: the text block is asserted to equal the structured payload |
| `README.md` | A1 D7: elicitation is 2026-07-28 only; earlier revisions get the `AUTH_REQUIRED` text |
| `CHANGELOG.md` | A1 D7: elicitation entry is 2026-07-28 only; the `-32042` mention is removed |
| `CLAUDE.md` | A1 D7: the invariant describes 2026-07-28 only and states the `-32042` mechanism is not implemented |
| `docs/specs/agent-auth/implementation-report.md` | A1: D1, D2, D4 and D6 marked resolved by A1; gate, traceability and file-list rows updated |
| `docs/specs/agent-auth/fix-round-2.md` (new) | This report |

Unchanged on purpose: `docs/SPEC.md` (its elicitation text is a pointer to the amendment, not a 2025 description),
`src/gmail_mcp/errors.py` (A1 D6 keeps the R4 text as built), `src/gmail_mcp/auth_state.py` and
`src/gmail_mcp/gmail_client.py` (no A1 item touches them), and all R1, R3 and R6 code.
