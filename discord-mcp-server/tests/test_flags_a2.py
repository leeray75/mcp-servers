"""A2 (SPEC section 17): independent parsing of ``flags`` and ``flags_new``, OR-ed effective flags,
and the boot line's ``flags_source``. Covers every case listed under "A2 tests" in section 17."""

from __future__ import annotations

from typing import Any

import pytest

from discord_mcp.capabilities import GUILD_MEMBERS_BITS, MESSAGE_CONTENT_BITS, plan_tools
from discord_mcp.discord_client import BootProbe, DiscordClient, _application_flags
from discord_mcp.server import _boot_summary
from tests.conftest import FakeClock, FakeDiscord, make_settings

MESSAGE_CONTENT = 1 << 18
GUILD_MEMBERS = 1 << 14
EQUAL_FLAGS = 278528  # message content (1<<18) | guild members (1<<14)
MALFORMED_FLAGS_NEW: list[Any] = [
    "²",  # superscript two: str.isdigit() is true, int() raises
    "-1",
    "1e3",
    "0x10",
    " 12",
    12.0,
    [],
    "1" * 31,
]


def _client(fake: FakeDiscord, fclock: FakeClock) -> DiscordClient:
    return DiscordClient(
        make_settings(),
        transport=fake.transport(),
        clock=fclock.clock,
        sleep=fclock.sleep,
        rand=lambda: 0.0,
    )


async def _probe(fake: FakeDiscord, fclock: FakeClock, application: dict[str, Any]) -> BootProbe:
    fake.application = {"id": "222222222222222222", **application}
    return await _client(fake, fclock).probe()


# ------------------------------------------------------------ the cases in section 17


async def test_flags_only_is_used(fake: FakeDiscord, fclock: FakeClock) -> None:
    probe = await _probe(fake, fclock, {"flags": EQUAL_FLAGS})
    assert probe.outcome == "ok" and probe.flags == EQUAL_FLAGS and probe.flags_source == "flags"


async def test_flags_new_integer_only_is_used(fake: FakeDiscord, fclock: FakeClock) -> None:
    probe = await _probe(fake, fclock, {"flags_new": EQUAL_FLAGS})
    assert probe.flags == EQUAL_FLAGS and probe.flags_source == "flags_new"


async def test_flags_new_decimal_string_only_is_used(fake: FakeDiscord, fclock: FakeClock) -> None:
    probe = await _probe(fake, fclock, {"flags_new": str(EQUAL_FLAGS)})
    assert probe.flags == EQUAL_FLAGS and probe.flags_source == "flags_new"


async def test_both_present_with_disjoint_bits_enables_both_intents(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    probe = await _probe(fake, fclock, {"flags": MESSAGE_CONTENT, "flags_new": str(GUILD_MEMBERS)})
    assert probe.flags == MESSAGE_CONTENT | GUILD_MEMBERS
    assert probe.flags_source == "flags+flags_new"
    plan = plan_tools(make_settings(), probe)
    assert plan.omitted == ()  # OR-ing both fields enables both intents (O2)


async def test_both_present_and_equal(fake: FakeDiscord, fclock: FakeClock) -> None:
    probe = await _probe(fake, fclock, {"flags": EQUAL_FLAGS, "flags_new": str(EQUAL_FLAGS)})
    assert probe.flags == EQUAL_FLAGS and probe.flags_source == "flags+flags_new"


async def test_neither_present_is_unknown(fake: FakeDiscord, fclock: FakeClock) -> None:
    probe = await _probe(fake, fclock, {})
    assert probe.outcome == "ok" and probe.flags is None and probe.flags_source is None
    plan = plan_tools(make_settings(), probe)
    assert dict(plan.omitted) == {
        "search_messages": "flags_missing",
        "list_members": "flags_missing",
    }


# ------------------------------------------------------------ malformed values never raise


@pytest.mark.parametrize("value", MALFORMED_FLAGS_NEW)
async def test_malformed_flags_new_is_ignored_and_probe_completes(
    value: Any, fake: FakeDiscord, fclock: FakeClock
) -> None:
    probe = await _probe(fake, fclock, {"flags_new": value})
    assert probe.outcome == "ok"
    assert probe.flags is None and probe.flags_source is None
    assert dict(plan_tools(make_settings(), probe).omitted) == {
        "search_messages": "flags_missing",
        "list_members": "flags_missing",
    }


@pytest.mark.parametrize("value", MALFORMED_FLAGS_NEW)
async def test_malformed_flags_new_beside_valid_flags_is_ignored(
    value: Any, fake: FakeDiscord, fclock: FakeClock
) -> None:
    probe = await _probe(fake, fclock, {"flags": MESSAGE_CONTENT, "flags_new": value})
    assert probe.flags == MESSAGE_CONTENT and probe.flags_source == "flags"


@pytest.mark.parametrize("value", [-1, True, 12.5, "7", None, [1]])
async def test_negative_boolean_or_unusable_flags_is_ignored(
    value: Any, fake: FakeDiscord, fclock: FakeClock
) -> None:
    probe = await _probe(fake, fclock, {"flags": value})
    assert probe.outcome == "ok" and probe.flags is None and probe.flags_source is None


async def test_unusable_flags_falls_through_to_usable_flags_new(
    fake: FakeDiscord, fclock: FakeClock
) -> None:
    probe = await _probe(fake, fclock, {"flags": -5, "flags_new": str(EQUAL_FLAGS)})
    assert probe.flags == EQUAL_FLAGS and probe.flags_source == "flags_new"


async def test_boolean_flags_new_is_ignored(fake: FakeDiscord, fclock: FakeClock) -> None:
    probe = await _probe(fake, fclock, {"flags": MESSAGE_CONTENT, "flags_new": True})
    assert probe.flags == MESSAGE_CONTENT and probe.flags_source == "flags"


def test_thirty_digit_string_is_usable_and_thirty_one_is_not() -> None:
    assert _application_flags({"flags_new": "1" * 30}) == (int("1" * 30), "flags_new")
    assert _application_flags({"flags_new": "1" * 31}) == (None, None)


# ------------------------------------------------------------ the boot line reports the source


@pytest.mark.parametrize(
    ("application", "expected_flags", "expected_source"),
    [
        ({"flags": EQUAL_FLAGS}, EQUAL_FLAGS, "flags"),
        ({"flags_new": EQUAL_FLAGS}, EQUAL_FLAGS, "flags_new"),
        ({"flags_new": str(EQUAL_FLAGS)}, EQUAL_FLAGS, "flags_new"),
        (
            {"flags": MESSAGE_CONTENT, "flags_new": str(GUILD_MEMBERS)},
            MESSAGE_CONTENT | GUILD_MEMBERS,
            "flags+flags_new",
        ),
        ({"flags": EQUAL_FLAGS, "flags_new": str(EQUAL_FLAGS)}, EQUAL_FLAGS, "flags+flags_new"),
        ({}, None, None),
        ({"flags_new": "²"}, None, None),
    ],
)
async def test_boot_line_reports_the_effective_flags_and_source(
    application: dict[str, Any],
    expected_flags: int | None,
    expected_source: str | None,
    fake: FakeDiscord,
    fclock: FakeClock,
) -> None:
    probe = await _probe(fake, fclock, application)
    line = _boot_summary(plan_tools(make_settings(), probe))
    flags_text = "null" if expected_flags is None else str(expected_flags)
    source_text = "null" if expected_source is None else expected_source
    assert f"application_flags: {flags_text} source={source_text}" in line


def test_boot_line_for_a_forced_tool_with_unknown_flags() -> None:
    plan = plan_tools(
        make_settings(discord_force_guild_members=True),
        BootProbe("ok", flags=None, flags_source=None),
    )
    line = _boot_summary(plan)
    assert "forced: list_members" in line
    assert "application_flags: null source=null" in line


def test_bit_constants_are_the_r6_bits() -> None:
    assert MESSAGE_CONTENT_BITS == (1 << 18) | (1 << 19)
    assert GUILD_MEMBERS_BITS == (1 << 14) | (1 << 15)
