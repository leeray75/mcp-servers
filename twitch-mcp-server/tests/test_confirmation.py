"""R31–R33: single-use, TTL-bound confirmation tokens bound to (tool, args-hash, channel)."""

from __future__ import annotations

import pytest

from tests.twitch_fakes import BROADCASTER, OPERATOR, FakeClock
from twitch_mcp.confirmation import (
    ConfirmationPlan,
    ConfirmationStore,
    args_hash,
    canonical_json,
    new_token,
)
from twitch_mcp.errors import TwitchMcpError

TOOL = "send_chat_message"
ARGS = {"message": "hello", "broadcaster_id": BROADCASTER}


def _store(fclock: FakeClock, ttl: float = 300.0) -> ConfirmationStore:
    return ConfirmationStore(ttl_seconds=ttl, clock=fclock.clock, wall_clock=fclock.unix)


# --------------------------------------------------------------------------- mint (R31)


def test_mint_binds_tool_args_and_channel_without_storing_values(fclock: FakeClock) -> None:
    store = _store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    assert minted.plan == ConfirmationPlan(
        tool=TOOL, broadcaster_id=BROADCASTER, arguments_hash=args_hash(ARGS)
    )
    assert minted.expires_at == fclock.clock() + 300.0
    # R52: the store keeps only the digest — the chat text is nowhere inside it.
    assert "hello" not in repr(store.__dict__)


def test_tokens_are_unique_urlsafe_and_long() -> None:
    tokens = {new_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        assert len(token) >= 32
        assert all(c.isalnum() or c in "-_" for c in token)


def test_args_hash_is_key_order_independent_and_sensitive_to_values() -> None:
    a = args_hash({"x": 1, "y": [1, 2], "z": {"b": True, "a": None}})
    b = args_hash({"z": {"a": None, "b": True}, "y": [1, 2], "x": 1})
    assert a == b  # canonical JSON: dict order cannot change the hash
    assert args_hash({"x": 1}) != args_hash({"x": 2})
    assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


# --------------------------------------------------------------------------- consume (R32)


def test_consume_is_single_use(fclock: FakeClock) -> None:
    store = _store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    store.consume(TOOL, BROADCASTER, ARGS, minted.token)  # first presentation passes
    with pytest.raises(TwitchMcpError) as exc:
        store.consume(TOOL, BROADCASTER, ARGS, minted.token)  # replay refused
    assert exc.value.kind == "stale_confirmation"
    assert exc.value.retryable is False


def test_consume_refuses_argument_drift(fclock: FakeClock) -> None:
    store = _store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    with pytest.raises(TwitchMcpError) as exc:
        store.consume(TOOL, BROADCASTER, {**ARGS, "message": "different"}, minted.token)
    assert exc.value.kind == "stale_confirmation"
    # The refused token is consumed by the attempt: it cannot be retried with the original args.
    with pytest.raises(TwitchMcpError):
        store.consume(TOOL, BROADCASTER, ARGS, minted.token)


def test_consume_refuses_tool_and_broadcaster_drift(fclock: FakeClock) -> None:
    store = _store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    with pytest.raises(TwitchMcpError) as exc:
        store.consume("delete_chat_message", BROADCASTER, ARGS, minted.token)
    assert exc.value.kind == "stale_confirmation"
    minted2 = store.mint(TOOL, BROADCASTER, ARGS)
    with pytest.raises(TwitchMcpError) as exc:
        store.consume(TOOL, OPERATOR, ARGS, minted2.token)
    assert exc.value.kind == "stale_confirmation"


def test_consume_refuses_unknown_token(fclock: FakeClock) -> None:
    store = _store(fclock)
    with pytest.raises(TwitchMcpError) as exc:
        store.consume(TOOL, BROADCASTER, ARGS, "never-minted-token-value")
    assert exc.value.kind == "stale_confirmation"


def test_expiry_refuses_after_ttl(fclock: FakeClock) -> None:
    store = _store(fclock, ttl=60.0)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    fclock.advance(59.0)
    store.consume(TOOL, BROADCASTER, ARGS, minted.token)  # still alive just before expiry
    minted2 = store.mint(TOOL, BROADCASTER, ARGS)
    fclock.advance(61.0)
    with pytest.raises(TwitchMcpError) as exc:
        store.consume(TOOL, BROADCASTER, ARGS, minted2.token)
    assert exc.value.kind == "stale_confirmation"


def test_validate_only_does_not_consume(fclock: FakeClock) -> None:
    store = _store(fclock)
    minted = store.mint(TOOL, BROADCASTER, ARGS)
    assert store.validate_only(TOOL, BROADCASTER, ARGS, minted.token) is True
    assert store.validate_only(TOOL, BROADCASTER, ARGS, minted.token) is True
    store.consume(TOOL, BROADCASTER, ARGS, minted.token)  # still consumable afterwards


# --------------------------------------------------------------------------- clearing (R33)


def test_clear_drops_everything(fclock: FakeClock) -> None:
    store = _store(fclock)
    tokens = [store.mint(TOOL, BROADCASTER, {**ARGS, "i": i}).token for i in range(3)]
    assert len(store) == 3
    assert store.clear() == 3
    assert len(store) == 0
    for token in tokens:
        with pytest.raises(TwitchMcpError) as exc:
            store.consume(TOOL, BROADCASTER, ARGS, token)
        assert exc.value.kind == "stale_confirmation"


def test_prune_removes_only_expired(fclock: FakeClock) -> None:
    store = _store(fclock, ttl=100.0)
    old = store.mint(TOOL, BROADCASTER, {"n": 1}).token
    fclock.advance(101.0)
    fresh = store.mint(TOOL, BROADCASTER, {"n": 2}).token
    assert store.prune() == 1
    assert old not in store
    assert store.validate_only(TOOL, BROADCASTER, {"n": 2}, fresh) is True
