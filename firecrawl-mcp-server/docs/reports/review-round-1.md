# Review round 1: firecrawl-mcp-server

Scope: every section of docs/SPEC.md, the implementation report, and the sibling sports-mcp-server for house style.

## Verification performed

- `npm test`: 42/42 pass, no network or Docker. Coverage: src 92.88% lines (config 100, gateway 93.94, main 87.59, redact 100). This meets the 85% target.
- Live checks with curl against http://127.0.0.1:8768 (token read from .env, never printed):
  - `/healthz` returned 200.
  - `/mcp` with no token returned 401 with `WWW-Authenticate: Bearer` and the body `{"error":"unauthorized"}`.
  - A lowercase `bearer <token>` scheme returned 401.
  - `Host: evil.com` returned 421.
  - `/other` returned 404.
  - A 2 MB POST returned 413.
  - An authenticated `initialize` returned `serverInfo` firecrawl-fastmcp 3.27.3 over SSE.
- The `.ps1` twins are byte-identical in structure to sports'. `scripts/lib/Invoke-WslScript.ps1` is identical to sports'.
- I did not run the `.ps1` scripts or the Docker build. I read the Dockerfile and compose file against the spec instead.

## Spec conformance

| Spec item | Result |
|---|---|
| Gateway auth: SHA-256 digests with `timingSafeEqual`, always compared; 401 shape with `WWW-Authenticate`, no detail | OK |
| `Authorization` stripped upstream (also `proxy-authorization` via the hop-by-hop list); Host rewritten | OK |
| Host guard (421) derived from `PUBLIC_PORT`, plus port 8000 | OK |
| Streaming proxy with SSE test, status and query preserved, 404 for other paths | OK |
| Abort propagation (`res` close and `req` aborted destroy upstream), 502 on child failure | OK |
| Body cap: declared Content-Length and streamed bytes both give 413 | OK |
| Supervisor: injectable spawn and exit, non-zero exit on child death, SIGTERM then SIGKILL grace, readiness poll with timeout, redacted `[firecrawl-mcp]` prefix | OK |
| Config: every rule in the spec table, errors name fields only, `config.mjs` is the only reader of `process.env` | OK |
| Dockerfile: multi-stage, `npm ci --omit=dev --ignore-scripts`, UID 10001, node-based HEALTHCHECK, digest-pin comment | OK |
| Compose: loopback-only publish with a warning comment, external `firecrawl_backend`, read-only rootfs, tmpfs, caps dropped, no-new-privileges, init, limits, log rotation | OK |
| Scripts: network check in start, restart and rebuild; setup generates the token with openssl; smoke covers steps 1-5 plus an abort check | OK, with the minors below |
| README and CLAUDE.md match the code | OK |

## Issues

No blocker or major issues were found.

### Minor

1. **minor** `src/main.mjs:354-355`. SIGTERM and SIGINT handlers are registered only after `run()` resolves. `run()` can wait up to `CHILD_START_TIMEOUT_SECONDS` (30 s) for readiness. A signal during that window uses Node's default action, which exits immediately and skips the graceful child shutdown.
   Fix: have `run()` return a handle early (or accept an `onShutdownReady` callback), or register the signal handlers before `await run(...)`. For example, declare `let shutdown` and call `process.on(sig, () => shutdown?.(sig))` before awaiting.

2. **minor** `src/main.mjs:320-324`. If `server.listen` fails (for example EADDRINUSE), `run()` rejects after the child has already been spawned. `main()` then exits 1 without killing the child.
   Fix: wrap the listen step in try/catch, call `child.kill('SIGKILL')`, then rethrow.

3. **minor** `src/gateway.mjs:139-144`. For a declared oversize Content-Length the 413 is sent and the body is drained with `req.resume()` without limit. The 413 path for streamed bodies destroys the request after the response, but this one does not. An authenticated client could make the server read an arbitrarily large body.
   Fix: send the 413 with `Connection: close` and call `res.once('finish', () => req.destroy())`, mirroring lines 177-179.

4. **minor** `src/gateway.mjs:215`. `server.requestTimeout = 0` disables the body-upload timeout. Slow bodies on authenticated requests are bounded only by the socket. Acceptable for a loopback-only server, but worth a comment, or setting `server.setTimeout`/a generous `requestTimeout` (for example 10 minutes) so long-lived SSE responses are unaffected.

5. **minor** `src/redact.mjs:19`. `redact` is applied to the JSON-serialised log line. A secret containing `"` or `\` would be escaped by `JSON.stringify` and would not match. The token is hex in practice, so this is low risk.
   Fix: redact field values before stringifying, or also redact `JSON.stringify(secret).slice(1, -1)`.

6. **minor** `scripts/smoke.sh:127-142`. Spec criterion 5 asks for aborting a *streaming* request. Step 6 aborts a half-sent POST instead, so it exercises request-body abort and not response-stream abort. The unit test `client abort propagates to the child` covers the streaming case.
   Fix: open `GET /mcp` with the token and `Accept: text/event-stream`, read the headers, close the socket, then re-check `/healthz`. Alternatively document the deviation, which is already in the implementation report as item 3.

7. **minor** `src/gateway.mjs:198`. `/healthz` and `/mcp` match any HTTP method, including POST and HEAD. This is harmless. Restricting `/healthz` to GET and HEAD would match the spec wording.

8. **minor** `docs/reports/implementation-report.md` and `scripts/*.ps1`. `.gitattributes` says `*.ps1 eol=crlf`, but there is no git repo and the files on disk are LF. The files are currently LF only, which PowerShell handles. If a git repo is later initialised they will be normalised to CRLF, which is fine. No action needed beyond awareness.

## Notes

- Test quality is good: injectable seams, a fake child that exercises SSE, die, hang and header echo, and no network use. Gaps are only the `main()` entry wiring and the `probeChild` timeout branch, both disclosed in the report.
- The `npm audit` axios advisories are documented in the README and report. They are not fixable while the pin stands.

VERDICT: APPROVED
