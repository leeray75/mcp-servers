# Fix round 2: spec amendment 1 (optional bearer token)

## Changes

- `src/config.mjs`: `MCP_AUTH_TOKEN` is optional. Unset or empty gives `authToken: undefined`. If set it must be >= 32 chars and not start with `replace-me`.
- `src/gateway.mjs`: the bearer check on `/mcp` runs only when a token is configured. `Authorization` is still stripped before forwarding; `/healthz`, Host guard, body cap and 404s are unchanged.
- `src/main.mjs`: logs a startup warning (`MCP_AUTH_TOKEN is not set: /mcp requires NO authentication...`) when no token is set.
- `scripts/setup.sh` (the `.ps1` twin forwards its arguments): default writes `.env` without a token; `--with-token` generates one; `--force` rewrites (timestamped backup).
- `scripts/lib/common.sh`: `check_env` treats the token as optional (validated only when set); `print_goose_hint` prints the `headers:` block only when a token is set and warns otherwise.
- `scripts/smoke.sh`: skips the 401 checks with a note when no token; raw-socket abort steps omit the Authorization line when there is no token.
- `.env.example`, `README.md` (new "Security tradeoff: auth is OFF by default" section, how to enable the token), `CLAUDE.md` updated.

## Tests

`npm test`: 49 tests, all pass; coverage of `src/` 94.47% lines, 92.90% branches, 83.93% functions.
New or changed: config (unset and empty mean disabled; the "missing token" failure case removed), gateway with no token (open `/mcp`, client Authorization stripped upstream, healthz, 404 and Host guard still apply), supervisor warning logged only when no token is set. The existing token-set gateway tests are unchanged.

## Live deployment decision and verification

- The live container now runs with auth OFF (the new default), still published only on `127.0.0.1:8768->8000/tcp`.
- The previous `.env` was copied to `.env.bak` (old token kept only there); `.env` has no `MCP_AUTH_TOKEN`.
- No token: `scripts/smoke.sh` passed (401 steps skipped with a note, live scrape OK) and the container log shows the startup warning.
- Token enforcement re-verified once: with `setup.sh --force --with-token` and a recreate, smoke passed with 401 for no token and for a wrong token. Then `.env` was restored to the default no-token state and the container recreated again; smoke passed.
- The temporary timestamped `.env` backups made by `setup.sh --force` were deleted; only `.env.bak` remains (git-ignored).
- No token values appear in this report.

## Caveat

Goose needs no `headers:` while auth is off. If a token is enabled later, Goose must be given the matching header.
