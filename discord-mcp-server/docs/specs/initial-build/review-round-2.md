# Review round 2: discord-mcp-server initial build

- **Reviewer:** independent read-only reviewer. Spec: `../../SPEC.md` including section 16 (amendment A1, approved 2026-10-29).
- **Verdict: APPROVED** (0 blockers, 0 majors, 4 minors, 2 spec observations).
- Method: re-read SPEC section 16 and the triage from disk; re-read the changed code (`discord_client.py`, `guard.py`,
  `capabilities.py`, `service.py`, `project.py`, `models.py`, `server.py`, `config.py`), tests, `.env.example`, README, root docs;
  did not rely on `fix-round-1.md`. Re-ran the gate. Fetched the Discord audit-log page myself and compared every entry.
  Reproduced M1's scenarios with a throw-away script outside the repo (nothing in the repo was edited except this report).

## Gate results observed

| Command | Result |
|---|---|
| `make check VENV=~/.venvs/discord-mcp` | ruff + format clean, `mypy --strict` clean, **414 passed**, total coverage **96.25%** (gate 90%) |
| `make audit VENV=~/.venvs/discord-mcp` | No known vulnerabilities found |

## Round 1 findings

- **M1 (lock leak): FIXED.** `discord_client.py` wraps the request/response handling in `try ... finally: lease.release()` (idempotent;
  the early releases before 429 replay, backoff sleeps and the 5xx path remain), and `except httpx.RequestError` maps non-transport
  failures (e.g. `DecodingError`) to `kind=upstream`. I re-ran my reproduction: a cancelled in-flight `get()` no longer wedges the
  bucket (follow-up call completes), a `DecodingError` yields `kind=upstream`, and the next call succeeds. Tests exist
  (`test_fix_round1.py` cancellation and decoding cases) and the implementer reports a mutation check; my reproduction independently
  confirms behaviour.
- **m1** fixed (cap 25, concurrent `asyncio.gather`, deterministic first failure). **m2** fixed (identical A1-R13 message everywhere,
  including the search mismatch path). **m3** fixed (`setdefault`, first learned state wins). **m4** fixed (non-finite/negative
  `retry_after` treated as absent). **m5** fixed (`_as_list`/`_as_dict`/`_require_object` -> `kind=upstream`). **m6** fixed (audit
  `before`/`after` accept snowflake or ISO, `after="0"`, `action_type >= 1`). **m7/m8** fixed (inapplicable Channel keys omitted via
  `exclude_unset`, `type_id` null not -1, `get_channel` has computed `bot_can_*`, `bot_joined` only in `list_threads`).
  **m9** fixed (parent id tiebreak). **m10** fixed (per-tool exact request-list guard test, DM/unknown identical per channel tool,
  bucket-stays-blocked client tests, key-set tests). **m11** fixed (root `CLAUDE.md` "five", lineage, coverage line; root `README.md`
  diagram and table; root `CHANGELOG.md`). The poll `votes: null` acceptance is justified.

## A1 verification

| Item | Result |
|---|---|
| A1-R5 (boot 401 registers every non-config-disabled tool) | Implemented in `capabilities.plan_tools`; tested. |
| A1-R6 (`flags`, then `flags_new` decimal string, `flags_missing`, force flags, enable-flags win) | Implemented (`_application_flags`, `plan_tools`, `config.py:70-71`). Tested. See minor n1. |
| A1-R7 (reasons, `forced`, flags integer and source in boot line, no ids) | Implemented in `server._boot_summary`; tested. |
| A1-R13 (identical channel refusal for every lookup failure; auth/rate_limited/unavailable pass through) | `guard.py:40-52`; tested per channel tool for unknown and DM channels. See minor n2. |
| A1-R15 (guild tools: zero requests; channel tools: at most one lookup; `list_guilds` filters) | Tested with exact request lists. |
| A1-R16 (4 MiB default, hint to lower `limit`) | `config.py`, `SIZE_CAP_HINT`; `.env.example`/README updated; tested. |
| A1-R43 (worst-case size test per tool, sizes recorded, descriptions state max `limit` and no truncation) | `tests/test_sizes.py` (14 tools), README table, descriptions carry "maximum N" and `NO_TRUNCATION`. |
| A1-7.3 (no `obfuscated`) | Removed from model, projection and description note kept. |
| A1-7.7 (25 channels, concurrent, `-` negation in schema and service, `reactions: null` for search, description) | Implemented and tested. |
| A1-7.13 (`search_members` description, permission -> GUILD_MEMBERS hint) | Implemented and tested. |
| A1-7.14 | Implemented (see m6 above). |
| Projections (Channel, Message `reactions`, AuditEntry.action) | As specified. |
| **Audit action table** | I fetched `https://docs.discord.com/developers/resources/audit-log` and compared all rows: **69 of 69 match exactly** (values and names, including 130-132, 146 `AUTO_MODERATION_QUARANTINE_USER`, 150/151, 163-167, 190-193; no 147). The source comment and per-entry test are present. |
| Channel type names | Unchanged, source comment added. |
| Docs (server README, CLAUDE.md, .env.example, CHANGELOG; root docs) | Reflect A1. |

No regressions found: the full suite passes, the invariants of SECTION 11 still hold (only `discord_client.py` imports httpx, guard
before any other request, redaction, exact tool set and order), and the rate-limit rules are unchanged apart from the lease fix.

## Issues

### Blocker / Major

None.

### Minor

- **n1. `flags_new` parsing can raise.** `discord_client.py` `_application_flags`: `new.isdigit()` is true for characters such as
  `"²"`, for which `int()` raises `ValueError` (reproduced). A malformed `flags_new` would escape the probe and fail startup instead
  of yielding `flags_missing`. Use `new.isascii() and new.isdigit()` (or `try: int(new)`).
- **n2. Guard masks transient upstream failures as `forbidden_guild`.** `guard.py:44-47` converts every lookup error except
  auth/rate_limited/unavailable, including `kind=upstream` (5xx, timeout, oversize) and `validation`, into
  `forbidden_guild`, which is non-retryable and misleading for an infrastructure fault. A1-R13 literally says "every failure", but its
  list is 404/403/unknown/non-allowlisted/DM. Consider letting `upstream` pass through (it reveals nothing about existence).
- **n3. Stale docstring.** `capabilities.py` `plan_tools` docstring has a duplicated trailing line ("when the intent is off.").
- **n4. Date mismatch in provenance.** A1 text says the audit table was fetched 2026-10-28; the source comment says 2026-10-29. The
  content is verified correct today; align the date in whichever place is wrong.

## Spec observations (not code defects)

- **O1. A1-R43 vs R11 (size).** I evaluated the implementer's figures: `list_messages` at `limit=100` is about 2.83 MB structured,
  5.66 MB on the wire with the duplicate text block; `get_audit_log` 2.27 / 4.54 MB; `list_pins` 1.42 / 2.83 MB. The code meets
  A1-R43 as written (structured under 4 MiB, measured, recorded, documented). I think the spec is acceptable but the 4 MiB figure is a
  transport bound, not a model-context bound: 5 MB is roughly a million tokens, far beyond any context window, although the test
  fixture is deliberately worst-case (100 messages, each 4000 characters, 100 mentions) and realistic pages are one to two orders of
  magnitude smaller. If the human wants a safer default, the cheaper levers are a smaller default `limit` for `list_messages`
  (currently 50, itself worst-case ~1.4 MB structured), or letting the R11 text block be the only copy (structured content is what
  the outputSchema needs, but the text block could be shortened). Not raised as an error: this is D11 as decided, and the README warns.
- **O2. A1-R6 precedence between `flags` and `flags_new`.** A1 uses `flags_new` only when `flags` is absent or not an integer. If
  Discord returns a present-but-legacy `flags` that omits a newer-style bit set in `flags_new`, detection would report
  `intent_disabled` incorrectly (the force flags are the escape hatch). Consider OR-ing both fields when both are present. Live
  behaviour is unproven (A1 open item AC5).

## Verdict

**APPROVED.** The major finding from round 1 is fixed and verified, every minor is fixed or justifiably accepted, A1 is implemented
and tested as written, the audit table matches the Discord docs exactly, and the gate is green (414 passed, 96.25%, no known
vulnerabilities). n1-n4 are optional polish; the orchestrator may take O1/O2 to the human.
