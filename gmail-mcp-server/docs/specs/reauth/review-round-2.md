# Re-authentication: review round 2

## Verdict: APPROVE
Round-1 findings 1-5 are all fixed. No BLOCKER or MAJOR; two new MINOR nits.

## Gate (re-run in python:3.12-slim via WSL Docker on a copy; copy removed afterwards with an alpine root container, nothing left in /tmp)
- ruff check: All checks passed! | ruff format --check: 42 files already formatted
- mypy --strict: no issues in 27 source files
- pytest: 279 passed, coverage 99.19% (gate 85%), signin.py 100%
- bash -n scripts/auth.sh scripts/lib/common.sh: OK

## Round-1 findings
1. Cross-site flooding of /auth: FIXED. signin.py start() refuses (403 static page, headers, `cross_site` log, nothing stored) when `Sec-Fetch-Site` is present and not `none`/`same-origin`; absent header still works. Residual non-browser eviction documented (README, SPEC 4.4).
2. HEAD /auth side effects: FIXED. HEAD returns 200 with security headers before any state is created (signin.py start()).
3. Stale-image 404 wait: FIXED. `auth_route_available` (common.sh:201) probes `curl -I /auth` for 200 (the bearer exemption covers HEAD; old images answer 401); auth.sh uses the server path only when `server_running && auth_route_available`, else warns to rebuild and uses the container; timeout message also hints at rebuild. Both calls are in `if` conditions, so `set -e` is not tripped; curl failures are masked by `|| true`.
4. `-T` on compose run: FIXED. Placed correctly before `-e`/`-p`; PIPESTATUS/`set +e` logic unchanged; CR stripping retained.
5. Doc wrapping: FIXED.

## New findings
6. MINOR - signin.py start() Sec-Fetch-Site check. If a client renders Goose's error as a clickable link inside a web page (not an external-app launch), the browser sends `Sec-Fetch-Site: cross-site` (or `same-site`) on that click and the user gets the 403 "Open the sign-in link directly" page; typed/pasted URLs, explorer.exe/wslview launches and most native apps send `none`. The page text covers it, but consider also allowing `Sec-Fetch-User: ?1` navigations (a real user click) or noting this in the README troubleshooting table.
7. MINOR - scripts/lib/common.sh:203. The curl line contains a run of spaces where a `\` continuation was presumably intended (works, but is a long line and looks accidental). Re-wrap with a backslash.

No regressions found: bearer exemption, Host-guard ordering, header coverage and logging are unchanged.
