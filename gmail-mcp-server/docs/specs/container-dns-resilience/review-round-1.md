# Review round 1: container-dns-resilience

Verdict: **APPROVED** (no blockers, no majors; 3 minors).

Gate run from WSL (`make check VENV=~/.venvs/gmail-mcp`): ruff, format, mypy --strict clean; 425 passed; coverage 99.20% (gate 85%).
No change to `requirements*.txt` or `pyproject.toml` (git diff empty). Verified by reading code, not the implementation report.

## Requirement check

| Req | Result |
|---|---|
| R1 | OK. `dns: ["${CONTAINER_DNS_1:-8.8.8.8}", "${CONTAINER_DNS_2:-1.1.1.1}"]` on both services; flow list of double-quoted scalars is valid YAML and valid Compose interpolation. `.env.example` documents both (commented). Tested. |
| R2 | OK. Stdlib only; one executor worker per host; single `wait(timeout)` deadline; `shutdown(wait=False, cancel_futures=True)`; outer `except` so it never raises; only bools and exception class names kept (addresses discarded). Tests: success, gaierror class-only, no addresses, hang within timeout, unexpected error, empty list. Non-tautological. |
| R3 | OK. No `config`/Settings import, no secrets, one content-free stderr line, exit 0 only if HTTP 200 and DNS ok. Tests cover 200/non-200/HTTPError/refused/BadStatusLine/bad port. |
| R4 | OK. Compose `["CMD","python","-m","gmail_mcp.healthcheck"]` equals Dockerfile CMD (test compares both); timings unchanged and asserted; `auth` keeps `disable: true`. |
| R5 | OK. Only `getaddrinfo` is used; no HTTPS/connect to Google. |
| R6 | OK with one minor (m3). README states unhealthy does not auto-restart; triage sequence present; SPEC.md, CHANGELOG [Unreleased], .env.example, CLAUDE.md updated. |
| R7 | OK. Hardening, loopback binding, `./data` mount, UID 10001 untouched and asserted by new tests. |
| R8 | OK. `load_settings()` test with `CONTAINER_DNS_*` set passes. |

Nothing outside the spec was added.

## Deviation 2 assessment (non-daemon executor workers)

`ThreadPoolExecutor` workers are non-daemon and are joined at interpreter exit (`threading._register_atexit`). When `getaddrinfo` hangs, `main()` returns 1 after about 4 s, but the process cannot exit until the stuck lookup returns (glibc resolver timeouts can be 10+ s), so Docker's 5 s timeout kills the check. Docker records a timeout as a failed check, the same outcome as exit 1, and that happens only when DNS is already broken, i.e. precisely when "unhealthy" is correct. No false healthy is possible. R2's bound (the `resolve_hosts` call returns within `timeout`) is met and tested; R3 requires exit 0 only on success, which holds. So this is **not a defect against R2/R3**; acceptable. The only cost is that the content-free diagnostic line is printed but Docker may report a timeout instead of exit 1, and a straggler process lives briefly.

## Issues

- **minor m1** `src/gmail_mcp/healthcheck.py:46` (`__main__` guard). Recommended hardening, optional: `code = main(); sys.stderr.flush(); os._exit(code)` so the process exits at once and the "1" is reported rather than a Docker timeout. Alternatively accept as is and document. Note this would require a spec-compatible test/exclusion for the guard; fine to defer.
- **minor m2** Worst-case timing: Python startup + 2 s HTTP + 2 s DNS is about 4.1-4.5 s against a 5 s Docker timeout (spec-designed). If HTTP and DNS are both slow, a timeout may surface instead of exit 1; harmless as above. Consider running DNS first or in parallel if flapping is observed.
- **minor m3** `CLAUDE.md:34` says `gmail_client.py` is "the only module that calls Google". `auth.py` and `signin.py` also contact Google (token endpoint, OAuth flow). R6 asked that confinement wording name `gmail_client.py` for Gmail/Google calls and `netcheck.py` for DNS only; suggest "the only module that calls the Gmail API (OAuth sign-in lives in `auth.py`/`signin.py`), and `netcheck.py` resolves DNS only".
- **minor m4 (info)** `netcheck.py:69-70` (defensive outer `except`) uncovered; acceptable given 99% total. `test_empty_host_list_is_not_ok` monkeypatches `PROBE_HOSTS` needlessly (harmless).

Live checks from spec section 7 (resolv.conf, healthy after rebuild, wsl shutdown, Tailscale reconnect, blocked resolver gives unhealthy) are manual and not verifiable here; they must be recorded in the summary report.
