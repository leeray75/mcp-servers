# Implementation report: optional OAuth `iss` (`docs/specs/oauth-iss/SPEC.md`, I1–I6)

Implementer pass. Requirements source: the approved SPEC (I1–I6, §6 design, §8 test plan), with the two
spec-review round-4 minors applied (m-G, m-H). Nothing committed.

## Traceability: requirement → implementation → test

| Req | Implementation | Test(s) |
|---|---|---|
| I1 | `src/twitch_mcp/oauth.py` `PendingStore.claim`: `if iss is not None and not _same(iss, flow.iss):` → `REFUSED_ISS`, flow dropped | `test_oauth_units.py::test_correct_iss_is_accepted_when_present` (b), `::test_wrong_iss_is_refused_and_drops_the_flow` (c, second claim `REFUSED_NO_FLOW`), `::test_state_mismatch_and_iss_mismatch` (existing); HTTP `test_server.py::test_oauth_callback_refuses_wrong_iss` |
| I2 | Same line: `iss is None` no longer refuses; `server.py` unchanged (passes `params.get("iss")`) | `test_oauth_units.py::test_flow_complete_happy_path_installs_token` (switched to `_callback`, no `iss`, cites I2); `::test_absent_iss_is_not_checked_and_the_flow_is_claimed`; HTTP `test_server.py::test_oauth_callback_completes_without_iss` |
| I3 | Empty string is `not None`, so it is compared and refused | `test_oauth_units.py::test_empty_iss_is_present_and_refused_never_skipped` (d) |
| I4 | `state` check untouched (`_same`, ordered before `iss`); single-use unchanged | Existing `test_state_is_single_use`, `test_state_mismatch_and_iss_mismatch`, `test_flow_complete_refuses_state_mismatch_and_consumes_flow` |
| I5 | TTL untouched (`>= expires_at` → `REFUSED_EXPIRED`) | Existing `test_state_expires_after_ten_minutes` |
| I6 | No new log or echo. Success page is `SIGNED_IN_PAGE`, failure page is generic; `iss` never logged | HTTP tests assert the success body is exactly `SIGNED_IN_PAGE`, contains no `code`/`state`, and the refusal body contains no `state`, `code` or `evil.example`. Existing logging and no-echo tests unchanged |

Also applied per spec:
- Comment and docstring wording to "checked when present" in `oauth.py` (module docstring, `expected_iss`,
  `PendingFlow`, `PendingStore` class, `PendingStore.claim` (added sentence), `OAuthFlow.iss`) and in
  `security.py` line 9.
- `_callback(flow, pending, *, state=_PENDING_STATE, iss=None, code="code-1")` helper in
  `tests/test_oauth_units.py`. All 10 former `flow.complete(` call sites moved to it; the `state="wrong"` case keeps
  its `state` argument; `iss` defaults to absent (I-D2).
- Two HTTP tests in `tests/test_server.py` after the R47 test, using `running_app(...)` with default `connected=False`,
  settings via `server_settings(twitch_user_oauth=True, twitch_oauth_token_file=str(tmp_path / ...))`, and a
  per-test `POST /oauth2/token` handler branching on `grant_type`. The `authorization_code` body carries
  `user_id` and `user_login` (m-G). No validate handler. Exchange POSTs are counted by parsing each form body.
  The 0600 assertion carries a Linux-only comment (m-H).
- Docs: `CLAUDE.md` OAuth invariants bullet reworded (architecture bullet untouched). `CHANGELOG.md` line 38 wording
  updated, and a new `### Fixed` subsection added at the end of `[Unreleased]`. `docs/SPEC.md` R47 gets a
  superseded-in-part pointer (text not rewritten). `README.md` has no `iss` text (checked; no change).
- `docs/specs/oauth-iss/tasks.md`: items B–K ticked. A (human approval) and L (review rounds) are not mine.

## Fail-before-fix evidence (one targeted run)

The fix line was temporarily reverted to the pre-fix `if iss is None or not _same(iss, flow.iss):`, the two named
tests were run, and the file was restored from a backup (line 184 confirmed as the fixed form afterwards).

```
FAILED tests/test_oauth_units.py::test_flow_complete_happy_path_installs_token
FAILED tests/test_server.py::test_oauth_callback_completes_without_iss - assert 400 == 200
2 failed in 2.12s
```

Both fail against the unfixed code and pass with the fix (see gate below).

## Deviations and notes

1. **Unit happy-path grant body still uses `login`**, not `user_login`. The spec says to switch that test to the
   helper with its assertions unchanged, and it does not mention the body. The m-G change was applied only to the
   new HTTP test body, which is where the spec's fake body is written. The stored `user_login` in the unit test
   stays `""` as before. Harmless to the assertions, but it is the one place the fake still differs from `build_token`.
2. **Two extra unit cases** beyond spec (a)–(d): `test_absent_iss_is_not_checked_and_the_flow_is_claimed`
   (store-level I2) and `test_correct_iss_is_accepted_when_present` (I1 positive, spec (b)). These are the same
   requirements, pinned at the `claim()` level.
3. **Boot-time mint is unhandled in the two HTTP tests.** The token-exchange handler is registered after
   `running_app` has booted (the spec's "before the first callback" ordering). The boot `client_credentials` mint
   therefore gets 404 and the boot logs `twitch_degraded_at_boot`. The callback does not need the app token, so both
   tests pass. Consequence: the `client_credentials` branch of the handler is not exercised in practice. It is kept
   because the spec specifies it, and it is harmless.
4. **Docstring line-length**: ruff E501 (100 columns) forced shorter wording in two docstrings and a rewrap in
   `security.py` line 9. The meaning is the spec's "checked when present".
5. **`docs/SPEC.md` line 9** already read "Amendment 4 … (approved by: human)" when I started, so I made no change
   there. Only the R47 pointer was added.

No SPEC-GAP found.

## Commands and results

- `wsl -e bash -c "cd '/mnt/c/.../twitch-mcp-server' && timeout 900 make check VENV=/home/leeray/venvs/twitch"`
  - First run: `ruff check` failed, E501 on three lines (`oauth.py:94`, `oauth.py:138`, `security.py:9`). Fixed by
    rewording (deviation 4).
  - Second run, **pass**. `make check` = `lint` (ruff check, ruff format --check) → `typecheck` (mypy) → `test` (pytest).
    The chain reached pytest, so lint and mypy passed:
    `975 passed in 60.73s`; coverage `Required test coverage of 90% reached. Total coverage: 90.84%`.
- `ruff format --check .` → `84 files already formatted`.
- `mypy --strict src tests` → `Success: no issues found in 48 source files`.
- Fail-before-fix run (above): 2 failed as expected, then restored.

## Files changed

- `src/twitch_mcp/oauth.py` (I1–I3 fix line; docstring wording)
- `src/twitch_mcp/security.py` (comment wording)
- `tests/test_oauth_units.py` (`_callback` helper; 10 call sites moved; happy path cited; new cases (b)–(d) and I2 store case)
- `tests/test_server.py` (imports; `_register_token_exchange`, `_authorization_code_forms`; two new HTTP tests)
- `CLAUDE.md` (OAuth invariants bullet)
- `CHANGELOG.md` (`[Unreleased]` wording at line ~38; new `### Fixed` entry)
- `docs/SPEC.md` (R47 superseded-in-part pointer)
- `docs/specs/oauth-iss/tasks.md` (items B–K ticked)
- `docs/reports/oauth-iss/implementation-report.md` (this file)

No dependency files changed. No `.env` values read or printed. Nothing committed. The running container was not touched.
