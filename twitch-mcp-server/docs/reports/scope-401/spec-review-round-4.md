# Spec review, round 4: scope-401 (Amendment 7)

Reviewed from disk: `SPEC.md`, `tasks.md`, `docs/reports/scope-401/spec-fix-round-3.md`. Read-only; nothing run.

**Verdict: APPROVED** (0 blockers, 0 major, 0 minor)

## Round 3 dispositions verified

- **M1 (write hint).** C3 now limits the class hint to the read `unauthenticated` raise (first-call `_map` and later-call `acquire`) and says a
  write never receives it. C6 and 6.1 keep `_unknown_outcome`'s hint (`twitch_client.py:700-714`, "Do not re-issue this call...") for the non-scope
  write `401`; only the read hint at `:775-778` is replaced. N13 asserts "Do not re-issue" is present and the class text is absent; tasks E and I match.
- **m1.** N7 wires `on_invalidate(lambda: client.limiter.reset_user_circuit())`, asserts the token file first and `auth_failed(USER_CLASS)` second, and
  correctly says the unfixed failure is the refused grant (`post_form` app-class acquire under the process-wide circuit).
- **m2.** N7 states the faked token body has no `user_id`, so `_resolve_user` and the validate call run.
- **m3.** N5a/N5b are gone; N4 covers both isolation directions at client level, N6 is the service-level app-circuit refusal, N15 carries the validate-401-trips-nothing check.
- **m4.** SPEC header and tasks.md title say C1-C10.

No regression or new scope creep found. `twitch_account_status`, hint length cap, grep test and `on_install` remain out. Cited lines still match the code
(`_unknown_outcome` 700-714, `post_form` 967, `validate_token` 1046, `fetch_validate` 1085/1091/1107, `oauth.py` `_invalidate` at 589/604).
C-D9 is stated once for the gate (4.2, 7, R24 marker); the in-flight race is recorded as an accepted limitation. Ready for the human gate.
