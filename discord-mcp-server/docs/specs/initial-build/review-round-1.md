# Review round 1: discord-mcp-server initial build

- **Reviewer:** independent read-only reviewer (Sonnet 5.5). Spec: `../../SPEC.md` (Approved 2026-10-28).
- **Verdict: CHANGES REQUESTED** (0 blockers, 1 major, 11 minors; 6 spec issues for the human).
- Method: read AGENTS.md section 7, both CLAUDE.md files, the full spec, workflow, every source file, scripts, Docker files, docs,
  the test-suite structure and the key tests; re-ran the gate; diffed `security.py` / `logging_setup.py` against `sports-mcp-server`;
  reproduced the major issue with a throw-away script outside the repo (nothing in the repo was edited except this report).

## Gate results observed

| Command | Result |
|---|---|
| `make check VENV=~/.venvs/discord-mcp` | ruff check and format clean, `mypy --strict` clean, **282 passed**, total coverage **96.22%** (gate 90%) |
| `make audit VENV=~/.venvs/discord-mcp` | No known vulnerabilities found |

`security.py` is byte-identical to the sibling; `logging_setup.py` differs only by the adaptations listed in the diff (patterns, extra
fields, comments). No `.env` exists in the tree; `.env`, `.coverage` and caches are git-ignored. Docker build/compose were not re-run
by me (Docker not exercised); I checked `docker-compose.yml` and `Dockerfile` against the sibling and R39 by diff: only names, port and
the removed `HOST`/`PORT` env differ, hardening (read-only, tmpfs, cap_drop, no-new-privileges, init, pids/mem/cpus, log rotation,
loopback port, healthcheck) is intact.

## Requirement coverage summary

R1-R42 and AC1-AC4 are implemented and have tests (or a documented reviewer check for R39-R42). SPEC section 11 invariants hold:
allowlist guard before any other request in every tool (guild tools: no request; channel tools: one cached `GET /channels/{id}`),
only `discord_client.py` imports httpx (grep test plus my own check), host pinned in `_url`, `follow_redirects=False` plus 3xx refusal,
no tokens/URLs/content in logs/errors/healthz, no state keyed to connections (`stateless_http=True`), exact tool set and order pinned.
Rate-limit rules (R20-R24, section 9) are implemented as specified: no hard-coded per-route limits, full `retry_after` honoured with
no cap and the bucket/global pause kept blocked, shared scope neither replayed nor counted, 401 hard-stop, invalid-request circuit.
Empty-vs-forbidden (R28-R29) never sends the messages request when permission is missing; 202/110000 is `index_pending`, never empty;
the R30 error shape is an `isError` result, kind mapping uses status and numeric `code` only; projections match section 8 key lists.

## Issues

### Blocker

None.

### Major

**M1. A cancelled or crashed in-flight request leaks the bucket lock and wedges that bucket forever.**
`src/discord_mcp/discord_client.py:215-239` (`DiscordClient.get`). The lease is acquired at :215 and released explicitly on
specific paths only (`_TooLarge`, `httpx.TransportError`, 429, normal completion). Any other exception raised while the request is in
flight leaves `state.lock` held: most importantly `asyncio.CancelledError` (client disconnect, MCP `notifications/cancelled`, server
shutdown, an outer timeout), but also `httpx.DecodingError` (a `RequestError` that is not a `TransportError`, raised by
`aiter_bytes()` on a bad gzip body) and any other unexpected exception. Every later request on the same route+major then blocks in
`state.lock.acquire()` (`ratelimit.py:649`) with no timeout, so, for example, `list_messages` for one channel hangs until the container is
restarted. `Lease`'s own docstring says "Release it in a `finally`" and `acquire` does so (`ratelimit.py:653-655`); `get` does not.
Reproduced: start `get()` against a transport that never answers, cancel the task, then call `get()` on the same route with a working
transport; the second call never completes (`asyncio.wait_for(..., 2)` times out). Uncached routes (messages, pins, search, members,
audit) are unshielded, so a cancelled MCP call is enough; cached routes are protected by the shielded cache task.
Fix: restructure the loop body as `try: ... finally: lease.release()` (release is idempotent), or wrap `_send` in
`except BaseException: lease.release(); raise`. Also catch `httpx.RequestError` (or add `httpx.DecodingError`) and map it to
`kind=upstream`. Add tests: cancelling an in-flight `get` then a follow-up call succeeds; a `DecodingError` yields `kind=upstream` and
the next call still works.

### Minor

**m1. Search channel filter can issue up to 500 sequential uncached upstream requests.**
`service.py:523-528`. Each `channel_ids` entry triggers `guard.resolve_channel` (one `GET /channels/{id}` on a cold cache) plus the
permission lookup, sequentially, all inside one tool call that the tool-call budget (R36) counts once. 500 cold lookups are paced only
by the global limiter and will take long or trip `rate_limited`. Within the spec, but worth a note in the README/description, or resolve
channels concurrently (`asyncio.gather`, bucket serialisation still applies per channel).

**m2. A channel from another allowlisted guild in `channel_ids` gets a different message than R13's.**
`service.py:525-526` raises `forbidden_guild` with "Channel is not in the given guild" rather than the R13 wording
("Guild not in this server's allowlist"). Kind is right; the message is the only deviation. Either reuse `GUILD_REFUSAL` or have the
spec allow a distinct message.

**m3. Bucket aliasing can replace a live state object.** `ratelimit.py:480-482` (`Lease.learn`). When two different routes share one
Discord bucket hash and the same major parameter, the second route's `learn` overwrites `_buckets["bucket:<hash>:<major>"]` with its own
`BucketState`, discarding the first's lock/`blocked_until`/`remaining`. Fix: if the key already maps to a state, keep it (and make this
lease's state that one, or merge `blocked_until`) instead of overwriting. Low practical impact on the routes used today.

**m4. Non-finite `retry_after` is not rejected.** `discord_client.py:92-110`. `float("inf")`/`"nan"` from a hostile or broken header/body
would block a bucket forever and put `Infinity`/`NaN` into `structuredContent`. Treat non-finite or negative values as "absent" (fall
back to the default) before use. (Honouring a large finite value in full is correct and intended.)

**m5. Robustness: unexpected body shapes become generic JSON-RPC internal errors.** `service.py:398` (`for c in channels_f.value` when
Discord returns a non-list), `service.py:390`/`:479`/`:708` (`project.guild/message/member` on a non-dict body, or a dict lacking
`id`). R30 reserves JSON-RPC errors for protocol faults and unexpected exceptions, so this is allowed, but a malformed 2xx body is more
useful as `kind=upstream`, "Discord returned an unexpected response". Validate shape in `_direct`/`_cached` callers.

**m6. `get_audit_log` rejects the oldest-first start the description promises, and has a dead code path.**
`server.py:496-505` constrains `before`/`after` to the 15-21 digit snowflake pattern, so `after="0"` (SPEC 7.14 description note) cannot
be sent, and the ISO conversion in `service.py:747-750` can never run for this tool. See spec issue S4; whichever way the spec is
resolved, align schema, service and description.

**m7. Channel model carries tool-specific fields where section 8 does not list them.** `models.py:76-93`: `get_channel` returns
`bot_can_view`, `bot_can_read_history` and `bot_joined` as null; `list_channels`/`list_threads` return the unrelated ones as null.
Section 8 lists these per tool. Implementer deviation 7 acknowledged it. Acceptable, but it makes the key set of `get_channel` differ
from a literal reading; consider separate result subclasses or accept it in the spec (S5).

**m8. Channel type fallback is a non-null sentinel.** `project.py:241-242` sets `type_id = -1` when Discord omits `type`; section 8 says
missing fields become null. `Channel.type_id: int` forbids null. Make `type_id` `int | None` (type `"unknown"`).

**m9. Channel ordering groups children under a category only when category positions are unique.** `service.py:150-156` sorts by
`(parent position, kind, position, id)`; two categories with equal `position` interleave their children. Add the parent id as a
tiebreak: `(parent_position, parent_id, kind, position, id)`.

**m10. Test gaps (tests pass but do not pin these behaviours).**
(a) `tests/test_discord_client.py:211-229` and `:264-281`: after a 429 beyond the in-call limit (and after a shared 429) no test calls
the route again and asserts that the call is refused locally with zero new requests and a decreasing `retry_after`; SPEC section 13
asks for "the bucket stays blocked for the full duration". The unit test in `test_ratelimit.py:93-101` covers the limiter, not the
client path.
(b) `tests/test_server.py:255-266`: the guard loop asserts on `fake.requests` with a `for` loop that is vacuous when no request was sent;
assert the exact expected request list per tool (none for guild tools, exactly the one channel lookup for channel tools). DM channels
are tested once in `test_service.py:100`, not per channel tool as SPEC section 13 describes ("non-allowlisted guild / channel / DM
channel" parametrised over every tool).
(c) No test for cancellation (see M1).
(d) Section 13 asks for one projection test per model asserting the exact key set; Role, Attachment, Embed, Reaction, Poll and
ThreadInfo key sets are only touched indirectly.

**m11. Root docs not fully updated (R42 / AC4).** Root `CLAUDE.md:15` still says "A monorepo of four independent, self-hosted MCP
servers" (now five); the lineage paragraph ("gmail -> vision -> sports -> firecrawl") and `CLAUDE.md:42` ("85%; sports is 90%") do not
mention discord (also 90%). The table row and root README row were added correctly.

(Also noted, below minor: `project.py:305-311` reports poll `votes: null` when the answer is absent from `answer_counts` even though
`results` exists; Discord omits zero-count answers in some payloads, so a present `results` with a missing answer would arguably be `0`.
Cosmetic.)

## Evaluation of the implementer's flagged interpretations

1. **Channel-id tools send one `GET /channels/{id}` before refusing (R15 "zero upstream requests"): ACCEPT.** There is no other way to
   learn a channel's guild (R12 itself prescribes the cached lookup). Guild-id tools send nothing, as R13 requires; the test pins both.
   Spec wording should be corrected (S1).
2. **`list_guilds` has no id, R15 applied as output filtering: ACCEPT.** The test asserts non-allowlisted guilds never appear. S1.
3. **401 at boot registers all tools so each returns `kind=auth`: ACCEPT.** Matches R5; R8's "minus omitted by R6" reads consistently
   because no intent can be known. Spec clarity: S2.
4. **Probe in the ASGI lifespan before serving: ACCEPT.** Satisfies R4 ("before the HTTP listener accepts MCP traffic") and keeps the
   httpx client and locks in the serving loop. Verified `create_app` registers the tools inside the lifespan and `tools/list` is stable.
5. **`list_guilds` capped at 5 pages x 200 with a warning: ACCEPT.** Matches SPEC 7.1 ("or 5 pages"). The warning log has no ids.
6. `User.bot` defaulting to false, other missing values null: accept (`project.py:167`).
7. 400 -> `validation`, other unlisted 4xx -> `upstream`, non-retryable: accept (R31 lists no other statuses).
8. `bot_can_view`/`bot_can_read_history` null on `get_channel`: accept, see m7.
9. `list_threads.bot_joined` from the response `members` ids equal to the thread id: accept (Discord thread-member `id` is the thread id).
10. Audit action names from recall: accept; unknown -> `action: null` as specified.
11. `/healthz` reports `discord_auth` after a runtime 401 too: accept (superset of R5, no leak).
12. Search `has_more`/`next_offset` counted per hit group: accept; it guarantees `next_offset` advances (`service.py:551-561`).

## Spec issues (problems in the spec, not the code)

- **S1. R15 vs R12/R13.** R15 says every tool's non-allowlisted id is refused "with zero upstream requests", but channel-id tools must
  fetch the channel to learn its guild (R12), and `list_guilds` takes no id. Reword R15 to "no upstream request except the single
  cached channel lookup for channel-scoped tools", and exempt `list_guilds`.
- **S2. R5 vs R8/R6.** R5 says that on a boot 401 every tool returns `kind=auth`, while R8/D6 say intent tools are omitted when the intent
  is off. State explicitly that with an unknown intent state (401) all non-config-disabled tools are registered.
- **S3. R13 "does not reveal whether the id exists".** A channel id that exists in a non-allowlisted guild returns `forbidden_guild`,
  while one that does not exist (or is invisible to the bot) returns `not_found`/`permission` from the lookup, so existence of channels
  in other guilds the bot can see is distinguishable. Either accept this and soften R13, or map every failure of the guard's channel
  lookup to `forbidden_guild`.
- **S4. SPEC 7.14 `after="0"` vs R25.** The description note asks for oldest-first paging via `after="0"`, but R25's snowflake pattern
  (`^[0-9]{15,21}$`) rejects `"0"`, and 7.14 does not say whether ISO datetimes are accepted (R26 says "where documented"). Decide:
  allow `"0"` explicitly for audit `after`, accept ISO datetimes, or drop the note.
- **S5. Section 8 vs the per-tool Channel fields.** Say whether `get_channel` includes `bot_can_view`/`bot_can_read_history`
  (null or computed; it already has the permission context) and whether null placeholders for the unrelated fields are acceptable.
- **S6. R36 does not bound upstream fan-out.** One tool call can cause up to ~500 upstream requests (search `channel_ids`) or 5 (guild
  pages). Consider an explicit per-call upstream request ceiling, or state that only the global pacing/invalid-request controls apply.

## Verdict

**CHANGES REQUESTED.** Fix M1 (with tests); fix or explicitly accept each minor (m1-m11) with a written reason; take S1-S6 to the human.
The rest of the build is solid: the gate passes, the invariants hold, and the high-risk semantics (rate limits, empty-vs-forbidden,
index-pending, error mapping, redaction) are implemented as specified.
