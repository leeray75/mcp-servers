"""Pydantic result models (section 8, R35). Each tool result is one of these, so ``outputSchema``
is generated from it. Field lists are exhaustive: anything not declared here is dropped by
``project.py``. Ids are strings (R25). Missing upstream values are null.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Meta(BaseModel):
    """Freshness and provenance of a result (R35)."""

    as_of: str
    cached: bool
    cache_age_seconds: int
    endpoint: str


class UserSummary(BaseModel):
    id: str
    username: str | None
    global_name: str | None
    bot: bool


class Role(BaseModel):
    id: str
    name: str | None
    position: int | None
    color: str | None
    permissions: str | None
    managed: bool | None
    hoist: bool | None
    mentionable: bool | None


class GuildSummary(BaseModel):
    id: str
    name: str | None
    owner: bool | None
    approximate_member_count: int | None
    approximate_presence_count: int | None
    created_at: str


class Guild(BaseModel):
    id: str
    name: str | None
    description: str | None
    owner_id: str | None
    approximate_member_count: int | None
    approximate_presence_count: int | None
    verification_level: int | None
    premium_tier: int | None
    premium_subscription_count: int | None
    preferred_locale: str | None
    features: list[str]
    emoji_count: int
    created_at: str
    roles: list[Role]


class ThreadInfo(BaseModel):
    archived: bool | None
    locked: bool | None
    archive_timestamp: str | None
    auto_archive_duration: int | None
    owner_id: str | None
    message_count: int | None
    member_count: int | None


class Channel(BaseModel):
    id: str
    guild_id: str | None
    name: str | None
    type: str
    type_id: int | None
    parent_id: str | None
    position: int | None
    topic: str | None
    nsfw: bool | None
    rate_limit_per_user: int | None
    last_message_id: str | None
    created_at: str
    bot_can_view: bool | None = None
    bot_can_read_history: bool | None = None
    thread: ThreadInfo | None = None
    bot_joined: bool | None = None


class Attachment(BaseModel):
    id: str
    filename: str | None
    content_type: str | None
    size: int | None
    width: int | None
    height: int | None


class EmbedField(BaseModel):
    name: str | None
    value: str | None


class Embed(BaseModel):
    type: str | None
    title: str | None
    description: str | None
    url: str | None
    fields: list[EmbedField]


class Reaction(BaseModel):
    emoji: str
    count: int | None


class PollAnswer(BaseModel):
    answer_id: str | None
    text: str | None
    votes: int | None


class Poll(BaseModel):
    question: str | None
    answers: list[PollAnswer]
    is_finalized: bool | None


class Message(BaseModel):
    id: str
    channel_id: str
    author: UserSummary | None
    timestamp: str | None
    edited_timestamp: str | None
    type: int | None
    content: str | None
    pinned: bool | None
    mention_everyone: bool | None
    mentions: list[UserSummary]
    mention_role_ids: list[str]
    attachments: list[Attachment]
    embeds: list[Embed]
    reactions: list[Reaction] | None
    reply_to_message_id: str | None
    thread_id: str | None
    poll: Poll | None
    sticker_names: list[str]
    flags: int | None


class Member(BaseModel):
    user: UserSummary | None
    nick: str | None
    role_ids: list[str]
    joined_at: str | None
    premium_since: str | None
    communication_disabled_until: str | None
    pending: bool | None


class Pin(BaseModel):
    pinned_at: str | None
    message: Message


class AuditChange(BaseModel):
    key: str | None
    old_value: Any
    new_value: Any


class AuditEntry(BaseModel):
    id: str
    action_type: int
    action: str | None
    user_id: str | None
    user_name: str | None
    target_id: str | None
    reason: str | None
    changes: list[AuditChange]
    options: dict[str, Any] | None
    created_at: str


# --- tool results -----------------------------------------------------------------------------


class ListGuildsResult(BaseModel):
    meta: Meta
    guilds: list[GuildSummary]
    missing_allowlisted_guild_ids: list[str]


class GuildResult(Guild):
    meta: Meta


class ChannelListResult(BaseModel):
    meta: Meta
    channels: list[Channel]


class ChannelResult(Channel):
    meta: Meta


class MessageListResult(BaseModel):
    meta: Meta
    messages: list[Message]
    count: int
    has_more: bool
    next_cursor: dict[str, str] | None


class MessageResult(Message):
    meta: Meta


class SearchResult(BaseModel):
    meta: Meta
    messages: list[Message]
    total_results: int
    offset: int
    has_more: bool
    next_offset: int | None


class ReactionListResult(BaseModel):
    meta: Meta
    users: list[UserSummary]
    has_more: bool
    next_cursor: dict[str, str] | None


class PinListResult(BaseModel):
    meta: Meta
    pins: list[Pin]
    has_more: bool
    next_cursor: dict[str, str] | None


class ThreadListResult(BaseModel):
    meta: Meta
    threads: list[Channel]


class MemberListResult(BaseModel):
    meta: Meta
    members: list[Member]
    has_more: bool
    next_cursor: dict[str, str] | None


class MemberResult(Member):
    meta: Meta


class MemberSearchResult(BaseModel):
    meta: Meta
    members: list[Member]


class AuditLogResult(BaseModel):
    meta: Meta
    entries: list[AuditEntry]
    order: str
    has_more: bool
    next_cursor: dict[str, str] | None


# --- user OAuth guild discovery (A3, U16-U21) -------------------------------------------------
# These models use the same exclude_unset rule as the rest: a key the projection never set is
# omitted from the result, not sent as null.


class AccountStatusResult(BaseModel):
    enabled: bool
    state: str
    user_id: str | None = None
    username: str | None = None
    scopes: list[str] | None = None
    expires_at: str | None = None
    connected_at: str | None = None
    pending_sign_in: bool
    last_error: str | None
    sign_in_url: str
    redirect_uri: str


class OpenCommands(BaseModel):
    windows: str
    macos: str
    linux: str


class ConnectResult(BaseModel):
    state: str
    username: str | None = None
    sign_in_url: str
    redirect_uri: str | None = None
    instructions: str | None = None
    open_commands: OpenCommands | None = None


class DisconnectResult(BaseModel):
    state: str
    revoked: bool | None = None


class MyGuild(BaseModel):
    id: str
    name: str | None
    icon: str | None
    owner: bool
    permissions: str
    features: list[str]
    approximate_member_count: int | None = None
    approximate_presence_count: int | None = None
    allowlisted: bool
    bot_present: bool | None
    can_add_bot: bool


class ListMyGuildsResult(BaseModel):
    guilds: list[MyGuild]
    count: int
    truncated: bool
    bot_guilds_known: bool


class InviteLinkResult(BaseModel):
    guild_id: str
    url: str
    note: str
