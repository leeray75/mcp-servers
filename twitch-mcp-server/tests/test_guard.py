"""R25-R27 (guard): the default-deny broadcaster allowlist.

The allowlist test is pure set membership against boot config, so a refused id provably produces
**zero** requests (R25). Every owner-lookup failure collapses to one denial string so the response
cannot be used to probe allowlist membership (R27), and the lookup itself is cached so each object
costs exactly one upstream call (R26).
"""

from __future__ import annotations

from typing import Any, Literal

import pytest

from tests.twitch_fakes import (
    BROADCASTER,
    OTHER_BROADCASTER,
    VIDEO_ID,
    FakeTwitch,
    denied_call_request_count,
    make_guard,
)
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.guard import BROADCASTER_REFUSAL, OWNER_REFUSAL, Guard

# --------------------------------------------------------------------------- R25: zero requests


def test_non_allowlisted_broadcaster_costs_zero_requests(fake: FakeTwitch) -> None:
    """R25: the allowlist is a boot-time set test, so a refused id makes zero requests."""
    guard, _cache, _clock, counters = make_guard(frozenset({BROADCASTER}))
    with pytest.raises(TwitchMcpError):
        guard.require_broadcaster(OTHER_BROADCASTER)
    assert fake.request_count == 0
    assert counters["owner_calls"] == 0
    assert fake.requests == [], "nothing may reach the transport for a refused id (R25)"


def test_non_allowlisted_broadcaster_raises_the_documented_kind() -> None:
    """R25/R43: the refusal is a ``forbidden_channel`` error, not a generic upstream failure."""
    guard, _cache, _clock, _counters = make_guard(frozenset({BROADCASTER}))
    with pytest.raises(TwitchMcpError) as info:
        guard.require_broadcaster(OTHER_BROADCASTER)
    assert info.value.kind == "forbidden_channel"
    assert info.value.message == BROADCASTER_REFUSAL
    assert info.value.retryable is False, "a refusal is not a transient condition"


def test_allowlisted_broadcaster_is_accepted_without_a_request(fake: FakeTwitch) -> None:
    """R25: the positive case is also free — the check never consults upstream."""
    guard, _cache, _clock, counters = make_guard(frozenset({BROADCASTER}))
    guard.require_broadcaster(BROADCASTER)
    assert denied_call_request_count(fake, lambda: guard.require_broadcaster(BROADCASTER)) == 0
    assert counters["owner_calls"] == 0


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        " ",
        "abc",
        "1" * 21,
        "-1",
        "12.5",
        "1e3",
        f"{BROADCASTER} ",
        BROADCASTER + "0",
    ],
    ids=[
        "empty",
        "space",
        "letters",
        "twenty-one-digits",
        "negative",
        "decimal",
        "exponent",
        "trailing-space",
        "not-allowlisted",
    ],
)
def test_malformed_or_unknown_ids_are_refused_without_a_request(
    bad_id: str, fake: FakeTwitch
) -> None:
    """R25/R17: a malformed id never reaches a URL segment, and costs zero requests."""
    guard, _cache, _clock, counters = make_guard(frozenset({BROADCASTER}))
    with pytest.raises(TwitchMcpError) as info:
        guard.require_broadcaster(bad_id)
    assert info.value.kind == "forbidden_channel"
    assert info.value.message == BROADCASTER_REFUSAL
    assert fake.request_count == 0
    assert counters["owner_calls"] == 0


def test_denial_never_echoes_the_refused_id() -> None:
    """R25/R6: the denial text is constant and names no value."""
    guard, _cache, _clock, _counters = make_guard(frozenset({BROADCASTER}))
    with pytest.raises(TwitchMcpError) as info:
        guard.require_broadcaster(OTHER_BROADCASTER)
    assert OTHER_BROADCASTER not in info.value.message
    assert OTHER_BROADCASTER not in info.value.to_text()
    assert info.value.to_payload()["error"]["message"] == BROADCASTER_REFUSAL


def test_denial_is_logged_without_the_refused_id(caplog: pytest.LogCaptureFixture) -> None:
    """R53: refusals are logged by outcome only, so logs cannot enumerate the allowlist."""
    guard, _cache, _clock, _counters = make_guard(frozenset({BROADCASTER}))
    with caplog.at_level("WARNING"), pytest.raises(TwitchMcpError):
        guard.require_broadcaster(OTHER_BROADCASTER)
    assert OTHER_BROADCASTER not in caplog.text
    assert any(r.getMessage() == "refused request" for r in caplog.records)


def test_guard_construction_never_touches_the_network(fake: FakeTwitch) -> None:
    """R11/R25: building a Guard performs no I/O at all."""

    async def unused(object_id: str) -> Any:  # pragma: no cover - never called
        raise AssertionError("this resolver must never be called")

    def built() -> Guard:
        return Guard(frozenset({BROADCASTER}), unused)

    assert denied_call_request_count(fake, built) == 0


def test_guard_exposes_its_allowlist_read_only() -> None:
    """R25: the allowlist comes from boot config and cannot be widened at runtime."""
    guard, _cache, _clock, _counters = make_guard(frozenset({BROADCASTER, OTHER_BROADCASTER}))
    assert guard.allowed_broadcaster_ids == frozenset({BROADCASTER, OTHER_BROADCASTER})
    assert isinstance(guard.allowed_broadcaster_ids, frozenset)


def test_an_empty_allowlist_refuses_everything(fake: FakeTwitch) -> None:
    """R25/R8: with nothing allowlisted, every id is refused (config aborts earlier in practice)."""

    async def unused(object_id: str) -> Any:  # pragma: no cover - never called
        raise AssertionError("unreachable")

    guard = Guard(frozenset(), unused)
    with pytest.raises(TwitchMcpError) as info:
        guard.require_broadcaster(BROADCASTER)
    assert info.value.message == BROADCASTER_REFUSAL
    assert fake.request_count == 0


# --------------------------------------------------------------------------- R26: one lookup


async def test_owner_resolution_costs_exactly_one_lookup_across_repeats() -> None:
    """R26: repeated resolutions of the same object cost exactly one cached upstream lookup."""
    guard, _cache, _clock, counters = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: {"broadcaster_id": BROADCASTER}}
    )
    first = await guard.resolve_owner(VIDEO_ID)
    second = await guard.resolve_owner(VIDEO_ID)
    third = await guard.resolve_owner(VIDEO_ID)
    assert counters["owner_calls"] == 1, "the owner lookup is cached (R26)"
    assert first.broadcaster_id == second.broadcaster_id == third.broadcaster_id == BROADCASTER
    assert second.fetched.cached is True, "the repeat call is served from cache"


async def test_each_object_costs_its_own_single_lookup() -> None:
    """R26: one lookup per distinct object id, no fan-out."""
    guard, _cache, _clock, counters = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: BROADCASTER, "444444444": BROADCASTER}
    )
    await guard.resolve_owner(VIDEO_ID)
    await guard.resolve_owner(VIDEO_ID)
    await guard.resolve_owner("444444444")
    await guard.resolve_owner("444444444")
    assert counters["owner_calls"] == 2


async def test_owner_lookup_uses_a_user_id_bearing_field() -> None:
    """R26: ``user_id`` is a valid owner field (clips); a login name never is."""
    guard, _cache, _clock, _counters = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: {"user_id": BROADCASTER}}
    )
    resolved = await guard.resolve_owner(VIDEO_ID)
    assert resolved.broadcaster_id == BROADCASTER

    login_only, _c, _cl, _cn = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: {"author_name": "some_login"}}
    )
    with pytest.raises(TwitchMcpError) as info:
        await login_only.resolve_owner(VIDEO_ID)
    assert info.value.kind == "forbidden_channel"


async def test_integer_owner_ids_are_normalised() -> None:
    """R26: Helix ids arrive as strings, but an int must not silently fail the membership test."""
    guard, _cache, _clock, _counters = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: {"broadcaster_id": int(BROADCASTER)}}
    )
    resolved = await guard.resolve_owner(VIDEO_ID)
    assert resolved.broadcaster_id == BROADCASTER


async def test_malformed_object_id_costs_no_lookup() -> None:
    """R25/R26: a malformed object id is refused before any owner lookup."""
    guard, _cache, _clock, counters = make_guard(frozenset({BROADCASTER}), {VIDEO_ID: BROADCASTER})
    with pytest.raises(TwitchMcpError) as info:
        await guard.resolve_owner("not-an-id")
    assert info.value.kind == "forbidden_channel"
    assert counters["owner_calls"] == 0


# --------------------------------------------------------------------------- R27: uniform denial


@pytest.mark.parametrize(
    "failure",
    [
        None,  # unknown id: the shared fake raises a 404 upstream error
        TwitchMcpError("permission", "403 from Helix", http_status=403, code=403),
        TwitchMcpError("upstream", "404 from Helix", http_status=404, code=10000),
        ValueError("malformed body"),
        KeyError("data"),
    ],
    ids=["unknown", "403", "404", "malformed", "missing-key"],
)
async def test_every_lookup_failure_gives_the_identical_denial(failure: Exception | None) -> None:
    """R27: unknown id, 403, 404 and a malformed body are indistinguishable to the caller.

    A genuine 5xx/unavailable is *not* in this list: R27 keeps it as itself so an outage cannot hide
    behind a denial (see :func:`test_auth_rate_limit_and_unavailable_pass_through_unchanged`).
    """
    errors: dict[str, Exception] = {} if failure is None else {VIDEO_ID: failure}
    guard, _cache, _clock, counters = make_guard(frozenset({BROADCASTER}), {}, errors=errors)
    with pytest.raises(TwitchMcpError) as info:
        await guard.resolve_owner(VIDEO_ID)
    error = info.value
    assert error.kind == "forbidden_channel"
    assert error.message == OWNER_REFUSAL
    assert error.http_status is None, "the upstream status must not leak through the denial"
    assert error.code is None, "the upstream code must not leak through the denial"
    assert counters["owner_calls"] == 1, "each failure still costs exactly one lookup (R26)"


async def test_an_unreachable_upstream_is_reported_as_an_outage_not_a_denial() -> None:
    """R27: a 5xx that says nothing about ownership must not be laundered into a refusal."""
    errors: dict[str, Exception] = {
        VIDEO_ID: TwitchMcpError("upstream", "503 from Helix", http_status=503, retryable=True)
    }
    guard, _cache, _clock, counters = make_guard(frozenset({BROADCASTER}), {}, errors=errors)
    with pytest.raises(TwitchMcpError) as info:
        await guard.resolve_owner(VIDEO_ID)
    assert info.value.kind == "upstream", "an outage is an outage (R27)"
    assert info.value.http_status == 503
    assert counters["owner_calls"] == 1


async def test_a_5xx_that_reveals_a_non_allowlisted_owner_is_still_a_uniform_denial() -> None:
    """R27: the moment an answer proves the object is not ours, the caller gets the denial."""
    guard, _cache, _clock, counters = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: {"broadcaster_id": OTHER_BROADCASTER}}
    )
    with pytest.raises(TwitchMcpError) as info:
        await guard.resolve_owner(VIDEO_ID)
    assert info.value.message == OWNER_REFUSAL
    assert counters["owner_calls"] == 1


async def test_denial_text_is_byte_identical_across_every_failure() -> None:
    """R27: byte-for-byte identity, so responses cannot be compared to probe membership."""
    texts: set[str] = set()
    payloads: set[str] = set()
    for failure in (
        None,
        TwitchMcpError("permission", "x", http_status=403),
        TwitchMcpError("upstream", "x", http_status=404),
        ValueError("boom"),
    ):
        errors: dict[str, Exception] = {} if failure is None else {VIDEO_ID: failure}
        guard, _cache, _clock, _counters = make_guard(frozenset({BROADCASTER}), {}, errors=errors)
        with pytest.raises(TwitchMcpError) as info:
            await guard.resolve_owner(VIDEO_ID)
        texts.add(info.value.to_text())
        payloads.add(repr(info.value.to_payload()))
    assert len(texts) == 1, texts
    assert len(payloads) == 1, payloads
    assert OWNER_REFUSAL in next(iter(texts))


async def test_object_owned_by_a_non_allowlisted_broadcaster_is_refused_uniformly() -> None:
    """R27: a known-but-refused owner and a failed lookup read identically."""
    guard, _cache, _clock, counters = make_guard(
        frozenset({BROADCASTER}), {VIDEO_ID: {"broadcaster_id": OTHER_BROADCASTER}}
    )
    with pytest.raises(TwitchMcpError) as known:
        await guard.resolve_owner(VIDEO_ID)
    assert known.value.message == OWNER_REFUSAL
    assert counters["owner_calls"] == 1

    errors: dict[str, Exception] = {VIDEO_ID: TwitchMcpError("upstream", "x", http_status=404)}
    failing, _c, _cl, _cn = make_guard(frozenset({BROADCASTER}), {}, errors=errors)
    with pytest.raises(TwitchMcpError) as unknown:
        await failing.resolve_owner(VIDEO_ID)
    assert unknown.value.to_payload() == known.value.to_payload(), (
        "a refused owner must be indistinguishable from a failed lookup (R27)"
    )


@pytest.mark.parametrize(
    ("kind", "status"),
    [("rate_limited", 429), ("unauthenticated", 401), ("upstream", 503)],
)
async def test_auth_rate_limit_and_unavailable_pass_through_unchanged(
    kind: Literal["rate_limited", "unauthenticated", "upstream"], status: int
) -> None:
    """R27: real outages surface as themselves; collapsing them would hide an outage."""
    # 429/401/503 are all genuine outages here (no ownership answer was ever received), so the guard
    # must re-raise the identical error object rather than launder it into a denial.
    passthrough = TwitchMcpError(kind, "upstream trouble", http_status=status, retry_after=3.0)
    guard, _cache, _clock, _counters = make_guard(
        frozenset({BROADCASTER}), {}, errors={VIDEO_ID: passthrough}
    )
    with pytest.raises(TwitchMcpError) as info:
        await guard.resolve_owner(VIDEO_ID)
    assert info.value is passthrough, "the same error object, unchanged"
    assert info.value.kind == kind
    assert info.value.http_status == status


async def test_a_denial_reports_no_upstream_detail() -> None:
    """R27: the denial payload carries no retry hint either, so its shape is constant."""
    errors: dict[str, Exception] = {
        VIDEO_ID: TwitchMcpError("permission", "x", http_status=403, code=403)
    }
    guard, _cache, _clock, _counters = make_guard(frozenset({BROADCASTER}), {}, errors=errors)
    with pytest.raises(TwitchMcpError) as info:
        await guard.resolve_owner(VIDEO_ID)
    assert info.value.retryable is False
    assert info.value.retry_after is None
    assert info.value.http_status is None
    assert info.value.code is None
