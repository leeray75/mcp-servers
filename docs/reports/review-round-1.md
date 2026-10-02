# Review round 1

Verdict: **CHANGES REQUIRED**

Counts: 1 BLOCKER, 2 MAJOR, 5 MINOR.

Scope reviewed: SPEC sections 2-9 and 11, PLAN, the implementation report, all of `src/`, `tests/` (read and mutation-probed), `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`, `.env.example`, `Makefile`, every script (.sh and .ps1), and the README. Overall the implementation is strong and the security design is sound; the blocker is an environment-specific failure of the sign-in path on the user's actual repo location.

## Findings

1. **BLOCKER - `write_token` fails with `EPERM` when `./data` is on a WSL `/mnt/c` (9p/drvfs) bind mount, so sign-in cannot complete.**
   `src/gmail_mcp/auth.py:38` (`os.chmod(tmp_name, TOKEN_FILE_MODE)`). This repo lives at `C:\Users\hoyen\Documents\Roo Code Workspace\...`, i.e. `/mnt/c/...` in WSL (`mount` shows `C:\ on /mnt/c type 9p (... aname=drvfs; uid=1000 ...)`).
   Reproduced with the same container hardening (uid 10001, cap-drop ALL, read-only root) against a bind mount under `/mnt/c`: `mkstemp` ok, **`os.chmod` -> `PermissionError: [Errno 1] Operation not permitted`**, `os.replace` ok. The exception propagates out of `auth_cli.main()` (not handled), so the user completes Google consent and then gets a traceback and no token. The chmod is also redundant, since `mkstemp` already creates 0600 (a mutation removing it still passes all 208 tests). The implementer only exercised the chown helper on ext4 (report, "chown helper test"), never on the real location; `data_writable` (`test -w`) passes on drvfs so the scripts do not warn.
   Fix: do not let a chmod failure abort the write (drop the redundant chmod, or `contextlib.suppress(OSError)` around it and rely on mkstemp's 0600); add a unit test that monkeypatches `os.chmod` to raise `PermissionError` and asserts the token is still written; handle unexpected exceptions in `auth_cli.main` with a clean message. Also document in the README (security model + troubleshooting) that on `/mnt/c` POSIX modes are not enforced (any Windows process of the user can read `data/token.json`), and recommend cloning into the WSL ext4 filesystem (e.g. `~/gmail-mcp-server`) or using a Docker named volume; consider having `ensure_data_dir`/`status` warn when `./data` resolves under `/mnt/`.

2. **MAJOR - Several behaviours are not pinned by tests (tests pass with the code deliberately broken).**
   Mutations applied to a copy (with `PYTHONPATH` pointing at the copy) that were *caught*: `compare_digest` -> `==`, `HostGuardMiddleware` removed, `access_type` offline -> online, `prompt=consent` removed, annotations flipped, `In-Reply-To` dropped, `Date`/`From` summary headers dropped. Mutations that **survived all 208 tests**: removing the 0600 chmod (see finding 1; also shows the 0600 test never exercises the chmod because mkstemp supplies the mode) and switching `cte="quoted-printable"` to `8bit` in `src/gmail_mcp/tools.py:175` (no test asserts the transfer encoding of non-ASCII bodies). My attempt to also mutate the `1//` refresh-token redaction regex did not apply cleanly (sed no-op), so I make no claim there.
   SPEC section 9 requires a "timing-safe path" auth test; the test that kills the `==` mutation is incidental, and there is no explicit test that `hmac.compare_digest` is used for missing, malformed and wrong headers.
   Fix: add (a) a test that the MIME of a non-ASCII body is ASCII-safe on the wire (`Content-Transfer-Encoding` is `quoted-printable` or `base64`, and `as_bytes()` is pure ASCII), (b) a test that spies on `security.hmac.compare_digest` for missing/malformed/wrong headers, (c) the chmod-failure test from finding 1.

3. **MAJOR - Thread body budget starves the newest messages.**
   `src/gmail_mcp/tools.py:88-100`. The 5x budget is consumed oldest-first, so in a long thread (more than ~5 messages near the cap) the most recent messages, the ones an assistant usually needs in order to reply, are returned with `body == ""` and `truncated=True`. Fix: allocate the budget from the newest message backwards (iterate `reversed(kept)`, then restore chronological order), and add a test with 8 long messages asserting the last message keeps its body.

4. **MINOR - Unicode line separators pass subject validation and surface as a generic "Internal server error".**
   `src/gmail_mcp/models.py:17,37`. `contains_control_chars` covers ASCII controls only; U+2028 and U+0085 (verified) are rejected later by the stdlib SMTP policy with `ValueError: Header values may not contain linefeed or carriage return characters`, which `_instrumented` turns into the opaque "Internal server error; see the server logs." No injection occurs (the stdlib refuses it); this is a validation-quality issue. Fix: also reject `\u0085\u2028\u2029` (ideally every `str.splitlines()` boundary) in subject/address/query validators, with a test.

5. **MINOR - `References` truncation can cut a Message-ID in half.**
   `src/gmail_mcp/tools.py:208` (`chain[-MAX_REFERENCES_CHARS:]`). Slicing by characters can leave a partial leading `<id`. Fix: drop whole leading tokens until the length fits. (Note `clean_header` already caps the inherited header at 1000 chars, which drops the newest ids first.)

6. **MINOR - Reply threading depends on the caller matching the subject; the tool does not say so.**
   `src/gmail_mcp/server.py:192-194`. Gmail threads a draft by `threadId` only when the Subject matches; `create_draft` neither defaults to `Re: <original subject>` nor warns. Fix: state in the tool description and README that `subject` should be the original subject (usually `Re: ...`), or default it when `reply_to_message_id` is set.

7. **MINOR - `auth_cli` does not handle oauthlib scope-change errors or other flow exceptions.**
   `src/gmail_mcp/auth_cli.py:34-43`. If the user unticks a permission, oauthlib raises (scope mismatch) instead of returning fewer scopes, so the friendly "tick every permission box" branch (lines 53-61) is largely unreachable and the user sees a raw traceback. Fix: wrap `run_local_server` in a try/except printing a concise, secret-free message (class name only) and returning 1.

8. **MINOR - Documentation nits.**
   (a) README line 152 should say plainly that with a read-only `GMAIL_SCOPES` the `create_draft` tool is still listed but will fail (deviation 9). (b) `requirements.txt` includes `httpx2`/`httpcore2`/`mcp-types`, pulled in by `mcp==2.2.0`; I could not independently verify these are the genuine upstream dependency names (see "Could not verify"); add a one-line note that the transitive set was reviewed.

## SPEC conformance summary

- Section 2 layout: matches; extra `errors.py`, `parsing.py`, `Makefile`, `.gitattributes`, `requirements*.txt` are justified and documented.
- Section 3: single pydantic-settings class, the only module reading the environment (grep confirms), secrets are `SecretStr`, errors rebuilt from field names only, `.env` git/docker-ignored, `.env.example` placeholders only. `git grep` over all history found no `GOCSPX-`/`ya29.` values other than test fakes.
- Section 4.1: constant-time compare always executed, uniform 401 JSON with `WWW-Authenticate: Bearer`, `/healthz` unauthenticated and credential-agnostic, Host guard (421) and Origin guard (403) run before auth. The port-agnostic Host check is a documented, justified deviation (the container sees the published port); the hostname allow-list is the actual defence. SDK-level DNS-rebinding protection is also on.
- Section 4.2: missing/invalid/revoked token -> `AuthRequiredError` -> actionable tool error, process stays up, service rebuilt lazily. Atomic write (same-dir mkstemp, fsync, replace) is correct apart from finding 1. Token never logged. Scopes validated at startup and against the stored token.
- Section 4.3: PKCE (library default, tested), offline, consent, redirect `http://127.0.0.1:8400/`, bind 0.0.0.0 in container, published only to 127.0.0.1 by `auth.sh`, URL printed, only scopes printed. Correct apart from findings 1 and 7.
- Section 5: exactly six tools with correct annotations. A grep of `src/` for `.send(`, `.delete(`, `.trash`, `.modify`, `.batch`, `.settings`, `.attachments(`, `.insert(`, `.import_` returns nothing; the only Gmail calls are threads.list/get, messages.get, labels.list, drafts.list/get/create. Attachment `body.data` is never decoded (`extract_text_body` skips parts with a filename). ID regex, 50-recipient and 100 KB limits, CR/LF rejection verified; MIME built by the stdlib SMTP policy (non-ASCII subject becomes RFC 2047, body quoted-printable utf-8, verified by running `build_mime`). Untrusted-content note present in descriptions and server instructions.
- Section 6: JSON logs, fixed extra-field allow-list, exception class name only, access log off, literal-secret plus pattern redaction; client secret and MCP token registered in `create_app`.
- Section 7: Dockerfile multi-stage, hash-checked `--only-binary` install, UID 10001, no build tools, `init: true`; compose has loopback-only publish, `read_only`, tmpfs, `cap_drop: ALL`, `no-new-privileges`, limits, log rotation, healthcheck, `auth` profile, no docker socket, no privileged. `.dockerignore` excludes `.env*`, `data/`, `.git`, caches, tests. Digest pinning is documented, not enforced (as the SPEC allows).
- Section 8: all seven scripts as .sh with `set -euo pipefail`, own-location root resolution, `.env` parsed as data (never sourced), names-only warnings, executable bit set in git (7 x 100755), ps1 twins via a shared wrapper using `wslpath` and forwarding args and exit code.
- Section 9: ruff, format, mypy --strict, pytest and coverage pass (below). `pip-audit` documented plus `make audit`.
- Section 11 checklist: satisfied except sign-in on `/mnt/c` (finding 1) and the items that need real Google.
- `uri` vs `url` for Goose: README example, `common.sh` hint and the implementation report are mutually consistent, and `uri` matches my recollection of Goose's `streamable_http` extension schema (`uri`, `headers`, `timeout`); I could not run Goose or fetch its docs, so treat it as consistent and plausible, not proven. The deviation from SPEC section 9 is documented.

## Checks I re-ran (real outcomes)

All in a throwaway `python:3.12-slim` container (copy of the repo, hash-locked `requirements-dev.txt`, fresh venv):

- `ruff check .` -> All checks passed.
- `ruff format --check .` -> 30 files already formatted.
- `mypy` (strict) -> Success: no issues found in 25 source files.
- `pytest` -> **208 passed**, coverage **99.02%** (gate 85%), including the 0600 mode test (ran on Linux).
- `build_mime` with non-ASCII subject/body and reply headers: correct RFC 2047 subject, quoted-printable utf-8 body, `In-Reply-To`/`References` present.
- Validator probes: U+2028/U+0085 subject passes model validation then fails in the stdlib (finding 4); `"a b"@x.com`, `a@localhost`, `Bob <a@b.com>`, IP-literal and full-width/IDN addresses are rejected.
- 10 targeted source mutations against the test suite (results under finding 2).
- On the real `/mnt/c` 9p mount with the container's hardening flags: `mkstemp` ok, `chmod` EPERM, `replace` ok (finding 1).
- Cleanup: no fake `.env` created, no gmail container exists (`docker ps -a`), temp files in WSL `/tmp` and the test directory under `%LOCALAPPDATA%\Temp` removed. No project file other than this report was modified.

## Could not verify

- Behaviour against real Google (OAuth round trip, refresh, real Gmail JSON shapes) and Goose itself.
- Image build and container hardening claims (non-root, read-only FS, port binding, 401/421 behaviour): I did not rebuild or start the container; I relied on the implementer's report plus reading the Dockerfile and compose file.
- The PowerShell twins (read, not executed), the full idempotence matrix of the scripts, and whether `httpx2`/`httpcore2`/`mcp-types` are the genuine upstream dependencies of `mcp==2.2.0` (hash-locked, reportedly pip-audit clean, but I did not query PyPI).
