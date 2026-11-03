# Spec review, round 1: docs/specs/simplification/SPEC.md (+ tasks.md, workflow.md, pointers)

Reviewer: read-only (grep/read only; no tests run). Verdict: **CHANGES REQUESTED** (3 blockers, 3 majors, 6 minors).

## Fidelity to the human's decisions
Decisions 1-7 are all encoded (S-D1..S-D7; S-D4a flags the cache point for the human as required). No decision is dropped.
Scope creep is minimal: only the pyproject-reading unit test (S11, minor 4) and the make-cert/docs edits that follow
from the removals. All AGENTS.md section 4 sections are present, Status is Draft, Open questions is empty.
Verified names exist: `PlaintextRefusalMiddleware` (security.py:113, server.py:95/1889), `_modes_are_expressible`,
`_check_tls_location` (config.py:123/390), `DEFAULT_TLS_HOST_DIR` (config.py:47), `TLS_KEY_MODE`, `MODE_PROBE_NAME`,
`SYNTHETIC_MODE`, `tls_key_mode_rule_skipped` (config.py:407), `tool_calls_per_minute*` (config.py:216-217), `_charge`
(service.py:451), all eight test_tls.py test names in 8.1, `IO_MODULES` (test_ratelimit.py:29), test_server.py:788,
test_service.py:309, test_config.py 277/278/394, twitch_fakes.py 472-473. `extra="ignore"` is at config.py:168, so the S2
tolerance claim holds. Cache consumers in S-D4a are real (server.py:1715-1728 `owner:<id>`).

## Blockers (the spec as written leaves `make check` red)

**B1. tests/test_gate.py R57 sweep fails once R23 loses its tests.** `test_every_specification_requirement_is_named_by_a_test`
(test_gate.py:84-101) requires every `- **R<n>**` line in docs/SPEC.md to be cited by some `tests/*.py`. The spec keeps R23's
text in docs/SPEC.md (decision 6: no rewrite, SPEC.md 11 "Removed") but deletes test_budget.py (the only real citer;
server_fakes.py:300 is also being edited). R23 becomes uncited and the gate fails. The spec never mentions test_gate.py.
Fix: add to S9/8.2/6.1 an explicit mechanism, e.g. make the S2 test cite R23 in its docstring ("R23 superseded by S2"), or add
a `SUPERSEDED` set to test_gate.py beside `SELF_CITING`; state which, and list tests/test_gate.py in 6.1 and tasks.md.

**B2. test_gate.py traceability-table checks fail on the initial-build report.**
`test_every_traceability_row_cites_a_real_test_of_its_own_file` (test_gate.py:116-145) parses
docs/reports/initial-build/implementation-report.md; its row 75 (`| R23 | test_budget.py::...`) cites only deleted tests, so
R23 lands in `uncovered`. Fix: the spec must either update that report row (a historical doc; say so and list it under
S14/6.1) or exempt superseded requirements in the test; pick one and put it in tasks.md.

**B3. `test_every_tls_requirement_is_named_by_a_test_in_this_module` cannot pass as S9 describes.** test_tls.py:936-961 reads
`required` from docs/specs/tls-oauth-redirect/SPEC.md (`^- \*\*T(\d+)\*\*`), which still declares T5 and T8 because existing
requirement text is not rewritten. S9/B7 only shrink `EXPECTED_TLS_REQUIREMENTS` to T1-T4, T6, T7, T9-T12; the assertion
`required == EXPECTED` then fails, and the "cites" sweep also demands T5/T8 be cited. Fix: S9 must say the test subtracts a
`SUPERSEDED_TLS_REQUIREMENTS = {"5","8"}` from `required` (and ignores citations of them), or the TLS spec gets a
machine-readable marker the regex skips. Note T4 (kept, minus its clause) and its test citations must still resolve.

## Major

**M1. Acceptance greps are unachievable.** A-S2 (SPEC.md:215-217) and task A9 (tasks.md:20-21) require no `budget` under
`src/` or `tests/`. Unrelated hits remain: cache.py:120 ("whole budget"), twitch_client.py:528 ("5xx budget"),
ratelimit.py:72, tests/test_write_safety.py:75/100/123 (read-retry budget tests). Fix: narrow the grep to `ToolBudget`,
`twitch_mcp.budget`, `budget.py`, `tool_calls_per_minute`, `TOOL_CALLS`, and word-boundary "tool.call budget"/"R23".

**M2. test_tls.py edits are under-listed, so the A-S2 grep and imports break.** test_tls.py:49-56 imports `DEFAULT_TLS_HOST_DIR`,
`TLS_KEY_MODE`, `_check_tls_location`; `_write_pair` defaults `key_mode=TLS_KEY_MODE` (line 72) and line 480 uses it; docstrings at
lines 11, 76, 191, 200 cite the middleware/owner-only key. 8.1 deletes tests but never says to fix the imports, helper
default, fixture wording (`"owner-only key"`, line 191) and the module docstring. Fix: add a 6.1/tasks B-row listing these.

**M3. Documentation inventory (S14) misses live statements of removed behaviour.** README.md:117 ("refused with 400") and :394
(troubleshooting row "400 ... cleartext is refused (T8)"), README.md:148 also carries the repo-tree clause;
docker-compose.tls.yml:11 ("key must be mode 0600 ... or config.py refuses (T4, T-D7)"); docs/SPEC.md:455 (test_budget.py row)
and the 6.1 directory list. S14 says no doc may describe these as current, but 6.1 omits them. Fix: add rows for each.

## Minor

1. 6.1 `tests/test_service.py` row cites lines 145-146/211-215, which are in `service_fakes.py`; `service_fakes.py` also needs
   lines 167-168 (`tool_calls_per_minute*` kwargs), 243 and 263 (constructor/`budget=` field). Correct the references.
2. 6.1 row "tests/test_tls.py (make-cert assertions)" is speculative: the make-cert tests (687-757) assert no warning text. Drop or
   say "verify none".
3. Header line 6 "Amends ... R19-R23 ..." reads as if R19-R22 change; say "R23 only (R19-R22 unchanged)".
4. S11's pyproject-reading unit test is the one piece of new test machinery; test_gate.py already pins pyproject text. Prefer a
   one-line assertion added there, or drop it (the gate itself proves the timeout works by existing).
5. S12/hang: pytest-timeout defaults to the signal method; for an async hang in the loop thread state `timeout_method`
   (thread is safer) or confirm signal fires inside asyncio. Otherwise the 30 s guard may not trigger on the reported hang.
6. S7 "GET /oauth/start remains the only 302" is not meaningfully testable route-by-route; reduce to asserting the app has no
   redirect-producing code besides oauth.py, or just the `/healthz`-over-http 200 case. Also service.py module docstring
   numbered list (lines 11+) needs renumbering after "1. budget" is removed.

## Consistency / invariants
SPEC.md, tasks.md and workflow.md agree (S1-S15, A-D-E ordering, `make check`+reading only, 5 rounds). The 13 CLAUDE.md edit
list is consistent with the table and does not touch the tool set, R16, R27, R28-R34, loopback, one-httpx-module, network-free
tests or the 90% gate; it names the invariants it changes (budget, TLS, plaintext). Security trade-offs (handshake failure
instead of 400, operator-owned key permissions, `.gitignore` as sole key-commit guard) are stated honestly. The change removes
code and adds no new mechanism beyond minor 4.
