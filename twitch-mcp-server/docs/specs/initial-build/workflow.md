# Workflow: implement / review loop for the twitch-mcp-server initial build

Runs only after the human set `../../SPEC.md` to **Approved** (explicit "Approve" in chat, 2026-10-29 19:28 -04:00).
Process authority: `../../../../AGENTS.md`.

## Roles

| Role | Agent | Edits code | Writes |
|---|---|---|---|
| Orchestrator | main session | no (coordinates, runs the final gate, writes the summary) | `summary-report.md` |
| Implementer (scaffolding/ops/docs) | one subagent | yes — **never** under `src/` or `tests/` | `implementation-report.md` (scaffold section), `fix-round-N.md` |
| Implementer (leaf modules) | one subagent, runs concurrently with the above | yes — **only** `src/twitch_mcp/` and `tests/` | `implementation-report.md` (module section), `fix-round-N.md` |
| Reviewer | one subagent, read-only | **no** | `review-round-N.md` |

The two implementer workstreams are file-disjoint by construction (the scaffolding agent owns everything outside
`src/` and `tests/`; the leaf agent owns `src/twitch_mcp/` and `tests/`), which is what lets them run in parallel.
The reviewer is distinct from both implementers and re-reads the code and spec from disk every round. The
orchestrator never edits feature code. The same reviewer is reused for every round (resumed with SendMessage); a
spec-amendment restart reuses the same agents.

## Loop (at most 5 review rounds)

1. Orchestrator syncs local `main` with `origin/main` (fast-forward only), then creates `feature/twitch-mcp-server`
   from it.
2. Orchestrator sends both implementer prompts (below). They implement their halves and write
   `docs/reports/initial-build/implementation-report.md` (one requirement → implementation → test row per R1–R57,
   plus A1–A8), deviations (should be none), and every command run with its result.
3. Reviewer round 1 writes `docs/reports/initial-build/review-round-1.md`: issues classified blocker / major /
   minor, each with `file:line`, verdict APPROVED or CHANGES REQUESTED.
4. If CHANGES REQUESTED: the implementer that owns each affected file evaluates every finding, fixes **all legitimate
   blockers and majors**, fixes or explicitly accepts each minor with a written justification, and rebuts any finding
   it judges invalid with evidence. It records everything in `fix-round-N.md` and re-runs the gate.
5. Reviewer round N+1 checks the fixes, the rebuttals and for regressions. Repeat until APPROVED, **max 5 rounds**.
6. If round 5 is not APPROVED, the orchestrator stops and asks the human (more rounds, amend spec, accept known
   limitations, abandon).
7. On APPROVED the orchestrator re-runs the gate itself and writes `summary-report.md`, then stops. **No commit,
   push, tag or release unless the human asks.**

**Spec issues (any subagent):** if an implementer or the reviewer finds the spec wrong, incomplete, contradictory or
impossible, it stops and reports the item verbatim as a `SPEC-GAP:` line with the requirement number. The
orchestrator takes the issue to the human, amends `SPEC.md` (recorded as an amendment), gets explicit re-approval,
and restarts the cycle from step 2 for the affected parts, reusing the same agents. Nobody improvises around the
spec.

## Environment

Run in WSL with the venv outside the Windows drive: the pre-built
`/home/leeray/.venvs/twitch-mcp/bin/python` (3.12.14) already carries mcp, httpx, pydantic, pydantic-settings,
starlette, uvicorn, pytest, pytest-asyncio, pytest-cov, ruff, mypy, pip-audit and uv; `uv` is at
`/home/leeray/.local/bin/uv`. The package itself is not installed, so run tests as
`PYTHONPATH=src /home/leeray/.venvs/twitch-mcp/bin/python -m pytest tests/ --no-cov -q` from the project directory
(drop `--no-cov` only for the full gate run). Never read out, print, log or commit `.env` values (there should be no
`.env` in the repo; tests never need one). Tests stay offline; no real Twitch calls in this loop — live checks
(spec A2/A3) are opt-in and run by the human. `.sh` files must stay LF (`.gitattributes`); verify with
`file scripts/*.sh` or `grep -c $'\r'`.

## Implementer prompt (scaffolding/ops/docs — template)

> You are the implementer for an approved spec (`twitch-mcp-server/docs/SPEC.md`). Read, in order: the root
> `AGENTS.md` (section 6), the root `CLAUDE.md`, `discord-mcp-server/CLAUDE.md` (the sibling you copy from), then
> the spec and `docs/specs/initial-build/tasks.md`. You own everything **outside** `src/` and `tests/`:
> `pyproject.toml`, `requirements*.txt` (via `make lock` / uv only — never hand-edited), `Makefile`, `Dockerfile`,
> `docker-compose.yml`, `docker-compose.oauth.yml`, `.env.example`, `scripts/` (+ `.ps1` twins, `lib/`), `README.md`,
> `CHANGELOG.md`, `CLAUDE.md`, `docs/specs/initial-build/{tasks,workflow}.md`, `data/.gitkeep`. Implement exactly
> what the spec requires and nothing else. If the spec is wrong, incomplete, ambiguous or impossible on any point,
> STOP on that item, do not improvise, and report it verbatim as `SPEC-GAP:` with the requirement number. Never put
> a real credential anywhere; `.env.example` uses `replace-me…` placeholders only. Validate with
> `docker compose config` and `docker compose -f docker-compose.yml -f docker-compose.oauth.yml config`. Do not
> commit.

## Implementer prompt (leaf modules — template)

> You are the implementer for an approved spec (`twitch-mcp-server/docs/SPEC.md`). Read, in order: the root
> `AGENTS.md` (section 6), the root `CLAUDE.md`, `discord-mcp-server/CLAUDE.md` and `discord-mcp-server/src/`, then
> the spec and `docs/specs/initial-build/tasks.md`. You own **only** `src/twitch_mcp/` and `tests/` (plus
> `tools/try_live.py`). Follow T1–T10 and T13 of `tasks.md`; the scaffolding (pyproject, Dockerfile, compose,
> scripts, docs) is being built concurrently by another agent — do not create or edit those files. Implement exactly
> what the spec requires and nothing else: no extra tools, no prompts/resources, no EventSub, no passthrough. Write
> tests for every requirement (spec §8 file list), run the gate, and keep the tool set pinned per switch
> combination. If the spec is wrong, incomplete, ambiguous or impossible, STOP on that item and report it verbatim
> as `SPEC-GAP:` with the requirement number. Do not commit.

## Reviewer prompt (template)

> You are an independent, read-only reviewer. Do not edit any file except your report. Read `AGENTS.md` section 7,
> the root `CLAUDE.md`, `twitch-mcp-server/CLAUDE.md`, and `twitch-mcp-server/docs/SPEC.md`, then the code. Verify
> by reading the code and running the gate yourself; do not trust the implementers' report. Check: every requirement
> R1–R57 and acceptance criterion A1–A8 is implemented and tested; nothing outside the spec was added; the write
> invariants hold exactly (R16 writes never retry and the retry helper is unreachable from write paths; R28 two
> independent kill switches; R29 verified identity, never the allowlist alone; R31–R34 single-use bound confirm
> tokens with the guard re-run on the confirmed turn; R27 uniform denial with zero requests on refusal; R19 limits
> learned from headers only, nothing baked in; R11 only `twitch_client.py` imports httpx); the compose files publish
> `127.0.0.1:8770:8000` only and keep the container hardened (UID 10001, read-only rootfs, `cap_drop: ALL`,
> no-new-privileges, memory limit, single `./data` mount only in the oauth override); no secret-shaped value exists
> in any committed file; logs and `/healthz` leak nothing (R52/R53); docs (README/CHANGELOG/`.env.example`) match
> the code and the spec §6.4 tool tables verbatim. Write `docs/reports/initial-build/review-round-N.md` with each
> issue classified blocker / major / minor, a `file:line` for each, and a verdict APPROVED or CHANGES REQUESTED. If
> you find a problem in the spec itself, report it as a spec issue rather than a code issue.

## Artifacts

`docs/specs/initial-build/tasks.md`, `docs/specs/initial-build/workflow.md` (this file); the build reports live in
`docs/reports/initial-build/`: `implementation-report.md`, `review-round-N.md`, `fix-round-N.md`,
`summary-report.md` (the spec itself is `docs/SPEC.md`).
