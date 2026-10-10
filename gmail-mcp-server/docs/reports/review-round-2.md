# Review round 2

Verdict: **APPROVE** (no BLOCKER or MAJOR findings; 1 MINOR remains, plus 1 MINOR observation)

Counts: 0 BLOCKER, 0 MAJOR, 2 MINOR.

Reviewed commit 8b4de54 against the round-1 findings (diff of `src/`, `scripts/`, `README.md`, tests).

## Round-1 findings: verification

1. BLOCKER (chmod EPERM on `/mnt/c`): **resolved.** `os.chmod` is gone from `write_token` (`src/gmail_mcp/auth.py`); `mkstemp` supplies 0600. `auth_cli.main` now wraps the flow in `except Exception` and prints only `type(exc).__name__` (no message, no traceback), so secrets in exception text cannot leak; `KeyboardInterrupt` still propagates normally. The `/mnt/` warning in `common.sh::ensure_data_dir` uses `pwd -P` in a `case`, which is safe under `set -e`. README documents that modes are not enforced on `/mnt/c`. Mutation: re-adding a chmod now fails 1 test (the new chmod-rejecting test), so the test is meaningful.
2. MAJOR (tests not pinning behaviour): **resolved.** Mutations re-run: `cte="8bit"` -> 1 failed; `compare_digest` replaced by `==` -> 7 failed; re-added chmod -> 1 failed.
3. MAJOR (budget starved newest): **resolved.** `get_thread` iterates `reversed(kept)` and reverses back; budget never goes negative (each body is at most the remaining budget). Mutation restoring oldest-first -> 1 failed.
4. MINOR (U+2028/U+0085): **resolved.** `contains_control_chars` rejects U+0085/U+2028/U+2029 for subject, addresses and queries; bodies still allow them (intentional, and stdlib body encoding handles them). Operator precedence of the `allowed` expression is correct. `clean_header` now flattens them; legitimate text is unaffected (tab is still kept).
5. MINOR (References truncation): **partially resolved, see finding 1 below.** `_limit_references` drops whole leading IDs correctly.
6. MINOR (reply subject): resolved in tool description and README.
7. MINOR (auth_cli exceptions): resolved (class name only; test uses a fake `ya29.` secret in the exception message and asserts it is not echoed).
8. MINOR (docs): resolved. The dependency-origin note is based on package metadata; I did not independently query PyPI.

## New findings

1. **MINOR - Inherited `References` can still contain a partial Message-ID.**
   `src/gmail_mcp/parsing.py:75-79` (`clean_header` caps every header at 1000 chars) feeding `src/gmail_mcp/tools.py:216-219`. Verified: a 2559-char `References` header becomes 1000 chars and its last token is cut mid-ID (probe: `last token complete: False`), and that truncated tail is then used as the base of the new `References`. `_limit_references` cannot repair it. Impact is cosmetic (threading is driven by `threadId`/`In-Reply-To`, which is intact). Fix: when reading `References` for a reply, drop an unterminated final token (or fetch with a larger cap for that header), and add a test.
2. **MINOR (observation) - the `/mnt/` warning prints on every `start`/`rebuild`/`auth`.** Intentional and harmless, but on the user's own setup it will always show; consider moving the guidance to a one-time note. No change required for approval.

## Checks re-run (real outcomes)

In a throwaway `python:3.12-slim` container with the hash-locked dev dependencies:

- `ruff check .` -> All checks passed; `ruff format --check .` -> 32 files already formatted.
- `mypy` (strict) -> Success: no issues found in 25 source files.
- `pytest` -> **229 passed**, coverage **99.03%**.
- `pip-audit --requirement requirements.txt --require-hashes --disable-pip` -> **No known vulnerabilities found** (re-run after the fixes).
- Mutations: re-added chmod, `8bit`, `==`, oldest-first budget -> each killed (see above).
- Probe of `header_map` + `_limit_references` with a long References chain (finding 1).
- Cleanup: no fake `.env`, no gmail container, temp scripts in WSL `/tmp` removed; no project file other than this report was modified.

## Could not verify

- A mutation removing the Unicode line-break rejection: my sed did not match, so that specific mutant was not exercised (the unit tests for subject/address/query were read and appear to cover U+2028/U+2029/U+0085).
- Real Google sign-in on `/mnt/c` (only the file-write step was reproduced by the implementer and in round 1), real Gmail behaviour, Goose, and execution of the PowerShell twins; I did not rebuild or start the container this round.
