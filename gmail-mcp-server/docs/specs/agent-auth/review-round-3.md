# Review round 3: agent-auth

Reviewer: separate read-only pass, fresh context. Spec: `docs/specs/agent-auth/SPEC.md` (Approved, incl. human-approved Amendment A1, D5-D8). Date: 2026-10-25.
Known and accepted, not raised: D3/D5 README Google console labels unverified.

## Gate (re-run in WSL)

`make check VENV=~/.venvs/gmail-mcp`: 372 passed in 14.02s; total coverage 99.29% (gate 85%). New modules `auth_state.py` and `resources.py` are 100%; `server.py` 98% (lines 266, 276, 293 missed, all pre-existing paths); `config.py` 98%. Ruff/mypy steps of `make check` completed without failure.

## A1 verification

- D5: no `lastModified` as a feature. The only grep hits are the implementation report explaining its absence. `server.py` resource annotations carry only audience and priority. `docs/SPEC.md` R3.2 and the spec state the resource has no `lastModified`. OK.
- D6 / R4.2: `errors.py:auth_required_message` emits the bare URL once, followed by a space ("open {url} in their browser"), and quoted copies in the three OS commands. No `scripts/auth` text. `tests/test_errors.py:26-31` asserts `count(f"{url} ") == 1`, the quoted forms, no `scripts/auth`, length <= 700. OK.
- D7: no leftover `UrlElicitationRequiredError`, `-32042`, `uuid` or `URL_ELICITATION_REVISION` in `src`, `tests` (sources) or user docs. The only mentions are intentional: a test docstring (`tests/test_elicitation.py:225`) and CLAUDE.md / implementation-report text stating the mechanism is not implemented. `server.py:_sign_in_response` returns `InputRequiredResult` only when the kill switch is on, `elicitation.url` is declared and `ctx.protocol_version == "2026-07-28"`; otherwise `None`, so the R4 `ToolError` is raised. Only mode `url` is ever sent. `tests/test_mcp_gate.py:128` pins the 2025 fallback and the absence of any elicitation; gate tests (a) and (b) pass. OK.
- D8: `auth_status` returns a `CallToolResult` whose text block is `status.model_dump_json()` and whose `structured_content` is the same model; test at `tests/test_auth_status_and_resources.py:86-91` asserts `json.loads(text) == structured_content`. The payload holds no secrets (resource credential test passes). OK.

## Regression check

- Seven tools in the specified order, pinned by `test_tool_order_is_deterministic_and_has_seven_tools`; four resources listed and tested.
- No new unauthenticated routes (resources go through the guarded `/mcp` route; test present). `auth_state.py` and `resources.py` import no network libraries (only `auth`, `config`, `errors`, `tools` constants, pydantic, stdlib).
- Server remains `stateless_http=True`, `json_response=True`; no dependency or Docker file changes (`git diff` of requirements/pyproject is empty). No changes outside `gmail-mcp-server/` other than the root `AGENTS.md` / `CLAUDE.md`, which were already modified before this feature and are not part of this diff scope.
- `_instrumented` logs only tool name, duration and outcome; error text contains no secrets.
- M1/m1 fixes from earlier rounds remain covered by `test_rejection_recovery.py` and `test_gmail_rejection.py`, which pass.

## Docs consistency

README (lines 143-146), CHANGELOG [Unreleased] (lines 30-34), CLAUDE.md (line 45), `docs/SPEC.md`, `.env.example` (`AUTH_URL_ELICITATION`) and the implementation report all state: elicitation for 2026-07-28 only, earlier revisions get `AUTH_REQUIRED`, no `lastModified`, bare link plus OS commands. Cross-references use `docs/specs/<feature>/` paths; no stale `docs/reauth` or `docs/reports/reauth` references remain.

## Issues

Blockers: none.
Majors: none.

Minors:
- m1 (accepted, informational): `server.py` `auth_status` uses `cast(AuthStatus, CallToolResult(...))` to supply a custom text block. It works and is covered by tests, but relies on the SDK passing `CallToolResult` through while validating `structured_content` against the output schema. Fix only if the SDK pin changes; the existing tests would catch a break.
- m2 (cosmetic): `tasks.md` and `workflow.md` in the feature directory are not referenced by the report; no action required.

Counts: blocker 0, major 0, minor 2 (both accepted, no change needed).

VERDICT: APPROVED
