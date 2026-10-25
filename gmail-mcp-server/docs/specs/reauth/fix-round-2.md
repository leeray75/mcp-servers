# Fix round 2

6. `signin.py` `_browser_request_allowed`: `/auth` still allows requests without `Sec-Fetch-Site` and those with `none`/`same-origin`; a cross-site request is now also allowed when it is a user-activated top-level navigation (`Sec-Fetch-User: ?1`, `Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document` or absent). Everything else cross-site gets 403 and stores nothing. Tests: allowed with and without Dest; refused with no headers, no user activation, `?0`, non-navigate mode, iframe dest. README and docs/SPEC.md §4.4 updated.
7. (Claimed fixed here, but it was not: the backslash was lost in shell escaping. Actually fixed in fix-round-3.)

Gate (python:3.12-slim on a copy in /tmp/gate1, removed with the alpine root rm): ruff check clean, ruff format --check clean (43 files), mypy strict no issues in 27 files, pytest 286 passed, coverage 99.19%.
