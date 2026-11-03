"""Result-model tests (R38, SPEC §6.4).

Models are the single source of truth for every tool's ``outputSchema``: they must
construct from projected Helix JSON, reject unknown keys (``extra='forbid'`` so the
schema stays closed) and omit unset optional keys entirely rather than emitting them
as ``null`` (R38).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from twitch_mcp import models

# --- §6.4 population ---------------------------------------------------------------------------

READ_RESULT_MODELS: tuple[type[models._Result], ...] = (
    models.UsersResult,
    models.FollowsResult,
    models.ChannelsResult,
    models.SearchChannelsResult,
    models.StreamsResult,
    models.GamesResult,
    models.VideosResult,
    models.ClipsResult,
    models.PollsResult,
    models.PredictionsResult,
    models.EmotesResult,
    models.ChatBadgesResult,
    models.ChatSettingsResult,
    models.MarkersResult,
    models.SubscribersResult,
    models.EditorsResult,
    models.ModeratorsResult,
    models.BannedUsersResult,
    models.AutomodSettingsResult,
    models.ChattersResult,
    models.ScheduleResult,
)

ACCOUNT_RESULT_MODELS: tuple[type[models._Result], ...] = (
    models.AccountStatusResult,
    models.ConnectResult,
    models.DisconnectResult,
)

ALL_RESULT_MODELS: tuple[type[models._Result], ...] = (
    *READ_RESULT_MODELS,
    *ACCOUNT_RESULT_MODELS,
    models.WriteResult,
)

ENTITY_MODELS: tuple[type[models._Result], ...] = (
    models.User,
    models.Follow,
    models.Channel,
    models.SearchResult,
    models.Stream,
    models.Game,
    models.Video,
    models.Clip,
    models.PollChoice,
    models.Poll,
    models.PredictionOutcome,
    models.Prediction,
    models.Emote,
    models.EmoteSet,
    models.BadgeVersion,
    models.BadgeSet,
    models.ChatSettings,
    models.StreamMarker,
    models.Marker,
    models.Subscription,
    models.Editor,
    models.Moderator,
    models.BannedUser,
    models.AutomodSettings,
    models.Chatter,
    models.ScheduleCategory,
    models.ScheduleSegment,
    models.Schedule,
    models.TokenValidation,
)


def test_all_exports_only_advertise_result_models() -> None:
    """``__all__`` lists the tool-result surface the service layer registers."""

    assert models.__all__  # non-empty
    assert all(name.endswith("Result") for name in models.__all__)
    for name in models.__all__:
        assert isinstance(getattr(models, name), type)


@pytest.mark.parametrize("model", ALL_RESULT_MODELS, ids=lambda m: m.__name__)
def test_r38_every_result_model_accepts_an_empty_envelope(model: type[models._Result]) -> None:
    """Every §6.4 result model constructs; unset optionals are omitted, never null (R38)."""

    instance = model()
    dumped = instance.model_dump(mode="json", exclude_unset=True)
    assert dumped == {}
    assert None not in dumped.values()


@pytest.mark.parametrize("model", ENTITY_MODELS, ids=lambda m: m.__name__)
def test_r38_every_entity_model_round_trips(model: type[models._Result]) -> None:
    """Each entity projection constructs and survives a JSON round trip (R38)."""

    instance = model()
    payload = instance.model_dump(mode="json", exclude_unset=True)
    assert model.model_validate(payload) == instance


@pytest.mark.parametrize("model", ALL_RESULT_MODELS, ids=lambda m: m.__name__)
def test_r38_unknown_keys_are_rejected(model: type[models._Result]) -> None:
    """``extra='forbid'`` keeps outputSchema closed: a stray Helix key never leaks through."""

    with pytest.raises(ValidationError):
        model.model_validate({"totally_unknown_key": 1})


@pytest.mark.parametrize("model", ENTITY_MODELS, ids=lambda m: m.__name__)
def test_r38_entities_reject_unknown_keys(model: type[models._Result]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"totally_unknown_key": 1})


# --- envelope behaviour ------------------------------------------------------------------------


def test_r38_unset_keys_are_omitted_but_explicit_none_is_kept() -> None:
    """R38: never-set keys disappear from the payload; a deliberately set null survives."""

    partial = models.UsersResult(users=[models.User(id="123", display_name="CoolStreamer")])
    dumped = partial.model_dump(mode="json", exclude_unset=True)
    assert "meta" not in dumped
    assert dumped == {"users": [{"id": "123", "display_name": "CoolStreamer"}]}

    explicit = models.UsersResult(meta=None, users=[])
    assert explicit.model_dump(mode="json", exclude_unset=True) == {"meta": None, "users": []}


def test_r38_models_drive_a_closed_output_schema() -> None:
    """The generated JSON schema forbids extras, so structuredContent cannot drift (R38)."""

    schema = models.UsersResult.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"meta", "users"}


def test_meta_envelope_carries_freshness_not_values() -> None:
    """Meta is the as-of/cached/age envelope (SPEC §6.4); it never carries data fields."""

    meta = models.Meta(
        as_of="2026-10-29T00:00:00Z", cached=True, cache_age_seconds=30, endpoint="GET /streams"
    )
    assert meta.model_dump(mode="json", exclude_unset=True) == {
        "as_of": "2026-10-29T00:00:00Z",
        "cached": True,
        "cache_age_seconds": 30,
        "endpoint": "GET /streams",
    }
    assert models.Meta().model_dump(mode="json", exclude_unset=True) == {}


def test_pagination_envelope_shape() -> None:
    """Pagination carries the opaque cursor dict only; default {} means no more pages (R42)."""

    assert models.Pagination().cursor == {}
    page = models.Pagination(cursor={"cursor": "opaque-cursor-value"})
    assert page.model_dump(mode="json", exclude_unset=True) == {
        "cursor": {"cursor": "opaque-cursor-value"}
    }
    with pytest.raises(ValidationError):
        models.Pagination(cursor="not-a-dict")  # type: ignore[arg-type]


# --- nested projections ------------------------------------------------------------------------


def test_nested_poll_projection_projects_helix_json() -> None:
    """Polls nest choices; projection from raw Helix keys must round-trip (R38 compactness)."""

    result = models.PollsResult(
        meta=models.Meta(endpoint="GET /polls"),
        total=1,
        polls=[
            models.Poll(
                id="poll-1",
                broadcaster_id="141981764",
                title="Pick a game",
                status="ACTIVE",
                choices=[models.PollChoice(id="c1", title="A", votes=3, channel_points_voting=100)],
            )
        ],
    )
    payload = result.model_dump(mode="json", exclude_unset=True)
    assert payload["polls"][0]["choices"][0]["title"] == "A"
    assert "duration" not in payload["polls"][0]  # unset sibling omitted, not null (R38)
    assert models.PollsResult.model_validate(payload) == result


def test_nested_schedule_and_chat_settings_projections() -> None:
    """Schedule segments and chat settings are the other list-nested §6.4 projections."""

    schedule = models.ScheduleResult(
        schedule=models.Schedule(
            broadcaster_id="141981764",
            segments=[
                models.ScheduleSegment(
                    id="s1",
                    title="Stream",
                    category=models.ScheduleCategory(id="509670", name="Science & Technology"),
                    is_recurring=False,
                )
            ],
        )
    )
    payload = schedule.model_dump(mode="json", exclude_unset=True)
    assert payload["schedule"]["segments"] == [
        {
            "id": "s1",
            "title": "Stream",
            "category": {"id": "509670", "name": "Science & Technology"},
            "is_recurring": False,
        }
    ]

    settings = models.ChatSettingsResult(
        settings=[models.ChatSettings(room_id="141981764", slow_mode=5, subscriber_mode=False)]
    )
    assert settings.model_dump(mode="json", exclude_unset=True) == {
        "settings": [{"room_id": "141981764", "slow_mode": 5, "subscriber_mode": False}]
    }


def test_automod_levels_are_integers_not_free_text() -> None:
    """AutoMod levels are numeric per Helix; the model must reject arbitrary strings.

    Helix documents the levels as sibling integer fields on the single record it returns -- there is
    no nested ``settings`` array on this endpoint -- so the test drives the documented field names.
    """

    settings = models.AutomodSettingsResult(
        settings=models.AutomodSettings(
            broadcaster_id="141981764",
            moderator_id="295706674",
            overall_level=2,
            disability=3,
            aggression=1,
            sexuality_sex_or_gender=2,
            misogyny=2,
            bullying=3,
            swearing=0,
            race_ethnicity_or_religion=3,
            sex_based_terms=1,
        )
    )
    assert settings.settings is not None
    assert settings.settings.overall_level == 2
    assert settings.settings.race_ethnicity_or_religion == 3
    with pytest.raises(ValidationError):
        models.AutomodSettings(disability="very strict")  # type: ignore[arg-type]


# --- account + write results --------------------------------------------------------------------


def test_token_validation_never_carries_a_token_value() -> None:
    """``TokenValidation`` projects /oauth2/validate fields only (R52: no secret material)."""

    validation = models.TokenValidation(
        token_type="user",
        client_id="cid",
        user_id="141981764",
        user_login="coolstreamer",
        scopes=["chat:read"],
        expires_in=15000,
    )
    dumped = validation.model_dump(mode="json", exclude_unset=True)
    assert dumped["scopes"] == ["chat:read"]
    # Projection fields only: token_type names the kind, and no field ever carries
    # secret material (R52).
    assert set(dumped) == {
        "token_type",
        "client_id",
        "user_id",
        "user_login",
        "scopes",
        "expires_in",
    }
    assert all(key != "access_token" for key in dumped)


def test_account_status_result_shape() -> None:
    """Account status reports both identities' health plus the effective write plan (R36/R37)."""

    status = models.AccountStatusResult(
        app_token=models.TokenValidation(token_type="app"),
        user_oauth_enabled=False,
        user_connected=False,
        writes_enabled=False,
        enabled_write_groups=[],
        tool_count=25,
        degraded=False,
    )
    payload = status.model_dump(mode="json", exclude_unset=True)
    assert payload["tool_count"] == 25
    assert payload["enabled_write_groups"] == []
    assert "user" not in payload  # unset optional omitted (R38)


def test_connect_result_carries_url_not_secrets() -> None:
    """ConnectResult hands back the sign-in URL and state *name*; never code/state values (R52)."""

    connect = models.ConnectResult(
        state_name="pending",
        sign_in_url="https://id.twitch.tv/oauth2/authorize?client_id=cid&state=redacted",
        redirect_uri="http://127.0.0.1:8770/oauth/callback",
        scopes=["chat:read"],
        expires_in_seconds=600,
    )
    payload: dict[str, Any] = connect.model_dump(mode="json", exclude_unset=True)
    assert payload["state_name"] == "pending"
    assert "state" not in payload and "code" not in payload
    # The opaque state lives inside the URL only as a redacted placeholder (R52).
    assert "state=opaque" not in str(payload["sign_in_url"])


def test_disconnect_result_reports_revocation() -> None:
    """DisconnectResult confirms revoke + store clear (R51)."""

    assert models.DisconnectResult(revoked=True, user_id="141981764").model_dump(
        mode="json", exclude_unset=True
    ) == {"revoked": True, "user_id": "141981764"}


def test_write_result_default_is_a_confirmation_request() -> None:
    """First write turn: confirmation_required=True (a successful result), dispatched unset (R31/R32)."""

    write = models.WriteResult(
        confirmation_required=True,
        confirmation=models.WriteConfirmation(
            token="single-use-token",
            expires_at="2026-10-29T00:10:00Z",
            tool="create_chat_poll",
            broadcaster_id="141981764",
            arguments={"title": "Pick a game", "choices": ["A", "B"]},
        ),
    )
    payload = write.model_dump(mode="json", exclude_unset=True)
    assert payload["confirmation_required"] is True
    assert payload["confirmation"]["tool"] == "create_chat_poll"
    assert payload["confirmation"]["arguments"] == {"title": "Pick a game", "choices": ["A", "B"]}
    assert "dispatched" not in payload  # nothing dispatched yet


def test_write_result_dispatched_shape() -> None:
    """A dispatched write reports dispatched=True with Helix's first record and no confirmation."""

    write = models.WriteResult(
        confirmation_required=False,
        dispatched=True,
        data=[{"id": "poll-1", "status": "ACTIVE"}],
        message="Poll created.",
    )
    payload = write.model_dump(mode="json", exclude_unset=True)
    assert payload == {
        "confirmation_required": False,
        "dispatched": True,
        "data": [{"id": "poll-1", "status": "ACTIVE"}],
        "message": "Poll created.",
    }
    assert "confirmation" not in payload


def test_write_confirmation_rejects_unknown_plan_keys() -> None:
    """The confirmation plan is a closed dict of arguments; the model itself stays sealed."""

    with pytest.raises(ValidationError):
        models.WriteConfirmation.model_validate({"token": "t", "session_webhook_secret": "nope"})
