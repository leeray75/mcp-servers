# Spec review, round 1: docs/specs/token-volume/SPEC.md (+ tasks.md, Amendment 6 pointer)

Reviewer: read-only. Verdict: **CHANGES REQUESTED** (0 blockers, 4 majors, 6 minors).

## Verified correct
- Docker named-volume initialisation (empty volume, copies image /data content and ownership) is relied on correctly.
  `chown 10001:10001 /data` is Dockerfile:45, in the `runtime` stage (FROM at :27; USER at :47). Dockerfile unchanged is right.
- Top-level `volumes:` with `name:` in an override file merges with the base file (base has no top-level volumes).
  `read_only: true` rootfs and `tmpfs: /tmp` are unaffected by a volume at /data. `compose down --remove-orphans`
  (stop.sh:16) never removes named volumes; no script contains `down -v`/`volume rm`/`volume prune` (grep).
- Cited items exist: oauth.py:419 (`_write_private` call), test_oauth_units.py:152, test_server.py:735,
  test_deployment.py:43/80/81/83-84 (85 lines), Dockerfile 43-45, stop.sh 2-3, common.sh 54-55, .env.example 81-83,
  CHANGELOG 41/65-66, docker-compose.yml 7-8, docs/SPEC.md Amendment 6 line 11, `.gitignore:6`, data/.gitkeep untracked.
- `.ps1` twins contain no `data`/`volume` reference. All 11 AGENTS.md section-4 sections present; Status Draft.

## Majors

**M1. `test_no_script_removes_the_token_volume` contradicts V6's own output.** SPEC.md:62-67 requires setup.sh to print
"removed only by `disconnect_twitch_account` or `docker volume rm`", and V-D4/stop.sh header will likely also mention it.
The test (SPEC.md:103-104) scans `scripts/**` for `volume rm` and so fails on the required message (or forces the
implementer to drop it). Fix: scan only executable lines (strip `#` comments and exclude printf/echo text) and match
real invocations (`docker\s+volume\s+(rm|prune)`, `compose\s+down\b[^\n]*(\s-\w*v\w*|--volumes)`), or phrase the
message without the literal command and say so in V6. Also cover `--volumes` and combined flags (`-fv`), not only `down -v`.

**M2. Text tests can false-pass on commented-out lines.** SPEC.md:99-102 reads the override "as text" and asserts
`name: twitch-mcp-data`, `twitch-mcp-data:/data` and `/data/twitch_user_token.json`. The override's header comment (and
the old `# - ./data:/data`) can satisfy these substrings. Fix: specify that every test first drops full-line and trailing
`#` comments, and that matching is line-anchored regex (e.g. `^volumes:\s*$` top-level, `^\s+-\s+twitch-mcp-data:/data\s*$`,
`^\s+name:\s*twitch-mcp-data\s*$` under the `twitch-mcp-data:` key). Include a negative self-check case if cheap.

**M3. V1 "no host path" test and V3 second clause are under-specified/untested.** SPEC.md:99-101 does not say how the
service-level `volumes:` list is isolated from the top-level `volumes:` key (both contain the token `volumes:`); the base
test's `split("services:")` trick breaks when top-level `volumes:` follows services in the override. V3's clause "top-level
`volumes:` lives only in the override" (SPEC.md:55-56) has no test: the existing test only sees text after `services:`, so a
top-level block placed before `services:` in the base passes. Fix: specify indentation-based extraction (service list =
indented `- ` items under the service's `volumes:`; top-level = column-0 `volumes:`), add an assertion on the base file for
`^volumes:` absent, and reject long-form `type: bind`/`source:` entries too.

**M4. `test_no_file_still_bind_mounts_the_data_folder` scope contradicts V8 and the README edit is unspecified.**
SPEC.md:105-106 scans compose, Dockerfile and README only, yet V8 edits `.env.example`, setup.sh, common.sh, CLAUDE.md,
docs/SPEC.md. README must keep an upgrade note about the legacy `./data` folder (see m3), which the test (`./data` "as a
mount", undefined) would flag. Fix: define the exact pattern (`\./data\s*:/data` and `\./data` on a compose volume line, comments
stripped), extend the file list to compose files, scripts/*.sh, .env.example, and state the README sentence wording so the
test and doc agree (or restrict the README check to the `./data:/data` string).

## Minors
- m1. SPEC.md:18 cites oauth.py "line 368" for `path.chmod`; it is line 367 (368 is `except`). Also `temp.chmod` at 365 runs first.
- m2. setup.sh block to replace is lines 238-244 (the `# R48` comment starts at 238, mkdir at 241); V6/tasks E say 239-244, which leaves a stale "Created here, git-ignored" comment.
- m3. Operator impact is incomplete: say explicitly that an existing `./data/twitch_user_token.json` is left behind (not deleted, still a live refresh token) and that the operator should delete it, or call `disconnect` after re-signing in; put this in README and CHANGELOG. Also state that turning `TWITCH_USER_OAUTH` off leaves the volume (and token) orphaned until `docker volume rm twitch-mcp-data`, and that a pre-existing volume or a later image ownership change is not re-initialised (copy happens only when the volume is empty).
- m4. V8 lists README:146 and CLAUDE.md:24 (and design lists common.sh), but those lines mention no mount (`/data` path only); either drop them or say "no change". V8's file list omits `scripts/lib/common.sh` while §6 and tasks D include it. R1 (docs/SPEC.md:125-126) has no mount wording, so "amends R1" only applies to the invariant text in CLAUDE.md:167; align the Amends header with V-D5.
- m5. V7 comment text "legacy token folder (pre-V6)" is ambiguous (V6 is a requirement, not a release); use "pre-Amendment 6".
- m6. §8 step 5 asserts `tool_count` 25 without a cited source (not verified here); cite the pinned-tool-set test or drop the number. §11 says "None" yet adds "need the human's confirmation": AGENTS.md §4 wants it empty; rephrase as a gate item in the approval request, not in §11.

## Traceability
V1 (test 1, M2/M3), V2 (test 2), V3 (existing test + gap M3), V4 (existing test), V5 (existing tests), V6 (test 3 + M1),
V7 (no test; disk-state only, acceptable but note it), V8 (test 4 + M4, rest manual), V9 operator-run. No scope creep found.
