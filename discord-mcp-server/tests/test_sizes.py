"""A1-R43: worst-case projected size per tool at its maximum limit, asserted under 4 MiB.

D11 (results are complete pages, not truncated) is a deliberate departure from the sibling servers'
per-tool size budgets. This test builds the largest plausible upstream payload for each tool and
measures the projected result. Run ``pytest -s tests/test_sizes.py`` to print the measured sizes,
which are recorded in ``docs/specs/initial-build/implementation-report.md``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from discord_mcp.config import Settings
from discord_mcp.discord_client import DiscordClient
from discord_mcp.service import DiscordService
from tests.conftest import FakeClock, FakeDiscord, make_settings

GUILD = "333333333333333333"
TEXT = "999999999999999999"
THREAD = "171717171717171717"
CAP = 4 * 1024 * 1024
LONG = "x" * 200


def snow(base: int, i: int) -> str:
    return str(base + i)


def worst_user(i: int) -> dict[str, Any]:
    return {
        "id": snow(120000000000000000, i),
        "username": LONG[:32],
        "global_name": LONG[:32],
        "bot": False,
    }


def worst_message(i: int, channel: str = TEXT) -> dict[str, Any]:
    return {
        "id": snow(1234567890123456000, i),
        "channel_id": channel,
        "author": worst_user(i),
        "content": "c" * 4000,
        "timestamp": "2026-10-20T12:00:00.000000+00:00",
        "edited_timestamp": "2026-10-20T12:00:00.000000+00:00",
        "type": 0,
        "pinned": False,
        "mention_everyone": False,
        "mentions": [worst_user(1000 + k) for k in range(100)],
        "mention_roles": ["666666666666666666"] * 25,
        "attachments": [
            {
                "id": snow(141000000000000000, k),
                "filename": "f" * 100,
                "content_type": "image/png",
                "size": 1,
                "width": 1,
                "height": 1,
                "url": "https://cdn.discordapp.com/" + "u" * 200,
            }
            for k in range(10)
        ],
        "embeds": [
            {
                "type": "rich",
                "title": "t" * 256,
                "description": "d" * 1500,
                "url": "https://example.com/" + "u" * 200,
                "fields": [
                    {"name": "n" * 256, "value": "v" * 1024, "inline": True} for _ in range(3)
                ],
            }
            for _ in range(1)
        ],
        "reactions": [
            {"count": 1, "me": False, "emoji": {"id": None, "name": "x"}} for _ in range(20)
        ],
        "message_reference": {"message_id": snow(1212121212121212000, i)},
        "sticker_items": [{"id": "161616161616161616", "name": "s" * 30, "format_type": 1}],
        "poll": {
            "question": {"text": "q" * 300},
            "answers": [{"answer_id": k, "poll_media": {"text": "a" * 55}} for k in range(10)],
            "results": {
                "is_finalized": True,
                "answer_counts": [{"id": k, "count": 5} for k in range(10)],
            },
        },
        "flags": 0,
    }


def worst_channel(cid: str, guild: str = GUILD, parent: str | None = None) -> dict[str, Any]:
    return {
        "id": cid,
        "type": 0,
        "guild_id": guild,
        "name": "n" * 100,
        "position": 1,
        "parent_id": parent,
        "topic": "t" * 1024,
        "nsfw": False,
        "rate_limit_per_user": 0,
        "last_message_id": "1234567890123456789",
        "permission_overwrites": [
            {"id": snow(150000000000000000, k), "type": 0, "allow": "0", "deny": "0"}
            for k in range(10)
        ],
    }


EVERYONE_ROLE: dict[str, Any] = {
    "id": GUILD,
    "name": "@everyone",
    "position": 0,
    "color": 0,
    "permissions": "66688",
    "managed": False,
    "hoist": False,
    "mentionable": False,
}


def worst_guild() -> dict[str, Any]:
    return {
        "id": GUILD,
        "name": "g" * 100,
        "description": "d" * 300,
        "owner_id": "444444444444444444",
        "approximate_member_count": 1,
        "approximate_presence_count": 1,
        "verification_level": 1,
        "premium_tier": 0,
        "premium_subscription_count": 0,
        "preferred_locale": "en-US",
        "features": ["F" * 40] * 20,
        "emojis": [{"id": snow(155000000000000000, k), "name": "e"} for k in range(50)],
        "roles": [EVERYONE_ROLE]
        + [
            {
                "id": snow(160000000000000000, k),
                "name": "r" * 100,
                "position": k,
                "color": 16777215,
                "permissions": "2147483647",
                "managed": False,
                "hoist": True,
                "mentionable": True,
            }
            for k in range(250)
        ],
    }


def worst_member(i: int) -> dict[str, Any]:
    return {
        "user": worst_user(i),
        "nick": "n" * 32,
        "roles": [snow(160000000000000000, k) for k in range(50)],
        "joined_at": "2025-06-01T10:00:00.000000+00:00",
        "premium_since": None,
        "communication_disabled_until": None,
        "pending": False,
    }


def worst_audit(i: int) -> dict[str, Any]:
    return {
        "id": snow(190000000000000000, i),
        "user_id": ALICE_ID,
        "target_id": THREAD,
        "action_type": 110,
        "reason": "r" * 512,
        "changes": [
            {"key": f"k{k}", "old_value": "o" * 500, "new_value": "n" * 500} for k in range(20)
        ],
        "options": {f"opt{k}": "v" * 100 for k in range(10)},
    }


ALICE_ID = "121212121212121212"


def measure(model: Any) -> tuple[int, int]:
    structured = json.dumps(
        model.model_dump(mode="json", exclude_unset=True), separators=(",", ":")
    )
    return len(structured.encode()), 2 * len(structured.encode())


@pytest.fixture
def big_fake(fake: FakeDiscord) -> FakeDiscord:
    fake.add("GET", r".*/guilds/333333333333333333$", fake.json(worst_guild()), times=None)
    fake.add(
        "GET",
        r".*/guilds/333333333333333333/channels",
        fake.json([worst_channel(snow(170000000000000000, k), parent=None) for k in range(500)]),
        times=None,
    )
    fake.add("GET", rf".*/channels/{TEXT}$", fake.json(worst_channel(TEXT)), times=None)
    fake.add(
        "GET",
        rf".*/channels/{TEXT}/messages$",
        fake.json([worst_message(k) for k in range(100)]),
        times=None,
    )
    fake.add("GET", rf".*/channels/{TEXT}/messages/\d+$", fake.json(worst_message(0)), times=None)
    fake.add(
        "GET",
        rf".*/channels/{TEXT}/messages/\d+/reactions/.+",
        fake.json([worst_user(k) for k in range(100)]),
        times=None,
    )
    fake.add(
        "GET",
        rf".*/channels/{TEXT}/messages/pins",
        fake.json(
            {
                "items": [
                    {"pinned_at": "2026-10-19T09:00:00.000000+00:00", "message": worst_message(k)}
                    for k in range(50)
                ],
                "has_more": True,
            }
        ),
        times=None,
    )
    fake.add(
        "GET",
        rf".*/guilds/{GUILD}/messages/search",
        fake.json(
            {
                "total_results": 999,
                "messages": [[{**worst_message(k), "hit": True}] for k in range(25)],
            }
        ),
        times=None,
    )
    fake.add(
        "GET",
        rf".*/guilds/{GUILD}/threads/active",
        fake.json(
            {
                "threads": [
                    {
                        **worst_channel(snow(180000000000000000, k), parent=TEXT),
                        "type": 11,
                        "thread_metadata": {
                            "archived": False,
                            "auto_archive_duration": 1440,
                            "archive_timestamp": "2026-10-28T10:00:00.000000+00:00",
                        },
                    }
                    for k in range(500)
                ],
                "members": [
                    {"id": snow(180000000000000000, k), "user_id": "111111111111111111"}
                    for k in range(500)
                ],
            }
        ),
        times=None,
    )
    fake.add(
        "GET",
        rf".*/guilds/{GUILD}/members$",
        fake.json([worst_member(k) for k in range(1000)]),
        times=None,
    )
    fake.add(
        "GET",
        rf".*/guilds/{GUILD}/members/search",
        fake.json([worst_member(k) for k in range(1000)]),
        times=None,
    )
    fake.add(
        "GET", rf".*/guilds/{GUILD}/members/{ALICE_ID}$", fake.json(worst_member(0)), times=None
    )
    fake.add(
        "GET",
        rf".*/guilds/{GUILD}/audit-logs",
        fake.json(
            {
                "audit_log_entries": [worst_audit(k) for k in range(100)],
                "users": [worst_user(k) for k in range(100)],
            }
        ),
        times=None,
    )
    guilds = [
        {
            "id": snow(300000000000000000, k),
            "name": "g" * 100,
            "owner": False,
            "permissions": "0",
            "features": ["F"] * 10,
            "approximate_member_count": 1,
            "approximate_presence_count": 1,
        }
        for k in range(99)
    ]
    guilds.append(
        {"id": GUILD, "name": "g" * 100, "owner": True, "permissions": "0", "features": []}
    )
    fake.add("GET", r".*/users/@me/guilds", fake.json(guilds), times=None)
    return fake


ALLOWLIST = ",".join([GUILD] + [snow(300000000000000000, k) for k in range(99)])


@pytest.mark.parametrize(
    "tool",
    [
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
    ],
)
async def test_worst_case_projection_is_under_four_mebibytes(
    tool: str, big_fake: FakeDiscord, fclock: FakeClock
) -> None:
    settings: Settings = make_settings(
        discord_allowed_guild_ids=ALLOWLIST, discord_max_response_bytes=4 * 1024 * 1024
    )
    client = DiscordClient(
        settings,
        transport=big_fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )
    service = DiscordService(settings, client, now=fclock.wall, sleep=fclock.sleep)
    call: Any
    if tool == "list_guilds":
        call = service.list_guilds()
    elif tool == "get_guild":
        call = service.get_guild(GUILD)
    elif tool == "list_channels":
        call = service.list_channels(GUILD)
    elif tool == "get_channel":
        call = service.get_channel(TEXT)
    elif tool == "list_messages":
        call = service.list_messages(TEXT, limit=100)
    elif tool == "get_message":
        call = service.get_message(TEXT, snow(1234567890123456000, 0))
    elif tool == "search_messages":
        call = service.search_messages(GUILD, limit=25)
    elif tool == "list_reactions":
        call = service.list_reactions(TEXT, snow(1234567890123456000, 0), "✅", limit=100)
    elif tool == "list_pins":
        call = service.list_pins(TEXT, limit=50)
    elif tool == "list_threads":
        call = service.list_threads(GUILD)
    elif tool == "list_members":
        call = service.list_members(GUILD, limit=1000)
    elif tool == "get_member":
        call = service.get_member(GUILD, ALICE_ID)
    elif tool == "search_members":
        call = service.search_members(GUILD, "al", limit=1000)
    else:
        call = service.get_audit_log(GUILD, limit=100)
    model = await call
    structured, text = measure(model)
    print(f"SIZE tool={tool} structured_bytes={structured} text_plus_structured_bytes={text}")
    assert structured < CAP
