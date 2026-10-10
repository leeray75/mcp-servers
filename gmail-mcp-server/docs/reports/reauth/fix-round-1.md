# Fix round 1

1. Cross-site flooding of `/auth`: `GET /auth` now returns the 403 error page (with security headers, outcome `cross_site` logged, nothing stored) when `Sec-Fetch-Site` is present and not `none`/`same-origin`; requests without the header still work. Residual eviction risk (non-browser local process) documented in README security model and docs/SPEC.md §4.4. Tests: refused for cross-site/same-site/bogus, allowed for none/same-origin/absent.
2. `HEAD /auth` returns 200 with headers only and creates no pending entry (test checks the store is empty). The old HEAD-302 test was replaced.
3. `scripts/lib/common.sh` `auth_route_available` probes `curl -I /auth` expecting 200 (older images answer 401). `auth.sh` uses the server path only if both `server_running` and the probe pass; otherwise it warns to run `scripts/rebuild.sh` (`rebuild.ps1` on Windows) and uses the one-off container. The timeout message also mentions rebuilding. Without curl the probe is skipped.
4. Added `-T` to the piped `compose run`: stdout is a pipe anyway, no input is needed, and `-T` avoids TTY CR/LF mangling; output flow is unchanged (`PYTHONUNBUFFERED=1` still set, `\r` stripping kept). Not run against real Docker flow (forbidden), reasoning only.
5. Re-wrapped the README "Testing mode" paragraph line and the SPEC §4.2 line (table rows left as is, they cannot wrap).

Gate (python:3.12-slim, copy of repo in /tmp/gate1, removed afterwards with `docker run --rm -v /tmp:/t alpine rm -rf /t/gate1 /t/rv`; /tmp/rv gone): ruff check and format clean (final run), mypy strict: no issues in 27 files, pytest 279 passed, coverage 99.19%. `bash -n` on auth.sh and common.sh OK.
