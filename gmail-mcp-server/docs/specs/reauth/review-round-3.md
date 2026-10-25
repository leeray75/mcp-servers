# Re-authentication: review round 3

## Verdict: APPROVE
No BLOCKER or MAJOR. Finding 6 fixed; finding 7 NOT fixed (cosmetic only, remains MINOR).

## Gate (python:3.12-slim via WSL Docker on a copy; copy removed with an alpine root container, nothing left in /tmp)
ruff check: All checks passed | ruff format --check: 44 files already formatted | mypy --strict: no issues in 27 source files | pytest: 286 passed, coverage 99.19% | bash -n common.sh: OK

## Findings
6. FIXED. signin.py `_browser_request_allowed` (lines 93-106): no/`none`/`same-origin` Sec-Fetch-Site passes; otherwise requires `Sec-Fetch-User == "?1"` AND `Sec-Fetch-Mode == "navigate"` AND Dest `document` (absent allowed). Starlette header lookup is case-insensitive; comparisons are exact. `Sec-Fetch-*` are forbidden request headers, so a hostile page's fetch/XHR/img/iframe cannot forge them; user-activated top-level navigations from another site remain possible by design (requires a user click, same as any link), which is the accepted residual. Tests (test_signin.py:181-223) cover allowed/refused combinations including `?0`, no-cors, iframe dest. README and SPEC 4.4 updated.
7. NOT FIXED (MINOR, cosmetic). scripts/lib/common.sh:203 still has a run of spaces before the URL instead of a `\` continuation (fix-round-2 claims it was re-wrapped; the file does not show that). Behaviour is correct; replace the spaces with ` \` and a newline.

No regressions found.
