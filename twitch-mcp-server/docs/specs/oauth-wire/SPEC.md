# SPEC — OAuth wire shapes match Twitch's documented responses (bug fix, Amendment 5)

**Title:** Accept Twitch's documented OAuth response shapes, and audit every OAuth wire shape the server consumes
**Status:** Approved (approved by: human, 2026-10-31). Gate confirmations: W-D2, W-D3, W-D4, W-D5 and W-D6 confirmed.
**Date:** 2026-10-31
**Amends:** [`docs/SPEC.md`](../../SPEC.md) R13 (the credential-header exception, now also `POST /oauth2/revoke`), R47
and R49 (token-response parsing, including the `scope` shape, the 400 mapping and the refresh reauth), R50 (the `login`
field read from `GET /oauth2/validate`), and R51 (the revoke request and its tolerated 400). R46, R48 (token-file format)
and R52 are unchanged. [`docs/specs/oauth-iss/SPEC.md`](../oauth-iss/SPEC.md) is not amended: its `iss` rules are untouched.
This spec adds requirements `W1`–`W12`. It does not renumber any requirement and does not rewrite R13 or R47–R51 text in
place; the pointer line in `docs/SPEC.md` identifies the superseded parts.

---

## 1. Problem / motivation

Observed live on 2026-10-31: after the `oauth-iss` fix, a real sign-in reached `POST /oauth2/token` (200) and then
failed with outcome `upstream`, before any `GET /oauth2/validate` call.

Cause: `build_token` (`src/twitch_mcp/oauth.py:249`) refuses any `scope` that is not a `str` (`oauth.py:276`). Twitch's
documented authorization-code response carries `scope` as a JSON array of strings
(<https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/>, example `"scope": ["channel:moderate", "chat:edit",
"chat:read"]`). Every fake in `tests/` returned a string `scope`, so the unit and HTTP tests passed against a shape
Twitch never sends.

The human chose (W-D1) to fix the scope shape and to audit every OAuth wire shape the server consumes against Twitch's
documentation, so that no further live mismatch remains. The audit (§4) found further mismatches of the same kind, which
this spec also fixes: the refresh response has the same array `scope`; the validate response names its login field
`login`, not `user_login`; and the revoke request omits the `client_id` that Twitch requires, so disconnect never revokes
at Twitch. Revoke also has a tolerated 400 that the current error mapping would turn into a failed disconnect, and a
refresh rejection does not clear the stored credential in production.

## 2. Goals

- Accept `scope` as a JSON array of strings on both the authorization-code exchange and the refresh response, storing
  it as today's space-joined string.
- Keep every other type of `scope` refused as `malformed`, and keep the token-file format and every stored token loadable.
- Make each OAuth wire shape the server consumes match Twitch's documentation, recorded row by row in §4.
- Make the test fakes return Twitch's documented shapes by default, so the shape bug cannot hide behind a fake again.
- Add an HTTP-level callback test whose token body uses a `scope` array, which fails against the unfixed `build_token`.

## 3. Non-goals

- Helix API response shapes. These were audited under A2-D3 (`docs/SPEC.md` Amendment 2) and are not re-audited here.
- The authorize URL, the scope set, the `state`/`iss` rules (R47, `oauth-iss`), the token-file format (R48), and the
  refresh lock (R49).
- Changing what is logged. No token, `scope`, `login` or `client_secret` value is logged or echoed (R52).
- Editing source, tests, `CLAUDE.md` or `CHANGELOG.md` in this spec phase (the implementation phase owns them).

## 4. Decisions and OAuth wire audit

### 4.1 Decisions

| Ref | Decision | Rationale and trade-off |
|---|---|---|
| **W-D1** | Fix the `scope` shape and audit all consumed OAuth shapes against Twitch's docs. Recorded from the human's decision of 2026-10-31. | The live bug was one shape; the audit closes the rest. |
| **W-D2** *(derived; confirm at gate)* | `scope` is normalised at the parsing boundary (`build_token`). A JSON array of strings is joined with single spaces into the existing `UserToken.scope: str`. A plain string is still accepted. Any other type, or an array with a non-string element, is `malformed`. The token-file format is unchanged, so existing stored tokens stay loadable. | Keeps `UserToken.scope` a string everywhere downstream (`scopes`, the R45 hint, the token file). |
| **W-D3** *(derived; confirm at gate)* | Test fakes return Twitch's documented shapes by default: `scope` as an array in **token-endpoint bodies**, `scopes` as an array and `login` (not `user_login`) in **validate bodies**, and no undocumented `user_id`/`login`/`user_login` in token bodies. The **on-disk token file keeps a string `scope`** (R48; `_from_file` requires a `str`). Explicit tests cover array scope, string scope and malformed scope types on exchange and refresh. | The previous amendment's fake carried `user_id` in the token body, which hid the validate path. |
| **W-D4** *(derived; confirm at gate)* | A `400` from `POST /oauth2/token` (exchange, refresh **and the client-credentials mint**) or from `POST /oauth2/revoke` is mapped by **status alone** to `kind=unauthenticated`. The text-keyed set `_REAUTH_ERRORS` (`oauth.py:67–69`) is removed. A `401` keeps its current mapping. A `5xx` and any other status are unchanged. The hint depends on the grant (W12). **Effect on the mint:** a `400` from `client_credentials` changes from `upstream` to `unauthenticated`. The boot already degrades on any mint error (`server.py:1522–1526` decides on `exc.retryable`), so boot behaviour is unchanged; what changes is the tool-call kind (`service.py` ~520, which treats an `unauthenticated` mint as `app_token_unavailable`) and the hint. The mint gets its own hint. This is intended. | Twitch's documented 400 bodies carry `error: "Bad Request"` and a `message` (refresh page), or `status` and `message` only (revoke page). None carries an `error` code that the set matches, so the set never fires. R44 forbids keying on message text. |
| **W-D5** *(derived; confirm at gate)* | The revoke request is sent as a form containing `client_id` and `token`, with no `Client-Id` or `Authorization` header, which is what Twitch documents. This is the exact R13 exception (§6.2). On `/oauth2/token` the header guard applies only when **neither** `client_id` nor `bearer` is passed as a keyword. On `/oauth2/revoke`, **passing any keyword** (`client_id` only, `bearer` only, or both) makes `post_form` raise `unauthenticated` before the socket, so revoke never carries `Client-Id` or `Authorization`. The revoke form must also carry a non-empty `client_id`, or the call is refused before the socket. | Today `disconnect` sends `{"token": ...}` only, and `server.py:1722` passes no `client_id`, so `_headers` refuses before the socket. Disconnect then swallows the refusal and deletes the local credential without revoking it at Twitch. This changes the test `test_only_the_token_endpoint_may_go_without_credentials` (`tests/test_twitch_client.py:316–330`), which is updated accordingly. |
| **W-D6** *(derived; confirm at gate)* | `refresh_now` (`oauth.py:641`) forces re-auth and clears the store **only** when the refresh grant returns an `unauthenticated` outcome (a `400` or `401` from the token endpoint). Transport errors, `5xx` and other `upstream` errors re-raise **without** clearing the store. Today the grant call sits outside the `try` that calls `_force_reauth` (`oauth.py:662–675`), so in production a refresh rejection never clears the store. | R49 says "a refresh failure forces re-auth and clears the store". The current code meets that only for fakes that return a status. **Known consequences to confirm at the gate:** a `401` caused by a wrong server `client_secret` (not by the user token) also clears the user's store; a malformed-request `400` on refresh also clears it. Both follow R49's literal wording and are accepted. |

### 4.2 OAuth wire audit

Sources: four Twitch pages, read on 2026-10-31: getting-tokens (authorization-code and client-credentials responses),
refresh-tokens (request and success body, plus the refresh error body), validate-tokens (200 and 401 bodies) and
revoke-tokens (request, 200, 400 and 404 bodies). Only fields and types the docs state are used. The `Code` column names
the file and line the server reads. Production `post_form` raises on every non-2xx (`twitch_client.py:979–980`), so
production error mapping is `_oauth_error` (`twitch_client.py:984`); `_token_or_raise` (`oauth.py:683`) handles status
returns, which only unit fakes produce.

| # | Response or request | Twitch documents (field: type) | Server reads | Verdict |
|---|---|---|---|---|
| A1 | Authorization-code exchange response (`POST /oauth2/token`, `grant_type=authorization_code`) | `access_token`: string; `refresh_token`: string; `expires_in`: number; `token_type`: `"bearer"`; `scope`: **array of strings** | `build_token` (`oauth.py:249–295`) | **MISMATCH** `scope` (`oauth.py:276` rejects the array). Others MATCH. `token_type` is case-insensitive (`oauth.py:274`). |
| A2 | Exchange response: identity fields | Not in the documented body. Identity comes from validate. | `oauth.py:280–281` reads undocumented `user_id`/`user_login` if present; `exchange` falls back to `_resolve_user` (`oauth.py:635–637`) | MATCH as a fallback. The undocumented fields are tolerated, not relied on (W-D3). |
| A3 | Refresh request (`POST /oauth2/token`, `grant_type=refresh_token`) | Form: `client_id`, `client_secret` (required unless public), `grant_type`, `refresh_token` | `refresh_now` form (`oauth.py:662–669`) | MATCH |
| A4 | Refresh response | Documented example: `access_token`, `expires_in`, `refresh_token`, `scope` **array of strings**, `token_type`. `refresh_token` may change. The getting-tokens page's own example shows a string, so both shapes are accepted (W2). | `build_token` with `fallback` (`oauth.py:267–269`) | **MISMATCH** `scope` (same as A1). Omitted `refresh_token` or `scope` keeps the fallback, which is tolerated. |
| A5 | Client-credentials mint request | Form: `client_id`, `client_secret`, `grant_type=client_credentials` | `server.py:1438–1443` | MATCH |
| A6 | Client-credentials response | `access_token`: string; `expires_in`: number; `token_type`: string. No `scope`. | `_bearer_from_grant` (`server.py:1464–1475`) reads `access_token` and `expires_in` | MATCH. `token_type` and `scope` are not needed. |
| A7 | Validate response, 200 (`GET /oauth2/validate`) | `client_id`: string; `scopes`: **array of strings**; `expires_in`: integer; `login`: string, **null for app tokens**; `user_id`: string, **null for app tokens** | `validate_token` (`twitch_client.py:1016`): `scopes` list (`:1045`), `user_id` null-safe (`:1051`), `expires_in` (`:1052`), `user_login=_field(body, "user_login")` (`:1059`). `_resolve_user` reads `user_login` (`oauth.py:733–740`). | `scopes`, `user_id` and `expires_in`: MATCH. **`login`: MISMATCH.** The server reads `user_login`, which Twitch never sends, so the login is always empty (W5). |
| A8 | Validate response, 401 (`GET /oauth2/validate`) | `status`: 401; `message`: string | `validate_token` maps `status == 401` by status alone (`twitch_client.py:1034–1035`) | MATCH |
| A9 | Revoke request (`POST /oauth2/revoke`) | Form: `client_id` (required), `token` (required). No header is documented. | `disconnect` sends `{"token": ...}` (`oauth.py:604`). `post_form` builds `_headers` (`twitch_client.py:409–425`, `:927`). `server.py:1722` passes no `client_id`. | **MISMATCH.** `client_id` is missing from the form, and the headers are refused before the socket. Twitch never sees the revoke (W6). |
| A10 | Revoke response, 200 | Success: HTTP 200 with no body | `post_form` returns `(status, None)` on 2xx (`twitch_client.py:966–975`) | MATCH |
| A11 | Revoke response, 400 | `status`: 400; `message`: `"Invalid token"` (a dead token). No `error` field. | `post_form` raises `_oauth_error` on non-2xx (`twitch_client.py:979–980`). `_oauth_error` (`:984`) reads `error` only, so 400 becomes `upstream`. `disconnect` re-raises non-`unauthenticated` (`oauth.py:606–608`), so the local credential is kept. | **MISMATCH.** A dead token would stop disconnect before the local credential is deleted, breaking R51. Fixed by W4/W7. |
| A12 | Revoke response, 404 | `status`: 404; `message`: `"client does not exist"` | `_oauth_error` → `upstream` | MATCH (a 404 is a configuration fault, not a dead token) |
| A13 | Token-endpoint 400, refresh (`error`, `status`, `message`) | Example: `error: "Bad Request"`, `status: 400`, `message: "Invalid refresh token"`. The text also says a dead refresh token may return 401. | Production: `post_form` raises `_oauth_error` (`twitch_client.py:979–980`, `:984`), which keys 400 on `error` in a fixed set, so `"Bad Request"` never matches and the refusal becomes `upstream`. `refresh_now` clears the store only for `unauthenticated` and only for status-returning fakes (`oauth.py:662–675`, W-D6). | **MISMATCH.** The reauth path never fires for a dead refresh token in production (W4, W11). **Known wording gap, out of scope:** the refresh page's 401 for a dead refresh token gets `_oauth_error`'s 401 text, "Twitch rejected this server's credentials", with an env-check hint. Behaviour is kept by W4; the wording is not changed here. |
| A14 | Token-type check | `"bearer"` lowercase (both examples) | `token_type.lower() != "bearer"` (`oauth.py:274`) | MATCH |

Summary: **MISMATCH** on A1, A4 (`scope` array), A7 (`login`), A9 (revoke request), A11 and A13 (400 mapping and reauth).
Every other row is a verified MATCH. Each MISMATCH is a requirement in §5.

## 5. Requirements

- **W1** — `build_token` accepts `scope` as a JSON array in which every element is a `str`. It joins the elements with
  single spaces into `UserToken.scope`. An empty array gives `""`. This applies to the authorization-code exchange and
  to the refresh response (A1, A4).
- **W2** — A plain `str` `scope` is still accepted unchanged on both grants (A1, A4). On a refresh, a `scope` that is
  absent, `null`, `""` or `[]` keeps the previous `fallback.scope`, as today (`oauth.py:267–269`). On an exchange,
  an absent or `null` `scope` is `malformed`.
- **W3** — Any other `scope` type is refused with `kind=upstream` and the existing malformed message: a number, boolean,
  object, a non-array, or an array with any non-`str` element. This holds on the exchange and on the refresh. The
  refusal names no value (R52).
- **W4** — A `400` from `POST /oauth2/token` (exchange, refresh and client-credentials mint) and from `POST /oauth2/revoke`
  maps to `kind=unauthenticated` by status alone (W-D4). `_REAUTH_ERRORS` (`oauth.py:67–69`) is deleted. In production
  the decision is made in `_oauth_error` (`twitch_client.py:984`), which keys first on the path and then on the form's `grant_type`, and no longer reads `error`. `_token_or_raise` (`oauth.py:683`) applies the same rule to
  status returns. A `401` keeps its existing mapping, and a `5xx` stays `upstream` with `retryable=true`.
  Removing `_REAUTH_ERRORS` is safe: it is referenced only at `oauth.py:67` and `oauth.py:690`, and no test imports it.
  The existing tests that return `(400, {"error": "invalid_grant"})` (`tests/test_oauth_units.py:210` and `:363`) keep
  passing, because a status-only 400 is still `unauthenticated`.
- **W5** — The validate login is read from the documented field `login`. `validate_token` sets `BootProbe.user_login`
  from `login` (`twitch_client.py:1059`), and `_resolve_user` reads `login` (`oauth.py:733–740`). `user_login` is no
  longer read from a validate body. A null `login` (app token) gives `""` / `None`, as today.
- **W6** — **The R13 exception, exactly.** `post_form` (`twitch_client.py:927`) sends no `Client-Id` and no `Authorization`
  header on `OAUTH_TOKEN_PATH` **only when the caller passes neither `client_id` nor `bearer` as a
  keyword** (the both-`None` guard, kept as it is for the token path). On the revoke path, **passing any keyword** (`client_id` only, `bearer` only, or both) makes `post_form` raise `unauthenticated` before the socket; revoke never carries `Client-Id` or `Authorization` (R2-1). On the revoke path the `form` must carry a non-empty `client_id`, or the call is
  refused before the socket with `kind=unauthenticated`. Any other path keeps `_headers` and its existing refusal (R13).
  `disconnect` (`oauth.py:604`) sends `{"client_id": self._client_id, "token": <access token>}`. The `client_secret` is
  not sent on revoke, as Twitch does not document it.
- **W7** — A dead token on revoke (a `400`, mapped to `unauthenticated` by W4) is handled by `disconnect`
  (`oauth.py:606–608`): it logs `revoke_refused` and still deletes the local credential (R51). A `404` or `5xx` on revoke
  raises `upstream`, and `disconnect` keeps the local credential, as today. If `self._client_id` is empty, `disconnect` does not call revoke at all: it logs `revoke_skipped`, still deletes the local credential, and returns the user id (R51).
- **W8** — Test fakes mirror Twitch's documented shapes by default (W-D3). Specifically:
  - **Token-endpoint bodies** (the grant fakes): `tests/test_oauth_units.py:350–354` (`_GRANT_OK`, also used by
    `:443`), `tests/test_oauth_units.py:539–547` (drop the `user_id` and `login` keys), and `tests/test_server.py:671–679`
    (`_register_token_exchange`, drop `user_id` and make `scope` an array): `scope` is an array, with no undocumented keys.
  - **Validate bodies:** `tests/server_fakes.py:65–75` (`VALIDATE_USER`: drop `user_login` and `user_type`; keep `login`
    and `scopes` as an array) and `tests/twitch_fakes.py:291–310` (`set_validate`: drop `"user_login"` and
    `"user_type"`; keep `"login"`). `tests/test_oauth_units.py:550` already uses `login`.
  - **Not changed:** `tests/server_fakes.py:350–364` writes the **on-disk token file**, which keeps a string `scope`
    (R48, `_from_file` at `oauth.py:302`). `tests/test_oauth_units.py:41–47` (`_token()`) builds a stored `UserToken`, so it
    keeps a string `scope` too.
  - **Explicit unit tests** (`tests/test_oauth_units.py`), on exchange and on refresh: array scope → space-joined string;
    string scope → unchanged; absent refresh scope → fallback; number, object and array-with-int scope → `malformed`.
  - **Explicit client tests** (`tests/test_twitch_client.py`) replace `test_only_the_token_endpoint_may_go_without_credentials`
    (`:316–330`): a revoke with `client_id` in the form succeeds with no `Client-Id` or `Authorization` header; a revoke with
    no `client_id` in the form, is refused before the socket; a revoke passed `client_id` only, `bearer` only, or both is refused before the socket, with zero requests recorded.
- **W9** — HTTP callback test. `test_oauth_callback_completes_without_iss` in `tests/test_server.py` (currently at `:694`,
  using `_register_token_exchange` at `:660–690`) is changed to Twitch's documented shapes: the authorization-code body
  carries `scope` as an **array**, and it carries no `user_id` or `login`. The test registers its `GET /oauth2/validate`
  handler **after boot**, keyed on the `Authorization` header value: the user access token returned by the callback's
  body answers with `user_id`, `login` and `scopes` (array); any other bearer keeps today's app answer. The `running_app()`
  boot (`connected=False`) already issues one validate request, which the default fake answers with a 404 (degraded).
  The test therefore counts **new** requests: it records `len(fake.requests_for("GET", "/oauth2/validate"))` after the
  handler is registered and before the callback, and asserts that the count rises by exactly one during the callback. The
  `oauth-iss` assertions (200, one `authorization_code` POST, the 0600 token file, no `iss` in the form, no `code` or
  `state` in the body) are unchanged. **This test must fail against the unfixed `build_token`** (the array scope raises
  `malformed`, so the callback returns 400). The unit-level expectation is the same: a unit test with an array `scope`
  fails with `upstream` on the unfixed code. The implementation report records both failing runs before the fix. The
  wrong-`iss` HTTP test (`:719`) gets the same token-body shape and must still pass.
- **W10** — Security and logging. The `scope` value, the `login` value, the `client_id` in the revoke form and every
  token stay out of logs and responses. The refusal messages name the field, never its value. R52 log lines are
  unchanged. The existing logging tests in `tests/test_logging.py` pass unchanged.
- **W11** — **Refresh reauth (W-D6).** `refresh_now` (`oauth.py:641`) wraps the **grant call and** the `_token_or_raise`
  decision in one `try`. An `unauthenticated` `TwitchMcpError` from either calls `self._force_reauth("refresh_rejected")`
  (store cleared, `last_error` set) and is re-raised. Any other error (transport, `5xx`, other `upstream`) is re-raised
  without clearing the store. Tests use a grant fake that **raises** (not only one that returns a status): an
  `unauthenticated` raise clears the store; a `TwitchMcpError("upstream", retryable=True)` raise keeps it; a `500`
  status return keeps it.
- **W12** — **Mint and revoke hints (W-D4).** `_oauth_error` (`twitch_client.py:984`) returns a grant-specific hint for a
  `400`: for `client_credentials`, "Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env and restart." (not the
  sign-in hint); for `authorization_code` and `refresh_token`, the existing sign-in hint (`connect_twitch_account`); for
  revoke, no sign-in hint. A test covers the mint `400` at `post_form` level: it is `unauthenticated` with the env hint,
  and the boot is unchanged (it already degrades on any mint error, `server.py:1522–1526`). The implementer confirms in the report both the boot path (`server.py:1522–1526`) and the tool-call path (`service.py` ~520, `app_token_unavailable`).

## 6. Design

### 6.1 Source (`src/twitch_mcp/oauth.py`)

- `build_token(body, *, now, fallback=None)` (line 249): before the `isinstance(scope, str)` check (line 276),
  normalise `scope`: a `str` stays; a `list` whose elements are all `str` becomes `" ".join(scope)`; `None` stays
  `None`; anything else raises the existing `malformed`. Then, when `fallback` is given and `scope` is falsy
  (`None`, `""`), use `fallback.scope` (the existing rule at `:268`, now after normalisation). The final
  `isinstance(scope, str)` check (`:276`) becomes `if scope is None: raise malformed`. The docstring (`:250–254`) states the
  array shape.
- `_REAUTH_ERRORS` (lines 67–69): delete the constant (W4).
- `_token_or_raise(self, status, body, hint, *, fallback=None)` (line 683): the 400 branch (line 690) raises
  `unauthenticated` for any `400`, whatever the body. The `error` read (line 688) is removed.
- `refresh_now` (lines 641–675): the `self._grant(...)` call (`:662–669`) and `_token_or_raise` (`:671`) move into one
  `try`; the `except` (`:672–675`) calls `_force_reauth` only for `unauthenticated` (W11, W-D6).
- `UserAuth.disconnect` (lines 593–612): line 604 becomes
  `await self._revoke({"client_id": self._client_id, "token": token.access_token.get_secret_value()})`, guarded by
  `if self._client_id` so an empty id skips the call (W7). `self._client_id` is the one `set_credentials` stores
  (lines 753–755).
- `_resolve_user` (lines 716–740): the login is read from `body.get("login")` (line 737) instead of `user_login`.
- `_from_file`, `_FILE_STRINGS`, `_FILE_NUMBERS` (lines 298–331): unchanged (W-D2, R48).
- `scope` in `UserToken` stays `str`; `scopes` (`oauth.py:236–238`) is unchanged.

### 6.2 Source (`src/twitch_mcp/twitch_client.py`)

- `post_form` (line 927): the header branch (`:944–952`) becomes: on `OAUTH_TOKEN_PATH`, no headers and no `_headers` call when neither keyword is passed (today's rule, `:950–952`); on `OAUTH_REVOKE_PATH`, any keyword raises `unauthenticated` before the socket (R2-1), otherwise no headers and no `_headers` call. For the revoke path, a `form` without a non-empty `client_id` raises
  `TwitchMcpError("unauthenticated", ...)` before the socket (W6). The allowed-path check (`:935–936`) is unchanged.
- **Docstring and comment (`twitch_client.py:927–951`), edits required:** the docstring line "``/oauth2/token`` legitimately
  has no credential yet, so it is the only endpoint that may go without ``Client-Id``/``Authorization``" becomes "``/oauth2/token``
  and ``/oauth2/revoke`` carry identifiers in the form body (token: client id and secret; revoke: client id and the token) and may go without ``Client-Id``/``Authorization``, with revoke refused if any keyword is passed (R2-1); anything else still needs both or is refused (R13)". The comment block at
  `:944–951` is rewritten to match, and names the revoke `client_id` requirement.
- `_oauth_error(status, body)` (line 984) becomes `_oauth_error(status, body, path, grant)` (W4, W12): the 400 branch uses
  status alone and selects the hint by `grant`; the `error` read (`:985`) is removed. `post_form` passes `path` and `form.get("grant_type")` (no default). The label is keyed first on `path` (`OAUTH_REVOKE_PATH` → revoke; otherwise the token path), and only then on `grant`: a token-path form without `grant_type` gets the generic 400 hint.
- `validate_token` (line 1016): line 1059 becomes `user_login=_field(body, "login")`.
- `_parse_json` (line 269) and `_field` (line 276) are unchanged.

### 6.3 Source (`src/twitch_mcp/server.py`)

- `_bearer_from_grant` (line 1464) and the client-credentials mint (lines 1438–1443) are unchanged. The mint's `400` now
  arrives as `unauthenticated` with the env hint (W4, W12).
- `revoke=lambda form: client.post_form(REVOKE_FORM_PATH, form)` (line 1722) is unchanged. The `client_id` now arrives in
  the form, so no `server.py` change is needed.

### 6.4 Tests

- `tests/test_oauth_units.py`: fakes per W8; new unit tests for W1–W3, W5 and W11 (raising fakes). The existing
  `test_disconnect_revokes_and_touches_nothing_else` (`:277–297`) calls `auth.set_credentials(CLIENT_ID, CLIENT_SECRET)`
  first, because `_client_id` is `""` in that test today. It keeps its single-revoke assertion and gains an assertion that
  the form carries that `client_id`. A second case with no credentials set asserts that no revoke is sent (W7) and the
  local credential is still deleted.
- `tests/test_twitch_client.py`: `test_only_the_token_endpoint_may_go_without_credentials` (`:316–330`) is replaced per W8.
  New client tests cover W4 and W12 at `post_form` level, with status fakes that return the body: revoke `400`
  (`{"status":400,"message":"Invalid token"}`) → `unauthenticated`; token-endpoint `400` with `error: "Bad Request"` →
  `unauthenticated` with the sign-in hint; mint `400` (`client_credentials`) → `unauthenticated` with the env hint; `404`
  revoke → `upstream`; `401` unchanged; `503` → `upstream` with `retryable=true`. Keyword cases (R2-1): revoke with `client_id` only, `bearer` only, or both each raises `unauthenticated` before the socket (zero requests recorded). A W7 unit test uses a revoke fake that
  **raises** `TwitchMcpError("unauthenticated", http_status=400)`.
- `tests/server_fakes.py`, `tests/twitch_fakes.py`, `tests/test_server.py`: default fakes per W8; W9 test changed; the
  wrong-`iss` HTTP test keeps its assertions.
- `tests/test_identity.py`, `tests/test_models.py`, `tests/service_fakes.py`: no change expected. The implementer confirms
  this in the report. `test_service.py:1067` (`disconnect`) is unaffected: `make_connected_flow` attaches no revoke.

### 6.5 Docs

- **`CLAUDE.md` Architecture bullet for `twitch_client.py`:** the phrase "`Client-Id` + `Bearer` asserted before the socket
  (R13)" becomes "`Client-Id` + `Bearer` asserted before the socket (R13), except `POST /oauth2/token` and
  `POST /oauth2/revoke`, which carry identifiers in the form body (W6): the token request carries `client_id` and `client_secret`; revoke carries `client_id` and the token being revoked, nothing else". The "One httpx module" invariant already
  names `/oauth2/revoke` and needs no change.
- **`CLAUDE.md` OAuth (R46–R51) invariants bullet:** add one clause, "token `scope` is accepted as a JSON array or a string,
  a token-endpoint or revoke 400 is `unauthenticated` by status, and a refresh rejection clears the store." Cited by bullet
  name, not line number. Applied with the code.
- **`docs/SPEC.md` R13 (line ~157):** add the marker "Superseded in part by Amendment 5 (W6): the exception also covers
  `POST /oauth2/revoke`, and only when neither header keyword is passed." Text is not rewritten.
- **`CHANGELOG.md` `[Unreleased]` → `Fixed`** (the subsection exists, line ~116): "OAuth sign-in accepts Twitch's documented
  `scope` array; the validate login is read from `login`; disconnect revokes the token at Twitch (client id now sent) and
  deletes the local credential even for a dead token; a refresh rejection clears the stored credential; an app-token mint rejection now points at `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET`."
- `docs/SPEC.md`: pointer line only (§12). R47, R49, R50 and R51 text is not rewritten.
- `README.md`: no change expected. The implementer confirms in the report.
- `docs/specs/oauth-wire/tasks.md`: a checklist, not user docs.

## 7. Security and invariants

- **Secrets and values stay out of logs and responses (R52).** `scope`, `login`, the revoke `client_id` and every token
  are never logged or echoed. A refusal names the field, never its value. The revoke `client_id` is a public identifier,
  not a secret, and it travels in the form body as the token request already does.
- **`client_secret` is not sent on revoke.** Twitch documents only `client_id` and `token` for revoke, so the secret does
  not leave the server on this path.
- **Malformed is still refused.** Any non-string `scope` element, non-array non-string `scope`, or missing `access_token`
  or `refresh_token` remains `kind=upstream` (W3). No partial token is installed.
- **The R13 exception is narrowed to two exact paths and to the no-keyword case.** Only `/oauth2/token` and
  `/oauth2/revoke` may go without `Client-Id`/`Authorization`, `/oauth2/token` goes without them only when the caller passes neither keyword; `/oauth2/revoke` never carries them, and passing any keyword is refused (R2-1). The revoke
  path additionally requires `client_id` in its form (W6). No other endpoint can borrow the exception.
- **400 mapping is status-based (W4).** It cannot be defeated by an unexpected body, and it does not key on message text (R44).
- **Refresh clears the store on `unauthenticated` only (W11).** Accepted consequences, to confirm at the gate (W-D6): a
  `401` from a wrong server `client_secret` and a malformed-request `400` on refresh both clear the stored user credential,
  as R49's wording requires.
- **Revoke changes what gets deleted, not what gets logged.** The local credential is deleted on a dead-token 400 (W7),
  which is R51's intent. A 404 or 5xx keeps the local credential, as today.
- **Token-file format and loadability (R48) are unchanged.** `scope` stays a string on disk, so existing files load.
- Loopback bind, bearer exemption, tool set, and the R47 `state` and `iss` rules are unchanged.

## 8. Test plan and acceptance criteria

| Req | Verification |
|---|---|
| W1 | Unit: exchange and refresh with `scope` array → space-joined string; `[]` → `""`. HTTP: W9 test. |
| W2 | Unit: string `scope` unchanged; refresh with absent, `null`, `""` or `[]` scope keeps `fallback.scope`; exchange with absent `scope` → `malformed`. |
| W3 | Unit: `scope` of `5`, `{}`, `true`, `["a", 1]` on exchange and refresh → `kind=upstream`, no token installed. |
| W4 | Client test (§6.4): revoke `400` (`message` only), token `400` (`error: "Bad Request"`) and mint `400` → `unauthenticated`; `404` → `upstream`; `401` unchanged; `503` → `upstream`, `retryable=true`. |
| W5 | Unit: validate body with `login` → `user_login` set; with `user_login` only → empty login. `_resolve_user` takes `login`. |
| W6 | Client test: revoke form has `client_id` and `token`, no `Client-Id`/`Authorization` header, one POST; revoke without `client_id` → refused, no request recorded; revoke with `client_id` only, `bearer` only, or both → each refused before the socket, zero requests recorded. |
| W7 | Unit: revoke fake **raises** `unauthenticated` (400) → local credential deleted, `revoke_refused` logged; `404` revoke → `upstream`, file kept; empty `_client_id` → no revoke sent, `revoke_skipped` logged, credential deleted. |
| W8 | The fakes in §6.4 use the documented shapes; the tests listed in W8 pass; the on-disk token-file fake is unchanged. |
| W9 | HTTP: `test_oauth_callback_completes_without_iss` with array `scope`, no `user_id`/`login`, validate handler registered after boot: 200, one `authorization_code` POST, exactly one new validate request, token file 0600, no `iss`/`code`/`state` leak. **Fails against the unfixed `build_token`** (recorded in the report). |
| W10 | Existing logging tests pass unchanged; the W9 success body contains neither the `scope` names nor `login`. |
| W11 | Unit with raising grant fakes: `unauthenticated` raise clears the store and sets `last_error`; `upstream` `retryable` raise keeps the store; `500` status return keeps it. |
| W12 | Client test: mint `400` → `unauthenticated` with the env hint; boot unchanged; tool-call path reports the env hint (`server.py:1522–1526`, `service.py` ~520). |

Acceptance also requires that the W9 HTTP test and the W9 unit test with an array `scope` fail against the unfixed
`build_token` (the implementation report records one run of each, or the cited line, before the fix).

Gate: `make check` (ruff, `mypy --strict`, pytest) passes. Coverage is at least 90% branch, and the floor is not lowered.
Every test keeps the 30-second per-test timeout (`pytest-timeout`, S11–S12); no test raises it. Iterate on one file with
`--no-cov`. The implementation report lists the commands and results.

## 9. Out of scope

- Helix response shapes (audited under A2-D3).
- The authorize URL, the scope set, the `state`/`iss` rules, the token-file format, the refresh lock beyond W11, and the R45
  hint text.
- The refresh-401 wording gap in A13 (the `401` message text and hint are kept as they are; the change is recorded here so
  a reviewer does not re-raise it).
- Live Twitch sign-in or revoke as an automated check. The human verifies these by hand.
- Adding a `client_secret` to revoke, or any new Twitch endpoint.

## 10. Process

Per AGENTS.md §2–§8: the human approves this spec (Phase 3) before any code. Then an implementer subagent applies §6, a
separate reviewer subagent runs the review loop (max 5 rounds), and a summary report is written under
`docs/reports/oauth-wire/`. Nothing is committed unless the human asks.

## 11. Open questions

None. W-D2 to W-D6 are derived decisions; the human confirms them at the approval gate. The W-D4 mint effect and the W-D6
consequences in §4.1 are the points to look at first.

## 12. Header pointers to add on approval

- `docs/SPEC.md`, under the Amendment 4 line: "**Amendment 5:** pending — see docs/specs/oauth-wire/SPEC.md (Draft, not
  yet approved)." On approval it becomes "approved by: human". R13 (the header exception, W6), R47 and R49 (the `scope`
  parsing, the 400 mapping and the refresh reauth, W1–W4 and W11), R50 (the validate `login`, W5) and R51 (the revoke
  request and tolerated 400, W6–W7) are marked superseded in place. Their text is not rewritten.
- `CLAUDE.md` wording changes per §6.5 (the `twitch_client.py` Architecture bullet for R13, and the OAuth invariants
  bullet), applied with the code.

---

**Amendment W-A1 (2026-10-31, approved by: human):** two SPEC-GAPs raised by the implementer.
- **G1:** `docs/reports/initial-build/implementation-report.md` row R13 cited
  `tests/test_twitch_client.py::test_only_the_token_endpoint_may_go_without_credentials`, which this spec replaces. That row
  now cites the two replacement revoke tests instead (same treatment as simplification amendment S-A1).
- **G2:** `docs/SPEC.md` carries in-place superseded-in-part markers for all five amended requirements — R13, R47, R49, R50
  and R51 — pointing to Amendment 5 (§12 governs over the shorter §6.5/tasks list).
