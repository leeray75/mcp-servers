# Fix round 1 — release v0.2.0

Date: 2026-10-25. Actor: implementer (orchestrator-directed). Reviewer verdict was **APPROVED** with 0 blockers,
0 majors and 5 minors. Per `../AGENTS.md` §7 every minor is fixed or explicitly accepted with written justification.
No blocker or major existed to fix. All dispositions below were re-verified against the code by the orchestrator
rather than taken from either subagent's report.

## Dispositions

| ID | Severity | Claim | Disposition | Evidence / justification |
|---|---|---|---|---|
| m-1 | minor | `README.md:12` claims "three resources" but four are registered | **REJECTED — false positive** | Verified at the source and the pinned test, not from either report. `src/sports_mcp/resources.py::register_resources` registers exactly three: `sports://guide`, `sports://leagues`, and the `sports://leagues/{league}/teams` template (its own docstring says the same). `server.py:272` says "eleven sports tools and three resources". The pinning test `tests/test_server.py::test_resources_are_listed_and_readable` asserts `listed == {"sports://guide", "sports://leagues"}` and `templates == {"sports://leagues/{league}/teams"}` — 2 + 1 = 3, and it passes in the gate run. `README.md:12` is in fact the `../vision-mcp-server` sibling sentence, not a resource claim; the resource list is at `README.md:276-278` and names exactly the three. Changing "three" to "four" would have **introduced** the staleness this release exists to remove. Reviewer's file:line and count are both wrong; the claim does not reproduce. |
| m-2 | minor | `nba_summary_final.json` carries June/July game dates while sitting under the "league set on 2026-10-04" sentence | **ACCEPTED as written** | The sentence's subject is when the response was **captured**, not when the game was played ("**real ESPN responses** captured with `tools/capture_fixtures.py` … in two campaigns: the league set on **2026-10-04**…"). The NBA Finals fixture was captured on 2026-10-04; its game legitimately happened 2026-06-06 (`"date":"2026-06-06T00:30Z"`, `season.type: 3`). Both facts are true and the README asserts only the first. Restating game dates would add noise without adding accuracy. |
| m-3 | nit | `.env.example` documents `ESPN_TIMEOUT_SECONDS=10` while the default is the float `10.0` | **ACCEPTED — no inaccuracy, out of release scope** | `config.py:37` is `Annotated[float, Field(ge=1, le=120)] = 10.0`; setting `ESPN_TIMEOUT_SECONDS=10` parses to exactly `10.0`, so the documented value is behaviourally identical and reads more naturally than `10.0`. `.env.example` is not in the approved spec's §5 file list, and the README table (which is) prints `10` too, consistently. Editing it here would widen the release's blast radius for zero user-visible gain. |
| m-4 | minor | The release commit must include the untracked `docs/specs/release-v0.2.0/` directory | **FIXED — in the commit step** | Correct and required: spec §5 lists the release spec plus its SDD reports as part of the release. The commit stages the whole directory, so the branch does not ship with an untracked spec. Confirmed by `git show --stat HEAD` after the commit (see `summary-report.md`). |
| m-5 | minor | The "…for NFL, NBA and MLB" player-endpoint claim is a union across fixture kinds (profiles exist for NFL and NBA only) | **ACCEPTED as written** | The sentence enumerates the four endpoint kinds and then the sports covered by the player fixture set as a whole; it does not claim sport-by-sport coverage of each kind, and every sport named does have player fixtures (`player_stats_*` and `player_gl_*` cover NFL, NBA and MLB). Spelling out per-kind coverage would make the sentence noticeably longer for a distinction no reader needs. The reviewer also accepted it. Flagged here as a known nuance so a later pass can tighten it if desired. |

## Net effect on the six release files

None. m-1 was the only finding that would have changed a file, and it is rejected on evidence. m-4 is satisfied by the
commit itself. No file was re-edited in this round, so the diff the reviewer approved is the diff that ships, and no
regression re-review is required (`../AGENTS.md` §7 step 2 covers "the fixes and for regressions"; there were no
fixes to re-check).

## Accepted items carried forward

None require follow-up work. For completeness, the pre-existing items the release deliberately leaves alone are listed
in `SPEC.md` §7 (historical artifacts, the three per-feature "open items for the human", Dockerfile digest pinning,
the 40 KB structured-budget follow-up, Phase 2 stretch items).
