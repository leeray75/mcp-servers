# firecrawl-mcp-server — SDD Summary Report

Date: 2026-10-05

## Process
| Step | Actor | Output | Result |
|---|---|---|---|
| 1. Spec | Orchestrator | `docs/SPEC.md` | Written from the handoff (Option B), mirroring `sports-mcp-server` |
| 2. Implement | Implementer subagent | code, scripts, `docs/reports/implementation-report.md` | 42 tests pass, container healthy, smoke passes |
| 3. Review round 1 | Reviewer subagent | `docs/reports/review-round-1.md` | APPROVED with 8 minor issues (0 blocker, 0 major) |
| 4. Fix round 1 | Implementer subagent | `docs/reports/fix-round-1.md` | 7 fixed, 1 accepted (CRLF note) |
| 5. Review round 2 | Reviewer subagent | `docs/reports/review-round-2.md` | APPROVED, no regressions |

Loop used 2 of a maximum of 3 review rounds.

## What was built
`mcp-servers/firecrawl-mcp-server`: a Docker (WSL2) container running the pinned `firecrawl-mcp@3.27.3` in Streamable HTTP mode against the
self-hosted Firecrawl API (`http://api:3002` on the external `firecrawl_backend` network). A zero-dependency Node gateway adds bearer-token auth
(timing-safe), a Host-header guard, a 1 MiB body cap, a streaming proxy and `/healthz`. A supervisor manages the child process and shutdown.
Published on `127.0.0.1:8768` only, with a read-only rootfs, non-root user and all capabilities dropped. Scripts (`setup/start/stop/restart/rebuild/logs/status/smoke` with `.ps1` twins), README and CLAUDE.md follow the sibling servers.

## Verification
- `npm test`: 47/47 pass, `src/` coverage 94.4% lines.
- Live smoke passes: health 200, no/wrong token 401, `initialize`, `tools/list` (25 tools), live `firecrawl_scrape` of example.com against the self-hosted API, and a mid-response abort followed by a healthy check.
- Reviewer curl checks: Host guard 421, 404 for unknown paths, 413 for oversize bodies, `/healthz` GET/HEAD only.
- Token not found in container logs. Gmail (8765), vision (8766), sports (8767) and the Firecrawl stack are untouched.

## Open items (for you)
1. **Goose `config.yaml` was not modified.** To switch, replace `firecrawl-local` with the `streamable_http` snippet in the README (`uri: http://127.0.0.1:8768/mcp`, bearer header from `.env`), then retire the old `npx firecrawl-mcp` processes.
2. `firecrawl_search` against the self-hosted API is unverified (handoff caveat); keep the disabled cloud `firecrawl` entry as a fallback.
3. `npm audit` reports 3 high advisories in `axios`, a transitive dependency of `firecrawl-mcp` 3.27.3. They can't be fixed without leaving the pin; re-check on upstream releases.
4. `.ps1` twins were copied from sports but never executed.
5. firecrawl-mcp logs harmless FastMCP/Zod stack traces for health probes and aborted requests.
6. No git repo was initialised in the directory.
