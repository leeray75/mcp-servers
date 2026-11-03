# Spec fix round 3: scope-401 (Amendment 7)

Addresses `docs/reports/scope-401/spec-review-round-3.md` (0 blockers, 1 major, 4 minor). Markdown only: `SPEC.md`,
`tasks.md`, this report. No code, tests or config changed; nothing run; nothing committed.

## Resolution

| Item | Resolution in SPEC.md |
|---|---|
| M1 write hint replaced | The class hint applies only to the read `unauthenticated` raise (C3, §6.1). A non-scope write `401` keeps `_unknown_outcome`'s R16 hint ("Do not re-issue this call ..."), unchanged (C6, §6.1). N13 asserts the "Do not re-issue" text is present and the class sign-in or restart text is absent. Tasks E and I updated. |
| m1 N7 failing on missing API | N7 wires the reset as `on_invalidate(lambda: client.limiter.reset_user_circuit())`, so a missing method fails on behaviour. The test asserts the token file first, then `client.limiter.auth_failed(USER_CLASS) is False`. On the unfixed code the single process-wide circuit refuses `post_form` (the grant, an app-class acquire), so `exchange` fails with `unauthenticated` before the token file is written. |
| m2 N7 token body | N7 states the faked token response carries no `user_id`, so `exchange` runs `_resolve_user` and a validate call. The faked validate answers `200` with `user_id` and `login`. |
| m3 duplicate tests | N5a and N5b removed. N4 now covers both directions of per-class isolation at client level (user circuit leaves app reads working; app circuit leaves user reads working). N15 carries the validate-`401`-trips-nothing check. N6 is reduced to the app-circuit-refuses-app-reads case at service level. |
| m4 counting drift | SPEC header and tasks.md title now say `C1`–`C10`. Section 5 lists C1–C10. |

## Gate items

Human-decided: C-D1(a), C-D1(b), C-D2, C-D4 (replaced: a missing-scope write `401` stays `permission`).

Derived, confirm at the gate:
- C-D3: user circuit cleared by the `on_invalidate` listener on token install (sign-in or refresh), and also on `forget()`.
- C-D5: `/healthz` degrades on the app circuit only.
- C-D9: R24 stops Helix calls of the affected class; token, validate and revoke endpoints and the mint are never gated; R50 unchanged. Flag prominently.
- C-D10: "holds" sentence only when the body has a `scopes` list.
- Accepted limitation (§7): the in-flight old-token `401` race after a reset.

## Requirement list after the fix

- C1: `message` read only to recognise a missing-scope phrase on `401`/`403`.
- C2: missing-scope `401`/`403` → `permission`, no circuit; hint sanitised; holds-sentence only with a `scopes` list.
- C3: Helix-only per-class circuit; the class hint applies only to the read `unauthenticated` raise; listener once per class.
- C4: non-scope user Helix `401` opens the user circuit only.
- C5: non-scope app Helix `401` opens the app circuit only; `twitch_account_status` unchanged.
- C6: write `401`: scope → `permission`, no circuit; non-scope → `unknown_outcome` with the R16 hint, and the write's class circuit.
- C7: user circuit reset via the `on_invalidate` listener (install and forget); app circuit never reset.
- C8: `/healthz` degraded on the app circuit only; R3 keys unchanged.
- C9: token exchange, refresh, revoke, mint and validate never gated; a validate `401` does not trip.
- C10: README and `.env.example` scope docs and copy-paste line; verified against the scopes page; no code scope table.

## Traceability (tests in SPEC §8)

C1, C2: N1–N3, N11, N14. C3: N10 and the changed R24 tests. C4, C5: N4, N6. C6: N13. C7: N7–N9. C8: N12. C9: N7, N8, N15. C10: docs, reviewed by hand.
