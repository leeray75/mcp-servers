# Container DNS resilience (gmail-mcp-server) — Specification

- **Status:** Approved — 2026-11-14, approved by: human
- **Date:** 2026-11-14
- **Amends:** `docs/SPEC.md` (container section) and the server `CLAUDE.md` invariant "network access is confined to
  one module". Precedes `docs/specs/network-failure-diagnostics/SPEC.md` (Spec B), which reuses this spec's probe.
- **Source:** `docs/reports/container-dns-outage/bug-report.md` (defects C1, C2, C5), corrected by the review below.

## 1. Problem / motivation

After a host sleep/hibernate or a Tailscale reconnect, the container lost name resolution and every Gmail call
failed until `docker restart gmail-mcp`. Docker kept reporting `(healthy)` because the healthcheck only probes the
container's own loopback. Compose sets no `dns:`, so the container inherits whatever resolver the host exposes.

Corrections to the bug report that shape this spec (verified against the code and the installed libraries):

- The logged `outcome: TransportError` is `google.auth.exceptions.TransportError`, raised while **refreshing the access
  token** at `oauth2.googleapis.com` (`google_auth_httplib2.py:121-122`), not at `gmail.googleapis.com`. Both hosts
  must resolve for the server to work, so both are probed.
- Plain Docker does **not** restart an `unhealthy` container; `restart: unless-stopped` reacts to process exit only.
  A DNS-aware healthcheck therefore makes the failure visible but does not heal it. Prevention is C1 (explicit DNS).
- The Dockerfile healthcheck reads `$PORT`; the compose one hardcodes `8000`, so the two are not identical today.
- The report attributes `10.255.255.254` to Tailscale; it is probably WSL2's DNS stub. The root cause chain is therefore
  a strong hypothesis, not proven. Section 7 makes the live check part of acceptance.

## 2. Goals and non-goals

**Goals:** (a) explicit, overridable container DNS; (b) a healthcheck that detects a dead resolver; (c) one shared
healthcheck definition for compose and Dockerfile; (d) a documented triage sequence.

**Non-goals:** restarting or self-terminating on failure (decision D2); an autoheal sidecar; any change to tools,
resources, schemas or error text (Spec B); new dependencies; changing the sign-in flow.

## 3. Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Spec split | Two specs: this one (config, healthcheck, docs) and Spec B (client errors, `auth_status`). |
| D2 | Healthcheck effect | **Label only.** `unhealthy` becomes truthful; nothing restarts the container. No exit-on-failure, no autoheal. |
| D3 | Container DNS | Overridable via `.env`: `CONTAINER_DNS_1` (default `8.8.8.8`) and `CONTAINER_DNS_2` (default `1.1.1.1`). Two variables because a Compose list cannot be built from one string. |
| D4 | Probe hosts | `gmail.googleapis.com` and `oauth2.googleapis.com` (the two hosts the server already contacts). |
| D5 | Probe mechanism | Stdlib `socket.getaddrinfo` only. No HTTPS, no TCP connect, so a Google outage never flips health. |
| D6 | Shared definition | Both healthchecks run `python -m gmail_mcp.healthcheck`; the logic lives in code, not in two inline strings. |

## 4. Requirements

- **R1** `docker-compose.yml`: services `gmail-mcp` and `auth` set `dns: ["${CONTAINER_DNS_1:-8.8.8.8}",
  "${CONTAINER_DNS_2:-1.1.1.1}"]` (via the shared `x-` anchor or per service). `.env.example` documents both as optional.
- **R2** New module `src/gmail_mcp/netcheck.py` (stdlib only; Spec B later adds an `httplib2` import for error
  classification, `httplib2` being an existing pinned dependency): `PROBE_HOSTS` (D4) and
  `resolve_hosts(hosts, timeout) -> DnsResult` returning per-host success and an overall `ok`. The call is bounded by
  `timeout` seconds in wall-clock time even if the resolver hangs (worker thread; the abandoned thread is acceptable).
  It never raises and never returns addresses, only booleans and exception class names.
- **R3** New module `src/gmail_mcp/healthcheck.py` with `main() -> int` and `python -m gmail_mcp.healthcheck`. It
  passes (exit 0) only when `GET http://127.0.0.1:$PORT/healthz` (timeout 2 s) returns 200 **and** `resolve_hosts` is ok. It does not
  import `config.Settings`, reads no secrets, and prints at most one content-free line on failure.
- **R4** `Dockerfile` `HEALTHCHECK` and the compose `healthcheck.test` both become
  `["CMD","python","-m","gmail_mcp.healthcheck"]` with unchanged interval, timeout, start period and retries. The
  `auth` service keeps `healthcheck: disable: true`.
- **R5** A Google HTTPS failure, a 4xx/5xx from Google or a slow Google response never changes health (D5).
- **R6** `README.md` gains a short network-triage section (logs via `scripts/logs.*`, then `auth_status`, then
  `scripts/restart.*`), the two new settings, and an honest note that `unhealthy` does not auto-restart the container.
  `docs/SPEC.md`, `CHANGELOG.md` `[Unreleased]` and the server `CLAUDE.md` (module list; network confinement now names
  `gmail_client.py` for Gmail/Google calls and `netcheck.py` for DNS resolution only) are updated.
- **R7** All hardening is unchanged: loopback-only port binding, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`,
  `init`, `pids_limit`, `mem_limit`, `cpus`, UID 10001, the `./data` bind mount.
- **R8** `.env` is passed into the container via `env_file`, so the new variables reach the process. `Settings` must
  still load with them present (they are not fields and must not break startup).

## 5. Design

Files: `docker-compose.yml`, `Dockerfile`, `.env.example`, `src/gmail_mcp/netcheck.py`, `src/gmail_mcp/healthcheck.py`,
tests, `README.md`, `docs/SPEC.md`, `CLAUDE.md`, `CHANGELOG.md`.

`resolve_hosts` runs the lookups in a `concurrent.futures.ThreadPoolExecutor` (one worker per host) and waits with a
single overall deadline, so total time is at most `timeout`, not `timeout × hosts`. Healthcheck budget: the HTTP probe
uses a 2 s timeout and the DNS probe 2 s, run in sequence, so the worst case is about 4 s, inside Docker's 5 s `timeout`.

## 6. Security and invariants

Outbound traffic added: DNS lookups for two hostnames the server already uses. No new destinations, no new listeners, no
new inbound routes, no secrets read. Port binding stays `127.0.0.1`; the Host/Origin guard and bearer auth are not
touched. The Google DNS default sends hostname lookups to public resolvers (visible to them); users who object set
`CONTAINER_DNS_1/2` to their own resolver. Split-DNS names (such as `*.ts.net`) will not resolve in the container by
default; the server needs none.

## 7. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| R1, R4, R7 | `tests/test_docs_and_config.py`: parse compose; `dns` present on both services with non-empty defaults; healthcheck `test` equals the Dockerfile `HEALTHCHECK` command; hardening keys, loopback port binding and the bind mount still asserted |
| R2 | `tests/test_netcheck.py`: resolves ok (monkeypatched `getaddrinfo`); `gaierror` gives not ok with class name only; a hanging resolver returns within the timeout; never raises |
| R3, R5 | `tests/test_healthcheck.py`: 200 + DNS ok exits 0; DNS failure exits 1; non-200 exits 1; `/healthz` unreachable exits 1; no network in tests |
| R6 | Reviewer reads the docs against the code |
| R8 | Test: `Settings` loads with `CONTAINER_DNS_1/2` set in the environment |
| Gate | `make check VENV=~/.venvs/gmail-mcp` (ruff, format, mypy --strict, pytest, coverage ≥ 85%) |
| Live (manual, recorded in the summary) | After rebuild: `docker exec gmail-mcp cat /etc/resolv.conf` shows the configured servers; container `healthy`; after `wsl --shutdown` and start, and after a Tailscale disconnect/reconnect, `search_threads` succeeds without a manual restart; a deliberately blocked resolver turns the container `unhealthy` within about 90 s. If the outage still reproduces with `dns:` set, stop and report: the root cause hypothesis is wrong and this spec needs amending |

## 8. Out of scope

`CONTAINER_DNS` for other servers; a `doctor` script; autoheal; exit-on-failure; client error classification and
`auth_status` changes (Spec B); publishing the OAuth consent screen; commit, push, tag.

## 9. Open questions

None.
