"""TwitchService (SPEC §6.1, §6.3, §6.4): one method per tool, in the spec's order.

What is proven here, requirement by requirement:

* **R25** — every tool that takes a broadcaster id (plus the ``user_id``/``from_id``/``to_id``
  forms) refuses a non-allowlisted id with ``kind=forbidden_channel`` and **zero requests**.
  Each such tool has its own parametrized case asserting ``harness.requests == 0``.
* **R26/R27** — an owned object costs exactly one cached owner lookup and nothing else, and every
  lookup failure returns byte-identical denial text.
* **R28–R34** — each of the 15 write tools: group off → ``disabled``; no token →
  ``confirmation_required`` with 0 requests; valid token → exactly 1 request; replay, argument
  drift, broadcaster drift, tool drift, wrong-tool token and expiry → ``stale_confirmation`` with
  0 requests; ``clear_confirmations``/``on_identity_change`` invalidate outstanding tokens (R33).
* **R38** — results dump with ``exclude_unset``, so a key the projection never set is absent rather
  than ``null``.
* **R39/R40** — ``first`` is bounded per endpoint, mutually exclusive cursors are refused, and
  malformed ids/cursors never reach a URL (R17).
* **R42** — an empty Helix cursor is reported as "no further page"; a real cursor is carried.
* **R45** — an empty list is a success; a missing scope is ``kind=permission``.
* **R52/R53** — a free-text write (chat message, ban reason, automod term) never appears in any
  log record, while the write's audit line still carries broadcaster id, tool and outcome.

Every collaborator is real (client, limiter, cache, identity, confirmation store); only the
transport, the clock, the owner lookup and the credential sources are faked — see
:mod:`tests.service_fakes`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from tests.service_fakes import (
    ALL_WRITE_GROUPS,
    APP_BEARER,
    USER_BEARER,
    ServiceHarness,
    envelope,
    make_service,
)
from tests.twitch_fakes import (
    BROADCASTER,
    GAME_ID,
    MODERATOR_ID,
    OPERATOR,
    OTHER_BROADCASTER,
    VIDEO_ID,
    FakeClock,
    FakeTwitch,
    make_settings,
)
from twitch_mcp.confirmation import STALE_MESSAGE
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.guard import BROADCASTER_REFUSAL, OWNER_REFUSAL
from twitch_mcp.identity import NOT_AUTHORIZED_MESSAGE
from twitch_mcp.project import has_more
from twitch_mcp.service import DISABLED_WRITE_MESSAGE, MAX_FIRST, TwitchService

POLL_ID_LONG = "poll-00001"
PREDICTION_ID = "pred-00001"
NOT_ALLOWLISTED = OTHER_BROADCASTER
#: A channel that is on neither the guard allowlist nor any roster: R25 writes aim here.
OUTSIDE_ALLOWLIST = "777777777"
CHAT_TEXT = "gl hf everyone"
BAN_REASON = "posting nonsense links"
AUTOMOD_TERM = "absolute-nonsense-term"

#: Every tool that takes a broadcaster/user/from/to id, with the kwarg name the guard checks and a
#: callable that invokes the tool with that id. R25 gets one test case per entry.
GUARDED_TOOLS: list[tuple[str, str, Callable[[TwitchService, str], Any]]] = [
    ("get_channels", "broadcaster_ids", lambda s, i: s.get_channels(broadcaster_ids=[i])),
    ("get_polls", "broadcaster_id", lambda s, i: s.get_polls(broadcaster_id=i)),
    ("get_predictions", "broadcaster_id", lambda s, i: s.get_predictions(broadcaster_id=i)),
    (
        "get_channel_chat_settings",
        "broadcaster_ids",
        lambda s, i: s.get_channel_chat_settings(broadcaster_ids=[i]),
    ),
    (
        "get_channel_chat_badges",
        "broadcaster_id",
        lambda s, i: s.get_channel_chat_badges(broadcaster_id=i),
    ),
    ("get_stream_markers", "broadcaster_id", lambda s, i: s.get_stream_markers(broadcaster_id=i)),
    (
        "get_broadcaster_subscribers",
        "broadcaster_id",
        lambda s, i: s.get_broadcaster_subscribers(broadcaster_id=i),
    ),
    ("get_channel_editors", "broadcaster_id", lambda s, i: s.get_channel_editors(broadcaster_id=i)),
    ("get_moderators", "broadcaster_id", lambda s, i: s.get_moderators(broadcaster_id=i)),
    ("get_banned_users", "broadcaster_id", lambda s, i: s.get_banned_users(broadcaster_id=i)),
    (
        "get_automod_settings",
        "broadcaster_id",
        lambda s, i: s.get_automod_settings(broadcaster_id=i),
    ),
    ("get_chat_chatters", "broadcaster_id", lambda s, i: s.get_chat_chatters(broadcaster_id=i)),
    ("get_schedule", "broadcaster_id", lambda s, i: s.get_schedule(broadcaster_id=i)),
    ("get_users", "id", lambda s, i: s.get_users(ids=[i])),
    ("get_streams", "user_ids", lambda s, i: s.get_streams(user_ids=[i])),
    ("get_videos", "user_id", lambda s, i: s.get_videos(user_id=i)),
    ("get_clips", "broadcaster_id", lambda s, i: s.get_clips(broadcaster_id=i)),
    ("get_emotes", "broadcaster_id", lambda s, i: s.get_emotes(broadcaster_id=i)),
    ("get_users_follows", "from_id", lambda s, i: s.get_users_follows(from_id=i)),
    ("get_users_follows", "to_id", lambda s, i: s.get_users_follows(to_id=i)),
]

#: The 15 writes of SPEC §6.4 with their group, a valid argument set, the Helix path the single
#: dispatch must hit and the verb.
WRITE_CASES: list[tuple[str, str, dict[str, Any], str, str]] = [
    (
        "update_channel",
        "channel",
        {"title": "Live now", "game_id": GAME_ID},
        "/helix/channels",
        "PATCH",
    ),
    (
        "create_poll",
        "polls",
        {"title": "Best map?", "choices": ["Alpha", "Beta"]},
        "/helix/polls",
        "POST",
    ),
    (
        "end_poll",
        "polls",
        {"poll_id": POLL_ID_LONG, "status": "TERMINATED"},
        "/helix/polls",
        "PATCH",
    ),
    (
        "create_prediction",
        "predictions",
        {"title": "Who wins?", "outcomes": ["Blue", "Red"]},
        "/helix/predictions",
        "POST",
    ),
    (
        "end_prediction",
        "predictions",
        {"prediction_id": PREDICTION_ID, "status": "CANCELLED"},
        "/helix/predictions",
        "PATCH",
    ),
    ("send_chat_message", "chat", {"message": CHAT_TEXT}, "/helix/chat/messages", "POST"),
    (
        "timeout_user",
        "moderation",
        {"user_id": MODERATOR_ID, "duration": 600},
        "/helix/moderation/timeouts",
        "POST",
    ),
    (
        "remove_timeout",
        "moderation",
        {"user_id": MODERATOR_ID},
        "/helix/moderation/timeouts",
        "DELETE",
    ),
    (
        "ban_user",
        "moderation",
        {"user_id": MODERATOR_ID, "reason": BAN_REASON},
        "/helix/moderation/banned",
        "POST",
    ),
    ("unban_user", "moderation", {"user_id": MODERATOR_ID}, "/helix/moderation/banned", "DELETE"),
    (
        "set_automod_term",
        "moderation",
        {"text": AUTOMOD_TERM, "category": "harassment"},
        "/helix/moderation/automod/terms",
        "PUT",
    ),
    (
        "remove_automod_term",
        "moderation",
        {"text": AUTOMOD_TERM},
        "/helix/moderation/automod/terms",
        "DELETE",
    ),
    ("add_vip", "roles", {"user_id": MODERATOR_ID}, "/helix/channels/vips", "POST"),
    ("remove_vip", "roles", {"user_id": MODERATOR_ID}, "/helix/channels/vips", "DELETE"),
    ("add_moderator", "roles", {"user_id": MODERATOR_ID}, "/helix/moderation/moderators", "POST"),
    (
        "remove_moderator",
        "roles",
        {"user_id": MODERATOR_ID},
        "/helix/moderation/moderators",
        "DELETE",
    ),
]


def harness(fake: FakeTwitch, fclock: FakeClock, **kwargs: Any) -> ServiceHarness:
    """A service whose guard refuses everything but ``BROADCASTER`` and whose transport is faked."""
    kwargs.setdefault("allowed", frozenset({BROADCASTER}))
    return make_service(fake, fclock, **kwargs)


def write_harness(
    fake: FakeTwitch,
    fclock: FakeClock,
    *,
    moderators: dict[str, Sequence[str]] | None = None,
    editors: dict[str, Sequence[str]] | None = None,
    **kwargs: Any,
) -> ServiceHarness:
    """A service with both R28 kill switches thrown, user OAuth on and the account connected."""
    kwargs.setdefault("allowed", frozenset({BROADCASTER, NOT_ALLOWLISTED}))
    kwargs.setdefault("writes_on", True)
    kwargs.setdefault("user_oauth", True)
    kwargs.setdefault("user_connected", True)
    kwargs.setdefault("moderators", moderators or {BROADCASTER: [OPERATOR]})
    kwargs.setdefault("editors", editors or {BROADCASTER: [OPERATOR]})
    # The operator is verified only on BROADCASTER: every other channel must die at the guard,
    # proving it runs before identity (SPEC §6.3), not merely that someone refused.
    return make_service(fake, fclock, **kwargs)


async def dispatch(
    h: ServiceHarness,
    tool: str,
    broadcaster: str,
    arguments: dict[str, Any],
    *,
    confirm: str | None,
) -> Any:
    """Call one write tool by name with a broadcaster id plus its own arguments."""
    method = getattr(h.service, tool)
    return await method(broadcaster_id=broadcaster, confirm=confirm, **arguments)


async def mint(h: ServiceHarness, tool: str, broadcaster: str, arguments: dict[str, Any]) -> str:
    """First turn of a write: returns the single-use confirm token."""
    result = await dispatch(h, tool, broadcaster, arguments, confirm=None)
    assert result.confirmation_required is True
    assert result.confirmation is not None and result.confirmation.token
    return str(result.confirmation.token)


def _drifted(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """The same tool call with one bound argument changed after the token was minted (R32)."""
    drifted = dict(arguments)
    if tool == "update_channel" or tool in {"create_poll", "create_prediction"}:
        drifted["title"] = "changed after minting"
    elif tool == "end_poll":
        drifted["status"] = "ARCHIVED"
    elif tool == "end_prediction":
        drifted["status"] = "RESOLVED"
        drifted["winning_outcome_id"] = "outcome-1"
    elif tool == "send_chat_message":
        drifted["message"] = CHAT_TEXT + "!"
    elif tool == "timeout_user":
        drifted["duration"] = 300
    elif tool == "ban_user":
        drifted["reason"] = "a different reason"
    elif tool == "set_automod_term":
        drifted["category"] = "profanity"
    elif tool == "remove_automod_term":
        drifted["text"] = "a-different-term"
    else:  # the target-user writes
        drifted["user_id"] = OPERATOR
    return drifted


def forbid(fake: FakeTwitch, method: str, path: str, body: dict[str, Any], status: int) -> None:
    """Make one endpoint answer with a fixed (error) body."""
    fake.add(method, path, fake.json(body, status=status))


# --------------------------------------------------------------------------- construction (R6/R11)


def test_service_takes_every_collaborator_by_injection(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R6/R11: the service holds no singleton and imports neither ``os`` nor ``httpx``."""
    import inspect

    import twitch_mcp.service as service_module

    h = harness(fake, fclock)
    assert isinstance(h.service, TwitchService)
    assert h.service.settings is h.settings
    assert h.service.limiter is h.limiter
    assert h.service.identity_verifier is h.identity
    assert h.service.confirmations is h.confirmations
    source = inspect.getsource(service_module)
    assert "import os" not in source and "httpx" not in source


def test_tool_plan_reflects_settings_and_connection(fake: FakeTwitch, fclock: FakeClock) -> None:
    """The plan ``server.py`` registers from is the same pure decision ``/healthz`` reports (R35)."""
    h = harness(fake, fclock, user_oauth=True, user_connected=True)
    plan = h.service.tool_plan()
    assert (
        plan.tool_count == 25
    )  # 23 reads + 2 account tools (user OAuth on, connected), writes off
    h2 = write_harness(fake, fclock)
    assert h2.service.tool_plan().enabled_write_groups == tuple(
        ALL_WRITE_GROUPS.split(",")
    )  # spec order (R35)


# --------------------------------------------------------------------------- R25: zero requests


@pytest.mark.parametrize(
    ("tool", "field", "call"),
    GUARDED_TOOLS,
    ids=[f"{tool}:{field}" for tool, field, _ in GUARDED_TOOLS],
)
async def test_r25_non_allowlisted_id_costs_zero_requests(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    field: str,
    call: Callable[[TwitchService, str], Any],
) -> None:
    """R25: every id-bearing read is guarded before any request; the count must be exactly 0."""
    h = harness(fake, fclock, user_oauth=True, user_connected=True)
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await call(h.service, NOT_ALLOWLISTED)
    assert exc.value.kind == "forbidden_channel"
    assert exc.value.message == BROADCASTER_REFUSAL
    assert h.requests == 0
    assert h.counters["app_token_calls"] == 0  # not even a credential was fetched


@pytest.mark.parametrize(
    ("tool", "group", "arguments", "path", "verb"),
    WRITE_CASES,
    ids=[case[0] for case in WRITE_CASES],
)
async def test_r25_non_allowlisted_id_costs_zero_requests_for_every_write(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R25 + R31: a write on a non-allowlisted channel never reaches the network or the mint."""
    h = write_harness(
        fake, fclock, moderators={BROADCASTER: [OPERATOR]}, editors={BROADCASTER: [OPERATOR]}
    )
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, tool, OUTSIDE_ALLOWLIST, arguments, confirm=None)
    assert exc.value.kind == "forbidden_channel"
    assert exc.value.message == BROADCASTER_REFUSAL
    assert h.requests == 0
    assert len(h.confirmations) == 0


async def test_r25_malformed_id_is_refused_without_a_request(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R25/R17: a shapeless id cannot reach a URL segment either."""
    h = harness(fake, fclock)
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_polls(broadcaster_id="../../admin")
    assert exc.value.kind == "forbidden_channel"
    assert h.requests == 0


# --------------------------------------------------------------------------- R26/R27: owner lookups


async def test_r26_video_id_costs_exactly_one_cached_lookup(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R26: one cached owner lookup learns the broadcaster, and nothing else beyond the tool call."""
    h = harness(fake, fclock, owners={VIDEO_ID: {"id": VIDEO_ID, "user_id": BROADCASTER}})
    fake.add(
        "GET",
        r"/helix/videos",
        fake.json(envelope([{"id": VIDEO_ID, "user_id": BROADCASTER, "title": "A video"}])),
    )
    first = await h.service.get_videos(video_ids=[VIDEO_ID])
    second = await h.service.get_videos(video_ids=[VIDEO_ID])
    assert first.videos and second.videos
    assert h.counters["owner_calls"] == 1  # the lookup, once
    assert h.requests == 2  # the two tool calls; the lookup rode the first one


async def test_r26_clip_id_is_resolved_through_the_owner_lookup(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R26: an owned object resolves through exactly one cached lookup."""
    numeric_clip = "333000333"  # R17: owner resolution only accepts user-shaped (digit) ids
    h = harness(
        fake, fclock, owners={numeric_clip: {"id": numeric_clip, "broadcaster_id": BROADCASTER}}
    )
    fake.add(
        "GET",
        r"/helix/clips",
        fake.json(
            envelope([{"id": numeric_clip, "broadcaster_id": BROADCASTER, "title": "A clip"}])
        ),
    )
    result = await h.service.get_clips(clip_ids=[numeric_clip])
    assert result.clips and result.clips[0].id == numeric_clip
    assert h.counters["owner_calls"] == 1


async def test_r26_lookup_happens_before_the_request_it_gates(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R26: an owner outside the allowlist refuses the tool call itself, not just the lookup."""
    h = harness(fake, fclock, owners={VIDEO_ID: {"id": VIDEO_ID, "user_id": NOT_ALLOWLISTED}})
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_videos(video_ids=[VIDEO_ID])
    assert exc.value.kind == "forbidden_channel"
    assert exc.value.message == OWNER_REFUSAL
    assert h.requests == 0


@pytest.mark.parametrize(
    "error",
    [
        TwitchMcpError("upstream", "no such video", http_status=404, code=10000),
        TwitchMcpError("upstream", "denied", http_status=403, code=10000),
        RuntimeError("the lookup crashed"),
    ],
    ids=["404", "403", "crash"],
)
async def test_r27_every_lookup_failure_is_the_identical_denial(
    fake: FakeTwitch, fclock: FakeClock, error: Exception
) -> None:
    """R27: the response cannot probe allowlist membership — one message for every failure."""
    h = harness(fake, fclock, owner_errors={VIDEO_ID: error})
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_videos(video_ids=[VIDEO_ID])
    assert exc.value.message == OWNER_REFUSAL
    assert exc.value.kind == "forbidden_channel"
    assert h.requests == 0


async def test_r27_missing_owner_entry_is_the_same_denial(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R27: an id with no owner fixture is indistinguishable from a refused lookup."""
    h = harness(fake, fclock, owners={})
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_videos(video_ids=[VIDEO_ID])
    assert exc.value.message == OWNER_REFUSAL
    assert h.requests == 0


async def test_r27_outage_surfaces_as_itself_not_as_a_denial(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R27 pass-through: a 5xx during the lookup is not laundered into an allowlist refusal."""
    h = harness(
        fake,
        fclock,
        owner_errors={VIDEO_ID: TwitchMcpError("upstream", "twitch is down", http_status=503)},
    )
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_videos(video_ids=[VIDEO_ID])
    assert exc.value.kind == "upstream"
    assert exc.value.message != OWNER_REFUSAL


# --------------------------------------------------------------------------- R28: write switches


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r28_write_is_disabled_when_the_group_is_off(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R28: with both kill switches off, every write refuses with ``disabled`` and zero requests."""
    h = harness(fake, fclock, user_oauth=True, user_connected=True, writes_on=False)
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, tool, BROADCASTER, arguments, confirm=None)
    assert exc.value.kind == "disabled"
    assert exc.value.message == DISABLED_WRITE_MESSAGE
    assert h.requests == 0


async def test_r28_master_switch_alone_is_not_enough(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R28: TWITCH_ENABLE_WRITES alone does not enable a group when TWITCH_WRITE_GROUPS is empty."""
    settings = make_settings(
        twitch_allowed_broadcaster_ids=BROADCASTER,
        twitch_enable_writes=True,
        twitch_write_groups="",
        twitch_user_oauth=True,
    )
    h = harness(fake, fclock, settings=settings, user_oauth=True, user_connected=True)
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "update_channel", BROADCASTER, {"title": "x"}, confirm=None)
    assert exc.value.kind == "disabled"
    assert h.requests == 0


async def test_r28_only_the_enabled_group_is_allowed(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R28: enabling ``polls`` leaves every other group's write disabled, zero requests."""
    settings = make_settings(
        twitch_allowed_broadcaster_ids=",".join([BROADCASTER, NOT_ALLOWLISTED]),
        twitch_enable_writes=True,
        twitch_write_groups="polls",
        twitch_user_oauth=True,
    )
    h = write_harness(fake, fclock, settings=settings)
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "update_channel", BROADCASTER, {"title": "Live now"}, confirm=None)
    assert exc.value.kind == "disabled"
    assert h.requests == 0
    # The enabled group is not disabled — it reaches the confirmation gate instead.
    result = await dispatch(
        h, "create_poll", BROADCASTER, {"title": "Ok?", "choices": ["A", "B"]}, confirm=None
    )
    assert result.confirmation_required is True


# --------------------------------------------------------------------------- R29: write identity


async def test_r29_unverified_operator_writes_zero_requests(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R29: an operator on neither roster is refused before the mint and before any request."""
    h = write_harness(fake, fclock, moderators={NOT_ALLOWLISTED: []}, editors={NOT_ALLOWLISTED: []})
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(
            h, "create_poll", BROADCASTER, {"title": "Ok?", "choices": ["A", "B"]}, confirm=None
        )
    assert exc.value.kind == "not_authorized"
    assert exc.value.message == NOT_AUTHORIZED_MESSAGE
    assert h.requests == 0
    assert len(h.confirmations) == 0


async def test_r29_own_channel_needs_no_roster(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R29: the token user's own channel is writable from the token, never from the roster."""
    h = write_harness(
        fake, fclock, moderators={BROADCASTER: []}, editors={BROADCASTER: []}, operator=BROADCASTER
    )
    result = await dispatch(
        h, "create_poll", BROADCASTER, {"title": "Ok?", "choices": ["A", "B"]}, confirm=None
    )
    assert result.confirmation_required is True
    assert h.requests == 0


async def test_r29_channel_group_requires_the_editor_roster(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R29: ``channel`` checks editors; being merely a moderator is not enough."""
    h = write_harness(fake, fclock, moderators={BROADCASTER: [OPERATOR]}, editors={BROADCASTER: []})
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "update_channel", BROADCASTER, {"title": "x"}, confirm=None)
    assert exc.value.kind == "not_authorized"
    assert h.requests == 0


async def test_r29_moderation_group_requires_the_moderator_roster(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R29: ``moderation`` checks moderators; being merely an editor is not enough."""
    h = write_harness(fake, fclock, moderators={BROADCASTER: []}, editors={BROADCASTER: [OPERATOR]})
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "ban_user", BROADCASTER, {"user_id": MODERATOR_ID}, confirm=None)
    assert exc.value.kind == "not_authorized"
    assert h.requests == 0


# --------------------------------------------------------------------------- R31–R34: write path


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r31_mint_is_a_success_result_with_zero_requests(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R31: the first turn resolves the plan and mints a token — a *successful* result, nothing sent."""
    h = write_harness(fake, fclock)
    fake.strict = True
    result = await dispatch(h, tool, BROADCASTER, arguments, confirm=None)
    assert result.confirmation_required is True
    assert result.dispatched is False
    confirmation = result.confirmation
    assert confirmation is not None
    assert confirmation.token and confirmation.tool == tool
    assert confirmation.broadcaster_id == BROADCASTER
    assert confirmation.expires_at
    assert h.requests == 0


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r31_confirmation_plan_carries_no_free_text(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R31/R52: the plan summarizes free text as its length, never its value."""
    h = write_harness(fake, fclock)
    fake.strict = True
    result = await dispatch(h, tool, BROADCASTER, arguments, confirm=None)
    plan = result.confirmation.arguments or {}
    assert result.confirmation.token
    for value in plan.values():
        assert not isinstance(value, str) or value in {"TERMINATED", "CANCELLED", "harassment"}
    if "message" in arguments:
        assert plan["message"] == {"characters": len(CHAT_TEXT)}
    if "reason" in arguments:
        assert plan["reason"] == {"characters": len(BAN_REASON)}
    if "text" in arguments:
        assert plan["text"] == {"characters": len(AUTOMOD_TERM)}
    if "choices" in arguments:
        assert plan["choices"] == {"count": 2}
    if "outcomes" in arguments:
        assert plan["outcomes"] == {"count": 2}


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r32_valid_token_dispatches_exactly_one_request(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R31/R32 + R16: the second turn performs exactly one request with the right verb."""
    h = write_harness(fake, fclock)
    token = await mint(h, tool, BROADCASTER, arguments)
    h.reset_counts()
    result = await dispatch(h, tool, BROADCASTER, arguments, confirm=token)
    assert result.confirmation_required is False
    assert result.dispatched is True
    assert result.data is not None
    assert h.requests == 1
    assert h.methods == [verb]
    assert h.paths == [path]
    assert USER_BEARER in h.fake.requests[0].headers["Authorization"]
    assert len(h.confirmations) == 0  # single-use: the entry is gone


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r32_replayed_token_is_refused_with_zero_requests(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R32: the same token twice is the identical stale refusal; nothing reaches the network."""
    h = write_harness(fake, fclock)
    token = await mint(h, tool, BROADCASTER, arguments)
    h.reset_counts()
    first = await dispatch(h, tool, BROADCASTER, arguments, confirm=token)
    assert first.dispatched is True
    h.reset_counts()
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, tool, BROADCASTER, arguments, confirm=token)
    assert exc.value.kind == "stale_confirmation"
    assert exc.value.message == STALE_MESSAGE
    assert h.requests == 0


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r32_argument_drift_is_refused_with_zero_requests(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R32: a token is bound to the canonical arguments; any drift is stale, never dispatched."""
    h = write_harness(fake, fclock)
    token = await mint(h, tool, BROADCASTER, arguments)
    h.reset_counts()
    fake.strict = True
    drifted = _drifted(tool, arguments)
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, tool, BROADCASTER, drifted, confirm=token)
    assert exc.value.kind == "stale_confirmation"
    assert exc.value.message == STALE_MESSAGE
    assert h.requests == 0


async def test_r32_broadcaster_drift_is_refused(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R32: a token minted for one channel cannot authorize the same arguments on another."""
    h = write_harness(
        fake, fclock, moderators={BROADCASTER: [OPERATOR], NOT_ALLOWLISTED: [OPERATOR]}
    )
    arguments = {"user_id": MODERATOR_ID, "duration": 600}
    token = await mint(h, "timeout_user", BROADCASTER, arguments)
    h.reset_counts()
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "timeout_user", NOT_ALLOWLISTED, arguments, confirm=token)
    # R32: the token is bound to (tool, args-hash, broadcaster_id) — a drifted broadcaster is
    # stale_confirmation; the guard's own refusal is covered by R25 and R34.
    assert exc.value.kind == "stale_confirmation"
    assert h.requests == 0
    # Inside the allowlist, the token-vs-broadcaster binding is what refuses:
    token2 = await mint(h, "timeout_user", NOT_ALLOWLISTED, arguments)
    h.reset_counts()
    with pytest.raises(TwitchMcpError) as exc2:
        await dispatch(h, "unban_user", NOT_ALLOWLISTED, {"user_id": MODERATOR_ID}, confirm=token2)
    assert exc2.value.kind == "stale_confirmation"


async def test_r32_expired_token_is_refused_with_zero_requests(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R32: past the TTL a token is stale, and the refusal costs no request."""
    h = write_harness(fake, fclock, confirm_ttl=300)
    arguments = {"user_id": MODERATOR_ID, "duration": 600}
    token = await mint(h, "timeout_user", BROADCASTER, arguments)
    fclock.advance(301)
    h.reset_counts()
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "timeout_user", BROADCASTER, arguments, confirm=token)
    assert exc.value.kind == "stale_confirmation"
    assert h.requests == 0


@pytest.mark.parametrize(
    "tool,group,arguments,path,verb", WRITE_CASES, ids=[c[0] for c in WRITE_CASES]
)
async def test_r33_clearing_or_identity_change_kills_outstanding_tokens(
    fake: FakeTwitch,
    fclock: FakeClock,
    tool: str,
    group: str,
    arguments: dict[str, Any],
    path: str,
    verb: str,
) -> None:
    """R33: disconnect/refresh/401 semantics — after clearing, a live token becomes stale (0 requests)."""
    h = write_harness(fake, fclock)
    token = await mint(h, tool, BROADCASTER, arguments)
    h.service.on_identity_change()
    assert len(h.confirmations) == 0
    h.reset_counts()
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, tool, BROADCASTER, arguments, confirm=token)
    assert exc.value.kind == "stale_confirmation"
    assert h.requests == 0


async def test_r33_clear_confirmations_reports_the_count(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R33: the server's shutdown path can report how many outstanding tokens it dropped."""
    h = write_harness(fake, fclock)
    await mint(h, "add_vip", BROADCASTER, {"user_id": MODERATOR_ID})
    await mint(h, "remove_vip", BROADCASTER, {"user_id": MODERATOR_ID})
    assert h.service.clear_confirmations() == 2
    assert h.service.clear_confirmations() == 0


async def test_r34_guard_runs_again_after_the_token_is_consumed(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R34: a token never bypasses the guard — a channel removed mid-flow is refused, zero requests."""
    h = write_harness(
        fake,
        fclock,
        allowed=frozenset({BROADCASTER, NOT_ALLOWLISTED}),
        moderators={BROADCASTER: [OPERATOR], NOT_ALLOWLISTED: [OPERATOR]},
    )
    arguments = {"user_id": MODERATOR_ID, "duration": 600}
    token = await mint(h, "timeout_user", NOT_ALLOWLISTED, arguments)
    assert h.requests == 0 and len(h.confirmations) == 1
    # The allowlist shrinks after the mint (a config reload or operator mistake caught late).
    h.guard._allowed = frozenset({BROADCASTER})  # noqa: SLF001
    h.reset_counts()
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "timeout_user", NOT_ALLOWLISTED, arguments, confirm=token)
    assert exc.value.kind == "forbidden_channel"
    assert exc.value.message == BROADCASTER_REFUSAL
    assert h.requests == 0


# --------------------------------------------------------------------------- R38: exclude_unset


async def test_r38_unset_projection_keys_are_omitted_not_nulled(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R38: a field the projection never set is absent from the dump, never ``null``."""
    h = harness(
        fake,
        fclock,
        owners={
            "111000111": {"id": "111000111", "user_id": BROADCASTER},
            "222000222": {"id": "222000222", "user_id": BROADCASTER},
        },
    )
    partial = {"id": "111000111", "user_id": BROADCASTER, "title": "A clip"}
    missing = {"id": "222000222", "user_id": BROADCASTER}
    fake.add(
        "GET",
        r"/helix/videos",
        fake.json({"data": [partial, missing], "pagination": {}}),
    )
    result = await h.service.get_videos(video_ids=["111000111", "222000222"])
    # (A body-carried `total` cannot be asserted here: project_videos never forwards `total`,
    # so it is absent whatever the body says — vacuous. The per-record check is the real R38.)
    dump = result.model_dump(mode="json", exclude_unset=True)
    assert "title" in dump["videos"][0]
    assert "title" not in dump["videos"][1]
    assert "description" not in dump["videos"][0]


# --------------------------------------------------------------------------- R39/R40/R17: argument rules


async def test_r40_first_above_the_documented_bound_is_refused(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R40: a page bigger than Helix documents never becomes a request."""
    h = harness(fake, fclock)
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_polls(broadcaster_id=BROADCASTER, first=MAX_FIRST["get_polls"] + 1)
    assert exc.value.kind == "invalid_arguments"
    assert h.requests == 0


@pytest.mark.parametrize(
    "tool", ["get_users_follows", "get_streams", "get_videos", "get_clips", "get_schedule"]
)
async def test_r40_mutually_exclusive_cursors_are_refused(
    fake: FakeTwitch, fclock: FakeClock, tool: str
) -> None:
    """R40: ``after`` and ``before`` together are an invalid argument, never a request."""
    h = harness(fake, fclock)
    fake.strict = True
    call = getattr(h.service, tool)
    kwargs: dict[str, Any] = {"after": "a1", "before": "b1", "broadcaster_id": BROADCASTER}
    if tool in {"get_videos", "get_clips"}:
        kwargs.pop("broadcaster_id")
        kwargs["game_id"] = GAME_ID
    if tool == "get_users_follows":
        kwargs.pop("broadcaster_id")
        kwargs["from_id"] = BROADCASTER
    if tool == "get_streams":
        kwargs.pop("broadcaster_id")
        kwargs["game_id"] = GAME_ID
    with pytest.raises(TwitchMcpError) as exc:
        await call(**kwargs)
    assert exc.value.kind == "invalid_arguments"
    assert h.requests == 0


async def test_r17_malformed_cursor_never_reaches_a_url(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R17/R40: a cursor with a shape an attacker chose is refused before the socket."""
    h = harness(fake, fclock)
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_polls(broadcaster_id=BROADCASTER, after="abc?redirect=evil")
    assert exc.value.kind == "invalid_arguments"
    assert h.requests == 0


# --------------------------------------------------------------------------- R42: cursor semantics


async def test_r42_empty_cursor_means_no_further_page(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R42: Helix's last page (``pagination: {}`` or an empty cursor) has nothing behind it."""
    h = harness(fake, fclock)
    fake.add("GET", r"/helix/polls", fake.json({"data": [], "pagination": {}}))
    result = await h.service.get_polls(broadcaster_id=BROADCASTER)
    assert result.polls == []
    assert has_more(result.pagination) is False
    # The alternative last-page encoding behaves the same.
    h.reset_counts()
    fake.add("GET", r"/helix/polls", fake.json({"data": [], "pagination": {"cursor": ""}}))
    result2 = await h.service.get_polls(broadcaster_id=BROADCASTER)
    assert has_more(result2.pagination) is False


async def test_r42_a_real_cursor_is_carried(fake: FakeTwitch, fclock: FakeClock) -> None:
    """R42: a non-empty cursor survives projection so the caller can ask for the next page."""
    h = harness(fake, fclock)
    fake.add(
        "GET", r"/helix/polls", fake.json({"data": [], "pagination": {"cursor": "next-page-1"}})
    )
    result = await h.service.get_polls(broadcaster_id=BROADCASTER)
    assert has_more(result.pagination) is True
    assert result.pagination is not None and result.pagination.cursor.get("cursor") == "next-page-1"


# --------------------------------------------------------------------------- R45: empty vs forbidden


async def test_r45_empty_result_is_a_success_not_a_permission_problem(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R45: an empty list is genuinely empty — a normal result, not an error and not a masked 403."""
    h = harness(fake, fclock)
    result = await h.service.get_polls(broadcaster_id=BROADCASTER)  # fake default: {"data": []}
    assert result.polls == []
    assert result.meta is not None


async def test_r45_missing_scope_surfaces_as_permission(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R45: Twitch's own missing-scope words become ``kind=permission`` at the service boundary."""
    h = harness(fake, fclock, user_oauth=True, user_connected=True)
    forbid(
        fake,
        "GET",
        r"/helix/subscriptions",
        {
            "status": 403,
            "error": "Missing scope: moderator:read:subscribers required for this endpoint",
            "scopes": ["chat:read"],
        },
        403,
    )
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.get_broadcaster_subscribers(broadcaster_id=BROADCASTER)
    assert exc.value.kind == "permission"


# --------------------------------------------------------------------------- R52/R53: logging


async def test_r52_free_text_never_appears_in_any_log_record(
    fake: FakeTwitch, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """R52: message text, ban reason and automod term stay out of the log; the audit line is complete."""
    h = write_harness(fake, fclock)
    with caplog.at_level(logging.DEBUG):
        token = await mint(h, "send_chat_message", BROADCASTER, {"message": CHAT_TEXT})
        h.reset_counts()
        await dispatch(h, "send_chat_message", BROADCASTER, {"message": CHAT_TEXT}, confirm=token)
        token = await mint(
            h, "ban_user", BROADCASTER, {"user_id": MODERATOR_ID, "reason": BAN_REASON}
        )
        h.reset_counts()
        await dispatch(
            h,
            "ban_user",
            BROADCASTER,
            {"user_id": MODERATOR_ID, "reason": BAN_REASON},
            confirm=token,
        )
        token = await mint(
            h, "set_automod_term", BROADCASTER, {"text": AUTOMOD_TERM, "category": "harassment"}
        )
        h.reset_counts()
        await dispatch(
            h,
            "set_automod_term",
            BROADCASTER,
            {"text": AUTOMOD_TERM, "category": "harassment"},
            confirm=token,
        )
    rendered = "\n".join(
        record.getMessage() + " " + " ".join(str(v) for v in vars(record).values())
        for record in caplog.records
    )
    for secret in (CHAT_TEXT, BAN_REASON, AUTOMOD_TERM, APP_BEARER, USER_BEARER):
        assert secret not in rendered


async def test_r53_write_audit_carries_broadcaster_tool_and_outcome(
    fake: FakeTwitch, fclock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """R53: the dispatched write logs broadcaster id + tool + outcome — and nothing else about it."""
    h = write_harness(fake, fclock)
    token = await mint(h, "ban_user", BROADCASTER, {"user_id": MODERATOR_ID, "reason": BAN_REASON})
    h.reset_counts()
    with caplog.at_level(logging.DEBUG):
        await dispatch(
            h,
            "ban_user",
            BROADCASTER,
            {"user_id": MODERATOR_ID, "reason": BAN_REASON},
            confirm=token,
        )
    audits = [r for r in caplog.records if getattr(r, "tool", None)]
    assert audits, "expected at least one write-audit record"
    audit = audits[-1]
    assert getattr(audit, "broadcaster_id", None) == BROADCASTER
    audit_tool = str(getattr(audit, "tool", ""))
    assert "POST" in audit_tool and "/moderation/banned" in audit_tool
    assert getattr(audit, "outcome", None), "the audit line must carry an outcome"
    assert BAN_REASON not in audit.getMessage()


# --------------------------------------------------------------------------- account tools (R47/R51)


async def test_r47_connect_hands_back_a_url_and_no_secret(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R47: the connect tool is a URL plus instructions — no code, token or state anywhere."""
    h = harness(fake, fclock, user_oauth=True)
    result = await h.service.connect_twitch_account()
    assert result.sign_in_url and result.instructions
    assert result.already_connected is False
    dump = result.model_dump(mode="json", exclude_none=True)
    assert "state" not in dump  # a state VALUE is never returned (R47/R52)
    assert "state=" in result.sign_in_url  # the URL carries the opaque state for the callback
    assert h.requests == 0  # nothing left the process


async def test_r47_connect_is_disabled_without_oauth(fake: FakeTwitch, fclock: FakeClock) -> None:
    h = harness(fake, fclock)  # user_oauth off
    with pytest.raises(TwitchMcpError) as exc:
        await h.service.connect_twitch_account()
    assert exc.value.kind == "disabled"


async def test_r51_disconnect_revokes_only_this_credential(
    fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R51 + R30/R33: disconnect revokes, deletes, and kills stale identity verdicts and tokens."""
    h = write_harness(fake, fclock)
    token = await mint(h, "add_vip", BROADCASTER, {"user_id": MODERATOR_ID})
    result = await h.service.disconnect_twitch_account()
    assert result.revoked is True and result.user_id == OPERATOR
    assert len(h.confirmations) == 0
    h.reset_counts()
    fake.strict = True
    with pytest.raises(TwitchMcpError) as exc:
        await dispatch(h, "add_vip", BROADCASTER, {"user_id": MODERATOR_ID}, confirm=token)
    assert exc.value.kind == "stale_confirmation"
    assert h.requests == 0
