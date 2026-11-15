# Bug Report: Container DNS failure causes silent total Gmail outage; healthcheck and error text mask the cause

| Field | Value |
|---|---|
| **Component** | `docker-compose.yml`, `Dockerfile`, `src/gmail_mcp/gmail_client.py`, `src/gmail_mcp/auth_state.py` |
| **Version affected** | 0.2.0 (`pyproject.toml:7`, `src/gmail_mcp/__init__.py:3`) |
| **Severity** | High — complete loss of function, self-reported as healthy, misleading guidance to the calling agent |
| **Type** | Infra/config defect + diagnostic/observability defect |
| **Reporter** | goose (agent), 2026-11-14 |
| **Status** | Open. Workaround applied (container restart); root cause unaddressed |
| **Reproducible** | Yes — deterministic after host sleep/hibernate or Tailscale reconnect |

---

## 1. Executive summary

On Windows + WSL2 + Tailscale, the `gmail-mcp` container loses DNS resolution after the host
sleeps/hibernates or Tailscale reconnects. The container cannot resolve `gmail.googleapis.com`,
so **every** Gmail API call fails. Three separate mechanisms then conspire to make the failure
look like something it is not:

1. **Docker reports the container `(healthy)`** — its healthcheck only probes the container's own
   loopback endpoint and never exercises DNS or egress.
2. **`auth_status` reports `signed_in: true, next_step: "No action needed"`** — it is a purely local
   token inspection and never contacts Google. The documented agent contract
   (`src/gmail_mcp/resources.py:19-34`) instructs agents to gate retries on this payload, so it
   actively steers a correct-looking agent into retrying forever.
3. **The error text says "try again shortly"** — for a failure that never self-heals until the
   container is restarted.

Net effect: a hard outage that presents as healthy, authenticated, and transient. Diagnosing it
required ~10 exploratory commands; `scripts/logs.ps1` alone would have named it in one.

**The application logic is not at fault.** Auth, token handling, and the API client all behaved
correctly. The defects are in container network configuration and in how failures are classified
and surfaced.

---

## 2. Environment

| | |
|---|---|
| Host | Windows 11, user `hoyen` |
| Runtime | Docker Engine **inside WSL2 `Ubuntu`** (not Docker Desktop; `docker.exe` absent from Windows PATH) |
| Container | `gmail-mcp`, image `gmail-mcp-server:local`, `restart: unless-stopped`, UID 10001, `read_only`, `cap_drop: [ALL]` |
| Uptime at failure | ~36 h |
| VPN | Tailscale active — container `resolv.conf` carried `search taileaa4a8.ts.net`, host resolver `10.255.255.254` |
| Deps | `httplib2==0.32.0`, `google-api-python-client==2.201.0`, `google-auth==2.59.1`, `google-auth-httplib2==0.4.4` |

---

## 3. Observed symptoms

Every MCP tool (`search_threads`, `get_thread`) returned:

```
Error executing tool search_threads: Could not reach the Gmail API; try again shortly.
```

Persisted across 5 consecutive calls over ~2 minutes — not transient. Meanwhile:

- `auth_status` → `{"state":"signed_in","signed_in":true,"ready_to_retry":true,"token_age_days":3.84,"warnings":[],"next_step":"No action needed."}`
- `docker ps` → `gmail-mcp ... Up 36 hours (healthy)`

---

## 4. Root cause

```
WSL2 sleep/hibernate  (or Tailscale interface reconnect)
  └─> host upstream resolver (10.255.255.254, Tailscale) becomes unreachable / changes
       └─> Docker embedded DNS at 127.0.0.11 keeps forwarding to the stale address
            └─> in-container resolver: socket.gaierror [Errno -3] Temporary failure in name resolution
                 └─> httplib2 wraps it as TransportError
                      └─> gmail_client.py:136  except (GoogleAuthError, HttpLib2Error, OSError, TimeoutError)
                           └─> GmailApiError("Could not reach the Gmail API; try again shortly.")
```

The container had **no way to translate a hostname to an address**. The Windows host and the WSL
distro both resolved and connected normally — the breakage existed only inside the container's
network namespace, which is why layered host-side testing looked clean.

### Evidence

**1. Inside the container — DNS dead** (`docker exec -i gmail-mcp python - < gtest.py`):

```
CONTAINER_EGRESS_FAIL URLError <urlopen error [Errno -3] Temporary failure in name resolution>
DNS_FAIL gaierror [Errno -2] Name or service not known
```

**2. Inside the container — numeric IP (no DNS), pre-restart:**

```
TCP_FAIL 142.250.80.46 TimeoutError timed out
TCP_OK   8.8.8.8
```

Routing to Google's range was also broken on that path, while a general external TCP path worked.
This rules out "Google is down" and points squarely at the container's network namespace.

**3. Container resolver config — the stale forwarder:**

```
nameserver 127.0.0.11
search taileaa4a8.ts.net
options ndots:0
```

**4. Host and WSL distro both healthy** (proves the fault is container-scoped):

| Test | Result |
|---|---|
| Windows → `https://gmail.googleapis.com/gmail/v1/users/me/threads` | `401` (reachable, unauthenticated — correct) |
| WSL distro → same URL | `401` |
| WSL distro DNS `gmail.googleapis.com` | `OK` |
| WSL proxy env vars | none |

**5. Healthcheck cannot see the failure** — `docker-compose.yml` / `Dockerfile`:

```
["CMD","python","-c","import sys,urllib.request as u;
  sys.exit(0 if u.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"]
```

It probes its own loopback (`server.py:380`). It exercises no DNS, no egress, and no Google
endpoint. A fully offline container reports `healthy` indefinitely, so `restart: unless-stopped`
can never self-heal this.

**6. The server logged the truth all along:**

```json
{"timestamp":"2026-11-15T00:41:59...","level":"WARNING","logger":"gmail_mcp.gmail_client",
 "message":"gmail transport failure","outcome":"TransportError"}
```

5 occurrences, exactly matching the 5 failed tool calls. Emitted at `gmail_client.py:137`
via `logger.warning(..., extra={"outcome": type(exc).__name__})`.

### Resolution applied (workaround only)

```
docker restart gmail-mcp     # then ~18 s later, re-tested inside the container
CONTAINER_EGRESS status 401 (reachable; 401 expected)
DNS gmail.googleapis.com -> 172.217.118.4
```

`401` is the correct expected result: DNS resolves and Google answers. Restart re-reads the
resolver and is sufficient as a workaround. **It does not prevent recurrence.**

Credential durability was verified before restarting: the token lives on a bind mount
(`./data:/data` → `/mnt/c/Users/hoyen/Documents/Roo Code Workspace/mcp-servers/gmail-mcp-server/data`),
not the read-only rootfs, so a restart does **not** force re-authentication. Please preserve this
property in any fix.

---

## 5. Contributing defects (the actual fix work)

### C1 — No container DNS configuration · **P0**

`docker-compose.yml` `services.gmail-mcp` has **no `dns:` key**, so the container inherits the
host's resolver via Docker's embedded DNS. When Tailscale rewrites the host resolver across a
sleep cycle, the container's view goes stale and nothing recovers it.

**Requested change:** set explicit, sleep-stable `dns:` on the `gmail-mcp` service (and on the
`auth` one-off service, which shares the same `x-hardening` anchor and the same network
dependency). Recommend internal-to-WSL-safe public resolvers, e.g. `["8.8.8.8","1.1.1.1"]`, or a
documented `.env`-overridable `CONTAINER_DNS` list so enterprise/VPN users can substitute their own.

**Acceptance:**
- `docker exec gmail-mcp cat /etc/resolv.conf` shows the configured servers, not only `127.0.0.11`.
- After `wsl --shutdown` + restart, or a Tailscale disconnect/reconnect, `search_threads` still succeeds.
- Loopback-only port binding, `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`,
  `mem_limit`, `cpus` and UID 10001 are all unchanged (see §7).

### C2 — Healthcheck is blind to DNS/egress · **P0**

The healthcheck (`docker-compose.yml` `healthcheck.test` **and** the duplicated `HEALTHCHECK` in
`Dockerfile`) validates only that the local ASGI app answers. It cannot distinguish "server up and
usable" from "server up, network dead" — the exact state that occurred.

**Requested change:** have the healthcheck additionally prove the resolver works. Prefer
`socket.getaddrinfo("gmail.googleapis.com", 443)` (stdlib) over a full HTTPS fetch, because it
tests the thing that broke while **not** flapping when Google itself is briefly down. Keep the
existing loopback probe as the "app is alive" check.

⚠️ **Do not make the healthcheck depend on a successful Google HTTPS round-trip.** Coupling
restart decisions to Google's availability converts a Google outage into a container restart loop —
strictly worse than the current bug. If you want both signals, separate them: let the healthcheck
gate on DNS + local liveness, and expose egress as a distinct diagnostic (C4).

Note the two definitions must stay in sync; consider making one authoritative and having a test
assert they match (see `tests/test_docs_and_config.py`).

**Acceptance:**
- With DNS deliberately broken (e.g. `docker exec` a resolver-blocked namespace, or a unit-level
  harness), the healthcheck transitions to `unhealthy` within `interval × retries` (30 s × 3).
- With Google reachable-but-slow or momentarily erroring, the healthcheck stays `healthy`.
- `docker-compose.yml` and `Dockerfile` healthchecks agree (asserted by a test).

### C3 — Catch-all exception handler misclassifies and misdirects · **P1**

`src/gmail_mcp/gmail_client.py:136-138`:

```python
except (GoogleAuthError, httplib2.HttpLib2Error, OSError, TimeoutError) as exc:
    logger.warning("gmail transport failure", extra={"outcome": type(exc).__name__})
    raise GmailApiError("Could not reach the Gmail API; try again shortly.") from None
```

Three problems:

1. **DNS failures are collapsed into "try again shortly."** `socket.gaierror` is an `OSError`, so
   it lands here. The advice is wrong and directive: the condition never clears on its own. This
   phrasing caused a compliant agent to retry pointlessly.
2. **The classification signal is thrown away.** `type(exc).__name__` is known and logged, but
   `from None` plus the fixed message discards it for the MCP client — the only surface the agent
   actually sees.
3. **`num_retries=READ_RETRIES` (`gmail_client.py:31`, value `2`) provides no protection here.**
   `google-api-python-client` retries HTTP 5xx/429, not name-resolution failures. Worth stating in
   a comment so nobody assumes transport errors are covered.

**Requested change:** split DNS/resolver failures from genuinely transient transport errors.
Detect `socket.gaierror` (and the DNS-flavoured `httplib2` `TransportError`), and raise a distinct
`GmailMcpError` subclass whose `user_message()` states the diagnosis and the remedy, e.g.:

> `NETWORK_UNREACHABLE: The container could not resolve gmail.googleapis.com. This does not clear
> on its own — do not retry. Restart the container: scripts/restart.ps1 (Windows) / scripts/restart.sh.`

Keep the generic transport branch for real transient errors, where "try again shortly" is honest.
Preserve `from None` so no Google-internal detail leaks, and keep log messages free of query
content per existing convention.

**Acceptance:**
- Unit test: raising `socket.gaierror` from the fake service yields the new DNS-specific message
  and a stable `outcome` in the log.
- Unit test: raising a plain transient `HttpLib2Error` still yields the existing retry-later message.
- Existing `AuthRequiredError` / `RefreshError` / `HttpError` branches and `_clear_rejection()`
  semantics unchanged (`tests/test_gmail_client.py`, `tests/test_gmail_rejection.py`,
  `tests/test_rejection_recovery.py` still pass).

### C4 — `auth_status` omits connectivity, contradicting its agent contract · **P1**

`AuthStatus` (`src/gmail_mcp/auth_state.py:40-55`) is a frozen Pydantic model of purely local facts
(`signed_in`, `ready_to_retry`, `token_age_days`, `rejected_at`, `warnings`, `next_step`, …). It
never contacts Google. Yet `src/gmail_mcp/resources.py:19-34` documents it as the gate an agent
should trust before retrying. During this outage it said `next_step: "No action needed."` while the
server was 100% non-functional — technically accurate, practically misleading.

**Requested change:** add a bounded connectivity probe to the payload, e.g.
`transport: "reachable" | "dns_failure" | "unreachable" | "not_checked"`, backed by a
`socket.getaddrinfo` resolution of `gmail.googleapis.com` with a short (~2 s) timeout, plus an
optional TCP `:443` connect. Update `next_step`/`warnings` so that a DNS failure yields an explicit
"restart the container, do not retry" instruction — mirroring how `AUTH_REQUIRED` already yields
"sign in, do not retry."

This is the highest-leverage fix for agent behaviour: it makes the single tool agents are told to
trust able to detect this class of failure.

**Acceptance:**
- `auth_status` during a deliberately-broken-resolver state returns `transport: "dns_failure"` and a
  `next_step` naming `scripts/restart.*`.
- Probe timeout is bounded; a hung resolver cannot stall `auth_status`.
- Model stays `frozen`; no secrets, tokens or query content in any field.
- `tests/test_auth_status_and_resources.py` extended; the documented contract in `resources.py`
  updated in the same change so doc and behaviour cannot drift.

### C5 — Operational: the diagnostic path is undocumented · **P2**

The server already logs `outcome: <ExceptionType>` per transport failure, which pinpoints this
failure in one command. Neither `README.md` nor the agent-facing resource text mentions
`scripts/logs.ps1` / `scripts/logs.sh` as the first move when a tool returns
"Could not reach the Gmail API."

**Requested change:** document the triage sequence (logs → `auth_status` → restart) in `README.md`,
and add the log pointer to the new C3 error message.

---

## 6. Impact

- **Availability:** 100% loss of Gmail tooling until manual container restart. Silent to Docker,
  `auth_status`, and the error text.
- **Agent trust:** the two authoritative signals (healthcheck, `ready_to_retry`) both reported
  healthy during a total outage, training users and agents to distrust or bypass them.
- **Diagnostic cost:** ~10 explorations instead of 1, because the correct classification existed
  only in a log line no surface exposed.
- **Recurrence:** certain. Every sleep/hibernate cycle on this host re-arms it.

---

## 7. Non-goals / invariants to preserve

Do **not** change any of the following while fixing this. They are the server's security posture
(`CLAUDE.md`, `docker-compose.yml` header comment):

- Port binding stays `127.0.0.1:${HOST_PORT}:8000`. **Never** `0.0.0.0`.
- `read_only: true`, `cap_drop: [ALL]`, `security_opt: no-new-privileges:true`, `init: true`,
  `pids_limit: 128`, `mem_limit: 512m`, `cpus: 1.0`, UID/GID `10001:10001`.
- Token persistence stays on the `./data` bind mount (so fixes never force re-auth).
- The `Host`/`Origin` DNS-rebinding guard and bearer auth (`src/gmail_mcp/security.py`,
  `server.py:401`) — note: adding a DNS/egress probe must not weaken this. Outbound probing is fine;
  inbound header checks stay.
- The "never use browser automation on the sign-in page" instruction in `errors.py:28-33`.
- No new outbound destinations beyond `gmail.googleapis.com` (and an NTP-free clock assumption);
  this server's tool set and network footprint are deliberately fixed.

**Dependency constraint:** `Dockerfile` installs with `pip install --require-hashes
--only-binary=:all:`. **Adding a new dependency requires re-hashing `requirements.txt` and is to be
avoided.** Prefer stdlib `socket`. (`dnspython==2.8.0` is present in `requirements.txt` but only as
a transitive dep via `email-validator` — it is not imported anywhere in `src/`. Do not treat it as a
blessed direct dependency without an explicit decision.)

---

## 8. Test plan

| Ref | Test | Where |
|---|---|---|
| R-C3a | `socket.gaierror` from fake service → DNS-specific message, `outcome=gaierror` logged | `tests/test_gmail_client.py` |
| R-C3b | Transient `HttpLib2Error` → existing retry-later message | `tests/test_gmail_client.py` |
| R-C4a | Broken resolver → `auth_status.transport == "dns_failure"` + restart `next_step` | `tests/test_auth_status_and_resources.py` |
| R-C4b | Probe timeout bounded (mocked hanging resolver) | `tests/test_auth_status_and_resources.py` |
| R-C2a | compose + Dockerfile healthcheck definitions identical | `tests/test_docs_and_config.py` |
| R-C1a | compose `dns:` present and non-empty; hardening keys still present | `tests/test_docs_and_config.py` |
| R-reg | Full existing suite + coverage gate unchanged | `make check` |
| Live | `wsl --shutdown` → `scripts/start.ps1` → `search_threads` succeeds | manual |
| Live | Tailscale disconnect/reconnect → tool still succeeds without manual restart | manual |

Run `make check`, and `make audit` if any dependency changes.

---

## 9. Process

This repo is governed by `AGENTS.md` (spec-driven development). Per §1, this bug report is a
documentation-only artifact and is exempt; **the fixes in §5 are not.** They require a spec under
`docs/specs/<feature>/SPEC.md`, an explicit human approval at the gate, then implementer and
reviewer passes with reports under `docs/reports/<feature>/`, before any code changes.

Suggested spec scope: one feature covering C1–C4 (`container-network-resilience`), with C5 folded in
as the docs update.

### Suggested commit-splitting

1. `fix(compose): pin container DNS` — C1
2. `fix(healthcheck): probe DNS resolution, keep local liveness` — C2
3. `fix(client): distinguish DNS failures from transient transport errors` — C3
4. `feat(auth_status): report transport health` — C4
5. `docs: add network triage guide` — C5

### Reproduction / verification helpers used

Scratch (safe to delete): `C:\Users\hoyen\gtest.py` (DNS + HTTPS probe),
`C:\Users\hoyen\gtest2.py` (numeric-IP TCP, isolates DNS from routing). Both were piped in via
`docker exec -i gmail-mcp python -` because the container rootfs is read-only. Suggest promoting
equivalents into `scripts/` as a supported `doctor` command so the next occurrence is self-serve.

### Key one-liners for the fixer

```bash
# the log line that names this bug
docker logs gmail-mcp 2>&1 | grep 'transport failure'

# in-container DNS/egress truth (rootfs is read-only -> pipe via stdin)
docker exec -i gmail-mcp python - < probe.py

# what the container's resolver actually says
docker exec gmail-mcp cat /etc/resolv.conf

# verify a fix: 401 == DNS resolves and Google is reachable
docker exec -i gmail-mcp python -c "import urllib.request as u,urllib.error as e
try: u.urlopen('https://gmail.googleapis.com/gmail/v1/users/me/threads')
except e.HTTPError as x: print('OK', x.code)"
```
