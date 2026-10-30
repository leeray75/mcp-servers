# Phase 5 Review — Round 1

**Feature:** HTTPS for the OAuth redirect (TLS termination)
**Spec:** `docs/specs/tls-oauth-redirect/SPEC.md` (Status: Approved by human 2026-10-30 10:12; amended with the human's authorization 2026-10-30 10:40, adding **T-D10** to T4/T-D7 and **T-D11**)
**Parent spec:** `docs/SPEC.md` (R1–R57, Amendments 1 and 2; A2-D4 caps review at 5 rounds; A1-D5/A2-D5 leave live/operator-run checks UNVERIFIED)
**Round:** 1 of 5
**Date:** 2026-10-30
**Reviewer:** read-only reviewer subagent `20261030_12` (distinct from the implementer; the implementer's report was not read before the findings were formed)

**Verdict: CHANGES REQUESTED** — 0 blockers, 4 majors, 5 minors.

**How this review was performed.** Every finding below came from reading the code from disk and running things. I
edited no file: at the end of the review all twelve protected files are **byte-identical** to the copies taken before
I started, verified with `sha1sum` (e.g. `src/twitch_mcp/config.py` = `18a58fce…`, `docs/specs/tls-oauth-redirect/SPEC.md`
= `7211e731…`). Temporary probe files I created (`tests/test_zz_*`, `tools/_rv_*`) and any `./certs` or `.env` I generated
were all removed; `git status --porcelain` shows only the pre-existing entries.

The core of the method was **mutation testing**: 64 targeted mutations were applied one at a time to the feature's
source, docs and scripts, with the test suite run after each. A mutation that *survived* (tests stayed green while the
behaviour was broken) is evidence of a **vacuous test** — the failure mode the previous feature was caught in. Every
survivor is reported as a finding; every kill is reported as evidence that the corresponding requirement is genuinely
pinned. Mutations were always restored in a `finally` block and re-verified against the pristine copy.

Commands run (from `/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/twitch-mcp-server`, tools from
`/home/leeray/venvs/twitch/bin`):

- `ruff check .` · `ruff format --check .` · `mypy --strict src tests` · `pytest -q` (full suite, coverage gate on)
- `pytest tests/test_tls.py tests/test_server.py` (per mutation, 64×)
- `bash -n` over all ten `scripts/*.sh` + `scripts/lib/common.sh`; `shellcheck` (not installed); `pwsh` (not installed)
- `scripts/make-cert.sh` executed in a scratch repo copy, incl. `--force`; `openssl x509 -noout -subject -dates -ext …`; `openssl verify`; `ssl.SSLContext.load_cert_chain`
- `docker compose -f … config` for base, base+tls, base+oauth+tls
- a real `uvicorn` TLS listener booted via `twitch_mcp.__main__.main()`, then attacked over raw sockets (TLS and cleartext)

---

## 1. Gate results (probe 9) — run myself, verbatim

| Gate | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `64 files already formatted` |
| `mypy --strict src tests` | `Success: no issues found in 50 source files` |
| `pytest -q` | **`982 passed in 63.46s`** |
| branch-coverage gate (R56, ≥90%) | `Required test coverage of 90% reached. Total coverage: 90.99%` |

Per-module coverage of the touched files: `config.py` 95% (missing 123–125, 127, 222, 287, 289, 367), `security.py` 99%
(83–84), `server.py` 91%, `__main__.py` 100%.

**Acceptance H1 is met** (`make check` equivalent is green). **H2 is met**: the only amendments to the pre-existing
suite are the three `/healthz` key-set assertions in `tests/test_server.py`, which T6 itself requires, and they bite —
adding a sixth key to `/healthz` fails five separate tests, including
`test_healthz_reports_exactly_five_keys_without_credentials` and its `json.dumps(body).count(":") == 5` guard.
**H4 is met** (see T3). **H3** was verified by me against a real listener (probe 1/2 below), which is more than the
spec's "operator-run, UNVERIFIED" bar requires. **H5** (live browser sign-in, no certificate warning) remains
**UNVERIFIED per A1-D5/A2-D5** — it needs the operator's browser and Twitch console; I did not and cannot run it.

---

## 2. Requirement-by-requirement table

"Tested by" names the test in `tests/test_tls.py` unless noted. "Bites?" is the mutation-testing result: I broke the
implementation and asked whether the named test noticed.

| T | Requirement (abridged) | Implemented? | Tested by | Bites? | Verdict |
|---|---|---|---|---|---|
| **T1** | Both vars read by `config.py` only; both-or-neither; names the field, never the value | `config.py:292-296`, `347-363` | `test_one_tls_variable_alone_stops_startup_and_names_the_missing_one`, `test_blank_tls_variables_are_the_same_as_unset`, `test_the_server_without_tls_configuration_is_the_plain_http_server_of_before` | ✅ M7 killed; also caught by `config.py:358-363` read | **PASS** |
| **T2** | Neither set → behaviour byte-identical to today | `config.py:358-359`; refusal middleware inert (`security.py:131`) | `test_the_server_without_tls_configuration_is_the_plain_http_server_of_before`, `test_the_same_server_serves_plaintext_when_tls_is_off`, `test_healthz_reports_tls_off_when_no_certificate_is_configured` | ✅ M4/M48 killed | **PASS** |
| **T3** | Both set → uvicorn serves TLS on the same port; both URLs become `https` | `__main__.py:64-71`; `config.py:392-409` | `test_tls_material_derives_https_urls_from_the_host_port`, `test_uvicorn_gets_the_certificate_pair_or_explicit_nones` | ✅ M5, M6, M47 killed. Independently confirmed live: `Uvicorn running on https://127.0.0.1:8795`; `redirect_uri`/`sign_in_url` = `https://127.0.0.1:8770/oauth/callback` and `/oauth/start` | **PASS** |
| **T4** | Refuses missing / unreadable / non-PEM / wrong key mode, secret-free; mode rule per T-D10 | `config.py:364-384`, `73-98` | `test_a_missing_certificate_file_refuses_startup_without_reciting_the_path`, `test_a_key_that_is_not_owner_only_is_refused`, `test_a_key_mode_is_enforced_only_where_the_filesystem_can_express_it`, `test_the_mode_probe_reports_the_filesystem_rather_than_the_platform`, `test_material_that_is_not_a_certificate_is_refused`, `test_an_unreadable_certificate_is_refused`, `test_a_key_that_does_not_match_its_certificate_is_refused` | ✅ M8/M10/M11/M49/M50 killed. Probe's three answers are tested (`312-334`). **But the "probe cannot be written ⇒ mode silently off" path is not covered — M-A survived (major 1)** | **PASS, with a gap** |
| **T5** | Cert/key refused inside the repo tree | `config.py:101-114` | `test_a_key_inside_the_repository_is_refused_except_in_the_certs_directory`, `test_a_path_outside_the_repository_is_always_allowed` | ✅ M9 killed | **PASS** |
| **T6** | `/healthz` no bearer + `tls` key; everything else still needs the bearer | `server.py:1803-1820`, `1887-1889` | `test_healthz_carries_the_tls_flag_and_never_needs_a_bearer`, `test_tls_does_not_relax_bearer_auth_on_any_route` + 3 amended `test_server.py` assertions | ✅ M4, M45 killed | **PASS** |
| **T7** | Host/Origin guard, bearer auth and single-`httpx` rule unchanged; Helix TLS independent | `security.py` Origin check unchanged; `config.py:379` adds only `ssl` | `test_an_https_origin_is_accepted_whether_or_not_tls_is_on`, `tests/test_network_guard.py::test_twitch_client_is_the_only_module_that_imports_httpx` | ✅ M14 killed; R11/R12 verified independently (§4.5) | **PASS** |
| **T8** | Cleartext refused with a clear message, **never** a 3xx | `security.py:113-140`; wired outermost at `server.py:1889` | `test_a_plaintext_request_to_a_tls_server_is_refused_and_never_redirected`, `test_the_same_server_serves_plaintext_when_tls_is_off` | ✅ M1 (302+Location), M2 (inert), M3 (unwired), M43/M44 (scheme bypass), M46 (order swap) all killed; proved outermost empirically (§4.1) | **PASS** |
| **T9** | No cert content, key content or key path in any log, error or `/healthz` body | `config.py:364-384` (names fields only), `security.py:134-140` (extras carry only status+outcome), `logging_setup.py` | `test_no_certificate_detail_reaches_the_log_or_the_health_response`, `test_json_serialisation_of_the_health_body_is_still_small` | ✅ M12 (path into ConfigError), M13 (path into log extra), M28/M41 killed. Live grep of a real TLS server's log: clean (§4.4) | **PASS** |
| **T10** | `make-cert.sh` + PS twin → CA+leaf in git-ignored `./certs`; prints only CA path + fingerprint; never key material | `scripts/make-cert.sh`, `scripts/make-cert.ps1`; `.gitignore:certs/` | `test_the_certificate_script_builds_the_documented_identity_and_quiets_the_key`, `test_the_powershell_twin_trusts_the_ca_for_the_current_user_only` | ⚠️ M15/M16/M18/M19/M20/M34/M37/M38 killed, **but M-C (script `cat`s the private key) survived — the anti-`cat` regex is bypassable (major 3)** | **PASS, with a vacuous assertion** |
| **T11** | `./certs:/certs:ro` only when TLS configured; rootfs read-only; `/certs` never writable | `docker-compose.tls.yml` | `test_the_tls_override_mounts_the_folder_read_only_and_nothing_else`, `tests/test_deployment.py::test_compose_mounts_no_host_directory_by_default` | ✅ M21/M22/M23/M51 killed; `docker compose config` proves it (§4.7). **M-B survived: nothing pins the override's container-side env defaults (major 2)** | **PASS, with a gap** |
| **T12** | README states the exact URI for the TLS mode + the trust steps | `README.md:37`, `88-92`, `108`, `114` | `test_the_readme_gives_the_uri_to_register_and_the_trust_command` | ✅ M27 (URI→http), M28 (trust cmd), M29 ("redirected"), M30 (var names), W1/W2 (macOS row) killed | **PASS** |

Amendments: **T-D10** implemented and honest (`config.py:73-98`; my own run on this Windows/WSL host confirms the premise —
a key on `/mnt/c` reports `0o777` and the probe returns `False`; a real-ext4 `0644` key is refused) — but see major 1
and minor 1. **T-D11** is documentation and script output only: README `88-92` and `make-cert.sh:113-122` both carry the
macOS login-keychain command, and `make-cert.sh` really does branch on platform (`uname`/`Darwin`/`MSYS`/`CYGWIN`
present at `make-cert.sh`). I did **not** ask for macOS tests, per the instruction.

---

## 3. Findings

### Blockers — none

No path was found in which a bearer token can be accepted over cleartext, the refusal can be bypassed, a private key
is committed or printed, or the loopback/hardening posture (R1/R2) is weakened. See §4 for the attacks I tried.

### Majors

**MAJOR 1 — `config.py:89-98`: the mode check fails *open*, and the fail-open has a second, untested trigger that
also has a test-writing trap in it.**
`_filesystem_carries_unix_modes` returns `False` for *any* `OSError` from the probe write, not only the read-only-`/certs`
case the spec names (T-D10 authorises exactly one reason: "the container's read-only `/certs`").
*How confirmed:* `tests/test_zz_probe_td10.py` (temporary, since deleted): with a real ext4 filesystem, a key at mode
`0644` and its **directory** chmod-ed `0555`, the probe write raises → returns `False` → `load_settings()`
**accepted** the world-readable key (`PROBE SAID False -> ACCEPTED with mode 0o644`). Baseline control in the same run:
the same `0644` key in a writable directory is correctly refused. The existing probe test
(`tests/test_tls.py:312-334`) does exercise the read-only-directory branch — but only through the helper in isolation,
never through `load_settings()`, so **no test pins the end-to-end consequence of that third answer**.
*Also, a trap:* the obvious way to write that missing test is `chmod 0o500`, but that keeps the **owner** write bit, so
the probe still succeeds and the test asserts the wrong thing. That is precisely the case a reviewer cannot certify
from a green suite.
*Why major, not blocker:* the adversary who can make the directory unwritable already owns the key's directory and
could simply read the key; in the container `/certs` is `:ro` with the host path under the operator's control. My
judgement is on the fail-open itself: **accepted** (see §4.3) — but its scope is wider than the spec authorises and it
is untested at the `load_settings()` boundary.

**MAJOR 2 — `docker-compose.tls.yml:16-17`: the container-side TLS defaults are unpinned by any test.**
The override supplies `TWITCH_TLS_CERT_FILE: ${TWITCH_TLS_CERT_FILE:-/certs/server.crt}` and the key equivalent.
Without them the container reads only `.env` (`env_file: .env`, `docker-compose.yml:31`), which — per `make-cert.sh`'s
own step 2, which instructs the operator to put **exactly those container paths** in `.env` — would resolve to nothing,
and the server would come up in **plain HTTP** while the operator believes TLS is on.
*How confirmed:* `tools/_rv_bite.sh` (temporary) deleted both `TWITCH_TLS_*` lines from `docker-compose.tls.yml` and
ran the **full suite**: `993 passed`, coverage `90.99%` — **survived**. `tests/test_tls.py:577-589` (the T11 test) reads
the file as text and asserts only the mount; the earlier "kill" I recorded for this mutation in a filtered run was a
false positive caused by collateral from a different mutation, which I re-tested properly on the full suite.
*Why major:* it is the one edit to this file that silently converts a TLS deployment back into a cleartext bearer
deployment — the exact risk class T8/T-D9 exist to prevent — and nothing fails.

**MAJOR 3 — `tests/test_tls.py:566`: the "never prints key material" assertion is bypassable; the requirement it
guards (T10, and T9 for the generator) is effectively unpinned.**
The assertion is `assert not re.search(r"^\s*cat\s+.*\.key", script, re.MULTILINE)`. It only fires when a line both
starts with `cat` **and ends in `.key`**.
*How confirmed:* `tools/_rv_cat.sh` (temporary) inserted `cat "${LEAF_KEY}"` after `LEAF_DAYS=398` in
`scripts/make-cert.sh` — a literal instruction to dump the private key to stdout. Result: regex match `None`
(the test believes no key is catted) and **`33 passed`** — survived. `cat "$LEAF_KEY"`, `cat "${LEAF_KEY}"`,
`echo $(<server.key)`, `openssl … -noout -text` on the key, or `set -x` at key-generation time all slip past it.
The sibling assertion `assert "openssl genrsa" in script and ">/dev/null" in script` is equally shallow (it is satisfied
by any `>/dev/null` anywhere in the file).
*Mitigating evidence I verified independently:* in the run I executed, the script printed **no** key material — output
contained only the CA path and SHA-256 fingerprint (`/tmp/zz_mk1.out`, grepped for `BEGIN … PRIVATE KEY`, `proc-type`,
base64 runs: clean), keys landed `0600`, dir `0700`, and `umask 077` is applied before creation even under an inherited
`umask 022`. So the shipped script is **correct**; the *test* cannot tell if someone edits it.

**MAJOR 4 — `docs/specs/tls-oauth-redirect/SPEC.md:130-131` + `scripts/make-cert.sh:130-131`: the script can tell the
operator to register a plain-`http` redirect URI — the exact string the feature exists to eliminate.**
`make-cert.sh` ends by printing `4) Register exactly this OAuth Redirect URL … $(redirect_uri)`, and `redirect_uri()`
(`scripts/lib/common.sh:163-165`) derives its scheme from `tls_configured()` (`common.sh:39-41`), which reads
`TWITCH_TLS_CERT_FILE`/`TWITCH_TLS_KEY_FILE` **from `.env` only**. Run before those lines are added to `.env` — which is
the ordering `make-cert.sh` *itself* prescribes, since step 2 is "put these two lines in `.env`" — it prints `http://…`.
*How confirmed (verbatim from my scratch run, `umask 022`, fresh scratch repo):*

```
  4) Register exactly this OAuth Redirect URL at https://dev.twitch.tv/console/apps
       http://127.0.0.1:8770/oauth/callback
```

This is the single most consequential string in the whole feature: it is the value the Twitch console rejects
("Redirect URIs must use HTTPS protocol"), and an operator who copies it gets a sign-in flow that fails at the console
with a confusing error — or worse, registers it, gets TLS working, and then has a stale console entry.
*Why not a blocker:* it is script output only. It cannot weaken a running server (`config.py:392-395` derives the scheme
from the settings, never from `.env`'s presence, and the live listener proved itself `https`), and README §1/§2.1 give
the correct `https` URI, which **is** pinned (`tests/test_tls.py:604-614`, mutation M27 killed).
*Recommended fix:* since the certificate's entire purpose is TLS, print the `https` URI unconditionally (or refuse to
print a URI until `tls_configured` is true). No test currently asserts anything about this line — add one.

### Minors

**MINOR 1 — `config.py:73-98`: `False` is overloaded across three filesystem conditions** ("chmod is a no-op", "probe
not writable", "cannot stat"). T-D10 only authorises the second as a *skip* reason. An operator with a genuinely
readable-but-unwritable key directory gets a world-readable key accepted with no warning line at all. A one-line
`logger.info`-style signal (or splitting the return into `real / cannot-express / not-probable`) would make the skip
observable. Confirmed by the same repro as major 1.

**MINOR 2 — `tests/test_tls.py:616-627`: the T-map sweep can detect an *uncited* requirement but not a *removed* one.**
`required` is recomputed from the spec on every run (`required = set(re.findall(r"^- \*\*T(\d+)\*\*", body, …))`), so
deleting a requirement from `SPEC.md` removes it from the required set and the assertion passes vacuously.
*How confirmed:* `tests/test_zz_mapgap.py` (temporary) computed, with T8's spec line deleted: `required w/o T8 =
['1'..'7','9'..'12']`, `required - cited = []` → the sweep reports no gap, while no test implements T8 any more.
*Materiality is low* — the spec is human-approved and the git history preserves deletions — and note the **sibling**
sweep in `tests/test_gate.py:146-150` *does* check the reverse direction (tests citing a requirement the spec no longer
defines), so the R-map is better guarded than the T-map. Aligning the two would close it.

**MINOR 3 — `Dockerfile:58` / `docker-compose.yml:43`: the adaptive healthcheck is verified only by substring
assertions** (`"TWITCH_TLS_CERT_FILE" in source`, `"_create_unverified_context" in source`, `"127.0.0.1:8000/healthz" in
source`, `tests/test_tls.py:591-601`) and is **never executed**. A healthcheck that can never pass is a deployment
liability (the container reads `unhealthy` forever and `restart: unless-stopped` won't save it).
*How confirmed:* the mutations that survived are instructive — removing the *scheme adaptivity* from the Dockerfile
survived in one run while the port and syntax mutations were caught in another (`W4` broken-python-syntax and `W5`
bogus-path survived the filtered run; `M31` non-adaptive and `M33` wrong-port were killed). Because the assertions are
substrings over two files, which of several degradations bites is not uniform. *Suggested fix:* one test that extracts
the `python -c` body from each file and `compile()`s it, plus asserts the `if t else` branch is present in **both**.
*Mitigating evidence:* I ran the equivalent expression myself against a live TLS listener — it returns 200 over https
and the same code path serves http when TLS is off — so the healthcheck is **correct**, just under-tested.

**MINOR 4 — environment limitation, recorded for the record.** `shellcheck` and `pwsh` are **not installed** on this
WSL host (`command -v shellcheck` → empty; `command -v pwsh` → empty). So: `bash -n` passed for all ten scripts
(`logs.sh, make-cert.sh, rebuild.sh, restart.sh, setup.sh, smoke.sh, start.sh, status.sh, stop.sh, lib/common.sh`) and
all are LF (no CRLF, which would break bash under WSL); but `scripts/make-cert.ps1` and the other `.ps1` twins got a
read-only review only, with no syntax check. `make-cert.ps1:44` (`& certutil.exe -addstore -user Root $caPath | Out-Null`)
and its drive of `make-cert.sh` were read and are consistent with T-D4/T-D11 — mutation M19 (dropping `-user`) and M20
(breaking the `make-cert.sh` link) were both killed, so the *content* is pinned even though the *syntax* was never
parsed here. **Follow-up for the human:** run `shellcheck scripts/*.sh scripts/lib/*.sh` and a `pwsh -NoProfile
-Command { … }` syntax pass where those tools exist.

**MINOR 5 — `README.md:108`**: "`docker-compose.tls.yml` is applied automatically once both are set" is true but
incomplete — it is `scripts/lib/common.sh:57-67 compose()` that layers the override, so **only the `scripts/*.sh`
entry points** apply it. A bare `docker compose up -d` (or CI/tooling that bypasses the wrappers) with TLS set in `.env`
starts the container with **no `/certs` mount**, and `config.py:369-370` then refuses with
`TWITCH_TLS_CERT_FILE does not name a readable file` (`config.py:370`) — fail-closed and safe, but the operator gets a
mysterious error. One sentence ("use the scripts, or pass `-f docker-compose.tls.yml`") would close it. Confirmed by
reading `compose()` and by reproducing the refusal.

---

## 4. The numbered probes — what I ran and what happened

### 4.1 Probe 1 — is the plaintext refusal *actually* outermost? **RAN. Yes. No 3xx anywhere.**
*Reasoning:* `Starlette.add_middleware` does `user_middleware.insert(0, ...)`, and `build_middleware_stack` wraps
`reversed(user_middleware)` — so the **last added is outermost**. `server.py:1887-1889` adds `BearerAuthMiddleware`,
then `HostGuardMiddleware`, then `PlaintextRefusalMiddleware` last ⇒ refusal outermost. The comment at `server.py:1881-1886`
states this and is correct.
*Empirical proof:* I asserted the order on a booted app — `USER_MIDDLEWARE_ORDER
['PlaintextRefusalMiddleware', 'HostGuardMiddleware', 'BearerAuthMiddleware']`. Then, in the **same** cleartext request
I paired each path with a **bad** `Host: evil.example` **and** an `Origin`, and separately with a **valid bearer**:

```
CLEAR-BADHOST /healthz      400 {'content-length': '74', 'content-type': 'text/plain; charset=utf-8'}
CLEAR-BADHOST /mcp          400 …    CLEAR-BADHOST /oauth/start     400 …
CLEAR-BADHOST /oauth/callback 400 …  CLEAR-BADHOST / 400 …  /docs 400 …  /openapi.json 400 …
CLEAR-MCP-WITH-BEARER      400      CLEAR-HEALTHZ-WITH-BEARER  400
```

400 (not 421) with a bad Host proves the refusal runs **before** the Host guard; 400 with a valid bearer proves it runs
**before** bearer auth. No `Location` header on any response; no `https://` substring in any body.
*Gate hardening check:* I drove `PlaintextRefusalMiddleware` directly with schemes `http`, **absent**, `HTTPS`, `wss`,
`https` → `INNER_RAN_FOR ['https']`, `REFUSAL_STATUSES [400, 400, 400, 400]`. The gate is fail-closed even for a
scheme-less or wrong-case scope (`security.py:131` compares against lowercase `"https"` exactly); mutations M43
(case-insensitive bypass) and M44 (inverted condition) were both killed.
*At the real listener:* cleartext to the TLS port dies in the TLS layer before ASGI (`EMPTY-RESPONSE (connection closed
with no bytes)` for `/healthz`, `/mcp`, `/oauth/start`, `/`, and `/mcp` + bearer). **No route is answerable in the clear.**

### 4.2 Probe 2 — can TLS-on ever accept a bearer over cleartext? **RAN. No.**
Booted the **real** `twitch_mcp.__main__.main()` with a generated pair (uvicorn args captured as
`ssl_certfile=…/server.crt`, `ssl_keyfile=…/server.key`), log line `Uvicorn running on https://127.0.0.1:8795`:

```
https /healthz        (200, …, '{"status":"degraded","version":"0.1.0","tool_count":16,"twitch_reachable":false,"tls":true}')
https /mcp no-bearer  401    https /mcp bearer  200
```

*Forgotten compose override:* refusal, not a cleartext fallback — reproduced: `TWITCH_TLS_CERT_FILE does not name a
readable file`. *Not a certificate:* `… do not parse as a certificate and its matching key (SSLError)` (`config.py:378-384`)
— the pair is proven parseable via `ssl.SSLContext.load_cert_chain` **before** `uvicorn.run`, so no half-listening
socket (mutation M10 killed). *Only one variable set:* refused naming the missing field, never the path
(`config.py:360-363`); I additionally forced half-configured settings straight into `create_app`, bypassing the
validator, and got `tls:false` + `scheme http` — the flag and the scheme cannot disagree (both read
`tls_enabled`, `config.py:388-395`; M48 killed). *Relative path* → `must be an absolute path`. *Directory as key* →
refused. *Swapped cert/key* → refused (`test_a_key_that_does_not_match_its_certificate_is_refused`).

### 4.3 Probe 3 — is the T-D10 fail-open bounded? Can an attacker turn the mode check off? **RAN. Accepted, with major 1.**
*The premise is real, verified on this host:* a freshly generated key under `/mnt/c/…` reports `0o777` and
`_filesystem_carries_unix_modes` → `False` (`WIN key mode reported: 0o777 probe says: False`), so a literal `0600`
check really would make TLS unreachable on the operator's own machine while protecting nothing (NTFS ACL is the control).
The control case proves the rule still bites where modes exist: an ext4 `0644` key → `REFUSED: … TWITCH_TLS_KEY_FILE
must have mode 0600`.
*Who controls what:* the **directory** — an attacker needs write/ownership of the key's parent to make the probe
unwritable; the **probe filename** — it is a fixed constant `.twitch-mcp-mode-probe` (`config.py:89`), so an attacker
who owns the directory can pre-create a decoy at `0600` (I verified `_filesystem_carries_unix_modes` returns `True` on
a decoy-only directory); the **filesystem** — `chmod`-as-no-op and read-only mounts flip it without any attacker.
*Adversarial repro:* `0644` key in a `chmod 0555` directory → `PROBE SAID False -> ACCEPTED with mode 0o644`.
*Plain statement:* **I accept it.** The mode rule defends against operator error, not against an adversary who already
owns the key's directory — such an adversary can read the key directly, so the probe grants nothing they did not already
have. It is bounded by filesystem ownership and, in the container, by the `:ro` mount over a host path only the
operator/root controls (`docker compose config` proves `read_only: true`). Fail-**closed** here would be worse: it would
break TLS in the container (read-only `/certs` — the normal case) and on the operator's WSL-mounted drive, i.e. exactly
the two deployments the feature exists for. What I do **not** accept is the untested third trigger (major 1) and the
silent, unlogged skip (minor 1).

### 4.4 Probe 4 — any key path / cert path / key content reaching a log, `/healthz`, or a `ConfigError`? **RAN. Clean.**
Checked `security.py:134-140`: the plaintext rejection logs `extra={"status": 400, "outcome": "plaintext"}` only — no
route, no path. `HostGuardMiddleware` carries only `{status, outcome}`. `logging_setup.py:94-102` whitelists the extras
it serialises (`tool, route, duration_ms, outcome, status, token_type, broadcaster_id, scope`) and redacts string extras
by secret key-name and pattern (`:39-49`, `:150-153`), so a path could only arrive via a deliberate `extra`.
*Live proof:* ran a real TLS server, attacked it (401s, healthz, cleartext), then grepped the entire server log:
`clean: /tmp/zz_live_material` · `clean: server.key` · `clean: server.crt` · `clean: PRIVATE KEY` ·
`clean: <the bearer>` . (An earlier `LEAK FOUND` in my own harness was my probe printing the uvicorn kwargs — not the
server; re-ran with that debug line removed.) `/healthz` body is 91 bytes, five keys, `tls` boolean only
(`test_json_serialisation_of_the_health_body_is_still_small`, M5/M28 killed). `ConfigError` messages name fields, never
values: `config.py:361-384` — and mutation M12 (interpolating `{raw}` into the message) was **killed**, so this is
pinned, not just true. Also verified the message cannot smuggle the path via `type(exc).__name__`
(`config.py:383` — an exception class name, not a path).

### 4.5 Probe 5 — is the Origin check really untouched, and does nothing new open a socket or import a networking module? **RAN. Clean.**
`security.py`'s Origin check accepts `parts.scheme in {"http", "https"}` — unchanged by this feature, and T7's docstring
claim at `security.py:11-14` is accurate. Confirmed by `test_an_https_origin_is_accepted_whether_or_not_tls_is_on` and
by mutation M14 (restricting to `{"http"}`) being **killed**.
*Module discipline:* AST scan over `src/twitch_mcp/*.py` — `httpx` offenders: `['twitch_client.py']` (satisfies R11, and
the dedicated AST test `tests/test_network_guard.py::test_twitch_client_is_the_only_module_that_imports_httpx` enforces
it). Networking-ish imports outside the sanctioned module: `{'ssl': ['config.py']}` — one import, used solely for
`ssl.SSLContext(...).load_cert_chain(...)` at `config.py:379` to *parse* material; it opens no socket. No `socket`,
`urllib3`, `aiohttp` or `requests` anywhere in `src/`; no `bind`/`connect`/`listen`/`create_server`. The only `websocket`
hit (`security.py:174`) is pre-existing ASGI scope handling, not a socket. `urllib.parse` uses are URL parsing, not I/O.
*Hosts (R12):* only `DEFAULT_API_BASE = "https://api.twitch.tv/helix"` and `DEFAULT_AUTH_BASE = "https://id.twitch.tv"`,
both pinned by `_check_base_url` (`config.py:117-139`, rejects any other scheme/host/port/path/credentials), and no new
host string was introduced (mutation M43-`evil.example` killed).
*Verification-disabled confinement (T-D8):* exactly four sites — `Dockerfile:58`, `docker-compose.yml:43` (both the
container self-probe to `127.0.0.1:8000/healthz`) and `scripts/smoke.sh:41` (loopback probe). `twitch_client.py` contains
**no** `verify=False` / `CERT_NONE` / custom context, so calls to Twitch keep full verification.
*R55:* the autouse network block in `tests/conftest.py` blocked my own in-pytest socket attempts
(`AssertionError: test attempted a real network request: 127.0.0.1`) — which is the guard working. The full suite
passes with no network. (My live-listener probe deliberately ran **outside** pytest.)

### 4.6 Probe 6 — is the T-map honest? Do the text assertions bite? **RAN. 64 mutations. The suite bites; 4 survivors.**
The map is honest about **coverage**: all twelve `- **Tn**` headers are present in the spec and each is cited inside
`tests/test_tls.py` (per-T citation counts: T1:5 T2:5 T3:4 T4:8 T5:3 T6:4 T7:3 T8:4 T9:8 T10:3 T11:1 T12:3), and
`test_every_tls_requirement_is_named_by_a_test_in_this_module` enforces it. It is *not* honest about **deletions**
(minor 2). It is *not* a name-only table — renaming test functions (V4) and stripping citations (V3) were both killed.
*Killed (the tests genuinely bite):* M1 302+Location · M2 refusal inert · M3 refusal unwired · M4 `tls` key dropped ·
M5 uvicorn ssl args dropped · M6 scheme always http · M7 both-or-neither removed · M8 mode rule off · M9 repo-location
off · M10 non-PEM accepted · M11 missing file accepted · M12 key path in ConfigError · M13 key path in log extra ·
M14 https Origin rejected · M15 SAN dropped · M16 key `chmod 644` · M18 leaf days wrong · M19 machine-wide trust ·
M20 PS twin no longer drives the sh · M21/M22 writable mount · M23 base gains a volume · M24/M25 healthcheck scheme
pinned · M26 verification re-enabled · M27 README URI→http · M28 README trust cmd broken · M29 README "redirected" ·
M30 README var names · M31 Dockerfile healthcheck not adaptive · M32/M33 healthcheck ports · M34 `umask` removed ·
M36 `cat` of key ending in `.key` · M37 PS `-user` dropped · M38 second generator introduced · M43/M44 scheme bypass ·
M45 healthz needs bearer · M46 **middleware order swapped** · M47 empty strings instead of `None` · M48
`tls_enabled` from cert *or* key · M49/M50 probe forced true/false · M51 `.gitignore` loses `certs/` · M52 smoke always
http · M53 `status.sh` hardcodes http · M54 `common.sh` scheme hardcoded · V1–V8 (map, log captor, `https_client`
silently switched to `http`, `_write_pair` no longer sets the mode) · W1/W2 (README macOS row broken/deleted).
*The four survivors* are **major 1** (mode check silently off), **major 2** (compose TLS defaults unpinned),
**major 3** (bypassable anti-`cat` regex) and **minor 2** (removed requirement undetectable). Note especially M46: an
earlier round recorded the middleware-order swap as a survivor and it is now killed, and M13/M12 confirm T9's log and
error guarantees are enforced, not merely asserted. The text-assertion tests I was told to distrust (README, compose,
Dockerfile, `make-cert.sh/.ps1`) mostly **do** bite — with the specific exceptions in major 3 and minor 3.

### 4.7 Probe 7 — deployment posture. **RAN. Correct.**
`docker compose config` (run from a scratch copy with a throwaway `.env`, so the repo was never dirtied):
*Base only:* `BASE MOUNTS NOTHING (good)` — no `volumes:` key at all; hardening intact (`read_only: true`,
`cap_drop: [ALL]`, `no-new-privileges:true`, `init: true`, `pids_limit: 128`, `mem_limit: 512m`, `tmpfs /tmp:size=16m,mode=1777`,
and `host_ip: 127.0.0.1 / target: 8000 / published: "8770"` — R1/R2).
*Base + tls:* exactly one bind — `source: …/certs`, `target: /certs`, **`read_only: true`** — with the rootfs still
`read_only: true`. The override itself contains no `read_only:` key (the base owns that posture, and
`tests/test_tls.py:584` asserts it is *not* restated there).
*Base + oauth + tls:* two mounts, `/data` (writable, expected for the token store) and `/certs` (`read_only: true`).
*Adaptive healthcheck:* valid Python in both files; verification-disabled appears exactly once per file and is confined
to the loopback self-probe (R12/R55 checked in §4.5). Caveat: never executed by the suite (minor 3) — but I executed
the equivalent myself against a live TLS listener and it returns 200.

### 4.8 Probe 8 — shell scripts. **RAN.**
`bash -n`: all ten scripts + `lib/common.sh` **OK**; all LF, no CRLF. `shellcheck` and `pwsh` not installed (minor 4).
`scripts/make-cert.sh` run in a scratch repo copy (so `lib/common.sh` resolved) with a deliberately hostile
`umask 022`: it still produced `drwx------ certs`, `ca.key 600`, `server.key 600`, `*.crt 644`; idempotent re-run and
`--force` both exit 0. The openssl invocations produce exactly the identity T-D5 demands — verified with `openssl x509`:
`subject=CN = localhost`, `X509v3 Subject Alternative Name: DNS:localhost, IP Address:127.0.0.1`,
`Extended Key Usage: TLS Web Server Authentication`, `Basic Constraints: critical CA:FALSE`, leaf
`notAfter=Dec 2 2027` (398 days), CA `CA:TRUE` to 2036 (3650 days); `openssl verify -CAfile ca.crt server.crt: OK`;
and `ssl.SSLContext(PROTOCOL_TLS_SERVER).load_cert_chain(...)` accepts the pair (so a boot from this material works).
Key material can never be printed **in this version**: output was grepped for `BEGIN … PRIVATE KEY`, `proc-type` and
base64 runs — clean; the only key-path string in the output is the literal `TWITCH_TLS_KEY_FILE=/certs/server.key`
instruction line. `./certs` and the scratch dirs were deleted afterwards (`ls -d certs` → not present).
*Caveat:* that "can never" is a property of the current text, not of the tests — see major 3.

### 4.9 Probe 9 — re-run the gate. **RAN.** Numbers in §1. (982 passed / 90.99% / ruff clean / mypy clean.)

### 4.10 Probe 10 — is the documentation truthful? **RAN. Truthful, one script-output defect (major 4) and minor 5.**
`load_settings()` with the generated pair returns exactly `https://127.0.0.1:8770/oauth/callback` and
`https://127.0.0.1:8770/oauth/start`; README §1 (`:37`) and §2.1 carry that URI byte-for-byte, and the README contains
**zero** occurrences of an `http://` variant of it. `.env.example` names match `config.py`'s field names exactly — the
documented TLS vars are precisely `{TWITCH_TLS_CERT_FILE, TWITCH_TLS_KEY_FILE}` (and renaming them in `.env.example`
**is** caught: W3 killed on the full suite). The troubleshooting rows describe what the code does — I reproduced each
refusal behind the row: `must have mode 0600`, `must both be set, or neither`, and the "400 from a client that still
uses http" row. The README's claim that `make-cert.sh` prints whichever trust command applies is true (the script
branches on `uname`/`Darwin`/`MSYS`/`CYGWIN`), and the macOS command in README `:92` matches the script's exactly.
The one untruth is the URI `make-cert.sh` prints when TLS is not yet in `.env` (major 4).

---

## 5. Scope audit — anything touched that the spec does not authorise?

Compared the implemented surface against §6 of the spec file-by-file. Every touched file is authorised. No unauthorised
extras found: no HSTS, no HTTP/2, no ACME, no mTLS, no pinning, no EventSub, no port change, no sidecar, no new outbound
host, no change to `twitch_client.py`'s transport (all §9 out-of-scope items are absent). `docs/SPEC.md` carries the
R2/R3/§6.5 amendment notes as declared. The three amended `tests/test_server.py` assertions are exactly the ones T6
mandates and no others (`git status` shows no other test file modified). No secrets in the diff; no `certs/` or `.env`
committed or present.

---

## 6. Verdict

**CHANGES REQUESTED.** 0 blockers, 4 majors, 5 minors.

The security-critical behaviour is correct and, for the most part, genuinely pinned: I could not accept a bearer token
over cleartext by any route, I could not make the refusal redirect, I could not get key material into a log, `/healthz`,
or an error message, and I could not weaken the container posture. T8's "never a 3xx" is real and enforced.
`test_tls.py` is not a decorative suite — 60 of 64 mutations were killed, including the middleware-order swap that
mattered most.

What must change before approval, in priority order:

1. **`make-cert.sh` must not print an `http://` redirect URI** (major 4) — one line of script plus one test.
2. **Pin the `docker-compose.tls.yml` container-side TLS defaults** (major 2) — their loss silently downgrades a TLS
   deployment to cleartext bearer and the full suite stays green.
3. **Make the "never prints key material" assertion actually mean something** (major 3) — the current regex is bypassed
   by `cat "${LEAF_KEY}"`.
4. **Cover the probe-unwritable ⇒ mode-check-off path through `load_settings()`** (major 1), avoiding the `chmod 0o500`
   trap; ideally log the skip (minor 1).

The five minors can be fixed in the same pass or explicitly accepted in writing with justification. I did not edit any
file other than this report.
