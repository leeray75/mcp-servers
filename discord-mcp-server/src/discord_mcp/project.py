"""Raw Discord JSON to projected models (section 8, A1 projections, R25-R27, R34).

Only the fields listed in section 8 survive. Snowflakes are kept as strings (numbers returned by
Discord are stringified). CDN URLs (attachments, avatars) are never copied. Fields that do not apply
to a tool are left unset so they are omitted from the output (A1-5); every base field is always set.
Malformed bodies (not an object, or no ``id``) raise ``kind=upstream`` (review m5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from discord_mcp.errors import DiscordMcpError
from discord_mcp.models import (
    Attachment,
    AuditChange,
    AuditEntry,
    Channel,
    Embed,
    EmbedField,
    Guild,
    GuildSummary,
    Member,
    Message,
    Pin,
    Poll,
    PollAnswer,
    Reaction,
    Role,
    ThreadInfo,
    UserSummary,
)
from discord_mcp.snowflake import discord_timestamp, snowflake_created_at, to_iso

# Channel types: https://docs.discord.com/developers/resources/channel (verified 2026-10-28, A1).
CHANNEL_TYPES: Final[dict[int, str]] = {
    0: "text",
    2: "voice",
    4: "category",
    5: "announcement",
    10: "announcement_thread",
    11: "public_thread",
    12: "private_thread",
    13: "stage",
    14: "directory",
    15: "forum",
    16: "media",
}
THREAD_TYPES: Final = frozenset({10, 11, 12})

# Audit log event names: https://docs.discord.com/developers/resources/audit-log
# Transcribed from the "Audit Log Events" table (fetched 2026-10-29, A1-12).
# 69 events, values 1-193.
# A test asserts every entry; unknown values map to null.
AUDIT_ACTIONS: Final[dict[int, str]] = {
    1: "GUILD_UPDATE",
    10: "CHANNEL_CREATE",
    11: "CHANNEL_UPDATE",
    12: "CHANNEL_DELETE",
    13: "CHANNEL_OVERWRITE_CREATE",
    14: "CHANNEL_OVERWRITE_UPDATE",
    15: "CHANNEL_OVERWRITE_DELETE",
    20: "MEMBER_KICK",
    21: "MEMBER_PRUNE",
    22: "MEMBER_BAN_ADD",
    23: "MEMBER_BAN_REMOVE",
    24: "MEMBER_UPDATE",
    25: "MEMBER_ROLE_UPDATE",
    26: "MEMBER_MOVE",
    27: "MEMBER_DISCONNECT",
    28: "BOT_ADD",
    30: "ROLE_CREATE",
    31: "ROLE_UPDATE",
    32: "ROLE_DELETE",
    40: "INVITE_CREATE",
    41: "INVITE_UPDATE",
    42: "INVITE_DELETE",
    50: "WEBHOOK_CREATE",
    51: "WEBHOOK_UPDATE",
    52: "WEBHOOK_DELETE",
    60: "EMOJI_CREATE",
    61: "EMOJI_UPDATE",
    62: "EMOJI_DELETE",
    72: "MESSAGE_DELETE",
    73: "MESSAGE_BULK_DELETE",
    74: "MESSAGE_PIN",
    75: "MESSAGE_UNPIN",
    80: "INTEGRATION_CREATE",
    81: "INTEGRATION_UPDATE",
    82: "INTEGRATION_DELETE",
    83: "STAGE_INSTANCE_CREATE",
    84: "STAGE_INSTANCE_UPDATE",
    85: "STAGE_INSTANCE_DELETE",
    90: "STICKER_CREATE",
    91: "STICKER_UPDATE",
    92: "STICKER_DELETE",
    100: "GUILD_SCHEDULED_EVENT_CREATE",
    101: "GUILD_SCHEDULED_EVENT_UPDATE",
    102: "GUILD_SCHEDULED_EVENT_DELETE",
    110: "THREAD_CREATE",
    111: "THREAD_UPDATE",
    112: "THREAD_DELETE",
    121: "APPLICATION_COMMAND_PERMISSION_UPDATE",
    130: "SOUNDBOARD_SOUND_CREATE",
    131: "SOUNDBOARD_SOUND_UPDATE",
    132: "SOUNDBOARD_SOUND_DELETE",
    140: "AUTO_MODERATION_RULE_CREATE",
    141: "AUTO_MODERATION_RULE_UPDATE",
    142: "AUTO_MODERATION_RULE_DELETE",
    143: "AUTO_MODERATION_BLOCK_MESSAGE",
    144: "AUTO_MODERATION_FLAG_TO_CHANNEL",
    145: "AUTO_MODERATION_USER_COMMUNICATION_DISABLED",
    146: "AUTO_MODERATION_QUARANTINE_USER",
    150: "CREATOR_MONETIZATION_REQUEST_CREATED",
    151: "CREATOR_MONETIZATION_TERMS_ACCEPTED",
    163: "ONBOARDING_PROMPT_CREATE",
    164: "ONBOARDING_PROMPT_UPDATE",
    165: "ONBOARDING_PROMPT_DELETE",
    166: "ONBOARDING_CREATE",
    167: "ONBOARDING_UPDATE",
    190: "HOME_SETTINGS_CREATE",
    191: "HOME_SETTINGS_UPDATE",
    192: "VOICE_CHANNEL_STATUS_CREATE",
    193: "VOICE_CHANNEL_STATUS_DELETE",
}


def _dict_of(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unexpected(what: str) -> DiscordMcpError:
    return DiscordMcpError("upstream", f"Discord returned an unexpected {what} response")


def _require_object(raw: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(raw, dict) or raw.get("id") is None:
        raise _unexpected(what)
    return raw


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v) for v in values if v is not None]


def _dicts(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    return [v for v in values if isinstance(v, dict)]


def color_hex(value: Any) -> str | None:
    """Role colour as ``#rrggbb``; a zero colour (no colour) is null."""
    number = _int_or_none(value)
    if not number:
        return None
    return f"#{number & 0xFFFFFF:06x}"


def channel_type_name(type_id: int | None) -> str:
    if type_id is None:
        return "unknown"
    return CHANNEL_TYPES.get(type_id, "unknown")


def _created_at(snowflake: str) -> str:
    return to_iso(snowflake_created_at(snowflake))


def user_summary(raw: Any) -> UserSummary | None:
    if not isinstance(raw, dict) or raw.get("id") is None:
        return None
    return UserSummary(
        id=str(raw["id"]),
        username=_str_or_none(raw.get("username")),
        global_name=_str_or_none(raw.get("global_name")),
        bot=raw.get("bot") is True,
    )


def role(raw: Mapping[str, Any]) -> Role:
    return Role(
        id=str(raw["id"]),
        name=_str_or_none(raw.get("name")),
        position=_int_or_none(raw.get("position")),
        color=color_hex(raw.get("color")),
        permissions=_str_or_none(raw.get("permissions")),
        managed=_bool_or_none(raw.get("managed")),
        hoist=_bool_or_none(raw.get("hoist")),
        mentionable=_bool_or_none(raw.get("mentionable")),
    )


def guild_summary(raw: Any) -> GuildSummary:
    obj = _require_object(raw, "guild")
    guild_id = str(obj["id"])
    return GuildSummary(
        id=guild_id,
        name=_str_or_none(obj.get("name")),
        owner=_bool_or_none(obj.get("owner")),
        approximate_member_count=_int_or_none(obj.get("approximate_member_count")),
        approximate_presence_count=_int_or_none(obj.get("approximate_presence_count")),
        created_at=_created_at(guild_id),
    )


def guild(raw: Any) -> Guild:
    obj = _require_object(raw, "guild")
    guild_id = str(obj["id"])
    roles = sorted(
        (role(r) for r in _dicts(obj.get("roles")) if r.get("id") is not None),
        key=lambda r: -(r.position or 0),
    )
    return Guild(
        id=guild_id,
        name=_str_or_none(obj.get("name")),
        description=_str_or_none(obj.get("description")),
        owner_id=_str_or_none(obj.get("owner_id")),
        approximate_member_count=_int_or_none(obj.get("approximate_member_count")),
        approximate_presence_count=_int_or_none(obj.get("approximate_presence_count")),
        verification_level=_int_or_none(obj.get("verification_level")),
        premium_tier=_int_or_none(obj.get("premium_tier")),
        premium_subscription_count=_int_or_none(obj.get("premium_subscription_count")),
        preferred_locale=_str_or_none(obj.get("preferred_locale")),
        features=_str_list(obj.get("features")),
        emoji_count=len(_dicts(obj.get("emojis"))),
        created_at=_created_at(guild_id),
        roles=roles,
    )


def _thread_info(raw: Mapping[str, Any]) -> ThreadInfo:
    meta = _dict_of(raw.get("thread_metadata"))
    return ThreadInfo(
        archived=_bool_or_none(meta.get("archived")),
        locked=_bool_or_none(meta.get("locked")),
        archive_timestamp=discord_timestamp(meta.get("archive_timestamp")),
        auto_archive_duration=_int_or_none(meta.get("auto_archive_duration")),
        owner_id=_str_or_none(raw.get("owner_id")),
        message_count=_int_or_none(raw.get("message_count")),
        member_count=_int_or_none(raw.get("member_count")),
    )


def channel(raw: Any, **applicable: bool) -> Channel:
    """Project a channel. ``applicable`` holds only the per-tool flags that apply (A1-5)."""
    obj = _require_object(raw, "channel")
    channel_id = str(obj["id"])
    type_id = _int_or_none(obj.get("type"))
    fields: dict[str, Any] = {
        "id": channel_id,
        "guild_id": _str_or_none(obj.get("guild_id")),
        "name": _str_or_none(obj.get("name")),
        "type": channel_type_name(type_id),
        "type_id": type_id,
        "parent_id": _str_or_none(obj.get("parent_id")),
        "position": _int_or_none(obj.get("position")),
        "topic": _str_or_none(obj.get("topic")),
        "nsfw": _bool_or_none(obj.get("nsfw")),
        "rate_limit_per_user": _int_or_none(obj.get("rate_limit_per_user")),
        "last_message_id": _str_or_none(obj.get("last_message_id")),
        "created_at": _created_at(channel_id),
    }
    if type_id in THREAD_TYPES:
        fields["thread"] = _thread_info(obj)
    fields.update(applicable)
    return Channel(**fields)


def _embed(raw: Mapping[str, Any]) -> Embed:
    return Embed(
        type=_str_or_none(raw.get("type")),
        title=_str_or_none(raw.get("title")),
        description=_str_or_none(raw.get("description")),
        url=_str_or_none(raw.get("url")),
        fields=[
            EmbedField(name=_str_or_none(f.get("name")), value=_str_or_none(f.get("value")))
            for f in _dicts(raw.get("fields"))
        ],
    )


def _reaction(raw: Mapping[str, Any]) -> Reaction:
    emoji = _dict_of(raw.get("emoji"))
    name = _str_or_none(emoji.get("name")) or ""
    emoji_id = _str_or_none(emoji.get("id"))
    return Reaction(
        emoji=f"{name}:{emoji_id}" if emoji_id else name,
        count=_int_or_none(raw.get("count")),
    )


def _poll(raw: Any) -> Poll | None:
    if not isinstance(raw, dict):
        return None
    question = _dict_of(raw.get("question"))
    results_raw = raw.get("results")
    results = results_raw if isinstance(results_raw, dict) else None
    counts: dict[str, int] = {}
    if results is not None:
        for entry in _dicts(results.get("answer_counts")):
            count = _int_or_none(entry.get("count"))
            if entry.get("id") is not None and count is not None:
                counts[str(entry["id"])] = count
    answers: list[PollAnswer] = []
    for answer in _dicts(raw.get("answers")):
        media = _dict_of(answer.get("poll_media"))
        answer_id = _str_or_none(answer.get("answer_id"))
        answers.append(
            PollAnswer(
                answer_id=answer_id,
                text=_str_or_none(media.get("text")),
                votes=counts.get(answer_id) if answer_id is not None and results else None,
            )
        )
    return Poll(
        question=_str_or_none(question.get("text")),
        answers=answers,
        is_finalized=_bool_or_none(results.get("is_finalized")) if results is not None else None,
    )


def _sticker_names(raw: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("sticker_items", "stickers"):
        for sticker in _dicts(raw.get(key)):
            name = sticker.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def message(raw: Any, *, search: bool = False) -> Message:
    """Project a message. Search results carry no reactions upstream: null (A1-10)."""
    obj = _require_object(raw, "message")
    reference = _dict_of(obj.get("message_reference"))
    thread = _dict_of(obj.get("thread"))
    return Message(
        id=str(obj["id"]),
        channel_id=str(obj.get("channel_id", "")),
        author=user_summary(obj.get("author")),
        timestamp=discord_timestamp(obj.get("timestamp")),
        edited_timestamp=discord_timestamp(obj.get("edited_timestamp")),
        type=_int_or_none(obj.get("type")),
        content=_str_or_none(obj.get("content")),
        pinned=_bool_or_none(obj.get("pinned")),
        mention_everyone=_bool_or_none(obj.get("mention_everyone")),
        mentions=[u for u in (user_summary(m) for m in _dicts(obj.get("mentions"))) if u],
        mention_role_ids=_str_list(obj.get("mention_roles")),
        attachments=[
            Attachment(
                id=str(a.get("id", "")),
                filename=_str_or_none(a.get("filename")),
                content_type=_str_or_none(a.get("content_type")),
                size=_int_or_none(a.get("size")),
                width=_int_or_none(a.get("width")),
                height=_int_or_none(a.get("height")),
            )
            for a in _dicts(obj.get("attachments"))
        ],
        embeds=[_embed(e) for e in _dicts(obj.get("embeds"))],
        reactions=None if search else [_reaction(r) for r in _dicts(obj.get("reactions"))],
        reply_to_message_id=_str_or_none(reference.get("message_id")),
        thread_id=_str_or_none(thread.get("id")),
        poll=_poll(obj.get("poll")),
        sticker_names=_sticker_names(obj),
        flags=_int_or_none(obj.get("flags")),
    )


def member(raw: Any) -> Member:
    obj = _dict_of(raw)
    return Member(
        user=user_summary(obj.get("user")),
        nick=_str_or_none(obj.get("nick")),
        role_ids=_str_list(obj.get("roles")),
        joined_at=discord_timestamp(obj.get("joined_at")),
        premium_since=discord_timestamp(obj.get("premium_since")),
        communication_disabled_until=discord_timestamp(obj.get("communication_disabled_until")),
        pending=_bool_or_none(obj.get("pending")),
    )


def pin(raw: Any) -> Pin:
    obj = _dict_of(raw)
    if not isinstance(obj.get("message"), dict):
        raise _unexpected("pin")
    return Pin(pinned_at=discord_timestamp(obj.get("pinned_at")), message=message(obj["message"]))


def audit_entry(raw: Any, users: Iterable[Mapping[str, Any]]) -> AuditEntry:
    obj = _require_object(raw, "audit log entry")
    entry_id = str(obj["id"])
    action_type = _int_or_none(obj.get("action_type"))
    if action_type is None:
        raise _unexpected("audit log entry")
    user_id = _str_or_none(obj.get("user_id"))
    names = {str(u["id"]): _str_or_none(u.get("username")) for u in users if u.get("id")}
    options = obj.get("options") if isinstance(obj.get("options"), dict) else None
    return AuditEntry(
        id=entry_id,
        action_type=action_type,
        action=AUDIT_ACTIONS.get(action_type),
        user_id=user_id,
        user_name=names.get(user_id) if user_id else None,
        target_id=_str_or_none(obj.get("target_id")),
        reason=_str_or_none(obj.get("reason")),
        changes=[
            AuditChange(
                key=_str_or_none(c.get("key")),
                old_value=c.get("old_value"),
                new_value=c.get("new_value"),
            )
            for c in _dicts(obj.get("changes"))
        ],
        options=options,
        created_at=_created_at(entry_id),
    )
