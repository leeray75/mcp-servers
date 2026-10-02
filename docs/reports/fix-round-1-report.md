# Fix round 1 report

All eight findings were accepted and fixed; none were skipped. No real credentials were used, Google was not contacted,
nothing was pushed (local commit only).

## Per finding

1. **BLOCKER - `os.chmod` EPERM on `/mnt/c`.** `src/gmail_mcp/auth.py`: removed the redundant `os.chmod` (the temp file from
   `mkstemp` is already 0600; the docstring now says why there is no chmod). `auth_cli.main` now wraps the flow and token write
   (see 7). Tests: `test_write_token_survives_filesystems_that_reject_chmod` (patches `os.chmod`/`os.fchmod` to raise
   `PermissionError`; token still written). Docs: README security model and troubleshooting now state that POSIX modes are not
   enforced on `/mnt/c` and recommend cloning into the WSL filesystem; `scripts/lib/common.sh::ensure_data_dir` prints a warning when
   `./data` resolves under `/mnt/` (seen live in `rebuild.sh`). **Real repro of the fix:** a container with the production flags
   (UID 10001, `--read-only`, `--cap-drop ALL`, `no-new-privileges`) bind-mounting this repo's `data/` on `/mnt/c` ran
   `write_token` -> `write ok`. (I did not separately re-create the pre-fix EPERM; I rely on the reviewer's reproduction.)
2. **MAJOR - surviving mutants / timing-safe test.** New tests: `test_non_ascii_body_is_ascii_safe_on_the_wire` (CTE is
   quoted-printable/base64, `as_bytes()` decodes as ASCII, content round-trips); `test_every_auth_decision_goes_through_hmac_compare_digest`
   (parametrized missing/empty/`Bearer`/wrong/Basic/valid: exactly one `compare_digest` call per request and the result matches the
   outcome); chmod test as in 1. **Mutation checks I re-ran:** `cte="8bit"` -> 2 tests fail; `compare_digest` -> `==` -> 2 new tests fail;
   oldest-first budget restored -> 2 tests fail. All three mutants are now killed; sources restored afterwards.
3. **MAJOR - budget starved newest messages.** `tools.get_thread` now iterates `reversed(kept)` and restores chronological order.
   Test `test_thread_body_budget_favours_the_newest_messages` (8 long messages, order chronological, last 5 full, first 3 empty).
4. **MINOR - U+2028/U+0085.** `models.contains_control_chars` now also rejects U+0085, U+2028, U+2029 (bodies may still contain
   them; other controls remain rejected). Tests cover subject, address and query with U+2028/U+2029/U+0085/VT/FF/FS and the body
   exemption. The new test also exposed a gap in `parsing.clean_header` (U+0085 was not flattened); fixed by reusing
   `contains_control_chars`. Side effect: tabs in returned headers are now kept rather than replaced by a space (harmless).
5. **MINOR - References truncation.** `tools._limit_references` drops whole leading Message-IDs; tests assert the result is a suffix
   of complete IDs under the 2000-char limit, and that short chains are untouched.
6. **MINOR - reply subject.** `create_draft` tool description and README troubleshooting now say Gmail only groups a draft into the
   conversation if the subject matches, so pass the original subject (usually `Re: ...`). I did not default the subject automatically.
7. **MINOR - auth_cli exceptions.** Flow/token-write exceptions now print `Sign-in failed (<ClassName>)` plus the "tick every box"
   hint, exit 1, and never the exception message. Tests: flow raising a `Warning` containing a fake `ya29.SECRET` (not echoed), and
   `write_token` raising `OSError`. A `Sign-in failed` row was added to the README troubleshooting table.
8. **MINOR - docs.** (a) README now says plainly that with read-only `GMAIL_SCOPES` `create_draft` is still listed but every call
   fails with 403. (b) I verified the dependency names from the installed `mcp` metadata: `mcp==2.2.0` declares `httpx2>=2.5.0` and
   `mcp-types==2.2.0`; `httpx2`/`httpcore2` list `github.com/pydantic/httpx2` as home page/source (author Tom Christie). Noted in the
   README. I could not query PyPI itself; `pip-audit` was clean in round 0 (not re-run this round).

## Commands and real outcomes

| Command | Outcome |
|---|---|
| `pytest` (Windows, Python 3.13) | 228 passed, 1 skipped (POSIX mode test), coverage 99.03% |
| `pytest` (python:3.12-slim container, hash-locked dev deps, full current suite) | **229 passed, 0 skipped**, coverage 99.03% |
| `ruff check .` / `ruff format --check .` (Linux) | All checks passed / 31 files already formatted |
| `mypy` strict (Windows and Linux) | Success: no issues found in 25 source files |
| `scripts/rebuild.sh` (no cache, fake env) | exit 0, healthy; printed the new `/mnt/` warning |
| Container checks (fake env) | `user=10001`, read-only root, `CapDrop=[ALL]`, port `127.0.0.1:18765`; `/healthz` 200; `/mcp` without token 401; `Host: evil.com` 421 |
| `status.sh`, `stop.sh` | ran; container and network removed; fake `.env` deleted; no leftover `gmail-mcp` container |

## Not verified

- Real Google sign-in on `/mnt/c` (only the file-write step was reproduced in a container).
- The Linux container run was done on a copy outside the repo; the `__pycache__` files it created (root-owned) were removed with a root container.
