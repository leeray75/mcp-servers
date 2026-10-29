# Review round 3: discord-mcp-server initial build

- **Reviewer:** independent read-only reviewer. Spec: `../../SPEC.md` including section 17 (amendment A2, approved 2026-10-29).
- **Verdict: APPROVED** (0 blockers, 0 majors, 1 minor, 0 spec issues). The single minor should be fixed (one line plus one test
  assertion) but needs no further review round.
- Method: read SPEC section 17 and the code, tests and docs from disk; ran `_application_flags` myself against 25 inputs; re-ran the
  gate. `fix-round-2.md` was read only for the claimed locations.

## Gate results observed

| Command | Result |
|---|---|
| `make check VENV=~/.venvs/discord-mcp` | ruff + format clean, `mypy --strict` clean, **454 passed**, total coverage **96.35%** (gate 90%) |
| `make audit VENV=~/.venvs/discord-mcp` | No known vulnerabilities found |

## A2 verification

**A2-R6a (independent parsing, OR, ASCII-only, never raises): correct.** `discord_client.py:42` (`[0-9]{1,30}` used with
`fullmatch`), `:136-149` (`_usable_flags`), `:152-166` (`_application_flags`). `flags` is usable only as a non-boolean, non-negative
JSON integer; `flags_new` as the same or an ASCII-digit string of 1-30 characters; the regex runs before `int()`, so `int()` is never
fed unvalidated text. I called `_application_flags` directly on these inputs; none raised:

| Input | Result |
|---|---|
| `{}` | `(None, None)` |
| `flags: 8` / `flags_new: "16"` / `flags_new: 16` | `(8,"flags")` / `(16,"flags_new")` / `(16,"flags_new")` |
| `flags: 1<<14`, `flags_new: str(1<<18)` | OR `278528`, `"flags+flags_new"` |
| `flags: 5`, `flags_new: "5"` | `(5, "flags+flags_new")` |
| `"²"`, `"٣"` (Arabic-Indic digit), `"-1"`, `"1e3"`, `"0x10"`, `" 12"`, `"12\n"`, `""`, `12.0`, `[]`, 31-digit string | all `(None, None)` |
| 30-digit string | usable (`"flags_new"`) |
| `flags: -1`, `True`, `"8"`, `nan`, `{}`, `None`; `flags_new: True` | all unusable |
| `flags: 8`, `flags_new: "x"` | `(8, "flags")` (malformed field ignored beside a good one) |

`probe()` still returns `ok` with `flags=None` when neither field is usable, so `plan_tools` (unchanged) yields `flags_missing`
unless forced, and the enable/disable/force precedence is untouched.

**A2-R7a (effective integer and `flags_source` values): implemented, one presentation deviation (m1 below).** `BootProbe.flags_source`
takes exactly `flags`, `flags_new`, `flags+flags_new` or `None`; `ToolPlan` carries both; the boot line includes them.

**A2 tests: all present and meaningful.** `tests/test_flags_a2.py` (40 cases) covers `flags` only, `flags_new` integer and string,
disjoint bits (and asserts both tools register via `plan_tools`), equal bits, neither present, all eight listed malformed
`flags_new` values (probe completes, field ignored, `flags_missing` results), malformed `flags_new` beside a valid `flags`, negative
and boolean `flags`, boolean `flags_new`, the 30/31-digit boundary, and the boot-line source for seven cases. They assert concrete
values (`probe.flags`, `probe.flags_source`, tool registration), not just absence of exceptions.

**Docs.** Server `README.md:191-194`, `CLAUDE.md:80-83` and `CHANGELOG.md:28-29` describe independent parsing, OR and the source
values. No regressions found: whole suite green, no other module changed behaviour, no dependency change.

## Issues

### Blocker / Major

None.

### Minor

- **m1. Boot line prints `source=none` where A2-R7a says `null`.** `server.py:537` (`source = plan.flags_source or "none"`) and the
  assertions at `tests/test_flags_a2.py:166` and `:177` (`source=none`). A2-R7a says the line records `flags_source` as one of
  `flags`, `flags_new`, `flags+flags_new` or `null`. The implementer's argument ("existing display convention") does not hold within
  the same line, which already renders an absent flags integer as `null` (`server.py:536`): `application_flags: null source=none`
  is inconsistent, and the amendment text, which the human approved, says `null`. The docs disagree with each other as well:
  `CLAUDE.md:82-83` says `null`, `README.md:194` says `none`. The deviation is cosmetic and harmless to behaviour, so it is minor,
  but it is a literal departure from approved wording and costs one line to remove. Fix: `source = plan.flags_source or "null"`,
  update the two test assertions, and change `README.md:194` to `null`. (`"none"` is still used elsewhere in the line for empty
  tool lists, which A1-R7 did not specify; that is fine.)

## Spec issues

None. A2 is precise, consistent with A1 and implementable as written.

## Verdict

**APPROVED.** A2-R6a and the A2 tests are implemented exactly; A2-R7a is implemented except for the `null` versus `none` rendering,
which should be corrected (m1) before the summary report. No further review round is needed for that one-line change if the gate
stays green.
