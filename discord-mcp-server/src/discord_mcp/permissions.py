"""Effective permission computation for the bot (R28-R29, section 10).

Pure functions over raw Discord JSON (guild with roles, bot member, channel overwrites). Permission
values are decimal strings in Discord JSON and are parsed as Python ints, never floats.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

VIEW_CHANNEL: Final = 1 << 10
READ_MESSAGE_HISTORY: Final = 1 << 16
VIEW_AUDIT_LOG: Final = 1 << 7
CONNECT: Final = 1 << 20
ADMINISTRATOR: Final = 1 << 3
ALL_PERMISSIONS: Final = (1 << 64) - 1

PERMISSION_NAMES: Final[Mapping[int, str]] = {
    VIEW_CHANNEL: "VIEW_CHANNEL",
    READ_MESSAGE_HISTORY: "READ_MESSAGE_HISTORY",
    VIEW_AUDIT_LOG: "VIEW_AUDIT_LOG",
    CONNECT: "CONNECT",
}

_ROLE_OVERWRITE: Final = 0
_MEMBER_OVERWRITE: Final = 1


def parse_permissions(value: Any) -> int:
    """A Discord permission bitfield (decimal string) as an int; anything else is 0."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _overwrite_pair(overwrite: Mapping[str, Any]) -> tuple[int, int]:
    return parse_permissions(overwrite.get("deny")), parse_permissions(overwrite.get("allow"))


def base_permissions(guild: Mapping[str, Any], member_role_ids: Iterable[str]) -> int:
    """Step 1: the ``@everyone`` role (id == guild id) OR the bot's roles."""
    guild_id = str(guild.get("id", ""))
    roles: dict[str, int] = {}
    for role in guild.get("roles") or []:
        if isinstance(role, dict) and isinstance(role.get("id"), str):
            roles[role["id"]] = parse_permissions(role.get("permissions"))
    base = roles.get(guild_id, 0)
    for role_id in member_role_ids:
        base |= roles.get(role_id, 0)
    return base


def effective_permissions(
    *,
    guild: Mapping[str, Any],
    member_role_ids: Iterable[str],
    bot_user_id: str,
    channel: Mapping[str, Any] | None = None,
) -> int:
    """Discord's documented algorithm (section 10).

    ``channel`` is the channel whose overwrites apply: for a thread, pass its parent channel.
    ``channel=None`` gives guild-level base permissions (used for VIEW_AUDIT_LOG).
    """
    role_ids = list(member_role_ids)
    base = base_permissions(guild, role_ids)
    if str(guild.get("owner_id", "")) == bot_user_id or base & ADMINISTRATOR:
        return ALL_PERMISSIONS
    if channel is None:
        return base

    guild_id = str(guild.get("id", ""))
    overwrites = [o for o in (channel.get("permission_overwrites") or []) if isinstance(o, dict)]
    perms = base

    everyone = [o for o in overwrites if o.get("id") == guild_id]
    for overwrite in everyone:
        deny, allow = _overwrite_pair(overwrite)
        perms = (perms & ~deny) | allow

    role_deny = 0
    role_allow = 0
    role_set = set(role_ids)
    for overwrite in overwrites:
        if overwrite.get("type") == _ROLE_OVERWRITE and overwrite.get("id") in role_set:
            deny, allow = _overwrite_pair(overwrite)
            role_deny |= deny
            role_allow |= allow
    perms = (perms & ~role_deny) | role_allow

    for overwrite in overwrites:
        if overwrite.get("type") == _MEMBER_OVERWRITE and overwrite.get("id") == bot_user_id:
            deny, allow = _overwrite_pair(overwrite)
            perms = (perms & ~deny) | allow

    if not perms & VIEW_CHANNEL:
        return 0  # step 4: without VIEW_CHANNEL every other permission is absent
    return perms


def missing_permissions(perms: int, required: Iterable[int]) -> list[str]:
    """Names of the required permission bits that are absent, in the order given."""
    return [PERMISSION_NAMES[bit] for bit in required if not perms & bit]


def can_view(perms: int) -> bool:
    return bool(perms & VIEW_CHANNEL)


def can_read_history(perms: int) -> bool:
    return bool(perms & VIEW_CHANNEL) and bool(perms & READ_MESSAGE_HISTORY)
