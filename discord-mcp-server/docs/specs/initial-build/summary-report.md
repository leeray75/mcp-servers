# Summary report: discord-mcp-server initial build

- **Date:** 2026-10-29
- **Spec:** `../../SPEC.md` (approved 2026-10-28; amendment A1, section 16, and amendment A2, section 17, approved 2026-10-29; all approved by: human)
- **Branch:** `feature/discord-mcp-server` (from `main` after fast-forwarding to `origin/main` at `8f56ed2`). **Nothing committed or pushed.**
- **Result:** review round 3 verdict **APPROVED** (round 2 approved the build; A2 then added one fix and one review round); 3 of 5 review rounds used.

## Process

| # | Step | Actor | Output | Result |
|---|---|---|---|---|
| 1 | Read brief and sibling specs; ask 15 decisions | orchestrator + human | decisions D1-D16 | answered |
| 2 | Write spec, tasks, workflow | orchestrator | `SPEC.md`, `tasks.md`, `workflow.md` | Draft |
| 3 | Approval gate | human | "Approve" | Approved 2026-10-28 |
| 4 | Sync `main`, create branch | orchestrator | `feature/discord-mcp-server` | done |
| 5 | Implement T0-T13 | implementer (Haiku 5.5) | code, `implementation-report.md` | gate green, 282 tests |
| 6 | Review round 1 | reviewer (Sonnet 5.5) | `review-round-1.md` | CHANGES REQUESTED: 1 major, 11 minor, 6 spec issues |
| 7 | Triage external review (F1-F11), verified against code and docs.discord.com | orchestrator | `external-review-triage.md` | 7 confirmed, 2 partly/plausible, 2 rebutted |
| 8 | Spec amendment A1 (S1-S6, F1-F11) | orchestrator + human | `SPEC.md` section 16 | Approved 2026-10-29 |
| 9 | Fix round 1 (M1, m1-m11, all of A1) | implementer (same agent) | code, `fix-round-1.md` | gate green, 414 tests |
| 10 | Review round 2 | reviewer (same agent) | `review-round-2.md` | **APPROVED**: 0 blockers, 0 majors, 4 minors, 2 spec observations |
| 11 | Spec amendment A2 (round-2 n1 + O2: OR `flags` and `flags_new`, never-raising parsing) | orchestrator + human | `SPEC.md` section 17 | Approved 2026-10-29 |
| 12 | Fix round 2 (A2) | implementer (same agent) | code, `fix-round-2.md` | gate green, 454 tests |
| 13 | Review round 3 | reviewer (same agent) | `review-round-3.md` | **APPROVED**: 1 minor (`source=none` vs `null`) |
| 14 | Fix round 3 (that minor; reviewer said no further round needed if the gate stays green) | implementer (same agent) | `fix-round-3.md` | fixed; orchestrator verified the diff |
| 15 | Final gate re-run | orchestrator | this report | green |

## What was built (mapped to the spec)

A new read-only MCP server in `discord-mcp-server/` (Python 3.12, `mcp` 2.3.0, Streamable HTTP on `127.0.0.1:8769`, bearer + Host/Origin
guard, hardened Docker in WSL2):

- **14 tools** in a fixed order (section 7, R8-R11): `list_guilds`, `get_guild`, `list_channels`, `get_channel`, `list_messages`,
  `get_message`, `search_messages`, `list_reactions`, `list_pins`, `list_threads`, `list_members`, `get_member`, `search_members`,
  `get_audit_log`. `search_messages` and `list_members` depend on privileged intents detected at boot from the bitwise OR of `flags` and
  `flags_new` (each parsed independently, never raising), with `DISCORD_FORCE_*` and `DISCORD_ENABLE_*` overrides (A1-R6, A2-R6a, A2-R7a).
- **Allowlist guard** on every tool, default-deny, with an indistinguishable refusal for channel ids (R12-R15, A1-R13, A1-R15).
- **Rate-limit layer** (R20-R24, section 9): header-learned buckets keyed on bucket + major param, full `retry_after` with no cap,
  global pause and pacing, shared scope neither counted nor replayed, invalid-request circuit, 401 hard-stop.
- **Empty vs forbidden** via computed effective permissions (R28-R29); search `202/110000` reported as `index_pending` (7.7).
- **Errors** as `isError` results with the R30 structured shape, mapped by numeric code (R30-R31); no secrets/URLs/content in logs or
  errors (R32).
- **Projections** per section 8 and A1 (no `obfuscated`; search results carry `reactions: null`; audit action names transcribed from the
  docs and verified entry by entry by the reviewer).
- **Ops and docs:** scripts with `.ps1` twins, Dockerfile, compose, README (permissions integer 66688, intents, token rotation),
  CHANGELOG, server `CLAUDE.md`, root `CLAUDE.md` / `README.md` / `CHANGELOG.md` entries (R39-R42, A1 docs).

## Verification

| Check | Result (orchestrator re-run, 2026-10-29) |
|---|---|
| `make check` (ruff, format, `mypy --strict`, pytest, branch coverage gate 90%) | **454 passed, coverage 96.35%** |
| `make audit` | No known vulnerabilities |
| `docker compose config` (with a placeholder empty `.env`, removed afterwards; run after fix round 1, no compose change since) | valid |
| `docker build`, offline container run (implementer, round 0) | built; `/healthz` degraded `discord_unreachable_at_boot` as specified |
| Reviewer reproductions (rounds 1-3) | M1 wedge reproduced, then confirmed fixed; audit table compared with the live docs page; 25 malformed flag inputs run through the parser, none raised |
| Live Discord (AC5) | **not run** (needs your token; optional) |

## Deviations and accepted items

- No deviations from the approved spec (with A1).
- Accepted in fix round 1: poll `votes` are null when an answer is absent from Discord's counts (cosmetic).
- Round 2 minors, left open (the verdict is APPROVED; none blocks):
  - **n1** (malformed `flags_new` could crash startup): **fixed by A2** (rounds 2-3).
  - **n2** a 5xx/timeout during the guard's channel lookup becomes non-retryable `forbidden_guild` (what A1-R13 says, but misleading).
  - **n3** a duplicated docstring line in `plan_tools`.
  - **n4** the audit table fetch date is 2026-10-28 in A1 and 2026-10-29 in the source comment.

## Spec observations for the human (reviewer round 2)

- **O1 size:** worst case `list_messages` at `limit=100` is about 2.8 MB structured, or about 5.7 MB on the wire, because R11 also
  duplicates the data as text. `get_audit_log` is about 2.3 / 4.5 MB. This meets A1-R43, but the fixtures are synthetic worst cases.
  If clients struggle, the cheaper levers are a smaller default `limit` or a shorter text block.
- **O2 intents:** **resolved by A2**: both fields are now OR-ed.
- Fix round 3 also updated one assertion in `tests/test_fix_round1.py` that checked the old `source=none` text, a direct consequence
  of the same fix (accepted by the orchestrator).

## Open items for the human

1. Rotate the leaked bot token and client secret; delete the Developer Portal PDF prints and their extractions (brief section 3.2,
   F10). Reduce the portal permissions to **66688** (`VIEW_CHANNEL | READ_MESSAGE_HISTORY | VIEW_AUDIT_LOG`).
2. Optional AC5 live smoke: `scripts/setup.sh`, `scripts/start.sh`, `scripts/smoke.sh`; also confirm the real `/applications/@me`
   body and whether `search_members` works without the GUILD_MEMBERS intent (`tools/try_live.py`).
3. `shellcheck` was not available; the scripts were only checked with `bash -n`.
4. Decide whether to address n2-n4 or O1 in a follow-up SDD change (all optional).
5. Commit / PR when you're ready (nothing has been committed).
