# Spec fix round 1: docs/specs/oauth-iss (finding → fix)

Responds to `docs/reports/oauth-iss/spec-review-round-1.md` (CHANGES REQUESTED). Markdown only. No source, test,
config or CLAUDE.md edits; nothing run; nothing committed.

| Finding | Fix | Where |
|---|---|---|
| M1: `PendingFlows` does not exist; class is `PendingStore` (oauth.py:137) | Renamed everywhere | SPEC §1, §5 I1, §6.1; tasks B |
| M2: wrong CLAUDE.md line (11 is the SDD note) | Cited by bullet name: OAuth (R46–R51) invariants, phrase "an `iss` check" | SPEC §6.5, §12; tasks I |
| M3: no success HTTP test exists; §6.3 misdescribed | Two NEW tests specified in `tests/test_server.py` near line 614: (1) start → callback with code+state only → 200 and one `authorization_code` POST; (2) wrong `iss` → 400, no new token POST, flow dropped. Fake Twitch gap named: `POST /oauth2/token` (`tests/server_fakes.py:174–178`) answers app-token only and must gain an `authorization_code` branch plus a validate route for the user bearer | SPEC §6.4; tasks G, H |
| m1: CHANGELOG and comments missing | `CHANGELOG.md` `[Unreleased]` → `Fixed` entry and line ~38 wording; comment-only edits at `oauth.py` ~11, ~138, ~168 and `security.py` ~9–10. Note: the `claim` docstring (~168) does not name `iss`, so it gets an added "checked when present" line, not a replacement | SPEC §6.1, §6.5; tasks B, C, J |
| m2: no mechanism for the driver change | One helper `_callback(flow, pending, *, state=None, iss=None, code="code-1")` in `tests/test_oauth_units.py`, `iss` default omitted. Only the correct-`iss` case (b) passes `iss` explicitly. Existing `state="wrong"` case unchanged | SPEC §6.3; tasks E |
| m3: I3 verification vague | Unit case required (§6.3(d)); HTTP `?iss=` case optional | SPEC §5 I3, §8; tasks E |
| m4: no test required to fail on old code | Unit (a) and HTTP test 1 must fail against unfixed `oauth.py:183`; implementation report records one targeted run or line reading. Report also records that I-D2's default-omit applies to every driver of `claim()`/`complete()` | SPEC §6.3, §6.4, §8; tasks F, E |

Unchanged, as the reviewer confirmed: I1–I6 scope, I-D1 and I-D2 wording, the security reasoning, the `docs/SPEC.md`
pointer, and the absence of any `iss` logging or echo.

Open for the human: I-D2 (derived) is still to be confirmed at the approval gate. Open questions: None.
