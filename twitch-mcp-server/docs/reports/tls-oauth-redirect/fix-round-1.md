# Fix Round 1 — TLS / OAuth redirect

**Feature:** HTTPS for the OAuth redirect (`docs/specs/tls-oauth-redirect/SPEC.md`, T1-T12)
**Responds to:** `docs/reports/tls-oauth-redirect/review-round-1.md` (0 blockers, 4 majors, 5 minors)
**Actor:** implementer
**Date:** 2026-10-30

Every major is fixed and each fix was re-mutated to confirm the new test actually kills the original
defect. Four minors are fixed; one is environment-bound and is recorded as a human follow-up.

---

## Majors

### MAJOR 1 — the T-D10 mode check failed open on any `OSError`

`src/twitch_mcp/config.py`. `_filesystem_carries_unix_modes` returned `False` ("skip the rule") for any
`OSError` from the probe write, so the reviewer's `0644` key in a `chmod 0555` folder on real ext4
booted.

Replaced by `_modes_are_expressible(directory)`, which separates the three situations the old function
collapsed:

| probe outcome | directory signature | decision |
| --- | --- | --- |
| written and holds `0600` | — | modes are real → **enforce** |
| written and does not hold it | — | filesystem records no modes → **skip** (the authorised T-D10 case) |
| cannot be written, cannot be chmod-ed | every inode `0777` | mode-less mount (the container's read-only `/certs`, a drvfs/NTFS drive) → **skip** |
| cannot be written, cannot be chmod-ed | anything else | no conclusion → **enforce** |

Only a mount that carries both halves of the mode-less signature is exempted, so a hand-`chmod`-ed key
in a locked-down folder can no longer be waved through because the probe happened to be unwriteable.

Verification:

* Reviewer's reproduction re-run outside pytest on `/tmp` (ext4): folder mode `555`, key mode `644` →
  `_modes_are_expressible` now returns `True` (was `False`), so `load_settings()` refuses with the
  `0600` message.
* New test `test_an_unwritable_folder_alone_does_not_excuse_a_world_readable_key` pins it through
  `load_settings()`, and asserts its own premise first (`pytest.raises(OSError)` on the probe write,
  and the folder is not `0777`) so a root-run pass cannot be vacuous. The `chmod 0o500` trap the
  reviewer warned about is exactly what the premise assertion guards.
* `test_the_mode_probe_reports_the_filesystem_rather_than_the_platform` now covers all three outcomes,
  including the exempt one: an unwritable folder whose inodes report `0777` (simulated by reporting a
  genuinely world-writable inode's stat in place of the folder's).

### MAJOR 2 — nothing pinned the container-side `TWITCH_TLS_*` defaults

New test `test_the_tls_override_also_tells_the_container_where_the_material_is` in `tests/test_tls.py`.
It parses the mount target out of `docker-compose.tls.yml` and requires each variable's default to be
`<mounted-at>/<file>` — so the mount and the two paths are one decision rather than three lines that
can drift — and it also requires `make-cert.sh` step 2 to print exactly those two `.env` lines.

Re-mutated: deleting the two `environment:` lines now fails the suite
(`TWITCH_TLS_CERT_FILE must default to the file inside the mounted folder`), where before it left 993
tests green.

### MAJOR 3 — "never prints key material" was bypassable

Two changes, both in `tests/test_tls.py`:

1. `_lines_that_could_print_a_key(script)` replaces the `^\s*cat\s+.*\.key` regex. It blanks
   single-quoted literals first (so the legitimate `printf '... TWITCH_TLS_KEY_FILE=/certs/server.key'`
   instruction stays), then flags any line that names a key path or key variable *and* contains a
   byte-reading command (`cat`, `head`, `tail`, `tee`, `strings`, `less`, `more`, `od`, `xxd`,
   `base64`, `Get-Content`, `ReadAllText`) or `openssl pkey|rsa|ec … -text`. Applied to
   `make-cert.sh` **and** `make-cert.ps1`.
2. `test_running_the_certificate_script_never_shows_the_key_and_names_the_https_uri` runs the script
   for real, in a copy of the tree, against a stub `openssl` that writes recognisable key bytes.
   Assertions are on what the operator would see.

Re-mutated: inserting `cat "${LEAF_KEY}"` before the `chmod 600` line fails **both** the static test and
the executed test (`FAKE-PRIVATE-KEY-MATERIAL-4c1f is contained here: …`).

### MAJOR 4 — `make-cert.sh` told the operator to register a plain-`http` URI

`scripts/make-cert.sh` step 4 now prints `https://127.0.0.1:$(host_port)/oauth/callback` literally. The
port is still read from `.env`; only the scheme is fixed, because this script is the step *before* `.env`
carries the TLS variables — `redirect_uri()` would necessarily still say `http` at that moment. A comment
records why this one URI is not derived, and `scripts/status.sh` remains the derived answer afterwards.

Re-mutated: restoring `$(redirect_uri)` fails the executed test with
`assert 'https://127.0.0.1:8771/oauth/callback' in '… http://127.0.0.1:8771/oauth/callback …'`.

---

## Minors

| # | Finding | Disposition |
| --- | --- | --- |
| 1 | The skip was silent and `False` was overloaded across three conditions | **Fixed.** `_modes_are_expressible`'s docstring names the three outcomes and which may skip; `_check_tls` now emits one `logger.warning` when the rule stands down, naming the variable and the filesystem and never the key's location (T9). Pinned by `test_a_skipped_key_mode_rule_says_so_without_naming_the_key`, which asserts the reason is attributable **and** that neither path appears in any captured record. |
| 2 | The T-map sweep could not see a removed requirement | **Fixed.** `EXPECTED_TLS_REQUIREMENTS` pins T1-T12 as a literal set, and the sweep now checks both directions: spec-vs-suite and cited-vs-spec, mirroring `test_the_citation_map_is_not_stale`. |
| 3 | The adaptive healthcheck was never executed | **Fixed.** `_health_probe_snippets()` extracts the `python -c` body from `Dockerfile` and `docker-compose.yml`, asserts the two are identical, and runs each one with only `urllib.request.urlopen` replaced. Covered: no TLS → `http://127.0.0.1:8000/healthz` with no context; blank variable → same; TLS on → `https://…` with the unverified context; 503 → exit 1. |
| 4 | `shellcheck` and `pwsh` unavailable | **Partly mitigated, human follow-up.** Added `test_every_shell_script_in_the_repository_still_parses`, which runs `bash -n` over every `scripts/**/*.sh`. `shellcheck` is not made a prerequisite, and the PowerShell twins still get read-only review only — running them needs `pwsh`, which is not installed on this host. **For the human:** run `shellcheck scripts/*.sh scripts/lib/*.sh` and `pwsh -NoProfile -Command "& { . ./scripts/make-cert.ps1 }"` (or PSScriptAnalyzer) once on a machine that has them. |
| 5 | README "applied automatically" overstated the override | **Fixed.** §2.1 now says the `compose` helper in `scripts/lib/common.sh` layers `docker-compose.tls.yml` for every `scripts/*.sh` command, and gives the explicit two-`-f` form for a bare `docker compose up`. |

---

## Files changed this round

| File | Change |
| --- | --- |
| `src/twitch_mcp/config.py` | `_modes_are_expressible` (bounded fail-open), `MODE_PROBE_NAME`, `SYNTHETIC_MODE`, module logger, warning on a skipped rule |
| `scripts/make-cert.sh` | step 4 prints the `https` registration URI |
| `README.md` | §2.1 override wording |
| `tests/test_tls.py` | 6 new tests, 2 strengthened, executed healthcheck and executed `make-cert.sh`, both-direction T-map |

No production behaviour outside the spec's scope changed: TLS is still opt-in, still one container, still
loopback-only, and the plaintext refusal is untouched.

## Gate

```
ruff check  src tests tools   All checks passed!
ruff format --check           51 files already formatted
mypy (strict, src + tests)    Success: no issues found in 50 source files
pytest                        990 passed, coverage 90.96% (gate 90%)
```

## Mutations re-run this round

| Mutation | Result |
| --- | --- |
| delete both `environment:` defaults from `docker-compose.tls.yml` | **killed** (MAJOR 2) |
| insert `cat "${LEAF_KEY}"` into `make-cert.sh` | **killed** twice (MAJOR 3) |
| revert step 4 to `$(redirect_uri)` | **killed** (MAJOR 4) |
| `0644` key in a `0555` folder on ext4 | **killed** — refusal restored (MAJOR 1) |

All mutations were applied outside pytest on throwaway copies and reverted; `docker-compose.tls.yml` and
`scripts/make-cert.sh` were verified back to their intended content afterwards.
