"""R38/R42: raw Helix JSON → models, exclusion of inapplicable keys, pagination semantics."""

from __future__ import annotations

from typing import Any

import pytest

from tests.twitch_fakes import load
from twitch_mcp import project
from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.models import ClipsResult, Pagination, StreamsResult, UsersResult


def test_users_projection_keeps_declared_fields() -> None:
    result = project.project_users(load("users.json"))
    assert isinstance(result, UsersResult)
    assert result.users is not None and result.users[0].id == "123456789"
    assert result.users[0].display_name == " OperatorLogin "
    assert result.users[0].created_at == "2018-01-01T00:00:00Z"
    assert result.meta is not None and result.meta.endpoint == "GET /users"


def test_projection_drops_fields_that_are_not_in_the_models() -> None:
    """R38: a Helix field added upstream cannot widen a tool's output schema."""
    body = load("users.json")
    body["data"][0]["unsupported_new_field"] = {"secret": "value"}
    result = project.project_users(body)
    assert result.model_dump(exclude_none=True)  # projection succeeded despite the extra field
    assert "unsupported_new_field" not in result.model_dump(exclude_none=True)["users"][0]
    assert result.users is not None and result.users[0].login == "operator_login"


def test_streams_projection_omits_inapplicable_keys_never_null() -> None:
    """R38: keys Helix returned as null are left unset; the server dumps with exclude_unset."""
    result = project.project_streams(load("streams.json"))
    assert isinstance(result, StreamsResult)
    assert result.streams is not None
    offline = next(s for s in result.streams if s.id == "20002")
    live = next(s for s in result.streams if s.id == "20001")
    assert live.title == "Live now!"
    # The projection never passed title/tags for the offline record: they are unset, not None-filled.
    assert "title" not in offline.model_dump(exclude_unset=True)
    assert "tags" not in offline.model_dump(exclude_unset=True)
    assert "title" in live.model_dump(exclude_unset=True)


def test_pagination_empty_cursor_means_no_more() -> None:
    """R42: Helix's last page carries no cursor; has_more must be false."""
    last_page = project.project_streams(load("streams.json"))  # "pagination": {"cursor": ""}
    assert project.has_more(last_page.pagination) is False

    mid_page = project.project_clips({"data": [], "pagination": {"cursor": "abc123"}})
    assert isinstance(mid_page, ClipsResult)
    assert project.has_more(mid_page.pagination) is True
    assert mid_page.pagination is not None and mid_page.pagination.cursor == {"cursor": "abc123"}

    no_block = project.project_clips({"data": []})
    assert project.has_more(no_block.pagination) is False
    assert project.has_more(None) is False


def test_pagination_model_shape() -> None:
    page = Pagination(cursor={"cursor": "xyz"})
    assert project.has_more(page) is True
    assert project.has_more(Pagination()) is False


def test_malformed_body_is_upstream_error_never_empty_list() -> None:
    """G5/R42: an empty list means genuinely empty; a wrong shape raises upstream."""
    bad_bodies: list[Any] = [None, [], {"no_data": 1}, {"data": "not-a-list"}, {"data": [1, 2]}]
    for bad in bad_bodies:
        try:
            project.project_clips(bad)
        except TwitchMcpError as err:
            assert err.kind == "upstream"
        else:
            raise AssertionError(f"expected upstream error for {bad!r}")
    # A genuinely empty list projects to an empty result, not an error.
    empty = project.project_clips({"data": [], "pagination": {}})
    assert empty.clips == []


def test_clips_projection_drops_url_fields() -> None:
    """URL templates never enter the models (R52): thumbnail_url is not a Clip field."""
    body = {
        "data": [
            {
                "id": "ClipOne",
                "broadcaster_id": "123456789",
                "title": "A synthetic clip",
                "duration": 30.0,
                "thumbnail_url": "https://clips-media-assets.example/thumb.jpg?token=leak",
            }
        ],
        "pagination": {},
    }
    result = project.project_clips(body)
    assert result.clips is not None and result.clips[0].id == "ClipOne"
    assert not hasattr(result.clips[0], "thumbnail_url")
    assert "leak" not in result.model_dump_json(exclude_none=True)


def test_write_data_projection_returns_plain_records() -> None:
    body = {"data": [{"message_id": "abc", "is_sent": True, "is_pending": False}], "pagination": {}}
    records = project.project_write_data(body)
    assert records == [{"message_id": "abc", "is_sent": True, "is_pending": False}]


def test_meta_carries_endpoint_template_and_as_of_only() -> None:
    result = project.project_users(
        {"data": []}, endpoint="GET /users", as_of="2026-10-29T00:00:00+00:00"
    )
    assert result.meta is not None
    assert result.meta.endpoint == "GET /users"
    assert result.meta.as_of == "2026-10-29T00:00:00+00:00"
    assert result.meta.cached is False


def test_every_project_function_is_typed() -> None:
    import inspect

    from twitch_mcp import models

    for name, fn in vars(project).items():
        if not name.startswith("project_") or not inspect.isfunction(fn):
            continue
        assert "return" in fn.__annotations__, f"{name} lacks a return annotation"
        assert "body" in inspect.signature(fn).parameters, name
        hint: Any = fn.__annotations__["return"]
        if name == "project_write_data":
            assert hint in (list, "list[Any]", "list"), f"{name} returns plain records"
            continue
        # project.py uses PEP 563 string annotations; resolve against project then models.
        if isinstance(hint, str):
            hint = getattr(project, hint, None) or getattr(models, hint, None)
        assert isinstance(hint, type), f"{name} return must be a model class, got {hint!r}"


# --- A2-D3: the three Helix wire shapes, pinned to the documented format -------------------------
#
# These three endpoints are where a projection can be shaped to fit its own fake instead of the API.
# Each pair below therefore pins both halves: the documented body projects, and the body this server
# used to expect fails loudly rather than coming back looking empty (R45/G5).

_AUTOMOD_DOCUMENTED: dict[str, Any] = {
    "data": [
        {
            "broadcaster_id": "123",
            "moderator_id": "456",
            "overall_level": 2,
            "disability": 0,
            "aggression": 1,
            "sexuality_sex_or_gender": 0,
            "misogyny": 0,
            "bullying": 1,
            "swearing": 3,
            "race_ethnicity_or_religion": 0,
            "sex_based_terms": 2,
        }
    ],
    "pagination": {},
}


def test_automod_settings_projection_reads_integer_levels() -> None:
    """A2-D3: one record, integer levels, and a zero level stays a real zero rather than going absent."""
    result = project.project_automod_settings(_AUTOMOD_DOCUMENTED)
    settings = result.settings
    assert settings is not None
    assert settings.overall_level == 2
    assert settings.swearing == 3
    dumped = settings.model_dump(exclude_unset=True)
    assert dumped["disability"] == 0
    assert dumped["race_ethnicity_or_religion"] == 0


def test_automod_settings_rejects_the_nested_shape_it_replaced() -> None:
    """R45/G5: the pre-ruling nested-``settings`` body is an upstream failure, not a record of no levels."""
    legacy = {
        "data": [
            {
                "broadcaster_id": "123",
                "moderator_id": "456",
                "settings": [{"category": "swearing", "level": "no one"}],
            }
        ]
    }
    with pytest.raises(TwitchMcpError) as excinfo:
        project.project_automod_settings(legacy)
    assert excinfo.value.kind == "upstream"


def test_automod_settings_mistyped_field_is_a_tool_error_not_an_internal_error() -> None:
    """R43: a Helix body that mistypes a level must surface as an ``isError`` result with a kind."""
    broken = {"data": [{**_AUTOMOD_DOCUMENTED["data"][0], "overall_level": "very strict"}]}
    with pytest.raises(TwitchMcpError) as excinfo:
        project.project_automod_settings(broken)
    assert excinfo.value.kind == "upstream"


def test_chatters_projection_reads_the_flat_list_and_total() -> None:
    """A2-D3: ``data`` is a flat user array; ``total`` becomes ``chatter_count``."""
    body = {
        "data": [
            {"user_id": "1", "user_login": "mod_one", "user_name": "Mod One"},
            {"user_id": "2", "user_login": "view_one", "user_name": "View One"},
        ],
        "total": 7,
        "pagination": {"cursor": "abc"},
    }
    result = project.project_chatters(body)
    assert result.chatter_count == 7
    assert result.chatters is not None
    assert [c.user_login for c in result.chatters] == ["mod_one", "view_one"]
    assert "moderators" not in result.model_dump(exclude_unset=True)


def test_chatters_projection_refuses_a_role_bucket_body() -> None:
    """G5: the old ``{moderators: [...], viewers: [...]}`` shape is unreadable, never an empty list."""
    with pytest.raises(TwitchMcpError) as excinfo:
        project.project_chatters({"data": {"moderators": ["a"], "viewers": ["b"]}})
    assert excinfo.value.kind == "upstream"


def test_schedule_projection_reads_the_documented_object() -> None:
    """A2-D3: ``data`` is one object holding the broadcaster fields and ``segments``."""
    body = {
        "data": {
            "broadcaster_id": "123",
            "broadcaster_name": "Broadcaster",
            "broadcaster_login": "broadcaster",
            "segments": [
                {
                    "id": "s1",
                    "start_time": "2026-01-01T17:00:00Z",
                    "end_time": "2026-01-01T19:00:00Z",
                    "title": "Science & Technology",
                    "category": {"id": "509670", "name": "Science & Technology"},
                    "is_recurring": False,
                }
            ],
        },
        "pagination": {"cursor": "abc"},
    }
    result = project.project_schedule(body)
    schedule = result.schedule
    assert schedule is not None
    assert schedule.broadcaster_id == "123"
    assert schedule.segments is not None and len(schedule.segments) == 1
    segment = schedule.segments[0]
    assert segment.category is not None
    assert segment.category.id == "509670"
    assert segment.category.name == "Science & Technology"
    dumped = segment.model_dump(exclude_unset=True)
    assert "category_id" not in dumped and "category_name" not in dumped


def test_schedule_projection_refuses_a_bare_array_body() -> None:
    """G5: ``data`` as an array is not an empty schedule; it is a body this server cannot read."""
    with pytest.raises(TwitchMcpError) as excinfo:
        project.project_schedule({"data": [{"id": "s1"}]})
    assert excinfo.value.kind == "upstream"
