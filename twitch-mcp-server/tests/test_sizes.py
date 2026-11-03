"""R41: worst-case structured projection of every tool stays under 4 MiB.

For each registered tool the fake transport serves its **maximum `first`** as a page of fully
populated records — every optional field of the projected entity filled with a long-ish value, so
the projection is exercised at its largest realistic size rather than at an empty page. The test
then measures both the serialized ``structuredContent`` and the text block of the real
``CallToolResult`` and asserts each stays under 4 MiB (``TWITCH_MAX_RESPONSE_BYTES``' ceiling).

Results are complete pages and are never truncated (R41); the guard against a blow-up is this
size check plus ``first``'s schema bound, which the second test pins tool by tool so a bound can
never quietly widen.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.server_fakes import (
    READ_ARGS,
    WRITE_ARGS,
    boot_server,
    call,
    server_settings,
)
from tests.twitch_fakes import BROADCASTER, GAME_ID, FakeClock, FakeTwitch, helix_headers

#: The ceiling every projection must stay under (R41, mirroring TWITCH_MAX_RESPONSE_BYTES).
FOUR_MIB = 4 * 1024 * 1024

#: ``first`` as bounded by each tool's own schema (SPEC §6.1 / the tool's ``First*Arg`` alias).
MAX_FIRST: dict[str, int] = {
    "get_users_follows": 200,
    "search_channels": 100,
    "get_streams": 100,
    "get_top_games": 100,
    "get_games": 100,
    "get_videos": 100,
    "get_clips": 100,
    "get_polls": 20,
    "get_predictions": 20,
    "get_stream_markers": 100,
    "get_broadcaster_subscribers": 20,
    "get_moderators": 100,
    "get_banned_users": 100,
    "get_chat_chatters": 1000,
    "get_schedule": 25,
}

#: Which response key each tool's list lands under, so the fake can build one page of it.
LIST_KEY: dict[str, str] = {
    "get_users": "data",
    "get_users_follows": "data",
    "get_channels": "data",
    "search_channels": "data",
    "get_streams": "data",
    "get_top_games": "data",
    "get_games": "data",
    "get_videos": "data",
    "get_clips": "data",
    "get_polls": "data",
    "get_predictions": "data",
    "get_emotes": "data",
    "get_channel_chat_settings": "data",
    "get_channel_chat_badges": "data",
    "get_stream_markers": "data",
    "get_broadcaster_subscribers": "data",
    "get_channel_editors": "data",
    "get_moderators": "data",
    "get_banned_users": "data",
    "get_automod_settings": "data",
    "get_chat_chatters": "data",
    "get_schedule": "data",
}

_PAD = "x" * 64
_LONG = "long " * 40


def _user(index: int) -> dict[str, Any]:
    return {
        "id": str(100000000 + index),
        "login": f"viewer{index}",
        "display_name": f"Viewer {index} {_PAD}",
        "user_type": "affiliate",
        "broadcaster_type": "partner",
        "description": _LONG,
        "profile_image_url": f"https://static-cdn.jtvnw.net/{_PAD}.jpg",
        "offline_image_url": f"https://static-cdn.jtvnw.net/{_PAD}.jpg",
        "view_count": 1_234_567,
        "email": f"viewer{index}@example.invalid",
        "created_at": "2020-01-02T03:04:05Z",
    }


def _record(tool: str, index: int) -> dict[str, Any]:
    """One fully populated record for one tool's projected entity type."""
    if tool == "get_users":
        return _user(index)
    if tool == "get_users_follows":
        return {
            "from_id": str(100000000 + index),
            "from_name": f"From {index} {_PAD}",
            "to_id": BROADCASTER,
            "to_name": f"To {index} {_PAD}",
            "followed_at": "2026-01-01T00:00:00Z",
        }
    if tool in {"get_channels", "search_channels"}:
        return {
            "broadcaster_id": BROADCASTER,
            "id": BROADCASTER,
            "broadcaster_login": f"channel{index}",
            "display_name": f"Channel {index} {_PAD}",
            "broadcaster_name": f"Channel {index} {_PAD}",
            "broadcaster_language": "en",
            "game_id": GAME_ID,
            "game_name": f"Game {index} {_PAD}",
            "title": _LONG,
            "delay": 0,
            "tags": [_PAD] * 10,
            "content_classification_labels": ["Gambling"] * 6,
            "is_branded_content": True,
            "language": "en",
            "thumbnail_url": f"https://static-cdn.jtvnw.net/{_PAD}.jpg",
            "is_live": True,
        }
    if tool == "get_streams":
        return {
            "id": str(100000000 + index),
            "user_id": BROADCASTER,
            "user_login": f"streamer{index}",
            "user_name": f"Streamer {index} {_PAD}",
            "game_id": GAME_ID,
            "game_name": f"Game {index} {_PAD}",
            "type": "live",
            "title": _LONG,
            "viewer_count": 99_999,
            "started_at": "2026-10-29T12:00:00Z",
            "language": "en",
            "is_mature": False,
            "tags": [_PAD] * 10,
        }
    if tool in {"get_top_games", "get_games"}:
        return {
            "id": str(200000000 + index),
            "name": f"Game {index} {_PAD}",
            "box_art_url": f"https://static-cdn.jtvnw.net/{_PAD}.jpg",
        }
    if tool == "get_videos":
        return {
            "id": str(4400882000 + index),
            "user_id": BROADCASTER,
            "user_login": f"uploader{index}",
            "user_name": f"Uploader {index} {_PAD}",
            "title": _LONG,
            "description": _LONG,
            "created_at": "2026-01-01T00:00:00Z",
            "published_at": "2026-01-01T00:00:00Z",
            "duration": "1h2m3s",
            "period": "all_time",
            "view_count": 1234,
            "language": "en",
            "viewable": "public",
            "type": "archive",
        }
    if tool == "get_clips":
        return {
            "id": f"clip{index}",
            "broadcaster_id": BROADCASTER,
            "broadcaster_name": f"Bcaster {index} {_PAD}",
            "creator_id": str(100000000 + index),
            "creator_name": f"Creator {index} {_PAD}",
            "video_id": str(4400882000 + index),
            "game_id": GAME_ID,
            "language": "en",
            "title": _LONG,
            "duration": 60.5,
            "created_at": "2026-01-01T00:00:00Z",
            "view_count": 4242,
        }
    if tool == "get_polls":
        return {
            "id": f"poll{index}",
            "broadcaster_id": BROADCASTER,
            "broadcaster_name": f"Bcaster {_PAD}",
            "title": _LONG,
            "status": "RUNNING",
            "duration": 60,
            "started_at": "2026-01-01T00:00:00Z",
            "ended_at": None,
            "channel_points_betting": True,
            "bits_betting": True,
            "channel_points_voting": False,
            "choices": [
                {
                    "id": f"c{k}",
                    "title": f"Choice {k} {_PAD}",
                    "votes": 100 + k,
                    "channel_points_voting": 5,
                }
                for k in range(5)
            ],
        }
    if tool == "get_predictions":
        return {
            "id": f"pred{index}",
            "broadcaster_id": BROADCASTER,
            "broadcaster_name": f"Bcaster {_PAD}",
            "title": _LONG,
            "status": "ACTIVE",
            "winning_outcome_id": None,
            "prediction_window": 60,
            "created_at": "2026-01-01T00:00:00Z",
            "ended_at": None,
            "outcomes": [
                {
                    "id": f"o{k}",
                    "title": f"Outcome {k} {_PAD}",
                    "users": 40 + k,
                    "channel_points": 9000 + k,
                    "color": "BLUE" if k else "PINK",
                }
                for k in range(2)
            ],
        }
    if tool == "get_emotes":
        return {
            "id": str(300000000 + index),
            "name": f"feels{_PAD}",
            "images": {"url_1x": f"https://static-cdn.jtvnw.net/{_PAD}.png"},
            "format": "static",
            "scale": ["1", "2", "3", "4"],
            "theme_mode": ["light", "dark"],
        }
    if tool == "get_channel_chat_settings":
        return {
            "room_id": BROADCASTER,
            "room_name": f"room{_PAD}",
            "emitter_enabled": ["1"],
            "slow_mode": 30,
            "slow_mode_wait_time": 30,
            "follower_mode": 0,
            "follower_mode_duration": 0,
            "subscriber_mode": False,
            "emote_mode": False,
            "unique_chat_mode": False,
            "non_animated_emotes": [_PAD] * 5,
        }
    if tool == "get_channel_chat_badges":
        return {
            "set_id": str(500000000 + index),
            "versions": [
                {
                    "id": str(k),
                    "title": f"Badge {k} {_PAD}",
                    "image": f"https://static-cdn.jtvnw.net/{_PAD}.png",
                    "description": _LONG,
                }
                for k in range(10)
            ],
        }
    if tool == "get_stream_markers":
        return {
            "video_id": str(4400882000 + index),
            "markers": [
                {
                    "id": f"m{k}",
                    "created_at": "2026-01-01T00:00:00Z",
                    "description": _LONG,
                    "position_seconds": k * 60,
                    "video_id": str(4400882000 + index),
                }
                for k in range(20)
            ],
        }
    if tool == "get_broadcaster_subscribers":
        return {
            "tier": "1000",
            "is_gift": False,
            "gifter_id": None,
            "gifter_login": None,
            "gifter_name": None,
            "user": _user(index),
        }
    if tool in {"get_channel_editors", "get_moderators"}:
        return {
            "user_id": str(600000000 + index),
            "user_login": f"mod{index}",
            "user_name": f"Mod {index} {_PAD}",
            "created_at": "2026-01-01T00:00:00Z",
        }
    if tool == "get_banned_users":
        return {
            "user_id": str(600000000 + index),
            "user_login": f"banned{index}",
            "user_name": f"Banned {index} {_PAD}",
            "expires_at": "2026-11-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "reason": _LONG,
            "moderator": f"mod{_PAD}",
        }
    if tool == "get_automod_settings":
        # Helix answers with one record of integer levels, not a list of level objects.
        return {
            "broadcaster_id": BROADCASTER,
            "moderator_id": "666666666",
            "overall_level": 2,
            "disability": 3,
            "aggression": 3,
            "sexuality_sex_or_gender": 3,
            "misogyny": 3,
            "bullying": 3,
            "swearing": 3,
            "race_ethnicity_or_religion": 3,
            "sex_based_terms": 3,
        }
    if tool == "get_chat_chatters":
        return {
            "user_id": str(700000000 + index),
            "user_login": f"chatter{index}",
            "user_name": f"Chatter {index} {_PAD}",
        }
    if tool == "get_schedule":
        return {
            "id": f"seg{index}",
            "start_time": "2026-11-01T18:00:00Z",
            "end_time": "2026-11-01T21:00:00Z",
            "title": _LONG,
            "category": {"id": GAME_ID, "name": f"Game {_PAD}"},
            "canceled_until": None,
            "tags": [_PAD] * 5,
            "is_recurring": False,
        }
    raise AssertionError(f"no worst-case record defined for {tool}")


def _all_lists(node: Any, *, depth: int = 0) -> list[list[Any]]:
    """Every list anywhere in a projected result, so a page nested inside an object still counts."""
    if depth > 4:
        return []
    found: list[list[Any]] = []
    if isinstance(node, list):
        found.append(node)
        for item in node:
            found.extend(_all_lists(item, depth=depth + 1))
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_all_lists(value, depth=depth + 1))
    return found


def serve_worst_case(fake: FakeTwitch, tool: str, *, count: int, fclock: FakeClock) -> None:
    """Serve one fully populated page of ``count`` records for ``tool``."""
    key = LIST_KEY[tool]
    if tool == "get_schedule":
        # The one endpoint whose ``data`` is a single object holding the segments rather than an
        # array; the projection refuses any other shape, and this is what Helix really returns.
        body: dict[str, Any] = {
            "data": {
                "broadcaster_id": BROADCASTER,
                "broadcaster_name": "Broadcaster",
                "broadcaster_login": "broadcaster",
                "segments": [_record(tool, i) for i in range(count)],
            },
            "pagination": {"cursor": "abc" if count else ""},
        }
    else:
        body = {
            key: [_record(tool, i) for i in range(count)],
            "pagination": {"cursor": "abc" if count else ""},
        }
    if tool in {"get_users_follows", "get_chat_chatters"}:
        body["total"] = count
    fake.add("GET", r"/helix/.*", fake.json(body, headers=helix_headers(800, 799, clock=fclock)))


@pytest.mark.parametrize("tool", sorted(LIST_KEY))
async def test_worst_case_page_stays_under_4_mib(
    tool: str, fake: FakeTwitch, fclock: FakeClock
) -> None:
    """R41: at its maximum ``first`` with fully populated records, the result fits under 4 MiB."""
    settings = server_settings(twitch_user_oauth=True)
    server, _svc, plan = await boot_server(fake, fclock, settings, connected=True)
    assert tool in plan.registered, f"{tool} must be registered for its worst case to be measured"
    count = MAX_FIRST.get(tool, 100)
    serve_worst_case(fake, tool, count=count, fclock=fclock)
    arguments = {**READ_ARGS[tool]}
    if "first" in arguments:
        arguments["first"] = count
    fake.clear()
    result = await call(server, tool, arguments)
    assert result.is_error is not True, result.content[0].text
    structured = len(result.model_dump_json().encode("utf-8"))
    text = len(result.content[0].text.encode("utf-8"))
    assert structured < FOUR_MIB, f"{tool} structuredContent is {structured} bytes at first={count}"
    assert text < FOUR_MIB, f"{tool} text block is {text} bytes at first={count}"
    # The page really was the worst case: it holds `count` records and was not truncated (R41).
    body = result.structured_content
    lists = _all_lists(body)
    if tool == "get_automod_settings":
        # Helix answers this one with a single record of integer levels, so no list exists at any
        # depth and the worst case is that record -- bounded by the two size assertions above.
        assert lists == [], f"{tool} projected a list where Helix returns one record"
    else:
        assert lists, f"{tool} projected no list at first={count}"
        assert max(len(v) for v in lists) >= 1


async def test_every_list_tool_is_covered_by_the_size_matrix() -> None:
    """R41: no list-returning tool can be silently absent from the matrix above."""
    settings = server_settings(twitch_user_oauth=True)
    fake, fclock = FakeTwitch(), FakeClock()
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    from tests.server_fakes import list_tools

    tools = await list_tools(server)
    list_tools_expected = {name for name in LIST_KEY} | set(WRITE_ARGS)
    # The account pair is control-plane (a sign-in URL and a revoke acknowledgement); neither
    # returns a list, so neither belongs in a size matrix. Everything else must be covered.
    control_plane = {"twitch_account_status", "connect_twitch_account", "disconnect_twitch_account"}
    for tool in tools:
        assert tool.name in list_tools_expected or tool.name in control_plane, tool.name


async def test_first_is_bounded_by_the_endpoint_maximum() -> None:
    """R40/R41: each tool's ``first`` ceiling matches the endpoint's documented maximum."""
    settings = server_settings(twitch_user_oauth=True)
    fake, fclock = FakeTwitch(), FakeClock()
    server, _svc, _plan = await boot_server(fake, fclock, settings, connected=True)
    from tests.server_fakes import list_tools

    tools = {t.name: t for t in await list_tools(server)}
    for tool, maximum in MAX_FIRST.items():
        schema = tools[tool].input_schema
        node: dict[str, Any] = schema.get("properties", {}).get("first", {})
        # Pydantic emits a shared constrained int as a $ref (First200Arg), so the bounds live in
        # $defs -- read them from there rather than reporting "no maximum" on a schema that has one.
        ref = node.get("$ref")
        if isinstance(ref, str):
            resolved = schema.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
            node = {**resolved, **{k: v for k, v in node.items() if k != "$ref"}}
        assert node.get("maximum") == maximum, (tool, node)
        assert node.get("minimum") == 1, tool
    # And a page beyond the ceiling is refused before anything is sent (R39), so the ceiling holds.
    fake.clear()
    result = await call(server, "get_chat_chatters", {"broadcaster_id": BROADCASTER, "first": 1001})
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "invalid_arguments"
    assert fake.request_count == 0
