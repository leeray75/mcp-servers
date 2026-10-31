# Tasks: user-token store on a named volume (`docs/specs/token-volume/SPEC.md`, V1–V9)

Status: Draft. Nothing here starts until the human approves the SPEC in chat (V-D2 to V-D5 confirmed). Tick a box only
when its acceptance check has run and passed.

- [ ] A. Approval: human approves SPEC and confirms V-D2 to V-D5 in chat; header set to Approved.
- [ ] B. Write the test changes first in `tests/test_deployment.py`: the helper (SPEC §6), `test_oauth_override_declares_the_named_volume`,
  `test_oauth_override_mounts_only_the_named_volume`, and the rewritten body of `test_compose_mounts_no_host_directory_by_default`
  (test_deployment.py:80). Run them against the unchanged compose files and record the failures in the implementation report. [V1, V3]
- [ ] C. `docker-compose.oauth.yml`: top-level `volumes:` with `twitch-mcp-data:` and `name: twitch-mcp-data`; replace
  `- ./data:/data` with `- twitch-mcp-data:/data`; rewrite the header comment. [V1, V2]
- [ ] D. Comment-only edits: `docker-compose.yml` lines 7–8, `Dockerfile` lines 43–44 (instruction unchanged),
  `.env.example` lines 81–83, `scripts/lib/common.sh` lines 54–55. [V4, V8]
- [ ] E. `scripts/setup.sh` lines 238–244: remove the `# R48` comment, the `mkdir -p "${ROOT_DIR}/data"`, the Token folder
  line and the `sudo chown` message; print the one volume line from V6. [V6]
- [ ] F. `scripts/stop.sh` header comment (lines 2–3) names the volume. [V6]
- [ ] G. `.gitignore` comment rewrite (line 5, "legacy token folder (older versions)"); delete `data/.gitkeep` from disk,
  then remove the empty `data/` folder. Keep the `data/` rule. [V7]
- [ ] H. Docs (V8 list in SPEC §5 only): `README.md:66` and `:416` rewritten, plus the V5 operator notes and the legacy-upgrade
  note; `CLAUDE.md:167–168`; `docs/SPEC.md` R2 (126–127), R48 (259), §6.1 (322), Amendment 6 status line (set to Approved
  on approval); `CHANGELOG.md` `[Unreleased]` entry with the V5 notes. Do NOT change `README.md:146`, `CLAUDE.md:24`, `CLAUDE.md:157`. [V5, V8]
- [ ] I. Run the gate: `make check` in WSL (venv outside the drive). Single file: `pytest tests/test_deployment.py --no-cov`.
  All green, coverage at least 90% branch, no timeout raised. [V1, V3, V4, V5]
- [ ] J. Reviewer subagent (read-only) writes `docs/reports/token-volume/review-round-N.md`; fix loop max 5 rounds.
  Reviewer checklist (not tests):
  - no script runs `down -v`, `down --volumes`, `volume rm` or `volume prune` (grep the `scripts/` tree);
  - V2: the override's `TWITCH_OAUTH_TOKEN_FILE` is still `/data/twitch_user_token.json`;
  - V6 setup.sh message names `twitch-mcp-data` and `disconnect_twitch_account`, and creates no folder;
  - V7 `.gitignore` keeps `data/`; `data/.gitkeep` is gone;
  - V8 every doc line in SPEC §5 matches, and nothing else changed;
  - `oauth.py`, `twitch_client.py`, `Dockerfile` instruction unchanged (git diff).
- [ ] K. Operator-run live check (SPEC §8 steps 1–6), recorded by the operator in the summary report. [V9]
- [ ] L. Summary report at `docs/reports/token-volume/summary-report.md`. Do not commit unless the human asks.
