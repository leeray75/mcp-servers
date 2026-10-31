"""The fixed tool set and the boot registration plan (R35-R37).

:data:`READ_TOOLS` is the 23-row table of SPEC §6.4 **in that order**; :data:`ACCOUNT_TOOLS` is
the two account tools that appear only with ``TWITCH_USER_OAUTH=true``; :data:`WRITE_TOOLS` is the
sixteen writes in their six groups. :func:`compute_tool_plan` turns settings plus "is a user token
present" into the ordered list of names ``server.py`` registers.

Three rules make this the whole story of registration (R36):

* a tool is omitted when **its write group is disabled** — which needs *both* kill switches off to
  matter (R28);
* a tool is omitted when **a token type it requires is absent** (the five user-token reads, and the
  two account tools, when user OAuth is off);
* a tool is omitted when the operator's per-tool disable flag names it explicitly.

Nothing else ever changes the list, and **no tool is ever added at runtime** (R36). There are no
writes beyond these sixteen, no prompt, no resource and no endpoint passthrough (R37). The
planner is pure, so the boot log, ``/healthz``'s ``tool_count`` and the pinned tests share one
decision.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final, Literal

from twitch_mcp.config import Settings
from twitch_mcp.identity import (
    GROUP_CHANNEL,
    GROUP_CHAT,
    GROUP_MODERATION,
    GROUP_POLLS,
    GROUP_PREDICTIONS,
    GROUP_ROLES,
)

#: Which credential a tool needs. ``"app"`` always exists; ``"user"`` needs user OAuth on *and* a
#: connected account (R46, R49).
TokenRequirement = Literal["app", "user"]

#: The 23 read tools of SPEC §6.4, in registration order (R35). The token column is in
#: :data:`TOOL_TOKEN_TYPES`.
READ_TOOLS: Final[tuple[str, ...]] = (
    "twitch_account_status",
    "get_users",
    "get_users_follows",
    "get_channels",
    "search_channels",
    "get_streams",
    "get_top_games",
    "get_games",
    "get_videos",
    "get_clips",
    "get_polls",
    "get_predictions",
    "get_emotes",
    "get_channel_chat_settings",
    "get_channel_chat_badges",
    "get_stream_markers",
    "get_broadcaster_subscribers",
    "get_channel_editors",
    "get_moderators",
    "get_banned_users",
    "get_automod_settings",
    "get_chat_chatters",
    "get_schedule",
)

#: The two account tools, registered only when user OAuth is on (SPEC §6.4 "Account (2)").
ACCOUNT_TOOLS: Final[tuple[str, ...]] = (
    "connect_twitch_account",
    "disconnect_twitch_account",
)

#: The six write groups of SPEC §6.4, in the order the README documents them.
WRITE_GROUPS: Final[tuple[str, ...]] = (
    GROUP_CHANNEL,
    GROUP_POLLS,
    GROUP_PREDICTIONS,
    GROUP_CHAT,
    GROUP_MODERATION,
    GROUP_ROLES,
)

#: The 15 write tools, grouped (R37 — there are exactly these and no others).
WRITE_TOOLS: Final[dict[str, tuple[str, ...]]] = {
    GROUP_CHANNEL: ("update_channel",),
    GROUP_POLLS: ("create_poll", "end_poll"),
    GROUP_PREDICTIONS: ("create_prediction", "end_prediction"),
    GROUP_CHAT: ("send_chat_message",),
    GROUP_MODERATION: (
        "timeout_user",
        "remove_timeout",
        "ban_user",
        "unban_user",
        "set_automod_term",
        "remove_automod_term",
    ),
    GROUP_ROLES: ("add_vip", "remove_vip", "add_moderator", "remove_moderator"),
}

#: Flattened write tool names, in group order.
ALL_WRITE_TOOLS: Final[tuple[str, ...]] = tuple(
    name for group in WRITE_GROUPS for name in WRITE_TOOLS[group]
)

#: Every tool name this server can ever register (R37: at most 41).
ALL_TOOLS: Final[tuple[str, ...]] = READ_TOOLS + ACCOUNT_TOOLS + ALL_WRITE_TOOLS

#: The maximum tool count of SPEC §6.4 (all groups plus user OAuth on).
MAX_TOOL_COUNT: Final = 41  # 23 reads + 2 account + 16 writes, per the SPEC §6.4 table

#: The token type each read tool requires (SPEC §6.4 "Token" column). Anything absent here is
#: app-token: tools that exist even with user OAuth off.
TOOL_TOKEN_TYPES: Final[dict[str, TokenRequirement]] = {
    "get_stream_markers": "user",
    "get_broadcaster_subscribers": "user",
    "get_channel_editors": "user",
    "get_moderators": "user",
    "get_banned_users": "user",
    "get_automod_settings": "user",
    "get_chat_chatters": "user",
}

#: Every write tool needs the user token: a write acts as the operator, never as the app (R29).
WRITE_TOKEN_REQUIREMENT: Final[TokenRequirement] = "user"  # noqa: S105 - a bucket class, not a credential

#: Why a tool was left out of the plan (R36). Exactly the three causes the spec allows.
OmitReason = Literal["write_group_disabled", "token_absent", "explicitly_disabled"]


@dataclass(frozen=True, slots=True)
class ToolPlan:
    """The boot registration decision: what is registered, what was omitted, and why (R35-R37)."""

    registered: tuple[str, ...]
    omitted: tuple[tuple[str, OmitReason], ...] = field(default=())
    user_oauth: str = "off"
    writes_enabled: bool = False
    enabled_write_groups: tuple[str, ...] = ()

    @property
    def tool_count(self) -> int:
        """What ``/healthz`` reports (R3): a count, never names or secrets."""
        return len(self.registered)

    def omitted_reason(self, name: str) -> OmitReason | None:
        """Why ``name`` is absent, or ``None`` when it is registered."""
        for tool, reason in self.omitted:
            if tool == name:
                return reason
        return None


def _normalize_disabled(disabled: object) -> frozenset[str]:
    """Normalise the per-tool disable-flag names handed in by the caller (R36).

    ``config.py`` is the sole module that inspects the operator's settings input (R6), so this
    module reads none: ``server.py``, which loaded the settings, hands over the tool names whose
    per-tool disable flag was true — as an iterable or a comma/space-delimited string.
    Unknown names are dropped rather than raising: a typo in a disable flag must never add a tool
    (R36) and must never crash boot.
    """
    if disabled is None:
        return frozenset()
    if isinstance(disabled, str):
        parts = disabled.replace(",", " ").split()
    elif isinstance(disabled, Iterable):
        parts = [str(item) for item in disabled]
    else:
        return frozenset()
    known = {name.lower() for name in ALL_TOOLS}
    return frozenset(part.strip().lower() for part in parts if part.strip().lower() in known)


def token_requirement(name: str) -> TokenRequirement:
    """The credential one tool requires (SPEC §6.4 "Token" column)."""
    if name in ALL_WRITE_TOOLS or name in ACCOUNT_TOOLS:
        return WRITE_TOKEN_REQUIREMENT if name in ALL_WRITE_TOOLS else "user"
    if name == "twitch_account_status":
        return "app"  # answerable with the app token; enriches itself when a user token exists
    return TOOL_TOKEN_TYPES.get(name, "app")


def group_of(name: str) -> str | None:
    """The write group one tool belongs to, or ``None`` for a read or account tool."""
    for group, names in WRITE_TOOLS.items():
        if name in names:
            return group
    return None


def compute_tool_plan(
    settings: Settings,
    has_user_token: bool,
    disabled_tools: object = None,
) -> ToolPlan:
    """The ordered list of tools to register, per R28/R35/R36.

    ``has_user_token`` is "user OAuth is on *and* an account is connected": the single condition the
    seven user-token reads and the two account tools need (R46, R49). ``disabled_tools`` is the set
    of tool names whose per-tool disable flag was true; the caller loaded it, because
    ``config.py`` alone inspects the operator's settings input (R6). Registration order is exactly
    :data:`READ_TOOLS`, then :data:`ACCOUNT_TOOLS`, then the write groups in
    :data:`WRITE_GROUPS` order — the order SPEC §6.4 pins (R35).
    """
    disabled = _normalize_disabled(disabled_tools)
    writes_on = settings.writes_enabled
    groups = settings.write_groups
    registered: list[str] = []
    omitted: list[tuple[str, OmitReason]] = []

    def consider(name: str, requirement: TokenRequirement, group: str | None) -> None:
        if group is not None and not (writes_on and group in groups):
            omitted.append((name, "write_group_disabled"))
            return
        if requirement == "user" and not (settings.twitch_user_oauth and has_user_token):
            omitted.append((name, "token_absent"))
            return
        if name in disabled:
            omitted.append((name, "explicitly_disabled"))
            return
        registered.append(name)

    for name in READ_TOOLS:
        consider(name, token_requirement(name), None)
    for name in ACCOUNT_TOOLS:
        consider(name, "user", None)
    for group in WRITE_GROUPS:
        for name in WRITE_TOOLS[group]:
            consider(name, WRITE_TOKEN_REQUIREMENT, group)

    enabled_groups = tuple(g for g in WRITE_GROUPS if writes_on and g in groups)
    user_oauth = (
        f"on({'connected' if has_user_token else 'not_connected'})"
        if settings.twitch_user_oauth
        else "off"
    )
    return ToolPlan(
        registered=tuple(registered),
        omitted=tuple(omitted),
        user_oauth=user_oauth,
        writes_enabled=writes_on,
        enabled_write_groups=enabled_groups,
    )


#: What the default boot (writes off, user OAuth off) exposes: the 23 reads minus the seven that
#: need a user token, i.e. the sixteen app-token reads.
DEFAULT_REGISTERED: Final[tuple[str, ...]] = tuple(
    name for name in READ_TOOLS if TOOL_TOKEN_TYPES.get(name, "app") == "app"
)


__all__ = [
    "ACCOUNT_TOOLS",
    "ALL_TOOLS",
    "ALL_WRITE_TOOLS",
    "DEFAULT_REGISTERED",
    "MAX_TOOL_COUNT",
    "READ_TOOLS",
    "TOOL_TOKEN_TYPES",
    "WRITE_GROUPS",
    "WRITE_TOOLS",
    "OmitReason",
    "ToolPlan",
    "compute_tool_plan",
    "group_of",
    "token_requirement",
]
