"""Fix round 1 regression tests: review M1 and m1-m11, and amendment A1 (SPEC section 16)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from discord_mcp import project
from discord_mcp.capabilities import plan_tools
from discord_mcp.config import Settings
from discord_mcp.discord_client import (
    BootProbe,
    DiscordClient,
    Route,
    _float_or_none,
    _retry_after_seconds,
)
from discord_mcp.errors import DiscordMcpError
from discord_mcp.models import (
    Attachment,
    Embed,
    EmbedField,
    Poll,
    PollAnswer,
    Reaction,
    Role,
    ThreadInfo,
)
from discord_mcp.project import AUDIT_ACTIONS
from discord_mcp.ratelimit import RateLimiter
from discord_mcp.server import _boot_summary
from discord_mcp.service import DiscordService, _sort_channels
from tests.conftest import (
    GUILD,
    OTHER_CHANNEL,
    TEXT,
    THREAD,
    FakeClock,
    FakeDiscord,
    make_settings,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "discord_mcp"
TEXT_ROUTE = Route.build("/channels/{channel_id}", TEXT, channel_id=TEXT)
DM = "303030303030303030"
MISSING = "123123123123123123"


def _rate(retry_after: float) -> httpx.Response:
    return httpx.Response(
        429,
        json={"message": "limited", "retry_after": retry_after},
        headers={"x-ratelimit-scope": "user"},
    )


def _client(
    settings: Any, fake: FakeDiscord, fclock: FakeClock, transport: Any = None
) -> DiscordClient:
    return DiscordClient(
        settings,
        transport=transport or fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )


# ------------------------------------------------------------------ M1: lease released on every path


class SwitchableTransport(httpx.AsyncBaseTransport):
    """Hangs while ``hang`` is set, then serves from the wrapped transport."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.hang = False
        self.never = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.hang:
            await self.never.wait()
        return await self.inner.handle_async_request(request)


async def test_cancelled_in_flight_request_does_not_wedge_its_bucket(
    settings: Settings, fake: FakeDiscord, fclock: FakeClock
) -> None:
    transport = SwitchableTransport(fake.transport())
    transport.hang = True
    client = _client(settings, fake, fclock, transport)
    task = asyncio.ensure_future(client.get(TEXT_ROUTE))
    await asyncio.sleep(0.01)  # the request is now in flight and holds the bucket lease
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    transport.hang = False
    response = await asyncio.wait_for(client.get(TEXT_ROUTE), timeout=2)
    assert response.status == 200


class BrokenBody(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx.DecodingError("bad body")
        yield b""  # pragma: no cover - makes this an async generator


async def test_decoding_error_is_upstream_and_the_next_call_works(
    settings: Settings, fake: FakeDiscord, fclock: FakeClock
) -> None:
    client = _client(settings, fake, fclock)
    fake.add(
        "GET", r".*/channels/\d+", lambda _r: httpx.Response(200, stream=BrokenBody()), times=1
    )
    with pytest.raises(DiscordMcpError) as info:
        await client.get(TEXT_ROUTE)
    assert info.value.kind == "upstream"
    assert (await asyncio.wait_for(client.get(TEXT_ROUTE), timeout=2)).status == 200


# ------------------------------------------------------------------ m3, m4: limiter and retry_after


async def test_shared_bucket_keeps_the_first_learned_state(fclock: FakeClock) -> None:
    limiter = RateLimiter(
        max_wait_seconds=30,
        invalid_budget=100,
        global_rps=1000,
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    first_lease = await limiter.acquire("GET", "/a/{x}", "1")
    first_lease.learn({"x-ratelimit-bucket": "h"})
    first_lease.release()
    first_state = limiter._buckets["bucket:h:1"]
    second = await limiter.acquire("GET", "/b/{x}", "1")
    second.learn({"x-ratelimit-bucket": "h"})
    second.release()
    assert limiter._buckets["bucket:h:1"] is first_state


@pytest.mark.parametrize("value", ["inf", "nan", "-1", "-inf"])
def test_non_finite_or_negative_retry_after_is_treated_as_absent(value: str) -> None:
    assert _float_or_none(value) is None
    assert _retry_after_seconds({"retry_after": value}, {}) == 1.0


def test_non_finite_float_is_treated_as_absent() -> None:
    assert _float_or_none(float("inf")) is None
    assert _retry_after_seconds({"retry_after": float("nan")}, {"retry-after": "3"}) == 3.0


async def test_bucket_stays_blocked_for_the_full_duration_on_later_calls(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_rate_limit_wait_seconds=5)
    client = _client(settings, fake, fclock)
    fake.add("GET", r".*/channels/\d+", lambda _r: _rate(1337.0), times=1)
    with pytest.raises(DiscordMcpError) as first:
        await client.get(TEXT_ROUTE)
    assert first.value.retry_after == 1337.0
    sent = len(fake.requests)
    fclock.t += 100
    with pytest.raises(DiscordMcpError) as second:
        await client.get(TEXT_ROUTE)
    assert second.value.kind == "rate_limited"
    assert second.value.retry_after == 1237.0
    assert len(fake.requests) == sent  # refused locally, no request


# ------------------------------------------------------------------ m5: malformed bodies


async def test_list_body_of_the_wrong_shape_is_upstream(
    fake: FakeDiscord, service: DiscordService
) -> None:
    fake.add("GET", rf".*/guilds/{GUILD}/members", fake.json({"oops": True}), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await service.list_members(GUILD)
    assert info.value.kind == "upstream"


def test_projection_of_a_non_object_is_upstream() -> None:
    with pytest.raises(DiscordMcpError) as info:
        project.guild([])
    assert info.value.kind == "upstream"


# ------------------------------------------------------------------ m7, m8, m9: channel shape and order


def test_type_id_is_null_when_discord_omits_the_type() -> None:
    model = project.channel({"id": "123456789012345678"})
    assert model.type == "unknown" and model.type_id is None
    assert project.channel({"id": "123456789012345678"}).model_dump(mode="json")["type_id"] is None


def test_children_group_under_their_own_parent_when_categories_share_a_position() -> None:
    raws: list[dict[str, Any]] = [
        {"id": "200000000000000002", "type": 4, "position": 0},
        {"id": "200000000000000001", "type": 4, "position": 0},
        {"id": "300000000000000003", "type": 0, "position": 0, "parent_id": "200000000000000002"},
        {"id": "300000000000000001", "type": 0, "position": 0, "parent_id": "200000000000000001"},
    ]
    order = [c["id"] for c in _sort_channels(raws)]
    assert order == [
        "200000000000000001",
        "300000000000000001",
        "200000000000000002",
        "300000000000000003",
    ]


async def test_inapplicable_channel_keys_are_omitted_not_null(service: DiscordService) -> None:
    text = (await service.get_channel(TEXT)).model_dump(mode="json", exclude_unset=True)
    assert "bot_can_view" in text and "bot_can_read_history" in text
    assert "bot_joined" not in text and "thread" not in text
    listed = (await service.list_channels(GUILD)).model_dump(mode="json", exclude_unset=True)
    assert all("bot_can_view" in c and "obfuscated" not in c for c in listed["channels"])
    threads = (await service.list_threads(GUILD)).model_dump(mode="json", exclude_unset=True)
    assert all(
        "bot_joined" in t and "bot_can_view" not in t and "thread" in t for t in threads["threads"]
    )


def test_projection_key_sets_match_section_8() -> None:
    assert set(Role.model_fields) == {
        "id",
        "name",
        "position",
        "color",
        "permissions",
        "managed",
        "hoist",
        "mentionable",
    }
    assert set(Attachment.model_fields) == {
        "id",
        "filename",
        "content_type",
        "size",
        "width",
        "height",
    }
    assert set(Embed.model_fields) == {"type", "title", "description", "url", "fields"}
    assert set(EmbedField.model_fields) == {"name", "value"}
    assert set(Reaction.model_fields) == {"emoji", "count"}
    assert set(Poll.model_fields) == {"question", "answers", "is_finalized"}
    assert set(PollAnswer.model_fields) == {"answer_id", "text", "votes"}
    assert set(ThreadInfo.model_fields) == {
        "archived",
        "locked",
        "archive_timestamp",
        "auto_archive_duration",
        "owner_id",
        "message_count",
        "member_count",
    }


# ------------------------------------------------------------------ A1-R13: one channel refusal


async def test_every_channel_lookup_failure_gets_one_identical_refusal(
    service: DiscordService, fake: FakeDiscord
) -> None:
    messages = set()
    for channel in (DM, MISSING, OTHER_CHANNEL):  # DM, unknown (404), other allowlisted-out guild
        with pytest.raises(DiscordMcpError) as info:
            await service.get_channel(channel)
        assert info.value.kind == "forbidden_guild"
        messages.add(info.value.message)
    assert messages == {"Channel is not in an allowlisted guild"}


async def test_auth_and_rate_limit_still_surface_from_the_channel_lookup(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_rate_limit_wait_seconds=0)
    service = DiscordService(
        settings, _client(settings, fake, fclock), now=fclock.wall, sleep=fclock.sleep
    )
    fake.add("GET", rf".*/channels/{TEXT}$", lambda _r: _rate(5.0), times=1)
    with pytest.raises(DiscordMcpError) as info:
        await service.get_channel(TEXT)
    assert info.value.kind == "rate_limited"


# ------------------------------------------------------------------ A1-R6, A1-R7: flags and planning


async def test_flags_new_is_used_when_flags_is_absent(fake: FakeDiscord, fclock: FakeClock) -> None:
    fake.application = {"id": "222222222222222222", "flags_new": "278528"}
    probe = await _client(make_settings(), fake, fclock).probe()
    assert probe.outcome == "ok" and probe.flags == 278528 and probe.flags_source == "flags_new"


async def test_no_usable_flags_is_ok_with_unknown_flags(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.application = {"id": "222222222222222222"}
    probe = await _client(make_settings(), fake, fclock).probe()
    assert probe.outcome == "ok" and probe.flags is None and probe.flags_source is None


@pytest.mark.parametrize(
    ("probe", "settings_kwargs", "omitted", "forced"),
    [
        (
            BootProbe("ok", flags=None),
            {},
            {"search_messages": "flags_missing", "list_members": "flags_missing"},
            [],
        ),
        (
            BootProbe("ok", flags=0),
            {"discord_force_message_content": True},
            {"list_members": "intent_disabled"},
            ["search_messages"],
        ),
        (
            BootProbe("unreachable"),
            {"discord_force_guild_members": True},
            {"search_messages": "probe_failed"},
            ["list_members"],
        ),
        (BootProbe("ok", flags=278528), {"discord_force_message_content": True}, {}, []),
        (
            BootProbe("ok", flags=0),
            {"discord_force_message_content": True, "discord_enable_search": False},
            {"search_messages": "config_disabled", "list_members": "intent_disabled"},
            [],
        ),
        (BootProbe("auth"), {}, {}, []),
    ],
)
def test_plan_with_flags_missing_and_force_flags(
    probe: BootProbe, settings_kwargs: dict[str, Any], omitted: dict[str, str], forced: list[str]
) -> None:
    plan = plan_tools(make_settings(**settings_kwargs), probe)
    assert dict(plan.omitted) == omitted
    assert list(plan.forced) == forced


def test_boot_line_records_forced_tools_and_observed_flags() -> None:
    plan = plan_tools(
        make_settings(discord_force_message_content=True), BootProbe("ok", flags=None)
    )
    line = _boot_summary(plan)
    assert "forced: search_messages" in line
    assert "application_flags: null source=null" in line
    assert "omitted: search_messages=flags_missing" not in line  # forced, so registered


# ------------------------------------------------------------------ A1-R16: size cap default and hint


def test_default_response_cap_is_four_mebibytes() -> None:
    assert make_settings().discord_max_response_bytes == 4_194_304


async def test_oversized_response_hint_asks_for_a_smaller_limit(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_response_bytes=65_536)
    client = _client(settings, fake, fclock)
    fake.add("GET", r".*/channels/\d+", fake.json({"blob": "x" * 70_000}), times=None)
    with pytest.raises(DiscordMcpError) as info:
        await client.get(TEXT_ROUTE)
    assert "smaller limit" in info.value.hint


# ------------------------------------------------------------------ A1-7.3, A1-7.7: channels and search


async def test_list_channels_has_no_obfuscated_field(service: DiscordService) -> None:
    result = (await service.list_channels(GUILD)).model_dump(mode="json", exclude_unset=True)
    assert all("obfuscated" not in channel for channel in result["channels"])


async def test_search_channel_ids_are_capped_at_25(service: DiscordService) -> None:
    ids = [str(100000000000000000 + i) for i in range(26)]
    with pytest.raises(DiscordMcpError) as info:
        await service.search_messages(GUILD, channel_ids=ids)
    assert info.value.kind == "validation"


async def test_search_looks_up_every_channel_and_sends_negated_has(
    service: DiscordService, fake: FakeDiscord
) -> None:
    result = await service.search_messages(
        GUILD, channel_ids=[TEXT, THREAD], has=["-image", "link"]
    )
    lookups = [r.url.path for r in fake.requests if r.url.path.startswith("/api/v10/channels/")]
    assert f"/api/v10/channels/{TEXT}" in lookups and f"/api/v10/channels/{THREAD}" in lookups
    params = fake.requests[-1].url.params
    assert params.get_list("has") == ["-image", "link"]
    assert params.get_list("channel_id") == [TEXT, THREAD]
    assert all(m.reactions is None for m in result.messages)


async def test_search_results_carry_null_reactions(service: DiscordService) -> None:
    result = await service.search_messages(GUILD)
    dumped = result.model_dump(mode="json", exclude_unset=True)
    assert dumped["messages"] and all(
        "reactions" in m and m["reactions"] is None for m in dumped["messages"]
    )


async def test_search_members_forbidden_hints_at_the_guild_members_intent(
    service: DiscordService, fake: FakeDiscord
) -> None:
    fake.add(
        "GET", rf".*/guilds/{GUILD}/members/search", fake.json({"code": 50001}, 403), times=None
    )
    with pytest.raises(DiscordMcpError) as info:
        await service.search_members(GUILD, "al")
    assert info.value.kind == "permission"
    assert "GUILD_MEMBERS" in info.value.hint


# ------------------------------------------------------------------ A1-7.14: audit log


async def test_audit_after_accepts_zero_iso_and_any_positive_action_type(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    fake.guild["roles"][1]["permissions"] = str((1 << 10) | (1 << 7))
    service = DiscordService(
        make_settings(), _client(make_settings(), fake, fclock), now=fclock.wall, sleep=fclock.sleep
    )
    result = await service.get_audit_log(GUILD, after="0", action_type=1000)
    assert result.order == "oldest_first"
    assert fake.requests[-1].url.params.get("after") == "0"
    assert fake.requests[-1].url.params.get("action_type") == "1000"
    await service.get_audit_log(GUILD, after="2026-10-27T12:00:00Z")
    assert fake.requests[-1].url.params.get("after", "").isdigit()
    with pytest.raises(DiscordMcpError) as info:
        await service.get_audit_log(GUILD, action_type=0)
    assert info.value.kind == "validation"
    with pytest.raises(DiscordMcpError):
        await service.get_audit_log(GUILD, before="0")


# ------------------------------------------------------------------ A1 projections: audit table


# Transcribed from https://docs.discord.com/developers/resources/audit-log (fetched 2026-10-29).
AUDIT_TABLE: list[tuple[int, str]] = [
    (1, "GUILD_UPDATE"),
    (10, "CHANNEL_CREATE"),
    (11, "CHANNEL_UPDATE"),
    (12, "CHANNEL_DELETE"),
    (13, "CHANNEL_OVERWRITE_CREATE"),
    (14, "CHANNEL_OVERWRITE_UPDATE"),
    (15, "CHANNEL_OVERWRITE_DELETE"),
    (20, "MEMBER_KICK"),
    (21, "MEMBER_PRUNE"),
    (22, "MEMBER_BAN_ADD"),
    (23, "MEMBER_BAN_REMOVE"),
    (24, "MEMBER_UPDATE"),
    (25, "MEMBER_ROLE_UPDATE"),
    (26, "MEMBER_MOVE"),
    (27, "MEMBER_DISCONNECT"),
    (28, "BOT_ADD"),
    (30, "ROLE_CREATE"),
    (31, "ROLE_UPDATE"),
    (32, "ROLE_DELETE"),
    (40, "INVITE_CREATE"),
    (41, "INVITE_UPDATE"),
    (42, "INVITE_DELETE"),
    (50, "WEBHOOK_CREATE"),
    (51, "WEBHOOK_UPDATE"),
    (52, "WEBHOOK_DELETE"),
    (60, "EMOJI_CREATE"),
    (61, "EMOJI_UPDATE"),
    (62, "EMOJI_DELETE"),
    (72, "MESSAGE_DELETE"),
    (73, "MESSAGE_BULK_DELETE"),
    (74, "MESSAGE_PIN"),
    (75, "MESSAGE_UNPIN"),
    (80, "INTEGRATION_CREATE"),
    (81, "INTEGRATION_UPDATE"),
    (82, "INTEGRATION_DELETE"),
    (83, "STAGE_INSTANCE_CREATE"),
    (84, "STAGE_INSTANCE_UPDATE"),
    (85, "STAGE_INSTANCE_DELETE"),
    (90, "STICKER_CREATE"),
    (91, "STICKER_UPDATE"),
    (92, "STICKER_DELETE"),
    (100, "GUILD_SCHEDULED_EVENT_CREATE"),
    (101, "GUILD_SCHEDULED_EVENT_UPDATE"),
    (102, "GUILD_SCHEDULED_EVENT_DELETE"),
    (110, "THREAD_CREATE"),
    (111, "THREAD_UPDATE"),
    (112, "THREAD_DELETE"),
    (121, "APPLICATION_COMMAND_PERMISSION_UPDATE"),
    (130, "SOUNDBOARD_SOUND_CREATE"),
    (131, "SOUNDBOARD_SOUND_UPDATE"),
    (132, "SOUNDBOARD_SOUND_DELETE"),
    (140, "AUTO_MODERATION_RULE_CREATE"),
    (141, "AUTO_MODERATION_RULE_UPDATE"),
    (142, "AUTO_MODERATION_RULE_DELETE"),
    (143, "AUTO_MODERATION_BLOCK_MESSAGE"),
    (144, "AUTO_MODERATION_FLAG_TO_CHANNEL"),
    (145, "AUTO_MODERATION_USER_COMMUNICATION_DISABLED"),
    (146, "AUTO_MODERATION_QUARANTINE_USER"),
    (150, "CREATOR_MONETIZATION_REQUEST_CREATED"),
    (151, "CREATOR_MONETIZATION_TERMS_ACCEPTED"),
    (163, "ONBOARDING_PROMPT_CREATE"),
    (164, "ONBOARDING_PROMPT_UPDATE"),
    (165, "ONBOARDING_PROMPT_DELETE"),
    (166, "ONBOARDING_CREATE"),
    (167, "ONBOARDING_UPDATE"),
    (190, "HOME_SETTINGS_CREATE"),
    (191, "HOME_SETTINGS_UPDATE"),
    (192, "VOICE_CHANNEL_STATUS_CREATE"),
    (193, "VOICE_CHANNEL_STATUS_DELETE"),
]


def test_audit_table_has_every_documented_event_and_nothing_else() -> None:
    assert len(AUDIT_TABLE) == 69
    assert dict(AUDIT_TABLE) == AUDIT_ACTIONS


@pytest.mark.parametrize(("value", "name"), AUDIT_TABLE)
def test_each_audit_event_name_is_projected(value: int, name: str) -> None:
    entry = project.audit_entry({"id": "123456789012345678", "action_type": value}, [])
    assert entry.action == name


def test_unknown_audit_value_maps_to_null() -> None:
    entry = project.audit_entry({"id": "123456789012345678", "action_type": 194}, [])
    assert entry.action is None


def test_source_urls_are_recorded_in_the_projection_module() -> None:
    text = (SRC / "project.py").read_text(encoding="utf-8")
    assert "https://docs.discord.com/developers/resources/audit-log" in text
    assert "https://docs.discord.com/developers/resources/channel" in text


# ------------------------------------------------------------------ guard coverage through the service


async def test_dm_message_is_the_same_as_other_channel_refusals(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings()
    service = DiscordService(
        settings, _client(settings, fake, fclock), now=fclock.wall, sleep=fclock.sleep
    )
    with pytest.raises(DiscordMcpError) as info:
        await service.list_messages(DM)
    assert info.value.message == "Channel is not in an allowlisted guild"


async def test_shared_429_bucket_stays_blocked_and_later_calls_are_refused_locally(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings = make_settings(discord_max_rate_limit_wait_seconds=5)
    client = _client(settings, fake, fclock)
    shared = httpx.Response(
        429,
        json={"message": "limited", "retry_after": 900.0},
        headers={"x-ratelimit-scope": "shared"},
    )
    fake.add("GET", r".*/channels/\d+", lambda _r: shared, times=1)
    with pytest.raises(DiscordMcpError) as first:
        await client.get(TEXT_ROUTE)
    assert first.value.kind == "rate_limited" and first.value.retry_after == 900.0
    assert len(fake.requests) == 1  # shared: never replayed in the same call
    assert client.limiter.circuit_retry_after() is None  # shared: never counted as invalid
    fclock.t += 60
    with pytest.raises(DiscordMcpError) as second:
        await client.get(TEXT_ROUTE)
    assert second.value.retry_after == 840.0
    assert len(fake.requests) == 1  # refused locally for the full block
