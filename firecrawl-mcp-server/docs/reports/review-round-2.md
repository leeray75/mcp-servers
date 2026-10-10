# Review round 2: firecrawl-mcp-server

## Verification
- `npm test`: 47/47 pass; coverage of src/ is 94.39% lines. `node --check` passes.
- Live container (Up, healthy, published as `127.0.0.1:8768->8000/tcp`; token never printed):
  - `/healthz` GET and HEAD returned 200.
  - `POST /healthz` returned 405.
  - `/mcp` without a token returned 401.
  - A 2 MB body returned 413, both with and without `Expect: 100-continue`.
  - `/healthz` returned 200 immediately afterwards.

## Round 1 minors

| # | Status | Evidence |
|---|---|---|
| 1 | Fixed | `src/main.mjs:103-104` registers SIGTERM/SIGINT inside `run()`, before listen and the readiness loop. The loop exits when `shuttingDown` is set, and `doExit` is idempotent. `main()` no longer registers handlers. |
| 2 | Fixed | `src/main.mjs:106-115`: on a listen error the child is SIGKILLed, `shuttingDown` is set (so the child 'exit' handler does not double-exit), and the error is rethrown to `main()`, which exits 1. |
| 3 | Fixed | `src/gateway.mjs:86-90` and `126-127`: 413 with `Connection: close`; `req` is destroyed 500 ms after the response finishes, via an unref'd timer, so it neither holds the process open nor resets the connection before the client reads the 413. |
| 4 | Fixed | `src/gateway.mjs:168`: `requestTimeout` is 600000 ms with a comment. SSE responses are unaffected. |
| 5 | Fixed | `src/redact.mjs:8` also matches the JSON-escaped form of each secret. |
| 6 | Fixed | `smoke.sh` step 6 now also aborts mid-response. The unit test for streaming abort already existed. |
| 7 | Fixed | `src/gateway.mjs:146-153`: `/healthz` accepts GET and HEAD; other methods get 405 with `Allow`. |
| 8 | Accepted | No change needed. |

## Regressions checked
- No regressions found in the signal/startup logic. A listen failure leaves the signal handlers registered, but the process exits right after, so this is harmless.
- The delayed destroy only fires after `finish`; a late timer on an already-destroyed request is a no-op.
- The 401, 421 and 404 paths are unchanged, and the Authorization-strip behaviour is intact.
- Tests grew from 42 to 47 and cover each fix.

No new issues.

VERDICT: APPROVED
