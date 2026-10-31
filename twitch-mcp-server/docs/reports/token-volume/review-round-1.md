# Implementation review, round 1: docs/specs/token-volume

Reviewer: read-only. Verdict: **APPROVED** (0 blockers, 0 majors, 1 minor).

## Gate
`make check` (WSL, venv /home/leeray/venvs/twitch): ruff, mypy --strict, 1006 passed, coverage 91.36% (floor 90%). Green.

## Requirements
- V1: override has top-level `volumes:` with `twitch-mcp-data:` and `name: twitch-mcp-data` inside the block; service list is exactly `twitch-mcp-data:/data`. Test asserts the name via `_block` (indentation), not a loose text match.
- V2: `/data` target and `TWITCH_OAUTH_TOKEN_FILE: /data/twitch_user_token.json` unchanged (diff).
- V3: base compose untouched except header comment; test checks no top-level `volumes`, no flow-style `volumes: <value>` line (m1 applied), empty service list. Helper strips comment lines and trailing ` #` comments, so a commented mount cannot satisfy it.
- V4: Dockerfile diff is comment-only; the `RUN mkdir -p /data && chown 10001:10001 /data` line is unchanged.
- V5: oauth.py diff belongs to oauth-iss/oauth-wire; nothing from this change. twitch_client.py not touched by it.
- V6: setup.sh block replaced (no mkdir, no chown message, one line naming `twitch-mcp-data`, `disconnect_twitch_account`, `docker volume rm`); stop.sh header names the volume. Grep of scripts/ for `down -v|--volumes|volume rm|volume prune` hits only that printed help string (accepted by the spec checklist).
- V7: `.gitignore` keeps `data/` with the new comment; `data/` folder and `.gitkeep` are gone.
- V8: README (setup paragraph, operator notes, legacy note, security line), CLAUDE.md container invariant (V-D5 wording), docs/SPEC.md R2 and R48, CHANGELOG [Unreleased] with the operator notes, compose headers, Dockerfile, .env.example and common.sh comments are all correct and consistent. README:146, CLAUDE.md:24/157 untouched as required.
- V9: operator-run, pending (not failed).

## SPEC-GAP judgement
docs/SPEC.md §6.1 (directory layout, ~line 324) only lists file names including `docker-compose.oauth.yml`; it has no `./data` or mount wording. Leaving it unchanged is a true no-op.

## Findings
- Minor m1: The `.ps1` twins were not shown to need a change (grep for data/chown finds nothing); no action.
- Minor (only one counted): the setup.sh line is long (>100 chars); style only, accepted.

Blockers: 0. Majors: 0. Minors: 1 (accepted).
