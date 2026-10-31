# Spec fix round 1: docs/specs/token-volume (against spec-review-round-1.md)

Verdict of the review: CHANGES REQUESTED (4 majors, 6 minors). All addressed in SPEC.md and tasks.md. Markdown only; nothing run; nothing committed.

| Item | Resolution |
|---|---|
| M1 | Dropped `test_no_script_removes_the_token_volume`. Now a reviewer checklist item in tasks.md J (grep for `down -v`, `down --volumes`, `volume rm`, `volume prune`). V6 keeps only the setup.sh message requirement. |
| M2 | Helper strips full-line and trailing `#` comments and reads structure by indentation, so commented-out lines cannot satisfy a test (SPEC §6). |
| M3 | One helper returns top-level keys and the `twitch-mcp` service volume items by indentation. Tests: oauth override declares the named volume; service volume list equals `["twitch-mcp-data:/data"]` (rejects host paths and long-form bind); base compose has no top-level `volumes:` and no service volumes. The text-split test is replaced. |
| M4 | `test_no_file_still_bind_mounts_the_data_folder` removed (covered by the equality test). Docs and scripts are checked in review, not by grep. README keeps a short legacy-upgrade note. |
| m1 | oauth.py cited at line 367 (`path.chmod`), with `temp.chmod` at 365. |
| m2 | setup.sh block cited as lines 238–244. |
| m3 | Operator notes added (V5): leftover `./data` token must be deleted; disabling OAuth leaves the volume until `docker volume rm twitch-mcp-data`; existing volume is not re-initialised. Written into README and CHANGELOG (V8). |
| m4 | V8 list corrected: README:146, CLAUDE.md:24 and :157 no change; `scripts/lib/common.sh` added; R1 unchanged. "Amends" header aligned with V-D5 (R2, R48, §6.1, CLAUDE.md container invariant). |
| m5 | "pre-V6" replaced with "older versions" in .gitignore wording (V7). |
| m6 | tool_count 25 cited to CLAUDE.md "Tool set" invariant. §11 is now plain "None."; the gate items are listed in §4 under Decisions. |

Open for the reviewer: confirm the helper rules (comment stripping, indentation) are specific enough to implement without further design.
