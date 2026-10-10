# Review round 3: spec amendment 1 (optional bearer token)

## Verification
- `npm test`: 49/49 pass; coverage of src/ is 94.47% lines.
- Live container (Up, healthy, published as `127.0.0.1:8768->8000/tcp`; no token printed, `.env.bak` not read). `.env` contains no `MCP_AUTH_TOKEN` line. The log contains the "NO authentication" startup warning. Checks run from inside WSL:
  - `/healthz` returned 200.
  - `Host: evil.com` returned 421.
  - An `initialize` without an Authorization header returned `serverInfo` (firecrawl-fastmcp 3.27.3).
  - A POST with a bogus `Authorization: Bearer x` header was not rejected with 401 (the child returned 406 for the missing Accept header). So the gateway ignores and strips the header when auth is off.
- The Windows-side `curl` to `127.0.0.1:8768` returned connection failures (000) during this review, although it worked in rounds 1 and 2. The same requests succeed inside WSL and the container is healthy. This looks like transient WSL localhost forwarding on the host, not a code issue. It is worth re-checking from Goose.

## Spec conformance
- **Config** (`src/config.mjs:17-22,44`): unset or empty means `authToken: undefined`. A set value is validated (at least 32 chars, no `replace-me` prefix). Errors name the field only. OK.
- **Gateway** (`src/gateway.mjs:156`): the bearer check runs only when `config.authToken` is set. `Authorization` is stripped in both modes (line 81). Host guard, healthz, body cap and 404 are unchanged. OK.
- **Startup warning** (`src/main.mjs:55-56`): logged only when no token is set. OK.
- **`setup.sh`**: the default writes `.env` without a token. `--with-token` generates one, and `--force` makes a timestamped backup. `check_env` validates the token only when it is set. The `.ps1` twin forwards its arguments. OK.
- **`start.sh` and `print_goose_hint`**: the `headers:` block appears only with a token; otherwise a warning is printed. OK.
- **`smoke.sh`**: the 401 steps are skipped with a note when auth is off, and the raw-socket steps omit the Authorization line. OK.
- **Docs** (`README.md`, `CLAUDE.md`, `.env.example`): the tradeoff section, how to enable the token, and the env table are consistent with the amendment. No doc still claims the token is required.
- **Tests**: unset and empty config; no-token gateway with header stripped and warning logged; the existing token-set tests are unchanged.

## Issues

Minor only; none block.

1. **minor** `docs/SPEC.md:68,72-73,111`. Pre-amendment text (setup generates a token; smoke step 2 asserts 401; acceptance criterion 4 says unauthenticated `/mcp` returns 401). It is explicitly overridden by Amendment 1, so this is only a readability note.
2. **minor** `docker-compose.yml:36`. The comment says "the bearer token travels in clear text". It is still accurate when a token is set, but could add "(when set)". It is cosmetic.
3. **minor (environmental)** Windows-side `curl` to the published port failed intermittently during this review (see Verification).

No regressions found.

VERDICT: APPROVED
