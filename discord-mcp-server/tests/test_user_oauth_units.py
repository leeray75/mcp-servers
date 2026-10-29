"""U8-U9, U11, U19, U24-U25, U26: oauth.py units with a fake clock and synthetic tokens."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from discord_mcp.errors import DiscordMcpError
from discord_mcp.oauth import (
    PENDING_TTL_SECONDS,
    PendingFlow,
    PendingStore,
    TokenFileInvalid,
    TokenStore,
    UserAuth,
    UserToken,
    authorize_url,
    build_token,
    code_challenge,
    invite_url,
    new_state,
    new_verifier,
)
from tests.conftest import FakeClock

RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
REDIRECT = "http://127.0.0.1:8769/oauth/callback"


def token(
    expires_at: float = 10_000_000.0, access: str = "ACC-1", refresh: str = "REF-1"
) -> UserToken:
    return UserToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        token_type="Bearer",
        scope="identify guilds",
        expires_at=expires_at,
        user_id="444444444444444444",
        username="synthetic-human",
        connected_at=1000.0,
    )


def body(access: str = "NEW", expires_in: int = 604800) -> dict[str, Any]:
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": "identify guilds",
    }


# ------------------------------------------------------------------ PKCE and URLs (U8, U21)


def test_code_challenge_matches_the_rfc7636_vector() -> None:
    assert code_challenge(RFC_VERIFIER) == RFC_CHALLENGE


def test_new_verifier_and_state_come_from_the_right_alphabets() -> None:
    verifier = new_verifier()
    assert len(verifier) == 64
    assert set(verifier) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )
    assert len(new_state()) == 43  # 32 bytes, URL-safe base64


def test_authorize_url_is_exact_with_pkce() -> None:
    url = authorize_url("222", REDIRECT, "STATE", "CHAL")
    assert url == (
        "https://discord.com/oauth2/authorize?response_type=code&client_id=222"
        "&scope=identify%20guilds"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8769%2Foauth%2Fcallback"
        "&state=STATE&prompt=consent&code_challenge=CHAL&code_challenge_method=S256"
    )


def test_authorize_url_without_pkce_has_no_challenge() -> None:
    url = authorize_url("222", REDIRECT, "STATE", None)
    assert "code_challenge" not in url
    assert url.endswith("&state=STATE&prompt=consent")


def test_invite_url_is_exact_and_makes_no_request() -> None:
    assert invite_url("222", "333") == (
        "https://discord.com/oauth2/authorize?client_id=222&scope=bot&permissions=66688"
        "&guild_id=333&disable_guild_select=true"
    )


# ------------------------------------------------------------------ pending flow (U9, U11)


def make_store(clock: FakeClock) -> PendingStore:
    states = iter(["STATE-1", "STATE-2", "STATE-3"])
    return PendingStore(
        clock.clock,
        make_state=lambda: next(states),
        make_verifier=lambda: RFC_VERIFIER,
    )


def test_start_replaces_the_previous_flow_and_claim_is_single_use() -> None:
    clock = FakeClock()
    store = make_store(clock)
    store.start()
    flow = store.start()
    assert flow.state == "STATE-2"
    assert store.claim("STATE-1") == "state_mismatch"  # the first flow was replaced
    store.start()
    claimed = store.claim("STATE-3")
    assert isinstance(claimed, PendingFlow) and claimed.verifier == RFC_VERIFIER
    assert store.claim("STATE-3") == "no_flow"  # a second use is refused


def test_claim_refuses_a_missing_or_wrong_state_and_consumes_the_flow() -> None:
    clock = FakeClock()
    store = make_store(clock)
    store.start()
    assert store.claim(None) == "state_mismatch"
    assert store.claim("STATE-1") == "no_flow"


def test_claim_without_a_flow_is_no_flow() -> None:
    assert make_store(FakeClock()).claim("anything") == "no_flow"


def test_a_flow_expires_after_ten_minutes() -> None:
    clock = FakeClock()
    store = make_store(clock)
    store.start()
    assert store.pending is True
    clock.t += PENDING_TTL_SECONDS
    assert store.pending is False
    assert store.claim("STATE-1") == "expired"


def test_clear_drops_the_pending_flow() -> None:
    clock = FakeClock()
    store = make_store(clock)
    store.start()
    store.clear()
    assert store.pending is False
    assert store.claim("STATE-1") == "no_flow"


# ------------------------------------------------------------------ token file (U19)


def test_token_store_round_trips_and_writes_mode_0600(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "token.json")
    store.save(token())
    loaded = store.load()
    assert loaded == token()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token.json"]  # no temp file left
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(tmp_path / "token.json").st_mode) == 0o600


def test_a_missing_token_file_is_none(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "absent.json")
    assert store.load() is None
    store.delete()  # a missing file is not an error


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"not json", "not_json"),
        (b"[]", "unsupported_format"),
        (b'{"version": 2}', "unsupported_format"),
        (b'{"version": 1, "access_token": "A"}', "invalid_fields"),
    ],
)
def test_invalid_token_files_are_reported_by_reason(
    tmp_path: Path, raw: bytes, reason: str
) -> None:
    path = tmp_path / "token.json"
    path.write_bytes(raw)
    with pytest.raises(TokenFileInvalid) as caught:
        TokenStore(path).load()
    assert caught.value.reason == reason


def test_a_token_file_with_bad_types_or_empty_tokens_is_invalid(tmp_path: Path) -> None:
    import json

    good = {
        "version": 1,
        "access_token": "A",
        "refresh_token": "R",
        "token_type": "Bearer",
        "scope": "identify guilds",
        "expires_at": 5.0,
        "user_id": "4",
        "username": "u",
        "connected_at": 1.0,
    }
    cases = [
        {**good, "expires_at": True},
        {**good, "connected_at": "soon"},
        {**good, "access_token": ""},
    ]
    for case in cases:
        path = tmp_path / "token.json"
        path.write_text(json.dumps(case), encoding="utf-8")
        with pytest.raises(TokenFileInvalid) as caught:
            TokenStore(path).load()
        assert caught.value.reason == "invalid_fields"


def test_an_unreadable_token_path_is_invalid(tmp_path: Path) -> None:
    directory = tmp_path / "is-a-directory"
    directory.mkdir()
    with pytest.raises(TokenFileInvalid) as caught:
        TokenStore(directory).load()
    assert caught.value.reason == "unreadable"


def test_saving_into_a_missing_directory_is_unavailable(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "missing-dir" / "token.json")
    with pytest.raises(DiscordMcpError) as caught:
        store.save(token())
    assert caught.value.kind == "unavailable"


# ------------------------------------------------------------------ token responses (U12, U25)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: "not a dict",
        lambda b: {k: v for k, v in b.items() if k != "access_token"},
        lambda b: {**b, "access_token": ""},
        lambda b: {k: v for k, v in b.items() if k != "refresh_token"},
        lambda b: {**b, "token_type": "Mac"},
        lambda b: {k: v for k, v in b.items() if k != "token_type"},
        lambda b: {**b, "scope": 5},
        lambda b: {**b, "expires_in": 0},
        lambda b: {**b, "expires_in": True},
        lambda b: {**b, "expires_in": "3600"},
    ],
)
def test_build_token_rejects_every_malformed_response(mutate: Any) -> None:
    good = {
        "access_token": "A",
        "refresh_token": "R",
        "token_type": "Bearer",
        "scope": "identify guilds",
        "expires_in": 3600,
    }
    with pytest.raises(DiscordMcpError) as caught:
        build_token(mutate(good), now=100.0, user_id="4", username="u", connected_at=1.0)
    assert caught.value.kind == "upstream"


def test_build_token_sets_expiry_and_keeps_refresh_and_scope_on_refresh() -> None:
    fresh = build_token(
        {
            "access_token": "A",
            "refresh_token": "R",
            "token_type": "bearer",
            "scope": "identify guilds",
            "expires_in": 60,
        },
        now=100.0,
        user_id="4",
        username="u",
        connected_at=1.0,
    )
    assert fresh.expires_at == 160.0
    assert fresh.token_type == "Bearer"
    renewed = build_token(
        {"access_token": "B", "expires_in": 120},
        now=200.0,
        user_id="4",
        username="u",
        connected_at=1.0,
        fallback=fresh,
    )
    assert renewed.access_token.get_secret_value() == "B"
    assert renewed.refresh_token.get_secret_value() == "R"
    assert renewed.scope == "identify guilds"
    assert renewed.expires_at == 320.0


# ------------------------------------------------------------------ UserAuth (U16, U19, U24-U25)


def make_auth(tmp_path: Path, clock: FakeClock, exchange: Any = None) -> UserAuth:
    auth = UserAuth(TokenStore(tmp_path / "token.json"), clock=clock.clock)
    if exchange is not None:
        auth.attach_refresh(exchange)
    return auth


async def test_a_token_near_expiry_is_refreshed_once_under_concurrency(
    tmp_path: Path, fclock: FakeClock
) -> None:
    """U25: three callers near expiry share one refresh and reuse its token."""
    calls: list[str] = []

    async def exchange(refresh: str) -> Mapping[str, Any]:
        calls.append(refresh)
        await asyncio.sleep(0)
        return body("NEW")

    auth = make_auth(tmp_path, fclock, exchange)
    auth.install(token(expires_at=fclock.clock() + 100))
    results = await asyncio.gather(*(auth.access_token() for _ in range(3)))
    assert results == ["NEW", "NEW", "NEW"]
    assert calls == ["REF-1"]
    assert auth.token is not None and auth.token.refresh_token.get_secret_value() == "REF-1"


async def test_a_token_far_from_expiry_is_used_without_refresh(
    tmp_path: Path, fclock: FakeClock
) -> None:
    auth = make_auth(tmp_path, fclock, None)
    auth.install(token(expires_at=fclock.clock() + 10_000))
    assert await auth.access_token() == "ACC-1"


async def test_a_stale_token_already_replaced_is_reused(tmp_path: Path, fclock: FakeClock) -> None:
    """U24: a 401 for a token another caller already replaced does not refresh again."""
    auth = make_auth(tmp_path, fclock, None)
    auth.install(token(access="NEW"))
    assert await auth.access_token(stale="OLD") == "NEW"


async def test_a_stale_token_still_current_is_refreshed(tmp_path: Path, fclock: FakeClock) -> None:
    async def exchange(refresh: str) -> Mapping[str, Any]:
        return body("FRESH")

    auth = make_auth(tmp_path, fclock, exchange)
    auth.install(token(access="OLD"))
    assert await auth.access_token(stale="OLD") == "FRESH"


async def test_a_rejected_refresh_clears_the_token_and_records_it(
    tmp_path: Path, fclock: FakeClock
) -> None:
    """U25: invalid_grant clears the file and memory, and the call is kind=auth."""

    async def exchange(refresh: str) -> Mapping[str, Any]:
        raise DiscordMcpError("auth", "refused")

    auth = make_auth(tmp_path, fclock, exchange)
    auth.install(token(expires_at=fclock.clock() + 1))
    with pytest.raises(DiscordMcpError) as caught:
        await auth.access_token()
    assert caught.value.kind == "auth"
    assert auth.token is None
    assert auth.last_error == "refresh_rejected"
    assert not (tmp_path / "token.json").exists()


async def test_a_refresh_outage_keeps_the_stored_token(tmp_path: Path, fclock: FakeClock) -> None:
    async def exchange(refresh: str) -> Mapping[str, Any]:
        raise DiscordMcpError("unavailable", "down")

    auth = make_auth(tmp_path, fclock, exchange)
    auth.install(token(expires_at=fclock.clock() + 1))
    with pytest.raises(DiscordMcpError) as caught:
        await auth.access_token()
    assert caught.value.kind == "unavailable"
    assert auth.token is not None


async def test_an_upstream_refresh_error_is_not_treated_as_rejection(
    tmp_path: Path, fclock: FakeClock
) -> None:
    async def exchange(refresh: str) -> Mapping[str, Any]:
        raise DiscordMcpError("upstream", "bad request")

    auth = make_auth(tmp_path, fclock, exchange)
    auth.install(token(expires_at=fclock.clock() + 1))
    with pytest.raises(DiscordMcpError) as caught:
        await auth.access_token()
    assert caught.value.kind == "upstream"
    assert auth.token is not None and auth.last_error is None


async def test_a_refresh_that_cannot_be_persisted_is_unavailable(
    tmp_path: Path, fclock: FakeClock
) -> None:
    """U25: the new pair is persisted before use; a failed write means the call is unavailable."""

    async def exchange(refresh: str) -> Mapping[str, Any]:
        return body("NEW")

    auth = UserAuth(
        TokenStore(tmp_path / "missing" / "token.json"), clock=fclock.clock, refresh=exchange
    )
    auth.token = token(expires_at=fclock.clock() + 1)
    with pytest.raises(DiscordMcpError) as caught:
        await auth.access_token()
    assert caught.value.kind == "unavailable"


async def test_no_token_is_auth_with_the_connect_hint(tmp_path: Path, fclock: FakeClock) -> None:
    auth = make_auth(tmp_path, fclock, None)
    with pytest.raises(DiscordMcpError) as caught:
        await auth.access_token()
    assert caught.value.kind == "auth"
    assert "connect_discord_account" in caught.value.hint


async def test_expiry_without_a_refresh_function_is_unavailable(
    tmp_path: Path, fclock: FakeClock
) -> None:
    auth = make_auth(tmp_path, fclock, None)
    auth.install(token(expires_at=fclock.clock() + 1))
    with pytest.raises(DiscordMcpError) as caught:
        await auth.access_token()
    assert caught.value.kind == "unavailable"


def test_load_marks_an_invalid_file_and_logs_only_the_reason(
    tmp_path: Path, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """U19: a bad file is treated as disconnected and reported by reason, never by content."""
    (tmp_path / "token.json").write_bytes(b"SECRET-CONTENT-NOT-JSON")
    auth = make_auth(tmp_path, fclock, None)
    with caplog.at_level(logging.WARNING):
        auth.load()
    assert auth.state == "token_file_invalid"
    assert "SECRET-CONTENT" not in caplog.text
    assert any(getattr(r, "outcome", None) == "not_json" for r in caplog.records)
    auth.forget()
    assert auth.state == "not_connected"
    assert not (tmp_path / "token.json").exists()


def test_state_and_load_of_a_valid_file(tmp_path: Path, fclock: FakeClock) -> None:
    auth = make_auth(tmp_path, fclock, None)
    assert auth.state == "not_connected"
    auth.install(token())
    reloaded = make_auth(tmp_path, fclock, None)
    reloaded.load()
    assert reloaded.state == "connected"
    assert reloaded.token == token()


def test_a_missing_token_folder_is_a_boot_warning_not_a_crash(
    tmp_path: Path, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """U19: the folder is absent: boot continues disconnected and warns once by outcome."""
    auth = UserAuth(TokenStore(tmp_path / "no-such-folder" / "token.json"), clock=fclock.clock)
    with caplog.at_level(logging.WARNING):
        auth.load()
    assert auth.state == "not_connected"
    assert any(getattr(r, "outcome", None) == "token_folder_missing" for r in caplog.records)


def test_a_failed_delete_is_unavailable_and_the_token_leaves_memory(
    tmp_path: Path, fclock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3 fix round (m5): a delete error is a DiscordMcpError, not an internal error, and the
    token is gone from memory before the file is touched."""
    auth = make_auth(tmp_path, fclock, None)
    auth.install(token())

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(DiscordMcpError) as caught:
        auth.forget()
    assert caught.value.kind == "unavailable"
    assert auth.token is None
    assert auth.state == "not_connected"


async def test_disconnect_under_the_refresh_lock_cannot_resurrect_the_file(
    tmp_path: Path, fclock: FakeClock
) -> None:
    """A3 fix round (m6): a refresh in flight finishes first; disconnect then deletes the file for good."""
    gate = asyncio.Event()

    async def exchange(refresh: str) -> Mapping[str, Any]:
        await gate.wait()
        return body("NEW")

    auth = make_auth(tmp_path, fclock, exchange)
    auth.install(token(expires_at=fclock.clock() + 1))

    async def disconnect() -> None:
        async with auth.lock:
            auth.forget()

    refreshing = asyncio.create_task(auth.access_token())
    await asyncio.sleep(0)
    disconnecting = asyncio.create_task(disconnect())
    await asyncio.sleep(0)
    gate.set()
    assert await refreshing == "NEW"
    await disconnecting
    assert auth.token is None
    assert not (tmp_path / "token.json").exists()


def test_redaction_literals_stay_bounded_under_repeated_flow_starts(
    fclock: FakeClock,
) -> None:
    """A3 fix round (m7): an unauthenticated caller of /oauth/start cannot grow the redaction set."""
    from discord_mcp import logging_setup

    store = PendingStore(fclock.clock)
    store.start()
    before = len(logging_setup._literals)  # noqa: SLF001 - the redaction set under test
    for _ in range(200):
        store.start()
    assert len(logging_setup._literals) <= before  # noqa: SLF001


def test_a_stray_temp_file_is_removed_on_load_and_save(tmp_path: Path, fclock: FakeClock) -> None:
    """A3 fix round (m11): leftovers of an interrupted save hold tokens and are deleted."""
    stray = tmp_path / ".token.json.abcdef.tmp"
    stray.write_text("access", encoding="utf-8")
    auth = make_auth(tmp_path, fclock, None)
    auth.load()
    assert not stray.exists()
    stray.write_text("refresh", encoding="utf-8")
    auth.install(token())
    assert not stray.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["token.json"]


def test_redaction_literals_stay_bounded_across_start_callback_expiry_and_clear(
    fclock: FakeClock,
) -> None:
    """A3.2 round 2 (n2): every path that ends a flow unregisters its state and verifier."""
    from discord_mcp import logging_setup

    store = PendingStore(fclock.clock)
    store.start()
    baseline = len(logging_setup._literals)  # noqa: SLF001 - the redaction set under test
    for _ in range(200):
        flow = store.start()
        claimed = store.claim(flow.state)  # callback with the right state
        assert isinstance(claimed, PendingFlow)
        store.release(claimed)
        store.start()
        fclock.t += PENDING_TTL_SECONDS + 1  # expiry
        assert store.claim("anything") == "expired"
        store.start()
        store.clear()  # clear
    assert len(logging_setup._literals) <= baseline  # noqa: SLF001


def test_a_claimed_verifier_stays_redactable_until_released(fclock: FakeClock) -> None:
    """A3.2 round 2 (n2): a verifier still needed for the exchange is never unregistered early."""
    from discord_mcp import logging_setup

    store = PendingStore(fclock.clock)
    flow = store.start()
    claimed = store.claim(flow.state)
    assert isinstance(claimed, PendingFlow)
    assert flow.verifier in logging_setup._literals  # noqa: SLF001
    store.release(claimed)
    assert flow.verifier not in logging_setup._literals  # noqa: SLF001


def test_a_refused_claim_unregisters_its_secrets_at_once(fclock: FakeClock) -> None:
    from discord_mcp import logging_setup

    store = PendingStore(fclock.clock)
    flow = store.start()
    assert store.claim("wrong") == "state_mismatch"
    assert flow.state not in logging_setup._literals  # noqa: SLF001
    assert flow.verifier not in logging_setup._literals  # noqa: SLF001
