# Summary report: container DNS resilience (Spec A)

| Step | Actor | Output | Result |
|---|---|---|---|
| Plan | human + orchestrator | decisions D1-D6 in `SPEC.md` | done |
| Spec approval | human | `SPEC.md` Approved 2026-11-14 | approved |
| Implement | implementer (Haiku) | code, tests, docs, `implementation-report.md` | gate green |
| Review round 1 | reviewer (Sonnet) | `review-round-1.md` | APPROVED, minors only |
| Minor fix | orchestrator | `CLAUDE.md` wording (m3) | done |

Review rounds used: 1 of 5.

## Built (spec requirement -> result)
R1 `dns:` on `gmail-mcp` and `auth` from `CONTAINER_DNS_1/2` (defaults 8.8.8.8, 1.1.1.1); R2 `netcheck.py` bounded,
secret-free DNS probe of `gmail.googleapis.com` and `oauth2.googleapis.com`; R3/R4/R5 `python -m gmail_mcp.healthcheck`
(`/healthz` 200 plus DNS ok; no Google HTTPS), identical in compose and Dockerfile; R6 README triage, `docs/SPEC.md`,
`CLAUDE.md`, `CHANGELOG.md` `[Unreleased]`, `.env.example`; R7 hardening unchanged; R8 `Settings` loads with the new vars.

## Verification
`make check` (WSL, run by implementer and again by reviewer): ruff, format, mypy --strict clean; 425 passed; coverage 99.2%.
Not re-run after the m3 doc-only edit.

## Deviations and accepted minors
- Compose `test:` rewritten in flow style so a stdlib-only test can parse it (PyYAML is not a dependency); the command is identical.
- Non-daemon executor threads can delay process exit when `getaddrinfo` hangs; Docker counts that as a failed check, so it cannot report false healthy. Accepted by the reviewer.
- m1 (`os._exit`) and m2 (4.1-4.5 s worst-case budget vs 5 s timeout) accepted: revisit if the check flaps. m4 (one uncovered defensive branch) accepted.

## Open items for the human
Live checks from spec section 7 are NOT done: rebuild the image, `cat /etc/resolv.conf`, `wsl --shutdown` and Tailscale
reconnect, and a blocked-resolver case turning the container `unhealthy`. If the outage still reproduces with `dns:` set,
the root-cause hypothesis is wrong and the spec needs amending. Nothing is committed, pushed or tagged.
