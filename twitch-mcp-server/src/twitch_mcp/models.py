"""Pydantic result models for every tool in SPEC §6.4 (23 reads, 2 account, 16 writes).

Each tool returns ``Annotated[CallToolResult, <Model>]`` so ``outputSchema`` is derived from the
model it names (R38). Two rules govern these classes:

1. **Projection, not passthrough.** ``project.py`` copies raw Helix JSON into these models; anything
   not declared here is dropped. Helix's URL fields carry tracking templates and query strings and
   are deliberately absent (R52).
2. **Unset keys are omitted, never emitted as null** (R38). The server dumps results with
   ``model_dump(mode="json", exclude_unset=True)``, so a key the projection never set does
   not appear at all. Optional fields are therefore declared with a default of ``None`` — a
   field that is *required* here is one the projection always fills.

The envelope (:class:`Meta`, :class:`Pagination`) is shared by every result.
``pagination.cursor`` of ``{}`` means no further page and is reported as ``has_more=False``
(R42).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Result(BaseModel):
    """Base for every tool result: unknown keys are rejected, unset keys are omitted by the server.

    Every field is optional with a default so a projection that cannot fill a field simply omits it
    (R38) instead of failing or emitting null.
    """

    model_config = ConfigDict(extra="forbid")


# --- envelope ------------------------------------------------------------------------------------


class Meta(_Result):
    """Freshness and provenance of a result. Route templates only, never a URL (R52/R53)."""

    as_of: str = ""
    cached: bool = False
    cache_age_seconds: int = 0
    endpoint: str = ""


class Pagination(_Result):
    """Helix cursor state. An empty ``cursor`` means the end of the list (R42)."""

    cursor: dict[str, str] = Field(default_factory=dict)


# --- shared entities -----------------------------------------------------------------------------


class User(_Result):
    id: str = ""
    login: str | None = None
    display_name: str | None = None
    user_type: str | None = None
    broadcaster_type: str | None = None
    description: str | None = None
    profile_image_url: str | None = None
    offline_image_url: str | None = None
    view_count: int | None = None
    email: str | None = None
    created_at: str | None = None


class Follow(_Result):
    from_id: str | None = None
    from_name: str | None = None
    to_id: str | None = None
    to_name: str | None = None
    followed_at: str | None = None


class Channel(_Result):
    broadcaster_id: str = ""
    broadcaster_login: str | None = None
    broadcaster_name: str | None = None
    broadcaster_language: str | None = None
    game_id: str | None = None
    game_name: str | None = None
    title: str | None = None
    delay: int | None = None
    tags: list[str] | None = None
    content_classification_labels: list[str] | None = None
    is_branded_content: bool | None = None


class SearchResult(_Result):
    id: str = ""
    broadcaster_login: str | None = None
    display_name: str | None = None
    game_id: str | None = None
    game_name: str | None = None
    language: str | None = None
    title: str | None = None
    thumbnail_url: str | None = None
    is_live: bool | None = None
    tags: list[str] | None = None


class Stream(_Result):
    id: str | None = None
    user_id: str = ""
    user_login: str | None = None
    user_name: str | None = None
    game_id: str | None = None
    game_name: str | None = None
    type: str | None = None
    title: str | None = None
    viewer_count: int | None = None
    started_at: str | None = None
    language: str | None = None
    is_mature: bool | None = None
    tags: list[str] | None = None


class Game(_Result):
    id: str = ""
    name: str | None = None
    box_art_url: str | None = None


class Video(_Result):
    id: str = ""
    user_id: str | None = None
    user_login: str | None = None
    user_name: str | None = None
    title: str | None = None
    description: str | None = None
    created_at: str | None = None
    published_at: str | None = None
    duration: str | None = None
    period: str | None = None
    view_count: int | None = None
    language: str | None = None
    viewable: str | None = None
    type: str | None = None


class Clip(_Result):
    id: str = ""
    broadcaster_id: str | None = None
    broadcaster_name: str | None = None
    creator_id: str | None = None
    creator_name: str | None = None
    video_id: str | None = None
    game_id: str | None = None
    language: str | None = None
    title: str | None = None
    duration: float | None = None
    created_at: str | None = None
    view_count: int | None = None


class PollChoice(_Result):
    id: str | None = None
    title: str | None = None
    votes: int | None = None
    channel_points_voting: int | None = None


class Poll(_Result):
    id: str = ""
    broadcaster_id: str | None = None
    broadcaster_name: str | None = None
    title: str | None = None
    status: str | None = None
    duration: int | None = None
    started_at: str | None = None
    ended_at: str | None = None
    channel_points_betting: bool | None = None
    bits_betting: bool | None = None
    channel_points_voting: bool | None = None
    choices: list[PollChoice] | None = None


class PredictionOutcome(_Result):
    id: str | None = None
    title: str | None = None
    users: int | None = None
    channel_points: int | None = None
    color: str | None = None


class Prediction(_Result):
    id: str = ""
    broadcaster_id: str | None = None
    broadcaster_name: str | None = None
    title: str | None = None
    status: str | None = None
    winning_outcome_id: str | None = None
    prediction_window: int | None = None
    created_at: str | None = None
    ended_at: str | None = None
    outcomes: list[PredictionOutcome] | None = None


class Emote(_Result):
    id: str = ""
    name: str | None = None
    images: dict[str, str] | None = None
    format: str | None = None
    scale: list[str] | None = None
    theme_mode: list[str] | None = None


class EmoteSet(_Result):
    id: str = ""
    emote_set_id: str | None = None
    owner_id: str | None = None
    owner_name: str | None = None
    scope: str | None = None
    emotes: list[Emote] | None = None


class BadgeVersion(_Result):
    id: str | None = None
    title: str | None = None
    image: str | None = None
    description: str | None = None


class BadgeSet(_Result):
    set_id: str = ""
    versions: list[BadgeVersion] | None = None


class ChatSettings(_Result):
    room_id: str = ""
    room_name: str | None = None
    emitter_enabled: list[str] | None = None
    slow_mode: int | None = None
    slow_mode_wait_time: int | None = None
    follower_mode: int | None = None
    follower_mode_duration: int | None = None
    subscriber_mode: bool | None = None
    emote_mode: bool | None = None
    unique_chat_mode: bool | None = None
    non_animated_emotes: list[str] | None = None


class StreamMarker(_Result):
    id: str | None = None
    created_at: str | None = None
    description: str | None = None
    position_seconds: int | None = None
    video_id: str | None = None


class Marker(_Result):
    video_id: str | None = None
    markers: list[StreamMarker] | None = None


class Subscription(_Result):
    tier: str | None = None
    is_gift: bool | None = None
    gifter_id: str | None = None
    gifter_login: str | None = None
    gifter_name: str | None = None
    user: User | None = None


class Editor(_Result):
    user_id: str | None = None
    user_login: str | None = None
    user_name: str | None = None
    created_at: str | None = None


class Moderator(_Result):
    user_id: str | None = None
    user_login: str | None = None
    user_name: str | None = None
    created_at: str | None = None


class BannedUser(_Result):
    user_id: str | None = None
    user_login: str | None = None
    user_name: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    reason: str | None = None
    moderator: str | None = None


class AutomodSettings(_Result):
    """``GET /moderation/automod/settings`` returns **one record** of integer levels, not a list.

    Helix documents these levels as numbers on the record itself (``overall_level`` plus one field
    per category). There is no nested ``settings`` array on this endpoint, and a projection that
    looked for one would silently report "no settings" for every real response -- which R45 forbids,
    because an empty result has to mean genuinely empty.
    """

    broadcaster_id: str | None = None
    moderator_id: str | None = None
    overall_level: int | None = None
    disability: int | None = None
    aggression: int | None = None
    sexuality_sex_or_gender: int | None = None
    misogyny: int | None = None
    bullying: int | None = None
    swearing: int | None = None
    race_ethnicity_or_religion: int | None = None
    sex_based_terms: int | None = None


class Chatter(_Result):
    user_id: str | None = None
    user_login: str | None = None
    user_name: str | None = None


class ScheduleCategory(_Result):
    """A schedule segment's ``category`` is a nested ``{id, name}`` object, not two flat fields."""

    id: str | None = None
    name: str | None = None


class ScheduleSegment(_Result):
    id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    title: str | None = None
    category: ScheduleCategory | None = None
    canceled_until: str | None = None
    tags: list[str] | None = None
    is_recurring: bool | None = None


class Schedule(_Result):
    broadcaster_id: str | None = None
    broadcaster_name: str | None = None
    broadcaster_login: str | None = None
    segments: list[ScheduleSegment] | None = None


# --- read results (23) ---------------------------------------------------------------------------


class TokenValidation(_Result):
    """One ``GET /oauth2/validate`` projection. Scopes are names; no token ever appears (R52)."""

    token_type: str | None = None
    client_id: str | None = None
    user_id: str | None = None
    user_login: str | None = None
    scopes: list[str] | None = None
    expires_in: int | None = None


class AccountStatusResult(_Result):
    """``twitch_account_status`` — the health of both identities and the effective tool plan."""

    meta: Meta | None = None
    app_token: TokenValidation | None = None
    user: TokenValidation | None = None
    user_oauth_enabled: bool | None = None
    user_connected: bool | None = None
    writes_enabled: bool | None = None
    enabled_write_groups: list[str] | None = None
    tool_count: int | None = None
    sign_in_url: str | None = None
    degraded: bool | None = None
    degraded_reason: str | None = None


class UsersResult(_Result):
    meta: Meta | None = None
    users: list[User] | None = None


class FollowsResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    follows: list[Follow] | None = None
    pagination: Pagination | None = None


class ChannelsResult(_Result):
    meta: Meta | None = None
    channels: list[Channel] | None = None


class SearchChannelsResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    channels: list[SearchResult] | None = None
    pagination: Pagination | None = None


class StreamsResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    streams: list[Stream] | None = None
    pagination: Pagination | None = None


class GamesResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    games: list[Game] | None = None
    pagination: Pagination | None = None


class VideosResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    videos: list[Video] | None = None
    pagination: Pagination | None = None


class ClipsResult(_Result):
    meta: Meta | None = None
    clips: list[Clip] | None = None
    pagination: Pagination | None = None


class PollsResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    polls: list[Poll] | None = None
    pagination: Pagination | None = None


class PredictionsResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    predictions: list[Prediction] | None = None
    pagination: Pagination | None = None


class EmotesResult(_Result):
    meta: Meta | None = None
    template: dict[str, str] | None = None
    emote_sets: list[EmoteSet] | None = None
    emotes: list[Emote] | None = None


class ChatBadgesResult(_Result):
    meta: Meta | None = None
    badge_sets: list[BadgeSet] | None = None


class ChatSettingsResult(_Result):
    meta: Meta | None = None
    settings: list[ChatSettings] | None = None


class MarkersResult(_Result):
    meta: Meta | None = None
    markers: list[Marker] | None = None
    pagination: Pagination | None = None


class SubscribersResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    point_name: str | None = None
    subscribers: list[Subscription] | None = None
    pagination: Pagination | None = None


class EditorsResult(_Result):
    meta: Meta | None = None
    editors: list[Editor] | None = None


class ModeratorsResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    moderators: list[Moderator] | None = None
    pagination: Pagination | None = None


class BannedUsersResult(_Result):
    meta: Meta | None = None
    total: int | None = None
    banned_users: list[BannedUser] | None = None
    pagination: Pagination | None = None


class AutomodSettingsResult(_Result):
    meta: Meta | None = None
    settings: AutomodSettings | None = None


class ChattersResult(_Result):
    """``GET /chat/chatters`` returns a **flat** list of connected users.

    Roles are a separate question for Helix: the endpoint documents only ``user_id``,
    ``user_login`` and ``user_name`` per entry and tells callers to ask ``Get Moderators`` /
    ``Get VIPs`` for roles, so this projection never invents ``moderators`` / ``viewers`` /
    ``vips`` / ``staff`` / ``global_banned`` buckets that the API stopped returning.
    """

    meta: Meta | None = None
    chatter_count: int | None = None
    chatters: list[Chatter] | None = None
    pagination: Pagination | None = None


class ScheduleResult(_Result):
    meta: Meta | None = None
    schedule: Schedule | None = None
    pagination: Pagination | None = None


# --- account results (2, only registered with TWITCH_USER_OAUTH=true) -----------------------------


class ConnectResult(_Result):
    """``connect_twitch_account`` — how to sign in. Carries no code, token or state value (R52)."""

    state_name: str | None = None
    sign_in_url: str | None = None
    redirect_uri: str | None = None
    scopes: list[str] | None = None
    expires_in_seconds: int | None = None
    instructions: str | None = None
    already_connected: bool | None = None


class DisconnectResult(_Result):
    """``disconnect_twitch_account`` — the store was cleared and the token revoked (R51)."""

    revoked: bool | None = None
    user_id: str | None = None
    message: str | None = None


# --- write results and confirmation (16 writes) ---------------------------------------------------


class WriteConfirmation(_Result):
    """The single-use token and the resolved plan presented before a write is dispatched (R31).

    ``confirm`` appears only in the result that minted it (R33). ``plan.arguments`` is the validated
    argument *summary* — never a chat message body or any other free-text value (R52/R53).
    """

    token: str | None = None
    expires_at: str | None = None
    tool: str | None = None
    broadcaster_id: str | None = None
    arguments: dict[str, Any] | None = None


class WriteResult(_Result):
    """Every write tool returns this envelope, exactly once, on a non-retried request (R16).

    ``confirmation_required`` is deliberately a *successful* (non-``isError``) result: it is the
    normal first turn of every write, and its plan is what the human reads before deciding (SPEC
    §6.3). A dispatched write reports ``dispatched=True`` with the first record Helix returned.
    """

    confirmation_required: bool = False
    confirmation: WriteConfirmation | None = None
    dispatched: bool | None = None
    data: list[dict[str, Any]] | None = None
    message: str | None = None


__all__ = [name for name in globals() if name.endswith("Result")]
