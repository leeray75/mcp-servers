# SPEC — User-token store on a Docker named volume (deployment fix, Amendment 6)

**Title:** Keep the 0600 user-token store on a Docker-managed named volume instead of the `./data` bind mount
**Status:** Approved (approved by: human, 2026-10-31). Gate confirmations: V-D2, V-D3, V-D4 and V-D5 confirmed.
**Date:** 2026-10-31
**Amends:** [`docs/SPEC.md`](../../SPEC.md) R2 (mount wording, line 126–127), R48 (store location, line 259) and §6.1
(directory layout, line 322). The container invariant in `CLAUDE.md` (the "Container (R1/R2)" bullet) is amended the same
way. R1 has no mount wording and is unchanged. R46, R47, R49–R52 are unchanged. The `/certs` read-only mount for TLS
(T11) is unchanged. Requirements added here are `V1`–`V9`.

---

## 1. Problem / motivation

Live on 2026-10-31 with OAuth and TLS working end to end (token exchange `200`, validate `200`), the callback still
fails with outcome `upstream`: the token file cannot be saved. The repository lives on the Windows drive (`/mnt/c`,
drvfs). Docker bind-mounts `./data` from there. A file created by the container user (UID 10001) appears owned by UID
1000, so `Path.chmod(0o600)` in `oauth.py` `_write_private` (line 367; the `temp.chmod(0o600)` at line 365 fails the same
way) raises `PermissionError`. That is an `OSError`, which surfaces as `upstream` (`TokenStore.save` calls
`_write_private` at line 419). The reproduction is in the running container. The `0600` store (R48) therefore cannot
work on this host layout, and no code change to `_write_private` can fix a filesystem that does not honour POSIX modes.

## 2. Goals

- The token store is written at `0600` on a native Linux filesystem inside Docker.
- The token file is not in the Windows-visible repository tree.
- The base deployment (no OAuth) still mounts nothing.
- No change to the MCP tool set, the wire behaviour, the token-file format or the R1 loopback bind.

## 3. Non-goals

- No change to `oauth.py` or `twitch_client.py`.
- No migration of an existing `./data/twitch_user_token.json` into the volume (see V5 operator notes).
- No new Python dependency. The compose tests use one small line-based helper (V1), not a YAML parser.
- No change to `Dockerfile` (the `chown 10001:10001 /data` line stays; see V4).
- No automatic volume deletion, pruning or backup script.
- No test for script behaviour or for docs wording. Those are checked in review (V6, V8).

## 4. Decisions

| ID | Question | Decision |
|---|---|---|
| **V-D1** | How is `/data` provided with OAuth on? | **Docker named volume** at `/data` in `docker-compose.oauth.yml`, replacing the `./data:/data` bind mount. A fresh named volume is initialised from the image's `/data`, which is already `10001:10001` (Dockerfile:45, runtime stage), so the `0600` write works on native Linux storage. Human decision, given. |
| **V-D2** | Volume name | Top-level `volumes:` entry `twitch-mcp-data` with `name: twitch-mcp-data`, so the name does not depend on the compose project name. Service mount: `twitch-mcp-data:/data`. **(Recommended)** |
| **V-D3** | What happens to the repo's `data/` folder? | Delete the untracked `data/.gitkeep` (not in git: `git ls-files data` is empty; `git check-ignore` reports it ignored by `.gitignore:6`). **Keep** the `data/` line in `.gitignore`, because an old token file may still sit in a local `./data/` and must never be committed. Nothing mounts `./data` any more. **(Recommended)** Alternative: remove the `data/` ignore line too; rejected because it would make a leftover secret committable. |
| **V-D4** | Operator lifecycle | The credential persists across `stop`, `restart`, `rebuild` and `docker compose down` (the volume is not removed). `scripts/stop.sh:16` uses `compose down --remove-orphans`, which never removes named volumes. To delete the stored credential: `disconnect_twitch_account` (deletes the file and revokes it), or after `scripts/stop.sh`, `docker volume rm twitch-mcp-data`. **(Recommended)** |
| **V-D5** | Invariant wording | R2, R48, §6.1 and the `CLAUDE.md` container invariant say: "no host folder is mounted writable; user OAuth adds one named volume at `/data`; TLS adds `/certs` read-only." The "only host mount" wording is removed. |

**Gate items for the human** (asked in the approval request, not left open in this file): confirm V-D2 to V-D5.

## 5. Requirements

- **V1** — `docker-compose.oauth.yml` declares a top-level `volumes:` key containing `twitch-mcp-data:` with
  `name: twitch-mcp-data`, and the `twitch-mcp` service's volume list is exactly `["twitch-mcp-data:/data"]`. This
  rejects host paths, `./` or `/` sources, and long-form entries such as `type: bind`. Tested by the helper in §6.
- **V2** — The `/data` target and `TWITCH_OAUTH_TOKEN_FILE: /data/twitch_user_token.json` are unchanged. Checked by
  review (the environment block is not parsed by the helper) and by the live check (§8 step 4).
- **V3** — `docker-compose.yml` (base) has no top-level `volumes:` key and an empty service volume list. Tested by the
  helper in §6. The top-level volume exists only in the override.
- **V4** — `Dockerfile` is unchanged: `RUN mkdir -p /data && chown 10001:10001 /data` stays, because a new named volume
  inherits that owner. The existing test `test_the_data_directory_belongs_to_the_service_user` (test_deployment.py:43)
  keeps passing.
- **V5** — `oauth.py` and `twitch_client.py` are unchanged. The existing `0600` unit test
  (`tests/test_oauth_units.py::test_token_file_is_0600_round_trips_and_degrades`, line 152) and
  `tests/test_server.py:735` keep their assertions.
  **Operator notes** (written into README and CHANGELOG under V8): (a) an existing `./data/twitch_user_token.json` is
  left in place by the upgrade; it is a live refresh token, so delete it after re-signing in; (b) turning
  `TWITCH_USER_OAUTH` off leaves the volume, and so the token, in place until `docker volume rm twitch-mcp-data`;
  (c) an existing volume is not re-initialised: Docker copies the image's `/data` only into an empty volume.
- **V6** — `scripts/setup.sh` lines 238–244 are replaced. That block holds the `# R48` comment, the
  `mkdir -p "${ROOT_DIR}/data"`, the `Token folder` line and the `sudo chown` message. The replacement creates no
  folder, prints no `chown` instruction, and prints one line stating that the token is kept in the Docker volume
  `twitch-mcp-data` and is removed only by `disconnect_twitch_account` or `docker volume rm twitch-mcp-data`.
  `scripts/stop.sh` header comment (lines 2–3) is rewritten to name the volume. The `.ps1` twins are checked and need
  no change. No script runs `down -v`, `down --volumes`, `volume rm` or `volume prune`; this is a reviewer checklist
  item (tasks.md), not a test.
- **V7** — `.gitignore`: the `data/` rule stays, with its comment rewritten to "legacy token folder (older versions);
  never commit". `data/.gitkeep` is deleted from disk. `.dockerignore` is unchanged. Checked by review (disk state).
- **V8** — Documentation in step with the change. Files and exact lines to change:
  - `README.md:66` (the `./data` / `setup.sh` sentence) and `README.md:416` (security posture mount line): rewritten.
    Add the operator notes from V5 and a short legacy-upgrade note: "older versions stored the token in `./data`; delete
    any `twitch_user_token.json` there after upgrading."
  - `README.md:146` and `CLAUDE.md:24` and `CLAUDE.md:157`: no mount wording; **no change**.
  - `CLAUDE.md:167–168` (container invariant): rewritten per V-D5.
  - `docs/SPEC.md` R2 (lines 126–127), R48 (line 259), §6.1 (line 322): rewritten per V-D5. R1 unchanged.
  - `docker-compose.oauth.yml` header (lines 1–8), `docker-compose.yml` header (lines 7–8), `Dockerfile` comment
    (lines 43–44), `.env.example` (lines 81–83), `scripts/lib/common.sh` comment (lines 54–55): rewritten.
  - `.gitignore` comment (V7).
  - `CHANGELOG.md` `[Unreleased]` (line 5): new entry, including the V5 operator notes. Lines 41 and 65–66 describe a past
    release and stay as history.
  Checked by review against this list.
- **V9** — The live check in §8 passes (operator-run, recorded in the implementation report, not part of `make check`).

## 6. Design

Files changed:

| File | Change |
|---|---|
| `docker-compose.oauth.yml` | `- ./data:/data` becomes `- twitch-mcp-data:/data`; add top-level `volumes:` with `twitch-mcp-data:` and `name: twitch-mcp-data`; header comment (V1, V8) |
| `docker-compose.yml`, `Dockerfile`, `.env.example`, `scripts/lib/common.sh` | Comment-only edits (V8) |
| `scripts/setup.sh` (lines 238–244), `scripts/stop.sh` (lines 2–3) | V6 |
| `.gitignore` | Comment only (V7) |
| `README.md`, `CLAUDE.md`, `docs/SPEC.md`, `CHANGELOG.md` | V8 |
| `data/.gitkeep` | Deleted from disk (untracked) |
| `tests/test_deployment.py` | One helper and three tests (below) |

**Helper** (one function in `tests/test_deployment.py`, no other parsing). Input: a compose file path. It reads the text,
drops blank lines and lines whose stripped text starts with `#`, and removes a trailing ` #...` comment from each
remaining line. It returns:

- `top_keys`: the names of the zero-indent keys (lines ending in `:` with no leading whitespace);
- `service_volumes`: the list items (`- ...`, stripped) that sit directly under `volumes:` at the indentation of the
  `twitch-mcp` service's `volumes:` key, found by indentation under `services:` → `twitch-mcp:` → `volumes:`. Items
  nested deeper (long-form fields) are not returned, so `type: bind` shows up as a non-matching item.

**Tests** (in `tests/test_deployment.py`):

- `test_oauth_override_declares_the_named_volume` (V1): `"volumes" in top_keys` for the override; the top-level
  `volumes:` block contains a `twitch-mcp-data:` key and the line `name: twitch-mcp-data`.
- `test_oauth_override_mounts_only_the_named_volume` (V1, V2 target): `service_volumes == ["twitch-mcp-data:/data"]`.
  This one assertion covers the host-path rejection, the long-form rejection and the `/data` target.
- `test_base_compose_declares_no_volumes` (V3): for `docker-compose.yml`, `"volumes" not in top_keys` and
  `service_volumes == []`. This replaces the body of the existing `test_compose_mounts_no_host_directory_by_default`
  (test_deployment.py:80), which kept its name and its `split("services:")` text trick; the text trick is removed.

Existing tests kept unchanged: `test_the_container_runs_as_the_documented_unprivileged_uid`,
`test_the_data_directory_belongs_to_the_service_user` (V4), `test_the_image_starts_the_same_entry_point_the_tests_cover`,
`test_compose_publishes_on_host_loopback_only` (R1), `test_compose_applies_every_hardening_switch` (R2).

Self-check: the helper must return `["twitch-mcp-data:/data"]` for the new override; a commented-out `# - ./data:/data`
line must not appear. Both are covered by the stripping rule.

No test for scripts or docs (V6, V7, V8): checked by review. No dependency is added.

## 7. Security and invariants

- The credential stays in a Docker-managed volume under the Docker data root, not under the Windows-visible repository
  tree (`/mnt/c`). It is no longer readable or editable from the Windows drive by default.
- `0600` is enforced by the existing `_write_private` on a native filesystem, where POSIX modes apply.
- The volume is mounted only at `/data`. It is not exposed on any port, and the container's root filesystem stays
  read-only (R2). The container user is still UID 10001 with all capabilities dropped.
- The R1 loopback bind is untouched; `test_compose_publishes_on_host_loopback_only` keeps passing.
- Logging, `/healthz` and tool results are unchanged (R52–R54).
- Deliberate change to R2 and the container invariant: "the only host mount" becomes "no host folder is mounted
  writable; user OAuth adds one named volume at `/data`" (V-D5). `/certs` stays read-only.

## 8. Test plan and acceptance criteria

Automated (the gate, `make check` in WSL with the venv outside the drive):

- `make check`: ruff, `mypy --strict`, pytest. Coverage gate 90%, branch. Per-test timeout 30 s (`pytest-timeout`,
  signal method). Do not raise the timeout (CLAUDE.md, Commands).
- Acceptance: V1 and V3 pass their helper tests; V2, V4–V8 pass by existing test or by review; the gate is green.
  Single-test run: `pytest tests/test_deployment.py --no-cov`.

Operator-run live check (**not** part of `make check`; recorded by the operator, not by the implementer):

1. `scripts/rebuild.sh` (with `TWITCH_USER_OAUTH=true` and TLS configured), then `scripts/restart.sh --recreate`.
2. `docker volume inspect twitch-mcp-data` shows the volume exists.
3. Sign in through the TLS redirect. The callback reports success (no `upstream` outcome).
4. `docker exec twitch-mcp stat -c '%u:%g %a' /data/twitch_user_token.json` prints `10001:10001 600`.
5. `/healthz` reports `tool_count` 25 after sign-in. CLAUDE.md (Invariants, "Tool set") fixes this: 23 reads plus 2
   account tools, pinned by `tests/test_server.py`.
6. `scripts/stop.sh` then `scripts/start.sh`: the token survives (still `600`, and the account tools still work).

Note: Docker copies the image's `10001:10001` ownership into a new empty named volume. Step 4 verifies this. An existing
volume is not re-initialised (V5 note c).

## 9. Out of scope

- Migrating an existing `./data` token into the volume.
- Changing `_write_private` or adding a fallback for filesystems without POSIX modes.
- Backup or export of the volume.
- Any change to the tool set, to R-numbers outside those listed under "Amends", or to the TLS and OAuth wire behaviour.
- Committing, pushing or tagging (only with the human's request).

## 10. Process

Per AGENTS.md §2–§8: the human approves this spec (Phase 3) before any code. Then an implementer subagent applies §5–§6,
a separate reviewer subagent runs the review loop (max 5 rounds), and a summary report is written under
`docs/reports/token-volume/`. The reviewer checklist is in tasks.md. The live check in §8 is run by the operator and
recorded in that report.

## 11. Open questions

None.
