# Review round 1: release v0.2.0 (gmail-mcp-server)

Verdict: **APPROVED**

## Verified
- R1: `pyproject.toml` and `__version__` are 0.2.0. No other stale 0.1.0 in README, docs, Dockerfile, compose, scripts or tests (remaining hits are historical text in agent-auth-hardening and this release folder, plus CHANGELOG [0.1.0]).
- R2: diff against HEAD is exactly one added `## [0.2.0] - 2026-10-25` heading under an empty `[Unreleased]`; body and [0.1.0] byte-identical.
- R3: seven tools (six plus `auth_status`) and four resources match `server.py`; README config table matches `config.py` and `.env.example`; docs layout lines now list the four spec folders. No remaining drift found.
- R4: `test_package_version_matches_pyproject` is sound (tomllib on the real pyproject, compared to `__version__`). The two changelog tests now read the 0.2.0 section; correct.
- No behaviour change outside the spec (diff touches only metadata, docs and tests).
- R5: `make check` in WSL: 398 passed, coverage 99.31%.

## Issues
- Minor: CLAUDE.md intro list is missing a comma after "(server-hosted re-authentication)" before `docs/specs/agent-auth/`. Cosmetic.
- Minor: the new version test hardcodes "0.2.0", so the next release must update it (acceptable, arguably intended).

No blockers or majors.
