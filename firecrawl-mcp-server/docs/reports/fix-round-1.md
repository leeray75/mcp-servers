# Fix round 1

Resolves the 8 minor issues in `review-round-1.md`.

| # | Change | Test |
|---|---|---|
| 1 | `run()` now registers SIGTERM/SIGINT handlers (injectable `proc`) right after spawning the child, before listen and the readiness wait. `main()` no longer registers them. A signal during startup shuts the child down gracefully and exits 0. | `signal during startup shuts the child down gracefully` |
| 2 | `server.listen` failure (e.g. EADDRINUSE) now SIGKILLs the already-spawned child, then rethrows. | `listen failure kills the already-spawned child and rejects` |
| 3 | Declared oversize `Content-Length`: 413 sent with `Connection: close`, request destroyed 500 ms after the response finishes (a short delay, because destroying immediately reset the connection before the client read the 413). The streamed-body 413 path uses the same delayed destroy. | `declared oversize Content-Length: 413 and connection closed` (raw socket) |
| 4 | `server.requestTimeout` set to 10 minutes with an explanatory comment (bounds uploads only; SSE responses unaffected). | covered by existing proxy/SSE tests |
| 5 | Redactor also matches the JSON-escaped form of each secret (`JSON.stringify(s).slice(1,-1)`). | `redacts JSON-escaped forms of secrets in log lines` |
| 6 | `smoke.sh` step 6 now also sends a complete `tools/call` (live scrape; `tools/list` with `--no-live`) and closes the socket without reading the response, i.e. aborts mid-response, then re-checks `/healthz`. The half-sent-POST abort is kept. The unit test for streaming abort already existed. | smoke |
| 7 | `/healthz` accepts GET and HEAD only; other methods get 405 with `Allow: GET, HEAD`. | `/healthz allows GET and HEAD only` |
| 8 | Accepted, no change: `.ps1` files are LF on disk; `.gitattributes` normalises them to CRLF if a repo is initialised. |  |

## Results

- `npm test`: 47 tests, 47 pass. Coverage of `src/`: 94.39% lines, 92.81% branches, 83.93% functions (gateway 96.49, main 88.96, redact 100, config 100).
- Container recreated with `scripts/restart.sh --recreate`; it became healthy and is published as `127.0.0.1:8768->8000/tcp`.
- `scripts/smoke.sh` passed every step (health, 401 without and with a wrong token, initialize, tools/list with `firecrawl_scrape`, live scrape containing "Example Domain", mid-response abort then health 200).
- Token grep over container logs: 0 matches.
