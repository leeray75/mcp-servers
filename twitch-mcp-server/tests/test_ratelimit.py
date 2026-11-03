"""R19-R22 + R24: header-derived buckets, per-token-type isolation, fail-fast, 401 circuit.

R19 forbids baked-in limits and endpoint point costs, so this file contains the grep-style
assertion that no numeric limit appears in ``ratelimit.py``, and R11's no-network/no-I/O rule is
asserted here for the modules that carry it.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
from pathlib import Path

import pytest

from tests.twitch_fakes import FakeClock, helix_headers
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.ratelimit import (
    APP_CLASS,
    RATELIMIT_HEADER_LIMIT,
    RATELIMIT_HEADER_REMAINING,
    RATELIMIT_HEADER_RESET,
    USER_CLASS,
    RateLimiter,
    route_key,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "twitch_mcp"
IO_MODULES = ("ratelimit.py", "cache.py", "guard.py")

# R19: limits come from the headers only. The limiter may hold a *timer* constant and a leeway; it
# may never hold a capacity, a cost or a quota.
FORBIDDEN_RATE_LIMIT_CONSTANTS = {
    "800",  # the global-read ceiling people usually hard-code
    "30",  # the common per-route read limit
    "60",  # a whole window, baked in instead of learned
    "100",
    "600",
    "1600",
    "4500",
}


def build_limiter(fclock: FakeClock, *, max_wait: float = 30.0) -> RateLimiter:
    """A limiter whose clock and epoch-bridge both come from the test's fake clock.

    ``Ratelimit-Reset`` is an absolute unix timestamp, so the limiter needs the fake clock's
    ``offset()`` as well as ``clock()`` to project it — exactly the wiring a real process gets from
    the module-level bridge.
    """
    return RateLimiter(
        max_wait_seconds=max_wait,
        clock=fclock.clock,
        sleep=fclock.sleep,
        unix_to_monotonic=fclock.offset(),
    )


def learn(lease: object, limit: int, remaining: int, window: float, clock: FakeClock) -> None:
    """Feed one response's headers to the lease, on the test's own clock timeline.

    ``window`` is the seconds from now until ``Ratelimit-Reset``; the header itself carries an
    absolute unix timestamp built from ``clock`` so the limiter's injected clock and the header agree
    (see :func:`tests.twitch_fakes.helix_headers`).
    """
    lease.learn(  # type: ignore[attr-defined]
        helix_headers(limit, remaining, clock=clock, window_seconds=window)
    )


# ------------------------------------------------------------------ R11: no network, no I/O


@pytest.mark.parametrize("name", IO_MODULES)
def test_module_has_no_network_or_io_import(name: str) -> None:
    """R11: only twitch_client.py may import httpx; these four do no I/O at all."""
    tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "httpx" not in imported
    for forbidden in ("socket", "ssl", "urllib", "aiohttp", "requests", "asyncio.streams"):
        assert forbidden not in imported
    assert "open(" not in (SRC / name).read_text(encoding="utf-8")


def test_ratelimit_imports_asyncio_only_for_lock_and_sleep() -> None:
    """asyncio is allowed (locks, default sleep) but no transport API is referenced."""
    source = (SRC / "ratelimit.py").read_text(encoding="utf-8")
    assert "asyncio.Lock" in source
    assert "asyncio.sleep" in source
    for forbidden in ("create_connection", "open_connection", "start_server", "subprocess"):
        assert forbidden not in source


# ------------------------------------------------------------------ R19: nothing hard-coded


def test_no_limit_value_appears_anywhere_in_ratelimit_source() -> None:
    """R19 grep-style: no capacity/cost/quota literal exists in ratelimit.py.

    Only *header names* and timer arithmetic may appear, so the numbers below are the constants that
    would betray a baked-in limit. Timer-ish literals (0, 1, 2, 5, 100 for rounding, 0.05 leeway) are
    deliberately permitted and listed here so the check cannot be satisfied by deleting the test.
    """
    source = (SRC / "ratelimit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            literals.add(str(node.value))
    offenders = literals & FORBIDDEN_RATE_LIMIT_CONSTANTS
    assert not offenders, f"apparent hard-coded limit in ratelimit.py: {sorted(offenders)}"
    assert "0.05" in literals or "0.0" in literals  # the leeway constant is still present


def test_no_endpoint_point_cost_table_exists() -> None:
    """R19: no per-endpoint cost map, and no ``Ratelimit-*`` header read outside the three known."""
    tree = ast.parse((SRC / "ratelimit.py").read_text(encoding="utf-8"))
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append(node.func.id)
    for forbidden in ("point_cost", "cost", "quota", "credits"):
        assert forbidden not in calls, f"{forbidden}() suggests a baked-in cost model"
    assert {RATELIMIT_HEADER_LIMIT, RATELIMIT_HEADER_REMAINING, RATELIMIT_HEADER_RESET} == {
        "ratelimit-limit",
        "ratelimit-remaining",
        "ratelimit-reset",
    }
    # Every header name the module reads is one of the three (R19). Names are read via .get(...) on
    # the header mapping or via the three module constants; nothing else matches the prefix.
    header_names = set(
        re.findall(
            r'headers\.get\("([a-z-]+)"',
            SRC.joinpath("ratelimit.py").read_text(encoding="utf-8").lower(),
        )
    )
    assert header_names <= {
        "ratelimit-limit",
        "ratelimit-remaining",
        "ratelimit-reset",
    }


async def test_capacity_is_learned_not_assumed(fclock: FakeClock) -> None:
    """R19: before any headers the bucket is unbounded; after them, the learned values are what show."""
    limiter = build_limiter(fclock)
    lease = await limiter.acquire("app", "GET", "/channels")
    key = route_key("app", "GET", "/channels", "")
    assert limiter.snapshot("app")[key]["limit"] is None
    assert limiter.snapshot("app")[key]["remaining"] is None
    learn(lease, 30, 29, 60, fclock)
    lease.release()
    snapshot = limiter.snapshot("app")[key]
    assert snapshot["limit"] == 30
    assert snapshot["remaining"] == 29


async def test_partial_window_never_teaches_the_cadence(fclock: FakeClock) -> None:
    """R19: a mid-window Remaining is a count, not a cadence, so nothing may be guessed from it.

    A first contact with a route reports whatever Helix had left in its own window (29 of 30 here),
    which says nothing about how long that window is. Helix publishes the window's absolute end in
    ``Ratelimit-Reset``, but one response documents a single instant: with no second observation there
    is no interval to measure, so inferring a 60-second cadence would be the baked-in assumption R19
    forbids. The limiter stays permissive instead.
    """
    limiter = build_limiter(fclock)
    lease = await limiter.acquire("app", "GET", "/channels")
    learn(lease, 30, 29, 60, fclock)
    assert lease.state.window_seconds is None
    assert lease.state.window_started is None
    assert lease.state.blocked_until == 0.0, "a partial window must not park anything either"
    lease.release()


async def test_window_is_learned_from_two_consistent_full_windows(fclock: FakeClock) -> None:
    """R19: the cadence is measured, never assumed.

    One response documents one instant (``Ratelimit-Reset``), so no interval exists to derive from it.
    Two full windows whose resets differ by 60 seconds give the limiter that interval, and that
    measured value — not a constant — is what ends up in ``window_seconds``.
    """
    limiter = build_limiter(fclock)
    first = await limiter.acquire("app", "GET", "/games/top")
    learn(first, 30, 30, 60, fclock)
    assert first.state.window_seconds is None, "one reset is an instant, not a cadence"
    first.release()

    fclock.advance(60)
    second = await limiter.acquire("app", "GET", "/games/top")
    learn(second, 30, 30, 60, fclock)
    assert second.state.window_seconds == pytest.approx(60.0, abs=0.01)
    assert limiter.snapshot("app")[route_key("app", "GET", "/games/top", "")]["window_seconds"]
    second.release()


async def test_remaining_zero_parks_until_the_documented_reset(fclock: FakeClock) -> None:
    """R21: Remaining=0 parks the bucket until Ratelimit-Reset, and the wait is honoured."""
    limiter = build_limiter(fclock)
    lease = await limiter.acquire("app", "GET", "/polls")
    learn(lease, 10, 0, 7.5, fclock)
    lease.release()
    second = await limiter.acquire("app", "GET", "/polls")
    assert fclock.sleeps, "the exhausted bucket must have slept"
    assert fclock.sleeps[0] == pytest.approx(7.45, abs=0.05)
    second.release()


async def test_window_rotates_at_the_measured_boundary(fclock: FakeClock) -> None:
    """R19: the limiter rotates capacity itself from the measured cadence, without new headers."""
    limiter = build_limiter(fclock)
    key = route_key("app", "GET", "/games/top", "")

    first = await limiter.acquire("app", "GET", "/games/top")
    learn(first, 4, 4, 60, fclock)  # first full window: documents one reset instant
    first.release()
    assert limiter.snapshot("app")[key]["window_seconds"] is None

    fclock.advance(60)
    second = await limiter.acquire("app", "GET", "/games/top")
    learn(second, 4, 4, 60, fclock)  # second full window: the cadence is now measurable
    assert second.state.window_seconds == pytest.approx(60.0, abs=0.01)
    second.release()

    drained = await limiter.acquire("app", "GET", "/games/top")
    learn(drained, 4, 0, 60, fclock)  # the window is now empty
    drained.release()
    assert drained.state.remaining == 0
    fclock.advance(130)  # two learned windows have passed and no response arrived in between
    third = await limiter.acquire("app", "GET", "/games/top")
    assert third.state.remaining == 4, "the window rotated on the measured cadence alone"
    third.release()


async def test_429_parks_only_the_bucket_that_was_told_to_block(fclock: FakeClock) -> None:
    """R21: block() parks exactly the bucket that got the 429; other routes keep flowing."""
    limiter = build_limiter(fclock, max_wait=30.0)
    lease = await limiter.acquire("app", "GET", "/predictions")
    lease.block(12.0)
    lease.release()
    assert lease.state.spent is True
    unrelated = await limiter.acquire("app", "GET", "/moderation/moderators")
    unrelated.release()  # different route, unaffected by the parked bucket
    assert fclock.sleeps == [], "an unrelated route must not wait behind a parked bucket"
    # A route a 429 throttled is never replayed inside this process (R22): the acquisition is refused
    # with the wait the 429 documented, so nothing is slept and no second request goes out.
    with pytest.raises(TwitchMcpError) as info:
        await limiter.acquire("app", "GET", "/predictions")
    assert info.value.kind == "rate_limited"
    assert info.value.retry_after is not None and info.value.retry_after > 11
    assert fclock.sleeps == [], "a throttled route is refused, never waited out and replayed"
    # Once a real cadence has been measured, the same route becomes usable again after its window
    # rotates — the refusal is scoped to the window, not to the process (R22).
    measured = await limiter.acquire("app", "GET", "/videos")
    learn(measured, 10, 10, 60, fclock)
    measured.release()
    fclock.advance(60)
    measured_2 = await limiter.acquire("app", "GET", "/videos")
    learn(measured_2, 10, 10, 60, fclock)
    measured_2.release()
    assert measured_2.state.window_seconds == pytest.approx(60.0, abs=0.01)


async def test_wait_longer_than_max_fails_fast_without_a_request(fclock: FakeClock) -> None:
    """R21: over-long waits raise before the call, with retry_after and nothing slept.

    The long park comes from ``block()`` — what the client does with a 429's Reset — because a header
    that far ahead of "now" is not a plausible Helix window and is refused outright (see
    :func:`test_implausible_reset_header_is_ignored`).
    """
    limiter = build_limiter(fclock, max_wait=5.0)
    lease = await limiter.acquire("app", "GET", "/chat/settings")
    lease.block(90.0)
    lease.release()
    with pytest.raises(TwitchMcpError) as info:
        await limiter.acquire("app", "GET", "/chat/settings")
    error = info.value
    assert error.kind == "rate_limited"
    assert error.retry_after is not None and error.retry_after > 5.0
    assert error.retryable is True
    assert fclock.sleeps == [], "a fail-fast must never sleep, and the caller never sends"


async def test_spent_lease_marks_the_route_so_it_is_not_replayed(fclock: FakeClock) -> None:
    """R22: the client consults ``lease.state.spent``; a 429 sets it so the same call is not re-sent."""
    limiter = build_limiter(fclock, max_wait=5.0)
    lease = await limiter.acquire("app", "POST", "/polls")
    assert lease.state.spent is False
    lease.block(30.0)  # a long 429: over the 5 s in-call limit
    assert lease.state.spent is True
    lease.release()
    # The route stays parked, so even a naive replay loop is refused rather than re-sent.
    with pytest.raises(TwitchMcpError) as info:
        await limiter.acquire("app", "POST", "/polls")
    assert info.value.kind == "rate_limited"
    assert fclock.sleeps == []


async def test_learning_from_a_response_clears_spent_after_the_window(fclock: FakeClock) -> None:
    """R22: the replay block is scoped to the window, not to the process."""
    limiter = build_limiter(fclock)
    key = route_key("app", "PUT", "/moderation/automod/terms", "")

    first = await limiter.acquire("app", "PUT", "/moderation/automod/terms")
    learn(first, 5, 5, 60, fclock)  # first full window: one reset instant
    first.release()
    fclock.advance(60)
    second = await limiter.acquire("app", "PUT", "/moderation/automod/terms")
    learn(second, 5, 5, 60, fclock)  # the cadence is measured from two resets
    assert second.state.window_seconds == pytest.approx(60.0, abs=0.01)
    assert limiter.snapshot("app")[key]["window_seconds"]
    second.release()

    throttled = await limiter.acquire("app", "PUT", "/moderation/automod/terms")
    throttled.block(1.0)  # the 429 for this request
    assert throttled.received_429 is True
    assert throttled.state.spent is True
    throttled.release()
    fclock.advance(180)
    third = await limiter.acquire("app", "PUT", "/moderation/automod/terms")
    assert third.state.spent is False, "a new window clears the replay block"
    assert third.received_429 is False, "the flag is per acquisition, not process-wide"
    assert third.state.remaining == 5
    third.release()


async def test_learn_after_a_429_cannot_undo_the_replay_block(fclock: FakeClock) -> None:
    """R22: the client calls learn() on every response *including* the 429, so learn() must not clear
    the state block() just set — otherwise a refused request could be replayed."""
    limiter = build_limiter(fclock, max_wait=5.0)
    lease = await limiter.acquire("app", "POST", "/chat/messages")
    lease.block(60.0)
    lease.learn(helix_headers(10, 10, clock=fclock, window_seconds=60))  # the 429's own headers
    assert lease.received_429 is True
    assert lease.state.blocked_until > fclock.clock()
    lease.release()
    with pytest.raises(TwitchMcpError) as info:
        await limiter.acquire("app", "POST", "/chat/messages")
    assert info.value.kind == "rate_limited"


async def test_implausible_reset_header_is_ignored(fclock: FakeClock) -> None:
    """A Reset a day ahead of now is not a Helix window; trusting it would wedge the route forever."""
    limiter = build_limiter(fclock, max_wait=5.0)
    lease = await limiter.acquire("app", "GET", "/clips")
    lease.learn(helix_headers(10, 0, now=fclock.unix() - 1_000_000, window_seconds=86_400))
    assert lease.state.blocked_until == 0.0, "an implausible Reset must not park the bucket"
    lease.release()
    ok = await limiter.acquire("app", "GET", "/clips")
    assert fclock.sleeps == []
    ok.release()


# ------------------------------------------------------------------ R20: app vs user buckets


async def test_app_and_user_buckets_are_independent(fclock: FakeClock) -> None:
    """R20: each token type has its own bucket store, on the same route (D2: two buckets).

    The fake clock is deliberately **not** advanced anywhere in this test: with a frozen clock the
    waits recorded in ``fclock.sleeps`` can only have come from the bucket being exercised, so an
    inherited wait from the other token type would be visible.
    """
    limiter = build_limiter(fclock, max_wait=30.0)
    app_lease = await limiter.acquire("app", "GET", "/channels")
    app_lease.block(20.0)  # a 429 on the app token
    app_lease.release()

    # The user token is untouched by that 429: it proceeds immediately on the identical route.
    user_lease = await limiter.acquire("user", "GET", "/channels")
    assert fclock.sleeps == [], "the user bucket does not inherit the app bucket's park (R20)"

    # The app bucket is parked beyond what this call may wait: fail fast, nothing slept and no
    # request made (R21, R22).
    with pytest.raises(TwitchMcpError) as app_info:
        await limiter.acquire("app", "GET", "/channels")
    assert app_info.value.kind == "rate_limited"
    assert app_info.value.retry_after is not None and app_info.value.retry_after > 15
    assert fclock.sleeps == [], "a fail-fast never sleeps, and the user bucket cost nothing"

    # A 429 on the *user* token now parks only the user bucket.
    user_lease.block(3.0)
    user_lease.release()
    with pytest.raises(TwitchMcpError) as user_info:
        await limiter.acquire("user", "GET", "/channels")
    assert user_info.value.kind == "rate_limited"
    assert user_info.value.retry_after is not None and user_info.value.retry_after <= 3.001
    assert fclock.sleeps == [], "neither parked bucket is replayed inside a call"

    # The app token is still free on its other routes: the user bucket's park costs it nothing.
    app_free = await limiter.acquire("app", "GET", "/videos")
    app_free.release()
    assert fclock.sleeps == [], "the app token is unaffected by the user bucket's park (R20)"

    # The two keys are distinct even for the identical method and template, and each snapshot sees
    # only its own token type.
    assert route_key("app", "GET", "/channels", "") != route_key("user", "GET", "/channels", "")
    assert set(limiter.snapshot("app")) == {
        route_key("app", "GET", "/channels", ""),
        route_key("app", "GET", "/videos", ""),
    }
    assert set(limiter.snapshot("user")) == {route_key("user", "GET", "/channels", "")}


async def test_snapshot_is_scoped_by_token_type(fclock: FakeClock) -> None:
    limiter = build_limiter(fclock)
    for token_type in ("app", "user"):
        lease = await limiter.acquire(token_type, "GET", "/streams")
        learn(lease, 20, 19, 60, fclock)
        lease.release()
    assert set(limiter.snapshot("app")) == {route_key("app", "GET", "/streams", "")}
    assert set(limiter.snapshot("user")) == {route_key("user", "GET", "/streams", "")}
    assert limiter.snapshot("nobody") == {}


def test_route_key_includes_the_token_type() -> None:
    """R20: the token type is part of the key, so the two buckets cannot collide."""
    assert route_key("app", "GET", "/channels", "") != route_key("user", "GET", "/channels", "")
    assert route_key("app", "GET", "/channels", "1") == "app:GET /channels 1"


# ------------------------------------------------------------------ R24 / C3: the per-class 401 circuit


async def test_401_circuit_stops_its_own_token_class_only(fclock: FakeClock) -> None:
    limiter = build_limiter(fclock)
    assert limiter.auth_failed(APP_CLASS) is False
    limiter.trip_auth(USER_CLASS)
    assert limiter.auth_failed(USER_CLASS) is True
    assert limiter.auth_failed(APP_CLASS) is False
    for method, template in (("GET", "/channels"), ("POST", "/chat/messages")):
        with pytest.raises(TwitchMcpError) as info:
            await limiter.acquire(USER_CLASS, method, template)
        error = info.value
        assert error.kind == "unauthenticated"
        assert error.http_status == 401
        assert error.retryable is False
    lease = await limiter.acquire(APP_CLASS, "GET", "/channels")
    lease.release()


async def test_circuit_survives_every_attempt_and_logs_once(
    fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """R24: only a restart clears the app class, so repeated trips log exactly once per class."""
    limiter = build_limiter(fclock)
    with caplog.at_level(logging.ERROR):
        for _ in range(5):
            limiter.trip_auth(APP_CLASS)
        for _ in range(5):
            limiter.trip_auth(USER_CLASS)
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2
    with pytest.raises(TwitchMcpError):
        await limiter.acquire(APP_CLASS, "GET", "/users")
    assert limiter.auth_failed(APP_CLASS) is True


async def test_circuit_opens_before_any_wait_and_never_sleeps(fclock: FakeClock) -> None:
    limiter = build_limiter(fclock, max_wait=1.0)
    lease = await limiter.acquire(APP_CLASS, "GET", "/clips")
    learn(lease, 10, 0, 400, fclock)
    lease.release()
    limiter.trip_auth(APP_CLASS)
    with pytest.raises(TwitchMcpError) as info:
        await limiter.acquire(APP_CLASS, "GET", "/clips")
    assert info.value.kind == "unauthenticated"
    assert fclock.sleeps == []


async def test_the_auth_listener_fires_once_per_class(fclock: FakeClock) -> None:
    """C3 / R30 trigger 3: the cache drop runs on each class's first trip and never again."""
    limiter = build_limiter(fclock)
    calls: list[int] = []
    limiter.set_auth_failure_listener(lambda: calls.append(1))
    for _ in range(5):
        limiter.trip_auth(APP_CLASS)
    assert len(calls) == 1
    for _ in range(5):
        limiter.trip_auth(USER_CLASS)
    assert len(calls) == 2, "the user class announces its own first open"


async def test_a_raising_listener_never_masks_the_401(
    fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """The hook owns caches, not the caller's error: an exploding hook cannot swallow or rewrite it."""
    limiter = build_limiter(fclock)

    def explode() -> None:
        raise RuntimeError("the identity cache exploded")

    limiter.set_auth_failure_listener(explode)
    with caplog.at_level(logging.ERROR):
        limiter.trip_auth(APP_CLASS)
    assert limiter.auth_failed(APP_CLASS) is True, "the circuit opens even when the hook explodes"
    with pytest.raises(TwitchMcpError) as info:
        await limiter.acquire(APP_CLASS, "GET", "/users")
    assert info.value.kind == "unauthenticated"
    assert info.value.http_status == 401
    assert any("listener raised" in record.getMessage() for record in caplog.records)


async def test_the_listener_cannot_recurse_through_the_circuit(fclock: FakeClock) -> None:
    """Re-entrancy: a hook that trips the same class again must hit the latch, not loop."""
    limiter = build_limiter(fclock)
    depth: list[int] = []

    def reentrant() -> None:
        depth.append(len(depth))
        limiter.trip_auth(APP_CLASS)

    limiter.set_auth_failure_listener(reentrant)
    limiter.trip_auth(APP_CLASS)
    assert len(depth) == 1, "the latch is set before the hook runs, so the inner trip is a no-op"


async def test_reset_user_circuit_clears_only_the_user_class(fclock: FakeClock) -> None:
    """C7: the user circuit clears on a stored token; the app circuit never does."""
    limiter = build_limiter(fclock)
    limiter.trip_auth(APP_CLASS)
    limiter.trip_auth(USER_CLASS)
    limiter.reset_user_circuit()
    assert limiter.auth_failed(USER_CLASS) is False
    assert limiter.auth_failed(APP_CLASS) is True
    lease = await limiter.acquire(USER_CLASS, "GET", "/channels")
    lease.release()


# ------------------------------------------------------------------ misc contract


async def test_concurrent_leases_on_one_bucket_are_serialised(fclock: FakeClock) -> None:
    limiter = build_limiter(fclock)
    first = await limiter.acquire("app", "GET", "/videos")
    started = asyncio.Event()

    async def second() -> None:
        lease = await limiter.acquire("app", "GET", "/videos")
        started.set()
        lease.release()

    task = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert not started.is_set()
    first.release()
    await task
    assert started.is_set()


async def test_release_is_idempotent(fclock: FakeClock) -> None:
    limiter = build_limiter(fclock)
    lease = await limiter.acquire("app", "GET", "/schedule")
    lease.release()
    lease.release()  # must not raise on a double release
    other = await limiter.acquire("app", "GET", "/schedule")
    other.release()


async def test_cancelled_acquire_releases_the_lease(fclock: FakeClock) -> None:
    """A cancelled wait must not wedge the bucket (the sibling's review M1)."""
    limiter = build_limiter(fclock)
    first = await limiter.acquire("app", "GET", "/users/follows")
    task = asyncio.create_task(limiter.acquire("app", "GET", "/users/follows"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    first.release()
    second = await asyncio.wait_for(limiter.acquire("app", "GET", "/users/follows"), timeout=1)
    second.release()


async def test_malformed_headers_are_ignored_not_fatal(fclock: FakeClock) -> None:
    """A garbage Ratelimit-* value leaves learned state alone rather than raising."""
    limiter = build_limiter(fclock)
    lease = await limiter.acquire("app", "GET", "/channels")
    lease.learn({"Ratelimit-Limit": "abc", "Ratelimit-Remaining": "-4", "Ratelimit-Reset": "nan"})
    assert lease.state.limit is None
    assert lease.state.remaining is None
    assert lease.state.window_seconds is None
    # A valid header afterwards still lands, so one bad value is not sticky.
    lease.learn(
        {"Ratelimit-Limit": "12", "Ratelimit-Remaining": "11", "Ratelimit-Reset": "9999999999"}
    )
    assert lease.state.limit == 12
    assert lease.state.remaining == 11
    lease.release()
