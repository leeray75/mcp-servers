# Summary Report — HTTPS for the OAuth redirect

**Feature:** TLS termination, local CA, and the `https` OAuth Redirect URL
**Spec:** `docs/specs/tls-oauth-redirect/SPEC.md` — Status: Approved (human, 2026-10-30 10:12 -04:00); amended with human authorization 2026-10-30 10:40 -04:00 (T-D10, T-D11)
**Reports:** `docs/reports/tls-oauth-redirect/review-round-1.md`, `docs/reports/tls-oauth-redirect/fix-round-1.md`
**Date:** 2026-10-30 16:11 -04:00
**Requested by the human:** stop the reviewer and produce this report now.

---

## 1. Bottom line

**Result: BUILT, GATE-GREEN, AND ROUND-1 FINDINGS FIXED — but NOT independently re-reviewed.**

The feature is implemented and the whole quality gate passes: ruff clean, ruff format clean, mypy strict
clean over 50 files, **990 tests passed, 90.96% branch coverage** (gate 90%).

The review loop was cut short at the human's explicit instruction. Round 1 ran and returned
**CHANGES REQUESTED (0 blockers, 4 majors, 5 minors)**. All four majors and four of the five minors were
then fixed and each fix was **re-mutated by the implementer** to prove the new test kills the original
defect. Round 2 — the independent read-only re-review of those fixes — was delegated
(subagent `20261030_13`) and **cancelled on the human's order before it wrote any report**. So the
round-1 fixes rest on implementer-run verification only; no second pair of eyes has confirmed them.
That is the single material gap in this record.

The operator-facing path (generate a certificate, trust the CA, restart the container, paste the URI into
the Twitch console, sign in in a browser) has **never been executed on a real machine** — it needs the
human's Twitch application and trust store. See §6.

## 2. Process

| # | Step | Actor | Output | Result |
| --- | --- | --- | --- | --- |
| 1 | Diagnose the console rejection, options put to the human | orchestrator | chat | Console enforces HTTPS; TLS inside the existing container chosen over a sidecar |
| 2 | Spec authored | orchestrator | `docs/specs/tls-oauth-redirect/SPEC.md` | T1-T12, T-D1..T-D9 |
| 3 | **Approval gate** | human | spec header | **Approve**, 2026-10-30 10:12 |
| 4 | Implement | implementer | `config.py`, `__main__.py`, `security.py`, `server.py`, compose/Dockerfile, `scripts/`, `README.md`, `.env.example`, `.gitignore`, `tests/test_tls.py` | Gate green at 982 / 90.99% |
| 5 | Blocker: `0600` key mode impossible on the operator's WSL drive — stopped, escalated | orchestrator → human | chat | Human chose mode-aware enforcement + macOS docs, "do not force macOS tests on Windows" |
| 6 | Spec amendment (T-D10, T-D11), re-approved | human | spec header | Approved 2026-10-30 10:40 |
| 7 | Review round 1 (read-only subagent `20261030_12`) | reviewer | `review-round-1.md` | **CHANGES REQUESTED** — 0 blockers, 4 majors, 5 minors; 64 mutations, 60 killed |
| 8 | Fix round 1 | implementer | `fix-round-1.md` | 4/4 majors + 4/5 minors fixed, each re-mutated; gate green at 990 / 90.96% |
| 9 | Review round 2 (read-only subagent `20261030_13`) | reviewer | — | **CANCELLED on the human's instruction** — no report written |
| 10 | This summary, post-stop gate re-verified | orchestrator | this file | ruff / format / mypy clean, 990 passed, 90.96% |

Review rounds used: **1 of 5 completed.** No commit, push, tag or release was made (none was requested).

## 3. What was built, against the requirements

| Req | What it means | Where | Covering tests in `tests/test_tls.py` |
| --- | --- | --- | --- |
| T1 | Both TLS variables or neither; one alone aborts startup naming the field, never the value | `config.py` `_check_tls`, `_check_tls_paths_are_trimmed` | `…plain_http_server_of_before`, `…one_tls_variable_alone_stops_startup_and_names_the_missing_one[cert-without-key, key-without-cert]`, `…blank_tls_variables_are_the_same_as_unset` |
| T2 | With neither set, behaviour is byte-identical to before | `config.py`, `server.py`, `docker-compose.yml` | `…plain_http_server_of_before`, `…healthz_reports_tls_off_when_no_certificate_is_configured`, `…the_same_server_serves_plaintext_when_tls_is_off`, `…the_tls_override_mounts_the_folder_read_only_and_nothing_else` |
| T3 | With both, uvicorn serves TLS on the same port and both URLs become `https` | `__main__.py`, `config.py` `scheme`/`redirect_uri`/`sign_in_url` | `…tls_material_derives_https_urls_from_the_host_port`, `…uvicorn_gets_the_certificate_pair_or_explicit_nones[tls-off, tls-on]` |
| T4 | Refuses missing / unreadable / non-PEM / mismatched / non-`0600` material, secret-free | `config.py` `_check_tls`, `_modes_are_expressible` | `…a_missing_certificate_file_refuses_startup_without_reciting_the_path`, `…a_key_that_is_not_owner_only_is_refused`, `…a_key_mode_is_enforced_only_where_the_filesystem_can_express_it`, `…an_unwritable_folder_alone_does_not_excuse_a_world_readable_key`, `…the_mode_probe_reports_the_filesystem_rather_than_the_platform`, `…a_skipped_key_mode_rule_says_so_without_naming_the_key`, `…material_that_is_not_a_certificate_is_refused`, `…an_unreadable_certificate_is_refused`, `…a_key_that_does_not_match_its_certificate_is_refused` |
| T5 | No key inside the repo tree except the git-ignored `certs/` | `config.py` `_check_tls_location`, `.gitignore` | `…a_key_inside_the_repository_is_refused_except_in_the_certs_directory[in-the-tree, next-to-the-code, in-the-ignored-certs-dir]`, `…a_path_outside_the_repository_is_always_allowed` |
| T6 | `/healthz` unauthenticated with the new `tls` flag; every other route still needs the bearer | `server.py` | `…healthz_carries_the_tls_flag_and_never_needs_a_bearer`, `…healthz_reports_tls_off_when_no_certificate_is_configured`, `…tls_does_not_relax_bearer_auth_on_any_route`, `…json_serialisation_of_the_health_body_is_still_small` |
| T7 | Host/Origin guard, bearer auth and the single-httpx-module rule unchanged | `security.py` (unchanged `_origin_allowed`), `network.py` (untouched) | `…tls_does_not_relax_bearer_auth_on_any_route`, `…an_https_origin_is_accepted_whether_or_not_tls_is_on[tls-on, tls-off]` |
| T8 | Cleartext to a TLS server is a 400, never a 3xx | `security.py` `PlaintextRefusalMiddleware` | `…a_plaintext_request_to_a_tls_server_is_refused_and_never_redirected`, `…the_same_server_serves_plaintext_when_tls_is_off` |
| T9 | No certificate bytes, key bytes or key path in any log, error or `/healthz` | `config.py`, `server.py`, `security.py` | `…no_certificate_detail_reaches_the_log_or_the_health_response`, `…a_skipped_key_mode_rule_says_so_without_naming_the_key`, `…json_serialisation_of_the_health_body_is_still_small` |
| T10 | `make-cert.sh` (+ PowerShell twin) builds CA and leaf, prints only the CA path and fingerprint, never key material | `scripts/make-cert.sh`, `scripts/make-cert.ps1` | `…the_certificate_script_builds_the_documented_identity_and_quiets_the_key`, `…running_the_certificate_script_never_shows_the_key_and_names_the_https_uri`, `…the_powershell_twin_trusts_the_ca_for_the_current_user_only`, `…every_shell_script_in_the_repository_still_parses` |
| T11 | `./certs:/certs:ro` only when TLS is configured; rootfs still read-only; `/certs` never writable | `docker-compose.tls.yml`, `scripts/lib/common.sh` | `…the_tls_override_mounts_the_folder_read_only_and_nothing_else`, `…the_tls_override_also_tells_the_container_where_the_material_is` |
| T12 | README states the exact URI to register and the trust steps | `README.md` §2.1, §3, troubleshooting | `…the_readme_gives_the_uri_to_register_and_the_trust_command` |
| T-D8 | Container health probes follow the TLS mode, verification off for the self-probe only | `Dockerfile`, `docker-compose.yml`, `scripts/smoke.sh`, `scripts/status.sh` | `…the_shipped_healthcheck_probes_the_scheme_the_container_is_serving[no-tls, blank-tls-var, tls-on]`, `…the_shipped_healthcheck_exits_nonzero_when_healthy_is_a_lie` |
| — | The map itself: no T requirement without a test, and no test citing a T the spec dropped | — | `…every_tls_requirement_is_named_by_a_test_in_this_module` (both directions, T1-T12 pinned) |

41 tests in this module; the suite as a whole is 990.

## 4. Round-1 findings and their disposition

| Finding | Severity | Disposition | Independent? |
| --- | --- | --- | --- |
| Mode check failed open on any `OSError` | major | **Fixed.** `_modes_are_expressible` requires both halves of the mode-less signature (probe unwritable **and** every inode `0777`) before skipping | Implementer only |
| Nothing pinned the container-side `TWITCH_TLS_*` defaults | major | **Fixed**, and pinned against the mount target and against what `make-cert.sh` tells the operator to write | Implementer only |
| "Never prints key material" bypassable by `cat "${LEAF_KEY}"` | major | **Fixed** twice: a quote-aware static scanner plus a test that executes the script against a stub `openssl` | Implementer only |
| `make-cert.sh` printed an `http` redirect URI | major | **Fixed**: step 4 prints `https://127.0.0.1:$(host_port)/oauth/callback` literally; the port is still derived | Implementer only |
| The `0600` skip was silent | minor | **Fixed**: one warning naming the variable and the filesystem, never the path | Implementer only |
| T-map could not see a removed requirement | minor | **Fixed**: expected set pinned, both directions checked | Implementer only |
| Healthcheck never executed | minor | **Fixed**: extracted from both files, asserted identical, executed with only `urlopen` replaced | Implementer only |
| `shellcheck` / `pwsh` unavailable | minor | **Partly mitigated**: `bash -n` over every `scripts/**/*.sh` is now a test. Real linting is a **human follow-up** (§6) | — |
| README "applied automatically" overstated | minor | **Fixed**: names `common.sh compose()` and gives the explicit two-`-f` command | Implementer only |

Mutations re-run by the implementer after the fixes — all four original defects were killed:

| Mutation | Killed by |
| --- | --- |
| delete both `environment:` defaults from `docker-compose.tls.yml` | `…the_tls_override_also_tells_the_container_where_the_material_is` |
| insert `cat "${LEAF_KEY}"` into `make-cert.sh` | both the static scanner test and the executed-script test |
| revert step 4 to `$(redirect_uri)` | `…running_the_certificate_script_never_shows_the_key_and_names_the_https_uri` |
| `0644` key in a `0555` folder on ext4 | `load_settings()` refuses with the `0600` message (probe returns `True`); pinned by `…an_unwritable_folder_alone_does_not_excuse_a_world_readable_key` |

Every mutation was applied to a throwaway copy and reverted, and the two touched files were confirmed
back to their intended content.

## 5. Verification

```
ruff check src tests tools            All checks passed!
ruff format --check src tests tools   51 files already formatted
mypy (strict, src + tests)            Success: no issues found in 50 source files
pytest -q                             990 passed, 90.96% branch coverage (gate 90%)
```
Run 2026-10-30 16:1x -04:00 under WSL with `/home/leeray/venvs/twitch/bin/python`, after the reviewer was
stopped and after the working tree was checked for leftover reviewer mutations (none found:
`docs/reports/tls-oauth-redirect/` holds only `review-round-1.md` and `fix-round-1.md`).

Round 1 (before the fixes) additionally proved, by a reviewer with fresh context: middleware order is
`PlaintextRefusal → HostGuard → Bearer` empirically, a real uvicorn TLS listener boots via `main()` and
answers 200 with the bearer and 401 without, cleartext dies before the application, `docker compose
config` mounts nothing in the base file and `/certs` `read_only: true` under the override, and no route
ever answers a 3xx.

## 6. UNVERIFIED — needs the human

| Item | Why it could not be verified here | What to run |
| --- | --- | --- |
| Twitch console accepts `https://127.0.0.1:8770/oauth/callback` and the browser completes sign-in | Needs the human's Twitch application and browser trust store | `scripts/make-cert.sh` → trust the CA (`scripts/make-cert.ps1`, or `certutil -addstore -user Root certs\ca.crt` / `security add-trusted-cert -k ~/Library/Keychains/login.keychain-db certs/ca.crt`) → add `TWITCH_TLS_CERT_FILE=/certs/server.crt` and `TWITCH_TLS_KEY_FILE=/certs/server.key` to `.env` → `scripts/restart.sh --recreate` → `scripts/status.sh` → register the printed URI → `connect_twitch_account` |
| `make-cert.sh` output on the operator's own drive | Only run in throwaway copies against a stub `openssl` | Run it once for real and read the next-steps block; step 4 must be `https` |
| `shellcheck` over the shell scripts | `shellcheck` is not installed on this host | `shellcheck scripts/*.sh scripts/lib/*.sh` |
| `make-cert.ps1` beyond read-only review | `pwsh` is not installed on this host | `pwsh -File scripts/make-cert.ps1` (or PSScriptAnalyzer) |
| macOS trust path | By the human's explicit instruction, macOS is documentation-only and is not tested from Windows | `security add-trusted-cert …` on a Mac, once |
| Independent re-review of the round-1 fixes | Round 2 was cancelled on request | Re-run the read-only reviewer whenever you want the fixes confirmed by a second party |

## 7. Deviations and accepted issues

* **No deviation from the approved spec.** No requirement or design element was changed without a second
  human approval; the only two amendments (T-D10, T-D11) were authorised in conversation before the
  affected code was written.
* **Accepted by design, on the human's instruction:** macOS support is documentation and script output
  only, untested on this host.
* **Accepted by the reviewer in round 1:** the T-D10 fail-open — enforced wherever a filesystem can
  express a Unix mode, skipped only after the probe proves it cannot. It guards against operator error,
  not against an adversary who already owns the key directory, and fail-closed would have made TLS
  unreachable both in the container's read-only `/certs` and on the operator's own Windows drive.
* **Known gap, recorded:** the `shellcheck`/`pwsh` lint pass is not part of the gate on this host.

## 8. Standing state

Nothing was committed, pushed, tagged or released. `twitch-mcp-server/` remains untracked in git, so the
human reviews the whole tree before any commit. Values from `.env` were never read or printed.
