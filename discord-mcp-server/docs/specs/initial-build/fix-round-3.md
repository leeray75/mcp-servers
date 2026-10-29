# Fix round 3: discord-mcp-server initial build

- **Input:** `review-round-3.md` (APPROVED with one minor, m1): the boot line printed `source=none` where A2-R7a requires `null`.
- **Scope:** exactly that change. No other behaviour, doc or spec text changed. Nothing committed. No `.env` read or created.
  No sibling server changed. No dependency changed (`make lock` not run).

## Change

| File:line | Before | After |
|---|---|---|
| `src/discord_mcp/server.py:537` | `source = plan.flags_source or "none"` | `source = plan.flags_source or "null"` |
| `tests/test_flags_a2.py:166` | `source_text = "none" if expected_source is None else expected_source` | `source_text = "null" if expected_source is None else expected_source` |
| `tests/test_flags_a2.py:177` | `assert "application_flags: null source=none" in line` | `assert "application_flags: null source=null" in line` |
| `tests/test_fix_round1.py:357` | `assert "application_flags: null source=none" in line` | `assert "application_flags: null source=null" in line` |
| `README.md:194` | `` `flags+flags_new`, or `none`) `` | `` `flags+flags_new`, or `null`) `` |

Note: `tests/test_fix_round1.py:357` was not in the coordinator's list. It asserts the same boot-line text, so it failed
after the server change. It is the only extra edit and is required by the change.

## Tests

- `tests/test_flags_a2.py::test_boot_line_reports_the_effective_flags_and_source[*]` (7 cases, including the
  `neither present` and `malformed` cases where `flags_source` is null): pass.
- `tests/test_flags_a2.py::test_boot_line_for_a_forced_tool_with_unknown_flags`: pass.
- `tests/test_fix_round1.py::test_boot_line_records_forced_tools_and_observed_flags`: pass.

## Gate results (final)

| Command | Result |
|---|---|
| `make check VENV=~/.venvs/discord-mcp` | exit 0; ruff check and format clean (48 files); mypy `--strict` clean (33 files); pytest **454 passed**; total branch coverage **96.35%** (gate 90%) |
| `make audit VENV=~/.venvs/discord-mcp` | exit 0; no known vulnerabilities found |

The first `make check` after the edit failed on one test (`test_fix_round1.py:357`, the extra assertion above). It passes
after that line was updated. The counts above are for the final run.

## Commands run

| Command | Result |
|---|---|
| `grep` for `source=none` and `"none"` in server, tests, README and docs | Found the five locations in the table above |
| Python patch script (temp directory) for `server.py`, `test_flags_a2.py`, `README.md` | Applied; each anchor matched exactly once |
| `sed` on `tests/test_fix_round1.py:357` | Applied |
| `ruff format .`; `make check`; `make audit` (WSL) | See gate results |
