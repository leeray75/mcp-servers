"""Section 10 permissions, R5-R8 capability planning, and R34-R35 projections."""

from __future__ import annotations

import pytest

from discord_mcp import project
from discord_mcp.capabilities import TOOL_SPECS, intents_from_flags, plan_tools
from discord_mcp.discord_client import BootProbe
from discord_mcp.models import Channel, Guild, Message
from discord_mcp.permissions import (
    ADMINISTRATOR,
    ALL_PERMISSIONS,
    CONNECT,
    READ_MESSAGE_HISTORY,
    VIEW_AUDIT_LOG,
    VIEW_CHANNEL,
    can_read_history,
    can_view,
    effective_permissions,
    missing_permissions,
    parse_permissions,
)
from tests.conftest import ALICE, BOT, GUILD, load, make_settings

OWNER = "444444444444444444"
EVERYONE = GUILD
MODS = "666666666666666666"


def guild(roles: list[dict[str, str]], owner: str = OWNER) -> dict[str, object]:
    return {"id": GUILD, "owner_id": owner, "roles": roles}


def role(role_id: str, perms: int) -> dict[str, str]:
    return {"id": role_id, "permissions": str(perms)}


EVERYONE_VIEW_HISTORY = role(EVERYONE, VIEW_CHANNEL | READ_MESSAGE_HISTORY)


def test_permission_values_are_parsed_as_ints() -> None:
    assert parse_permissions("66560") == 66560
    assert parse_permissions(4) == 4
    assert parse_permissions("abc") == 0
    assert parse_permissions(True) == 0
    assert parse_permissions(None) == 0


def test_owner_gets_everything() -> None:
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY], owner=BOT),
        member_role_ids=[],
        bot_user_id=BOT,
        channel={
            "permission_overwrites": [
                {"id": EVERYONE, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}
            ]
        },
    )
    assert perms == ALL_PERMISSIONS


def test_administrator_role_gets_everything() -> None:
    admin = role(MODS, ADMINISTRATOR)
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY, admin]),
        member_role_ids=[MODS],
        bot_user_id=BOT,
        channel={
            "permission_overwrites": [
                {"id": EVERYONE, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}
            ]
        },
    )
    assert perms == ALL_PERMISSIONS


def test_everyone_role_base_applies_without_overwrites() -> None:
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY]),
        member_role_ids=[],
        bot_user_id=BOT,
        channel={"permission_overwrites": []},
    )
    assert can_view(perms) and can_read_history(perms)


def test_everyone_deny_then_role_allow_restores_the_permission() -> None:
    channel = {
        "permission_overwrites": [
            {"id": EVERYONE, "type": 0, "allow": "0", "deny": str(READ_MESSAGE_HISTORY)},
            {"id": MODS, "type": 0, "allow": str(READ_MESSAGE_HISTORY), "deny": "0"},
        ]
    }
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY, role(MODS, 0)]),
        member_role_ids=[MODS],
        bot_user_id=BOT,
        channel=channel,
    )
    assert can_read_history(perms)


def test_role_deny_and_allow_are_combined_then_applied() -> None:
    channel = {
        "permission_overwrites": [
            {"id": "r1", "type": 0, "allow": "0", "deny": str(READ_MESSAGE_HISTORY)},
            {"id": "r2", "type": 0, "allow": str(READ_MESSAGE_HISTORY), "deny": "0"},
        ]
    }
    # Combined: deny READ and allow READ -> allow wins because deny is applied first.
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY, role("r1", 0), role("r2", 0)]),
        member_role_ids=["r1", "r2"],
        bot_user_id=BOT,
        channel=channel,
    )
    assert can_read_history(perms)


def test_member_overwrite_for_the_bot_is_applied_last() -> None:
    channel = {
        "permission_overwrites": [
            {"id": BOT, "type": 1, "allow": "0", "deny": str(READ_MESSAGE_HISTORY)},
        ]
    }
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY]), member_role_ids=[], bot_user_id=BOT, channel=channel
    )
    assert can_view(perms) and not can_read_history(perms)


def test_without_view_channel_every_other_permission_is_absent() -> None:
    channel = {
        "permission_overwrites": [
            {"id": EVERYONE, "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)},
        ]
    }
    perms = effective_permissions(
        guild=guild([EVERYONE_VIEW_HISTORY]), member_role_ids=[], bot_user_id=BOT, channel=channel
    )
    assert perms == 0
    assert missing_permissions(perms, [READ_MESSAGE_HISTORY]) == ["READ_MESSAGE_HISTORY"]


def test_guild_level_permissions_ignore_overwrites() -> None:
    perms = effective_permissions(
        guild=guild([role(EVERYONE, VIEW_AUDIT_LOG)]),
        member_role_ids=[],
        bot_user_id=BOT,
        channel=None,
    )
    assert missing_permissions(perms, [VIEW_AUDIT_LOG]) == []


def test_missing_permissions_are_named_in_order() -> None:
    assert missing_permissions(0, [VIEW_CHANNEL, READ_MESSAGE_HISTORY, CONNECT]) == [
        "VIEW_CHANNEL",
        "READ_MESSAGE_HISTORY",
        "CONNECT",
    ]


@pytest.mark.parametrize(
    ("flags", "content", "members"),
    [
        (0, False, False),
        (1 << 18, True, False),  # MESSAGE_CONTENT
        (1 << 19, True, False),  # MESSAGE_CONTENT_LIMITED
        (1 << 14, False, True),  # GUILD_MEMBERS
        (1 << 15, False, True),  # GUILD_MEMBERS_LIMITED
        (278528, True, True),
    ],
)
def test_intent_flags_matrix(flags: int, content: bool, members: bool) -> None:
    assert intents_from_flags(flags) == {"message_content": content, "guild_members": members}


def test_plan_registers_everything_when_all_intents_are_on() -> None:
    plan = plan_tools(make_settings(), BootProbe("ok", flags=278528))
    assert plan.registered == tuple(spec.name for spec in TOOL_SPECS)
    assert plan.omitted == ()


def test_plan_omits_intent_tools_when_intents_are_off() -> None:
    plan = plan_tools(make_settings(), BootProbe("ok", flags=0))
    assert dict(plan.omitted) == {
        "search_messages": "intent_disabled",
        "list_members": "intent_disabled",
    }
    assert "search_messages" not in plan.registered


def test_plan_config_disable_wins_over_intents() -> None:
    settings = make_settings(discord_enable_search=False, discord_enable_member_list=False)
    plan = plan_tools(settings, BootProbe("ok", flags=278528))
    assert dict(plan.omitted) == {
        "search_messages": "config_disabled",
        "list_members": "config_disabled",
    }


def test_plan_unreachable_omits_only_intent_tools() -> None:
    plan = plan_tools(make_settings(), BootProbe("unreachable"))
    assert dict(plan.omitted) == {"search_messages": "probe_failed", "list_members": "probe_failed"}
    assert len(plan.registered) == len(TOOL_SPECS) - 2


def test_plan_auth_keeps_every_tool_so_each_returns_kind_auth() -> None:
    plan = plan_tools(make_settings(), BootProbe("auth"))
    assert plan.omitted == ()
    assert plan.registered == tuple(spec.name for spec in TOOL_SPECS)


def test_registration_order_is_the_section_7_order() -> None:
    assert [spec.name for spec in TOOL_SPECS] == [
        "list_guilds",
        "get_guild",
        "list_channels",
        "get_channel",
        "list_messages",
        "get_message",
        "search_messages",
        "list_reactions",
        "list_pins",
        "list_threads",
        "list_members",
        "get_member",
        "search_members",
        "get_audit_log",
    ]


# ------------------------------------------------------------------ projections (R34-R35)


def test_guild_projection_keeps_only_section_8_fields_and_sorts_roles() -> None:
    raw = load("guild")
    raw["unexpected_field"] = "dropped"
    projected = project.guild(raw)
    assert [r.name for r in projected.roles] == ["Mods", "@everyone"]
    assert projected.roles[0].color == "#3498db"
    assert projected.roles[1].color is None
    assert projected.emoji_count == 1
    assert projected.created_at.endswith("Z")
    assert set(Guild.model_fields) == set(projected.model_dump())


def test_channel_projection_flags_obfuscation_and_threads() -> None:
    raw = load("guild_channels")[3]  # voice channel with flag 1<<17
    model = project.channel(raw, bot_can_view=True, bot_can_read_history=False)
    assert model.type == "voice"
    assert "obfuscated" not in model.model_dump()
    assert model.bot_can_view is True and model.bot_can_read_history is False
    thread = project.channel(load("threads_active")["threads"][0], bot_joined=True)
    assert thread.type == "public_thread"
    assert thread.thread is not None and thread.thread.archived is False
    assert thread.bot_joined is True
    assert set(Channel.model_fields) >= {"bot_can_view", "thread", "bot_joined"}


def test_unknown_channel_type_maps_to_unknown() -> None:
    model = project.channel({"id": "123456789012345678", "type": 99})
    assert model.type == "unknown" and model.type_id == 99


def test_message_projection_drops_cdn_urls_and_keeps_full_content() -> None:
    raw = load("messages")[0]
    model = project.message(raw)
    dumped = model.model_dump_json()
    assert "cdn.discordapp.com" not in dumped
    assert "media.discordapp.net" not in dumped
    assert model.content == "Release notes are in the thread"
    assert model.reactions is not None
    assert model.reactions[1].emoji == "wave:555555555555555555"
    assert model.reactions[0].emoji == "✅"
    assert model.poll is not None and model.poll.answers[0].votes == 9
    assert model.sticker_names == ["Party"]
    assert model.reply_to_message_id == "1212121212121212121"
    assert model.author is not None and model.author.id == "121212121212121212"
    assert model.mentions[0].username == "bob"


def test_message_projection_handles_missing_optional_fields() -> None:
    model = project.message({"id": "123456789012345678", "channel_id": "1"})
    assert model.author is None and model.poll is None and model.reactions == []
    assert model.thread_id is None and model.timestamp is None


def test_poll_without_results_has_null_votes() -> None:
    raw = load("messages")[0]
    raw["poll"].pop("results")
    model = project.message(raw)
    assert model.poll is not None
    assert model.poll.is_finalized is None
    assert all(answer.votes is None for answer in model.poll.answers)


def test_message_projection_snowflakes_are_strings() -> None:
    model = project.message({"id": 123456789012345678, "channel_id": 123456789012345679})
    assert isinstance(model.id, str) and isinstance(model.channel_id, str)
    assert set(Message.model_fields) >= {"reactions", "poll", "mention_role_ids"}


def test_member_and_audit_projections() -> None:
    member = project.member(load("members")[0])
    assert member.nick == "Ali" and member.role_ids == [MODS]
    entries = load("audit_log")
    entry = project.audit_entry(entries["audit_log_entries"][0], entries["users"])
    assert entry.action == "THREAD_CREATE"
    assert entry.user_name == "alice"
    unknown = project.audit_entry(entries["audit_log_entries"][1], entries["users"])
    assert unknown.action == "ROLE_UPDATE" and unknown.user_name is None
    assert project.audit_entry({"id": "123456789012345678", "action_type": 9999}, []).action is None


def test_user_summary_and_pin_projection() -> None:
    assert project.user_summary({"id": ALICE, "bot": True}) is not None
    assert project.user_summary(None) is None
    pinned = project.pin(load("pins")["items"][0])
    assert pinned.pinned_at == "2026-10-19T09:00:00.000Z"
    assert pinned.message.content == "pinned text"


def test_color_hex_and_channel_type_names() -> None:
    assert project.color_hex(0) is None
    assert project.color_hex(16711680) == "#ff0000"
    assert project.channel_type_name(15) == "forum"
    assert project.channel_type_name(77) == "unknown"


def test_boot_probe_flags_defaults() -> None:
    probe = BootProbe("ok", bot_user_id=BOT, flags=1)
    assert probe.application_id is None
