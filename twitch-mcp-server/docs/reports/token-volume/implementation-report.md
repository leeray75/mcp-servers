# Implementation report: user-token store on a Docker named volume (`docs/specs/token-volume/SPEC.md`, V1–V9)

Status: implemented; gate green; V9 (live check) **operator-run, pending**. Nothing committed.
Spec: APPROVED (approved by: human, 2026-10-31). Spec-review round 2 minors m1–m3 applied.

## 1. Requirement traceability

| Req | Implementation | Verification |
|---|---|---|
| V1 | `docker-compose.oauth.yml`: `- ./data:/data` replaced by `- twitch-mcp-data:/data`; top-level `volumes:` with `twitch-mcp-data:` and `name: twitch-mcp-data` (indent-checked, m3). | `tests/test_deployment.py::test_oauth_override_declares_the_named_volume` and `::test_oauth_override_mounts_only_the_named_volume` (helper returns `["twitch-mcp-data:/data"]`). |
| V2 | `/data` target and `TWITCH_OAUTH_TOKEN_FILE: /data/twitch_user_token.json` unchanged in the override. | Equality assertion in the V1 mount test (target); env var by review and live check step 4. |
| V3 | `docker-compose.yml` has no top-level `volumes:` and no service volumes. Base-file test also fails on any flow-style `volumes:` line (m1, regex `^\s*volumes:\s*\S` on comment-stripped lines). | `tests/test_deployment.py::test_compose_mounts_no_host_directory_by_default` (rewritten body and docstring; no `./data` text, m3). |
| V4 | `Dockerfile` instruction unchanged (`RUN mkdir -p /data && chown 10001:10001 /data`); only the comment at lines 43–44 changed. | `test_the_data_directory_belongs_to_the_service_user` passes. `git diff` on Dockerfile shows comment lines only. |
| V5 | `oauth.py` and `twitch_client.py` not edited by this work (both were already modified before the spec; this work adds no diff to them). Operator notes (a)–(c) written into README and CHANGELOG. | `tests/test_oauth_units.py` and `tests/test_server.py` pass unchanged. |
| V6 | `scripts/setup.sh`: the `# R48`, `mkdir -p`, `Token folder` and `sudo chown` block replaced by one `printf` line naming `twitch-mcp-data`, `disconnect_twitch_account` and `docker volume rm twitch-mcp-data`; no folder created. `scripts/stop.sh` header rewritten to name the volume. `.ps1` twins untouched. | Review checklist (see §4). |
| V7 | `.gitignore` line 5 comment is now `# legacy token folder (older versions); never commit`; `data/` rule kept. `data/.gitkeep` deleted from disk and the empty `data/` folder removed. `.dockerignore` unchanged. | Disk state (`ls data` reports absent); `git ls-files data` was empty before. |
| V8 | README (paragraph near line 64, security-posture bullet near line 416, operator notes and legacy-upgrade note); `CLAUDE.md` container invariant (line 166–168); `docs/SPEC.md` R2 and R48; `CHANGELOG.md` `[Unreleased]` `### Changed`; comment-only edits in `docker-compose.yml`, `Dockerfile`, `.env.example`, `scripts/lib/common.sh`. `README.md:146`, `CLAUDE.md:24`, `CLAUDE.md:157` not changed. | Review checklist (see §4). |
| V9 | Live check §8 steps 1–6 not run by the implementer (no docker or compose commands run, per instructions). | **Operator-run, pending.** |

## 2. Deviations and spec gaps

1. **SPEC-GAP (minor, no edit made): `docs/SPEC.md` §6.1.** V8 lists §6.1 for a V-D5 rewrite. §6.1 (Directory layout) has no mount wording: it lists `docker-compose.oauth.yml` as a file and no `./data` entry. Nothing to rewrite, so §6.1 is unchanged. The R2 and R48 rewrites carry the V-D5 wording. The orchestrator should confirm this is acceptable.
2. **Spec-mandated text trips a checklist grep.** The V6 `printf` line in `scripts/setup.sh` (line 239) prints the string `docker volume rm twitch-mcp-data`. It is text printed to the operator, never executed. The reviewer grep for `volume rm` in `scripts/` therefore matches this line by design; no `down -v`, `down --volumes` or `volume prune` exists in `scripts/`.
3. **Edit method (no deviation in content).** The first attempt at the comment and script edits failed because Git Bash has no `python3`. The edits were applied by a script run through the WSL venv Python (four files), and the remaining three files (`setup.sh`, `stop.sh`, `.gitignore`) by exact-match Edit. The `data/.gitkeep` removal ran in the same failed command, before the other edits, which is harmless because the order is free.
4. **Spec status line.** `docs/SPEC.md` Amendment 6 line already reads `approved by: human`; no change was needed.

## 3. Failing-first evidence (B step)

Before any compose or doc change, `pytest tests/test_deployment.py --no-cov` against the unchanged compose files gave `2 failed, 6 passed`:
- `test_oauth_override_declares_the_named_volume`: `"volumes" in ['services']` is false.
- `test_oauth_override_mounts_only_the_named_volume`: `['./data:/data'] != ['twitch-mcp-data:/data']`.
The base-file test passed on the unchanged base file, as expected (it guards a regression).

## 4. Commands and results

- `make check VENV=/home/leeray/venvs/twitch` (WSL, `timeout 900`): **1006 passed**, total coverage **91.36%** (gate 90%, branch), exit green. Per-test timeout unchanged (30 s).
- `ruff check .`: All checks passed.
- `ruff format --check .`: 100 files already formatted.
- `mypy --strict src tests`: Success, no issues in 48 source files.
- `pytest tests/test_deployment.py --no-cov -q`: 8 passed.
- Reviewer checklist greps: `grep -rn -E 'down -v|down --volumes|volume prune|volume rm' scripts` matches only the spec-mandated print line (deviation 2).
- `git diff -- src/twitch_mcp/oauth.py` shows a diff from before this work; this work did not touch it. `twitch_client.py` not edited.
- All `.sh` files checked for CR bytes: none (LF). `.env` was never read or printed.
- No Docker or compose command was run. No commit made.

## 5. Files changed

- `docker-compose.oauth.yml` (V1, V2)
- `docker-compose.yml`, `Dockerfile`, `.env.example`, `scripts/lib/common.sh` (comments only, V4, V8)
- `scripts/setup.sh`, `scripts/stop.sh` (V6)
- `.gitignore` (V7 comment)
- `data/.gitkeep` deleted; empty `data/` removed (V7)
- `README.md`, `CLAUDE.md`, `docs/SPEC.md`, `CHANGELOG.md` (V8)
- `tests/test_deployment.py` (V1, V3 tests; helper)
- `docs/reports/token-volume/implementation-report.md` (this file)

## 6. Open items for the orchestrator

- V9 operator live check (SPEC §8 steps 1–6), to be recorded by the operator.
- Decide on deviation 1 (§6.1 has no mount wording).
- Commit, if wanted, is the human's call.
