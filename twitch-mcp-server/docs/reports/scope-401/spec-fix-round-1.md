# Spec fix round 1: scope-401 (Amendment 7)

Addresses `docs/reports/scope-401/spec-review-round-1.md` (1 blocker, 5 major, 7 minor). Markdown only: `SPEC.md`,
`tasks.md`, this report. No code, tests or config changed; nothing run; nothing committed.

## Resolution

| Item | Resolution in SPEC.md |
|---|---|
| B1 sign-in deadlock | Circuits cover Helix only (C-D9). `fetch_validate`, `post_form` and boot `validate_token` never check or trip a circuit. Tests N7 (sign-in with open user circuit succeeds and clears it). |
| M1 open app circuit wipes user token | Same as B1: `refresh_now`'s grant goes through `post_form`, which is not circuit-checked. Test N8 (refresh under open app circuit keeps the token file). C-D7 removed. |
| M2 `on_install` redundant | Dropped C7's `on_install`, `OAuthFlow.on_install` and task G. The reset rides `oauth.on_invalidate(service.limiter.reset_user_circuit)` at `server.py:1744`. Stated: it also runs on `forget()`, harmlessly. Safety noted: listeners are synchronous and `_invalidate` suppresses exceptions. |
| M3 write scope-401 | Human decision C-D4 (replaced): a missing-scope write `401` is `kind=permission`, current behaviour, matching the scope `403`. Non-scope write `401` stays `unknown_outcome` (C6). Writes never retry (R16). Test N13. |
| M4 false "holds no scopes" | C-D10: the sentence is added only when the body has a `scopes` list. N1–N3 assert it is absent. |
| M5 scope creep | C8 folded into C3 (N10). C6/C-D6 narrowed to one `user_token_rejected` degraded block (C5). C-D8/N11 hint cap dropped; only control-character sanitising remains (C2, N11). N13 grep dropped. Client, service and wiring property wrappers removed; wiring keeps one `auth_failed` for healthz (C8). C10 (healthz) kept. |
| m1 stale text | Listed in SPEC §6.1 and tasks C: `twitch_client.py` line 23, comments at 77–82, `_error_code` docstring (292–299), `errors.py` line 9, CLAUDE.md line 92, SPEC R44 wording (§6.3 markers). |
| m2 N1 failing order | N1–N3 assert `kind == "permission"` first, so they fail on the unfixed code for the right reason. |
| m3 N14 passes on unfixed code | Labelled a guard, not failing-first. Added a `403` with `message` "missing" and no "scope". |
| m4 hint sanitising | Added: control characters and newlines removed from the scope text (C2, N11). No length cap. |
| m5 N3 route | N3 registers its own `GET /helix/moderation/banned` fake; the `test_service.py` rows at 169–172 are write cases only. |
| m6 tasks | Tasks B, I and K cover the B1/M1 tests. |
| m7 listener and wording | C3 states the listener fires for either class and clears identity and confirm tokens for either. C4 states the user circuit opens only when a user token exists. |
| CLAUDE.md service bullet | Corrected to line 62; line 79 is the `identity.py` bullet. |
| docs/SPEC.md marker lines | Corrected to ~185, ~248, ~249, ~347–358. |

## Gate items

Human-decided: C-D1(a), C-D1(b), C-D2, C-D4 (replaced: missing-scope write `401` stays `permission`).

Derived, confirm at the gate:
- C-D3: user circuit cleared by the `on_invalidate` listener on token install (sign-in or refresh), and also on `forget()`.
- C-D5: `/healthz` degrades on the app circuit only.
- C-D9: circuits cover Helix calls only; sign-in, refresh, revoke, validate and the mint are never gated. This narrows R24 and R50 and removes the earlier C-D7. Flag prominently.
- C-D10: "holds" sentence only when the body has a `scopes` list.

## Requirement list after the fix

- C1: `message` read only to recognise a missing-scope phrase on `401`/`403`.
- C2: missing-scope `401`/`403` → `permission`, no circuit; hint sanitised; holds-sentence only with a `scopes` list.
- C3: Helix-only, per-class circuit; listener once per class.
- C4: non-scope user Helix `401` opens the user circuit only.
- C5: non-scope app Helix `401` opens the app circuit only; `twitch_account_status` reports `user_token_rejected` degraded instead of raising.
- C6: write `401`: scope → `permission`, no circuit; non-scope → `unknown_outcome` and the write's class circuit (R16).
- C7: user circuit reset via the `on_invalidate` listener (sign-in, refresh, forget); app circuit never reset.
- C8: `/healthz` degraded on the app circuit only; R3 keys unchanged.
- C9: sign-in, refresh, revoke, validate and mint never gated by a circuit.
- C10: README and `.env.example` scope docs and copy-paste line; verified against the scopes page; no code scope table.

## Traceability (tests in SPEC §8)

C1, C2: N1–N3, N11, N14. C3: N10 and the changed R24 tests. C4, C5: N4–N6. C6: N13. C7: N7–N9. C8: N12. C9: N7, N8. C10: docs, reviewed by hand.
