# Spec fix round 2: scope-401 (Amendment 7)

Addresses `docs/reports/scope-401/spec-review-round-2.md` (0 blockers, 1 major, 5 minor). Markdown only: `SPEC.md`,
`tasks.md`, this report. No code, tests or config changed; nothing run; nothing committed.

## Resolution

| Item | Resolution in SPEC.md |
|---|---|
| M1 N7/N8 cannot detect the regression | N7 and N8 now use a real `TwitchClient` over `FakeTwitch` (`make_client`, `tests/core_fakes.py:84`), `UserAuth` wired through `attach_grant(lambda form: client.post_form(OAUTH_TOKEN_PATH, form))` and `attach_validate(lambda b: _validate_pair(client, b))`, the circuit tripped by a real Helix `401`. Both are recorded as failing-first. New N15 is the client-level test: with both circuits open, `fetch_validate` and `post_form` still send requests; a validate `401` on a closed circuit raises `unauthenticated` and trips nothing. |
| m1 read hint | C3 now gives the class-correct hint: user class, "Sign in again with the connect_twitch_account tool. A successful sign-in clears this block."; app class, "Restart the container; the app-token circuit never resets itself." The first-call `_map` hint and the later-call `acquire` hint match. Validate (`trip_circuit=False`) carries no circuit hint. Lines 775–778 are replaced (SPEC §6.1, tasks E). |
| m2 C-D9 wording | C-D9 now states: "R24's circuit stops Helix API calls of the affected token class; token, validate and revoke endpoints are never gated; R50 unchanged", with the mint working under an open app circuit. The inexact "R24 and R50 read Helix only" rationale is removed. The `docs/SPEC.md` R24 marker carries the same sentence (SPEC §6.3). |
| m3 C5 `user_token_rejected` block | Dropped. C5 is now unchanged `twitch_account_status`: a rejected user token still makes it raise `unauthenticated`, as today (listed in §3 and §9). Its test is removed. `degraded_reason` is not used. |
| m4 in-flight old-token race | Recorded in SPEC §7 as an accepted known limitation: an in-flight old-token `401` after a reset re-opens the user circuit until the next sign-in, refresh or restart. No mechanism. |
| m5 N13 wording and N5 split | N13 now says only the circuit assertion at `tests/test_identity.py:546` changes; the existing test already posts a non-scope body (line 536). N5 is split into two assertions: (a) user Helix `401` opens the user circuit and app reads still work; (b) a validate `401` on a closed circuit raises `unauthenticated`, no hint, trips nothing. |

## Gate items

Human-decided: C-D1(a), C-D1(b), C-D2, C-D4 (replaced: missing-scope write `401` stays `permission`).

Derived, confirm at the gate:
- C-D3: user circuit cleared by the `on_invalidate` listener on token install (sign-in or refresh), and also on `forget()`.
- C-D5: `/healthz` degrades on the app circuit only.
- C-D9: R24 stops Helix calls of the affected class; token, validate and revoke endpoints and the mint are never gated. R50 unchanged. Flag prominently.
- C-D10: "holds" sentence only when the body has a `scopes` list.
- Accepted limitation (§7): the in-flight old-token `401` race after a reset.

## Requirement list after the fix

- C1: `message` read only to recognise a missing-scope phrase on `401`/`403`.
- C2: missing-scope `401`/`403` → `permission`, no circuit; hint sanitised; holds-sentence only with a `scopes` list.
- C3: Helix-only per-class circuit; class-correct hint; listener once per class.
- C4: non-scope user Helix `401` opens the user circuit only.
- C5: non-scope app Helix `401` opens the app circuit only; `twitch_account_status` unchanged.
- C6: write `401`: scope → `permission`, no circuit; non-scope → `unknown_outcome` and the write's class circuit (R16).
- C7: user circuit reset via the `on_invalidate` listener (install and forget); app circuit never reset.
- C8: `/healthz` degraded on the app circuit only; R3 keys unchanged.
- C9: token exchange, refresh, revoke, mint and validate never gated; a validate `401` does not trip.
- C10: README and `.env.example` scope docs and copy-paste line; verified against the scopes page; no code scope table.

## Traceability (tests in SPEC §8)

C1, C2: N1–N3, N11, N14. C3: N10 and the changed R24 tests. C4: N4. C5: N5a, N6. C6: N13. C7: N7–N9. C8: N12. C9: N7, N8, N15. C10: docs, reviewed by hand.
