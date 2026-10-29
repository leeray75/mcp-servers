# Review round 1: user OAuth guild discovery (amendment A3)

- **Reviewer:** independent, read-only (no code edited). **Date:** 2026-10-29.
- **Inputs read:** `AGENTS.md` section 7, root and server `CLAUDE.md`, `docs/SPEC.md`, `docs/specs/user-oauth/SPEC.md`
  (wins), `tasks.md`, all changed source, the new tests, scripts, compose override, docs. `implementation-report.md` was
  read last and not trusted.
- **Verdict: APPROVED** (0 blockers, 0 majors, 11 minors, 4 spec issues). Each minor must be fixed or explicitly accepted
  with a written reason in `fix-round-1.md`; none blocks approval.

## Gate (my own run)

Command: `wsl.exe -e bash -lc 'cd ".../discord-mcp-server" && make check VENV=~/.venvs/discord-mcp'`

| Step | Result |
|---|---|
| ruff format --check, ruff check, mypy --strict | passed (make check exited 0) |
| pytest | **594 passed** in 41.8 s |
| Coverage (branch, gate 90%) | **96.50%** (oauth.py 99%, config.py 100%, server.py 96%, service.py 95%, discord_client.py 96%) |
| Pre-existing tests unmodified (AC-U2) | `git diff --stat -- tests` is empty; all seven test files are untracked additions. Confirmed. |
| `bash -n` all `scripts/*.sh`, `scripts/lib/*.sh` | ok |
| Compose selection helper (stubbed `docker`, temp env file) | `true`/`"true"` add both `-f` files; `False` and missing file use the base call; value never printed. ok |
| `docker compose -f docker-compose.yml -f docker-compose.oauth.yml config -q` | valid; `docker-compose.yml` untouched (not in git status) |
| `.gitignore` | `git check-ignore data/x` matches `data/` |
| Live Discord docs (`developers/topics/oauth2`) | Confirms: no documented PKCE parameters (kill switch correct), Basic auth or body credentials accepted, `iss=https://discord.com` on every redirect, revoke response unspecified (so treating any 2xx as success is right). |

New test count is 140 (3+22+13+31+28+43), not the 137 the implementation report states (minor doc slip, m8).

## Requirement verification (read from code, not from the report)

U1-U4 config/plan: ok. U5-U7 order, annotations, budget, unknown-arg rejection: ok, pinned by tests. U8-U15 routes: authorize
parameter order and encoding exact (`oauth.py:80-92`); PKCE S256 base64url no padding, RFC 7636 vector tested; verifier is 64
chars from the unreserved set via `secrets`; state is `token_urlsafe(32)`; `claim()` always consumes the flow, compares with
`hmac.compare_digest`, expiry `>=` 600 s on an injectable clock; `iss` exact; refusals send no Discord request (tested);
pages are fixed text with no-store, no-referrer and `default-src 'none'`; bearer exemption is GET-only, passed in only when on,
Host/Origin guard outermost and still effective (tested 421/403); off: routes absent, 401 behind bearer. U16-U21 tools: status
local-only; connect never sends or creates state; elicitation needs `2026-07-28` + `elicitation.url` + kill switch and the URL is
exactly `settings.sign_in_url`; disconnect always forgets and clears the pending flow; `list_my_guilds` pages 200 x 5, derived
flags correct (`1<<3 | 1<<5`, owner), `bot_present` null and `bot_guilds_known=false` on any `DiscordMcpError`, no caching of
user results, counts omitted when not asked; invite URL parameter order exact, `66688` = VIEW_CHANNEL + VIEW_AUDIT_LOG +
READ_MESSAGE_HISTORY. U22-U25 client: `httpx` still imported only in `discord_client.py`; `post_form` is the only POST, URLs
derived from the API base, form body, Basic auth, no redirects, size cap, no retry except 429; bot client cannot POST (tested);
separate `RateLimiter` per client, user 401 does not trip a hard stop, bot hard-stop does not block the user client (tested
both directions); refresh is serialized with re-check, persisted before use (`install` saves then assigns), `invalid_grant`
clears, 5xx/transport keeps the token; only the OAuth `error` code is read, `error_description` never. U26-U29: client secret,
tokens, verifier, state, code registered for redaction; log test over a full fake flow; `/healthz` untouched. U30-U35: ok (see
issues for details). SDK gate (3 tests) passes.

### Judgement of the implementer's self-declared additions

| Addition | Verdict |
|---|---|
| Best-effort revoke after a failed connect (scope missing, malformed profile, token not storable) | Beyond U13, which names only the `/users/@me` failure. Defensible and safe, but spec-silent. Accept and record in the spec (m2). |
| Empty-body / any-2xx revoke counts as success | Required by Discord's undocumented revoke response. Accept. |
| Shared `_guild_pages` helper | Required by U20 ("the path and cache `list_guilds` uses"); behavior-preserving (existing tests pass untouched). Accept. |
| Smoke check "at most 19 and five account tools present" | Matches U33 "19 or 19 minus omissions". Accept. |
| Disconnect clears pending flow | **Not an addition**: it is stated in U18. |
| `test_oauth_sdk_gate` gate (c) legacy handshake | Extra test, harmless. Accept. |
| Boot line always carries `user_oauth=off` when off | Required by U28; the boot-line text for off mode changes, no pre-existing test pins it. Fine. |

## Issues

### Blockers
None.

### Majors
None.

### Minors

- **m1 Settings validated even when the switch is off** (`config.py:66-71`, `host_port` with `ge/le`, plus the two bool
  kill switches). U3 says none of the new settings is read for behavior when off; a malformed `HOST_PORT`,
  `DISCORD_OAUTH_PKCE` or `DISCORD_OAUTH_URL_ELICITATION` in `.env` now stops an otherwise unchanged off-mode server booting.
  Fix: parse these leniently when off (or accept as `str` and validate in `_check_user_oauth` only when on), or accept the
  risk and amend U3 (see S3).
- **m2 Revokes beyond the spec** (`service.py` `_exchange_and_store`: scope check, malformed `/users/@me`, `install` failure).
  Fix: keep, and add them to U13 in the spec; the implementation report should stop saying "nothing outside the spec was added".
- **m3 Previous grant revoked after the new token exists** (`service.py` `_exchange_and_store`, `previous = ...; _revoke(previous)`).
  See S1; mitigation suggested there.
- **m4 `_run_user` duplicates `_run`** (`server.py` `_run_user` vs `_run` at ~479). Fix: widen `_run`'s `call` type to
  `BaseModel | InputRequiredResult` and delete `_run_user`, so the budget/error/log behavior cannot drift.
- **m5 Token deletion errors escape** (`oauth.py` `TokenStore.delete` suppresses only `FileNotFoundError`; `UserAuth.forget`
  deletes before clearing memory; `_refresh_locked` the same). A `PermissionError` makes `disconnect_discord_account` an internal
  error while the token stays in memory, contradicting "always deletes" (U18). Fix: clear `self.token` first, then delete; map
  `OSError` to `DiscordMcpError("unavailable", ...)`.
- **m6 `disconnect` does not take the refresh lock** (`service.py` `disconnect`, `oauth.py` `_lock`). A refresh finishing
  concurrently calls `install()` after `forget()` and resurrects the token file. Fix: run `forget` (and the revoke read of the
  token) under `UserAuth._lock`, e.g. a `UserAuth.disconnect_locked()` helper.
- **m7 Unbounded redaction literals from an unauthenticated route** (`oauth.py` `PendingStore.start` calls `register_secret` for
  state and verifier on every `GET /oauth/start`; `logging_setup.redact` sorts all literals per log record). Loopback-only, but a
  local page that loops `/oauth/start` slowly degrades logging. Fix: register state/verifier only once per flow replaced (drop
  the old pair), or cap/evict literals.
- **m8 Docs inaccuracies**: README says non-GET requests to the routes get `405` but an unauthenticated one gets `401` (only an
  authenticated one gets 405, as the test shows); the tool table was inserted ahead of the "Result size" paragraph so that
  paragraph now reads as part of the OAuth section; `implementation-report.md` says 137 new tests (actual 140) and "nothing
  outside the spec was added". Server `CLAUDE.md` invariant bullets were widened into very long lines.
- **m9 Test gaps**: no test that user-token results are not cached (U20: call `list_my_guilds` twice, assert two user requests);
  U30-U33 have only manual verification though SPEC section 8 asks for a selection-helper test "if practical" and a
  `.gitignore` check. Fix: add a pytest that asserts `data/` in `.gitignore`, that `docker-compose.oauth.yml` has exactly the
  mount and env entry, and (optionally) runs `bash -c 'source scripts/lib/common.sh; ...'` with a stub `docker`.
- **m10 `chmod 700 ./data` in setup.sh** (`scripts/setup.sh`, new block). On a native Linux filesystem the container user
  (10001) is not the owner and could not write the token; on `/mnt/c` it is a no-op. Fix: drop the chmod (the file itself is
  0600) or document that the folder must be writable by UID 10001.
- **m11 Atomic-save residue**: if `os.replace` fails the temp file is removed, but if the process dies between fsync and
  replace a `.discord_user_token.json.*.tmp` (0600, containing tokens) remains in `./data`. Acceptable; note in the README
  "Token file" paragraph that stray `.tmp` files may be deleted.

## SPEC issues (spec wrong or incomplete; need human-approved amendment, not implementer change)

- **S1 Revoking the previous grant on reconnect (U14, U13)** can destroy the new grant. SPEC 1.1(8) says revoking either token
  revokes all tokens of that authorization, and Discord authorizations are per user and application, so a re-sign-in by the
  same human very likely shares the authorization. The code revokes the old refresh token after the new token is issued,
  which would then invalidate the new pair (first use would 401, refresh would fail, the account would silently disconnect).
  The same hazard applies to the failed-connect revokes in m2 when a previous connection exists. Suggest: do not revoke the
  previous grant on reconnect (the new code simply replaces it), and revoke on failure only when no previous token exists;
  confirm in AC-L1.
- **S2 U15 "any other method gets 405"** is ambiguous with "bearer required for non-GET". Behavior today: authenticated 405,
  unauthenticated 401. State that explicitly.
- **S3 U1 vs U3**: U1 gives `HOST_PORT` a 1-65535 range while U3 says none of the new settings is read when off. Say whether
  validation applies when off (m1).
- **S4 U13** should enumerate the failure paths that revoke (m2), and U33's "19 entries (or 19 minus intent omissions)" should
  say "at most 19".

## Verdict

**APPROVED.** No blocker or major issues; all invariants in the SPEC section 7 table hold as specified, off mode is unchanged
(454 pre-existing tests pass with no test file touched), and the gate is green at 96.5% coverage. The implementer should
fix or explicitly accept m1-m11, and the human should rule on S1 before the live sign-in check (AC-L1).
