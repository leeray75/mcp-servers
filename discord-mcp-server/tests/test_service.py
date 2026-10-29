"""Section 7 tool logic (DiscordService): guard, permission pre-check, paging, search, projections."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest

from discord_mcp.cache import TTLCache
from discord_mcp.config import Settings
from discord_mcp.discord_client import DiscordClient
from discord_mcp.errors import DiscordMcpError
from discord_mcp.service import DiscordService, validate_emoji
from tests.conftest import (
    ALICE,
    BOT,
    CATEGORY,
    GUILD,
    MESSAGE,
    OLD_MESSAGE,
    OTHER_GUILD,
    REPORTS,
    TEXT,
    THREAD,
    THREAD_B,
    VOICE,
    FakeClock,
    FakeDiscord,
    make_settings,
)

DM = "303030303030303030"
OTHER_CHANNEL = "404040404040404040"
PARENT_THREAD_PATH = re.escape(f"/api/v10/channels/{THREAD_B}")


def paths_of(fake: FakeDiscord) -> list[str]:
    return [req.url.path for req in fake.requests]


def mk(settings: Settings, fake: FakeDiscord, fclock: FakeClock) -> DiscordService:
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    return DiscordService(settings, client, now=fclock.wall, sleep=fclock.sleep)


# ------------------------------------------------------------------ guild guard (R12-R14)


async def test_list_guilds_returns_only_allowlisted_and_reports_missing(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_allowed_guild_ids=f"{GUILD},123456789012345678")
    svc = mk(settings, fake, fclock)
    result = await svc.list_guilds()
    assert [g.id for g in result.guilds] == [GUILD]
    assert result.missing_allowlisted_guild_ids == ["123456789012345678"]
    assert result.meta.endpoint == "GET /users/@me/guilds"


async def test_list_guilds_pages_until_exhausted(fake: FakeDiscord, fclock: FakeClock) -> None:
    settings = make_settings()
    svc = mk(settings, fake, fclock)
    full_page = [{"id": str(333333333333330000 + i), "name": f"g{i}"} for i in range(200)]
    full_page[0]["id"] = GUILD
    fake.add("GET", r".*/users/@me/guilds", fake.json(full_page), times=1)
    result = await svc.list_guilds()
    assert [g.id for g in result.guilds] == [GUILD]
    assert len(fake.requests) == 2  # a full page means there may be more; the next one is empty


async def test_non_allowlisted_guild_is_refused_with_no_request(
    fake: FakeDiscord, fclock: FakeClock, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.get_guild(OTHER_GUILD)
    assert info.value.kind == "forbidden_guild"
    assert info.value.message == "Guild not in this server's allowlist"
    assert fake.requests == []


async def test_get_guild_returns_projection_and_caches_metadata(
    fake: FakeDiscord, service: DiscordService
) -> None:
    first = await service.get_guild(GUILD)
    second = await service.get_guild(GUILD)
    assert first.name == "Example Guild"
    assert first.roles[0].name == "Mods"
    assert first.meta.cached is False and second.meta.cached is True
    assert paths_of(fake).count(f"/api/v10/guilds/{GUILD}") == 1


async def test_dm_channel_is_refused(fake: FakeDiscord, service: DiscordService) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.get_channel(DM)
    assert info.value.kind == "forbidden_guild"
    assert info.value.message == "Channel is not in an allowlisted guild"
    assert paths_of(fake) == [f"/api/v10/channels/{DM}"]


async def test_channel_in_other_guild_is_refused_after_only_the_channel_lookup(
    fake: FakeDiscord, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_messages(OTHER_CHANNEL)
    assert info.value.kind == "forbidden_guild"
    assert paths_of(fake) == [f"/api/v10/channels/{OTHER_CHANNEL}"]


# ------------------------------------------------------------------ channels (7.3-7.4)


async def test_list_channels_sorted_with_categories_first_and_bot_flags(
    service: DiscordService,
) -> None:
    result = await service.list_channels(GUILD)
    ids = [c.id for c in result.channels]
    # Uncategorised channels first (by position), then each category followed by its children.
    assert ids == [TEXT, VOICE, CATEGORY, REPORTS]
    by_id = {c.id: c for c in result.channels}
    assert by_id[REPORTS].bot_can_view is True
    assert by_id[REPORTS].bot_can_read_history is False  # overwrite denies history
    assert by_id[VOICE].type == "voice"


async def test_get_channel_checks_view_permission_and_projects(service: DiscordService) -> None:
    channel = await service.get_channel(TEXT)
    assert channel.name == "general" and channel.type == "text"
    assert channel.meta.endpoint == "GET /channels/{channel_id}"


async def test_thread_permission_uses_its_parent_overwrites(service: DiscordService) -> None:
    # THREAD_B's parent (REPORTS) denies READ_MESSAGE_HISTORY to @everyone.
    with pytest.raises(DiscordMcpError) as info:
        await service.list_messages(THREAD_B)
    assert info.value.kind == "permission"
    assert "READ_MESSAGE_HISTORY" in info.value.message


# ------------------------------------------------------------------ messages (7.5-7.6)


async def test_empty_versus_forbidden_no_messages_request_when_history_is_denied(
    fake: FakeDiscord, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_messages(REPORTS)
    assert info.value.kind == "permission"
    assert info.value.hint == "Grant READ_MESSAGE_HISTORY to the bot's role in this channel"
    assert f"/api/v10/channels/{REPORTS}/messages" not in paths_of(fake)


async def test_voice_channel_needs_connect(fake: FakeDiscord, service: DiscordService) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_messages(VOICE)
    assert info.value.kind == "permission" and "CONNECT" in info.value.message
    assert not any(p.endswith("/messages") for p in paths_of(fake))


async def test_genuinely_empty_channel_with_permission_is_success(
    fake: FakeDiscord, service: DiscordService
) -> None:
    fake.messages_by_channel[TEXT] = []
    result = await service.list_messages(TEXT)
    assert result.messages == [] and result.count == 0 and result.has_more is False
    assert result.next_cursor is None


async def test_list_messages_paging_cursors(fake: FakeDiscord, service: DiscordService) -> None:
    back = await service.list_messages(TEXT, limit=2)
    assert back.has_more is True
    assert back.next_cursor == {"before": OLD_MESSAGE}
    assert back.messages[0].id == MESSAGE

    forward = await service.list_messages(TEXT, limit=2, after=OLD_MESSAGE)
    assert forward.next_cursor == {"after": MESSAGE}

    around = await service.list_messages(TEXT, limit=2, around=MESSAGE)
    assert around.next_cursor is None
    query = fake.requests[-1].url.params
    assert query.get("around") == MESSAGE


async def test_list_messages_rejects_two_cursors_before_any_request(
    fake: FakeDiscord, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_messages(TEXT, before=MESSAGE, after=OLD_MESSAGE)
    assert info.value.kind == "validation"
    assert not any(p.endswith("/messages") for p in paths_of(fake))


async def test_list_messages_datetime_argument_becomes_snowflake(
    fake: FakeDiscord, service: DiscordService
) -> None:
    await service.list_messages(TEXT, before="2026-10-27T12:00:00Z")
    assert fake.requests[-1].url.params.get("before", "").isdigit()


async def test_messages_are_never_cached(fake: FakeDiscord, service: DiscordService) -> None:
    await service.list_messages(TEXT)
    await service.list_messages(TEXT)
    assert paths_of(fake).count(f"/api/v10/channels/{TEXT}/messages") == 2


async def test_get_message_and_not_found_hint(fake: FakeDiscord, service: DiscordService) -> None:
    message = await service.get_message(TEXT, MESSAGE)
    assert message.content == "Release notes are in the thread"
    with pytest.raises(DiscordMcpError) as info:
        await service.get_message(TEXT, "123456789012345678")
    assert info.value.kind == "not_found"
    assert "list_messages" in info.value.hint


# ------------------------------------------------------------------ search (7.7)


SEARCH_PATH = re.escape(f"/api/v10/guilds/{GUILD}/messages/search")


async def test_search_flattens_hits_and_reports_paging(
    fake: FakeDiscord, service: DiscordService
) -> None:
    result = await service.search_messages(GUILD, content="deploy", limit=25)
    assert [m.id for m in result.messages] == [MESSAGE, OLD_MESSAGE]
    assert result.total_results == 2 and result.offset == 0
    assert result.has_more is False and result.next_offset is None
    assert fake.requests[-1].url.params.get("content") == "deploy"


async def test_search_has_more_and_next_offset(fake: FakeDiscord, service: DiscordService) -> None:
    fake.search["total_results"] = 10
    result = await service.search_messages(GUILD, offset=0)
    assert result.has_more is True and result.next_offset == 2


async def test_search_index_pending_then_success(
    fake: FakeDiscord, service: DiscordService, fclock: FakeClock
) -> None:
    fake.add(
        "GET",
        SEARCH_PATH,
        fake.json(
            {
                "code": 110000,
                "message": "Index not yet available",
                "documents_indexed": 1,
                "retry_after": 1.5,
            },
            202,
        ),
        times=1,
    )
    result = await service.search_messages(GUILD)
    assert len(result.messages) == 2
    assert 1.5 in fclock.sleeps
    assert paths_of(fake).count(f"/api/v10/guilds/{GUILD}/messages/search") == 2


async def test_search_index_pending_exhausted_returns_index_pending(
    fake: FakeDiscord, service: DiscordService
) -> None:
    fake.add(
        "GET",
        SEARCH_PATH,
        fake.json({"code": 110000, "documents_indexed": 0, "retry_after": 1.0}, 202),
        times=None,
    )
    with pytest.raises(DiscordMcpError) as info:
        await service.search_messages(GUILD)
    assert info.value.kind == "index_pending"
    assert info.value.retryable is True
    assert paths_of(fake).count(f"/api/v10/guilds/{GUILD}/messages/search") == 3  # 1 + 2 retries


async def test_search_index_wait_longer_than_limit_is_not_slept(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_rate_limit_wait_seconds=2)
    svc = mk(settings, fake, fclock)
    fake.add(
        "GET",
        SEARCH_PATH,
        fake.json({"code": 110000, "documents_indexed": 0, "retry_after": 60.0}, 202),
        times=None,
    )
    with pytest.raises(DiscordMcpError) as info:
        await svc.search_messages(GUILD)
    assert info.value.kind == "index_pending"
    assert info.value.retry_after == 60.0
    assert 60.0 not in fclock.sleeps


async def test_search_channel_filters_must_be_in_guild_and_readable(
    fake: FakeDiscord, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.search_messages(GUILD, channel_ids=[OTHER_CHANNEL])
    assert info.value.kind == "forbidden_guild"
    with pytest.raises(DiscordMcpError) as readable:
        await service.search_messages(GUILD, channel_ids=[REPORTS])
    assert readable.value.kind == "permission"
    assert not any(p.endswith("/search") for p in paths_of(fake))


async def test_search_argument_limits_are_validated_before_any_request(
    fake: FakeDiscord, service: DiscordService
) -> None:
    cases: list[dict[str, Any]] = [
        {"content": "x" * 1025},
        {"author_ids": ["1" * 16] * 101},
        {"has": ["gif"]},
        {"before": "2026-10-27T12:00:00"},
    ]
    for kwargs in cases:
        with pytest.raises(DiscordMcpError) as info:
            await service.search_messages(GUILD, **kwargs)
        assert info.value.kind == "validation"
    assert not any(p.endswith("/search") for p in paths_of(fake))


async def test_search_passes_filters_as_repeated_keys(
    fake: FakeDiscord, service: DiscordService
) -> None:
    await service.search_messages(
        GUILD,
        author_ids=[ALICE, BOT],
        mentions=[BOT],
        has=["link", "image"],
        channel_ids=[TEXT],
        before="1234567890123456789",
        sort_by="relevance",
        sort_order="asc",
    )
    request = fake.requests[-1]
    assert request.url.params.get_list("author_id") == [ALICE, BOT]
    assert request.url.params.get_list("has") == ["link", "image"]
    assert request.url.params.get_list("channel_id") == [TEXT]
    assert request.url.params.get("max_id") == "1234567890123456789"
    assert request.url.params.get("sort_by") == "relevance"


# ------------------------------------------------------------------ reactions and pins (7.8-7.9)


@pytest.mark.parametrize(
    ("emoji", "encoded"),
    [("wave:555555555555555555", "wave%3A555555555555555555"), ("✅", "%E2%9C%85")],
)
async def test_reaction_emoji_is_percent_encoded_in_the_path(
    fake: FakeDiscord, service: DiscordService, emoji: str, encoded: str
) -> None:
    result = await service.list_reactions(TEXT, MESSAGE, emoji, type="burst", limit=2)
    assert f"/reactions/{encoded}" in fake.requests[-1].url.raw_path.decode()
    assert fake.requests[-1].url.params.get("type") == "1"
    assert result.next_cursor == {"after": "131313131313131313"}


@pytest.mark.parametrize("emoji", ["ab", "hello world", "x" * 33, "✅ ", ""])
def test_invalid_emoji_is_rejected(emoji: str) -> None:
    with pytest.raises(DiscordMcpError) as info:
        validate_emoji(emoji)
    assert info.value.kind == "validation"


async def test_reactions_need_history_permission(
    fake: FakeDiscord, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_reactions(REPORTS, MESSAGE, "✅")
    assert info.value.kind == "permission"
    assert not any("/reactions/" in p for p in paths_of(fake))


async def test_list_pins_projection_and_cursor(fake: FakeDiscord, service: DiscordService) -> None:
    fake.pins["has_more"] = True
    result = await service.list_pins(TEXT, limit=1, before="2026-10-28T00:00:00Z")
    assert result.pins[0].message.content == "pinned text"
    assert result.next_cursor == {"before": "2026-10-19T09:00:00.000Z"}
    assert fake.requests[-1].url.params.get("before") == "2026-10-28T00:00:00.000Z"


async def test_list_pins_without_history_is_permission(
    fake: FakeDiscord, service: DiscordService
) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_pins(REPORTS)
    assert info.value.kind == "permission"
    assert not any(p.endswith("/pins") for p in paths_of(fake))


# ------------------------------------------------------------------ threads, members, audit


async def test_list_threads_filters_sorts_and_marks_bot_joined(
    fake: FakeDiscord, service: DiscordService
) -> None:
    result = await service.list_threads(GUILD)
    assert [t.id for t in result.threads] == [THREAD_B, THREAD]  # newest id first
    assert result.threads[0].bot_joined is False and result.threads[1].bot_joined is True
    filtered = await service.list_threads(GUILD, parent_channel_id=REPORTS)
    assert [t.id for t in filtered.threads] == [THREAD_B]


async def test_list_threads_parent_must_be_in_guild(service: DiscordService) -> None:
    with pytest.raises(DiscordMcpError) as info:
        await service.list_threads(GUILD, parent_channel_id=OTHER_CHANNEL)
    assert info.value.kind == "forbidden_guild"


async def test_list_members_sends_limit_and_cursor(
    fake: FakeDiscord, service: DiscordService
) -> None:
    result = await service.list_members(GUILD, limit=2)
    assert fake.requests[-1].url.params.get("limit") == "2"
    assert result.has_more is True and result.next_cursor == {"after": "131313131313131313"}
    single = await service.list_members(GUILD, limit=5, after=ALICE)
    assert single.has_more is False and single.next_cursor is None


async def test_list_members_cursor_is_null_without_a_user(
    fake: FakeDiscord, service: DiscordService
) -> None:
    fake.add("GET", rf"/api/v10/guilds/{GUILD}/members", fake.json([{"roles": []}]), times=1)
    result = await service.list_members(GUILD, limit=1)
    assert result.has_more is True and result.next_cursor is None


async def test_get_member_and_search_members(fake: FakeDiscord, service: DiscordService) -> None:
    member = await service.get_member(GUILD, ALICE)
    assert member.nick == "Ali"
    found = await service.search_members(GUILD, "al", limit=3)
    assert len(found.members) == 2
    assert fake.requests[-1].url.params.get("query") == "al"
    with pytest.raises(DiscordMcpError) as info:
        await service.search_members(GUILD, "")
    assert info.value.kind == "validation"


async def test_audit_log_needs_view_audit_log_at_guild_level(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.guild["roles"][1]["permissions"] = "66560"  # @everyone without VIEW_AUDIT_LOG
    svc = mk(make_settings(), fake, fclock)
    with pytest.raises(DiscordMcpError) as info:
        await svc.get_audit_log(GUILD)
    assert info.value.kind == "permission" and "VIEW_AUDIT_LOG" in info.value.message
    assert not any(p.endswith("/audit-logs") for p in paths_of(fake))


async def test_audit_log_projection_order_and_cursor(fake: FakeDiscord, fclock: FakeClock) -> None:
    fake.guild["roles"][1]["permissions"] = str((1 << 10) | (1 << 7))
    svc = mk(make_settings(), fake, fclock)
    result = await svc.get_audit_log(GUILD, limit=2)
    assert result.order == "newest_first"
    assert result.entries[0].user_name == "alice"
    assert result.entries[0].action == "THREAD_CREATE"
    assert result.has_more is True and result.next_cursor == {"before": "202020202020202020"}
    after = await svc.get_audit_log(GUILD, limit=2, after="100000000000000000")
    assert after.order == "oldest_first" and after.next_cursor == {"after": "202020202020202020"}


async def test_audit_log_argument_rules(fake: FakeDiscord, fclock: FakeClock) -> None:
    fake.guild["roles"][1]["permissions"] = str((1 << 10) | (1 << 7))
    svc = mk(make_settings(), fake, fclock)
    with pytest.raises(DiscordMcpError):
        await svc.get_audit_log(GUILD, before="1" * 16, after="2" * 16)
    with pytest.raises(DiscordMcpError):
        await svc.get_audit_log(GUILD, action_type=0)
    with pytest.raises(DiscordMcpError):
        await svc.get_audit_log(GUILD, user_id="nope")


# ------------------------------------------------------------------ identity and boot


async def test_bot_identity_is_fetched_lazily_and_cached(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    svc = mk(make_settings(), fake, fclock)
    assert svc.bot_user_id is None
    await svc.list_channels(GUILD)
    assert svc.bot_user_id == BOT
    assert paths_of(fake).count("/api/v10/users/@me") == 1


async def test_bot_identity_malformed_is_upstream(fake: FakeDiscord, fclock: FakeClock) -> None:
    fake.add("GET", r".*/users/@me", fake.json({"username": "x"}), times=None)
    svc = mk(make_settings(), fake, fclock)
    with pytest.raises(DiscordMcpError) as info:
        await svc.list_channels(GUILD)
    assert info.value.kind == "upstream"


async def test_boot_warns_on_application_id_mismatch(
    fake: FakeDiscord, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    svc = mk(make_settings(discord_application_id="777777777777777777"), fake, fclock)
    with caplog.at_level(logging.WARNING):
        outcome = await svc.boot()
    assert outcome == "ok"
    assert any("DISCORD_APPLICATION_ID" in r.getMessage() for r in caplog.records)
    assert svc.probe_outcome == "ok" and svc.bot_user_id == BOT


async def test_boot_without_application_id_does_not_warn(
    fake: FakeDiscord, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    svc = mk(make_settings(), fake, fclock)
    with caplog.at_level(logging.WARNING):
        await svc.boot()
    assert not any("DISCORD_APPLICATION_ID" in r.getMessage() for r in caplog.records)


async def test_metadata_ttl_zero_disables_the_service_cache(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    svc = mk(make_settings(ttl_metadata_seconds=0), fake, fclock)
    await svc.get_guild(GUILD)
    await svc.get_guild(GUILD)
    assert paths_of(fake).count(f"/api/v10/guilds/{GUILD}") == 2


def test_service_uses_the_injected_cache(
    settings: Settings, fake: FakeDiscord, fclock: FakeClock
) -> None:
    cache = TTLCache(max_entries=8, max_bytes=1000, clock=fclock.clock, wall_clock=fclock.wall)
    client = DiscordClient(
        settings,
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    svc = DiscordService(settings, client, now=fclock.wall, sleep=fclock.sleep, cache=cache)
    assert svc.guard.allowed_guild_ids == frozenset({GUILD})
    assert httpx is not None
