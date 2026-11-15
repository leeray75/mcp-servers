# Implementation report: container DNS resilience (gmail-mcp-server)

- **Spec:** `docs/specs/container-dns-resilience/SPEC.md` (Approved, 2026-11-14)
- **Scope:** R1-R8 only. No dependencies added; `requirements*.txt` untouched. Nothing committed, pushed or tagged.
- **Docker was not run.** Live checks (section 7 of the spec) remain for the human after a rebuild.

## Requirement -> implementation -> test

| Req | Implementation | Test |
|---|---|---|
| R1 | `docker-compose.yml`: `dns: ["${CONTAINER_DNS_1:-8.8.8.8}", "${CONTAINER_DNS_2:-1.1.1.1}"]` on `gmail-mcp` and `auth` (per service). `.env.example`: both variables documented, commented out. | `test_docs_and_config.py::test_compose_sets_dns_on_both_services[gmail-mcp|auth]`, `test_env_example_documents_container_dns` |
| R2 | `src/gmail_mcp/netcheck.py`: `PROBE_HOSTS`, `HostResult`, `DnsResult`, `resolve_hosts(hosts, timeout)`. Concurrent `getaddrinfo` in a `ThreadPoolExecutor`, one overall `concurrent.futures.wait` deadline, `shutdown(wait=False, cancel_futures=True)`. Errors recorded as class names (`TimeoutError` for unfinished lookups). Addresses discarded. Outer `except` keeps it non-raising. | `tests/test_netcheck.py`: all-resolve, `gaierror` class-name only, no addresses in output, hanging resolver returns within timeout, unexpected exception never raises, empty host list, stdlib-only imports |
| R3 | `src/gmail_mcp/healthcheck.py`: `main() -> int`, `python -m gmail_mcp.healthcheck`. GET `http://127.0.0.1:$PORT/healthz` (2 s timeout) must return 200 and `resolve_hosts(timeout=2.0).ok` must be true. Exit 0 or 1. One stderr line on failure with booleans only. No import of `config`. | `tests/test_healthcheck.py`: healthy exits 0 and no output, PORT from env, default 8000, DNS failure exits 1 with the exact line, non-200, unreachable, `HTTPError`, `BadStatusLine`, non-numeric port, no `config` import or secret names in source |
| R4 | `Dockerfile` `HEALTHCHECK` and compose `healthcheck.test` both `CMD python -m gmail_mcp.healthcheck`; interval/timeout/start period/start interval/retries unchanged. `auth` keeps `disable: true`. | `test_compose_healthcheck_matches_dockerfile_command`, `test_dockerfile_healthcheck_timings_unchanged`, `test_compose_healthcheck_timings_unchanged`, `test_auth_service_keeps_healthcheck_disabled` |
| R5 | Only DNS and the local `/healthz` are probed. No HTTPS or TCP to Google, so a Google failure cannot change health. | `test_netcheck.py` (no HTTPS/connect path exists); `test_healthcheck.py` checks only the local URL is opened |
| R6 | `README.md`: two settings in the configuration table, "Network triage" section (logs -> `auth_status` -> restart; `unhealthy` does not auto-restart). `docs/SPEC.md` container section. `CLAUDE.md`: `netcheck.py` and `healthcheck.py` in the module list, `gmail_client.py` named as the Google-calling module. `CHANGELOG.md` `[Unreleased]`: Added and Changed. | Reviewer reads against code (spec section 7). Not tested automatically. |
| R7 | Hardening keys, `cap_drop`, `read_only`, loopback port, `./data` mount, `USER 10001:10001` unchanged. | `test_compose_hardening_and_loopback_binding_preserved` |
| R8 | `env_file: .env` unchanged. `Settings` already ignores unknown variables (`extra="ignore"`). | `test_settings_load_with_container_dns_variables` |

## Deviations

None from the spec's requirements. Notes:

1. **Compose parsing without PyYAML.** The spec says "parse compose YAML". PyYAML is neither in `requirements-dev.txt` nor in the venv, and the instructions forbid adding dependencies. The compose tests therefore use a small stdlib extractor over the fixed structure. The `dns:` and `test:` values are flow-style lists (valid JSON), decoded with `json.loads`. The `test:` value was changed from block style to flow style to allow this. The command is unchanged.
2. **Interpreter exit waits for a hung lookup.** `ThreadPoolExecutor` workers are non-daemon, and Python joins them at interpreter exit. A `getaddrinfo` that hangs past the 2 s budget therefore delays the healthcheck process exit. `resolve_hosts` itself still returns on time. Docker's 5 s timeout then marks the probe failed, which is the intended result, so the spec's behaviour holds. The alternative (daemon threads) departs from the spec's design section, so it was not changed. Flagged for review.

## Commands run

| Command | Result |
|---|---|
| `MSYS_NO_PATHCONV=1 wsl.exe -e bash -lc "... make check VENV=~/.venvs/gmail-mcp"` (final run) | Pass. ruff check: all checks passed. ruff format: 84 files already formatted. mypy --strict: no issues in 41 source files. pytest: 425 passed. Coverage 99.20% (gate 85%). |
| `ruff format .` and `ruff check --fix .` (formatting and import order only) | Applied to the new test and source files. Re-verified by the final gate run. |

Lint and typecheck fixes during the run: long lines wrapped, `HTTPError` headers argument set to `email.message.Message()`, Dockerfile line-continuation regex corrected in the test.

## Files changed

- `src/gmail_mcp/netcheck.py` (new)
- `src/gmail_mcp/healthcheck.py` (new)
- `Dockerfile`, `docker-compose.yml`, `.env.example`
- `tests/test_netcheck.py` (new), `tests/test_healthcheck.py` (new), `tests/test_docs_and_config.py` (appended)
- `README.md`, `docs/SPEC.md`, `CLAUDE.md`, `CHANGELOG.md`
- `docs/specs/container-dns-resilience/implementation-report.md` (this file)

## Outstanding (not run here)

Live checks from spec section 7: rebuild, `docker exec gmail-mcp cat /etc/resolv.conf`, container `healthy`, `wsl --shutdown` and Tailscale reconnect with `search_threads` succeeding, and a blocked resolver turning the container `unhealthy` within about 90 s.
