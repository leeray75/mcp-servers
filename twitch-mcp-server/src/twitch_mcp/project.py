"""Raw Helix JSON → the pydantic result models of ``models.py`` (R38, R42).

Projection, never passthrough: only fields declared in ``models.py`` survive, so a Helix field
added upstream cannot widen a tool's ``outputSchema`` (R38). Helix's URL fields carry tracking
templates and query strings and are deliberately left out of the models entirely (R52).

Three rules hold throughout:

* **Inapplicable keys are omitted, never emitted as null** (R38). Every entity is built through
  :func:`_build`, which passes only the keys Helix actually returned for that record; the server
  dumps with ``exclude_unset``. A key the projection never set simply does not appear.
* **An empty ``pagination.cursor`` means the end of the list** (R42). Helix answers the last page
  with ``"pagination": {}``; that becomes ``has_more=false`` by carrying no cursor, so a caller
  never asks for a page that will come back empty.
* **A malformed body is an upstream error, never an empty list.** An empty list always means
  genuinely empty (G5); a body that is not the shape Helix documents raises ``kind=upstream``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from twitch_mcp.errors import TwitchMcpError
from twitch_mcp.models import (
    AutomodSettings,
    AutomodSettingsResult,
    BadgeSet,
    BadgeVersion,
    BannedUser,
    BannedUsersResult,
    Channel,
    ChannelsResult,
    ChatBadgesResult,
    ChatSettings,
    ChatSettingsResult,
    Chatter,
    ChattersResult,
    Clip,
    ClipsResult,
    Editor,
    EditorsResult,
    Emote,
    EmoteSet,
    EmotesResult,
    Follow,
    FollowsResult,
    Game,
    GamesResult,
    Marker,
    MarkersResult,
    Meta,
    Moderator,
    ModeratorsResult,
    Pagination,
    Poll,
    PollChoice,
    PollsResult,
    Prediction,
    PredictionOutcome,
    PredictionsResult,
    Schedule,
    ScheduleResult,
    ScheduleSegment,
    SearchChannelsResult,
    SearchResult,
    Stream,
    StreamMarker,
    StreamsResult,
    SubscribersResult,
    Subscription,
    TokenValidation,
    User,
    UsersResult,
    Video,
    VideosResult,
)

#: The endpoint recorded in ``meta`` — a route template, never a URL (R52/R53).
Endpoint = str


def _build[T: BaseModel](model: type[T], record: Mapping[str, Any], keys: Sequence[str]) -> T:
    """Instantiate ``model`` with only the documented keys Helix actually returned (R38).

    Absent keys are left unset rather than passed as ``None``, so the server's ``exclude_unset``
    dump omits them entirely — which is what R38 asks for and what keeps an inapplicable key from
    appearing as ``null``.
    """
    values = {key: record[key] for key in keys if key in record and record[key] is not None}
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        # A Helix body that types a field differently from the documented schema is an upstream
        # fault, not a programming fault: R43 wants it reported as an ``isError`` tool result with
        # a kind, and reserves JSON-RPC errors for protocol failures. Letting pydantic escape here
        # would surface it to the agent as an opaque internal error instead.
        raise TwitchMcpError(
            "upstream",
            "Twitch returned a value this server cannot represent",
            hint="This is not an empty result: retry, and report it if it repeats.",
        ) from exc


def _records(body: Any, *, endpoint: str) -> list[dict[str, Any]]:
    """The ``data`` array of a Helix envelope, rejecting a body that is not one (R42/G5)."""
    if isinstance(body, Mapping):
        data = body.get("data")
        if isinstance(data, list) and all(isinstance(item, Mapping) for item in data):
            return [dict(item) for item in data]
    raise TwitchMcpError(
        "upstream",
        "Twitch returned a response body this server does not understand",
        hint="This is not an empty result: retry, and report it if it repeats.",
    )


def _pagination(body: Any) -> Pagination | None:
    """``pagination`` → :class:`Pagination`. An empty cursor is the end of the list (R42)."""
    if not isinstance(body, Mapping):
        return None
    page = body.get("pagination")
    if not isinstance(page, Mapping) or not page:
        # Helix's last page is `"pagination": {}`: no cursor to carry, so has_more is false (R42).
        return Pagination(cursor={})
    cursor = {str(key): str(value) for key, value in page.items() if isinstance(value, str)}
    return Pagination(cursor=cursor)


def has_more(page: Pagination | None) -> bool:
    """R42: a page has more behind it exactly when Helix gave a non-empty cursor.

    Helix signals the last page two ways, both of which mean "stop paging": a bare
    ``"pagination": {}`` (projected to ``cursor={}``) and a cursor whose value is the empty string
    (``{"cursor": ""}``). Either leaves no non-empty value to carry, so the check is on the cursor
    *values*, not merely on the dict being non-empty.
    """
    return page is not None and any(str(value) for value in page.cursor.values())


def _meta(endpoint: str, as_of: str, cached: bool = False, cache_age_seconds: int = 0) -> Meta:
    """The ``meta`` envelope every result carries. Template and freshness only (R52/R53)."""
    return Meta(as_of=as_of, cached=cached, cache_age_seconds=cache_age_seconds, endpoint=endpoint)


def now_iso(wall: Callable[[], datetime] | None = None) -> str:
    """The ``as_of`` stamp: UTC ISO-8601, from an injectable clock for tests."""
    moment = wall() if wall is not None else datetime.now(UTC)
    return moment.isoformat()


# --------------------------------------------------------------------------- entity projections

_USER_KEYS: Final = (
    "id",
    "login",
    "display_name",
    "user_type",
    "broadcaster_type",
    "description",
    "profile_image_url",
    "offline_image_url",
    "view_count",
    "email",
    "created_at",
)
_FOLLOW_KEYS: Final = ("from_id", "from_name", "to_id", "to_name", "followed_at")
_CHANNEL_KEYS: Final = (
    "broadcaster_id",
    "broadcaster_login",
    "broadcaster_name",
    "broadcaster_language",
    "game_id",
    "game_name",
    "title",
    "delay",
    "tags",
    "content_classification_labels",
    "is_branded_content",
)
_SEARCH_KEYS: Final = (
    "id",
    "broadcaster_login",
    "display_name",
    "game_id",
    "game_name",
    "language",
    "title",
    "thumbnail_url",
    "is_live",
    "tags",
)
_STREAM_KEYS: Final = (
    "id",
    "user_id",
    "user_login",
    "user_name",
    "game_id",
    "game_name",
    "type",
    "title",
    "viewer_count",
    "started_at",
    "language",
    "is_mature",
    "tags",
)
_GAME_KEYS: Final = ("id", "name", "box_art_url")
_VIDEO_KEYS: Final = (
    "id",
    "user_id",
    "user_login",
    "user_name",
    "title",
    "description",
    "created_at",
    "published_at",
    "duration",
    "period",
    "view_count",
    "language",
    "viewable",
    "type",
)
_CLIP_KEYS: Final = (
    "id",
    "broadcaster_id",
    "broadcaster_name",
    "creator_id",
    "creator_name",
    "video_id",
    "game_id",
    "language",
    "title",
    "duration",
    "created_at",
    "view_count",
)
_POLL_CHOICE_KEYS: Final = ("id", "title", "votes", "channel_points_voting")
_POLL_KEYS: Final = (
    "id",
    "broadcaster_id",
    "broadcaster_name",
    "title",
    "status",
    "duration",
    "started_at",
    "ended_at",
    "channel_points_betting",
    "bits_betting",
    "channel_points_voting",
)
_PREDICTION_OUTCOME_KEYS: Final = ("id", "title", "users", "channel_points", "color")
_PREDICTION_KEYS: Final = (
    "id",
    "broadcaster_id",
    "broadcaster_name",
    "title",
    "status",
    "winning_outcome_id",
    "prediction_window",
    "created_at",
    "ended_at",
)
_EMOTE_KEYS: Final = ("id", "name", "images", "format", "scale", "theme_mode")
_EMOTE_SET_KEYS: Final = ("id", "emote_set_id", "owner_id", "owner_name", "scope")
_BADGE_VERSION_KEYS: Final = ("id", "title", "image", "description")
_CHAT_SETTINGS_KEYS: Final = (
    "room_id",
    "room_name",
    "emitter_enabled",
    "slow_mode",
    "slow_mode_wait_time",
    "follower_mode",
    "follower_mode_duration",
    "subscriber_mode",
    "emote_mode",
    "unique_chat_mode",
    "non_animated_emotes",
)
_MARKER_KEYS: Final = ("id", "created_at", "description", "position_seconds", "video_id")
_SUBSCRIPTION_KEYS: Final = ("tier", "is_gift", "gifter_id", "gifter_login", "gifter_name")
_EDITOR_KEYS: Final = ("user_id", "user_login", "user_name", "created_at")
_MODERATOR_KEYS: Final = ("user_id", "user_login", "user_name", "created_at")
_BANNED_KEYS: Final = (
    "user_id",
    "user_login",
    "user_name",
    "expires_at",
    "created_at",
    "reason",
    "moderator",
)
#: The nine strictness levels Helix documents on the one record ``GET /moderation/automod/settings``
#: returns. A record carrying none of them is not a channel with no rules: it is a body this server
#: cannot read, which is why they are listed apart from the identity keys.
_AUTOMOD_LEVEL_KEYS: Final = (
    "overall_level",
    "disability",
    "aggression",
    "sexuality_sex_or_gender",
    "misogyny",
    "bullying",
    "swearing",
    "race_ethnicity_or_religion",
    "sex_based_terms",
)
_AUTOMOD_SETTINGS_KEYS: Final = ("broadcaster_id", "moderator_id", *_AUTOMOD_LEVEL_KEYS)
_CHATTER_KEYS: Final = ("user_id", "user_login", "user_name")
_SEGMENT_KEYS: Final = (
    "id",
    "start_time",
    "end_time",
    "title",
    "category",
    "canceled_until",
    "tags",
    "is_recurring",
)
_SCHEDULE_KEYS: Final = ("broadcaster_id", "broadcaster_name", "broadcaster_login")
_VALIDATE_KEYS: Final = (
    "token_type",
    "client_id",
    "user_id",
    "user_login",
    "scopes",
    "expires_in",
)


def project_users(body: Any, *, endpoint: str = "GET /users", as_of: str = "") -> UsersResult:
    """``GET /users`` → :class:`UsersResult`."""
    return UsersResult(
        meta=_meta(endpoint, as_of),
        users=[_build(User, r, _USER_KEYS) for r in _records(body, endpoint=endpoint)],
    )


def project_follows(
    body: Any, *, endpoint: str = "GET /users/follows", as_of: str = ""
) -> FollowsResult:
    """``GET /users/follows`` → :class:`FollowsResult` (``total`` and cursor included)."""
    records = _records(body, endpoint=endpoint)
    return FollowsResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        follows=[_build(Follow, r, _FOLLOW_KEYS) for r in records],
        pagination=_pagination(body),
    )


def project_channels(
    body: Any, *, endpoint: str = "GET /channels", as_of: str = ""
) -> ChannelsResult:
    """``GET /channels`` → :class:`ChannelsResult`."""
    return ChannelsResult(
        meta=_meta(endpoint, as_of),
        channels=[_build(Channel, r, _CHANNEL_KEYS) for r in _records(body, endpoint=endpoint)],
    )


def project_search_channels(
    body: Any, *, endpoint: str = "GET /search/channels", as_of: str = ""
) -> SearchChannelsResult:
    """``GET /search/channels`` → :class:`SearchChannelsResult`."""
    return SearchChannelsResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        channels=[_build(SearchResult, r, _SEARCH_KEYS) for r in _records(body, endpoint=endpoint)],
        pagination=_pagination(body),
    )


def project_streams(body: Any, *, endpoint: str = "GET /streams", as_of: str = "") -> StreamsResult:
    """``GET /streams`` → :class:`StreamsResult`."""
    return StreamsResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        streams=[_build(Stream, r, _STREAM_KEYS) for r in _records(body, endpoint=endpoint)],
        pagination=_pagination(body),
    )


def project_games(body: Any, *, endpoint: str = "GET /games", as_of: str = "") -> GamesResult:
    """``GET /games`` and ``GET /games/top`` → :class:`GamesResult`."""
    return GamesResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        games=[_build(Game, r, _GAME_KEYS) for r in _records(body, endpoint=endpoint)],
        pagination=_pagination(body),
    )


def project_videos(body: Any, *, endpoint: str = "GET /videos", as_of: str = "") -> VideosResult:
    """``GET /videos`` → :class:`VideosResult`. Also the owner lookup for ``video_id`` (R26)."""
    return VideosResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        videos=[_build(Video, r, _VIDEO_KEYS) for r in _records(body, endpoint=endpoint)],
        pagination=_pagination(body),
    )


def project_clips(body: Any, *, endpoint: str = "GET /clips", as_of: str = "") -> ClipsResult:
    """``GET /clips`` → :class:`ClipsResult`."""
    return ClipsResult(
        meta=_meta(endpoint, as_of),
        clips=[_build(Clip, r, _CLIP_KEYS) for r in _records(body, endpoint=endpoint)],
        pagination=_pagination(body),
    )


def project_polls(body: Any, *, endpoint: str = "GET /polls", as_of: str = "") -> PollsResult:
    """``GET /polls`` → :class:`PollsResult`, with nested choices."""
    polls: list[Poll] = []
    for record in _records(body, endpoint=endpoint):
        poll = _build(Poll, record, _POLL_KEYS)
        choices = record.get("choices")
        if isinstance(choices, list):
            poll = poll.model_copy(
                update={
                    "choices": [
                        _build(PollChoice, c, _POLL_CHOICE_KEYS)
                        for c in choices
                        if isinstance(c, Mapping)
                    ]
                }
            )
        polls.append(poll)
    return PollsResult(
        meta=_meta(endpoint, as_of), total=_total(body), polls=polls, pagination=_pagination(body)
    )


def project_predictions(
    body: Any, *, endpoint: str = "GET /predictions", as_of: str = ""
) -> PredictionsResult:
    """``GET /predictions`` → :class:`PredictionsResult`, with nested outcomes."""
    predictions: list[Prediction] = []
    for record in _records(body, endpoint=endpoint):
        prediction = _build(Prediction, record, _PREDICTION_KEYS)
        outcomes = record.get("outcomes")
        if isinstance(outcomes, list):
            prediction = prediction.model_copy(
                update={
                    "outcomes": [
                        _build(PredictionOutcome, o, _PREDICTION_OUTCOME_KEYS)
                        for o in outcomes
                        if isinstance(o, Mapping)
                    ]
                }
            )
        predictions.append(prediction)
    return PredictionsResult(
        meta=_meta(endpoint, as_of),
        predictions=predictions,
        total=_total(body),
        pagination=_pagination(body),
    )


def project_emotes(
    body: Any, *, endpoint: str = "GET /chat/emotes", as_of: str = ""
) -> EmotesResult:
    """``GET /chat/emotes`` (+ ``/set``, ``/global``) → :class:`EmotesResult`.

    Helix returns emote *sets* for those three endpoints and a bare emote list for the ``emote_id``
    form; both shapes are projected, and the unused half is left unset (R38).
    """
    records = _records(body, endpoint=endpoint)
    sets = [_build(EmoteSet, r, _EMOTE_SET_KEYS) for r in records]
    for emote_set, record in zip(sets, records, strict=True):
        emotes = record.get("emotes")
        if isinstance(emotes, list):
            emote_set = emote_set.model_copy(
                update={
                    "emotes": [
                        _build(Emote, e, _EMOTE_KEYS) for e in emotes if isinstance(e, Mapping)
                    ]
                }
            )
            sets[sets.index(emote_set)] = emote_set
    template = body.get("template") if isinstance(body, Mapping) else None
    has_sets = any("emote_set_id" in r for r in records)
    return EmotesResult(
        meta=_meta(endpoint, as_of),
        template={"template": str(template)} if isinstance(template, str) else None,
        emote_sets=sets if has_sets else None,
        emotes=None if has_sets else [_build(Emote, r, _EMOTE_KEYS) for r in records],
    )


def project_chat_badges(
    body: Any, *, endpoint: str = "GET /chat/badges", as_of: str = ""
) -> ChatBadgesResult:
    """``GET /chat/badges`` → :class:`ChatBadgesResult`."""
    sets: list[BadgeSet] = []
    for record in _records(body, endpoint=endpoint):
        badge_set = BadgeSet(set_id=str(record.get("set_id", "")))
        versions = record.get("versions")
        if isinstance(versions, list):
            badge_set = badge_set.model_copy(
                update={
                    "versions": [
                        _build(BadgeVersion, v, _BADGE_VERSION_KEYS)
                        for v in versions
                        if isinstance(v, Mapping)
                    ]
                }
            )
        sets.append(badge_set)
    return ChatBadgesResult(meta=_meta(endpoint, as_of), badge_sets=sets)


def project_chat_settings(
    body: Any, *, endpoint: str = "GET /chat/settings", as_of: str = ""
) -> ChatSettingsResult:
    """``GET /chat/settings`` → :class:`ChatSettingsResult`."""
    return ChatSettingsResult(
        meta=_meta(endpoint, as_of),
        settings=[
            _build(ChatSettings, r, _CHAT_SETTINGS_KEYS) for r in _records(body, endpoint=endpoint)
        ],
    )


def project_markers(
    body: Any, *, endpoint: str = "GET /streams/markers", as_of: str = ""
) -> MarkersResult:
    """``GET /streams/markers`` → :class:`MarkersResult`, grouping markers under their video."""
    markers: list[Marker] = []
    for record in _records(body, endpoint=endpoint):
        entry = Marker(video_id=str(record["video_id"]) if "video_id" in record else None)
        inner = record.get("markers")
        if isinstance(inner, list):
            entry = entry.model_copy(
                update={
                    "markers": [
                        _build(StreamMarker, m, _MARKER_KEYS)
                        for m in inner
                        if isinstance(m, Mapping)
                    ]
                }
            )
        markers.append(entry)
    return MarkersResult(meta=_meta(endpoint, as_of), markers=markers, pagination=_pagination(body))


def project_subscribers(
    body: Any, *, endpoint: str = "GET /subscriptions", as_of: str = ""
) -> SubscribersResult:
    """``GET /subscriptions`` → :class:`SubscribersResult`."""
    subscribers: list[Subscription] = []
    for record in _records(body, endpoint=endpoint):
        subscription = _build(Subscription, record, _SUBSCRIPTION_KEYS)
        user = record.get("user")
        if isinstance(user, Mapping):
            subscription = subscription.model_copy(update={"user": _build(User, user, _USER_KEYS)})
        subscribers.append(subscription)
    point_name = body.get("point_name") if isinstance(body, Mapping) else None
    return SubscribersResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        point_name=point_name if isinstance(point_name, str) else None,
        subscribers=subscribers,
        pagination=_pagination(body),
    )


def project_editors(
    body: Any, *, endpoint: str = "GET /channels/editors", as_of: str = ""
) -> EditorsResult:
    """``GET /channels/editors`` → :class:`EditorsResult`. Also the ``channel`` roster (R29)."""
    return EditorsResult(
        meta=_meta(endpoint, as_of),
        editors=[_build(Editor, r, _EDITOR_KEYS) for r in _records(body, endpoint=endpoint)],
    )


def project_moderators(
    body: Any, *, endpoint: str = "GET /moderation/moderators", as_of: str = ""
) -> ModeratorsResult:
    """``GET /moderation/moderators`` → :class:`ModeratorsResult`. The moderation roster (R29)."""
    return ModeratorsResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        moderators=[
            _build(Moderator, r, _MODERATOR_KEYS) for r in _records(body, endpoint=endpoint)
        ],
        pagination=_pagination(body),
    )


def project_banned_users(
    body: Any, *, endpoint: str = "GET /moderation/banned", as_of: str = ""
) -> BannedUsersResult:
    """``GET /moderation/banned`` → :class:`BannedUsersResult`."""
    return BannedUsersResult(
        meta=_meta(endpoint, as_of),
        total=_total(body),
        banned_users=[
            _build(BannedUser, r, _BANNED_KEYS) for r in _records(body, endpoint=endpoint)
        ],
        pagination=_pagination(body),
    )


def project_automod_settings(
    body: Any, *, endpoint: str = "GET /moderation/automod/settings", as_of: str = ""
) -> AutomodSettingsResult:
    """``GET /moderation/automod/settings`` → :class:`AutomodSettingsResult`.

    Helix returns a single-element ``data`` array whose one record carries the levels as plain
    integer fields (``overall_level`` plus one per category). There is no nested ``settings`` list:
    reading for one would answer "no settings" to every real response, and R45 forbids an empty
    result that does not actually mean empty.
    """
    records = _records(body, endpoint=endpoint)
    if not records:
        return AutomodSettingsResult(meta=_meta(endpoint, as_of), settings=None)
    record = records[0]
    # Every field of AutomodSettings is optional, so a record that carries none of them -- the old
    # nested-``settings`` shape this ruling replaced, or any body this server does not understand --
    # would project to a successful-looking record of all-absent levels. G5/R45 forbid an empty
    # result that does not actually mean empty, so the shape is checked before it is laundered.
    recognized = [key for key in _AUTOMOD_LEVEL_KEYS if record.get(key) is not None]
    if not recognized:
        raise TwitchMcpError(
            "upstream",
            "Twitch returned a moderation-settings body this server does not understand",
            hint="This is not an empty result: retry, and report it if it repeats.",
        )
    return AutomodSettingsResult(
        meta=_meta(endpoint, as_of),
        settings=_build(AutomodSettings, record, _AUTOMOD_SETTINGS_KEYS),
    )


def project_chatters(
    body: Any, *, endpoint: str = "GET /chat/chatters", as_of: str = ""
) -> ChattersResult:
    """``GET /chat/chatters`` → :class:`ChattersResult`, the flat user list Helix documents.

    Roles are not this endpoint's business: Helix returns ``user_id`` / ``user_login`` /
    ``user_name`` per entry and tells callers to ask ``Get Moderators`` and ``Get VIPs`` instead,
    so this projection never invents role buckets from a field that is not there.
    """
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, list):
        raise TwitchMcpError(
            "upstream",
            "Twitch returned a response body this server does not understand",
            hint="This is not an empty result: retry, and report it if it repeats.",
        )

    count = body.get("total")
    return ChattersResult(
        meta=_meta(endpoint, as_of),
        chatter_count=count if isinstance(count, int) and not isinstance(count, bool) else None,
        chatters=[
            _build(Chatter, entry, _CHATTER_KEYS) for entry in data if isinstance(entry, Mapping)
        ],
        pagination=_pagination(body),
    )


def project_schedule(
    body: Any, *, endpoint: str = "GET /schedule", as_of: str = ""
) -> ScheduleResult:
    """``GET /schedule`` → :class:`ScheduleResult`, with the schedule read out of ``data``.

    ``data`` is the one place Helix returns a **single object** rather than an array: it holds
    ``segments`` plus the broadcaster fields. A body shaped any other way is genuinely unreadable
    here, so it is an ``upstream`` failure and never an empty schedule (R42/G5).
    """
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, Mapping) or "broadcaster_id" not in data:
        raise TwitchMcpError(
            "upstream",
            "Twitch returned a response body this server does not understand",
            hint="This is not an empty result: retry, and report it if it repeats.",
        )
    schedule = _build(Schedule, data, _SCHEDULE_KEYS)
    segments = data.get("segments")
    if isinstance(segments, list):
        schedule = schedule.model_copy(
            update={
                "segments": [
                    _build(ScheduleSegment, s, _SEGMENT_KEYS)
                    for s in segments
                    if isinstance(s, Mapping)
                ]
            }
        )
    return ScheduleResult(
        meta=_meta(endpoint, as_of), schedule=schedule, pagination=_pagination(body)
    )


def project_token_validation(
    body: Any, *, endpoint: str = "GET /oauth2/validate", as_of: str = ""
) -> TokenValidation:
    """``GET /oauth2/validate`` → :class:`TokenValidation`. Scope names only (R52)."""
    if not isinstance(body, Mapping):
        raise TwitchMcpError(
            "upstream",
            "Twitch returned a response body this server does not understand",
            hint="This is not an empty result: retry, and report it if it repeats.",
        )
    return _build(TokenValidation, body, _VALIDATE_KEYS)


def project_write_data(body: Any) -> list[Any]:
    """A write response's ``data`` array, copied verbatim (R38: the caller declares the shape).

    A write's payload is Helix's own record for the object it just changed. It is returned as plain
    JSON rather than a model because its fields belong to the endpoint, and the R41 size test bounds
    it; nothing here invents a schema for it. The return is annotated ``list[Any]`` (not
    ``list[dict[str, Any]]``) because the frozen R38 test pins the raw return annotation to the
    plain-records form; the elements are ``dict[str, Any]`` records built below.
    """
    if isinstance(body, Mapping):
        data = body.get("data")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        return []
    return []


def _total(body: Any) -> int | None:
    """Helix's ``total`` when the endpoint publishes one; omitted otherwise (R38)."""
    if isinstance(body, Mapping):
        value = body.get("total")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


__all__ = [
    "Endpoint",
    "has_more",
    "now_iso",
    "project_automod_settings",
    "project_banned_users",
    "project_channels",
    "project_chat_badges",
    "project_chat_settings",
    "project_chatters",
    "project_clips",
    "project_editors",
    "project_emotes",
    "project_follows",
    "project_games",
    "project_markers",
    "project_moderators",
    "project_predictions",
    "project_polls",
    "project_schedule",
    "project_search_channels",
    "project_streams",
    "project_subscribers",
    "project_token_validation",
    "project_users",
    "project_videos",
    "project_write_data",
]
