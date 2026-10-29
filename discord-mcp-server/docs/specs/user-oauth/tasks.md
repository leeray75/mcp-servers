# Tasks and traceability skeleton: user OAuth mode (amendment A3)

Derived from `SPEC.md` in this folder. The implementer completes the traceability table in `implementation-report.md`; this file
is the plan, not a record.

## Work breakdown

| # | Task | Requirements | Main files |
|---|---|---|---|
| T0 | SDK gate test: per-request capabilities, protocol version and `InputRequiredResult` URL elicitation in stateless JSON mode. Stop and report if it fails. | §6.3 | `tests/test_oauth_sdk_gate.py` |
| T1 | Settings and cross-field validation; `.env.example` | U1-U3 | `config.py`, `.env.example` |
| T2 | `oauth.py`: pending flow (state, PKCE verifier, TTL, injectable clock/RNG), authorize and invite URL builders, token store (atomic 0600), `UserAuth` (token, refresh lock, `last_error`), secret registration | U8-U10, U19, U21, U25 (lock), U26 | `oauth.py` |
| T3 | `discord_client.py`: `post_form` limited to the two OAuth URLs, user-side client with its own `RateLimiter`, Bearer per request, OAuth error mapping, no POST retries except 429 | U22-U24 | `discord_client.py` |
| T4 | Refresh logic (pre-expiry and on 401, serialized, persist before use, invalid_grant clears) | U24-U25 | `oauth.py`, `service.py` |
| T5 | Service methods for the five tools; `bot_present` from the existing bot guild-list fetch | U16-U21 | `service.py`, `models.py`, `project.py` |
| T6 | Plan and registration: tool order, annotations, application-id mismatch, boot line | U4-U7, U28 | `capabilities.py`, `server.py` |
| T7 | Routes `/oauth/start`, `/oauth/callback` (state/iss/expiry checks, exchange, `/users/@me`, pages, headers) and the bearer exemption only when on | U8-U15, U27 | `server.py`, `security.py` |
| T8 | Connect elicitation (2026-07-28 + `elicitation.url` + kill switch) and fallback result with open commands | U17 | `server.py` |
| T9 | Tests for every requirement (SPEC §8); fake transport handles the two POST URLs; coverage ≥ 90% | all | `tests/` |
| T10 | `docker-compose.oauth.yml`, Dockerfile `/data`, `.gitignore`, `scripts/lib` compose-file selection, `setup.sh` prompt, `smoke.sh` | U30-U33 | container + scripts |
| T11 | Docs: README section, CHANGELOG, server `CLAUDE.md`, `docs/SPEC.md` §18, root `CLAUDE.md` + `CHANGELOG.md` | U34-U35 | docs |
| T12 | Gate (`make check`, `make audit` if deps change, `docker compose ... config` for both file sets) and `implementation-report.md` | AC-U1, AC-U2 | |

Order: T0 first (it can stop the build), then T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8, tests alongside each, then T10 -> T11 -> T12.

## Traceability table (to be completed by the implementer in `implementation-report.md`)

| Req | Implementation (file::symbol) | Test (file::name) |
|---|---|---|
| U1 … U35, SDK gate, AC-U1, AC-U2 | | |
