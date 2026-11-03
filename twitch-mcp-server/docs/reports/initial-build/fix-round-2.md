# Fix Round 2 — implementer response to [`review-round-2.md`](review-round-2.md)

**Date:** 2026-10-30 · **Actor:** implementer (Phase 4) · **Reviewer verdict addressed:** CHANGES REQUESTED
(1 blocker, 2 majors, 3 minors)

No spec text was changed by this round: every fix here moves the implementation *toward* R43, R45/G5 and A2-D3, which
the reviewer showed were not yet fully honoured. No amendment was required, so none was sought.

## Blocker

### B1 — traceability table was wrong in 9 rows and its enforcement was vacuous · **CLOSED**

Root cause of the mis-filing was found: the generator tracked the docstring window with `NR` (a global line counter
that does not reset between files) instead of `FNR`. At each file boundary a marker in the first lines of the *next*
test file was still inside the previous file's last test's window, so the row kept the current filename and the
previous file's function name. That is exactly the 9 rows the reviewer identified — the functions were real, the
files were wrong.

Fixed on both sides, because a table that can rot silently is the same as no table:

1. **Generator corrected** (`FNR`, plus an explicit reset at `FNR == 1`). The table in
   [`implementation-report.md`](implementation-report.md) §2 was regenerated from the test corpus.
2. **Enforcement made real** — `tests/test_gate.py` gained two checks that read the *report itself*:
   - `test_the_report_has_a_row_for_every_requirement` fails if any R1–R57 loses its row.
   - `test_every_traceability_cell_names_a_test_defined_in_that_file` parses every `file.py::test_name` cell in the
     report and asserts that name is defined as a `def` in that file. A rename, a move, or a fabricated citation now
     fails the suite instead of quietly misleading the next reviewer.

Both new tests pass; the reviewer's "delete a citation and nothing notices" scenario is now caught for the report
table (the requirement→citation sweep still operates on the test corpus, which is where those citations live).

## Majors

### F2 — `project_automod_settings` laundered an unreadable body into an empty-looking record · **CLOSED**

The nine strictness levels are now declared separately from the identity keys (`_AUTOMOD_LEVEL_KEYS`,
`src/twitch_mcp/project.py`), and the projection refuses a record that carries none of them:

```
recognized = [key for key in _AUTOMOD_LEVEL_KEYS if record.get(key) is not None]
if not recognized:
    raise TwitchMcpError("upstream", "Twitch returned a moderation-settings body …")
```

Deliberately keyed on the *levels*, not on `broadcaster_id`: the legacy nested-`settings` body the reviewer tested
carries a `broadcaster_id`, so an identity-based check would still have passed it through and answered "no settings"
— the precise R45/G5 violation. A `0` level is a real level and still projects (`is not None`, not truthiness).

Pinned by `tests/test_project.py::test_automod_settings_rejects_the_nested_shape_it_replaced` (and the documented
body by `…_reads_integer_levels`, which also pins that `0` survives `exclude_unset`).

### F3 — a mistyped Helix value escaped as a JSON-RPC internal error · **CLOSED**

`_build` now wraps `model_validate` and converts `pydantic.ValidationError` into
`TwitchMcpError("upstream", …)`. This is the single choke point every entity passes through, so the whole projection
layer is covered rather than just the automod path — which matters because the reviewer correctly noted that *no*
projection guarded value types, only shapes. `from exc` preserves the cause for the (redacted) log.

Pinned by `tests/test_project.py::test_automod_settings_mistyped_field_is_a_tool_error_not_an_internal_error`,
which asserts `kind == "upstream"` on the error a tool-level handler will render, rather than an
`MCPError(INTERNAL_ERROR, …)` reaching the agent.

## Minors

### F1 — the `not_found` hint told operators to widen the allowlist for an object that does not exist · **CLOSED**

The inline hint was removed from the 404 branch in `src/twitch_mcp/twitch_client.py`, so the default hint applies —
which points only at the id/login. The branch carries a comment recording why: it is reachable only after the guard
admitted the id, so naming the allowlist would send someone to widen it for a non-existent channel, re-importing the
ambiguity A2-D2 exists to remove.

### F4 — `project_schedule` accepts `data` without a `segments` key · **ACCEPTED, with justification**

Not changed, for a reason that outranks the tidy symmetry with F2: an empty schedule is a *legitimate* state, and
whether Twitch omits `segments` entirely for a broadcaster who has never scheduled anything is not something this
spec pins or this build has observed against the live API (A2/A3 are operator-run and unverified). A hard
`"segments" in data` check would turn a genuine "no schedule" into an `upstream` error — trading a cosmetic
silent-empty risk for a real false-failure on a normal account. `data` must still be a mapping carrying
`broadcaster_id` (a bare array or any other shape already raises `upstream`, now pinned by
`test_schedule_projection_refuses_a_bare_array_body`). Recommended follow-up if the operator's live run ever shows an
omitted `segments`: revisit with evidence rather than guesswork.

### F5 — `config.py:295` `# type: ignore[call-arg]` · **NO ACTION REQUIRED**

Reviewed and cleared by the reviewer itself; recorded here so the disposition is complete.

## Coverage added alongside

The three A2-D3 shapes had no direct projection tests — they were exercised only through the size fixtures, which is
how the round-1 shape defects survived in the first place. `tests/test_project.py` gained seven projection tests
(documented body projects; superseded shape fails) covering automod levels, the chatters flat list with `total`, and
the schedule `data` object with its nested `category`.

## Gate after these fixes

```
$ ruff check src tests tools                All checks passed!
$ ruff format --check src tests tools       50 files already formatted
$ mypy                                      Success: no issues found in 49 source files
$ pytest -q                                 949 passed in 90.50s
                                            Total coverage: 90.80%  (gate 90%)
```

Ready for review round 3.
