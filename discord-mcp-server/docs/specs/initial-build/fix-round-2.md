# Fix round 2: discord-mcp-server initial build

- **Input:** `review-round-2.md` (minor n1, observation O2) and `../../SPEC.md` section 17 (amendment A2, approved 2026-10-29,
  by the human). A2 replaces A1-R6's flag-source rule and extends A1-R7; everything else in A1 stands.
- **Scope, as A2 requires:** A2-R6a, A2-R7a, the "A2 tests" list, and the flag-detection docs. n2, n3, n4 and O1 are not
  touched (A2 out of scope).
- **Result:** every A2 item is implemented and tested as written. Nothing in A2 is wrong or impossible, so no spec issue was raised.
- **Nothing committed. No `.env` read or created. No sibling server changed. No dependency changed (`make lock` not run).**

## A2 items

| A2 item | Status | Where (file:line) | Test(s) |
|---|---|---|---|
| A2-R6a: `flags` usable iff a JSON integer, not a boolean, `>= 0` | Implemented | `discord_client.py:136-149` (`_usable_flags`, `accept_string=False`) | `test_flags_a2.py::test_negative_boolean_or_unusable_flags_is_ignored[-1, True, 12.5, "7", None, [1]]` |
| A2-R6a: `flags_new` usable iff a JSON integer (not boolean, `>= 0`) or a string matching `^[0-9]{1,30}$` (ASCII digits) parsed with `int()` | Implemented | `discord_client.py:42` (`_FLAGS_NEW_RE = [0-9]{1,30}`, used with `fullmatch`, so ASCII only); `:147` | `test_flags_a2.py::test_flags_new_integer_only_is_used`, `::test_flags_new_decimal_string_only_is_used`, `::test_thirty_digit_string_is_usable_and_thirty_one_is_not`, `::test_boolean_flags_new_is_ignored` |
| A2-R6a: effective flags = bitwise OR of every usable field | Implemented | `discord_client.py:152-166` (`_application_flags`: `own | new`) | `test_flags_a2.py::test_both_present_with_disjoint_bits_enables_both_intents` (OR enables both intents, `plan_tools` registers both tools); `::test_both_present_and_equal` |
| A2-R6a: parsing never raises; malformed values ignored; probe completes | Implemented | `discord_client.py:136-166` (no `int()` on unvalidated input; the regex runs before `int()`) | `test_flags_a2.py::test_malformed_flags_new_is_ignored_and_probe_completes[8 values]`, `::test_malformed_flags_new_beside_valid_flags_is_ignored[8 values]`, `::test_unusable_flags_falls_through_to_usable_flags_new` |
| A2-R6a: neither usable -> unknown -> `flags_missing` / force / enable rules unchanged | Implemented (unchanged code) | `capabilities.py:84-115` (`plan_tools`, not modified) | `test_flags_a2.py::test_neither_present_is_unknown`; `test_fix_round1.py::test_plan_with_flags_missing_and_force_flags[*]` (existing) |
| A2-R7a: boot line records the effective integer (or `null`) and `flags_source` in {`flags`, `flags_new`, `flags+flags_new`, `null`} | Implemented | `discord_client.py:166` (source strings); `server.py:531-541` (`_boot_summary`; `null` rendered as `none` in the line, so the line reads `application_flags: null source=none`) | `test_flags_a2.py::test_boot_line_reports_the_effective_flags_and_source[7 cases]`, `::test_boot_line_for_a_forced_tool_with_unknown_flags` |

Display note on `null`: the boot line has always written the absent source as `none`. A2-R7a's `null` is the value; the text is
`none`. The tests assert that text, and the report notes it.

## A2 tests (section 17) mapped to test names

| A2 test case | Test (`tests/test_flags_a2.py`) |
|---|---|
| `flags` only | `test_flags_only_is_used` |
| `flags_new` only, integer | `test_flags_new_integer_only_is_used` |
| `flags_new` only, decimal string | `test_flags_new_decimal_string_only_is_used` |
| both present, disjoint intent bits (OR enables both) | `test_both_present_with_disjoint_bits_enables_both_intents` |
| both present and equal | `test_both_present_and_equal` |
| neither present | `test_neither_present_is_unknown` |
| malformed `flags_new`: `"²"`, `"-1"`, `"1e3"`, `"0x10"`, `" 12"`, `12.0`, `[]`, 31-digit string | `test_malformed_flags_new_is_ignored_and_probe_completes[*]` (all 8 values; probe completes; `flags_missing` results) |
| negative or boolean `flags` | `test_negative_boolean_or_unusable_flags_is_ignored[-1, True, ...]` |
| boot line `flags_source` for each case | `test_boot_line_reports_the_effective_flags_and_source[*]` (7 cases) |

Extra cases beyond the list (each derived from A2-R6a's wording): `test_malformed_flags_new_beside_valid_flags_is_ignored[*]`,
`test_unusable_flags_falls_through_to_usable_flags_new`, `test_boolean_flags_new_is_ignored`,
`test_thirty_digit_string_is_usable_and_thirty_one_is_not`, `test_bit_constants_are_the_r6_bits`.

## Docs updated (flag detection)

| File | Change |
|---|---|
| `CLAUDE.md` (server), invariant "Intents" | Rewritten for A2: independent parsing, OR of usable fields, `flags_source` values, never raises, `flags_missing`, force and enable rules |
| `README.md`, "Intent detection" bullet | Rewritten for A2: independent reads, OR, malformed ignored, `flags_source` values, boot line |
| `CHANGELOG.md` (server), `[Unreleased]` "Changed" | Intents line rewritten for A2 |
| Troubleshooting row `flags_missing` | Unchanged; still accurate |

Root docs do not describe flag detection; unchanged.

## Gate results (final)

| Command | Result |
|---|---|
| `make check VENV=~/.venvs/discord-mcp` | exit 0: ruff check and format clean (46 files); mypy `--strict` clean (33 files); pytest **454 passed**; total branch coverage **96.35%** (gate 90%) |
| `make audit VENV=~/.venvs/discord-mcp` | exit 0: no known vulnerabilities found |
| `make lock` | Not run: no dependency changed |

Before this round: 414 passed at 96.25%. This round adds 40 A2 cases (`test_flags_a2.py`): 454 total.

## Commands run

| Command | Result |
|---|---|
| `awk` on `SPEC.md` section 17 and `review-round-2.md` (n1, O2) | Read; requirements as above |
| `ruff format tests`; `pytest -q --no-cov tests/test_flags_a2.py` | 40 passed |
| Python patch scripts in the Windows temp directory (CLAUDE.md, README, CHANGELOG, report rows) | Applied; verified by counts and grep |
| `ruff check --fix .` (one import-order item in `test_flags_a2.py`) | Fixed |
| `make check VENV=~/.venvs/discord-mcp` (final) | exit 0; 454 passed; 96.35% |
| `make audit VENV=~/.venvs/discord-mcp` (final) | exit 0; no known vulnerabilities |
