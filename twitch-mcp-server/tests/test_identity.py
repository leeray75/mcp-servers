"""R29–R30: identity comes from the token, roles from Twitch's own rosters, with TTL caching.

Three things are pinned here.

**R29 — own channel.** The operator's own ``broadcaster_id`` is the ``user_id`` of the token as
``GET /oauth2/validate`` reported it. There is no path from a tool argument to that value, so an
argument that disagrees with the token is only ever *compared* to it and can never be adopted.

**R29 — verified role.** Any other allowlisted channel needs proof from Twitch itself: the operator
in ``GET /channels/editors`` for the ``channel`` group, in ``GET /moderation/moderators`` for
moderation, chat, roles, polls and predictions. A target that is neither returns
``kind=not_authorized`` and provably zero requests.

**R30 — cache.** Verdicts live for ``TWITCH_IDENTITY_TTL_SECONDS``, die on token refresh, on
disconnect and on the first ``401``, and a roster that cannot be read is "not verified" — never
"verified".
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import SecretStr

from tests.core_fakes import make_identity
from tests.identity_fakes import (
    EDITORS_PATH,
    MODERATION_GROUPS,
    MODERATORS_PATH,
    IdentityWiring,
    clear_identity_cache,
    make_wiring,
    roster_body,
    serve_roster,
)
from tests.twitch_fakes import (
    BROADCASTER,
    MODERATOR_ID,
    OPERATOR,
    OTHER_BROADCASTER,
    USER_ID,
    FakeClock,
    FakeTwitch,
)
from twitch_mcp.cache import TTLCache
from twitch_mcp.confirmation import ConfirmationStore
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.guard import Guard
from twitch_mcp.identity import (
    IDENTITY_CACHE_PREFIX,
    NOT_AUTHORIZED_MESSAGE,
    VERDICT_EDITOR,
    VERDICT_MODERATOR,
    VERDICT_NONE,
    VERDICT_OWN,
    WRITE_GROUPS,
    IdentityVerifier,
    OperatorIdentity,
    Verification,
    members_of,
)
from twitch_mcp.logging_setup import register_secret, unregister_secret
from twitch_mcp.oauth import TokenStore, UserAuth, UserToken
from twitch_mcp.service import TwitchService

#: The operator is in this module's rosters; this id is in none of them.
NOT_A_MODERATOR = "777777777"
#: A channel that is neither the operator's own nor present in any roster.
STRANGER_CHANNEL = "900000001"
#: The two bearers the wired service is handed. Test values, registered for redaction.
APP_BEARER = "identity-test-app-bearer"
USER_BEARER = "identity-test-user-bearer"


def _always(members: Sequence[str]) -> Any:
    """A roster fetcher that always answers with the same members — no I/O, no channel sensitivity."""

    async def fetch(_roster: str, _broadcaster_id: str) -> Sequence[str]:
        return list(members)

    return fetch


def _raising(failure: BaseException) -> Any:
    """A roster fetcher that always fails — the fail-closed probe of R30."""

    async def fetch(_roster: str, _broadcaster_id: str) -> Sequence[str]:
        raise failure

    return fetch


def _never(_roster: str, _broadcaster_id: str) -> Any:
    async def fetch(_roster: str, _broadcaster_id: str) -> Sequence[str]:
        raise AssertionError("the own-channel path must not consult a roster (R29)")

    return fetch


def _cache(fclock: FakeClock | None = None) -> TTLCache:
    clock = fclock or FakeClock()
    return TTLCache(max_entries=64, max_bytes=1_000_000, clock=clock.clock, wall_clock=clock.wall)


def _verifier(
    operator: OperatorIdentity,
    members: Sequence[str] = (OPERATOR,),
    *,
    fetcher: Any = None,
    cache: TTLCache | None = None,
) -> IdentityVerifier:
    return IdentityVerifier(
        operator, cache or _cache(), fetcher or _always(members), ttl_seconds=300.0
    )


def _identity_count(cache: TTLCache) -> int:
    """How many identity entries the cache holds — measured through its public surface (R30).

    The cache exposes no key listing, so the count comes from ``invalidate(prefix)``, which returns
    exactly the number of keys that carried the identity prefix. Identity is the only prefix any
    test in this module stores, so this is the number of cached verdicts.
    """
    return cache.invalidate(IDENTITY_CACHE_PREFIX)


def _user_token(access: str, refresh: str) -> UserToken:
    now = time.time()
    return UserToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        token_type="Bearer",
        scope="user:read:email",
        expires_at=now + 3600.0,
        user_id=OPERATOR,
        user_login="operator",
        connected_at=now,
    )


# ---------------------------------------------------------------- R29: whose channel is "own"?


def test_own_broadcaster_id_is_the_token_user_id_never_an_argument() -> None:
    """R29: the operator id is the validate ``user_id``; an argument can never overwrite it."""
    operator = OperatorIdentity()
    assert operator.broadcaster_id is None, "not signed in yet, so there is no own channel"
    operator.broadcaster_id = OPERATOR  # what boot/connect/refresh assign from /oauth2/validate
    assert operator.broadcaster_id == OPERATOR
    # A disagreeing argument is only ever compared — never adopted.
    assert operator.is_own_channel(OTHER_BROADCASTER) is False
    assert operator.broadcaster_id == OPERATOR, "a foreign argument must not become the operator"
    assert operator.is_own_channel(OPERATOR) is True
    operator.broadcaster_id = ""  # signing out (server.py does this on a failed callback)
    assert operator.broadcaster_id is None


def make_operator_from_validate_probe(user_id: str | None) -> OperatorIdentity:
    """Mirror the only producer of the operator id: boot's ``/oauth2/validate`` user_id."""
    operator = OperatorIdentity()
    operator.broadcaster_id = user_id
    return operator


def test_operator_id_survives_a_disagreeing_argument_repeatedly() -> None:
    """R29: no number of foreign comparisons moves the token value (the server.py boot shape)."""
    operator = make_operator_from_validate_probe(OPERATOR)
    for claim in (OTHER_BROADCASTER, BROADCASTER, USER_ID, NOT_A_MODERATOR):
        assert operator.is_own_channel(claim) is False
        assert operator.broadcaster_id == OPERATOR
    assert operator.is_own_channel(OPERATOR) is True


async def test_unsigned_in_operator_refuses_before_any_roster_lookup(fake: FakeTwitch) -> None:
    """R29/R49: with no token there is no own channel, and no roster is consulted either."""
    verifier = _verifier(OperatorIdentity(), fetcher=_never("", ""))
    with pytest.raises(TwitchMcpError) as exc:
        await verifier.require_writable(BROADCASTER, "channel")
    assert exc.value.kind == "unauthenticated"
    assert fake.request_count == 0, "an unauthenticated refusal makes zero requests"


async def test_own_channel_authorizes_with_zero_requests(fake: FakeTwitch) -> None:
    """R29: the own-channel verdict is a token comparison — it costs no roster lookup."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    verdict = await wiring.verifier.require_writable(OPERATOR, "channel")
    assert isinstance(verdict, Verification)
    assert verdict.verdict == VERDICT_OWN
    assert verdict.cached is False, "there was nothing to cache: no lookup happened"
    assert verdict.broadcaster_id == OPERATOR
    assert fake.request_count == 0
    assert wiring.fetch_calls == []


async def test_argument_cannot_make_a_foreign_channel_the_operators(fake: FakeTwitch) -> None:
    """R29: the target argument is judged by the token, so it must earn its way in via the roster.

    Had the argument been honored as "own", there would have been no roster request at all.
    """
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    verdict = await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert verdict.verdict == VERDICT_MODERATOR
    assert wiring.verifier.is_own_channel(OTHER_BROADCASTER) is False
    assert fake.request_count == 1, "the target was proven by Twitch, not by the argument"
    assert wiring.roster_requests[0].url.path == f"/helix{MODERATORS_PATH}"


async def test_own_channel_ignores_the_editor_roster_that_would_deny_it(fake: FakeTwitch) -> None:
    """R29: own channel is the token comparison — an empty editor list cannot override it."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    serve_roster(fake, EDITORS_PATH, OPERATOR, [])
    verdict = await wiring.verifier.require_writable(OPERATOR, "channel")
    assert verdict.verdict == VERDICT_OWN
    assert fake.request_count == 0, "no roster may be consulted for the operator's own channel"


# ------------------------------------------------------- R29: which roster proves which group?


@pytest.mark.parametrize("group", sorted(WRITE_GROUPS))
async def test_channel_group_authorizes_through_the_editors_roster(
    group: str, fake: FakeTwitch
) -> None:
    """R29: every group hits exactly the Twitch endpoint that proves it — and only that one."""
    expected_path = EDITORS_PATH if group == "channel" else MODERATORS_PATH
    expected_roster = "editors" if group == "channel" else "moderators"
    wiring = make_wiring(fake, FakeClock(), operator=MODERATOR_ID)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [MODERATOR_ID, USER_ID])
    serve_roster(fake, EDITORS_PATH, OTHER_BROADCASTER, [MODERATOR_ID])
    verdict = await wiring.verifier.require_writable(OTHER_BROADCASTER, group)
    assert verdict.verdict == (VERDICT_EDITOR if group == "channel" else VERDICT_MODERATOR)
    assert [request.url.path for request in wiring.roster_requests] == [f"/helix{expected_path}"]
    assert wiring.fetch_calls == [(expected_roster, OTHER_BROADCASTER)]
    asked = wiring.roster_requests[0].url.params
    assert asked.get("broadcaster_id") == OTHER_BROADCASTER
    assert asked.get("first") == "100"
    assert fake.request_count == 1, "one channel costs exactly one roster lookup"


@pytest.mark.parametrize("group", sorted(WRITE_GROUPS))
async def test_the_wrong_roster_never_authorizes_a_group(group: str, fake: FakeTwitch) -> None:
    """R29: being in the *other* roster proves nothing about this group."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    wrong = MODERATORS_PATH if group == "channel" else EDITORS_PATH
    right = EDITORS_PATH if group == "channel" else MODERATORS_PATH
    # The operator appears ONLY in the roster this group must not use.
    serve_roster(fake, wrong, OTHER_BROADCASTER, [OPERATOR], roster=wiring.roster)
    with pytest.raises(TwitchMcpError) as exc:
        await wiring.verifier.require_writable(OTHER_BROADCASTER, group)
    assert exc.value.kind == "not_authorized", "membership in the wrong roster is not authority"
    assert [request.url.path for request in wiring.roster_requests] == [f"/helix{right}"]
    assert wiring.fetch_calls == [
        ("editors" if group == "channel" else "moderators", OTHER_BROADCASTER)
    ]
    assert fake.request_count == 1, "the wrong roster is never even asked"


async def test_moderation_groups_all_share_the_moderators_roster(fake: FakeTwitch) -> None:
    """R29: moderation/chat/roles/polls/predictions are all proven by GET /moderation/moderators."""
    assert set(MODERATION_GROUPS) == set(WRITE_GROUPS) - {"channel"}
    wiring = make_wiring(
        fake, FakeClock(), operator=OPERATOR, moderators={OTHER_BROADCASTER: [OPERATOR]}
    )
    for group in MODERATION_GROUPS:
        verdict = await wiring.verifier.require_writable(OTHER_BROADCASTER, group)
        assert verdict.verdict == VERDICT_MODERATOR
    assert [roster for roster, _ in wiring.fetch_calls] == ["moderators"]
    # One channel, five groups: the roster was fetched once and the other four verdicts were cached.
    assert fake.request_count == 1, "five groups on one channel cost one proof (R30)"


@pytest.mark.parametrize("group", sorted(WRITE_GROUPS))
async def test_group_selects_the_roster_regardless_of_the_target(group: str) -> None:
    """R29: the roster is chosen by the group, never by anything the caller claims."""
    calls: list[tuple[str, str]] = []

    async def fetch(roster: str, broadcaster_id: str) -> Sequence[str]:
        calls.append((roster, broadcaster_id))
        return [OPERATOR] if broadcaster_id == OTHER_BROADCASTER else []

    verifier = _verifier(OperatorIdentity(OPERATOR), fetcher=fetch)
    verdict = await verifier.require_writable(OTHER_BROADCASTER, group)
    assert verdict.verdict == (VERDICT_EDITOR if group == "channel" else VERDICT_MODERATOR)
    assert calls == [("editors" if group == "channel" else "moderators", OTHER_BROADCASTER)]


async def test_unknown_group_refuses_before_any_lookup(fake: FakeTwitch) -> None:
    """R29/R28: a group this server does not know is refused, and no roster is fetched."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    with pytest.raises(TwitchMcpError) as exc:
        await wiring.verifier.require_writable(OTHER_BROADCASTER, "channel ")
    assert exc.value.kind == "disabled"
    assert fake.request_count == 0


# ------------------------------------------------------------- R29: refusal costs zero requests


async def test_operator_absent_from_the_roster_is_not_authorized_with_zero_writes(
    fake: FakeTwitch,
) -> None:
    """R29: a roster that does not name the operator is a refusal with zero *writes* (R29).

    The roster lookup itself is the proof R29 demands, so exactly one GET leaves the process and no
    write verb does. ``strict`` is not used here because the roster GET is registered; instead a
    write to that channel is attempted afterwards and provably never reaches the transport.
    """
    wiring = make_wiring(fake, FakeClock(), operator=NOT_A_MODERATOR)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [MODERATOR_ID, USER_ID])
    with pytest.raises(TwitchMcpError) as exc:
        await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert exc.value.kind == "not_authorized"
    assert exc.value.message == NOT_AUTHORIZED_MESSAGE
    assert exc.value.retryable is False, "a refusal is not a transient condition"
    assert fake.methods == ["GET"], "the only request was the roster proof — no write went out"
    assert fake.request_count == 1
    fake.strict = True  # any further request, of any kind, now fails the run
    with pytest.raises(TwitchMcpError):
        await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert fake.request_count == 1, "the refusal is stable and still costs nothing"


async def test_operator_in_no_roster_refuses_every_group(fake: FakeTwitch) -> None:
    """R29: with empty rosters every group refuses — none degrades to allowed, and no write leaves.

    ``channel`` is proven by the editors list while the five moderation groups share one moderators
    list, so six groups cost exactly two proofs and not one write verb leaves the process.
    """
    wiring = make_wiring(fake, FakeClock(), operator=USER_ID)
    for group in sorted(WRITE_GROUPS):
        with pytest.raises(TwitchMcpError) as exc:
            await wiring.verifier.require_writable(OTHER_BROADCASTER, group)
        assert exc.value.kind == "not_authorized", f"group {group} must not degrade open"
    assert set(wiring.fetch_calls) == {
        ("moderators", OTHER_BROADCASTER),
        ("editors", OTHER_BROADCASTER),
    }, "each distinct roster asked once, its result cached between groups (R30)"
    assert fake.request_count == 2
    assert set(fake.methods) == {"GET"}, "not one write verb left the process (R29)"


async def test_only_a_verified_verdict_is_ever_returned() -> None:
    """R29: a refusal is raised, so no Verification object escapes with a bogus verdict.

    Every successful return must carry the verdict Twitch's roster justifies, and the ``VERDICT_NONE``
    value is never handed out as if it authorized anything.
    """
    verdicts = []
    for members in ([], [MODERATOR_ID], [OPERATOR]):
        verifier = _verifier(OperatorIdentity(OPERATOR), members)
        try:
            verdicts.append(await verifier.require_writable(OTHER_BROADCASTER, "polls"))
        except TwitchMcpError as exc:
            assert exc.kind == "not_authorized"
    assert [v.verdict for v in verdicts] == [VERDICT_MODERATOR]
    assert all(v.verdict != VERDICT_NONE for v in verdicts)
    assert all(v.broadcaster_id == OTHER_BROADCASTER for v in verdicts)


def test_refusal_is_logged_without_roster_content(caplog: pytest.LogCaptureFixture) -> None:
    """R29/R53: a refusal is auditable by group and channel, and names no roster material."""
    verifier = _verifier(OperatorIdentity(OPERATOR), [MODERATOR_ID])
    with caplog.at_level("WARNING"), pytest.raises(TwitchMcpError):
        asyncio.run(verifier.require_writable(OTHER_BROADCASTER, "chat"))
    assert any(r.getMessage().startswith("twitch write refused") for r in caplog.records)
    assert OPERATOR not in caplog.text, "the operator id is not leaked into the refusal log"
    assert MODERATOR_ID not in caplog.text, "roster content never reaches the log (R52)"


# --------------------------------------------------------------- R30: verification is cached


async def test_second_check_within_the_ttl_costs_no_extra_request(fake: FakeTwitch) -> None:
    """R30: inside TWITCH_IDENTITY_TTL_SECONDS a verified role is not asked for again."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR, identity_ttl=300)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    first = await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    second = await wiring.verifier.require_writable(OTHER_BROADCASTER, "chat")
    assert first.verdict == second.verdict == VERDICT_MODERATOR
    assert second.cached is True, "the second verdict came from the cache, not from Twitch"
    assert fake.request_count == 1, "the second group reused the same verified roster"
    assert wiring.fetch_calls == [("moderators", OTHER_BROADCASTER)]
    wiring.reset_counts()
    for _ in range(5):
        await wiring.verifier.require_writable(OTHER_BROADCASTER, "polls")
    assert fake.request_count == 0, "repeats within the TTL are free (R30)"


async def test_verification_expires_after_the_ttl_and_refetches(fake: FakeTwitch) -> None:
    """R30: past the TTL the verdict is stale, so the roster is asked for again."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR, identity_ttl=60)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert fake.request_count == 1
    wiring.clock.advance(59)
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert fake.request_count == 1, "still inside the TTL — no refetch"
    wiring.clock.advance(2)
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert fake.request_count == 2, "past the TTL the lookup is re-issued (R30)"


async def test_settings_ttl_is_the_verification_ttl_not_a_hardcoded_constant(
    fake: FakeTwitch,
) -> None:
    """R30: TWITCH_IDENTITY_TTL_SECONDS really is the expiry, not the 300 default."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR, identity_ttl=45)
    assert wiring.settings.twitch_identity_ttl_seconds == 45
    serve_roster(fake, EDITORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "channel")
    wiring.clock.advance(44)
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "channel")
    assert fake.request_count == 1, "44s of a 45s TTL is still cached"
    wiring.clock.advance(2)
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "channel")
    assert fake.request_count == 2, "the configured TTL, not 300, is what expired"


async def test_each_channel_and_roster_has_its_own_entry(fake: FakeTwitch) -> None:
    """R30: the key is (roster, channel) — one channel's verdict cannot cover another."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    with pytest.raises(TwitchMcpError) as exc:
        await wiring.verifier.require_writable(STRANGER_CHANNEL, "moderation")
    assert exc.value.kind == "not_authorized"
    assert fake.request_count == 2, "each channel proves its own membership"
    key = wiring.verifier.cache_key(OTHER_BROADCASTER, "moderators")
    assert key == f"{IDENTITY_CACHE_PREFIX}moderators:{OTHER_BROADCASTER}"
    assert OPERATOR not in key, "the cache key holds ids only, never the operator's token (R52)"


async def test_invalidated_verdict_is_reestablished_from_a_changed_roster(fake: FakeTwitch) -> None:
    """R30: after invalidation the answer comes from Twitch again — in both directions."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    # The roster table is live-served and mutable by design; re-calling serve_roster would register
    # a second route that first-match-wins never reaches, so the "revoked" roster would never be seen.
    roster = serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    await wiring.verifier.require_writable(OTHER_BROADCASTER, "chat")
    clear_identity_cache(wiring.cache)
    roster.moderators[OTHER_BROADCASTER] = [MODERATOR_ID]  # role revoked at the source
    with pytest.raises(TwitchMcpError) as exc:
        await wiring.verifier.require_writable(OTHER_BROADCASTER, "chat")
    assert exc.value.kind == "not_authorized"
    assert fake.request_count == 2, "the re-check genuinely went back to Twitch"


async def test_identity_invalidation_leaves_metadata_alone() -> None:
    """R30: identity can be dropped on its own — the guard's owner cache survives."""
    cache = _cache()

    async def fetch() -> Any:
        return {"ok": True}

    await cache.get("owner:12345", fetch, 300)
    await cache.get(f"{IDENTITY_CACHE_PREFIX}moderators:{BROADCASTER}", fetch, 300)
    assert len(cache) == 2
    cache.invalidate(IDENTITY_CACHE_PREFIX)
    assert len(cache) == 1, "only the identity entries were dropped (R30)"


# ----------------------------------------- R30: invalidated on refresh / disconnect / the 401


def test_identity_is_invalidated_on_token_refresh(tmp_path: Path) -> None:
    """R30 trigger 1 of 3: ``UserAuth.install`` (a refresh) kills every verified role."""
    auth = UserAuth(TokenStore(tmp_path / "twitch_user_token.json"), clock=time.time)
    auth.install(_user_token("identity-old-access-token", "identity-old-refresh-token"))
    cache = _cache()

    async def fetch() -> Any:
        return [OPERATOR]

    key = f"{IDENTITY_CACHE_PREFIX}moderators:{OTHER_BROADCASTER}"
    asyncio.run(cache.get(key, fetch, 300))
    assert len(cache) == 1, "a verdict is cached before the refresh"

    def drop_identity_verdicts() -> None:
        cache.invalidate(IDENTITY_CACHE_PREFIX)

    auth.on_invalidate(drop_identity_verdicts)
    auth.install(_user_token("identity-new-access-token", "identity-new-refresh-token"))
    assert _identity_count(cache) == 0, "a role under the old token cannot outlive the refresh"


@pytest.mark.asyncio
async def test_identity_is_invalidated_on_disconnect(tmp_path: Path) -> None:
    """R30 trigger 2 of 3: ``UserAuth.forget`` (a disconnect) kills every verified role."""
    auth = UserAuth(TokenStore(tmp_path / "twitch_user_token.json"), clock=time.time)
    auth.install(_user_token("identity-old-access-token", "identity-old-refresh-token"))
    cache = _cache()

    async def fetch() -> Any:
        return [OPERATOR]

    await cache.get(f"{IDENTITY_CACHE_PREFIX}editors:{OTHER_BROADCASTER}", fetch, 300)
    assert len(cache) == 1, "a verdict is cached before the disconnect"

    def drop_identity_verdicts() -> None:
        cache.invalidate(IDENTITY_CACHE_PREFIX)

    auth.on_invalidate(drop_identity_verdicts)
    assert await auth.disconnect() == OPERATOR
    assert auth.connected is False
    assert _identity_count(cache) == 0, "no roster verdict survives a disconnect (R30)"
    assert not auth.store.path.exists(), "and the credential itself is gone (R51)"


async def test_identity_is_invalidated_on_the_first_401(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R30 trigger 3 of 3: the first Helix 401 opens the circuit and drops every verified role.

    Exercises the real ``service._write`` sequence of SPEC §6.3: identity (a cached roster verdict)
    → confirmation mint → consume → dispatch, which is the only ordering in which the 401 can clear
    a verdict that was already cached (``service.on_identity_change``).
    """
    register_secret(APP_BEARER)
    register_secret(USER_BEARER)
    try:
        wiring = make_wiring(fake, fclock, operator=OPERATOR)
        serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
        service = _service(wiring, fclock)
        first = await service.create_poll(
            broadcaster_id=OTHER_BROADCASTER, title="A poll", choices=["a", "b"]
        )
        token = _confirm_token(first)
        assert len(wiring.cache) == 1, "the verdict was cached before the write"
        requests_before_write = fake.request_count
        fake.add("POST", r"^/helix/polls$", fake.json({"error": "Unauthorized"}, status=401))
        with pytest.raises(TwitchMcpError) as exc:
            await service.create_poll(
                broadcaster_id=OTHER_BROADCASTER,
                title="A poll",
                choices=["a", "b"],
                confirm=token,
            )
        assert exc.value.kind == "unknown_outcome", "R16: a write answered by a 401 is undetermined"
        assert exc.value.http_status == 401
        assert service.auth_failed is True, "R24: the process-wide 401 circuit is open"
        assert service.identity_verifier is wiring.verifier
        assert _identity_count(wiring.cache) == 0, "R30: no verdict survives the first 401"
        assert fake.request_count == requests_before_write + 1, "the write went out exactly once"
    finally:
        unregister_secret(APP_BEARER)
        unregister_secret(USER_BEARER)


# ---------------------------------------------------- R30: a broken lookup is never an approval


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (RuntimeError("roster resolver exploded"), "not_authorized"),
        (ValueError("not what we expected"), "not_authorized"),
        (TwitchMcpError("permission", "missing scope"), "not_authorized"),
        (TwitchMcpError("forbidden_channel", "not ours"), "not_authorized"),
        (TwitchMcpError("disabled", "group off"), "not_authorized"),
        (TwitchMcpError("invalid_arguments", "bad"), "not_authorized"),
        (TwitchMcpError("stale_confirmation", "stale"), "not_authorized"),
        (TwitchMcpError("unknown_outcome", "undetermined"), "not_authorized"),
        (TwitchMcpError("too_large", "too big"), "not_authorized"),
        (TwitchMcpError("confirmation_required", "plan"), "not_authorized"),
        # A 5xx/transport upstream is genuinely an outage, not a denial: it passes through (R27/R30).
        (TwitchMcpError("upstream", "Twitch is down", http_status=503, retryable=True), "upstream"),
        (TwitchMcpError("upstream", "no route to host", retryable=True), "upstream"),
    ],
    ids=[
        "runtime-error",
        "value-error",
        "permission",
        "forbidden-channel",
        "disabled",
        "invalid-arguments",
        "stale-confirmation",
        "unknown-outcome",
        "too-large",
        "confirmation-required",
        "5xx-pass-through",
        "transport-pass-through",
    ],
)
async def test_a_failed_lookup_never_becomes_an_authorization(
    failure: Exception, expected: str
) -> None:
    """R30 fail-closed: only a proven membership authorizes — a broken lookup denies or passes through.

    The two pass-through cases are the deliberate exception: collapsing a real outage into a denial
    would hide it (R27). Neither case ever returns a verdict.
    """
    verifier = _verifier(OperatorIdentity(OPERATOR), fetcher=_raising(failure))
    with pytest.raises(TwitchMcpError) as exc:
        await verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert exc.value.kind == expected


@pytest.mark.parametrize("kind", ["rate_limited", "unauthenticated"])
async def test_an_auth_or_rate_limit_failure_surfaces_unchanged(
    kind: Literal["rate_limited", "unauthenticated"],
) -> None:
    """R27/R30: auth and rate-limit failures are not laundered into an authorization refusal."""
    original = TwitchMcpError(kind, "upstream said so", http_status=429, retryable=True)
    verifier = _verifier(OperatorIdentity(OPERATOR), fetcher=_raising(original))
    with pytest.raises(TwitchMcpError) as exc:
        await verifier.require_writable(OTHER_BROADCASTER, "chat")
    assert exc.value is original


# ------------------------------------------------------------------- members_of (fail-closed)


def test_members_of_reads_a_normal_helix_roster() -> None:
    """members_of: the ordinary envelope, in order, as strings."""
    body = roster_body([OPERATOR, MODERATOR_ID])
    assert members_of(body) == [OPERATOR, MODERATOR_ID]
    assert members_of({"data": [{"user_id": OPERATOR}]}) == [OPERATOR]


def test_members_of_accepts_a_bare_list_and_a_custom_key() -> None:
    """members_of: the fetcher may hand over either shape; ``user_key`` selects the field."""
    assert members_of([{"user_id": OPERATOR}]) == [OPERATOR]
    assert members_of({"data": [{"moderator_id": OPERATOR}]}, user_key="moderator_id") == [OPERATOR]


def test_members_of_normalises_integer_ids() -> None:
    """members_of: a numeric user id is still a member, rendered as a string."""
    assert members_of({"data": [{"user_id": int(OPERATOR)}]}) == [OPERATOR]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": None},
        {"data": "not-a-list"},
        {"data": [None, "nonsense", 7, ["x"], True]},
        {"data": [{}]},
        {"data": [{"user_id": None}]},
        {"data": [{"user_id": True}]},
        {"data": [{"user_id": "not-a-number"}]},
        {"data": [{"user_id": " 555 "}]},
        {"data": [{"user_id": ""}]},
        {"data": [{"user_id": 12.5}]},
        None,
        "a string",
        7,
        [],
        [{"user_id": "no-envelope-but-not-a-mapping"}],
    ],
    ids=[
        "no-data",
        "data-null",
        "data-string",
        "records-not-mappings",
        "empty-record",
        "user-id-null",
        "user-id-bool",
        "user-id-word",
        "user-id-padded",
        "user-id-empty",
        "user-id-float",
        "body-none",
        "body-string",
        "body-int",
        "body-empty-list",
        "bare-record-without-data",
    ],
)
def test_members_of_never_crashes_and_always_fails_closed(body: Any) -> None:
    """R29 fail-closed: a malformed roster yields no members, so it can never authorize."""
    assert members_of(body) == []


def test_members_of_keeps_valid_members_alongside_garbage() -> None:
    """An unusable entry is simply not a member; it does not void the rest of the roster."""
    body = {"data": [{"user_id": OPERATOR}, {"user_id": "oops"}, None, {"user_id": MODERATOR_ID}]}
    assert members_of(body) == [OPERATOR, MODERATOR_ID]


async def test_a_malformed_roster_body_refuses_the_write(fake: FakeTwitch) -> None:
    """R29 end to end: a garbage roster body is a refusal, never an accidental pass."""
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    fake.add("GET", rf"^/helix{MODERATORS_PATH}$", fake.json({"data": "broken"}))
    with pytest.raises(TwitchMcpError) as exc:
        await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert exc.value.kind == "not_authorized"
    assert fake.request_count == 1, "the lookup happened once and denied"


# ------------------------------------------------------------------ service-level integration


def _service(wiring: IdentityWiring, fclock: FakeClock) -> TwitchService:
    """The real §6.3 write path: guard → identity → confirmation → exactly one dispatch."""

    async def app_token() -> str:
        return APP_BEARER

    async def user_token() -> str:
        return USER_BEARER

    async def fetch_owned(object_id: str) -> Any:
        return {"broadcaster_id": BROADCASTER}

    return TwitchService(
        wiring.settings,
        wiring.client,
        wiring.client.limiter,
        # The operator's own channel and one other channel are both allowlisted, so the guard lets
        # both through and the *identity* layer (R29) is what decides them.
        Guard(frozenset({BROADCASTER, OPERATOR, OTHER_BROADCASTER}), fetch_owned),
        wiring.cache,
        wiring.verifier,
        ConfirmationStore(ttl_seconds=300.0, clock=fclock.clock, wall_clock=fclock.unix),
        app_token=app_token,
        user_token=user_token,
        now=fclock.wall,
    )


def _confirm_token(result: Any) -> str:
    """Pull the minted confirm token out of the first-turn result (R31)."""
    confirmation = getattr(result, "confirmation", None)
    token = getattr(confirmation, "token", None)
    assert isinstance(token, str) and token, f"the first turn must mint a token (R31): {result!r}"
    return token


async def test_service_write_requires_a_verified_role_and_sends_no_write(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R29 via §6.3: an unverified channel is refused at identity, so no confirmation is minted
    and no write leaves the process.

    The roster GET is the proof R29 demands; the write path stops there, which is what
    ``methods == ["GET"]`` pins.
    """
    wiring = make_wiring(fake, fclock, operator=OPERATOR)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [MODERATOR_ID])  # operator absent
    service = _service(wiring, fclock)
    with pytest.raises(TwitchMcpError) as exc:
        await service.create_poll(
            broadcaster_id=OTHER_BROADCASTER, title="A poll", choices=["a", "b"]
        )
    assert exc.value.kind == "not_authorized"
    assert fake.methods == ["GET"], "the roster proof was the only request — never a write (R29)"
    assert fake.request_count == 1
    assert service.confirmations.clear() == 0, "identity refused before the gate minted anything"


async def test_service_honours_the_argument_as_a_target_never_a_claim(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R29 via §6.3: the argument names the *target*; who the operator is comes from the token.

    ``OPERATOR`` (the token's own id) is on the allowlist so the guard lets it through and identity
    accepts it with zero lookups; ``OTHER_BROADCASTER`` is a different channel that the argument
    cannot turn into "own", and with the operator absent from its roster it is refused there too.
    """
    wiring = make_wiring(fake, fclock, operator=OPERATOR)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [MODERATOR_ID])  # operator absent
    service = _service(wiring, fclock)
    own = await service.update_channel(broadcaster_id=OPERATOR, title="my own title")
    assert own.dispatched is False, "the first turn mints, it never dispatches (R31)"
    assert fake.request_count == 0, "own channel needs no roster lookup at all"
    fake.clear()
    with pytest.raises(TwitchMcpError) as exc:
        await service.update_channel(broadcaster_id=OTHER_BROADCASTER, title="someone else's")
    assert exc.value.kind == "not_authorized"
    assert fake.methods == ["GET"], "the argument could not make that channel 'own'"


async def test_service_uses_the_helper_wired_verifier_for_the_same_verdict(
    fake: FakeTwitch,
) -> None:
    """The core_fakes helper and server.py's shape agree on one verdict (no second truth)."""
    verifier, roster = make_identity(
        OPERATOR, _cache(), FakeClock(), moderators={OTHER_BROADCASTER: [OPERATOR]}
    )
    verdict = await verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert verdict.verdict == VERDICT_MODERATOR
    assert roster.calls == 1
    wiring = make_wiring(fake, FakeClock(), operator=OPERATOR)
    serve_roster(fake, MODERATORS_PATH, OTHER_BROADCASTER, [OPERATOR])
    same = await wiring.verifier.require_writable(OTHER_BROADCASTER, "moderation")
    assert same.verdict == verdict.verdict
