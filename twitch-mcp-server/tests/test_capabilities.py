"""R35–R37: fixed tool sets, taxonomy, and the registration plan as a pure function."""

from __future__ import annotations

from tests.twitch_fakes import make_settings
from twitch_mcp.capabilities import (
    ACCOUNT_TOOLS,
    ALL_TOOLS,
    ALL_WRITE_TOOLS,
    DEFAULT_REGISTERED,
    MAX_TOOL_COUNT,
    READ_TOOLS,
    TOOL_TOKEN_TYPES,
    WRITE_GROUPS,
    WRITE_TOOLS,
    compute_tool_plan,
    group_of,
    token_requirement,
)

ALL_GROUPS = "channel,polls,predictions,chat,moderation,roles"


def test_read_tools_count_and_order() -> None:
    assert len(READ_TOOLS) == 23
    assert len(set(READ_TOOLS)) == 23
    assert READ_TOOLS[0] == "twitch_account_status"
    assert READ_TOOLS[1] == "get_users"


def test_account_tools_are_exactly_two() -> None:
    assert ACCOUNT_TOOLS == ("connect_twitch_account", "disconnect_twitch_account")


def test_write_groups_are_six_and_hold_fifteen_writes() -> None:
    assert WRITE_GROUPS == ("channel", "polls", "predictions", "chat", "moderation", "roles")
    assert len(ALL_WRITE_TOOLS) == 16
    assert len(set(ALL_WRITE_TOOLS)) == 16
    assert sum(len(t) for t in WRITE_TOOLS.values()) == 16
    for group in WRITE_GROUPS:
        assert WRITE_TOOLS[group]
        for name in WRITE_TOOLS[group]:
            assert group_of(name) == group
    assert group_of("get_users") is None


def test_all_tools_and_max_count() -> None:
    assert ALL_TOOLS == READ_TOOLS + ACCOUNT_TOOLS + ALL_WRITE_TOOLS
    assert len(ALL_TOOLS) == len(set(ALL_TOOLS)) == 41  # 23 reads + 2 account + 16 writes
    # SPEC-GAP (R37 / §6.4): the prose says "Writes (15)" and "Maximum 40", but the §6.4 table
    # enumerates 16 writes (roles has 4: add_vip/remove_vip/add_moderator/remove_moderator).
    # Amendment 1 (2026-10-30, approved by: human): the table wins -- 16 writes, 41 total.
    assert MAX_TOOL_COUNT == 41  # Amendment 1 (D-1): 16 writes / 41 total per the table


def test_token_requirements_match_the_spec_table() -> None:
    assert token_requirement("get_stream_markers") == "user"
    assert token_requirement("get_broadcaster_subscribers") == "user"
    assert token_requirement("get_users") == "app"
    assert token_requirement("create_poll") == "user"  # every write acts as the operator
    assert len([n for n in READ_TOOLS if TOOL_TOKEN_TYPES.get(n) == "user"]) == 7


# --------------------------------------------------------------------------- R36/R37 plan


def test_default_boot_registers_only_app_reads() -> None:
    """R28/R36: writes off, user OAuth off → the sixteen app-token reads, nothing else."""
    plan = compute_tool_plan(make_settings(), has_user_token=False)
    assert plan.registered == DEFAULT_REGISTERED
    assert len(plan.registered) == 16
    assert all(name in READ_TOOLS for name in plan.registered)
    assert set(ALL_WRITE_TOOLS).isdisjoint(plan.registered)


def test_user_reads_and_account_tools_need_the_user_token() -> None:
    plan = compute_tool_plan(make_settings(), has_user_token=False)
    for name in READ_TOOLS:
        if TOOL_TOKEN_TYPES.get(name) == "user":
            assert (name, "token_absent") in plan.omitted
    for name in ACCOUNT_TOOLS:
        assert (name, "token_absent") in plan.omitted

    connected = compute_tool_plan(make_settings(twitch_user_oauth=True), has_user_token=True)
    assert set(ACCOUNT_TOOLS).issubset(set(connected.registered))
    for name in READ_TOOLS:
        if TOOL_TOKEN_TYPES.get(name) == "user":
            assert name in connected.registered
    assert connected.user_oauth == "on(connected)"


def test_user_oauth_off_never_registers_account_tools_even_when_connected() -> None:
    plan = compute_tool_plan(make_settings(twitch_user_oauth=False), has_user_token=True)
    for name in ACCOUNT_TOOLS:
        assert (name, "token_absent") in plan.omitted
    assert plan.user_oauth == "off"


def test_writes_register_only_for_enabled_groups() -> None:
    settings = make_settings(
        twitch_user_oauth=True, twitch_enable_writes=True, twitch_write_groups="channel,roles"
    )
    plan = compute_tool_plan(settings, has_user_token=True)
    for name in WRITE_TOOLS["channel"] + WRITE_TOOLS["roles"]:
        assert name in plan.registered
    for name in WRITE_TOOLS["polls"] + WRITE_TOOLS["predictions"] + WRITE_TOOLS["chat"]:
        assert (name, "write_group_disabled") in plan.omitted
    assert plan.writes_enabled is True
    assert set(plan.enabled_write_groups) == {"channel", "roles"}
    # Order (R35): reads, account, then writes in WRITE_GROUPS order.
    order = list(plan.registered)
    assert order.index(WRITE_TOOLS["channel"][0]) < order.index(WRITE_TOOLS["roles"][0])


def test_all_groups_enabled_registers_everything() -> None:
    settings = make_settings(
        twitch_user_oauth=True, twitch_enable_writes=True, twitch_write_groups=ALL_GROUPS
    )
    plan = compute_tool_plan(settings, has_user_token=True)
    assert set(ALL_WRITE_TOOLS).issubset(set(plan.registered))
    assert plan.registered == ALL_TOOLS  # every tool, in §6.4 order
    assert plan.tool_count == len(ALL_TOOLS)  # 23 reads + 2 account + 16 writes = 41
    # SPEC-GAP escalated to the human: SPEC §6.4's table enumerates 16 write tools while its
    # Amendment 1 resolved this: the table's 16 writes / 41 total are authoritative.


def test_writes_enabled_but_group_list_empty_omits_every_write() -> None:
    settings = make_settings(twitch_user_oauth=True, twitch_enable_writes=True)
    plan = compute_tool_plan(settings, has_user_token=True)
    assert not set(ALL_WRITE_TOOLS).intersection(plan.registered)


def test_writes_off_never_registers_writes_even_with_groups_listed() -> None:
    settings = make_settings(twitch_user_oauth=True, twitch_write_groups=ALL_GROUPS)
    plan = compute_tool_plan(settings, has_user_token=True)
    assert not set(ALL_WRITE_TOOLS).intersection(plan.registered)
    assert plan.writes_enabled is False


def test_explicit_disable_omits_any_tool_by_name() -> None:
    """R36: TWITCH_DISABLE_<TOOL>=true is the only other way a tool disappears."""
    plan = compute_tool_plan(make_settings(), has_user_token=False, disabled_tools=["get_clips"])
    assert ("get_clips", "explicitly_disabled") in plan.omitted
    assert "get_clips" not in plan.registered
    assert plan.omitted_reason("get_clips") == "explicitly_disabled"


def test_unknown_disable_name_never_adds_a_tool() -> None:
    plan = compute_tool_plan(make_settings(), has_user_token=False, disabled_tools=["not_a_tool"])
    assert plan.registered == DEFAULT_REGISTERED


def test_plan_is_a_pure_function_of_its_inputs() -> None:
    settings = make_settings(twitch_user_oauth=True)
    a = compute_tool_plan(settings, has_user_token=True, disabled_tools=[])
    b = compute_tool_plan(settings, has_user_token=True, disabled_tools=[])
    assert a.registered == b.registered and a.omitted == b.omitted
